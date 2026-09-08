#!/bin/bash
# Runs ON a VM (fed over stdin by drive.sh). Deploys one memory-governed run
# and launches LANES tmux lanes. Uploads are already in /tmp/fleet.
#
# Env (all required unless noted): LABEL, RUN_TAG, LANES, APPROVAL_BATCH_ID,
# INVENTORY (file name under verification_manifests/), MODEL, BASE_SHA, SRC_SHA,
# Q_SHAS (space-separated, one per lane in lane order), optional TIMEOUT_SECONDS,
# optional LANE_ENV_B64 (base64 "KEY=VAL" lines appended as exports to each lane script).
#
# Layout on the VM:
#   $HOME/GDM_FCP/FCP_recursive_v1-$RUN_TAG   immutable code baseline
#   $HOME/GDM_FCP/venv-$RUN_TAG               runtime
#   $HOME/GDM_FCP/fcp-run-$RUN_TAG-$LABEL     queues, state-*, work-*, output-*, logs
set -euo pipefail
: "${LABEL:?}" "${RUN_TAG:?}" "${LANES:?}" "${APPROVAL_BATCH_ID:?}" "${INVENTORY:?}" "${MODEL:?}" "${BASE_SHA:?}" "${SRC_SHA:?}" "${Q_SHAS:?}"

R="$HOME/GDM_FCP"
BASE="$R/FCP_recursive_v1-$RUN_TAG"
RUN="$R/fcp-run-$RUN_TAG-$LABEL"
VENV="$R/venv-$RUN_TAG"
ENVF="$R/.env"
UP=/tmp/fleet
LOCK="$R/.deploy-$RUN_TAG.lock"
HOST_BYTES="$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
LANE_LETTERS=(a b c d e f g h i j k l m n o p q r s t u v w x y z)

# One deploy per tag per host, ever. A second pass must never kill a live run.
mkdir -p "$R"
if ! mkdir "$LOCK" 2>/dev/null; then echo "ALREADY_DEPLOYING_OR_DEPLOYED $LABEL $RUN_TAG"; exit 0; fi
if pgrep -f '[s]cripts.batch.recovery_worker' >/dev/null; then echo "LIVE_WORKERS_PRESENT refusing"; rmdir "$LOCK"; exit 98; fi
if ls -d "$RUN"/state-*/checkpoints >/dev/null 2>&1; then echo "RUN_HAS_STATE refusing to overwrite $RUN"; rmdir "$LOCK"; exit 99; fi

test -f "$ENVF" || { echo "DEPLOY_FAIL no $ENVF"; exit 2; }
test -x "$HOME/.local/bin/agent" || { echo "DEPLOY_FAIL no agent CLI"; exit 2; }
"$HOME/.local/bin/agent" status --format text 2>&1 | grep -q "Logged in" || { echo "DEPLOY_FAIL agent not logged in"; exit 2; }
test "$(sha256sum $UP/baseline.tar | awk '{print $1}')" = "$BASE_SHA" || { echo "DEPLOY_FAIL baseline.tar hash"; exit 2; }
test "$(sha256sum "$UP/sources-$LABEL.tar" | awk '{print $1}')" = "$SRC_SHA" || { echo "DEPLOY_FAIL sources tar hash"; exit 2; }
i=0
for want in $Q_SHAS; do
  lane=${LANE_LETTERS[$i]}
  test "$(sha256sum "$UP/queue-$lane.json" | awk '{print $1}')" = "$want" || { echo "DEPLOY_FAIL queue-$lane.json hash"; exit 2; }
  i=$((i + 1))
done
test "$i" -eq "$LANES" || { echo "DEPLOY_FAIL Q_SHAS count $i != LANES $LANES"; exit 2; }
echo "UPLOADS_OK"

# Remove a partial tree from an earlier failed attempt of this same tag (no state existed, checked above).
for d in "$BASE" "$RUN"; do
  [ -e "$d" ] || continue
  chmod -R u+rwX "$d" 2>/dev/null || true
  rm -rf "$d"
done

mkdir -p "$BASE" "$RUN/sources" "$RUN/logs"
for ((j = 0; j < LANES; j++)); do
  lane=${LANE_LETTERS[$j]}
  mkdir -p "$RUN/work-$lane" "$RUN/state-$lane" "$RUN/output-$lane"
  cp "$UP/queue-$lane.json" "$RUN/queue-$lane.json"
done
tar -C "$BASE" -xf $UP/baseline.tar 2>/dev/null
tar -C "$RUN/sources" -xf "$UP/sources-$LABEL.tar" 2>/dev/null
cp $UP/plan.json "$RUN/plan.json" 2>/dev/null || true
find "$BASE" "$RUN" -name '._*' -delete 2>/dev/null || true
chmod -R u+rwX,go+rX,go-w "$BASE"
chmod 600 "$ENVF"
test -f "$BASE/verification_manifests/$INVENTORY" || { echo "DEPLOY_FAIL inventory $INVENTORY not in baseline"; exit 2; }

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check 'openpyxl>=3.1.5' pytest
fi
"$VENV/bin/python" -c 'import openpyxl, pytest; print("VENV_OK openpyxl", openpyxl.__version__)'

BASE="$BASE" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
base = Path(os.environ["BASE"])
files = {}
for path in sorted(base.rglob("*")):
    if path.is_symlink() or not path.is_file() or path.name == "BASELINE_MANIFEST.json":
        continue
    rel = path.relative_to(base).as_posix()
    parts = rel.split("/")
    if "__pycache__" in parts or ".pytest_cache" in parts or rel.endswith(".pyc") or parts[0] in (".venv", ".env", ".git"):
        continue
    files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((base / "BASELINE_MANIFEST.json").read_text())
actual = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
if len(files) != manifest["file_count"] or actual != manifest["tree_sha256"]:
    raise SystemExit(f"TREE_FAIL count={len(files)} hash={actual}")
print("TREEOK", actual, len(files))
PY

(
  cd "$BASE"
  export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BASE"
  "$VENV/bin/python" -m pytest -q -p no:cacheprovider \
    tests/test_recovery_worker.py tests/test_memory_governor.py tests/test_batch_hardened_runner.py \
    tests/test_hardened_runner_stream_commands.py tests/test_lane_supervisor.py \
    tests/test_scripted_agent_flow.py tests/test_report_render.py 2>&1 | tail -2
)
find "$BASE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

ln -sfn "$VENV" "$BASE/.venv"
ln -sfn "$ENVF" "$BASE/.env"

BASE="$BASE" RUN="$RUN" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
base = Path(os.environ["BASE"]); run = Path(os.environ["RUN"])
files = {}
for path in sorted(base.rglob("*")):
    if path.is_symlink() or not path.is_file():
        continue
    rel = path.relative_to(base).as_posix()
    if rel == ".env" or rel.startswith(".venv/"):
        continue
    files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
(run / "code-baseline.json").write_text(json.dumps({"files": files}, sort_keys=True, indent=2) + "\n")
print("CODE_BASELINE", len(files))
PY

# Prove worktree creation works before any lane starts.
(
  cd "$BASE"
  PYTHONPATH="$BASE" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" - "$BASE" "$RUN" <<'PY'
import sys
from pathlib import Path
from scripts.batch.recovery_worker import create_isolated_worktree, recycle_isolated_worktree
base, run = Path(sys.argv[1]), Path(sys.argv[2])
probe = run / "work-probe"
wt = create_isolated_worktree(base, probe, "0000")
assert (wt / ".venv").is_symlink() and (wt / ".recovery-worktree").is_file(), "worktree incomplete"
(wt / "xl_segment.py").write_text((wt / "xl_segment.py").read_text())
recycle_isolated_worktree(wt, base)
assert not wt.exists(), "recycle left the worktree behind"
probe.rmdir()
print("WORKTREE_PROBE_OK")
PY
)

sudo -n bash "$BASE/scripts/batch/harbor_host_protections.sh" apply --user "$(id -un)" >/dev/null
sudo -n bash "$BASE/scripts/batch/harbor_host_protections.sh" verify --user "$(id -un)" | tee "$RUN/host-verify.txt"
grep -q '^HOSTOK' "$RUN/host-verify.txt"
test ! -e /etc/logrotate.d/harbor-kernel-cap

# Budget dir: fresh per deploy, but only when it is ours (no live workers was checked above).
mkdir -p /var/tmp/harbor-memory
if [ "$(stat -c %U /var/tmp/harbor-memory)" = "$(id -un)" ]; then
  rm -f /var/tmp/harbor-memory/*.json /var/tmp/harbor-memory/*.lock
else
  echo "BUDGET_DIR_OWNED_BY $(stat -c %U /var/tmp/harbor-memory); leaving its files"
fi

# Per-run lane env overrides (safeguards), validated to KEY=VAL and exported last.
LANE_ENV_EXPORTS=""
if [ -n "${LANE_ENV_B64:-}" ]; then
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
      [A-Za-z_][A-Za-z0-9_]*=*) LANE_ENV_EXPORTS="${LANE_ENV_EXPORTS}export ${line}
";;
      *) echo "DEPLOY_FAIL bad lane env line: $line"; exit 2;;
    esac
  done < <(printf '%s' "$LANE_ENV_B64" | base64 -d)
  printf '%s' "$LANE_ENV_EXPORTS" > "$RUN/lane-env.sh"
fi

for ((j = 0; j < LANES; j++)); do
  lane=${LANE_LETTERS[$j]}
  cat > "$RUN/run-lane-$lane.sh" <<EOF
#!/bin/bash
set -u
cd "$BASE"
export PATH="$VENV/bin:\$HOME/.local/bin:\$PATH"
export VIRTUAL_ENV="$VENV"
export PYTHONNOUSERSITE=1
export PYTHONSAFEPATH=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$BASE"
export RECOVERY_QUEUE="$RUN/queue-$lane.json"
export RECOVERY_BASELINE_REPO="$BASE"
export RECOVERY_WORK_ROOT="$RUN/work-$lane"
export RECOVERY_STATE="$RUN/state-$lane"
export RECOVERY_OUTPUT="$RUN/output-$lane"
export RECOVERY_CODE_BASELINE="$RUN/code-baseline.json"
export RECOVERY_MODEL=$MODEL
export RECOVERY_AGENT="\$HOME/.local/bin/agent"
export RECOVERY_INVENTORY="$BASE/verification_manifests/$INVENTORY"
export RECOVERY_APPROVAL_BATCH_ID=$APPROVAL_BATCH_ID
export RECOVERY_LANE_COUNT=$LANES
export RECOVERY_LANE_ID=$LABEL-$lane
export RECOVERY_MEMORY_GOVERNOR=1
export RECOVERY_MEMORY_BUDGET_DIR=/var/tmp/harbor-memory
export RECOVERY_MEMORY_HOST_BYTES=$HOST_BYTES
export RECOVERY_PYTHON="$VENV/bin/python"
${TIMEOUT_SECONDS:+export RECOVERY_TIMEOUT_SECONDS=$TIMEOUT_SECONDS}
$LANE_ENV_EXPORTS
# Exports only above this line. The supervisor loop (worker exit codes ->
# rerun / sleep / lane fix) is versioned in the baseline, not generated here.
exec "$BASE/scripts/batch/run_lane.sh" 2>&1 | tee -a "$RUN/logs/lane-$lane.log"
EOF
  chmod 0755 "$RUN/run-lane-$lane.sh"
done

for ((j = 0; j < LANES; j++)); do
  lane=${LANE_LETTERS[$j]}
  tmux new-session -d -s "fcp-$RUN_TAG-$lane" "$RUN/run-lane-$lane.sh"
  sleep 3
done
sleep 25
for ((j = 0; j < LANES; j++)); do tmux has-session -t "fcp-$RUN_TAG-${LANE_LETTERS[$j]}"; done
WORKERS=$(pgrep -fc '[s]cripts.batch.recovery_worker' || true)
echo "WORKER_PROCS $WORKERS"
for ((j = 0; j < LANES; j++)); do
  lane=${LANE_LETTERS[$j]}
  echo "--- lane $lane tail"; tail -n 3 "$RUN/logs/lane-$lane.log" 2>/dev/null | cut -c1-300
done
echo "--- worktrees"; ls "$RUN"/work-* 2>/dev/null
test "$WORKERS" -ge "$LANES"
echo "DEPLOY_OK $LABEL host_bytes=$HOST_BYTES lanes=$LANES base=$BASE run=$RUN"
