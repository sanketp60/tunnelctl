#!/usr/bin/python3
"""Config helper for tunnelctl. Single place that parses tunnels.json.

Usage:
  _lib.py names                  -> print tunnel names, one per line
  _lib.py rows                   -> print 'name|local_port|remote_host|remote_port|type|description|bind_address'
  _lib.py field <name> <field>   -> print one resolved field (with defaults applied)
  _lib.py binds                  -> print unique non-loopback bind addresses
  _lib.py ssh-args <name>        -> print the gcloud ssh argv, NUL-separated
  _lib.py validate               -> exit 0 if config valid, else print errors and exit 1
"""
import json, os, sys

CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnels.json")
REQUIRED = ["name", "local_port", "remote_host", "remote_port", "type", "bastion"]
VALID_TYPES = {"pg", "mongo", "tcp"}


def load():
    with open(CONF) as f:
        data = json.load(f)
    defaults = data.get("defaults", {})
    bastions = data.get("bastions", {})
    tunnels = data.get("tunnels", [])
    for t in tunnels:
        t.setdefault("ssh_opts", defaults.get("ssh_opts", []))
        t.setdefault("description", t.get("name", ""))
        # Local address the forward binds to. Empty (the default) means "let
        # ssh decide", which binds every loopback address (127.0.0.1 AND ::1) —
        # do NOT substitute "127.0.0.1" here or IPv6-resolving clients break.
        # Set explicitly only when a service (e.g. Kafka) advertises an address
        # the client must reach on that exact IP.
        t.setdefault("bind_address", defaults.get("bind_address", ""))
        # Resolve bastion alias -> instance + zone (+ project).
        # Falls back to treating 'bastion' as a raw instance name (needs 'zone').
        b = t.get("bastion")
        spec = bastions.get(b, {})
        if b in bastions:
            t["_instance"] = spec.get("instance")
            t["zone"] = spec.get("zone")
        else:
            t["_instance"] = b  # raw instance name (legacy)
        # Project precedence: explicit tunnel project > bastion project > default.
        t["project"] = t.get("project") or spec.get("project") or defaults.get("project")
    return tunnels


def find(name):
    for t in load():
        if t["name"] == name:
            return t
    sys.stderr.write(f"tunnel '{name}' not found in {CONF}\n")
    sys.exit(2)


def validate():
    errors = []
    try:
        tunnels = load()
    except Exception as e:
        print(f"JSON parse error: {e}")
        return 1
    seen_names, seen_ports = {}, {}
    for i, t in enumerate(tunnels):
        ctx = t.get("name", f"index {i}")
        for fld in REQUIRED:
            if not t.get(fld) and t.get(fld) != 0:
                errors.append(f"[{ctx}] missing required field: {fld}")
        if t.get("type") and t["type"] not in VALID_TYPES:
            errors.append(f"[{ctx}] invalid type '{t['type']}' (use pg|mongo|tcp)")
        # Bastion alias must resolve to an instance + zone.
        if not t.get("_instance"):
            errors.append(f"[{ctx}] bastion '{t.get('bastion')}' has no instance "
                          f"(define it under \"bastions\" or set zone for a raw instance)")
        if not t.get("zone"):
            errors.append(f"[{ctx}] no zone (bastion alias '{t.get('bastion')}' "
                          f"missing zone, or raw instance needs a 'zone' field)")
        if not t.get("project"):
            errors.append(f"[{ctx}] no project (set it on bastion '{t.get('bastion')}', "
                          f"on the tunnel, or as defaults.project)")
        n = t.get("name")
        if n in seen_names:
            errors.append(f"duplicate name '{n}'")
        seen_names[n] = True
        # A port collides only when bound on the same local address, so several
        # tunnels may share e.g. :9092 across distinct bind_addresses. An empty
        # bind_address is loopback, so it must collide with an explicit one.
        p = (t.get("bind_address") or "127.0.0.1", t.get("local_port"))
        if p in seen_ports:
            errors.append(f"duplicate bind {p[0]}:{p[1]} ('{n}' and '{seen_ports[p]}')")
        seen_ports[p] = n
    if errors:
        print("\n".join(errors))
        return 1
    print("config OK")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "validate":
        return validate()
    if cmd == "names":
        for t in load():
            print(t["name"])
        return 0
    if cmd == "rows":
        for t in load():
            print("|".join(str(t[k]) for k in
                  ("name", "local_port", "remote_host", "remote_port", "type",
                   "description", "bind_address")))
        return 0
    if cmd == "binds":
        # Addresses that must exist as lo0 aliases before their tunnel can bind.
        seen = []
        for t in load():
            b = t.get("bind_address")
            if b and b not in ("127.0.0.1", "::1") and b not in seen:
                seen.append(b)
        for b in seen:
            print(b)
        return 0
    if cmd == "field":
        t = find(sys.argv[2])
        print(t.get(sys.argv[3], ""))
        return 0
    if cmd == "ssh-args":
        t = find(sys.argv[2])
        args = ["compute", "ssh", "--zone", t["zone"], t["_instance"],
                "--project", t["project"], "--tunnel-through-iap", "--"]
        args += list(t.get("ssh_opts", []))
        # Omit the bind prefix entirely when unset, so ssh keeps its default
        # behaviour of binding all loopback addresses (v4 + v6).
        fwd = f'{t["local_port"]}:{t["remote_host"]}:{t["remote_port"]}'
        if t.get("bind_address"):
            fwd = f'{t["bind_address"]}:{fwd}'
        args += ["-L", fwd, "-N"]
        # Trailing NUL after EVERY arg so `read -d ''` captures the last one too.
        sys.stdout.write("".join(a + "\0" for a in args))
        return 0
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
