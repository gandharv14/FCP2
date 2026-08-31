---
name: harbor-source-profiler
description: Profiles canonical public sources through bounded Sol High research and validates hash-bound source profiles. Use after normalization phase 1 for either plain or MCP Harbor workbook lanes.
disable-model-invocation: true
---

# Harbor Source Profiler

Wrap and follow [Profile MCP Sources](../profile-mcp-sources/SKILL.md), the
[source-profile contract](../profile-mcp-sources/SOURCE_PROFILES.md), and its
[formal schema](../profile-mcp-sources/source_profiles.schema.json). This skill
adds the autonomous lane, tracker, independent acceptance, and handoff policy;
it does not replace the wrapped gate.

## Lane contract

Work from the `synthetic-data-pipeline` repository root. Require `batch`, `id`,
`task_mode`, normalization-phase-1 disposition, and all artifact bindings from
`harbor-orchestrator`. Refuse mutable aliases, unbound paths, or missing hashes.

Read and atomically update:

`runs/harbor-fleet/<batch>/workbooks/<id>.json`

The canonical lane ID is exactly `harbor-source-profiler`. Every tracker
mutation must:

1. acquire an exclusive OS file lock on the sibling `<id>.json.lock`;
2. read and validate the tracker, its integer `revision`, and its byte hash;
3. preserve unknown fields and history, then update only this lane plus shared
   aggregate fields;
4. set `revision` to exactly the prior value plus one;
5. while still holding the lock, re-read and require the same revision and byte
   hash (compare-and-swap);
6. write a sibling temporary file, flush and `fsync`, atomically replace the
   tracker, `fsync` its parent directory, then release the lock.

Its lane `state` may be only `pending`, `ready`, `running`, `repairing`,
`passed`, or `terminal`; `not_applicable` is a disposition, not a state.

Discard a conflicting candidate and rebuild it from the latest tracker; never
overwrite a newer revision. Record attempt, gates, evidence paths, diagnostic
IDs, artifact SHA-256 values, accepted/skipped/rejected/generic counts, and
`lane_state["harbor-source-profiler"].current_confidence` with `level` and
`reason_codes`. Append every confidence transition to that lane's
`confidence_history`; entries are immutable and include tracker revision,
timestamp, diagnostic IDs, artifact hashes, level, reasons, and repair
authorization when applicable. This lane may not write another lane's current
value or history.

Current confidence may improve only after an orchestrator-authorized closed
repair resolves the exact recorded diagnostic and the owning gate plus every
invalidated downstream gate re-passes on new hashes. Preserve the prior low
entry in history; it does not permanently cap the repaired lane. Recompute
top-level `current_confidence` as the worst current value across required lanes
(`low` < `medium` < `high`) and union only their current namespaced reasons.
If any required lane lacks a current value, the aggregate is null and cannot
promote. A current low value cannot advance.

On any blocking or repairable result, append this handoff under `handoffs[]`
and stop:

```json
{
  "schema_version": "harbor-fleet-handoff/v1",
  "batch": "<batch>",
  "workbook_id": "<id>",
  "lane": "harbor-source-profiler",
  "gate": "<owning-gate>",
  "status": "failed",
  "failure_code": "<stable-code>",
  "summary": "<sanitized-summary>",
  "artifact_hashes": {"<path>": "<sha256>"},
  "current_confidence": "high|medium|low",
  "reasons": ["<reason>"],
  "diagnostics": ["<path>"],
  "next_owner": "harbor-orchestrator",
  "suggested_fixer": "<skill-or-none>",
  "attempts": {"used": 0, "remaining": 0},
  "rerun": {"owning_gate": "source-profile-init", "downstream": true}
}
```

Do not solicit decisions. The lane never edits failed output or dispatches a
fixer. Only the orchestrator may authorize a bounded redispatch. Any repair
invalidates this gate and every downstream gate; rerun them against new hashes.

## Scheduling

Invoke this lane after normalization phase 1 in both modes. If `task_mode` is
`plain` or the phase-1 specification is empty, do no profile work: set
`lane_state["harbor-source-profiler"].state` to `passed`, disposition to
`not_applicable`, record the bound eligibility-report SHA-256, model-call count
zero, and per-lane current confidence `high` with reason
`plain_or_empty_phase1`; append that transition to confidence history.
Recompute aggregate current confidence through the locked CAS procedure and
return to the orchestrator.

Only the MCP branch proceeds. Require `normalized_phase1` to be valid,
non-empty, and declared fixed at its verified SHA-256 for this profiling
dispatch. No process may mutate it while profiling or reviewing profiles.
Read-only work may run concurrently only when bound to the same hash.
Normalization phase 2 starts after this lane emits its mapping. Any upstream
repair or changed phase-1 hash invalidates profiling and all downstream
evidence.

## Profile workflow

Resolve these exact tracker bindings; never synthesize a conventional path:

```bash
WB="<id>"
RUN="<variable_source_run.path>"
INVENTORY="<variable_source_inventory.path>"
NORMALIZED="<normalized_phase1.path>"
PROFILES="<source_profiles.path>"
MAPPING="<source_profile_mapping.path>"
FRAGMENTS="$RUN/profile-fragments"
P=.cursor/skills/profile-mcp-sources/scripts
ARCHIVE=.cursor/skills/create-harbor-task/scripts/archive_run_dir.py
```

Before use, verify `variable_source_inventory.sha256`,
`normalized_phase1.sha256`, and any existing `source_profiles.sha256` or
`source_profile_mapping.sha256` against exact bytes. Require every file binding
to belong to the bound `variable_source_run.path`; a mismatch is blocking.
After each write, hash exact bytes and CAS-update the corresponding tracker
binding.

1. Build the wrapped skill's redacted worklist. Canonicalize, gate, and
   deduplicate URLs before any retrieval. Never expose workbook values, cells,
   questions containing values, private-source descriptions, or graded data.
   Hash its stable serialization and record `worklist_sha256`.
2. Redispatch is explicit:
   - Resume only when the worklist, inventory, and phase-1 spec hashes match
     the prior capture. Re-stamp with `rehash`, run normal validation, and
     continue only from pending work.
   - Otherwise archive the stale document and fragment directory, record their
     archive paths and hashes, and create a fresh envelope. Never overwrite or
     force initialization.

```bash
python3 "$ARCHIVE" "$PROFILES" "$FRAGMENTS"
python3 "$P/assemble_profiles.py" init \
  --out "$PROFILES" --spec "$NORMALIZED" --inventory "$INVENTORY"
```

For an unchanged worklist resume:

```bash
python3 "$P/assemble_profiles.py" rehash \
  --doc "$PROFILES" --spec "$NORMALIZED" --inventory "$INVENTORY"
python3 "$P/validate_source_profiles.py" \
  "$PROFILES" --inventory "$INVENTORY" --spec "$NORMALIZED"
```

3. Launch fresh `generalPurpose` subagents with model
   `gpt-5.6-sol-high`, with no more than six deduplicated canonical URLs in
   each subagent. Independent batches may run concurrently.
4. Enforce the wrapped safety budget exactly: at most three public page reads
   per source—the canonical page and at most two directly linked same-origin
   pages. Retrieval is read-only. No browser automation, authentication,
   cookies, credentials, form submission, challenge bypass, mirrors, caches,
   private endpoints, value search, or value inference.
5. A login or SSO page, password gate, 401, 403, paywall, bot challenge,
   unreachable source, or unsupported content is immediately skipped with the
   wrapped closed `skip_reason`. Do not retry access or extract any source
   detail. That source retains generic rendering.
6. Require one schema-conforming producer fragment per source with
   `review.status: pending` and merge only through:

```bash
python3 "$P/assemble_profiles.py" merge \
  --doc "$PROFILES" --fragment "<fragment.json>"
```

Subagents never edit `source_profiles.json`. A rejected fragment is not
hand-edited. Record the exact field error and return a repair handoff. The
orchestrator may authorize one corrected producer submission for that source;
a second rejection permanently omits the profile and records generic behavior.
Access-barrier skips have zero retries.

## Independent acceptance

Producer output is never accepted directly. For each pending producer fragment,
launch a fresh `generalPurpose` reviewer with model `gpt-5.6-sol-high` that did
not produce it. Give the reviewer only the redacted fragment and its public
capture evidence—never the workbook, normalized values, cells, questions,
graded targets, or another review.

The reviewer writes a replacement fragment with the same source identity and
capture facts. It may set `review.status` only to `accepted` or `rejected`, set
itself and review time, and explain rejection in `review.notes`; it must not
add unsupported evidence or repair producer prose. Validate and merge every
replacement only through the assembler:

```bash
python3 "$P/assemble_profiles.py" merge \
  --doc "$PROFILES" --fragment "<reviewed-fragment.json>"
```

Malformed reviewer output receives no lane-side repair. Emit a handoff to the
orchestrator with `suggested_fixer: none`; it is terminal for that source and
the source retains generic behavior. There is no manual acceptance path. Only
`profiled` plus independently `accepted` is eligible for downstream mapping;
rejected, pending, skipped, and unmatched profiles retain generic behavior.

## Closed validation

After all merges, stamp hashes and then validate without mutation, in this
order:

```bash
python3 "$P/validate_source_profiles.py" \
  "$PROFILES" --inventory "$INVENTORY" --spec "$NORMALIZED" --rehash
python3 "$P/validate_source_profiles.py" \
  "$PROFILES" --inventory "$INVENTORY" --spec "$NORMALIZED"
```

The second command must print `OK`. Require closed schemas, capture metadata,
bounded excerpts, public attribution, no auth-derived text, and no workbook
value leakage. The profiler must not edit the bound phase-1 specification.

Emit the mapping at bound `$MAPPING` for normalization phase 2, bound to the
phase-1 spec hash and final profiles hash. Each row names the normalized
source identity, canonical URL, independently accepted `profile_id`, and
deterministic `dataset_key`; no rejected or generic profile may appear.
Normalization phase 2 alone validates and atomically applies this mapping,
then runs final specification validation and downstream gates.

```json
{
  "schema_version": "harbor-source-profile-mapping/v1",
  "phase_1_spec_sha256": "<sha256>",
  "profiles_sha256": "<sha256>",
  "mappings": [
    {
      "normalized_source_id": "<id>",
      "canonical_url": "<url>",
      "profile_id": "<accepted source_id>",
      "dataset_key": "<deterministic key>"
    }
  ]
}
```

Sort rows by normalized source ID and reject duplicate, ambiguous, unmatched,
or non-accepted mappings rather than guessing a dataset key.

A whole-document validation failure is blocking. Preserve fragments, command
output, and hashes, set current confidence to `low`, append the transition to
confidence history, and hand off without changing the profile document or
normalized spec.

## Completion

Pass only when both profile validations succeed, the mapping protocol closes,
and every tracker binding hash matches bytes on disk. Report
candidate/deduplicated/accepted/skipped/rejected counts, every skip reason,
generic fallbacks, model, bounded read policy, producer/reviewer call counts,
profile and mapping hashes, and the unchanged phase-1 spec hash. Set current
confidence to `medium` for a valid gate with policy-skipped public sources and
to `high` when every eligible source is independently accepted; reasons and a
history entry are mandatory in either case.

Any completion handoff uses the same envelope with `status: passed`,
`next_owner: harbor-orchestrator`, and `suggested_fixer: none`.
