#!/bin/bash
# Verify the local baseline, tar it, and deploy to every idle host in fleet.json.
#
# usage: deploy_fleet.sh --run-dir DIR --fleet fleet.json --approval-batch-id ID \
#          [--lanes 2] [--model gpt-5.6-sol-high] [--run-tag TAG] [--timeout-seconds N] [--skip-tests]
#          [--lane-env KEY=VAL ...]
#
# --lane-env KEY=VAL  extra `export KEY=VAL` written into every run-lane-*.sh of THIS
#                     run only (repeatable). Use it for per-run safeguards such as
#                     RECOVERY_LARGE_WORKBOOKS=0568 RECOVERY_LARGE_TIMEOUT_SECONDS=28800.
#
# DIR is the build_queues.py --out directory (queues/, sources-*.tar, plan.json).
# Hosts with state != idle are skipped and listed. Deploys run FLEET_PARALLEL at a
# time; each host's log is DIR/deploy_<label>.log and must end with DEPLOY_OK.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

RUN_DIR=""; FLEET=""; APPROVAL=""; LANES=2; MODEL="gpt-5.6-sol-high"; RUN_TAG=""; TIMEOUT_SECONDS=""; SKIP_TESTS=0; LANE_ENV_LINES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2;;
    --fleet) FLEET="$2"; shift 2;;
    --approval-batch-id) APPROVAL="$2"; shift 2;;
    --lanes) LANES="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --run-tag) RUN_TAG="$2"; shift 2;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2;;
    --skip-tests) SKIP_TESTS=1; shift;;
    --lane-env)
      case "$2" in *=*) ;; *) die "--lane-env needs KEY=VAL, got '$2'";; esac
      LANE_ENV_LINES="${LANE_ENV_LINES}${2}
"; shift 2;;
    -h|--help) sed -n '2,10p' "$0"; exit 0;;
    *) die "unknown arg $1";;
  esac
done
[ -n "$RUN_DIR" ] && [ -n "$FLEET" ] && [ -n "$APPROVAL" ] || die "--run-dir, --fleet, --approval-batch-id are required"
[ -f "$RUN_DIR/plan.json" ] || die "$RUN_DIR has no plan.json; run build_queues.py first"
[ -n "$RUN_TAG" ] || RUN_TAG=$(date +%Y%m%d-%H%M)
echo "$RUN_TAG" > "$RUN_DIR/RUN_TAG"

# Which inventory file does this approval pin? Same rule as the worker.
INVENTORY=$(python3 - "$REPO_DIR" "$APPROVAL" <<'PY'
import hashlib, json, sys
from pathlib import Path
repo, approval_id = Path(sys.argv[1]), sys.argv[2]
reg = json.loads((repo / "verification_manifests/approved_source_inventories.v1.json").read_text())
match = [a for a in reg["approvals"] if a.get("batch_id") == approval_id]
if len(match) != 1: raise SystemExit(f"approval {approval_id} not found or ambiguous")
want = match[0]["inventory_artifact_sha256"]
for p in sorted((repo / "verification_manifests").glob("*.json")):
    if hashlib.sha256(p.read_bytes()).hexdigest() == want: print(p.name); break
else: raise SystemExit("no inventory file matches the approval's inventory_artifact_sha256")
PY
) || die "cannot resolve inventory for $APPROVAL"
echo "run_tag=$RUN_TAG lanes=$LANES model=$MODEL approval=$APPROVAL inventory=$INVENTORY"
if [ -n "$LANE_ENV_LINES" ]; then printf '%s' "$LANE_ENV_LINES" > "$RUN_DIR/lane-env.txt"; echo "lane env overrides:"; sed 's/^/  /' "$RUN_DIR/lane-env.txt"; else rm -f "$RUN_DIR/lane-env.txt"; fi
LANE_ENV_B64=$(printf '%s' "$LANE_ENV_LINES" | base64 | tr -d '\n')

echo "== local baseline"
python3 "$SCRIPTS_DIR/baseline_manifest.py" verify --repo "$REPO_DIR" || die "BASELINE_MANIFEST.json does not match the tree. Intended edit? run: python3 $SCRIPTS_DIR/baseline_manifest.py write --note '...'"
if [ "$SKIP_TESTS" -eq 0 ]; then
  (cd "$REPO_DIR" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
    tests/test_recovery_worker.py tests/test_memory_governor.py tests/test_batch_hardened_runner.py 2>&1 | tail -2) || die "local tests failed"
fi
(cd "$REPO_DIR" && COPYFILE_DISABLE=1 tar --exclude='./.venv' --exclude='./.env' --exclude='./.git' \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' --exclude='._*' \
  -cf "$RUN_DIR/baseline.tar" .) || die "tar failed"
echo "baseline.tar $(sha256_of "$RUN_DIR/baseline.tar")"

echo "== targets"
TARGETS=$(python3 -c '
import json, sys
f = json.load(open(sys.argv[1]))
hosts = f["hosts"] if isinstance(f, dict) else f
for i, h in enumerate(hosts):
    if isinstance(h, str): h = {"host": h}
    label = h.get("label") or f"vm{i+1}"
    state = h.get("state", "idle")
    print(label, h["host"], state)
' "$FLEET")
echo "$TARGETS" | awk '$3 != "idle" {print "SKIP " $1 " " $2 " state=" $3}'

export RUN_TAG LANES APPROVAL_BATCH_ID="$APPROVAL" INVENTORY MODEL TIMEOUT_SECONDS LANE_ENV_B64
while read -r label host state; do
  [ "$state" = "idle" ] || continue
  [ -d "$RUN_DIR/queues/$label" ] || { echo "SKIP $label no queues (built with a different fleet.json?)"; continue; }
  throttle "$FLEET_PARALLEL"
  echo "deploying $label -> $host"
  bash "$SCRIPTS_DIR/drive.sh" --run-dir "$RUN_DIR" --label "$label" --host "$host" > "$RUN_DIR/deploy_$label.log" 2>&1 &
done <<<"$TARGETS"
wait

echo "== results"
ok=0; bad=0
for log in "$RUN_DIR"/deploy_*.log; do
  [ -f "$log" ] || continue
  if grep -q '^DEPLOY_OK' "$log"; then ok=$((ok + 1)); echo "OK   $(basename "$log" .log | sed 's/deploy_//'): $(grep '^DEPLOY_OK' "$log" | tail -1 | cut -c1-120)"
  else bad=$((bad + 1)); echo "FAIL $(basename "$log" .log | sed 's/deploy_//'): $(grep -E 'FAIL|refusing|ALREADY|Error|error' "$log" | tail -1 | cut -c1-160)"; fi
done
echo "deployed_ok=$ok failed=$bad run_tag=$RUN_TAG"
[ "$bad" -eq 0 ]
