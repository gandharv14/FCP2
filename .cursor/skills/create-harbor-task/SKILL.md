---
name: create-harbor-task
description: Creates fail-closed Harbor workbook tasks with unified pre-run task disclosure and, when agent_records exist, an additional-assumptions Q&A file. MCP-backed when the audit yields maskable variables; a plain (no-MCP) task only when every audit row is genuinely excluded. Use when asked to create or rebuild a Harbor task, package an .xlsx or workbook id, generate variable-source MCP inputs, or promote a tasks_outputs bundle.
disable-model-invocation: true
---

# Create a Harbor workbook task

Run the financial-workbook pipeline for exactly one workbook at a time. The
golden workbook is an answer key and build-time validation input; it must never
enter the task environment. Ship an MCP research sidecar when normalization
produces maskable variables. A plain (no-MCP) task is permitted only when the
hardened eligibility check in `plain_eligibility.py` passes: every draft row
is excluded with a reason, none of the causes are extraction defects, no
forced-exclusion file exists, the first normalization of the run was also
empty, and the draft-row floor is met. An audit that yields no resolvable
candidates is an audit failure, not a plain-task trigger. A spec that was
non-empty and later emptied must never be downgraded to plain.

## Resolve paths

Accept a workbook path, a filename under `4-10 100`, or an id. Work from the
pipeline repository root and set:

```bash
WB=0256
RAW_SOURCE_FILE="4-10 100/$WB.xlsx"
SOURCE_RUN="source_out/$WB"
RELEASE_ROOT="release_out/$WB"
SOURCE_HEALTH="runs/$WB-source-health.json"
RECALC_RUN="runs/$WB-source-recalc"
TRUSTED_RUNNER_PUBLIC_KEY="/etc/harbor/excel-runner-public.pem"
EXCEL_ENGINE_VERSION="<exact-approved-Microsoft-Excel-version>"
# SOURCE, SOURCE_FILE, SOURCE_GENERATION_ID, and AST_ROOT are resolved from one
# immutable candidate tuple during staging, then from current-release.json.
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
AA=.cursor/skills/additional-assumptions-dialogue/SKILL.md
AA_RUN="runs/$WB-additional-assumptions"
STAGE_ROOT=tasks_outputs_mcp
STAGED="$STAGE_ROOT/$WB-outputs"
TASK="tasks_outputs/$WB-outputs"
```

Expected artifacts:

- `release_out/$WB/current-release.json`, immutable
  `releases/<release_id>/release-manifest.json`, and immutable
  `task-generations/<task_generation_id>/generation-manifest.json`
- inactive immutable source
  `generations/<generation_id>/{source/$WB.xlsx,ast/$WB/{nodes.csv,edges.csv},ast-provenance.json,generation-manifest.json}`
- `seg_out/$WB/curation.toml` and the inactive immutable candidate
  `generations/<generation_id>/{generation-manifest.json,segments.json,bands.csv,output_candidates.csv,lineage.json,lineage/}`
- `inputs_out/$WB-inputs.xlsx`: unredacted baseline inputs
- `runs/disclosure/$WB-outputs/{bands,probe,records,context,verify}.json`
- `runs/$WB-variable-sources/disclosure-faithfulness.md`
- `tasks_outputs_mcp/$WB-outputs/tests/disclosure.json`
- `runs/$WB-instruction-naturalization/{source.snapshot.md,state.json,spans.json,attempt-01/,attempt-02/,validation.json}`
- `runs/$WB-variable-sources/$WB-inputs-variable-sources.{md,inventory.json,metadata.json}`
- `draft.json`, `normalize_$WB.py`, `normalized.json`, `exclusions.json`,
  `normalization_report.json`, `source_profiles.json`, and profile captures
- `mcp/{runtime,eval,server.py,Dockerfile,mask_cells.json,masked_inputs.json}`
  (MCP mode only)
- `inputs_out_mcp/$WB-inputs.xlsx`: separately MCP-masked inputs (MCP mode only)
- `tasks_outputs_mcp/$WB-outputs/`: staged bundle (shape-agnostic name)
- `runs/$WB-variable-sources/oracle-report.json` (MCP mode only)
- `runs/$WB-variable-sources/{plain_eligibility.json,first_normalization.json}`
  and, in plain mode, `tests/normalization_exclusions.json`
- `runs/$WB-additional-assumptions/{claims.json,writer_pack.json}` and, when
  `agent_records` is non-empty, `draft.md`, `review.json`, `apply.json`
- `tasks_outputs_mcp/$WB-outputs/environment/additional-assumptions.md` and
  `tests/dialogue-applied.json` when dialogue apply ran

For multiple requested workbooks, complete all gates serially. Do not share a
normalized spec, MCP bundle, mask list, or staged task between workbook ids.
Stage all requested workbooks before promoting any of them.

## Non-negotiable gates

- Stop on every failed, skipped, missing, ambiguous, or unreviewed gate.
- Resolve one `current-release.json` once per production operation. During
  staging, pass both explicit inactive source and segmentation generation IDs.
  Never read source or segmentation `current.json` mid-operation.
- A `restricted_pass` source and its segmentation stay inactive until the task
  generation is immutable and the complete tuple passes release CAS.
- Legacy direct artifacts are an offline compatibility layout. Publication never
  replaces existing direct files or directories; migrate them only while offline.
- Preserve an existing `curation.toml`. Never pass `--recurate` unless the user
  explicitly asks to discard curation.
- Do not silently change curated outputs because normalization, profiling,
  masking, packaging, or rollout is difficult.
- Generate and package the GPT 5.6 Sol variable/source audit. Never pass
  `--no-variable-source-audit`.
- Run `/task-disclosure` against the exact staged workbook before
  naturalization. Registry drift, audit failure, mechanical verification
  failure, or a blocking fresh-review finding is a blocker.
- Run `/naturalize-finance-task-instruction` after the complete instruction is
  packaged. Any generation, validation, or semantic-review failure is a blocker.
- Missing audit credentials or audit failure is a blocker. An audit that
  yields no resolvable candidates is an audit failure, not a plain-task trigger.
- Missing profile skill, unresolved draft rows, partial masking, source-profile
  validation failure, failure of an MCP build that was attempted, failure of
  an oracle that was attempted, or grader failure is a blocker. Skipping MCP
  build and oracle in a verified plain task is not an MCP failure. Retain
  diagnostics and leave the current task untouched.
- Never package the old custom-formula hint and conventions sections as a
  fallback for unified disclosure.
- Never place the golden workbook, `eval/`, normalized specs, profile captures,
  snapshots, answer values, or source audit working files in `environment/`.

## Workflow

### 1. Preflight and AST

Do not read or print `.env`; the pipeline reads required credentials itself.
Only `.xlsx` is eligible for the authoritative-source path.

```bash
python3 -m pip install -r requirements.txt
test -f "$RAW_SOURCE_FILE"
python3 xl_source_health.py observe "$RAW_SOURCE_FILE" -o "$SOURCE_HEALTH"
SOURCE_ROUTE=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["route"])' \
  "$SOURCE_HEALTH")
```

Read `"$SOURCE_HEALTH"`. Stop on `unsupported` or `insufficient_evidence`.
External links/connections, macros, OLE, volatile formulas, data tables, and
unknown iteration semantics are not automatic-recalculation candidates.

For `recalc_candidate`, create a bound request:

```bash
mkdir -p "$RECALC_RUN"
python3 xl_source_recalc.py request "$RAW_SOURCE_FILE" \
  "$RECALC_RUN/recalculated/$WB.xlsx" \
  --allowed-root "$RECALC_RUN/recalculated" \
  --trusted-runner-public-key "$TRUSTED_RUNNER_PUBLIC_KEY" \
  --permitted-engine-version "$EXCEL_ENGINE_VERSION" \
  -o "$RECALC_RUN/request.json"
```

On a Linux/VM host, stop after writing the request. Resume only after a macOS
runner returns the exact hash-bound workbook and `result.json`. On the dedicated
macOS Excel runner, require an `excel-isolation-attestation/v1` file confirming
the dedicated session, disabled network/macros/add-ins/link updates, and
suppressed prompts. The attestation and sandbox runner must be root-owned,
non-writable by the invoking user, stored under protected root-owned parent
directories, and hash-bound to each other. The attestation must also bind
`$TRUSTED_RUNNER_PUBLIC_KEY`. The runner must sign an
`excel-runner-receipt/v1` payload that includes the request, source, output,
engine/version, isolation, calculation-complete, and completion-time claims:

```bash
python3 xl_source_recalc.py execute "$RECALC_RUN/request.json" \
  --source "$RAW_SOURCE_FILE" \
  --allowed-root "$RECALC_RUN/recalculated" \
  --isolation-attestation /etc/harbor/excel-isolation-attestation.json \
  --sandbox-runner /usr/local/sbin/harbor-excel-sandbox \
  -o "$RECALC_RUN/result.json"
python3 xl_source_health.py observe \
  "$RECALC_RUN/recalculated/$WB.xlsx" \
  -o "$RECALC_RUN/source-health-after.json"
EFFECTIVE_CANDIDATE="$RECALC_RUN/recalculated/$WB.xlsx"
EFFECTIVE_HEALTH="$RECALC_RUN/source-health-after.json"
```

For `pass`, use the original as `EFFECTIVE_CANDIDATE` and
`"$SOURCE_HEALTH"` as `EFFECTIVE_HEALTH`. Build a fresh production AST and
publish an inactive immutable source generation:

For `restricted_pass`, also use the original source and health report, but bind
the exact frozen cohort inventory. This route may build only an inactive source
and AST candidate. It cannot use ordinary identity or recalculation evidence:

```bash
if [ "$SOURCE_ROUTE" = "restricted_pass" ]; then
  RESTRICTION_INVENTORY=verification_manifests/restricted_source_cohort_123.v2.json
  test -f "$RESTRICTION_INVENTORY"
  PREPARED=$(python3 xl_source_recalc.py prepare "$RAW_SOURCE_FILE" \
    --workbook "$WB" --publication-root "$SOURCE_RUN" \
    --health "$SOURCE_HEALTH" --inventory "$RESTRICTION_INVENTORY")
elif [ "$EFFECTIVE_CANDIDATE" = "$RAW_SOURCE_FILE" ]; then
  PREPARED=$(python3 xl_source_recalc.py prepare "$EFFECTIVE_CANDIDATE" \
    --workbook "$WB" --publication-root "$SOURCE_RUN" \
    --health "$EFFECTIVE_HEALTH")
else
  PREPARED=$(python3 xl_source_recalc.py prepare "$EFFECTIVE_CANDIDATE" \
    --workbook "$WB" --publication-root "$SOURCE_RUN" \
    --health "$EFFECTIVE_HEALTH" \
    --request "$RECALC_RUN/request.json" \
    --result "$RECALC_RUN/result.json" \
    --original-source "$RAW_SOURCE_FILE" \
    --trusted-runner-public-key "$TRUSTED_RUNNER_PUBLIC_KEY")
fi
SOURCE_GENERATION_ID=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["generation_id"])' \
  <<<"$PREPARED")
SOURCE=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["source_root"])' \
  <<<"$PREPARED")
SOURCE_FILE=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["source_path"])' \
  <<<"$PREPARED")
SOURCE_SHA256=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["source_sha256"])' \
  <<<"$PREPARED")
AST_ROOT=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["ast_root"])' \
  <<<"$PREPARED")
test -f "$SOURCE_FILE"
test -f "$AST_ROOT/$WB/nodes.csv"
test -f "$AST_ROOT/$WB/edges.csv"
```

Do not reuse an AST across source hashes, policy/engine versions, builder-code
hashes, or builder arguments. Missing or mismatched AST provenance is a blocker.

### 2. Segment, preserve curation, require PASS

If curation exists, record its hash before running segmentation. Do not use
`--llm` on an already curated workbook unless explicitly requested.

```bash
test ! -f "$SEG_ROOT/$WB/curation.toml" || \
  shasum -a 256 "$SEG_ROOT/$WB/curation.toml"
python3 xl_segment.py "$WB" \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" -o "$SEG_ROOT"
GENERATION_ID="<generation_id returned by xl_segment.py>"
python3 -m xl_seg.publication validate-id "$SEG_ROOT/$WB" "$GENERATION_ID" \
  --source "$SOURCE_FILE" --ast-dir "$AST_ROOT/$WB" \
  --source-generation-dir "$SOURCE_RUN/generations/$SOURCE_GENERATION_ID" \
  --validate-live-evidence --require-pass
GENERATION_ID=$(python3 -m xl_seg.publication validate-id \
  "$SEG_ROOT/$WB" "$GENERATION_ID" \
  --source "$SOURCE_FILE" --ast-dir "$AST_ROOT/$WB" \
  --source-generation-dir "$SOURCE_RUN/generations/$SOURCE_GENERATION_ID" \
  --validate-live-evidence --require-pass |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["generation_id"])')
```

When no curation exists and the heuristic auto-includes nothing, the segmenter
escalates on its own: first the LLM adjudicator (when an API key is available),
then a top-4 fallback whose picks carry an
`include = true  # fallback: top-4 auto-include` marker. This auto-escalation
only ever runs on a curation file written fresh in the same invocation -- a
pre-existing curation is never escalated or overwritten, so a hash recorded
above changing without approval is still a hard stop. Treat `# heuristic:` and
`# fallback:` markers as legitimate machine provenance, not tampering, and call
out fallback-selected outputs explicitly when summarizing.

Summarize every included output and the strongest exclusions from
`curation.toml`. Require user confirmation before continuing. If the user edits
curation, re-run the same command. If an existing curation file changed without
explicit approval, stop.

After confirmation, keep both generations inactive. All remaining build commands
must pass `--source-generation-id "$SOURCE_GENERATION_ID"` and
`--segmentation-generation-id "$GENERATION_ID"`. Do not run
`xl_source_publication activate` for `restricted_pass`.

Only the full validator command above establishes PASS. Record its returned
`generation_id`; reading `current.json` or checking its schema alone is never a
verification result.

Do not weaken the verifier, use `--no-verify`, or special-case the workbook.

### 3. Build baseline inputs

```bash
python3 xl_input_mask.py "$WB" --source "$SOURCE" \
  --seg-dir "$SEG_ROOT" --ast-dir "$AST_ROOT" \
  --segmentation-mode strict --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  -o "$BASE_INPUT_ROOT"
test -f "$BASE_INPUT_ROOT/$WB-inputs.xlsx"
test -f "$BASE_INPUT_ROOT/$WB-inputs.segmentation.json"
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
  --segmentation-generation-id "$GENERATION_ID" \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --audit-root runs \
  --model openai/gpt-5.6-sol
```

Require complete metadata, matching inventory SHA-256, model
`openai/gpt-5.6-sol`, and passed qualified-reference/value validation. Reject
invented references or values. Cache reuse is allowed only when the baseline
inventory hash, model, prompt version, source generation, and segmentation
generation match. Never activate a candidate merely to satisfy the audit.

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
python3 gen_normalizer.py "$WB" \
  --source-file "$SOURCE_FILE" --source-sha256 "$SOURCE_SHA256" \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-root "$SEG_ROOT" \
  --segmentation-generation-id "$GENERATION_ID"
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

If `plain_eligibility.py` reports `mode: plain`, skip this step and step 8-10
and 14. `validate-spec` rejects an empty variable list; profiling has nothing
to attach. Record `n/a (plain)` for profile and maskability counts.

If `mode: fail` (zero variables but ineligible), stop. Do not package.

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
an allowlist to hide an unknown leak. If this step empties a spec that was
non-empty at first normalization, that is a blocker, not a plain task.

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
  --seg-dir "$SEG_ROOT" --ast-dir "$AST_ROOT" \
  --segmentation-mode strict --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  -o "$MCP_INPUT_ROOT" \
  --mask-cells "$MCP/mask_cells.json"
test "$(shasum -a 256 "$BASE_INPUT_ROOT/$WB-inputs.xlsx" | awk '{print $1}')" \
  = "$BASE_SHA"
test -f "$MCP_INPUT_ROOT/$WB-inputs.xlsx"
```

Require all intended MCP cells blank, no formulas, no unintended typed-cell
loss, and a non-empty mask. Never overwrite the baseline inputs workbook.

### 11. Package to staging

MCP mode:

```bash
test ! -e "$STAGED"
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --seg-root "$SEG_ROOT" \
  --ast-dir "$AST_ROOT" \
  --segmentation-mode strict \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
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
`tests/masked_inputs.json`. Absence of any required artifact is failure.

Plain mode: package with `--inputs-root "$BASE_INPUT_ROOT"` and **no** `--mcp`.
Require `research_service = false`, `tests/normalization_exclusions.json`, no
`environment/mcp-server`, no compose file, no `[[environment.mcp_servers]]`,
and no `## Research data service`. Run `plain_eligibility.check_plain_environment`
so `environment/` contains exactly `$WB-inputs.xlsx` and `Dockerfile`.

### 12. Build unified task disclosure

Run against the staged bundle and golden. Pass the AST root to `select`; later
commands consume its staged artifacts. Follow `/task-disclosure` through the
`roles` command and, when collisions exist, its one Sol High role-arbitration
agent before `detect`.

```bash
test -f "$DISCLOSURE"
python3 "$DISCLOSURE" select \
  --task-dir "$STAGED" \
  --golden "$SOURCE/$WB.xlsx" \
  --ast-dir "$AST_ROOT" \
  --seg-root "$SEG_ROOT" \
  --segmentation-mode strict \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID"
python3 "$DISCLOSURE" probe   --task-dir "$STAGED"
python3 "$DISCLOSURE" roles   --task-dir "$STAGED"
# Follow /task-disclosure: if ambiguous_roles.json has cases, launch one
# gpt-5.6-sol-high subagent to write role_resolutions.json before detect.
python3 "$DISCLOSURE" detect  --task-dir "$STAGED"
python3 "$DISCLOSURE" context --task-dir "$STAGED"
python3 "$DISCLOSURE" write   --task-dir "$STAGED"
python3 "$DISCLOSURE" verify  --task-dir "$STAGED"
test -f "$DISCLOSURE_RUN/bands.json" &&
  test -f "$DISCLOSURE_RUN/records.json" &&
  test -f "$DISCLOSURE_RUN/verify.json" &&
  test -f "$STAGED/tests/disclosure.json"
python3 - "$STAGED" <<'PY'
import json, sys
from pathlib import Path
task = Path(sys.argv[1])
records = json.load(open(task / "tests/disclosure.json", encoding="utf-8"))
has_section = "## Workbook disclosure" in (
    task / "instruction.md").read_text(encoding="utf-8")
if has_section != bool(records.get("agent_records")):
    raise SystemExit("disclosure heading/record presence mismatch")
PY
```

Require verification without `--force` or `--no-fail`, no old hint/manifest
sections, and no formulas/evidence in the agent-facing instruction.

### 13. Naturalize the complete staged instruction

Load and follow
`.cursor/skills/naturalize-finance-task-instruction/SKILL.md`. Initialize or
resume its code-owned recovery state. Launch at most two fresh `generalPurpose`
subagents with model `gpt-5.6-sol-high`; each receives the same immutable source
and writes only `preamble_body` and `input_body` for its attempt. Code must
reconstruct the full instruction from untouched source bytes.

Require `"$NAT_RUN/validation.json"` to report `valid: true` and `applied: true`,
and require `task.toml` to record model `gpt-5.6-sol-high`, endpoint
`cursor-subagent`, the prompt version, and matching source/instruction hashes.
Every rejected attempt must have reason codes. A semantic rejection must be
recorded with the recovery CLI before attempt two. The apply journal must be
committed or safely rolled back; mixed `instruction.md`/`task.toml` state is a
blocker.

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py \
  verify-applied "$NAT_RUN"
```
This is the last prose mutation of the disclosure-bearing instruction unless the
faithfulness review finds a repairable row-label defect. Do not continue on fallback
or uncertainty. Step 15.5 may later rewrite headings and pointers when it replaces
`## Workbook disclosure` with the Q&A file.

Re-run `python3 "$DISCLOSURE" verify --task-dir "$STAGED"` against the final
naturalized instruction. Then launch a fresh reviewer with the golden, delivered
workbook, staged instruction, and `tests/disclosure.json`. It must check every
sentence against the golden.

Apply this closed review policy:

- Missing, reversed, or otherwise incorrect formula details are blocking, including
  wrong signs/operators, omitted references or literals, incomplete ranges, and
  incorrect period or copied-column scope.
- False or answer-leaking wording is blocking.
- Literal English translation of complete formula mechanics is not blocking merely
  because it is formula-like.
- Unnecessary but true mechanics are non-blocking findings; flag them in the report.
- An incorrect row label is repairable. Navigate left on the same row from the
  referenced cell, skipping formula/cached display text, numbers, blanks, units,
  errors, and scenario markers to select the nearest semantic row name. Correct the
  label resolver or generated record upstream, rerun `detect`, `context`, `write`,
  and `verify`, then repeat naturalization from a fresh source snapshot and launch a
  fresh reviewer. Do not hand-edit only the final instruction. A label becomes
  blocking only when it cannot be resolved or remains wrong after regeneration.

Save `"$RUN/disclosure-faithfulness.md"` with separate blocking findings and
non-blocking flags. Stop only when a blocking finding remains after repair.

### 14. Run generalized HTTP oracle

Skip this step in plain mode. The oracle requires MCP manifests. Packaging
hygiene for a plain bundle is the closed-world `environment/` check in step 11.

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

### 15.5 Additional assumptions Q&A

Load and follow `"$AA"`. Do not copy its writer, paraphrase, reviewer,
`must_say`, spoken-formula, or apply/rollback logic into this skill. Run it
against the staged bundle (`TASK="$STAGED"`), not the promoted path.

```bash
test -f "$AA"
test -f "$STAGED/tests/disclosure.json"
test -f "$STAGED/environment/Dockerfile"
```

If `$STAGED/tests/dialogue-applied.json` is already present, do not re-run
steps 11–15 on this bundle. A full rebuild stages a fresh `$STAGED`, then
this step runs again.

```bash
python3 .cursor/skills/additional-assumptions-dialogue/scripts/extract_claims.py \
  --task-dir "$STAGED" --out "$AA_RUN"
```

If `"$AA_RUN/claims.json"` reports `"empty": true`, record
`n/a (no agent_records)` and continue to promote. Do not launch writer or
reviewer agents. Do not touch the Dockerfile.

Otherwise follow that skill’s 2-round loop (`gpt-5.6-sol-high` writer,
paraphrase-seniors, mechanical `check-draft`, independent reviewer) and apply
to `$STAGED`. Docker-smoke the **main** image (`$STAGED/environment`, not
`mcp-server`). `--skip-smoke` only if the user explicitly says the daemon is
down; otherwise fail-closed. A skipped smoke is not a normal pass — report it
as pending and do not promote unless the user accepted the skip.

After a non-empty apply, re-run the matching closed-world check. Do **not**
re-run `disclose.py write` / `verify`, naturalization, `xl_output_task.py`,
or the live HTTP oracle.

Plain mode:

```bash
python3 - "$STAGED" "$WB" <<'PY'
import sys
from pathlib import Path
from plain_eligibility import check_plain_environment
report = check_plain_environment(Path(sys.argv[1]), sys.argv[2])
if report.get("valid") is not True:
    raise SystemExit(report)
print("plain environment hygiene PASS")
PY
```

MCP mode:

```bash
python3 - "$STAGED" <<'PY'
import sys
from pathlib import Path
from xl_mcp_oracle import check_environment
bundle = Path(sys.argv[1])
env = bundle / "environment"
workbooks = sorted(
    p for p in env.iterdir()
    if p.is_file() and p.suffix.casefold() in {".xlsx", ".xlsm"}
)
if len(workbooks) != 1:
    raise SystemExit("expected exactly one workbook in environment/")
report = check_environment(bundle, workbooks[0])
if report.get("valid") is not True:
    raise SystemExit(report)
print("MCP environment hygiene PASS")
PY
```

Require `$STAGED/environment/additional-assumptions.md`, a Dockerfile
`COPY additional-assumptions.md /app/additional-assumptions.md` line,
`$STAGED/tests/dialogue-applied.json`, and no `## Workbook disclosure` left
in `instruction.md`.

### 16. Promote only after every gate passes

If a requested set contains multiple workbooks, wait until every staged bundle
passes before promoting the set. Publication first freezes the final staged task
as an immutable task generation, then atomically compare-and-swaps the one
authoritative `current-release.json`. Compatibility task paths are copies only.

After any additional-assumptions apply, verify that the live instruction still
matches the naturalizer metadata hash:

```bash
python3 .cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py \
  verify-metadata --instruction "$STAGED/instruction.md" \
  --task-toml "$STAGED/task.toml"
```

```bash
python3 - "$STAGED/tests/pipeline_bindings.json" "$RELEASE_ROOT/task-bindings.json" <<'PY'
import json, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
Path(sys.argv[2]).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
TASK_GENERATION=$(python3 -m xl_release_publication publish-task \
  "$STAGED" "$RELEASE_ROOT" "$WB" \
  --bindings "$RELEASE_ROOT/task-bindings.json")
TASK_GENERATION_ID=$(python3 -c \
  'import json,sys; print(json.load(sys.stdin)["generation_id"])' \
  <<<"$TASK_GENERATION")
if test -f "$RELEASE_ROOT/current-release.json"; then
  EXPECTED_RELEASE_ID=$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["release_id"])' \
    "$RELEASE_ROOT/current-release.json")
  EXPECTED=(--expected-current-release-id "$EXPECTED_RELEASE_ID")
else
  EXPECTED=(--expect-absent)
  # For legacy migration, run freeze-legacy first and add
  # --legacy-snapshot-hash <frozen hash>.
fi
python3 -m xl_release_publication publish "$RELEASE_ROOT" "$WB" \
  --source-root "$SOURCE_RUN" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-root "$SEG_ROOT/$WB" \
  --segmentation-generation-id "$GENERATION_ID" \
  --task-generation-id "$TASK_GENERATION_ID" "${EXPECTED[@]}"
```

Do not run Harbor rollouts unless the user separately asks.

## Completion report

Report, per workbook:

- workbook id, golden path, AST path, segmentation `PASS`, and curation hash
- source-health route/reasons, recalc request/result when applicable, immutable
  source generation ID, engine/version, source hash, and AST provenance verdict
- curated output names/bands and confirmation that curation was preserved
- disclosure run path; custom/convention disclosed, suppressed, standard, and
  unclassified counts; mechanical verdict; fresh faithfulness-review path and
  verdict
- instruction naturalization source/candidate/report paths, pinned Sol model,
  prompt version, source/instruction hashes, and semantic-review result
- MCP or plain mode; if plain, the eligibility reason and exclusion-code
  histogram; if MCP, the separate MCP input path/hash
- baseline input path/hash
- audit Markdown/inventory/metadata paths, model, inventory hash, and cache use
- draft row count; included/excluded disposition counts; atomic variable count
- profile count, reviewed count, captured public count, and every
  skipped-auth/paywall/blocked/unreachable source (`n/a (plain)` in plain mode)
- maskability report path; `cells`, `extra_cells`, and total masked-cell counts
  (`n/a (plain)` in plain mode)
- normalized spec, exclusions, profiles, and MCP paths; deterministic comparison
  and MCP validation/smoke summaries (`n/a (plain)` for MCP build/oracle)
- staged path, oracle report and result (`n/a (plain)`), exact grader score,
  additional-assumptions result (`n/a (no agent_records)`, applied, or
  smoke pending), `$AA_RUN/{claims,review,apply}.json` when extract ran,
  notes path and Dockerfile COPY when applied, main-image smoke result,
  final promoted path, previous-bundle backup path, and any retained diagnostics

Completion requires every gate that applies to the chosen mode to pass.
Otherwise report the first blocker and leave existing task bundles untouched.
