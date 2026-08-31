---
name: harbor-normalization-engineer
description: Imports audited rows, runs deterministic atomic normalization, decides plain eligibility, and audits maskability and leaks. Use when harbor-orchestrator assigns the normalization and specification-review lane for a Harbor fleet workbook.
disable-model-invocation: true
---

# Harbor Normalization Engineer

Own audit import, deterministic normalization, disposition completeness, plain
eligibility, specification validation, maskability, and offline leak detection.
This lane may emit reviewed repair proposals but never applies them.

## Shared contract

- Canonical lane ID: `harbor-normalization-engineer`.
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
- Use exactly `lane_state["harbor-normalization-engineer"]`. Its `state` is one
  of `pending|ready|running|repairing|passed|terminal`; phase or substatus
  belongs only in `phase` or `disposition`.
- On an accepted dispatch CAS-transition `ready` to `running`; use `passed` only
  after phase 2 passes and `terminal` for a closed unrecoverable result.
  `repairing` requires prior orchestrator authorization.
- Store its `current_confidence` there and append
  every confidence transition, reason, diagnostic binding, repair ID, and
  evidence hash to its append-only `confidence_history`.
- Current confidence may improve only after an orchestrator-authorized closed
  repair resolves its exact bound diagnostic and the owning gate plus every
  invalidated downstream gate re-passes on new hashes. Keep the historical low
  or medium event and repair record; it no longer blocks after that proof.
- Recompute top-level `current_confidence` as the worst of every lane's current
  value (`low < medium < high`). An unresolved medium remains medium and an
  unresolved low blocks promotion. Unrelated later lanes never overwrite an
  earlier lane's current confidence.
- Record phase, disposition, gate results, hashes, and diagnostics under
  `lane_state["harbor-normalization-engineer"]`.
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
  "lane": "harbor-normalization-engineer",
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

Read one tracker revision and bind all values below. Verify every immutable
input against its tracker hash and require the audit lane to be passed.

```bash
WB="<tracker.workbook_id>"
DEPENDENCY_PREFLIGHT="<tracker.bindings.dependency_preflight.path>"
DEPENDENCY_PREFLIGHT_SHA256="<tracker.bindings.dependency_preflight.sha256>"
RUN="<tracker.bindings.variable_source_run.path>"
AUDIT="<tracker.bindings.variable_source_audit.path>"
AUDIT_SHA256="<tracker.bindings.variable_source_audit.sha256>"
INVENTORY="<tracker.bindings.variable_source_inventory.path>"
INVENTORY_SHA256="<tracker.bindings.variable_source_inventory.sha256>"
DRAFT="<tracker.bindings.normalization_draft.path>"
NORMALIZER="<tracker.bindings.normalizer.path>"
NORMALIZED="<tracker.bindings.normalized_final.path>"
NORMALIZED_SHA256="<tracker.bindings.normalized_final.sha256>"
PHASE1_SPEC="<tracker.bindings.normalized_phase1.path>"
PHASE1_SPEC_SHA256="<tracker.bindings.normalized_phase1.sha256>"
EXCLUSIONS="<tracker.bindings.normalization_exclusions.path>"
EXCLUSIONS_SHA256="<tracker.bindings.normalization_exclusions.sha256>"
DISPOSITIONS="<tracker.bindings.normalization_report.path>"
DISPOSITIONS_SHA256="<tracker.bindings.normalization_report.sha256>"
PLAIN_REPORT="<tracker.bindings.plain_eligibility.path>"
PLAIN_REPORT_SHA256="<tracker.bindings.plain_eligibility.sha256>"
PROFILES="<tracker.bindings.source_profiles.path>"
PROFILES_SHA256="<tracker.bindings.source_profiles.sha256>"
PROFILE_MAPPING="<tracker.bindings.source_profile_mapping.path>"
PROFILE_MAPPING_SHA256="<tracker.bindings.source_profile_mapping.sha256>"
SOURCE="<tracker.bindings.source_root.path>"
SOURCE_FILE="<tracker.bindings.source_file.path>"
SOURCE_SHA256="<tracker.bindings.source_file.sha256>"
SOURCE_GENERATION_ROOT="<tracker.bindings.source_generation_root.path>"
SOURCE_GENERATION_ID="<tracker.bindings.source_generation.id>"
SOURCE_GENERATION_MANIFEST_SHA256="<tracker.bindings.source_generation.manifest_sha256>"
SEG_ROOT="<tracker.bindings.segmentation_root.path>"
GENERATION_ID="<tracker.bindings.segmentation_generation.id>"
SEGMENTATION_MANIFEST_SHA256="<tracker.bindings.segmentation_generation.manifest_sha256>"
BASE_INPUT="<tracker.bindings.baseline_inputs.path>"
BASE_INPUT_SHA256="<tracker.bindings.baseline_inputs.sha256>"
MASKABILITY_REPORT="<tracker.bindings.maskability_report.path>"
MASKABILITY_REPORT_SHA256="<tracker.bindings.maskability_report.sha256>"
LEAKSCAN_REPORT="<tracker.bindings.leakscan_report.path>"
LEAKSCAN_REPORT_SHA256="<tracker.bindings.leakscan_report.sha256>"
ALLOWLIST="<tracker.bindings.oracle_allowlist.path>"
ALLOWLIST_SHA256="<tracker.bindings.oracle_allowlist.sha256>"
```

Derive `RUNS_ROOT` as the parent of tracker-bound `RUN`; do not read another
binding name.

Require `ALLOWLIST` to be exactly
`runs/$WB-variable-sources/oracle-allowlist.json`. No other allowlist path is
valid for maskability, offline leakscan, or the later oracle.
Require every path derived internally by `xl_variable_mcp.py`,
`gen_normalizer.py`, `plain_eligibility.py`, or `maskability.py` to equal its
tracker binding before accepting output; an interface/path mismatch is terminal.

## Phase 1: Freeze the unprofiled specification

### Gate 1A: Lossless import

```bash
python3 xl_variable_mcp.py import "$AUDIT" "$DRAFT"
```

Require the draft row count to equal imported Markdown rows and every imported
row to begin as `needs_review`.

### Gate 1B: Generate, run twice, and verify dispositions

```bash
python3 gen_normalizer.py "$WB" \
  --source-file "$SOURCE_FILE" --source-sha256 "$SOURCE_SHA256" \
  --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-root "$SEG_ROOT" \
  --segmentation-generation-id "$GENERATION_ID"
test "$NORMALIZER" = "$RUN/normalize_$WB.py"
python3 "$NORMALIZER"
```

Hash `normalized.json`, `exclusions.json`, and `normalization_report.json`, run
the same normalizer again, and require all three hashes to remain identical.
Require one disposition per draft ID, valid included variable IDs, non-empty
excluded reasons/codes, no unresolved row, atomic variable dimensions, atomic
temp-file replacement, and a fixed first-normalization snapshot.
Atomically copy the verified unprofiled bytes to the tracker-bound
`"$PHASE1_SPEC"`, `fsync` it and its parent, and require it to remain immutable.

### Gate 1C: Decide mode

```bash
python3 plain_eligibility.py "$RUN" --report "$PLAIN_REPORT"
```

`fail` is terminal. A specification that was non-empty and later emptied never
becomes plain.

For either mode, CAS-publish `bindings.task_mode.value`,
`bindings.normalized_phase1.{path,sha256}`, and the plain-eligibility hash. Keep
`lane_state["harbor-normalization-engineer"].state = running`, set
`phase = phase1_complete`, and set `disposition` to `plain` or `mcp`. Stop the
invocation so `harbor-orchestrator` can dispatch `harbor-source-profiler`.
This lane never mutates the profiler's state. For plain mode, the profiler
itself later records `state = passed` and `disposition = not_applicable`.

## Phase 2: Resume after profiler

Resume only from a fresh tracker snapshot where:

- `lane_state["harbor-source-profiler"].state` is exactly `passed`;
- its `disposition` is exactly `accepted` or `not_applicable`;
- its recorded input spec hash equals `bindings.normalized_phase1.sha256`;
- accepted disposition has tracker-bound `source_profiles` and
  `source_profile_mapping` hashes, complete validation, and no pending profile;
- not-applicable disposition has no accepted profile reference.

If any condition differs, stop with `source_profiler_binding_mismatch`.

For plain mode, require profiler disposition `not_applicable`; atomically copy
the immutable phase-1 bytes to tracker-bound `normalized_final`, validate the
copy hash, CAS-publish it, and set this lane to `state = passed`,
`phase = phase2_complete`, `disposition = plain`. Maskability and leakscan are
not applicable.

Through `normalize_$WB.py`, deterministically replace generic source entries
only when profiler disposition is `accepted`. Consume the tracker-bound
`source_profile_mapping`; match by canonical audited URL; set source `id` and
`profile_id` to the accepted `source_id`; set `dataset_key` to its unique mapped
dataset key; and preserve name, canonical URL, role, and kind. Ambiguous mapping
is terminal. For `not_applicable`, retain generic source entries and require an
empty mapping. Skipped, rejected, or unmatched sources also retain generic
rendering. This incorporation is the planned phase transition, not a repair.

Run the updated normalizer twice and require byte-identical normalized,
exclusion, and disposition outputs. For accepted profiler disposition, validate:

```bash
python3 .cursor/skills/profile-mcp-sources/scripts/validate_source_profiles.py \
  "$PROFILES" --inventory "$INVENTORY" --spec "$PHASE1_SPEC"
```

For either allowed profiler disposition, validate the final spec:

```bash
python3 xl_variable_mcp.py validate-spec "$NORMALIZED"
```

For accepted profiler disposition, require the profile capture's phase-1 spec
hash to remain bound while the final spec contains only validated
`profile_id`/`dataset_key` references.

## Phase 2 maskability gates

Run the repository maskability gate first:

```bash
python3 maskability.py "$WB" --source "$SOURCE" --runs-root "$RUNS_ROOT"
```

Before accepting it, require its derived spec and report paths to equal
`"$NORMALIZED"` and `"$MASKABILITY_REPORT"`. The current command must consume
only `"$ALLOWLIST"` and record that path/hash in `"$MASKABILITY_REPORT"`. If its
installed interface resolves a different
allowlist filename or cannot bind the canonical path, stop with
`maskability_allowlist_interface_mismatch`; do not copy, link, or rename the
allowlist.

Then run offline leakscan:

```bash
ALLOWLIST_ARG=()
test ! -f "$ALLOWLIST" || ALLOWLIST_ARG=(--allowlist "$ALLOWLIST")
python3 xl_variable_mcp.py leakscan "$NORMALIZED" \
  --workbook "$WB" --source "$SOURCE" \
  --inputs "$BASE_INPUT" \
  "${ALLOWLIST_ARG[@]}" \
  --report "$LEAKSCAN_REPORT"
```

Omit `--allowlist` only when the canonical file is absent and the tracker
records its hash as null. Require complete masking, justified extra cells,
compatible shared refs, and zero unapproved leaks.

## Repair proposal boundary

On a leak failure, emit and review proposals only:

```bash
ALLOWLIST_ARG=()
test ! -f "$ALLOWLIST" || ALLOWLIST_ARG=(--allowlist "$ALLOWLIST")
python3 xl_variable_mcp.py leakscan "$NORMALIZED" \
  --workbook "$WB" --source "$SOURCE" \
  --inputs "$BASE_INPUT" \
  "${ALLOWLIST_ARG[@]}" \
  --report "$LEAKSCAN_REPORT" \
  --emit-patch "$RUN/leak_patch.json"
```

Do not apply a proposal or edit final JSON. Emit next owner
`harbor-orchestrator` and suggested fixer `harbor-spec-fixer` with the proposal
hash and owning gate. The repaired normalizer must re-enter phase 1; profiling
and all downstream gates rerun.

## Publish final specification

Only after profile validation, `validate-spec`, `maskability.py`, and leakscan
all pass, CAS-publish `bindings.normalized_final = {path, sha256}` plus profile,
allowlist, maskability, and leakscan hashes. Packaging must
wait for this exact final binding and must never consume the phase-1 binding
directly. Set `lane_state["harbor-normalization-engineer"].state = passed`,
`phase = phase2_complete`, and `disposition = mcp`.
