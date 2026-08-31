---
name: harbor-prose-fixer
description: Prepares bounded corrective context for Harbor disclosure, naturalization, and dialogue retries without launching producers or applying prose. Use when the orchestrator routes an A7, A8, or A10 validator diagnostic off-lane.
disable-model-invocation: true
---

# Harbor prose fixer

Prepare corrective context and immutable support artifacts for one off-lane
validator failure. Never launch a producer, edit final prose, run apply, continue
packaging, or promote. The owning A7, A8, or A10 lane consumes the context, launches
its producer, validates, and applies. All routing returns through
`harbor-orchestrator`.

## Shared tracker contract

Use:

```text
runs/harbor-fleet/<batch>/workbooks/<id>.json
```

Every mutation uses
`runs/harbor-fleet/<batch>/workbooks/<id>.json.lock`. Mutate only canonical
`lane_state["harbor-disclosure-writer"]`,
`lane_state["harbor-instruction-naturalizer"]`, or
`lane_state["harbor-dialogue-author"]`; never create `lanes`, display-name keys, or
a fixer lane. Allowed states are `pending`, `ready`, `running`, `repairing`,
`passed`, and `terminal`. Use that lock and compare-and-swap:

1. Read a snapshot and its nonnegative integer `revision`.
2. Acquire the shared tracker lock used by every lane.
3. Require the live revision to equal the snapshot revision.
4. Preserve unknown fields and history; apply the mutation.
5. Set `revision` to exactly the prior value plus one, write a sibling temporary
   file, flush and `fsync`, atomically replace the tracker, and `fsync` its parent.
6. On a CAS mismatch, discard the candidate, reload, and recompute the mutation.

Set the assigned owning lane to `repairing`. Append one `repairs.history` item with
fixer, diagnostic signature, gate retry slot, context attempt, hashes, result,
timestamps, and reentry gate; increment `repairs.count`. Each lane owns a
`current_confidence` object and append-only `confidence_history`. This fixer reports
repair evidence and does not raise or rewrite either field. On every commit,
recompute top-level `current_confidence` as the worst of all populated lane
`current_confidence` values under `low < medium < high`.

Only the owning lane may improve its `current_confidence`, and only after the
orchestrator authorizes the closed repair, the exact diagnostic is resolved, and
all invalidated owning and downstream gates pass on new hashes. That lane appends
the transition to `confidence_history`. Preserve historical low entries; they
remain evidence but do not permanently block a fully proven repair.

Copy validator messages character-for-character into `diagnostics`. Prefixes such
as `10|` shown by file viewers are line metadata, not file content; never copy those
prefixes into prompts, prose, templates, or artifacts. Record a warning when such
metadata is stripped from corrective context.

## Closed boundary and budget

Accept exactly one orchestrator-assigned gate retry slot and stable diagnostic
signature. Prepare exactly one repair-context attempt for that slot. If a matching
attempt already exists in `repairs.history`, return terminal without writing a new
context.

Never edit `instruction.md`, disclosure records, resolver code, `draft.md`,
`additional-assumptions.md`, templates, slots, task metadata, or validators. Never
invoke a model, producer, recovery apply, template refill, or final validation.
Mixed repair classes are terminal.

Write context only beneath:

```text
runs/harbor-fleet/<batch>/repairs/<id>/<gate>/<signature>/context.json
```

Bind schema version, workbook and batch, owning lane, gate, signature, exact
validator errors, immutable input hashes, allowed output paths, constraints,
remaining retry slot, and context SHA-256. Do not include answer values or unrelated
workbook content.

## A7 disclosure context

For a repairable row-label finding, include only the record ID, failing sentence,
verbatim finding, referenced golden location, deterministic nearest semantic label,
and input hashes. Resolve the label by walking left on the same golden row while
skipping formulas, cached display text, numbers, blanks, units, errors, and scenario
markers.

If the correct result requires editing shared resolver code, classify
`resolver_code_defect` and return terminal. Do not authorize or prepare a patch.
Otherwise return context to `harbor-orchestrator` for A7
`harbor-disclosure-writer`, which owns regeneration, faithcheck, verification,
naturalization invalidation, and fresh independent review.

## A8 naturalization context

Only when recovery state proves attempt 2 is the assigned unused retry slot, bind
the immutable original frozen writer-source hash, span-map hash, failed check names,
verbatim restore or validation errors, expected and observed literals, and recorded
semantic reason codes. Exclude the failed candidate from producer inputs.

Return context to the orchestrator for A8 `harbor-instruction-naturalizer`. A8 owns
the fresh Sol High producer, recovery CLI submit/reject/accept/apply sequence,
clause review, and `verify-applied`. An absent or consumed attempt-2 slot is
terminal.

## A10 dialogue context

Only when dialogue state proves an unused same-round retry slot, bind the current
round, exact `fill-check` structural error and slot ID, or exact `check-review`
schema errors and strict JSON template. Bind hashes for the original template,
slots, writer pack, and incoming filled draft. Do not create or refill a draft.

Return context to the orchestrator for A10 `harbor-dialogue-author`. A10 owns the
same producer identity, template refill, review correction, all dialogue validators,
transactional apply, and smoke. An absent or consumed same-round slot is terminal.

## Return

On successful context preparation, append a handoff with
`next_owner: harbor-orchestrator`, `result: reentry_gate`, the exact owning A7/A8/A10
gate, context path and hash, one consumed context attempt, downstream invalidation,
and repair evidence. Do not dispatch the owning lane or change its
`current_confidence`.

Return terminal through the orchestrator for missing immutable inputs, mixed
classes, shared resolver defects, ambiguous labels, unavailable retry slots,
repeated signatures, or any second context attempt. Context preparation never marks
the owning lane passed and never changes its confidence state or history.
