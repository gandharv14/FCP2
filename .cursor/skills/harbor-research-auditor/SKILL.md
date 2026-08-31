---
name: harbor-research-auditor
description: Builds the immutable baseline input mask and runs the pinned GPT 5.6 Sol variable-source audit with complete validation. Use when harbor-orchestrator assigns the baseline and research-audit lane for a Harbor fleet workbook.
disable-model-invocation: true
---

# Harbor Research Auditor

Own baseline input masking and the mandatory variable/source audit. The baseline
workbook remains immutable after this lane and is never replaced by an MCP-masked
workbook.

## Shared contract

- Canonical lane ID: `harbor-research-auditor`.
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
- Use exactly `lane_state["harbor-research-auditor"]`. Its `state` is one of
  `pending|ready|running|repairing|passed|terminal`; phase or substatus belongs
  only in `phase` or `disposition`.
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
- Record this lane's gate results, hashes, diagnostics, and retry counters under
  `lane_state["harbor-research-auditor"]`.
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
  "lane": "harbor-research-auditor",
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

## Load tracker bindings

Read one tracker revision and bind every value below. Verify each immutable file
or manifest against its tracker hash and require source, segmentation, and
preflight gates to be passed.

```bash
WB="<tracker.workbook_id>"
DEPENDENCY_PREFLIGHT="<tracker.bindings.dependency_preflight.path>"
DEPENDENCY_PREFLIGHT_SHA256="<tracker.bindings.dependency_preflight.sha256>"
SOURCE="<tracker.bindings.source_root.path>"
SOURCE_ROOT_SHA256="<tracker.bindings.source_root.sha256>"
SOURCE_GENERATION_ROOT="<tracker.bindings.source_generation_root.path>"
SOURCE_GENERATION_ID="<tracker.bindings.source_generation.id>"
SOURCE_GENERATION_MANIFEST_SHA256="<tracker.bindings.source_generation.manifest_sha256>"
AST_ROOT="<tracker.bindings.ast_root.path>"
AST_ROOT_SHA256="<tracker.bindings.ast_root.sha256>"
SEG_ROOT="<tracker.bindings.segmentation_root.path>"
GENERATION_ID="<tracker.bindings.segmentation_generation.id>"
SEGMENTATION_MANIFEST_SHA256="<tracker.bindings.segmentation_generation.manifest_sha256>"
BASE_INPUT_ROOT="<tracker.bindings.baseline_inputs_root.path>"
BASE_INPUT="<tracker.bindings.baseline_inputs.path>"
BASE_SEGMENTATION="<tracker.bindings.baseline_segmentation.path>"
RUN="<tracker.bindings.variable_source_run.path>"
AUDIT="<tracker.bindings.variable_source_audit.path>"
INVENTORY="<tracker.bindings.variable_source_inventory.path>"
METADATA="<tracker.bindings.variable_source_metadata.path>"
AUDIT_ROOT="<tracker.bindings.audit_root.path>"
AUDIT_PROJECT="cms6m4urm006n07z8ecxi1oi2"
```

Require `AUDIT_PROJECT` to equal `cms6m4urm006n07z8ecxi1oi2`.
Never read or print `.env`. The repository audit command reads its credential.

## Gate 1: Build immutable baseline inputs

```bash
python3 xl_input_mask.py "$WB" --source "$SOURCE" \
  --seg-dir "$SEG_ROOT" --ast-dir "$AST_ROOT" \
  --segmentation-mode strict --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID" \
  -o "$BASE_INPUT_ROOT"
test -f "$BASE_INPUT_ROOT/$WB-inputs.xlsx"
test -f "$BASE_INPUT_ROOT/$WB-inputs.segmentation.json"
shasum -a 256 "$BASE_INPUT_ROOT/$WB-inputs.xlsx"
```

Require verification `PASS`, zero surviving formulas, and all typed cells intact
except the documented pasted-answer policy. Hash both artifacts. Any failure or
non-strict binding stops the lane.
Require the emitted paths to equal `"$BASE_INPUT"` and `"$BASE_SEGMENTATION"`
before CAS-publishing their hashes.

## Gate 2: Run the pinned audit

The audit is mandatory and must use both the pinned model and active project:

```bash
python3 xl_variable_source_audit.py "$WB" \
  --inputs-root "$BASE_INPUT_ROOT" \
  --seg-root "$SEG_ROOT" \
  --segmentation-generation-id "$GENERATION_ID" \
  --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --audit-root "$AUDIT_ROOT" \
  --model openai/gpt-5.6-sol \
  --project-id "$AUDIT_PROJECT"
```

Do not use inventory-only mode as a production audit. Cache reuse is valid only
when baseline inventory hash, model, prompt version, source generation, and
segmentation generation all match.

## Gate 3: Validate complete audit evidence

Require:

- Markdown, inventory JSON, and metadata JSON all exist and hash successfully;
- metadata `status` is `complete`;
- metadata model is exactly `openai/gpt-5.6-sol`;
- metadata project ID is exactly `cms6m4urm006n07z8ecxi1oi2`;
- metadata inventory SHA-256 matches the inventory bytes;
- artifact and pinned generation metadata match this workbook operation;
- row-ID resolution is enabled and every citation resolves;
- validation status is `passed` for row-ID citations, qualified cell references,
  and workbook value numbers;
- output references and values originate from deterministic inventory resolution;
- no invented reference or value, malformed table, empty sentinel outcome, or
  partial API completion exists.

Any missing credential, audit error, incomplete metadata, hash mismatch,
validation failure, or no resolvable candidate is a blocker.

## Failure and retry policy

Definitive authentication, account, quota, or entitlement denials are terminal
and never retried. Semantic validation, binding, hash, and empty-audit failures
are also terminal.

Transient rate limits, timeouts, connection resets, and service-side 5xx errors
return a handoff to `harbor-orchestrator`. Only the orchestrator may CAS-increment
`lane_state["harbor-research-auditor"].retry_count`, apply `Retry-After` or exponential
backoff, and dispatch again. Permit at most two retries after the initial
attempt; a third transient result is terminal. The lane itself never sleeps or
retries.

Preserve a sanitized HTTP diagnostic without secrets, bearer material, cookies,
full request bodies, workbook values, or private URLs. Record only:

- UTC timestamp, endpoint host/path, HTTP method, status code, exception class;
- retry metadata and response headers limited to request ID, content type,
  retry-after, and rate-limit fields;
- a bounded redacted response summary and local diagnostic path/hash.

Every handoff has `next_owner: harbor-orchestrator`. Set `suggested_fixer` to
`harbor-infra-fixer` only for an invalid pinned project, invalid routing
signature, or demonstrated local endpoint/configuration fault; otherwise it is
null. Use stable `audit_http_*` failure codes.

## Success record

Record baseline path/hash, segmentation evidence path/hash, audit paths/hashes,
model, project ID, prompt version, inventory hash, API call completion, cache
status, validation checks, and current confidence `high` with binding reasons. Keep the
baseline workbook unchanged for all downstream audit and packaging checks.
CAS-publish the baseline and complete audit paths/hashes only after all gates
pass. CAS-set `lane_state["harbor-research-auditor"].state = passed`.
