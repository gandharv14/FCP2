#!/bin/bash
# Shared helpers for the run-fcp-workbook-fleet scripts. Source, do not run.
# Every hostname, project, and zone is a parameter; nothing here assumes the
# operator is a particular user.

FLEET_PROJECT="${FLEET_PROJECT:-saleseng}"
FLEET_ZONE="${FLEET_ZONE:-us-central1-a}"
FLEET_SSH_TIMEOUT="${FLEET_SSH_TIMEOUT:-25}"
FLEET_PARALLEL="${FLEET_PARALLEL:-6}"   # max concurrent IAP tunnels
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$SKILL_DIR/scripts"
REPO_DIR="$(cd "$SKILL_DIR/../../.." && pwd)"   # FCP_recursive_v1
RUNS_DIR="${FLEET_RUNS_DIR:-$HOME/fcp-fleet-runs}"

# GSSH is an array so it can sit behind `timeout`: timeout 120 "${GSSH[@]}" HOST --command '...'
GSSH=(gcloud compute ssh --zone "$FLEET_ZONE" --project "$FLEET_PROJECT"
  --tunnel-through-iap --quiet
  --ssh-flag="-oConnectTimeout=$FLEET_SSH_TIMEOUT"
  --ssh-flag="-oServerAliveInterval=15"
  --ssh-flag="-oServerAliveCountMax=4")

# gssh HOST [gcloud ssh args...]  -- IAP tunnel, short connect timeout, keepalive.
gssh() {
  local host="$1"; shift
  "${GSSH[@]}" "$host" "$@"
}

# with_timeout SECS cmd...  -- coreutils timeout when present (Linux, or brew gtimeout), else plain.
with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$secs" "$@"
  else "$@"; fi
}

# throttle N  -- block while N or more background jobs are running (bash 3.2 safe).
throttle() {
  while [ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$1" ]; do sleep 1; done
}

# gscp SRC... HOST:DEST
gscp() {
  gcloud compute scp --zone "$FLEET_ZONE" --project "$FLEET_PROJECT" \
    --tunnel-through-iap --quiet --scp-flag="-oConnectTimeout=$FLEET_SSH_TIMEOUT" "$@"
}

# run_remote HOST SCRIPT [args...]  -- feed a local script over stdin (bash -s) so
# the remote command line never contains the patterns we grep for (self-match trap).
run_remote() {
  local host="$1" script="$2"; shift 2
  gssh "$host" --command "bash -s $*" < "$script"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# Strip gcloud/ssh banner noise from remote output.
strip_noise() {
  grep -v -E '^(WARNING|Warning|External IP|Updates are available|To increase|Please|Existing host keys|Writing|Updating project ssh)' || true
}

die() { echo "ERROR: $*" >&2; exit 1; }
