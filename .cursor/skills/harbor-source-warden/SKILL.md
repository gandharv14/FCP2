---
name: harbor-source-warden
description: Guards workbook source health, binds approved recalculation, and publishes immutable source and AST candidates. Use when harbor-orchestrator assigns the source-health and source-publication lane for a Harbor fleet workbook.
disable-model-invocation: true
---

# Harbor Source Warden

Own only source health, recalculation binding, and immutable source/AST candidate
publication. Work from the pipeline repository root. Invoke existing repository
tools only; do not author helpers or alter pipeline code.

## Shared contract

- Canonical lane ID: `harbor-source-warden`.
- Read the tracker at `runs/harbor-fleet/<batch>/workbooks/<id>.json`; use only
  paths, IDs, bindings, and immutable SHA-256 values present in that snapshot.
  Never infer a default path or use an unbound shell variable.
- Every tracker mutation uses the one orchestrator lock
  `runs/harbor-fleet/<batch>/workbooks/<id>.json.lock` and compare-and-swap
  (CAS) on the monotonic top-level integer `revision`.
- Mutation protocol: acquire an exclusive `fcntl.flock`; reread the tracker;
  require `revision == expected_revision`; preserve other lanes; mutate only
  this lane and its owned bindings; set `revision = expected_revision + 1`;
  write canonical JSON to a sibling temporary file; flush and `fsync`; use
  `os.replace`; `fsync` the parent; then unlock. A mismatch returns
  `tracker_revision_conflict` to the orchestrator without a stale write.
- Use exactly `lane_state["harbor-source-warden"]`. Its `state` is one of
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
- Record this lane's gate results, artifact hashes, and diagnostics under
  `lane_state["harbor-source-warden"]`.
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
  "lane": "harbor-source-warden",
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

Read one tracker revision and bind every command input from these keys. Require
the recorded hash to match every existing immutable file or manifest before use:

```bash
WB="<tracker.workbook_id>"
DEPENDENCY_PREFLIGHT="<tracker.bindings.dependency_preflight.path>"
DEPENDENCY_PREFLIGHT_SHA256="<tracker.bindings.dependency_preflight.sha256>"
ORIGINAL_RAW_SOURCE="<tracker.bindings.original_raw_source.path-or-empty>"
ORIGINAL_RAW_SOURCE_SHA256="<tracker.bindings.original_raw_source.sha256-or-empty>"
SOURCE_HEALTH_BEFORE="<tracker.bindings.source_health_before.path-or-empty>"
SOURCE_HEALTH_BEFORE_SHA256="<tracker.bindings.source_health_before.sha256-or-empty>"
SOURCE_REMEDIATION_PLAN="<tracker.bindings.source_remediation_plan.path-or-empty>"
SOURCE_REMEDIATION_PLAN_SHA256="<tracker.bindings.source_remediation_plan.sha256-or-empty>"
SOURCE_REMEDIATION_MANIFEST="<tracker.bindings.source_remediation_manifest.path-or-empty>"
SOURCE_REMEDIATION_MANIFEST_SHA256="<tracker.bindings.source_remediation_manifest.sha256-or-empty>"
RAW_SOURCE_FILE="<tracker.bindings.raw_source.path>"
RAW_SOURCE_SHA256="<tracker.bindings.raw_source.sha256>"
SOURCE_RUN="<tracker.bindings.source_publication_root.path>"
SOURCE_GENERATION_ROOT="<tracker.bindings.source_generation_root.path>"
SOURCE_HEALTH="<tracker.bindings.source_health.path>"
RECALC_RUN="<tracker.bindings.recalc_run.path>"
RESTRICTION_INVENTORY="<tracker.bindings.restriction_inventory.path>"
RESTRICTION_INVENTORY_SHA256="<tracker.bindings.restriction_inventory.sha256>"
TRUSTED_RUNNER_PUBLIC_KEY="<tracker.bindings.trusted_runner_public_key.path>"
TRUSTED_RUNNER_PUBLIC_KEY_SHA256="<tracker.bindings.trusted_runner_public_key.sha256>"
ISOLATION_ATTESTATION="<tracker.bindings.excel_isolation_attestation.path>"
ISOLATION_ATTESTATION_SHA256="<tracker.bindings.excel_isolation_attestation.sha256>"
SANDBOX_RUNNER="<tracker.bindings.excel_sandbox_runner.path>"
SANDBOX_RUNNER_SHA256="<tracker.bindings.excel_sandbox_runner.sha256>"
EXCEL_ENGINE_VERSION="<tracker.bindings.excel_engine_version.value>"
```

Only `.xlsx` is eligible. Never read or print `.env`.

## Gate 1: Observe source health

```bash
test -f "$RAW_SOURCE_FILE"
if [ -n "$SOURCE_REMEDIATION_MANIFEST" ]; then
  test -f "$ORIGINAL_RAW_SOURCE"
  test -f "$SOURCE_HEALTH_BEFORE"
  test -f "$SOURCE_REMEDIATION_PLAN"
  test "$(shasum -a 256 "$ORIGINAL_RAW_SOURCE" | awk '{print $1}')" = \
    "$ORIGINAL_RAW_SOURCE_SHA256"
  test "$(shasum -a 256 "$SOURCE_HEALTH_BEFORE" | awk '{print $1}')" = \
    "$SOURCE_HEALTH_BEFORE_SHA256"
  test "$(shasum -a 256 "$SOURCE_REMEDIATION_PLAN" | awk '{print $1}')" = \
    "$SOURCE_REMEDIATION_PLAN_SHA256"
  test "$(shasum -a 256 "$SOURCE_REMEDIATION_MANIFEST" | awk '{print $1}')" = \
    "$SOURCE_REMEDIATION_MANIFEST_SHA256"
  python3 xl_volatile_formula_remediation.py verify \
    "$ORIGINAL_RAW_SOURCE" "$RAW_SOURCE_FILE" \
    --plan "$SOURCE_REMEDIATION_PLAN" \
    --manifest "$SOURCE_REMEDIATION_MANIFEST"
elif [ -n "$ORIGINAL_RAW_SOURCE$SOURCE_HEALTH_BEFORE$SOURCE_REMEDIATION_PLAN" ]; then
  echo "partial volatile-remediation bindings" >&2
  exit 1
fi
python3 xl_source_health.py observe "$RAW_SOURCE_FILE" -o "$SOURCE_HEALTH"
SOURCE_ROUTE=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["route"])' \
  "$SOURCE_HEALTH")
```

Hash `"$RAW_SOURCE_FILE"` and `"$SOURCE_HEALTH"`. `unsupported` and
`insufficient_evidence` are terminal source-lane outcomes. External
links/connections, macros, OLE, volatile formulas, data tables, and unknown
iteration semantics are not recalculation candidates. Remediation is never
attempted here because tracker `raw_source` is already immutable; unresolved
volatility must return to a fresh orchestrator intake.

## Gate 2: Bind recalculation when required

For `recalc_candidate`, first require a local macOS trusted runner. Verify the
public key, isolation attestation, and sandbox runner against their tracker
hashes. Each must be a non-symlink protected root-owned regular file with no
group/other write bit under protected root-owned parent directories. If any
prerequisite, platform check, or approved engine binding fails, emit terminal
`trusted_runner_unavailable`. Do not create a request for execution elsewhere,
wait, or establish an external checkpoint.

When all protected local prerequisites pass, execute the complete bound flow in
the same lane invocation:

```bash
mkdir -p "$RECALC_RUN"
python3 xl_source_recalc.py request "$RAW_SOURCE_FILE" \
  "$RECALC_RUN/recalculated/$WB.xlsx" \
  --allowed-root "$RECALC_RUN/recalculated" \
  --trusted-runner-public-key "$TRUSTED_RUNNER_PUBLIC_KEY" \
  --permitted-engine-version "$EXCEL_ENGINE_VERSION" \
  -o "$RECALC_RUN/request.json"
python3 xl_source_recalc.py execute "$RECALC_RUN/request.json" \
  --source "$RAW_SOURCE_FILE" \
  --allowed-root "$RECALC_RUN/recalculated" \
  --isolation-attestation "$ISOLATION_ATTESTATION" \
  --sandbox-runner "$SANDBOX_RUNNER" \
  -o "$RECALC_RUN/result.json"
python3 xl_source_health.py observe \
  "$RECALC_RUN/recalculated/$WB.xlsx" \
  -o "$RECALC_RUN/source-health-after.json"
EFFECTIVE_CANDIDATE="$RECALC_RUN/recalculated/$WB.xlsx"
EFFECTIVE_HEALTH="$RECALC_RUN/source-health-after.json"
```

Require protected root-owned attestation, sandbox runner, and trusted public
key; a signed `excel-runner-receipt/v1`; matching request/source/output hashes;
the approved engine/version; isolation; calculation completion; and completion
time. Any mismatch is terminal for this lane.

For `pass` or `restricted_pass`:

```bash
EFFECTIVE_CANDIDATE="$RAW_SOURCE_FILE"
EFFECTIVE_HEALTH="$SOURCE_HEALTH"
```

## Gate 3: Publish an inactive immutable candidate

```bash
if [ "$SOURCE_ROUTE" = "restricted_pass" ]; then
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

Never reuse an AST across source hashes, policy/engine versions, builder-code
hashes, or builder arguments. Missing or mismatched AST provenance fails the
gate. Keep every candidate inactive; this lane never activates or promotes it.
CAS-publish the validated source generation ID, source root/file/hash, AST
root/provenance hash, health path/hash, and recalculation evidence hashes into
tracker bindings before downstream dispatch.

## Success record

Record route and reasons, source generation ID, source hash, engine/version,
request/result hashes when applicable, AST files and provenance hashes, and
current confidence `high` only when every binding and immutable artifact
validates. CAS-set `lane_state["harbor-source-warden"].state = passed`.
