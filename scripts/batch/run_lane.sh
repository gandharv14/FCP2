#!/bin/bash
# Lane supervisor: keeps one recovery lane working its queue without a human.
#
# The worker (scripts.batch.recovery_worker) already retries a failed row on
# the spot until it delivers, parks on a controller defect, or hits the
# lifetime attempt cap. What it cannot do is outlive itself: when it exits,
# something has to decide what happens next. That is this loop.
#
# Worker exit codes (see recovery_worker.exit_code_for):
#   0  every row generated                -> lane done, exit 0
#   1  rows remain (capped / no progress) -> rerun; sleep only if nothing changed
#   2  host floor (disk, evidence space)  -> sleep RECOVERY_LANE_FLOOR_SLEEP, rerun
#   3  new controller fingerprint         -> run the lane fix step, then rerun
#   *  crash                              -> back off, rerun
#
# A lane overlay (RECOVERY_STATE/overlay/scripts/...) produced by the fix step
# is put ahead of the baseline on PYTHONPATH for every later cycle. Nothing
# under RECOVERY_BASELINE_REPO is ever modified.
#
# Touch RECOVERY_STATE/lane.stop to make the loop exit at the next check.
#
# Env: RECOVERY_STATE, RECOVERY_BASELINE_REPO (required, plus every RECOVERY_*
# the worker itself needs). Optional: RECOVERY_PYTHON (default python3),
# RECOVERY_LANE_IDLE_SLEEP (300), RECOVERY_LANE_FLOOR_SLEEP (600),
# RECOVERY_LANE_CRASH_SLEEP (60), RECOVERY_LANE_MAX_CYCLES (0 = unlimited),
# RECOVERY_LANE_FIX_CMD (default: python -m scripts.batch.lane_fix).
set -u
: "${RECOVERY_STATE:?}" "${RECOVERY_BASELINE_REPO:?}"
PY=${RECOVERY_PYTHON:-python3}
IDLE_SLEEP=${RECOVERY_LANE_IDLE_SLEEP:-300}
FLOOR_SLEEP=${RECOVERY_LANE_FLOOR_SLEEP:-600}
CRASH_SLEEP=${RECOVERY_LANE_CRASH_SLEEP:-60}
MAX_CYCLES=${RECOVERY_LANE_MAX_CYCLES:-0}
FIX_CMD=${RECOVERY_LANE_FIX_CMD:-"$PY -m scripts.batch.lane_fix"}
LOG="$RECOVERY_STATE/lane-supervisor.jsonl"
STOP="$RECOVERY_STATE/lane.stop"
OVERLAY="$RECOVERY_STATE/overlay"
mkdir -p "$RECOVERY_STATE"
cd "$RECOVERY_BASELINE_REPO" || exit 70
# Without this, `python -m` puts the cwd (the baseline) ahead of PYTHONPATH
# and a lane overlay would never be the module actually loaded.
export PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1

cycle=0
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() {
  # log EVENT [extra-json-fields]
  printf '{"at":"%s","cycle":%d,"event":"%s"%s}\n' "$(now)" "$cycle" "$1" "${2:-}" >> "$LOG"
  echo "LANE_SUPERVISOR $1 cycle=$cycle ${2:-}"
}
ledger_sha() {
  "$PY" - "$RECOVERY_STATE/recovery-ledger.json" <<'PYEOF'
import hashlib, json, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.is_file():
    print("")
    raise SystemExit(0)
try:
    data = json.loads(path.read_text())
    # Status per row is what matters; timestamps change every run.
    key = sorted((str(r.get("workbook_id")), str(r.get("status")), str(r.get("error", "")))
                 for r in data.get("records", []))
    print(hashlib.sha256(json.dumps(key).encode()).hexdigest())
except Exception:
    print("")
PYEOF
}
nap() {
  # sleep $1 seconds in 10s slices so lane.stop is honoured promptly
  local left=$1
  while [ "$left" -gt 0 ]; do
    [ -e "$STOP" ] && return 0
    local step=10; [ "$left" -lt 10 ] && step=$left
    sleep "$step"; left=$((left - step))
  done
}

idle_streak=0
crash_streak=0
last_sha=""
while :; do
  cycle=$((cycle + 1))
  if [ -e "$STOP" ]; then log stop; exit 0; fi
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycle" -gt "$MAX_CYCLES" ]; then log max_cycles; exit 1; fi
  if [ -f "$OVERLAY/scripts/batch/recovery_worker.py" ]; then
    export PYTHONPATH="$OVERLAY:$RECOVERY_BASELINE_REPO"
    overlay_json=",\"overlay\":true"
  else
    export PYTHONPATH="$RECOVERY_BASELINE_REPO"
    overlay_json=",\"overlay\":false"
  fi
  log worker_start "$overlay_json"
  "$PY" -u -m scripts.batch.recovery_worker
  rc=$?
  sha=$(ledger_sha)
  log worker_exit ",\"rc\":$rc,\"ledger_changed\":$([ "$sha" != "$last_sha" ] && echo true || echo false)"
  case "$rc" in
    0)
      log lane_done
      exit 0
      ;;
    1)
      crash_streak=0
      if [ -n "$sha" ] && [ "$sha" = "$last_sha" ]; then
        idle_streak=$((idle_streak + 1))
        # Nothing changed: every remaining row is capped, parked, or refused
        # before an attempt. Back off (cap 1h) but never give up: a raised cap,
        # freed disk, or a lane fix can change the answer.
        wait=$((IDLE_SLEEP * idle_streak)); [ "$wait" -gt 3600 ] && wait=3600
        log idle ",\"streak\":$idle_streak,\"sleep\":$wait"
        nap "$wait"
      else
        idle_streak=0
      fi
      ;;
    2)
      crash_streak=0
      log host_floor ",\"sleep\":$FLOOR_SLEEP"
      nap "$FLOOR_SLEEP"
      ;;
    3)
      crash_streak=0
      log fix_start
      $FIX_CMD
      frc=$?
      log fix_exit ",\"rc\":$frc"
      if [ "$frc" -ne 0 ]; then
        # The fix step refused, failed its tests, or hit its own cap. The
        # parked rows stay parked; the next cycle still works every other row.
        nap "$IDLE_SLEEP"
      fi
      ;;
    *)
      crash_streak=$((crash_streak + 1))
      wait=$((CRASH_SLEEP * crash_streak)); [ "$wait" -gt 600 ] && wait=600
      log crash ",\"streak\":$crash_streak,\"sleep\":$wait"
      nap "$wait"
      ;;
  esac
  last_sha=$sha
done
