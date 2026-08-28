#!/bin/bash
# Drive one workbook through gates 5-15 of create-harbor-task.
# Gate 15.5 (additional-assumptions-dialogue) is agent-only and is not part of
# this shell automation.
# Staging root tasks_outputs_mcp/ is shape-agnostic: MCP or plain.
# Usage: build_one.sh <WB>
set -uo pipefail
# Every gate path below is relative, so a failed cd would run all 15 gates --
# and stamp BUILDOK -- against whatever tree the caller happens to be in.
cd "$(dirname "$0")" || { echo "BUILDFAIL cd :: cannot cd to the pipeline root"; exit 1; }

WB="$1"
if [ -f "4-10 100/$WB.xlsx" ]; then SOURCE="4-10 100"; else SOURCE="batch-src"; fi
RUN="runs/$WB-variable-sources"
STAGED="tasks_outputs_mcp/$WB-outputs"
NAT_RUN="runs/$WB-instruction-naturalization"
D=.cursor/skills/task-disclosure/scripts/disclose.py
LOG="/tmp/build/$WB.log"
mkdir -p /tmp/build

fail() { echo "BUILDFAIL $WB gate=$1 :: $2"; exit 1; }

(
PLAIN=0
# ---- gate 4: versioned segmentation generation --------------------------
SEG_VALIDATION=$(python3 -m xl_seg.publication validate "seg_out/$WB" \
  --source "$SOURCE/$WB.xlsx" --ast-dir "ast_out/$WB" \
  --validate-live-evidence --require-pass 2>/dev/null) || exit 118
GENERATION_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["generation_id"])' \
  <<<"$SEG_VALIDATION") || exit 118
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
python3 gen_normalizer.py "$WB" 2>/dev/null || exit 92
python3 "$RUN/normalize_$WB.py" >/dev/null 2>&1 || exit 92
cp "$RUN/normalized.json" "/tmp/build/$WB.n1.json"
python3 "$RUN/normalize_$WB.py" >/dev/null 2>&1 || exit 92
cmp -s "/tmp/build/$WB.n1.json" "$RUN/normalized.json" || exit 93   # not byte-stable

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

python3 plain_eligibility.py "$RUN" >/tmp/build/$WB.mode || exit 114
MODE=$(cat /tmp/build/$WB.mode)
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
python3 /tmp/maskability.py "$WB" >/dev/null 2>&1 || exit 96

# ---- gate 9: deterministic double build + validate + smoke -------------
rm -rf "$RUN/mcp-build-a" "$RUN/mcp-build-b" "$RUN/mcp"
python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-a" \
  --workbook "$WB" --source "$SOURCE" >/dev/null 2>&1 || exit 97
python3 xl_variable_mcp.py build "$RUN/normalized.json" "$RUN/mcp-build-b" \
  --workbook "$WB" --source "$SOURCE" >/dev/null 2>&1 || exit 97
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
python3 xl_input_mask.py "$WB" --source "$SOURCE" --seg-dir seg_out \
  --ast-dir ast_out --segmentation-mode strict \
  --expected-generation-id "$GENERATION_ID" \
  -o inputs_out_mcp --mask-cells "$RUN/mcp/mask_cells.json" >/dev/null 2>&1 || exit 101
NOW=$(shasum -a 256 "inputs_out/$WB-inputs.xlsx" | awk '{print $1}')
[ "$BASE_SHA" = "$NOW" ] || exit 102
[ -f "inputs_out_mcp/$WB-inputs.xlsx" ] || exit 103
fi

# ---- gate 11: package to staging ---------------------------------------
rm -rf "$STAGED"
if [ "$PLAIN" -eq 1 ]; then
  python3 xl_output_task.py "$WB" --source "$SOURCE" --seg-root seg_out \
    --ast-dir ast_out --segmentation-mode strict \
    --expected-generation-id "$GENERATION_ID" \
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
  python3 xl_output_task.py "$WB" --source "$SOURCE" --seg-root seg_out \
    --ast-dir ast_out --segmentation-mode strict \
    --expected-generation-id "$GENERATION_ID" \
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
python3 "$D" select --task-dir "$STAGED" --golden "$SOURCE/$WB.xlsx" \
  --ast-dir ast_out --seg-root seg_out --segmentation-mode strict \
  --expected-generation-id "$GENERATION_ID" \
  >/dev/null 2>&1 || exit 106
python3 "$D" probe   --task-dir "$STAGED" >/dev/null 2>&1 || exit 106
python3 "$D" roles   --task-dir "$STAGED" >/dev/null 2>&1 || exit 106
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

# ---- gate 13 snapshot (naturalization applied separately) --------------
rm -rf "$NAT_RUN"; mkdir -p "$NAT_RUN"
cp "$STAGED/instruction.md" "$NAT_RUN/source.md"

# ---- gate 15: exact-answer grader smoke --------------------------------
# Gate 15.5 (additional-assumptions-dialogue writer/reviewer/apply) is not
# automated here.
GS="$RUN/grader-smoke"; rm -rf "$GS"; mkdir -p "$GS/workspace" "$GS/output"
python3 - "$STAGED/tests/answer_key.json" "$GS/workspace/answers.json" <<'PY' || exit 111
import json, sys
key = json.load(open(sys.argv[1], encoding="utf-8"))
json.dump(key["targets"], open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PY
python3 "$STAGED/tests/run_grader.py" --workspace "$GS/workspace" \
  --answer-key "$STAGED/tests/answer_key.json" --output-dir "$GS/output" \
  --mode discrete >/dev/null 2>&1 || exit 112
python3 -c "
import json,sys
r=json.load(open('$GS/output/reward.json'))
sys.exit(0 if r.get('score')==1.0 else 1)" || exit 113
exit 0
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
  *)   fail ?  "unexpected rc=$RC" ;;
esac
