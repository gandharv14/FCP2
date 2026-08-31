---
name: harbor-environment-packager
description: Builds deterministic MCP environments, creates a separate masked workbook, stages Harbor bundles, and enforces plain or MCP hygiene. Use when harbor-orchestrator assigns the environment-build and staging lane for a Harbor fleet workbook.
disable-model-invocation: true
---

# Harbor Environment Packager

Own deterministic environment construction, separate MCP masking, staging under
`tasks_outputs_mcp`, and closed-world environment hygiene. Never promote,
activate, or roll out a staged bundle.

## Shared contract

- Canonical lane ID: `harbor-environment-packager`.
- Read the tracker at `runs/harbor-fleet/<batch>/workbooks/<id>.json`; use only
  paths, IDs, bindings, and immutable SHA-256 values present in that snapshot.
  Never infer a default path or use an unbound shell variable.
- Every tracker mutation uses the one orchestrator lock
  `runs/harbor-fleet/<batch>/workbooks/<id>.json.lock` and CAS on the monotonic
  top-level integer `revision`.
- Under an exclusive `fcntl.flock`, reread and require
  `revision == expected_revision`; preserve other lanes; mutate only this lane
  and its owned bindings; increment revision by exactly one; atomically replace
  from a flushed, `fsync`ed sibling temporary JSON; `fsync` the parent; unlock.
  A mismatch returns `tracker_revision_conflict` without a stale write.
- Use exactly `lane_state["harbor-environment-packager"]`. Its `state` is one
  of `pending|ready|running|repairing|passed|terminal`; phase or substatus
  belongs only in `phase` or `disposition`.
- On an accepted dispatch CAS-transition `ready` to `running`; use `passed` only
  after all gates pass and `terminal` for a closed unrecoverable result.
  `repairing` requires prior orchestrator authorization.
- Store its `current_confidence` there and append every
  confidence transition, reason, diagnostic binding, repair ID, and evidence
  hash to its append-only `confidence_history`.
- Current confidence may improve only after an orchestrator-authorized closed
  repair resolves its exact bound diagnostic and the owning gate plus every
  invalidated downstream gate re-passes on new hashes. Keep the historical low
  or medium event and repair record; it no longer blocks after that proof.
- Recompute top-level `current_confidence` as the worst of every lane's current
  value (`low < medium < high`). An unresolved medium remains medium and an
  unresolved low blocks promotion. Unrelated later lanes never overwrite an
  earlier lane's current confidence.
- Record this lane's gate results, hashes, and diagnostics under
  `lane_state["harbor-environment-packager"]`.
- Never request human intervention. Lane agents do not repair their own failures.
- A failed, skipped, missing, ambiguous, or unreviewed gate emits a structured
  handoff to `harbor-orchestrator` and stops this lane.
- Forbidden controls: `--force` and `--no-verify`; recuration is prohibited;
  smoke is mandatory.
- Every repair must re-enter its owning gate. The orchestrator reruns all
  downstream gates; no prior downstream result survives a repair.
- Use this handoff shape under `handoffs[]`:

```json
{
  "schema_version": "harbor-fleet-handoff/v1",
  "batch": "<batch>",
  "workbook_id": "<id>",
  "lane": "harbor-environment-packager",
  "gate": "<owning-gate>",
  "status": "failed",
  "failure_code": "<stable-code>",
  "summary": "<sanitized-summary>",
  "artifact_hashes": {"<path>": "<sha256>"},
  "current_confidence": "high|medium|low",
  "reasons": ["<reason>"],
  "diagnostics": ["<path>"],
  "next_owner": "harbor-orchestrator",
  "suggested_fixer": null,
  "rerun": {"owning_gate": "<owning-gate>", "downstream": true}
}
```

## Load and validate every binding

Require upstream source, segmentation, preflight, audit, normalization,
eligibility, and specification gates to pass; maskability, profiler, and
leakscan must pass in MCP mode and be explicitly not applicable in plain mode.
Read one tracker revision and bind every command value below:

```bash
WB="<tracker.workbook_id>"
DEPENDENCY_PREFLIGHT="<tracker.bindings.dependency_preflight.path>"
DEPENDENCY_PREFLIGHT_SHA256="<tracker.bindings.dependency_preflight.sha256>"
SOURCE="<tracker.bindings.source_root.path>"
SOURCE_ROOT_SHA256="<tracker.bindings.source_root.sha256>"
SOURCE_FILE="<tracker.bindings.source_file.path>"
SOURCE_SHA256="<tracker.bindings.source_file.sha256>"
SOURCE_GENERATION_ROOT="<tracker.bindings.source_generation_root.path>"
SOURCE_GENERATION_ID="<tracker.bindings.source_generation.id>"
SOURCE_GENERATION_DIR="<tracker.bindings.source_generation.path>"
SOURCE_GENERATION_MANIFEST_SHA256="<tracker.bindings.source_generation.manifest_sha256>"
AST_ROOT="<tracker.bindings.ast_root.path>"
AST_ROOT_SHA256="<tracker.bindings.ast_root.sha256>"
SEG_ROOT="<tracker.bindings.segmentation_root.path>"
SEGMENTATION_WORKBOOK_ROOT="<tracker.bindings.segmentation_workbook_root.path>"
GENERATION_ID="<tracker.bindings.segmentation_generation.id>"
SEGMENTATION_MANIFEST_SHA256="<tracker.bindings.segmentation_generation.manifest_sha256>"
RUN="<tracker.bindings.variable_source_run.path>"
MODE="<tracker.bindings.task_mode.value>"
PLAIN_REPORT="<tracker.bindings.plain_eligibility.path>"
PLAIN_REPORT_SHA256="<tracker.bindings.plain_eligibility.sha256>"
NORMALIZED="<tracker.bindings.normalized_final.path>"
NORMALIZED_SHA256="<tracker.bindings.normalized_final.sha256>"
PROFILES="<tracker.bindings.source_profiles.path>"
PROFILES_SHA256="<tracker.bindings.source_profiles.sha256>"
MASKABILITY_REPORT="<tracker.bindings.maskability_report.path>"
MASKABILITY_REPORT_SHA256="<tracker.bindings.maskability_report.sha256>"
LEAKSCAN_REPORT="<tracker.bindings.leakscan_report.path>"
LEAKSCAN_REPORT_SHA256="<tracker.bindings.leakscan_report.sha256>"
BASE_INPUT_ROOT="<tracker.bindings.baseline_inputs_root.path>"
BASE_INPUT="<tracker.bindings.baseline_inputs.path>"
BASE_INPUT_SHA256="<tracker.bindings.baseline_inputs.sha256>"
MCP_INPUT_ROOT="<tracker.bindings.mcp_inputs_root.path>"
MCP_INPUT="<tracker.bindings.mcp_inputs.path>"
MCP="<tracker.bindings.mcp_bundle.path>"
MCP_SHA256="<tracker.bindings.mcp_bundle.sha256>"
MASK_CELLS="<tracker.bindings.mcp_mask_cells.path>"
MASK_CELLS_SHA256="<tracker.bindings.mcp_mask_cells.sha256>"
MCP_BUILD_A="<tracker.bindings.mcp_build_a.path>"
MCP_BUILD_B="<tracker.bindings.mcp_build_b.path>"
AUDIT_ROOT="<tracker.bindings.audit_root.path>"
ALLOWLIST="<tracker.bindings.oracle_allowlist.path>"
ALLOWLIST_SHA256="<tracker.bindings.oracle_allowlist.sha256>"
STAGE_ROOT="<tracker.bindings.stage_root.path>"
STAGED="<tracker.bindings.staged_bundle.path>"
```

Before commands, verify every non-null hash, generation manifest, source/AST
provenance binding, final spec/profile relationship, and upstream gate result.
Require `lane_state["harbor-normalization-engineer"].state = passed` and
`phase = phase2_complete`; consume only `bindings.normalized_final`, never the
phase-1 binding directly. Require
`lane_state["harbor-source-profiler"].state = passed` with disposition
`accepted` or `not_applicable`. Resolve mode only from the tracker-bound plain
eligibility report. In MCP mode, an accepted profiler disposition requires the
profile hash and final profile references to validate; in plain mode require
profiler disposition `not_applicable` and no accepted profile reference. Require `ALLOWLIST`
to be exactly `runs/$WB-variable-sources/oracle-allowlist.json`; this is also the
only path later HTTP-oracle dispatch may consume. In MCP mode the pre-build MCP
hash must be null or match an explicitly archived prior diagnostic.

## Gate 1: Deterministic double MCP build

Run only in MCP mode:

```bash
python3 .cursor/skills/create-harbor-task/scripts/archive_run_dir.py \
  "$MCP_BUILD_A" "$MCP_BUILD_B"
test ! -e "$MCP_BUILD_A" && test ! -e "$MCP_BUILD_B"
python3 xl_variable_mcp.py build "$NORMALIZED" "$MCP_BUILD_A" \
  --workbook "$WB" --source "$SOURCE"
python3 xl_variable_mcp.py build "$NORMALIZED" "$MCP_BUILD_B" \
  --workbook "$WB" --source "$SOURCE"
diff -qr "$MCP_BUILD_A" "$MCP_BUILD_B"
python3 - "$MCP_BUILD_A" <<'PY'
import json, sys
from pathlib import Path
from mcp_env.validate import validate
report = validate(Path(sys.argv[1]))
if report.get("valid") is not True:
    raise SystemExit(report)
print(json.dumps(report, indent=2, sort_keys=True))
PY
python3 .cursor/skills/create-harbor-task/scripts/archive_run_dir.py "$MCP"
test ! -e "$MCP"
mv "$MCP_BUILD_A" "$MCP"
uv run --python 3.12 --with fastmcp --with openpyxl \
  python xl_variable_mcp.py smoke "$MCP"
```

Require byte-identical builds, exact golden-value agreement, unique
unsuperseded evidence, complete acyclic provenance, conflicting broad queries,
no runtime evaluation keys, valid reviewed profile references, validator PASS,
and smoke PASS. Retain `"$MCP_BUILD_B"` and failed build directories as
diagnostics. Compute the deterministic MCP tree hash and CAS-publish it before
masking; require the emitted `mask_cells.json` path to equal `"$MASK_CELLS"` and
CAS-publish its hash. Downstream use must match both hashes.

Every handoff has `next_owner: harbor-orchestrator`. Set `suggested_fixer:
harbor-spec-fixer` for unknown workbook references, golden-value mismatches,
mask-cell defects, or specification/profile binding errors; otherwise leave it
null. This lane never edits the specification or retries a failed build.

## Gate 2: Mask MCP inputs separately

Run only after MCP build PASS:

```bash
BASE_SHA=$(shasum -a 256 "$BASE_INPUT" | awk '{print $1}')
python3 xl_input_mask.py "$WB" --source "$SOURCE" \
  --seg-dir "$SEG_ROOT" --ast-dir "$AST_ROOT" \
  --segmentation-mode strict --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  -o "$MCP_INPUT_ROOT" \
  --mask-cells "$MASK_CELLS"
test "$(shasum -a 256 "$BASE_INPUT" | awk '{print $1}')" \
  = "$BASE_SHA"
test -f "$MCP_INPUT"
```

Require all intended MCP cells blank, no formulas, no unintended typed-cell
loss, a non-empty mask, and an unchanged baseline hash. Never overwrite the
baseline inputs workbook. Require emitted MCP input path to equal
`"$MCP_INPUT"`, then CAS-publish its hash.

## Gate 3: Stage the bundle

Archive stale staging and require an absent destination:

```bash
python3 .cursor/skills/create-harbor-task/scripts/archive_run_dir.py "$STAGED"
test ! -e "$STAGED"
```

MCP mode:

```bash
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --seg-root "$SEG_ROOT" \
  --ast-dir "$AST_ROOT" \
  --segmentation-mode strict \
  --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  --inputs-root "$MCP_INPUT_ROOT" \
  --variable-source-audit-inputs-root "$BASE_INPUT_ROOT" \
  --variable-source-audit-root "$AUDIT_ROOT" \
  --variable-source-audit-model openai/gpt-5.6-sol \
  --mcp "$MCP" \
  --no-naturalize \
  -o "$STAGE_ROOT"
```

Require the MCP declaration, compose sidecar, extended timeout, research
instructions, audit metadata bound to baseline inputs, and
`tests/masked_inputs.json`.

Plain mode:

```bash
python3 xl_output_task.py "$WB" \
  --source "$SOURCE" \
  --seg-root "$SEG_ROOT" \
  --ast-dir "$AST_ROOT" \
  --segmentation-mode strict \
  --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  --inputs-root "$BASE_INPUT_ROOT" \
  --variable-source-audit-inputs-root "$BASE_INPUT_ROOT" \
  --variable-source-audit-root "$AUDIT_ROOT" \
  --variable-source-audit-model openai/gpt-5.6-sol \
  --no-naturalize \
  -o "$STAGE_ROOT"
```

Require `research_service = false`, `tests/normalization_exclusions.json`, and
no MCP server directory, compose file, MCP declaration, or research-service
instruction.

## Gate 4: Closed-world hygiene

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

For both modes, reject symlinks, unknown files, golden workbooks, `eval/`,
normalized specs, profile captures, snapshots, answer values, and audit working
files in `environment/`. MCP mode must contain exactly one separately masked
workbook and its valid sidecar. Plain mode must satisfy the closed-world
environment checker.

## Success record

Record mode, both build-tree hashes and diff result, validation and smoke
reports, baseline and MCP-input hashes, mask count, staging path/tree hash,
required artifact checks, hygiene report, retained diagnostics, and current
confidence with reasons. Success ends at a validated staged bundle under
`tasks_outputs_mcp`; promotion remains an orchestrator gate.
CAS-publish the staged tree hash and all validated input hashes. Any later oracle
must use the tracker-bound MCP hash and canonical allowlist path.
CAS-set `lane_state["harbor-environment-packager"].state = passed`.
