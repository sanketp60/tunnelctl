#!/bin/bash
# Generic tunnel runner. Reads tunnels.json for <name> and execs the gcloud IAP tunnel.
# Launched by launchd (com.fynd.tunnel.<name>). Must stay foreground (no -f).
# Usage: run-tunnel.sh <name>

set -euo pipefail

NAME="${1:?usage: run-tunnel.sh <name>}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${TUNNELS_PYTHON:-/usr/bin/python3}"

# launchd runs with a minimal PATH. Add common gcloud + Homebrew + system locations
# so this works regardless of where the Google Cloud SDK is installed.
export PATH="$HOME/google-cloud-sdk/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-$HOME/.config/gcloud}"

# Read NUL-separated gcloud argv from the config helper into an array.
args=()
while IFS= read -r -d '' a; do args+=("$a"); done < <("$PY" "$DIR/_lib.py" ssh-args "$NAME")

exec gcloud "${args[@]}"
