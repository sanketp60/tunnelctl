# Tunnels

Persistent, self-healing SSH/IAP database tunnels for macOS — configured from a single
JSON file and managed with one small CLI.

Open one config, list your databases, run `tunnelctl sync`, and every tunnel comes up as a
background service that **auto-starts at login, auto-reconnects on drop, and self-heals after
your laptop sleeps/wakes**. No more re-running `gcloud compute ssh ... -L ...` by hand or
hunting for the right `lsof | kill` incantation.

Built for Google Cloud IAP bastions (`gcloud compute ssh --tunnel-through-iap`), but the model
works for anything you can reach with `gcloud compute ssh`.

---

## Table of contents

- [Why](#why)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
- [Command reference](#command-reference)
- [Self-healing watchdog](#self-healing-watchdog)
- [Connecting your DB client](#connecting-your-db-client)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Files in this repo](#files-in-this-repo)
- [Security](#security)

---

## Why

Long-lived IAP/SSH tunnels are fragile in day-to-day use:

- **Laptop sleep kills them silently.** The `ssh` process often stays alive but its connection
  is dead — the local port still `LISTEN`s, so naive "is the port up?" checks (and `launchd`'s
  own `KeepAlive`) think everything is fine. You only find out when your query hangs.
- **`gcloud ... -f` backgrounding** doesn't integrate with any supervisor — you can't cleanly
  restart, inspect, or auto-recover.
- **Managing many tunnels** by hand (one wrapper script + one launchd plist each) is tedious
  and error-prone (e.g. pairing a bastion with the wrong zone).

This project fixes all three: declarative config, real end-to-end health checks, and a
watchdog that actually restarts dead-but-listening tunnels — while standing down when the real
problem is expired credentials (which a restart can't fix).

---

## How it works

```
tunnels.json ──► tunnelctl sync ──► one launchd agent per tunnel
                                     │
                                     ├─ run-tunnel.sh <name>  (reads JSON, execs gcloud ssh -L … -N)
                                     │      KeepAlive + RunAtLoad + SSH keepalives
                                     │
                                     └─ healthcheck.sh  (separate launchd agent, every 60s + on wake)
                                            real probe per tunnel ──► restart if dead AND auth OK
```

- **`tunnels.json`** is the single source of truth. Each tunnel is a small object; bastions are
  defined once as named aliases and referenced by tunnels.
- **`tunnelctl`** turns that config into per-tunnel `launchd` agents (generates the plists for
  you), and is your CLI for status / restart / logs / add / remove.
- **`run-tunnel.sh`** is a generic runner — `launchd` calls `run-tunnel.sh <name>`, it looks up
  the tunnel in JSON and `exec`s the real `gcloud compute ssh ... -L <local>:<host>:<port> -N`.
- **`healthcheck.sh`** is the watchdog (details below).

Each tunnel runs with SSH keepalives (`ServerAliveInterval`/`ServerAliveCountMax`/
`ExitOnForwardFailure`) so a dead peer is detected and the process exits — letting `launchd`
restart it. The watchdog is the backstop for the cases `launchd` can't see.

---

## Requirements

| Tool | Why | Install |
|------|-----|---------|
| macOS | uses `launchd` for process supervision | — |
| `gcloud` | establishes the IAP tunnel | <https://cloud.google.com/sdk/docs/install> |
| `pg_isready` | Postgres health probe | `brew install libpq` (then symlink) or `brew install postgresql` |
| Python 3 | config parsing + Mongo probe | system `/usr/bin/python3` is fine |
| `nc` | `tcp` health probe | ships with macOS |

Authenticate gcloud first: `gcloud auth login` (and select your project).

---

## Install

### Step 1 — Install prerequisites

```bash
# Google Cloud SDK (if you don't already have it) — see the link in Requirements.
# pg_isready (only needed for Postgres tunnels):
brew install libpq
brew link --force libpq        # puts pg_isready on your PATH
```

> **Important (macOS):** install the gcloud SDK somewhere like `~/google-cloud-sdk` — **not**
> inside `~/Downloads`, `~/Desktop`, or `~/Documents`. macOS blocks `launchd` from executing
> binaries in those protected folders (you'd get `Operation not permitted`).

### Step 2 — Authenticate gcloud

```bash
gcloud auth login
gcloud config set project <your-project-id>
```

### Step 3 — Clone the repo

```bash
git clone <this-repo> ~/tunnels
cd ~/tunnels
```

> You can clone anywhere — the scripts resolve their own paths. `~/tunnels` is just the
> convention used in these docs.

### Step 4 — Create your config

```bash
cp tunnels.example.json tunnels.json
```

Edit `tunnels.json` with your real bastions and databases (see [Configure](#configure)), then
validate it:

```bash
./tunnelctl validate
```

### Step 5 — Add the `tunnelctl` alias to your shell

Pick the block matching your shell so you can run `tunnelctl` from anywhere.

**zsh** (default on modern macOS):

```bash
echo 'alias tunnelctl="$HOME/tunnels/tunnelctl"' >> ~/.zshrc
source ~/.zshrc
```

**bash:**

```bash
echo 'alias tunnelctl="$HOME/tunnels/tunnelctl"' >> ~/.bash_profile
source ~/.bash_profile
```

> Adjust `$HOME/tunnels` if you cloned somewhere else. Don't have the alias yet? You can always
> run the tool with its full path: `~/tunnels/tunnelctl status`.

### Step 6 — Start the tunnels

```bash
tunnelctl sync       # generates a launchd agent per tunnel and starts them all
tunnelctl status     # verify: every tunnel should show HEALTH=OK
```

That's it — tunnels now run in the background and restart automatically at login.

### Step 7 (optional) — Enable the self-healing watchdog

The watchdog auto-recovers tunnels after sleep/wake. Create its launchd agent once:

```bash
cat > ~/Library/LaunchAgents/com.fynd.tunnel.healthcheck.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>            <string>com.fynd.tunnel.healthcheck</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$HOME/tunnels/healthcheck.sh</string>
    </array>
    <key>RunAtLoad</key>        <true/>
    <key>StartInterval</key>    <integer>60</integer>
    <key>StandardOutPath</key>  <string>$HOME/tunnels/logs/healthcheck.out.log</string>
    <key>StandardErrorPath</key><string>$HOME/tunnels/logs/healthcheck.err.log</string>
</dict>
</plist>
EOF

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fynd.tunnel.healthcheck.plist
tunnelctl status     # should now show "Watchdog: LOADED"
```

> Lower `StartInterval` (e.g. `30`) for faster recovery, higher for less frequent checks.
> If your launchd label prefix differs, set `TUNNELS_PREFIX` consistently (see [FAQ](#faq)).

### Uninstall

```bash
tunnelctl remove <name>          # remove individual tunnels, or:
# remove all, then the watchdog:
for n in $(./tunnelctl status | awk 'NR>2 && $1!~/^-/ {print $1; next} /Watchdog/{exit}'); do tunnelctl remove "$n"; done
launchctl bootout gui/$(id -u)/com.fynd.tunnel.healthcheck 2>/dev/null
rm -f ~/Library/LaunchAgents/com.fynd.tunnel.*.plist
```

---

## Configure

`tunnels.json` has three sections: `defaults`, `bastions`, and `tunnels`.

```json
{
  "defaults": {
    "project": "my-gcp-project-id",
    "ssh_opts": ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes"]
  },
  "bastions": {
    "dev":  { "instance": "my-dev-bastion",  "zone": "us-central1-a", "project": "my-dev-project" },
    "prod": { "instance": "my-prod-bastion", "zone": "us-central1-b", "project": "my-prod-project" }
  },
  "tunnels": [
    {
      "name": "myapp-dev",
      "local_port": 12000,
      "remote_host": "10.0.0.10",
      "remote_port": 5432,
      "type": "pg",
      "bastion": "dev",
      "description": "MyApp Dev Postgres"
    }
  ]
}
```

### Bastions (aliases)

Define each bastion **once** and reference it by alias. The instance, zone, **and GCP project**
come from the alias — so each bastion can live in a different project, and you can never
accidentally pair a bastion with the wrong zone or project.

| Field | Required | Description |
|-------|----------|-------------|
| `instance` | yes | gcloud compute instance name of the bastion |
| `zone` | yes | the bastion's zone (e.g. `us-central1-a`) |
| `project` | recommended | GCP project this bastion lives in. Falls back to `defaults.project` if omitted |

Add one interactively with `tunnelctl add-bastion`, or edit the `bastions` block directly.

**Project precedence** (highest first): a `project` set on the tunnel → the bastion's
`project` → `defaults.project`.

### Tunnel fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | unique id; becomes the launchd label and log file names |
| `local_port` | yes | port on `127.0.0.1` your client connects to (must be unique) |
| `remote_host` | yes | target DB IP/host **as seen from the bastion** |
| `remote_port` | yes | target DB port (e.g. `5432` Postgres, `27017` Mongo) |
| `type` | yes | health-probe type: `pg` \| `mongo` \| `tcp` |
| `bastion` | yes | a bastion **alias** from the `bastions` block (provides instance/zone/project) |
| `project` | no | overrides the bastion's project for this tunnel (rarely needed) |
| `description` | no | shown in `tunnelctl status` |
| `ssh_opts` | no | overrides `defaults.ssh_opts` for this tunnel |

### `defaults`

- `project` — fallback GCP project for bastions that don't set their own.
- `ssh_opts` — SSH options applied to every tunnel. The keepalive defaults are recommended;
  they make sleep/wake recovery faster.

After any edit, run **`tunnelctl sync`**.

---

## Command reference

```bash
tunnelctl status            # health table for all tunnels + watchdog state (default command)
tunnelctl sync              # apply tunnels.json: create/refresh/load/unload agents to match config
tunnelctl add               # interactive: prompt for tunnel fields, append to JSON, then sync
tunnelctl add-bastion       # interactive: define a bastion (alias/instance/zone/project)
tunnelctl restart <name>    # restart one tunnel
tunnelctl restart all       # restart every tunnel
tunnelctl logs <name>       # tail -f a tunnel's stdout+stderr logs
tunnelctl remove <name>     # unload the agent and delete the tunnel from JSON
tunnelctl validate          # check JSON: required fields, valid types, duplicate names/ports, alias resolution
tunnelctl edit              # open tunnels.json in $EDITOR (then run sync)
```

### Typical workflows

**Add a connection (interactive):**
```bash
tunnelctl add        # answer prompts (it lists your bastion aliases); auto-syncs
```

**Add a connection (by hand):**
```bash
tunnelctl edit       # add a tunnel object
tunnelctl validate   # catch mistakes before applying
tunnelctl sync       # start it
```

**Remove a connection:**
```bash
tunnelctl remove myapp-dev
```

`sync` reconciles reality to config: tunnels added to JSON get created and started; tunnels
removed from JSON get stopped and their plists deleted. The watchdog agent is left untouched.

### `status` output

```
NAME                       STATE     PID     HEALTH  DESCRIPTION
----                       -----     ---     ------  -----------
myapp-dev                  running   19010   OK      MyApp Dev Postgres
```

- **STATE** — launchd state (`running`, `spawn scheduled`, `UNLOADED`).
- **HEALTH** — result of a real probe through the tunnel (`OK` / `FAIL`), not just "port open".
- A crash-looping tunnel shows `spawn scheduled` + `FAIL` → usually a **config error**
  (wrong host/zone/bastion). Check `tunnelctl logs <name>`.

---

## Self-healing watchdog

`healthcheck.sh` runs as its own `launchd` agent on a `StartInterval` (e.g. every 60s) and at
wake. For each tunnel it runs a **real end-to-end probe** through the local port:

| `type` | Probe | Detects |
|--------|-------|---------|
| `pg` | `pg_isready` (server responds to a startup packet) | dead/stale tunnel without needing DB creds |
| `mongo` | minimal `OP_MSG {hello:1}` handshake (`mongo_ping.py`) | dead/stale tunnel, credential-free |
| `tcp` | `nc -z` connect | basic port liveness |

If a probe **fails**, the watchdog decides what to do:

1. **Cooldown** — if it restarted this tunnel < ~90s ago, it waits (no restart storms).
2. **Auth gate** — it runs `gcloud auth print-access-token`:
   - **Succeeds** (creds valid / silently refreshable) → the failure is a *stale tunnel*
     (typically sleep/wake) → it `kickstart`s that tunnel and re-probes.
   - **Fails** (interactive re-auth required) → it **stands down and logs** instead of
     restarting, because a restart can't fix expired credentials. Run `gcloud auth login` and
     the next cycle heals everything automatically.

This is the key behavior: **self-heal stale tunnels, but never crash-loop fighting an auth
problem.**

### Why a watchdog at all?

`launchd`'s `KeepAlive` only reacts to the process *exiting*. After sleep/wake an `ssh` tunnel
frequently keeps running with a dead connection and a still-bound local port — `KeepAlive` sees
"running" and does nothing. The active probe catches exactly this "zombie" state.

### Enabling it

See [Install · Step 7](#step-7-optional--enable-the-self-healing-watchdog) for the one-time
setup. The watchdog is a `launchd` agent that runs `healthcheck.sh` on a `StartInterval` (60s
recommended) and at wake. Logs go to `logs/healthcheck.log` (decisions) and
`logs/healthcheck.err.log` (errors). Adjust the interval in its plist for faster/slower
recovery.

---

## Connecting your DB client

Point your client at `127.0.0.1` and the tunnel's `local_port`:

```bash
# Postgres
psql -h 127.0.0.1 -p 12000 -U <user> -d <database>
```

**MongoDB replica sets:** if you tunnel a single replica-set node, connect with
`directConnection=true` so the driver doesn't try to reach the other (unreachable) members it
discovers:

```
mongodb://<user>:<pass>@127.0.0.1:27027/<db>?directConnection=true&authSource=<db>
```

> DB credentials live in your client, **never** in `tunnels.json`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `HEALTH=FAIL`, `STATE=running` | stale tunnel (sleep/wake) | wait ≤60s for the watchdog, or `tunnelctl restart <name>` |
| `HEALTH=FAIL` on **all** tunnels | gcloud auth expired | `gcloud auth login` (tunnels self-heal in ~60s) |
| `STATE=spawn scheduled` + `FAIL` | config error (wrong host/zone/bastion) | `tunnelctl logs <name>` and check the gcloud error |
| `pg_isready: command not found` in logs | `pg_isready` not on PATH | `brew install libpq` and ensure it's on PATH |
| `Operation not permitted` (exit 126) | gcloud SDK in a macOS-protected dir (`~/Downloads`, `~/Desktop`) | move the SDK to e.g. `~/google-cloud-sdk` |
| Mongo client hangs / "no primary" | driver doing replica-set discovery | add `directConnection=true` to the URI |
| Port already in use on sync | another process holds `local_port` | change `local_port` or free the port (`lsof -i :<port>`) |

General first step is always:

```bash
tunnelctl status        # what's FAIL?
tunnelctl logs <name>   # why?
```

---

## FAQ

**Does this store my database passwords?**
No. `tunnels.json` is infra only (hosts, ports, bastions). Credentials stay in your DB client.
`tunnels.json` is also gitignored.

**Will tunnels come back after a reboot?**
Yes — each agent has `RunAtLoad`, so they start at login.

**What happens when my gcloud session expires?**
Tunnels fail; the watchdog detects the auth problem and stands down (it won't crash-loop). Run
`gcloud auth login` and they reconnect automatically within ~60s.

**Can I use a non-IAP / plain SSH host?**
The runner builds a `gcloud compute ssh` command. For plain SSH you'd adapt `run-tunnel.sh` and
`_lib.py`'s `ssh-args` to emit an `ssh -L …` command instead — the config model and watchdog
stay the same.

**Can I run many tunnels through the same bastion?**
Yes — define the bastion once as an alias and reference it from as many tunnels as you like.

**Does it work with `zsh`/`bash`?**
The scripts are `bash` and target macOS's built-in `bash` 3.2 (so they avoid 4.x-only syntax).

---

## Files in this repo

| File | Purpose |
|------|---------|
| `tunnelctl` | the CLI (status/sync/add/restart/logs/remove/validate/edit) |
| `run-tunnel.sh` | generic per-tunnel runner invoked by launchd |
| `healthcheck.sh` | self-healing watchdog |
| `_lib.py` | parses/validates `tunnels.json`, resolves bastion aliases, builds gcloud args |
| `mongo_ping.py` | credential-free MongoDB liveness probe |
| `tunnels.example.json` | annotated template — copy to `tunnels.json` |
| `tunnels.json` | **your** config (gitignored, created from the example) |
| `logs/` | per-tunnel + watchdog logs (gitignored) |

---

## Security

- `tunnels.json` and `logs/` are **gitignored** — your real bastions, IPs, project, and any
  log output never get committed.
- The committed files contain only generic placeholders (`my-dev-bastion`, `10.0.0.10`, …).
- Keep DB credentials in your client, not in this repo.
- Before publishing a fork, double-check with: `git grep -nI <your-org-or-username>` on staged
  files to confirm nothing leaked.
