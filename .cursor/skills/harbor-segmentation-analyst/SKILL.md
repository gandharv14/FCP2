---
name: harbor-segmentation-analyst
description: Produces and strictly validates immutable workbook segmentation, preserves curation, and runs source preflight. Use when harbor-orchestrator assigns the segmentation and preflight lane for a Harbor fleet workbook.
disable-model-invocation: true
---

# Harbor Segmentation Analyst

Own segmentation, autonomous curation acceptance, strict generation validation,
and preflight. Keep source and segmentation generations inactive.

## Shared contract

- Canonical lane ID: `harbor-segmentation-analyst`.
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
- Use exactly `lane_state["harbor-segmentation-analyst"]`. Its `state` is one
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
  `lane_state["harbor-segmentation-analyst"]`.
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
  "lane": "harbor-segmentation-analyst",
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

Read one tracker revision and bind all inputs below. Before any command, verify
the source, source-generation manifest, AST provenance, and AST files against
their tracker hashes and require the source lane to be passed.

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
AST_WORKBOOK_ROOT="<tracker.bindings.ast_workbook_root.path>"
AST_WORKBOOK_ROOT_SHA256="<tracker.bindings.ast_workbook_root.sha256>"
SEG_ROOT="<tracker.bindings.segmentation_root.path>"
SEG_WORKBOOK_ROOT="<tracker.bindings.segmentation_workbook_root.path>"
CURATION="<tracker.bindings.curation.path>"
CURATION_SHA256="<tracker.bindings.curation.sha256>"
PREFLIGHT_ROOT="<tracker.bindings.preflight_root.path>"
PREFLIGHT_REPORT="<tracker.bindings.preflight_report.path>"
```

Missing or conflicting source bindings stop the lane.

## Gate 1: Preserve curation and segment

If `"$CURATION"` exists, hash it before segmentation and
require the same hash afterward.

```bash
test ! -f "$CURATION" || shasum -a 256 "$CURATION"
python3 xl_segment.py "$WB" \
  --source-generation-root "$SOURCE_GENERATION_ROOT" \
  --source-generation-id "$SOURCE_GENERATION_ID" -o "$SEG_ROOT"
```

Capture the exact generation ID returned by `xl_segment.py`:

```bash
GENERATION_ID="<generation_id returned by xl_segment.py>"
```

Apply this autonomous acceptance policy:

- An unchanged pre-existing curation sets current confidence to `high` and its
  before/after hash as the reason.
- Fresh curation generated in this invocation with heuristic or LLM provenance
  sets current confidence to `high`; record provenance and included outputs.
- A fresh top-4 fallback sets current confidence to `medium`; record that the
  fallback selected outputs because stronger selection produced none.
- Zero included outputs, a changed pre-existing curation hash, or provenance
  not bound to this invocation is terminal.
- There is no human checkpoint. Never mutate curation to make later gates pass.

Summarize every included output and the strongest exclusions in the workbook
record. Recuration is prohibited.

## Gate 2: Strict immutable validation

Only this full validator establishes PASS:

```bash
python3 -m xl_seg.publication validate-id "$SEG_WORKBOOK_ROOT" "$GENERATION_ID" \
  --source "$SOURCE_FILE" --ast-dir "$AST_WORKBOOK_ROOT" \
  --source-generation-dir "$SOURCE_GENERATION_DIR" \
  --validate-live-evidence --require-pass
GENERATION_ID=$(python3 -m xl_seg.publication validate-id \
  "$SEG_WORKBOOK_ROOT" "$GENERATION_ID" \
  --source "$SOURCE_FILE" --ast-dir "$AST_WORKBOOK_ROOT" \
  --source-generation-dir "$SOURCE_GENERATION_DIR" \
  --validate-live-evidence --require-pass |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["generation_id"])')
```

Strict FAIL, skipped live evidence, missing output candidates, generation drift,
source/AST mismatch, or any non-PASS verdict is terminal. Do not infer success
from `current.json`, schema validity, or partial verifier output.

Hash the immutable generation manifest, `segments.json`, `bands.csv`,
`output_candidates.csv`, `lineage.json`, and lineage directory artifacts.

## Gate 3: Preflight

```bash
python3 xl_preflight.py "$WB" --source "$SOURCE" --seg-root "$SEG_ROOT" \
  --out "$PREFLIGHT_ROOT"
```

Require `"$PREFLIGHT_REPORT"` classification `healthy`. These are terminal:
`frontier_unsafe`, `unsafe_circular`, `stale_cache_repairable`,
`cached_mismatch`, `unverified`, and `missing_source`. Preserve the report as
the quarantine diagnostic; do not work around it.

## Success record

Record source and segmentation generation IDs, curation before/after hash,
acceptance class and reason, included outputs, strongest exclusions, strict
validator evidence, preflight path/hash, and current confidence. Success requires
curation acceptance, strict PASS, and healthy preflight.
CAS-publish the immutable segmentation generation ID, manifest and artifact
hashes, curation hash, and preflight hash before downstream dispatch.
CAS-set `lane_state["harbor-segmentation-analyst"].state = passed`.
