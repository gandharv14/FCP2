---
name: task-disclosure
description: Build the unified pre-run disclosure for GDPval workbook rebuild tasks. Use when preparing or auditing task instructions, replacing the old custom-formula gate and workbook conventions manifest.
---

# Task Disclosure

This skill writes the one agent-facing disclosure section for a workbook rebuild task.
It replaces the custom-formula gate's post-run hint flow and the workbook conventions
manifest's separate writer.

## Rule

Only disclose a decision when all three are true:

1. A graded answer reads the cell in the golden workbook.
2. The delivered workbook leaves that cell blank.
3. A plausible alternative would move a graded answer, or the current evaluator cannot prove
   otherwise.

## Workflow

Run from `FCP2/`.

```bash
S=.cursor/skills/task-disclosure/scripts/disclose.py

python3 $S bench --tasks-root ../08_12_34_samples_tasks_outputs_hinted
python3 $S select --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S probe  --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S detect --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S context --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S write --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs --out ../tmp-0529-unified
python3 $S verify --task-dir ../tmp-0529-unified
```

For a batch:

```bash
python3 $S migrate \
  --tasks-root ../08_12_34_samples_tasks_outputs_hinted \
  --out ../08_12_34_samples_tasks_outputs_unified
```

## Artifacts

Each stage writes to `runs/disclosure/<task>/`:

- `bench.json`
- `bands.json`
- `probe.json`
- `records.json`
- `context.json`
- `verify.json`
- `migration-summary.json`

The writer creates two task artifacts:

- `tests/disclosure.json`: reviewer copy with evidence and audit details.
- `instruction.md`: agent-facing text without formulas or answer values.

## Verification

`verify` is blocking for mechanical invariants:

- Every selected cell is blank in the delivered workbook.
- No selected band appears twice.
- No agent-facing line contains a formula-shaped expression.
- No numeric literal in the disclosure matches a graded target within tolerance.
- No record names a graded target unless it is explicitly marked as accepted.

Layer-3 faithfulness review is intentionally separate: launch a fresh reviewer that did not
write the disclosure and ask whether each bullet is true of the golden and safe for the agent.

## Taxonomy

Read [TAXONOMY.md](TAXONOMY.md) before changing the detectors or writer.

