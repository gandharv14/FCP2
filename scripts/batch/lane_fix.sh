#!/bin/bash
# Thin wrapper so the lane supervisor and a human at a shell call the fix step
# the same way. All logic lives in scripts/batch/lane_fix.py.
set -u
: "${RECOVERY_STATE:?}" "${RECOVERY_BASELINE_REPO:?}"
PY=${RECOVERY_PYTHON:-python3}
cd "$RECOVERY_BASELINE_REPO" || exit 70
export PYTHONPATH="$RECOVERY_BASELINE_REPO"
exec "$PY" -u -m scripts.batch.lane_fix "$@"
