---
name: create-harbor-task
description: Creates fail-closed MCP-backed Harbor workbook tasks with unified pre-run task disclosure. Use when asked to create or rebuild a Harbor task, package an .xlsx or workbook id, generate variable-source MCP inputs, or promote a tasks_outputs bundle.
disable-model-invocation: true
---

# Create an MCP-backed Harbor task

Run the financial-workbook pipeline for exactly one workbook at a time. The
golden workbook is an answer key and build-time validation input; it must never
enter the task environment. This workflow has no plain-task fallback.

## Resolve paths

Accept a workbook path, a filename under `4-10 100`, or an id. Work from the
pipeline repository root and set:

```bash
WB=0256
SOURCE="4-10 100"
AST_ROOT=ast_out
SEG_ROOT=seg_out
BASE_INPUT_ROOT=inputs_out
MCP_INPUT_ROOT=inputs_out_mcp
RUN="runs/$WB-variable-sources"
AUDIT="$RUN/$WB-inputs-variable-sources.md"
INVENTORY="$RUN/$WB-inputs-variable-sources.inventory.json"
DRAFT="$RUN/draft.json"
NORMALIZED="$RUN/normalized.json"
EXCLUSIONS="$RUN/exclusions.json"
DISPOSITIONS="$RUN/normalization_report.json"
PROFILES="$RUN/source_profiles.json"
MCP="$RUN/mcp"
DISCLOSURE=.cursor/skills/task-disclosure/scripts/disclose.py
DISCLOSURE_RUN="runs/disclosure/$WB-outputs"
NAT_RUN="runs/$WB-instruction-naturalization"
STAGE_ROOT=tasks_outputs_mcp
STAGED="$STAGE_ROOT/$WB-outputs"
TASK="tasks_outputs/$WB-outputs"
```

Expected artifacts:

- `ast_out/$WB/{nodes.csv,edges.csv}`
- `seg_out/$WB/{segments.json,curation.toml,lineage.json}`
- `inputs_out/$WB-inputs.xlsx`: unredacted baseline inputs
- `runs/disclosure/$WB-outputs/{bands,probe,records,context,verify}.json`
- `runs/$WB-variable-sources/disclosure-faithfulness.md`
- `tasks_outputs_mcp/$WB-outputs/tests/disclosure.json`
- `runs/$WB-instruction-naturalization/{source.md,candidate.md,validation.json}`
- `runs/$WB-variable-sources/$WB-inputs-variable-sources.{md,inventory.json,metadata.json}`
- `draft.json`, `normalize_$WB.py`, `normalized.json`, `exclusions.json`,
  `normalization_report.json`, `source_profiles.json`, and profile captures
- `mcp/{runtime,eval,server.py,Dockerfile,mask_cells.json,masked_inputs.json}`
- `inputs_out_mcp/$WB-inputs.xlsx`: separately MCP-masked inputs
- `tasks_outputs_mcp/$WB-outputs/`: staged bundle
- `runs/$WB-variable-sources/oracle-report.json`

For multiple requested workbooks, complete all gates serially. Do not share a
normalized spec, MCP bundle, mask list, or staged task between workbook ids.
Stage all requested workbooks before promoting any of them.

## Non-negotiable gates

- Stop on every failed, skipped, missing, ambiguous, or unreviewed gate.
- Segmentation must report `passed: true`; `SKIPPED` is not acceptable.
- Preserve an existing `curation.toml`. Never pass `--recurate` unless the user
  explicitly asks to discard curation.
- Do not silently change curated outputs because normalization, profiling,
  masking, packaging, or rollout is difficult.
- Generate and package the GPT 5.6 Sol variable/source audit. Never pass
  `--no-variable-source-audit`.
- Run `/task-disclosure` against the exact staged MCP workbook before
  naturalization. Registry drift, audit failure, mechanical verification
  failure, or a blocking fresh-review finding is a blocker.
- Run `/naturalize-finance-task-instruction` after the complete instruction is
  packaged. Any generation, validation, or semantic-review failure is a blocker.
- Missing audit credentials or audit failure is a blocker, not permission to
  produce a plain task.
- Missing profile skill, unresolved draft rows, partial masking, source-profile
  validation failure, MCP failure, oracle failure, or grader failure is a
  blocker. Retain diagnostics and leave the current task untouched.
- Never package the old custom-formula hint and conventions sections as a
  fallback for unified disclosure.
- Never place the golden workbook, `eval/`, normalized specs, profile captures,
  snapshots, answer values, or source audit working files in `environment/`.

## Workflow

### 1. Preflight and AST

Do not read or print `.env`; the pipeline reads required credentials itself.

```bash
python3 -m pip install -r requirements.txt
test -f "$SOURCE/$WB.xlsx"
python3 xl_ast_graph.py "$SOURCE/$WB.xlsx" -o "$AST_ROOT"
test -f "$AST_ROOT/$WB/nodes.csv"
test -f "$AST_ROOT/$WB/edges.csv"
```

Reuse a complete AST only when it belongs to the same golden workbook and the
user did not request a rebuild.

### 2. Segment, preserve curation, require PASS

If curation exists, record its hash before running segmentation. Do not use
`--llm` on an already curated workbook unless explicitly requested.

```bash
test ! -f "$SEG_ROOT/$WB/curation.toml" || \
  shasum -a 256 "$SEG_ROOT/$WB/curation.toml"
python3 xl_segment.py "$WB" --source "$SOURCE" \
  --ast-dir "$AST_ROOT" -o "$SEG_ROOT"
```

Summarize every included output and the strongest exclusions from
`curation.toml`. Require user confirmation before continuing. If the user edits
curation, re-run the same command. If an existing curation file changed without
explicit approval, stop.

Require the full verification proof:

```bash
python3 - "$SEG_ROOT/$WB/segments.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
v = data.get("verification") or {}
if v.get("skipped") or v.get("passed") is not True:
    raise SystemExit("segmentation verification is not PASS: %r" % v)
if v.get("seeded_inside_output_cone_count", 0) != 0:
    raise SystemExit("segmentation seeded cells inside the output cone")
print("segmentation PASS")
PY
```

Do not weaken the verifier, use `--no-verify`, or special-case the workbook.

### 3. Build baseline inputs

```bash
python3 xl_input_mask.py "$WB" --source "$SOURCE" \
  --seg-dir "$SEG_ROOT" -o "$BASE_INPUT_ROOT"
test -f "$BASE_INPUT_ROOT/$WB-inputs.xlsx"
shasum -a 256 "$BASE_INPUT_ROOT/$WB-inputs.xlsx"
```

The command must report verification `PASS`, zero surviving formulas, and all
typed cells intact except its documented pasted-answer policy. Keep this file
unchanged: it is the input to the audit even after MCP masking exists.

### 4. Run the GPT 5.6 Sol audit

```bash
python3 xl_variable_source_audit.py "$WB" \
  --inputs-root "$BASE_INPUT_ROOT" \
  --seg-root "$SEG_ROOT" \
  --audit-root runs \
  --model openai/gpt-5.6-sol
```

Require complete metadata, matching inventory SHA-256, model
`openai/gpt-5.6-sol`, and passed qualified-reference/value validation. Reject
invented references or values. Cache reuse is allowed only when the baseline
inventory hash, model, and prompt version match.

### 5. Import every Markdown row

```bash
python3 xl_variable_mcp.py import "$AUDIT" "$DRAFT"
```

Confirm `draft.json.row_count` equals the number of imported data rows and every
row begins as `needs_review`. Import is preservation, not normalization.

### 6. Normalize atomically and account for every row

Create `"$RUN/normalize_$WB.py"` and execute it to write:

- `normalized.json`: atomic variables, one entity/metric/period/scenario/basis/
  unit/status tuple per variable, exact workbook refs and raw values
- `exclusions.json`: explicit, evidence-based exclusions
- `normalization_report.json`: exactly one disposition for each `draft_id`

Each disposition is either `included` with one or more resulting variable ids,
or `excluded` with a non-empty reason. Splitting a series into multiple scalar
variables is expected; silently dropping or retaining `needs_review` rows is
forbidden. Exclude calculations, outputs, internal forecasts, ambiguous values,
and compound series that cannot be masked completely.

The normalizer must write sibling temporary files, flush and `fsync`, validate
the complete set, then use `os.replace` for each final JSON. Never hand-edit a
final JSON in place. Re-running it from the same inputs must be byte-identical.

```bash
python3 "$RUN/normalize_$WB.py"
python3 - "$DRAFT" "$NORMALIZED" "$DISPOSITIONS" <<'PY'
import json, sys
draft, spec, report = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
draft_ids = [r["draft_id"] for r in draft["rows"]]
rows = report["dispositions"]
seen = [r["draft_id"] for r in rows]
if len(seen) != len(set(seen)) or set(seen) != set(draft_ids):
    raise SystemExit("draft row dispositions are not one-to-one and complete")
variables = {v["id"] for v in spec["variables"]}
for row in rows:
    if row["status"] == "included":
        ids = row.get("variable_ids") or []
        if not ids or not set(ids) <= variables:
            raise SystemExit("invalid included disposition: %r" % row)
    elif row["status"] == "excluded":
        if not str(row.get("reason", "")).strip():
            raise SystemExit("excluded disposition lacks reason: %r" % row)
    else:
        raise SystemExit("unresolved disposition: %r" % row)
print("all %d draft rows resolved" % len(draft_ids))
PY
```

### 7. Profile public sources with GPT 5.6 Sol

First load and follow `.cursor/skills/profile-mcp-sources/SKILL.md`. Launch one
or more `generalPurpose` subagents with model `gpt-5.6-sol-high`, batching no
more than six canonical URLs per subagent as that skill requires. Pass only its
redacted source worklist, require the bounded-fetch workflow, and require
`source_profiles.json` plus capture metadata. Do not substitute a different
model or perform an unreviewed profile.

Deduplicate canonical public URLs before fetching. A login, SSO/password form,
401/403, paywall, bot challenge, blocked page, or unreachable site must produce
`status: skipped` with the skill's closed `skip_reason`, no extracted
terminology or excerpt, and generic profile behavior only. Never authenticate,
use credentials, or infer source-specific details from an access-denied page.
Live pages shape terminology and metadata only; they never replace workbook
values.

Run the validator command documented by that skill. The expected invocation is:

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/validate_source_profiles.py \
  "$PROFILES" --inventory "$INVENTORY" --spec "$NORMALIZED" --rehash
python3 .cursor/skills/profile-mcp-sources/scripts/validate_source_profiles.py \
  "$PROFILES" --inventory "$INVENTORY" --spec "$NORMALIZED"
```

Require reviewed profiles, URL/locator attribution, bounded excerpts, hashes,
capture metadata, no auth-derived text, and no workbook-value leakage.
Atomically incorporate only accepted profiles and their `profile_id` /
`dataset_key` references into `normalized.json`; skipped, pending, rejected, or
unmatched sources retain generic rendering. Then run:

```bash
python3 xl_variable_mcp.py validate-spec "$NORMALIZED"
```

### 8. Review maskability, duplicates, and extra cells

Before build, inspect every included variable against the golden and baseline
workbooks. Write `"$RUN/maskability_report.json"` and fail unless:

- every representation of the value that would reveal the masked variable is
  found, including repeated assumptions, active-case copies, sensitivity-table
  centers, text/date/percent renderings, and Embedded Assumptions exposure
- exact value-checked refs are in `workbook.cells`
- display-only or non-comparable duplicates are justified in
  `workbook.extra_cells`
- every `extra_cells` ref has a reason and is reviewed for collateral masking
- a variable with an unmaskable or uncertain duplicate is fully excluded and
  its draft disposition is updated; partial masking is forbidden
- duplicate refs across variables are intentional, compatible, and documented

Re-run the atomic normalizer and profile validator after any change. Do not use
an allowlist to hide an unknown leak.

### 9. Build, validate, and smoke deterministic MCP output

Build twice in fresh sibling directories using the same normalized spec,
profiles, seed, and golden workbook. The build is network-free.

```bash
A="$RUN/mcp-build-a"
B="$RUN/mcp-build-b"
test ! -e "$A" && test ! -e "$B"
python3 xl_variable_mcp.py build "$NORMALIZED" "$A" \
  --workbook "$WB" --source "$SOURCE"
python3 xl_variable_mcp.py build "$NORMALIZED" "$B" \
  --workbook "$WB" --source "$SOURCE"
diff -qr "$A" "$B"
python3 - "$A" <<'PY'
import json, sys
from pathlib import Path
from mcp_env.validate import validate
report = validate(Path(sys.argv[1]))
if report.get("valid") is not True:
    raise SystemExit(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
test ! -e "$MCP"
mv "$A" "$MCP"
uv run --python 3.12 --with fastmcp --with openpyxl \
  python xl_variable_mcp.py smoke "$MCP"
```

Require exact golden-value agreement, unique unsuperseded evidence, complete
acyclic provenance chains, conflicting broad queries, no runtime evaluation
keys, valid reviewed profile references, and byte-identical builds. Keep `B`
and all failed build directories as diagnostics until completion.

### 10. Mask MCP inputs separately

```bash
BASE_SHA=$(shasum -a 256 "$BASE_INPUT_ROOT/$WB-inputs.xlsx" | awk '{print $1}')
python3 xl_input_mask.py "$WB" --source "$SOURCE" \
  --seg-dir "$SEG_ROOT" -o "$MCP_INPUT_ROOT" \
  --mask-cells "$MCP/mask_cells.json"
test "$(shasum -a 256 "$BASE_INPUT_ROOT/$WB-inputs.xlsx" | awk '{print $1}')" \
  = "$BASE_SHA"
test -f "$MCP_INPUT_ROOT/$WB-inputs.xlsx"
```

Require all intended MCP cells blank, no formulas, no unintended typed-cell
loss, and a non-empty mask. Never overwrite the baseline inputs workbook.

### 11. Package to staging with MCP and baseline audit

```bash
test ! -e "$STAGED"
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --seg-root "$SEG_ROOT" \
  --inputs-root "$MCP_INPUT_ROOT" \
  --variable-source-audit-inputs-root "$BASE_INPUT_ROOT" \
  --variable-source-audit-root runs \
  --variable-source-audit-model openai/gpt-5.6-sol \
  --mcp "$MCP" \
  --no-naturalize \
  -o "$STAGE_ROOT"
```

The audit must run or validly cache-reuse against baseline inputs, while the
packaged artifact must come from MCP inputs. Require the MCP server declaration,
compose sidecar, extended timeout, research instructions, audit metadata, and
`tests/masked_inputs.json`. Absence of any required artifact is failure; never
rerun without `--mcp`.

### 12. Build unified task disclosure

Run against the staged bundle and golden. Pass the AST root to `select`; later
commands consume its staged artifacts.

```bash
test -f "$DISCLOSURE"
python3 "$DISCLOSURE" select \
  --task-dir "$STAGED" \
  --golden "$SOURCE/$WB.xlsx" \
  --ast-dir "$AST_ROOT"
python3 "$DISCLOSURE" probe   --task-dir "$STAGED"
python3 "$DISCLOSURE" detect  --task-dir "$STAGED"
python3 "$DISCLOSURE" context --task-dir "$STAGED"
python3 "$DISCLOSURE" write   --task-dir "$STAGED"
python3 "$DISCLOSURE" verify  --task-dir "$STAGED"
test -f "$DISCLOSURE_RUN/bands.json" &&
  test -f "$DISCLOSURE_RUN/records.json" &&
  test -f "$DISCLOSURE_RUN/verify.json" &&
  test -f "$STAGED/tests/disclosure.json"
rg -q '^## Workbook disclosure$' "$STAGED/instruction.md"
```

Require verification without `--force` or `--no-fail`, no old hint/manifest
sections, and no formulas/evidence in the agent-facing instruction.

### 13. Naturalize the complete staged instruction

Load and follow
`.cursor/skills/naturalize-finance-task-instruction/SKILL.md`. Launch exactly
one `generalPurpose` subagent with model `gpt-5.6-sol-high`, preserve every
protected section—including `## Workbook disclosure`—byte-for-byte, and require
deterministic plus clause-by-clause semantic validation before atomically
replacing `"$STAGED/instruction.md"`.

Require `"$NAT_RUN/validation.json"` to report `valid: true` and `applied: true`,
and require `task.toml` to record model `gpt-5.6-sol-high`, endpoint
`cursor-subagent`, the prompt version, and matching source/instruction hashes.
This is the final content mutation; do not continue on fallback or uncertainty.

Re-run `python3 "$DISCLOSURE" verify --task-dir "$STAGED"` against the final
naturalized instruction. Then launch a fresh reviewer with the golden, delivered
workbook, staged instruction, and `tests/disclosure.json`. It must check every
sentence against the golden and fail on false, incomplete, ambiguous,
answer-leaking, or formula-like wording. Save
`"$RUN/disclosure-faithfulness.md"` and stop on a blocking finding.

### 14. Run generalized HTTP oracle

Use the reusable oracle, not a workbook-specific script:

```bash
ORACLE_IMAGE="mcp-$WB-oracle"
ORACLE_CONTAINER="mcp-$WB-oracle-$$"
ORACLE_ALLOWLIST="$RUN/oracle-allowlist.json"
docker build -t "$ORACLE_IMAGE" "$STAGED/environment/mcp-server"
docker run -d --name "$ORACLE_CONTAINER" -p 127.0.0.1::8000 "$ORACLE_IMAGE"
trap 'docker rm -f "$ORACLE_CONTAINER" >/dev/null 2>&1 || true' EXIT
ORACLE_PORT=$(docker port "$ORACLE_CONTAINER" 8000/tcp | awk -F: '{print $NF}')
ORACLE_URL="http://127.0.0.1:$ORACLE_PORT/mcp"
uv run --python 3.12 --with fastmcp --with openpyxl \
  python - "$STAGED" "$MCP" "$ORACLE_URL" \
  "$RUN/oracle-report.json" "$ORACLE_ALLOWLIST" <<'PY'
import asyncio, json, sys
from pathlib import Path
from xl_mcp_oracle import run_oracle
bundle, mcp, url, output, allowlist = sys.argv[1:]
allowlist = Path(allowlist) if Path(allowlist).is_file() else None
report = asyncio.run(run_oracle(
    Path(bundle), Path(mcp), url, allowlist_path=allowlist))
Path(output).write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
if report.get("valid") is not True:
    raise SystemExit("HTTP oracle failed")
print("HTTP oracle PASS")
PY
docker rm -f "$ORACLE_CONTAINER"
trap - EXIT
```

It must build and exercise the shipped sidecar over streamable HTTP, paginate
like an agent, and prove every variable resolves to exact unsuperseded evidence,
all chains are visible and acyclic, broad queries conflict, every intended cell
is blank, no unapproved duplicate representation survives, profile excerpts
are attributed and value-safe, and forbidden build/evaluation artifacts did not
ship. Use an explicit reviewed allowlist only for legitimate unavoidable
duplicates; unknown, duplicate, and unused entries fail rather than becoming
report-only warnings.

### 15. Smoke the exact-answer grader

Use the staged answer key only in a verifier workspace and require discrete
(exact-within-tolerance) score `1.0`:

```bash
GRADER_SMOKE="$RUN/grader-smoke"
mkdir -p "$GRADER_SMOKE/workspace" "$GRADER_SMOKE/output"
python3 - "$STAGED/tests/answer_key.json" \
  "$GRADER_SMOKE/workspace/answers.json" <<'PY'
import json, sys
key = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w", encoding="utf-8") as out:
    json.dump(key["targets"], out, indent=2)
PY
python3 "$STAGED/tests/run_grader.py" \
  --workspace "$GRADER_SMOKE/workspace" \
  --answer-key "$STAGED/tests/answer_key.json" \
  --output-dir "$GRADER_SMOKE/output" \
  --mode discrete
python3 - "$GRADER_SMOKE/output/reward.json" <<'PY'
import json, sys
reward = json.load(open(sys.argv[1], encoding="utf-8"))
if reward.get("score") != 1.0:
    raise SystemExit("exact-answer grader smoke failed: %r" % reward)
print("exact-answer grader score: 1.0")
PY
```

### 16. Promote only after every gate passes

If a requested set contains multiple workbooks, wait until every staged bundle
passes before promoting the set. Promotion uses same-filesystem renames and
rollback; do not delete or overwrite the current bundle in place.

```bash
python3 - "$STAGED" "$TASK" <<'PY'
import os, sys, time
from pathlib import Path
stage, dest = map(Path, sys.argv[1:])
if not stage.is_dir():
    raise SystemExit("validated stage is missing")
backup = dest.with_name(dest.name + ".previous-" + time.strftime("%Y%m%d%H%M%S"))
if backup.exists():
    raise SystemExit("backup path already exists: %s" % backup)
if dest.exists():
    os.replace(dest, backup)
try:
    os.replace(stage, dest)
except Exception:
    if backup.exists() and not dest.exists():
        os.replace(backup, dest)
    raise
print("promoted %s; previous=%s" % (dest, backup if backup.exists() else "none"))
PY
```

Do not run Harbor rollouts unless the user separately asks.

## Completion report

Report, per workbook:

- workbook id, golden path, AST path, segmentation `PASS`, and curation hash
- curated output names/bands and confirmation that curation was preserved
- disclosure run path; custom/convention disclosed, suppressed, standard, and
  unclassified counts; mechanical verdict; fresh faithfulness-review path and
  verdict
- instruction naturalization source/candidate/report paths, pinned Sol model,
  prompt version, source/instruction hashes, and semantic-review result
- baseline input path/hash and separate MCP input path/hash
- audit Markdown/inventory/metadata paths, model, inventory hash, and cache use
- draft row count; included/excluded disposition counts; atomic variable count
- profile count, reviewed count, captured public count, and every
  skipped-auth/paywall/blocked/unreachable source
- maskability report path; `cells`, `extra_cells`, and total masked-cell counts
- normalized spec, exclusions, profiles, and MCP paths; deterministic comparison
  and MCP validation/smoke summaries
- staged path, oracle report and result, exact grader score, final promoted path,
  previous-bundle backup path, and any retained diagnostics

Completion requires all gates to pass. Otherwise report the first blocker and
leave existing task bundles untouched.
