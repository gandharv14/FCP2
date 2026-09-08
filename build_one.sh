#!/bin/bash
# Drive one workbook through gates 5-15 of create-harbor-task.
# Gate 15.5 (additional-assumptions-dialogue) is agent-only and is not part of
# this shell automation.
# Staging root tasks_outputs_mcp/ is shape-agnostic: MCP or plain.
# Usage: build_one.sh <WB> [--release-id ID] [--gdpval-prose]
#        build_one.sh <WB> --source-generation-id ID --segmentation-generation-id ID
# --gdpval-prose (default off) swaps gate 13 from naturalize-finance-task-instruction
# to gdpval-prose-instruction (heading-free GDPval-style brief). Pass it on both the
# initial call and the --resume-after-naturalization call.
set -uo pipefail
# Every gate path below is relative, so a failed cd would run all 15 gates --
# and stamp BUILDOK -- against whatever tree the caller happens to be in.
cd "$(dirname "$0")" || { echo "BUILDFAIL cd :: cannot cd to the pipeline root"; exit 1; }

WB="$1"
shift
RELEASE_BASE="${RELEASE_ROOT:-release_out}"
SOURCE_GENERATION_ROOT="${SOURCE_GENERATION_ROOT:-source_out}"
SEGMENTATION_ROOT="${SEGMENTATION_ROOT:-seg_out}"
RELEASE_ID=""
SOURCE_GENERATION_ID=""
SEGMENTATION_GENERATION_ID=""
RESUME=0
GDPVAL_PROSE="${HARBOR_GDPVAL_PROSE:-0}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --resume-after-naturalization) RESUME=1; shift ;;
    --gdpval-prose) GDPVAL_PROSE=1; shift ;;
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    --source-generation-id) SOURCE_GENERATION_ID="$2"; shift 2 ;;
    --segmentation-generation-id) SEGMENTATION_GENERATION_ID="$2"; shift 2 ;;
    *) echo "BUILDFAIL $WB args :: unknown argument $1"; exit 1 ;;
  esac
done
RUN="runs/$WB-variable-sources"
STAGED="tasks_outputs_mcp/$WB-outputs"
# Gate 13 has two implementations. Default: the two-span naturalizer. With
# --gdpval-prose: the heading-free prose skill. Same CLI verbs, different
# recovery script and state dir, so a resume with the wrong flag fails closed.
if [ "$GDPVAL_PROSE" = "1" ]; then
  NAT_RUN="runs/$WB-instruction-prose"
  NAT_SCRIPT=.cursor/skills/gdpval-prose-instruction/scripts/prose_recovery.py
  NAT_SKILL=gdpval-prose-instruction
  NAT_RESUME_FLAGS="--resume-after-naturalization --gdpval-prose"
else
  NAT_RUN="runs/$WB-instruction-naturalization"
  NAT_SCRIPT=.cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py
  NAT_SKILL=naturalize-finance-task-instruction
  NAT_RESUME_FLAGS="--resume-after-naturalization"
fi
D=.cursor/skills/task-disclosure/scripts/disclose.py
# Gate 8 runs an agent-authored script. Keeping it beside the run artifacts
# rather than in world-writable /tmp keeps an editable-by-anyone path off the
# execution path.
MASKABILITY="${MASKABILITY_SCRIPT:-$RUN/maskability.py}"
mkdir -p "$RUN"
# Per-run scratch, so two builds of the same workbook cannot overwrite each
# other's intermediates. Fixed /tmp/build/$WB.* paths used to make any
# concurrency unsafe.
BUILD_TMP="$(mktemp -d "${TMPDIR:-/tmp}/harbor-build-$WB.XXXXXX")" \
  || { echo "BUILDFAIL $WB tmp :: cannot create scratch dir"; exit 1; }
trap 'rm -rf "$BUILD_TMP"' EXIT INT TERM HUP
# Inside the run dir rather than a shared /tmp path, so concurrent builds of one
# workbook cannot overwrite each other's log. Worktree quarantine copies runs/
# along with everything else, so a failed build's log is still preserved; only a
# successfully delivered worktree discards it, and then it is not needed. Set
# BUILD_LOG to keep logs outside the worktree entirely.
LOG="${BUILD_LOG:-$RUN/build.log}"

if [ "$RESUME" -eq 1 ]; then
  python3 "$NAT_SCRIPT" verify-applied "$NAT_RUN" >/dev/null 2>&1 \
    || { echo "BUILDFAIL $WB gate=13 :: $NAT_SKILL is not safely applied (state dir $NAT_RUN; resume must carry the same --gdpval-prose setting as the initial call)"; exit 1; }
  python3 "$D" verify --task-dir "$STAGED" >/dev/null 2>&1 \
    || { echo "BUILDFAIL $WB gate=13 :: post-naturalization disclosure failed"; exit 1; }
  GS="$RUN/grader-smoke"
  rm -rf "$GS"
  mkdir -p "$GS/workspace" "$GS/output"
  python3 - "$STAGED/tests/answer_key.json" "$GS/workspace/answers.json" <<'PY' \
    || { echo "BUILDFAIL $WB gate=15 :: answer-key setup failed"; exit 1; }
import json, sys
key = json.load(open(sys.argv[1], encoding="utf-8"))
json.dump(key["targets"], open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PY
  python3 "$STAGED/tests/run_grader.py" --workspace "$GS/workspace" \
    --answer-key "$STAGED/tests/answer_key.json" --output-dir "$GS/output" \
    --mode discrete >/dev/null 2>&1 \
    || { echo "BUILDFAIL $WB gate=15 :: grader failed"; exit 1; }
  python3 -c "
import json,sys
r=json.load(open('$GS/output/reward.json'))
sys.exit(0 if r.get('score')==1.0 else 1)" \
    || { echo "BUILDFAIL $WB gate=15 :: grader score is not 1.0"; exit 1; }
  echo "NATURALIZATION_OK_DOWNSTREAM_GATES_PENDING $WB" >&2
  echo "Continue faithfulness, oracle, dialogue, and promotion gates." >&2
  exit 76
fi

if [ -n "$SOURCE_GENERATION_ID" ] || [ -n "$SEGMENTATION_GENERATION_ID" ]; then
  [ -n "$SOURCE_GENERATION_ID" ] && [ -n "$SEGMENTATION_GENERATION_ID" ] \
    || { echo "BUILDFAIL $WB args :: both candidate generation IDs are required"; exit 1; }
  [ -z "$RELEASE_ID" ] \
    || { echo "BUILDFAIL $WB args :: release and candidate modes conflict"; exit 1; }
  SOURCE_STATE=$(python3 -m xl_release_publication resolve-candidate "$WB" \
    --source-root "$SOURCE_GENERATION_ROOT/$WB" \
    --source-generation-id "$SOURCE_GENERATION_ID" \
    --segmentation-root "$SEGMENTATION_ROOT/$WB" \
    --segmentation-generation-id "$SEGMENTATION_GENERATION_ID" 2>/dev/null) \
    || { echo "BUILDFAIL $WB gate=source :: invalid inactive candidate tuple"; exit 1; }
  PIN_ARGS=(--source-generation-root "$SOURCE_GENERATION_ROOT" \
    --source-generation-id "$SOURCE_GENERATION_ID" \
    --segmentation-generation-id "$SEGMENTATION_GENERATION_ID")
  NORMALIZER_PIN_ARGS=(--source-generation-root "$SOURCE_GENERATION_ROOT" \
    --source-generation-id "$SOURCE_GENERATION_ID" \
    --segmentation-root "$SEGMENTATION_ROOT" \
    --segmentation-generation-id "$SEGMENTATION_GENERATION_ID")
else
  RESOLVE_ARGS=()
  [ -z "$RELEASE_ID" ] || RESOLVE_ARGS+=(--release-id "$RELEASE_ID")
  SOURCE_STATE=$(python3 -m xl_release_publication resolve \
    "$RELEASE_BASE/$WB" "$WB" \
    --source-root "$SOURCE_GENERATION_ROOT/$WB" \
    --segmentation-root "$SEGMENTATION_ROOT/$WB" \
    "${RESOLVE_ARGS[@]}" 2>/dev/null) \
    || { echo "BUILDFAIL $WB gate=source :: no validated current release"; exit 1; }
  RELEASE_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["release_id"])' \
    <<<"$SOURCE_STATE") || exit 1
  PIN_ARGS=(--release-root "$RELEASE_BASE" --release-id "$RELEASE_ID" \
    --source-generation-root "$SOURCE_GENERATION_ROOT")
  NORMALIZER_PIN_ARGS=(--release-root "$RELEASE_BASE" --release-id "$RELEASE_ID" \
    --source-generation-root "$SOURCE_GENERATION_ROOT" \
    --segmentation-root "$SEGMENTATION_ROOT")
fi
SOURCE_ROOT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["source_root"])' \
  <<<"$SOURCE_STATE") || exit 1
SOURCE_FILE=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["source_path"])' \
  <<<"$SOURCE_STATE") || exit 1
SOURCE_SHA256=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["source_sha256"])' \
  <<<"$SOURCE_STATE") || exit 1
AST_ROOT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["ast_root"])' \
  <<<"$SOURCE_STATE") || exit 1
GENERATION_ID=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["bindings"]["segmentation_generation_id"])' \
  <<<"$SOURCE_STATE") || exit 1

fail() { echo "BUILDFAIL $WB gate=$1 :: $2"; exit 1; }

(
PLAIN=0
# ---- gate 4: versioned segmentation generation --------------------------
python3 - "$WB" "$GENERATION_ID" <<'PY' >/dev/null 2>&1 || exit 119
import sys
from pathlib import Path
from xl_seg.publication import validate_inputs_sidecar
wb, generation_id = sys.argv[1:]
validate_inputs_sidecar(
    Path("inputs_out") / f"{wb}-inputs.xlsx",
    expected_generation_id=generation_id,
    generation_dir=Path("seg_out") / wb / "generations" / generation_id,
)
PY

# ---- gate 5: import ------------------------------------------------------
python3 xl_variable_mcp.py import "$RUN/$WB-inputs-variable-sources.md" \
  "$RUN/draft.json" >/dev/null 2>&1 || exit 91

# ---- gate 6: generate + run atomic normalizer ----------------------------
python3 gen_normalizer.py "$WB" --source-file "$SOURCE_FILE" \
  --source-sha256 "$SOURCE_SHA256" "${NORMALIZER_PIN_ARGS[@]}" \
  2>/dev/null || exit 92
# Byte-stability needs two runs of the generated normalizer. These stay
# sequential: the emitted script reads only draft.json and source_profiles.json
# -- the workbook loads all happen once inside gen_normalizer.analyse above --
# so both runs take well under a second and overlapping them would add a race
# surface for no gain.
#
# NORMALIZE_OUT_DIR is cleared for the first run and set for the second. An
# inherited value would send the first run's output elsewhere, leaving a stale
# $RUN/normalized.json for cmp to compare against and pass -- a silently wrong
# spec rather than a failure.
NORMALIZE_OUT_DIR= python3 "$RUN/normalize_$WB.py" >/dev/null 2>&1 || exit 92
NORMALIZE_OUT_DIR="$BUILD_TMP/normalize-b" \
  python3 "$RUN/normalize_$WB.py" >/dev/null 2>&1 || exit 92
# Compared directly, with no intermediate copy: distinct output dirs mean both
# results are already on disk at their own paths.
cmp -s "$RUN/normalized.json" "$BUILD_TMP/normalize-b/normalized.json" \
  || exit 93   # not byte-stable

python3 - "$RUN/draft.json" "$RUN/normalized.json" "$RUN/normalization_report.json" <<'PY' >/dev/null 2>&1 || exit 94
import json, sys
draft, spec, report = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
draft_ids = [r["draft_id"] for r in draft["rows"]]
rows = report["dispositions"]; seen = [r["draft_id"] for r in rows]
assert len(seen) == len(set(seen)) and set(seen) == set(draft_ids)
variables = {v["id"] for v in spec["variables"]}
for row in rows:
    if row["status"] == "included":
        assert row.get("variable_ids") and set(row["variable_ids"]) <= variables
    elif row["status"] == "excluded":
        assert str(row.get("reason", "")).strip()
    else:
        raise SystemExit(1)
PY

MODE=$(python3 plain_eligibility.py "$RUN") || exit 114
if [ "$MODE" = plain ]; then
  PLAIN=1
  python3 - "$RUN/normalized.json" <<'PY' >/dev/null 2>&1 || exit 115
import json, sys
spec = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if spec.get("variables") == [] else 1)
PY
  rm -rf "$RUN/mcp" "$RUN/mcp-build-a" "$RUN/mcp-build-b"
  rm -f "inputs_out_mcp/$WB-inputs.xlsx"
fi

if [ "$PLAIN" -eq 0 ]; then
# ---- gate 7: spec validation --------------------------------------------
python3 xl_variable_mcp.py validate-spec "$RUN/normalized.json" >/dev/null 2>&1 || exit 95

# ---- gate 8: maskability ------------------------------------------------
# A missing script is not a masking leak; reporting it as one sends the
# operator looking for a leak that does not exist.
[ -f "$MASKABILITY" ] || exit 122
python3 "$MASKABILITY" "$WB" >/dev/null 2>&1 || exit 96

# ---- gate 9: deterministic double build + validate + smoke -------------
rm -rf "$RUN/mcp-build-a" "$RUN/mcp-build-b" "$RUN/mcp"
# Byte-stability needs two independent builds, but not two waits. They are safe
# to overlap: each writes only inside its own output dir, the seed comes from
# the spec rather than the clock, and there is no shared temp path or lock.
# Separate processes, not threads -- verify_against_golden sets the
# process-global warnings filter.
#
# Each build calls verify_against_golden, which loads the golden workbook, so
# overlapping them holds two workbooks in memory at once. On a lane whose
# memory reservation was sized for serial execution that can trip the cgroup
# ceiling, so HARBOR_SEQUENTIAL_BUILDS=1 forces the old behavior.
if [ "${HARBOR_SEQUENTIAL_BUILDS:-0}" = "1" ]; then
  python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-a" \
    --workbook "$WB" --source "$SOURCE_ROOT" >/dev/null 2>&1 || exit 97
  python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-b" \
    --workbook "$WB" --source "$SOURCE_ROOT" >/dev/null 2>&1 || exit 97
else
  python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-a" \
    --workbook "$WB" --source "$SOURCE_ROOT" >/dev/null 2>&1 &
  BUILD_A=$!
  python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-b" \
    --workbook "$WB" --source "$SOURCE_ROOT" >/dev/null 2>&1 &
  BUILD_B=$!
  # This script runs without `set -e`, so a bare `wait` would discard both exit
  # codes. Wait for both before judging, so a failure in one never orphans the
  # other outside this subshell.
  wait "$BUILD_A"; BUILD_A_STATUS=$?
  wait "$BUILD_B"; BUILD_B_STATUS=$?
  [ "$BUILD_A_STATUS" -eq 0 ] && [ "$BUILD_B_STATUS" -eq 0 ] || exit 97
fi
diff -qr "$RUN/mcp-build-a" "$RUN/mcp-build-b" >/dev/null 2>&1 || exit 98
python3 - "$RUN/mcp-build-a" <<'PY' >/dev/null 2>&1 || exit 99
import sys
from pathlib import Path
from mcp_env.validate import validate
r = validate(Path(sys.argv[1]))
raise SystemExit(0 if r.get("valid") is True else 1)
PY
mv "$RUN/mcp-build-a" "$RUN/mcp"
uv run --python 3.12 --with fastmcp --with openpyxl \
  python xl_variable_mcp.py smoke "$RUN/mcp" >/dev/null 2>&1 || exit 100

# ---- gate 10: mask MCP inputs separately -------------------------------
BASE_SHA=$(shasum -a 256 "inputs_out/$WB-inputs.xlsx" | awk '{print $1}')
python3 xl_input_mask.py "$WB" --source "$SOURCE_ROOT" --seg-dir "$SEGMENTATION_ROOT" \
  --ast-dir "$AST_ROOT" --segmentation-mode strict \
  --expected-generation-id "$GENERATION_ID" \
  "${PIN_ARGS[@]}" \
  -o inputs_out_mcp --mask-cells "$RUN/mcp/mask_cells.json" >/dev/null 2>&1 || exit 101
NOW=$(shasum -a 256 "inputs_out/$WB-inputs.xlsx" | awk '{print $1}')
[ "$BASE_SHA" = "$NOW" ] || exit 102
[ -f "inputs_out_mcp/$WB-inputs.xlsx" ] || exit 103
fi

# ---- gate 11: package to staging ---------------------------------------
rm -rf "$STAGED"
if [ "$PLAIN" -eq 1 ]; then
  python3 xl_output_task.py "$WB" --source "$SOURCE_ROOT" --seg-root "$SEGMENTATION_ROOT" \
    --ast-dir "$AST_ROOT" --segmentation-mode strict \
    "${PIN_ARGS[@]}" \
    --inputs-root inputs_out \
    --variable-source-audit-inputs-root inputs_out \
    --variable-source-audit-root runs \
    --variable-source-audit-model openai/gpt-5.6-sol \
    --no-naturalize -o tasks_outputs_mcp >/dev/null 2>&1 || exit 104
  for f in instruction.md task.toml tests/answer_key.json tests/outputs.json \
           tests/segmentation_generation_manifest.json \
           tests/inputs_generation.json \
           tests/test.sh tests/finance_grader tests/normalization_exclusions.json \
           "environment/$WB-inputs.xlsx" environment/Dockerfile; do
    [ -e "$STAGED/$f" ] || exit 105
  done
  python3 - "$STAGED" "$WB" <<'PY' >/dev/null 2>&1 || exit 116
import sys
from pathlib import Path
task, wb = Path(sys.argv[1]), sys.argv[2]
toml = (task / "task.toml").read_text(encoding="utf-8")
text = (task / "instruction.md").read_text(encoding="utf-8")
if (task / "environment" / "mcp-server").exists():
    raise SystemExit("mcp-server leaked into plain bundle")
if (task / "environment" / "docker-compose.yaml").exists() or (
        task / "environment" / "docker-compose.yml").exists():
    raise SystemExit("compose leaked into plain bundle")
if "[[environment.mcp_servers]]" in toml:
    raise SystemExit("mcp_servers leaked into plain bundle")
if "## Research data service" in text:
    raise SystemExit("research section leaked into plain instruction")
if (task / "tests" / "masked_inputs.json").exists():
    raise SystemExit("masked_inputs.json leaked into plain bundle")
PY
  python3 - "$STAGED" "$WB" <<'PY' >/dev/null 2>&1 || exit 117
import sys
from pathlib import Path
from plain_eligibility import check_plain_environment
report = check_plain_environment(Path(sys.argv[1]), sys.argv[2])
raise SystemExit(0 if report.get("valid") else 1)
PY
else
  python3 xl_output_task.py "$WB" --source "$SOURCE_ROOT" --seg-root "$SEGMENTATION_ROOT" \
    --ast-dir "$AST_ROOT" --segmentation-mode strict \
    "${PIN_ARGS[@]}" \
    --inputs-root inputs_out_mcp \
    --variable-source-audit-inputs-root inputs_out \
    --variable-source-audit-root runs \
    --variable-source-audit-model openai/gpt-5.6-sol \
    --mcp "$RUN/mcp" --no-naturalize -o tasks_outputs_mcp >/dev/null 2>&1 || exit 104
  for f in instruction.md task.toml tests/answer_key.json tests/masked_inputs.json \
           tests/segmentation_generation_manifest.json \
           tests/inputs_generation.json \
           "environment/$WB-inputs.xlsx"; do
    [ -e "$STAGED/$f" ] || exit 105
  done
fi

# ---- gate 12: unified disclosure ---------------------------------------
python3 "$D" select --task-dir "$STAGED" --golden "$SOURCE_FILE" \
  --ast-dir "$AST_ROOT" --seg-root "$SEGMENTATION_ROOT" --segmentation-mode strict \
  "${PIN_ARGS[@]}" \
  >/dev/null 2>&1 || exit 106
# probe and roles both read only bands.json from select and write different
# files, with no stage index or lock between them, so they can overlap. Note
# roles opens the golden workbook and probe does not.
python3 "$D" probe   --task-dir "$STAGED" >/dev/null 2>&1 &
PROBE_PID=$!
python3 "$D" roles   --task-dir "$STAGED" >/dev/null 2>&1 &
ROLES_PID=$!
wait "$PROBE_PID"; PROBE_STATUS=$?
wait "$ROLES_PID"; ROLES_STATUS=$?
[ "$PROBE_STATUS" -eq 0 ] && [ "$ROLES_STATUS" -eq 0 ] || exit 106
# Collisions are fine as long as a complete Sol High resolution file covers them.
python3 - "$WB" <<'PY' >/dev/null 2>&1 || exit 107
import json, os, sys
wb = sys.argv[1]
d = f"runs/disclosure/{wb}-outputs"
cp = f"{d}/ambiguous_roles.json"
cases = json.load(open(cp)) if os.path.exists(cp) else {}
cases = cases.get("cases") if isinstance(cases, dict) else cases
cases = cases or []
if not cases:
    raise SystemExit(0)
rp = f"{d}/role_resolutions.json"
if not os.path.exists(rp):
    raise SystemExit(1)
r = json.load(open(rp))
if r.get("agent_model") != "gpt-5.6-sol-high":
    raise SystemExit(1)
have = {x["case_id"] for x in (r.get("resolutions") or [])}
want = {c["case_id"] for c in cases}
raise SystemExit(0 if want <= have else 1)
PY
python3 "$D" detect  --task-dir "$STAGED" >/dev/null 2>&1 || exit 108
python3 "$D" context --task-dir "$STAGED" >/dev/null 2>&1 || exit 108
python3 "$D" write   --task-dir "$STAGED" >/dev/null 2>&1 || exit 108
python3 "$D" verify  --task-dir "$STAGED" >/dev/null 2>&1 || exit 109
python3 - "$STAGED" <<'PY' >/dev/null 2>&1 || exit 110
import json, sys
from pathlib import Path
task = Path(sys.argv[1])
records = json.load(open(task / "tests/disclosure.json", encoding="utf-8"))
has = "## Workbook disclosure" in (task / "instruction.md").read_text(encoding="utf-8")
raise SystemExit(0 if has == bool(records.get("agent_records")) else 1)
PY

# Retain AST evidence: strict live validation and safe reruns require it.

# ---- gate 13 recoverable state (rewrite applied separately by the skill) --
python3 "$NAT_SCRIPT" \
  init "$STAGED/instruction.md" --state-dir "$NAT_RUN" \
  --instruction "$STAGED/instruction.md" --task-toml "$STAGED/task.toml" \
  --answer-key "$STAGED/tests/answer_key.json" >/dev/null 2>&1 || exit 120
exit 121
) > "$LOG" 2>&1
RC=$?

case $RC in
  0)   V=$(python3 -c "
import json
from pathlib import Path
s=json.load(open('$RUN/normalized.json'))
r=json.load(open('$RUN/normalization_report.json'))
d=json.load(open('$STAGED/tests/disclosure.json'))
elig={}
p=Path('$RUN/plain_eligibility.json')
if p.is_file():
    elig=json.loads(p.read_text())
if elig.get('mode')=='plain':
    print('plain, 0 vars, %d disclosure records, %d excl (%s)'%(
      len(d.get('agent_records') or []), r['excluded_rows'],
      elig.get('plain_reason') or elig.get('reason') or 'excluded'))
else:
    m=json.load(open('$RUN/mcp/mask_cells.json'))
    print('%d vars, %d masked, %d disclosure records, %d incl/%d excl'%(
      len(s['variables']),len(m),len(d.get('agent_records') or []),
      r['included_rows'],r['excluded_rows']))
")
       echo "BUILDOK $WB :: $V" ;;
  91)  fail 5  "import failed" ;;
  92)  fail 6  "normalizer generation or execution failed" ;;
  93)  fail 6  "normalizer not byte-stable" ;;
  94)  fail 6  "dispositions not one-to-one/complete" ;;
  95)  fail 7  "validate-spec failed" ;;
  96)  fail 8  "maskability leak" ;;
  97)  fail 9  "MCP build failed" ;;
  98)  fail 9  "builds not byte-identical" ;;
  99)  fail 9  "MCP validation failed" ;;
  100) fail 9  "MCP smoke failed" ;;
  101) fail 10 "MCP input masking failed" ;;
  102) fail 10 "baseline inputs mutated" ;;
  103) fail 10 "masked inputs missing" ;;
  104) fail 11 "packaging failed" ;;
  105) fail 11 "staged bundle missing required artifact" ;;
  106) fail 12 "disclosure select/probe/roles failed" ;;
  107) fail 12 "ambiguous roles need Sol High arbitration" ;;
  108) fail 12 "disclosure detect/context/write failed" ;;
  109) fail 12 "disclosure verify failed" ;;
  110) fail 12 "disclosure heading/record mismatch" ;;
  111) fail 15 "answer key extraction failed" ;;
  112) fail 15 "grader run failed" ;;
  113) fail 15 "grader score != 1.0" ;;
  114) fail 6  "zero variables but not plain-eligible" ;;
  115) fail 6  "plain path reached with a non-empty spec" ;;
  116) fail 11 "MCP artifact leaked into a plain bundle" ;;
  117) fail 11 "plain environment hygiene failed" ;;
  118) fail 4  "versioned segmentation generation gate failed" ;;
  119) fail 4  "baseline inputs generation binding failed" ;;
  120) fail 13 "$NAT_SKILL recovery initialization failed" ;;
  121) echo "BUILDPENDING $WB gate=13 :: run /$NAT_SKILL, then rerun with $NAT_RESUME_FLAGS"; exit 75 ;;
  122) fail 8  "maskability review script missing at $MASKABILITY" ;;
  *)   fail ?  "unexpected rc=$RC" ;;
esac
