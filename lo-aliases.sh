#!/bin/bash
# Manage lo0 aliases required by tunnels that use an explicit bind_address.
#
# Some services (Kafka) hand the client an address in their metadata and the
# client then connects to THAT address, ignoring whatever it bootstrapped
# against. The only way to intercept locally is to bind the forward to the very
# same IP, which first has to exist on lo0.
#
# Addresses are read from tunnels.json (via _lib.py) so none are hardcoded here
# and this file stays safe to commit.
#
#   sudo ./lo-aliases.sh up      - add every required alias (idempotent)
#   sudo ./lo-aliases.sh down    - remove them
#        ./lo-aliases.sh status  - show which are present (no sudo needed)
#
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${TUNNELS_PYTHON:-/usr/bin/python3}"
LIB="$DIR/_lib.py"

binds() { "$PY" "$LIB" binds; }
present() { ifconfig lo0 | grep -q "inet $1 "; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "must run as root: sudo $0 $1"; exit 1; }; }

case "${1:-}" in
  up)
    need_root up
    n=0
    while IFS= read -r ip; do
      [ -z "$ip" ] && continue
      if present "$ip"; then echo "  present: $ip"
      else ifconfig lo0 alias "$ip" && echo "  added:   $ip"; fi
      n=$((n+1))
    done < <(binds)
    [ "$n" -eq 0 ] && echo "no bind_address entries in tunnels.json - nothing to do"
    ;;
  down)
    need_root down
    while IFS= read -r ip; do
      [ -z "$ip" ] && continue
      if present "$ip"; then ifconfig lo0 -alias "$ip" && echo "  removed: $ip"
      else echo "  absent:  $ip"; fi
    done < <(binds)
    ;;
  status)
    while IFS= read -r ip; do
      [ -z "$ip" ] && continue
      if present "$ip"; then echo "  UP      $ip"; else echo "  MISSING $ip"; fi
    done < <(binds)
    ;;
  *)
    echo "usage: $0 {up|down|status}"; exit 1 ;;
esac
