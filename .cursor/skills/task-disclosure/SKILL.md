---
name: task-disclosure
description: Build the unified pre-run disclosure for GDPval workbook rebuild tasks. Use when preparing or auditing task instructions, replacing the old custom-formula gate and workbook conventions manifest.
---

# Task Disclosure

This skill writes the one agent-facing disclosure section for a workbook rebuild task.
It replaces the custom-formula gate's post-run hint flow and the workbook conventions
manifest's separate writer.

## The three filters

1. **Registry matching.** Custom methods run first: role from the row label, then formula
   comparison against that role's catalogue variants. A confident out-of-catalogue match
   claims the band; standard or uncertain formulas pass to the convention detectors.
   When a label hits more than one method role, a single arbitration agent may pick one
   candidate before that chain runs. It does not write sentences.
2. **`Ship when`.** The matched registry entry decides whether this band may be disclosed here.
3. **Rendering and audit.** The same deterministic registry templates, writer, leak audit, and
   verifier handle convention and custom-method records.

The only model call is role arbitration on Filter 1 collisions. Custom prose stays a
deterministic English rendering of the parsed formula AST, including every reference,
literal, branch, and operation.

## Workflow

Run from `FCP2/`.

```bash
S=.cursor/skills/task-disclosure/scripts/disclose.py

python3 $S bench --tasks-root ../08_12_34_samples_tasks_outputs_hinted
python3 $S select --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S probe  --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S roles  --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S detect --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S context --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S write --task-dir ../08_12_34_samples_tasks_outputs_hinted/0529-outputs --out ../tmp-0529-unified
python3 $S verify --task-dir ../tmp-0529-unified
```

If `roles` reports a non-zero case count, launch the arbitration agent below and write
`runs/disclosure/<task>/role_resolutions.json` **before** `detect`. `detect` exits if
collisions exist and that file is missing or incomplete. Zero collisions need no file.

For a batch:

```bash
python3 $S migrate \
  --tasks-root ../08_12_34_samples_tasks_outputs_hinted \
  --out ../08_12_34_samples_tasks_outputs_unified
```

`migrate` runs `roles` then `detect`. It does not launch the agent. Supply a complete
`role_resolutions.json` per task first, or `detect` fails closed.

## Role arbitration agent

When `ambiguous_roles.json` has cases, launch **exactly one** Cursor `generalPurpose`
subagent with model `gpt-5.6-sol-high`. Give it only the cases file and the output path.
It must not read golden formulas, cached values, graded targets, or catalogue variants.

Use this prompt:

```text
You are arbitrating finance-row roles for a disclosure gate. Read the JSON file of
cases. Each case already lists the candidate method entry ids that regex matching
found, the row label, neighboring labels, which regex fired on the own label versus
a neighbor, and each candidate's registry Question.

For every case, pick exactly one candidate id from that case's `roles` list, or
`null`. Write JSON to the given output path:

{
  "agent_model": "gpt-5.6-sol-high",
  "resolutions": [
    {
      "case_id": "<copy from the case>",
      "label": "<copy from the case>",
      "chosen": "<one candidate id or null>",
      "reason": "<one short sentence>"
    }
  ]
}

Rules:
- Own label beats neighbors.
- A modifier such as "sales" inside "sales expenses" is not revenue.
- Generic labels (Other, Insurance, Total ...) abstain unless one candidate is the
  only reading a finance person would keep.
- Never invent a role that was not a candidate.
- Cover every case_id exactly once. Do not omit a case.
- Do not read workbooks, formulas, values, or graded cells.
```

Python only validates and applies the file. Invalid `chosen` values stay
`ambiguous_role`; they do not fail detect. A missing or incomplete file does.

## Artifacts

Each stage writes to `runs/disclosure/<task>/`:

- `bench.json`
- `bands.json`
- `probe.json`
- `ambiguous_roles.json`
- `role_resolutions.json`
- `records.json`
- `context.json`
- `verify.json`
- `migration-summary.json`

`records.json` carries `method_assessments` for every confidently roled band and
`custom_calibration` against the old `custom_logic` band labels. The old prose is never reused.

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
- Every custom sentence covers every parsed reference and literal.
- No custom and convention record claim the same cell.

Layer-3 faithfulness review is intentionally separate: launch a fresh reviewer that did not
write the disclosure and ask whether each bullet is true of the golden and safe for the agent.

## Taxonomy

Read [TAXONOMY.md](TAXONOMY.md) for record labels and [REGISTRY.md](REGISTRY.md) for every
permitted convention, method, `Ship when`, and sentence template.
