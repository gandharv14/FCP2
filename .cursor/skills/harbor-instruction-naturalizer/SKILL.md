---
name: harbor-instruction-naturalizer
description: Naturalizes complete Harbor finance instructions through frozen spans, fresh Sol High writers, semantic review, and journaled apply verification. Use after disclosure passes on a hash-bound staged workbook bundle.
disable-model-invocation: true
---

# Harbor Instruction Naturalizer

Wrap and follow [Naturalize Finance Task Instruction](../naturalize-finance-task-instruction/SKILL.md).
This skill adds autonomous tracking, off-lane repair routing, and a closed
two-attempt policy. The wrapped recovery state and validators are authoritative.

## Lane contract

Work from the `synthetic-data-pipeline` repository root. Require `batch`, `id`,
the staged path, disclosure pass hashes, and the exact `instruction.md`,
`task.toml`, and answer-key SHA-256 values from `harbor-orchestrator`.

Read and atomically update:

`runs/harbor-fleet/<batch>/workbooks/<id>.json`

The canonical lane ID is exactly `harbor-instruction-naturalizer`. Every
tracker mutation must:

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
IDs, source/frozen-span/candidate/instruction/metadata/journal hashes, and
`lane_state["harbor-instruction-naturalizer"].current_confidence` with `level`
and `reason_codes`. Append every confidence transition to that lane's
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

On any failure, append this handoff under `handoffs[]` and stop:

```json
{
  "schema_version": "harbor-fleet-handoff/v1",
  "batch": "<batch>",
  "workbook_id": "<id>",
  "lane": "harbor-instruction-naturalizer",
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
  "rerun": {
    "owning_gate": "naturalization-attempt-02|environment-packager",
    "downstream": true
  }
}
```

Do not solicit decisions. This lane never repairs a rejected attempt, changes
validator output, edits the staged instruction directly, or dispatches a
fixer. The orchestrator routes attempt-one diagnostics to
`harbor-prose-fixer`. Every accepted repair reruns its owning gate and all
downstream gates against new hashes.

## Initialize immutable state

Set:

```bash
WB="<id>"
STAGED="<tracker staged_path>"
NAT_RUN="runs/$WB-instruction-naturalization"
SOURCE="$NAT_RUN/source.snapshot.md"
WRITER_SOURCE="$NAT_RUN/writer_source.md"
SPAN_MAP="$NAT_RUN/frozen_spans.json"
MARKED="$NAT_RUN/candidate.marked.md"
CANDIDATE="$NAT_RUN/candidate.md"
RESTORED_PREAMBLE="$NAT_RUN/restored_preamble_body.md"
RESTORED_INPUT="$NAT_RUN/restored_input_body.md"
RECOVERY=.cursor/skills/naturalize-finance-task-instruction/scripts/naturalize_recovery.py
FREEZE=.cursor/skills/naturalize-finance-task-instruction/scripts/freeze_protected_spans.py
```

Initialize or resume only when the live source matches the existing snapshot:

```bash
uv run --python 3.12 python "$RECOVERY" init "$STAGED/instruction.md" \
  --state-dir "$NAT_RUN" \
  --instruction "$STAGED/instruction.md" \
  --task-toml "$STAGED/task.toml" \
  --answer-key "$STAGED/tests/answer_key.json"
```

Never naturalize a promoted task. A source mismatch is terminal for this run
and requests a fresh stage from the orchestrator.

## Freeze before model access

Freeze every validator-protected phrase and every exact-count number, cell
reference, URL, and inline-code span:

```bash
uv run --python 3.12 python "$FREEZE" freeze "$SOURCE" \
  --writer-source "$WRITER_SOURCE" \
  --span-map "$SPAN_MAP"
```

If freezing fails, no writer is launched. Writers receive only
`writer_source.md`, never the raw source, golden workbook, formulas, graded
targets, or failed candidate.

## Two fresh attempts

Use at most two total `generalPurpose` writer calls, each with model
`gpt-5.6-sol-high`. Each writer starts fresh from the same immutable
`writer_source.md` and writes only:

```bash
PREAMBLE="$NAT_RUN/attempt-01/preamble_body.md"
INPUT_BODY="$NAT_RUN/attempt-01/input_body.md"
```

Use `attempt-02` only after an orchestrator-authorized prose-fixer handback.
The writer must preserve every frozen block exactly once and must not emit
headings or protected sections. Include this sentence verbatim in every writer
prompt:

> Line prefixes such as `10|` are tool metadata and must never be copied into either output.

Also require zero-loss preservation of scope, deliverables, permissions,
prohibitions, procedures, modalities, finance terminology, names, paths,
periods, units, and exact tokens. The writer must add no formula, fact,
assumption, hint, interpretation, modelling advice, or answer value.

Code, not a model or manual edit, assembles the complete marked candidate from
the immutable `writer_source.md` plus exactly the two writer bodies:

```bash
uv run --python 3.12 python - \
  "$WRITER_SOURCE" "$PREAMBLE" "$INPUT_BODY" "$MARKED" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".cursor/skills/naturalize-finance-task-instruction/scripts")
from instruction_spans import assemble_instruction, scan_instruction
from naturalize_recovery import atomic_write_bytes
source = Path(sys.argv[1]).read_bytes()
spans = scan_instruction(source)
marked = assemble_instruction(
    source, spans, Path(sys.argv[2]).read_bytes(), Path(sys.argv[3]).read_bytes()
)
atomic_write_bytes(Path(sys.argv[4]), marked)
PY
```

Hash-check `writer_source.md` against the post-freeze tracker value immediately
before assembly. Then restore canonical spans:

```bash
uv run --python 3.12 python "$FREEZE" restore "$MARKED" \
  --span-map "$SPAN_MAP" \
  --output "$CANDIDATE" \
  --report "$NAT_RUN/restore.json"
```

Missing, duplicated, unknown, edited, or residual markers fail closed. Extract
the restored bodies through the same code-owned span parser:

```bash
uv run --python 3.12 python - \
  "$CANDIDATE" "$RESTORED_PREAMBLE" "$RESTORED_INPUT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".cursor/skills/naturalize-finance-task-instruction/scripts")
from instruction_spans import extract_editable_bodies, scan_instruction
from naturalize_recovery import atomic_write_bytes
candidate = Path(sys.argv[1]).read_bytes()
preamble, input_body = extract_editable_bodies(
    candidate, scan_instruction(candidate)
)
atomic_write_bytes(Path(sys.argv[2]), preamble)
atomic_write_bytes(Path(sys.argv[3]), input_body)
PY
```

Submit only those restored bytes:

```bash
uv run --python 3.12 python "$RECOVERY" submit "$NAT_RUN" \
  --preamble "$RESTORED_PREAMBLE" --input "$RESTORED_INPUT"
```

Require mechanical validation to pass. A retry is permitted only when
`state.json` reports `retry_ready`.

## Journal semantic review and apply

A reviewer that did not write the candidate performs clause-by-clause review
of both editable regions against the immutable source. It rejects any omitted,
weakened, strengthened, or added claim. Record an attempt-one rejection:

```bash
uv run --python 3.12 python "$RECOVERY" reject "$NAT_RUN" \
  --reason-code semantic_mismatch \
  --message "<specific omitted, weakened, or added claim>"
```

Mechanical, restore, generation, or semantic errors on attempt one are handed
verbatim to the orchestrator with `suggested_fixer: harbor-prose-fixer`.
F2 may only prepare corrective context; it does not launch a writer, submit a
candidate, review, accept, or apply. This lane launches the fresh attempt-two
writer from the original frozen source and owns every remaining command. No
viewer line prefixes may enter either body, corrective context, or artifact.

After mechanical and semantic review pass, bind approval, apply the two-file
transaction, and verify:

```bash
uv run --python 3.12 python "$RECOVERY" accept "$NAT_RUN" \
  --reviewer "independent-semantic-reviewer" \
  --message "Clause-by-clause semantic review passed"
uv run --python 3.12 python "$RECOVERY" apply "$NAT_RUN"
uv run --python 3.12 python "$RECOVERY" verify-applied "$NAT_RUN"
```

Require `valid: true`, `applied: true`, model `gpt-5.6-sol-high`, matching
source/candidate hashes, protected bytes, exact tokens, semantic anchors, and
no new answer-value occurrence. An interrupted journal must roll back or
reconcile before continuing; mixed `instruction.md` and `task.toml` state is
blocking.

## Caps and terminal policy

- Exactly two fresh writer attempts are available in total; after attempt two,
  no third generation exists.
- Attempt two never reads attempt one's candidate.
- Any attempt-two generation, restore, validation, or semantic-review failure
  is terminal for this stage.
- Exhaustion returns a terminal handoff with `rerun.owning_gate` set to
  `environment-packager`; the orchestrator alone decides whether to build a
  completely fresh stage or stop the lane.
- Pipeline-code faults, source-snapshot drift, and unreconciled journal state
  are terminal. Preserve all diagnostics.

Never apply an unreviewed candidate or weaken a deterministic check. After any
fresh restage or successful corrective attempt, rerun disclosure verification,
independent verification, dialogue generation, and promotion checks.

## Completion

Pass only after `verify-applied` succeeds and tracker hashes match the live
instruction and `task.toml`. Set current confidence to `high`; any uncertainty
sets current `low` and blocks. Append either transition to confidence history.
Return the source/candidate/report paths, prompt version, model, attempt count,
semantic-review record, journal state, final hashes, and a structured
completion handoff to `harbor-orchestrator`.

Any completion handoff uses the same envelope with `status: passed`,
`next_owner: harbor-orchestrator`, and `suggested_fixer: none`.
