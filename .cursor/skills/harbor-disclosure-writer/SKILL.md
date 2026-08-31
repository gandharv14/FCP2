---
name: harbor-disclosure-writer
description: Generates and mechanically verifies unified Harbor workbook disclosure with bounded Sol High role arbitration. Use when an autonomous workbook lane has a hash-bound staged bundle, golden workbook, AST, and segmentation generation.
disable-model-invocation: true
---

# Harbor Disclosure Writer

Wrap and follow [Task Disclosure](../task-disclosure/SKILL.md), its
[registry](../task-disclosure/REGISTRY.md), and its
[taxonomy](../task-disclosure/TAXONOMY.md). This skill adds the autonomous
lane, tracker, retry, and repair handoff contract. The wrapped detectors,
registry, renderer, faithcheck, and verifier remain authoritative.

## Lane contract

Work from the `synthetic-data-pipeline` repository root. Require `batch`, `id`,
the staged task path, immutable golden and AST paths, source and segmentation
generation IDs, and SHA-256 hashes for every input.

Read and atomically update:

`runs/harbor-fleet/<batch>/workbooks/<id>.json`

The canonical lane ID is exactly `harbor-disclosure-writer`. Every tracker
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

Discard a conflicting candidate and rebuild it from the latest tracker; never
overwrite a newer revision. Record attempt, gates, evidence paths, diagnostic
IDs, artifact SHA-256 values, disclosure counts, arbitration count, and
`lane_state["harbor-disclosure-writer"].current_confidence` with `level` and
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

On any failed gate, append this handoff under `handoffs[]` and stop:

```json
{
  "schema_version": "harbor-fleet-handoff/v1",
  "batch": "<batch>",
  "workbook_id": "<id>",
  "lane": "harbor-disclosure-writer",
  "gate": "<owning-gate>",
  "status": "failed",
  "failure_code": "<stable-code>",
  "summary": "<sanitized-summary>",
  "artifact_hashes": {"<path>": "<sha256>"},
  "current_confidence": "high|medium|low",
  "reasons": ["<reason>"],
  "diagnostics": ["<path>"],
  "next_owner": "harbor-orchestrator",
  "suggested_fixer": "harbor-prose-fixer|none",
  "attempts": {"used": 0, "remaining": 0},
  "rerun": {"owning_gate": "<gate>", "downstream": true}
}
```

Do not solicit decisions. The writer lane never repairs its output, edits a
failed artifact, or dispatches a fixer. The orchestrator alone classifies and
routes the handoff. Every accepted repair reruns the owning gate and all
downstream gates against new hashes.

## Resolve immutable inputs

Use tracker values rather than mutable publication aliases:

```bash
WB="<id>"
STAGED="<tracker staged_path>"
GOLDEN="<tracker immutable golden_path>"
AST_ROOT="<tracker immutable ast_root>"
SEG_ROOT="<tracker segmentation_root>"
SOURCE_GENERATION_ID="<tracker source_generation_id>"
GENERATION_ID="<tracker segmentation_generation_id>"
D=.cursor/skills/task-disclosure/scripts/disclose.py
DISCLOSURE_RUN="runs/disclosure/$WB-outputs"
```

Refuse to run if the staged workbook hash differs from the tracker or if any
generation binding is missing.

## Closed workflow

Run the stages in exactly this order:

```bash
python3 "$D" select \
  --task-dir "$STAGED" \
  --golden "$GOLDEN" \
  --ast-dir "$AST_ROOT" \
  --seg-root "$SEG_ROOT" \
  --segmentation-mode strict \
  --source-generation-root source_out \
  --source-generation-id "$SOURCE_GENERATION_ID" \
  --segmentation-generation-id "$GENERATION_ID"
python3 "$D" probe   --task-dir "$STAGED"
python3 "$D" roles   --task-dir "$STAGED"
```

If `"$DISCLOSURE_RUN/ambiguous_roles.json"` contains cases, launch exactly one
`generalPurpose` role arbiter for that submission with model
`gpt-5.6-sol-high`. Give it only the cases file and the
`role_resolutions.json` output path. It may choose only a listed candidate or
`null`; it must cover each case once and must not inspect workbooks, formulas,
cached values, graded targets, or catalogue variants.

Validate any resolution before detection:

```bash
python3 "$D" roles-validate --task-dir "$STAGED"
```

Never run `roles-validate` when there were zero cases. On a malformed
resolution, preserve the exact errors and return them only to the same
arbitration producer once, as the wrapped skill permits. This format retry
cannot change cases, candidates, or policy and does not involve
`harbor-prose-fixer`. Run `roles-validate` again; a second malformed submission
is terminal. There is exactly one producer retry and never a replacement
arbiter or parallel vote.

Continue only after zero collisions or validated resolutions:

```bash
python3 "$D" detect     --task-dir "$STAGED"
python3 "$D" context    --task-dir "$STAGED"
python3 "$D" write      --task-dir "$STAGED"
python3 "$D" faithcheck --task-dir "$STAGED" --golden "$GOLDEN"
python3 "$D" verify     --task-dir "$STAGED"
```

Require all of:

```bash
test -f "$DISCLOSURE_RUN/bands.json"
test -f "$DISCLOSURE_RUN/records.json"
test -f "$DISCLOSURE_RUN/faithcheck.json"
test -f "$DISCLOSURE_RUN/verify.json"
test -f "$STAGED/tests/disclosure.json"
```

`faithcheck` must precede `verify`. A nonzero faithcheck blocks verification
and every downstream gate. No override, force mode, old hint section, formula,
evidence, or answer value may reach the final instruction.

## Repair taxonomy and caps

- Incorrect semantic row label: repairable once only as closed row-label data
  regeneration when the existing wrapped leftward resolver can establish one
  unambiguous label. Hand off to the orchestrator with
  `suggested_fixer: harbor-prose-fixer`; F2 may prepare bounded corrective
  context and data regeneration but never edit shared resolver or generator
  code. Regenerate upstream records, then rerun `detect`, `context`, `write`,
  `faithcheck`, and `verify`.
- Malformed role-arbitration output: one corrected submission as specified
  above.
- Missing/reversed mechanics, omitted references or literals, wrong signs or
  operators, incomplete ranges, wrong period/copy scope, answer leakage, an
  unresolved label, or a repeated fault after regeneration: terminal.
- Any generic resolver, detector, renderer, generator, or other shared-code
  defect is terminal for the lane. Preserve diagnostics for a separate code
  change; F2 cannot change shared code.

Never directly edit `instruction.md`, `tests/disclosure.json`,
`records.json`, or generated disclosure prose. A repair changes upstream input
or closed row-label data only. Every failure handoff keeps
`next_owner: harbor-orchestrator` and names a `suggested_fixer` or `none`.
After any disclosure repair, rerun instruction naturalization, independent
verification, dialogue generation, and promotion checks.

## Completion

Pass only when every command succeeds, role resolutions are validated when
needed, both mechanical reports pass, disclosure heading presence matches
non-empty `agent_records`, and recorded hashes match disk. Set current
confidence to `high`; any unresolved ambiguity sets current `low` and blocks.
Append either transition to confidence history. Return the staged
instruction/disclosure hashes, record-category counts, arbitration count,
faithcheck and verify paths, and a structured handoff to `harbor-orchestrator`.

Any completion handoff uses the same envelope with `status: passed`,
`next_owner: harbor-orchestrator`, and `suggested_fixer: none`.
