#!/bin/bash
# Tunnel self-healing watchdog (config-driven from tunnels.json).
# For each tunnel: run a real liveness probe through the local port.
#   pg    -> pg_isready (server response = tunnel alive; no DB creds needed)
#   mongo -> OP_MSG hello handshake (mongo_ping.py)
#   tcp   -> plain TCP connect
# If a probe fails, restart that tunnel ONLY IF gcloud auth is valid.
# If auth needs interactive refresh -> log and stand down (no self-heal on ADC/reauth).
# Scheduled by launchd (com.fynd.tunnel.healthcheck) via StartInterval + at wake.

# launchd runs with a minimal PATH; add gcloud + Homebrew (pg_isready) + system bins.
export PATH="$HOME/google-cloud-sdk/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${TUNNELS_PYTHON:-/usr/bin/python3}"
LIB="$DIR/_lib.py"
LOG="$DIR/logs/healthcheck.log"
MONGO_PING="$DIR/mongo_ping.py"
GCLOUD="$(command -v gcloud || echo gcloud)"
PREFIX="${TUNNELS_PREFIX:-com.fynd.tunnel}"
UID_NUM=$(id -u)

PROBE_TIMEOUT=5
COOLDOWN=90
POST_RESTART_WAIT=12

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

run_with_timeout() {
  local secs=$1; shift
  "$@" & local p=$!
  ( sleep "$secs"; kill -9 "$p" 2>/dev/null ) & local k=$!
  wait "$p" 2>/dev/null; local rc=$?
  kill "$k" 2>/dev/null; wait "$k" 2>/dev/null
  return $rc
}

probe() { # port type
  case "$2" in
    pg)    pg_isready -h 127.0.0.1 -p "$1" -t "$PROBE_TIMEOUT" -q; local r=$?; [ "$r" -eq 0 ] || [ "$r" -eq 1 ] ;;
    mongo) "$PY" "$MONGO_PING" "$1" "$PROBE_TIMEOUT" >/dev/null 2>&1 ;;
    tcp)   nc -z -G "$PROBE_TIMEOUT" 127.0.0.1 "$1" >/dev/null 2>&1 ;;
  esac
}

auth_valid() { run_with_timeout 15 "$GCLOUD" auth print-access-token >/dev/null 2>&1; }
cooldown_active() { local f="/tmp/fynd-tunnel-${1}.last" now last
  [ -f "$f" ] || return 1; now=$(date +%s); last=$(cat "$f" 2>/dev/null || echo 0)
  [ $(( now - last )) -lt "$COOLDOWN" ]; }
mark_restart() { date +%s > "/tmp/fynd-tunnel-${1}.last"; }

main() {
  local healed=0 failed=0 ok=0
  while IFS='|' read -r name port host rport type desc; do
    [ -z "$name" ] && continue
    local label="$PREFIX.$name"
    if probe "$port" "$type"; then ok=$((ok+1)); continue; fi

    log "UNHEALTHY: $desc ($label) on :$port"
    if cooldown_active "$label"; then
      log "  -> in cooldown (<${COOLDOWN}s), skipping"; failed=$((failed+1)); continue
    fi
    if ! auth_valid; then
      log "  -> gcloud auth needs refresh; STANDING DOWN. Run: gcloud auth login"; failed=$((failed+1)); continue
    fi
    log "  -> auth OK, restarting (kickstart)"
    mark_restart "$label"
    launchctl kickstart -k "gui/$UID_NUM/$label" 2>>"$LOG"
    sleep "$POST_RESTART_WAIT"
    if probe "$port" "$type"; then log "  -> RECOVERED: $desc"; healed=$((healed+1))
    else log "  -> still down; retry next cycle (after cooldown)"; failed=$((failed+1)); fi
  done < <("$PY" "$LIB" rows)

  if [ "$healed" -gt 0 ] || [ "$failed" -gt 0 ]; then
    log "cycle done: ok=$ok healed=$healed failed=$failed"
  fi
}

main
