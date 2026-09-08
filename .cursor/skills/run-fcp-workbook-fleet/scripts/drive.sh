#!/bin/bash
# Upload one host's artifacts and run deploy_remote.sh there. Runs once per host.
#
# usage: drive.sh --run-dir DIR --label LABEL --host HOST
# Env (required): RUN_TAG, LANES, APPROVAL_BATCH_ID, INVENTORY, MODEL. Optional: TIMEOUT_SECONDS,
# LANE_ENV_B64 (base64 of "KEY=VAL" lines appended as exports to every run-lane-*.sh).
# DIR is the build_queues.py --out directory; it must also hold baseline.tar
# (see deploy_fleet.sh, which builds it and calls this for every target host).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

RUN_DIR=""; LABEL=""; HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --label) LABEL="$2"; shift 2;;
    --host) HOST="$2"; shift 2;;
    *) die "unknown arg $1";;
  esac
done
[ -n "$RUN_DIR" ] && [ -n "$LABEL" ] && [ -n "$HOST" ] || die "--run-dir, --label, --host are required"
: "${RUN_TAG:?}" "${LANES:?}" "${APPROVAL_BATCH_ID:?}" "${INVENTORY:?}" "${MODEL:?}"

Q_DIR="$RUN_DIR/queues/$LABEL"
LANE_LETTERS=(a b c d e f g h i j k l m n o p q r s t u v w x y z)
QUEUES=()
Q_SHAS=""
for ((j = 0; j < LANES; j++)); do
  lane=${LANE_LETTERS[$j]}
  [ -f "$Q_DIR/queue-$lane.json" ] || die "missing $Q_DIR/queue-$lane.json"
  QUEUES+=("$Q_DIR/queue-$lane.json")
  Q_SHAS="$Q_SHAS $(sha256_of "$Q_DIR/queue-$lane.json")"
done
BASE_SHA=$(sha256_of "$RUN_DIR/baseline.tar")
SRC_SHA=$(sha256_of "$RUN_DIR/sources-$LABEL.tar")

gssh "$HOST" --command 'rm -rf /tmp/fleet && mkdir -p /tmp/fleet' 2>&1 | strip_noise || { echo "$LABEL PREP_FAIL"; exit 1; }
gscp "$RUN_DIR/baseline.tar" "$RUN_DIR/sources-$LABEL.tar" "${QUEUES[@]}" "$RUN_DIR/plan.json" "$HOST:/tmp/fleet/" 2>&1 | strip_noise || { echo "$LABEL SCP_FAIL"; exit 1; }

gssh "$HOST" --command "LABEL=$LABEL RUN_TAG=$RUN_TAG LANES=$LANES APPROVAL_BATCH_ID=$APPROVAL_BATCH_ID INVENTORY=$INVENTORY MODEL=$MODEL TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-} LANE_ENV_B64=${LANE_ENV_B64:-} BASE_SHA=$BASE_SHA SRC_SHA=$SRC_SHA Q_SHAS='$Q_SHAS' bash -s" < "$SCRIPTS_DIR/deploy_remote.sh" 2>&1 | strip_noise
rc=${PIPESTATUS[0]}
echo "$LABEL remote_exit=$rc"
exit "$rc"
