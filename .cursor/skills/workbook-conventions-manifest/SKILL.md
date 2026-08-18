---
name: workbook-conventions-manifest
description: Build a conventions manifest for a GDPval spreadsheet-rebuild task by reading the modelling decisions off the original workbook, so the agent can reproduce the original's choices and the grader can score fairly. Use when preparing or repairing rebuild tasks, when a task is scoring badly for reasons the delivered file cannot disclose, or when the user mentions conventions, manifests, formula hints, unwinnable cells or task fairness.
disable-model-invocation: true
---

# Workbook conventions manifest

## The problem this solves

These tasks take a real financial model (**the original**, called "the golden" in the
pipeline), delete every calculated cell, and ask an agent to rebuild it. Deleting a cell
removes its formula as well as its value. The number is usually recoverable from the
surviving inputs; **the decision the formula encoded is not.**

Across the twenty analysed tasks, 134 of 302 graded cells were unreachable for this reason.
The worst example recurs in seven workbooks: a row labelled "Discount Period" holding
`=(J7-I7)/2`, which discounts from mid-year. The label survives, the formula does not, and
mid-year and year-end are both standard. Every agent chose year-end and was marked wrong.

This skill reads those decisions off the original and publishes them with the task.

## When to use

- Preparing a rebuild task, before it ships.
- Diagnosing a task where agents miss cells for reasons the delivered file cannot show.
- Auditing an existing task's disclosure for completeness or answer leakage.

## What already exists, and why it is not enough

The pipeline has a hint mechanism today: a review step compares each formula against a fixed
catalogue at `FCP2/.cursor/skills/custom-formula-gate/CATALOG.md`, flags anything unusual,
and `FCP2/xl_formula_hint_tasks.py` renders prose hints into the instruction. Every task
carries them.

It misses the failures for three structural reasons, and this skill inverts all three.

- Coverage is decided by "is this formula unusual", not "does a graded answer depend on it".
  **Here, selection works backwards from the graded cells.**
- The catalogue classes composition choices as definitional and sign, scale, lag and `SUM`
  scope as structural, so neither is ever hinted — and those are where the failures live.
  **Here, the dictionary covers them.**
- Hints are prose, and the code forbids `=`, forcing paraphrase. In 0523 "averaged forecast
  EBITDA" hid the fact that the window is three *historical* years and every agent lost the
  entry price. **Here, entries are structured values from a closed vocabulary.**

## Workflow

```
- [ ] 1. Closure    which deleted cells each graded answer depends on
- [ ] 2. Defects    is the original broken in a way no disclosure fixes
- [ ] 3. Detect     read the decisions off the original
- [ ] 4. Audit      completeness and answer leakage
- [ ] 5. Emit       write the manifest and the instruction section
- [ ] 6. Review     resolve every flagged record by hand before shipping
```

All commands take `--task-dir`. The original is found automatically under `FCP Workbooks/`;
pass `--golden` if it lives elsewhere. Run from the repo root.

```bash
S=.cursor/skills/workbook-conventions-manifest/scripts/conventions.py
python3 $S closure  --task-dir 08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S defects  --task-dir 08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S detect   --task-dir 08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S audit    --task-dir 08_12_34_samples_tasks_outputs_hinted/0529-outputs
python3 $S emit     --task-dir 08_12_34_samples_tasks_outputs_hinted/0529-outputs
```

### 1. Closure

Walks back from every graded cell through the original's formula graph and reports how many
cells in that chain the delivered file no longer carries. This is the denominator for
everything else: those are the cells whose logic went missing.

### 2. Defects

Run this **before** building a manifest. Some answers are wrong in the original itself and
the answer key inherits the error; no disclosure can fix those, because disclosing them means
instructing the agent to reproduce a bug. Two signatures are implemented:

- `label_sign_mismatch` — a row labelled `(1-T)` whose formula multiplies by `(1 + rate)`
  with a **positive** rate. The sign check matters: where the rate cell is negative,
  `(1 + rate)` is correct and there is no defect.
- `empty_operand_zero_target` — a graded target that evaluates to zero only because an
  operand was never filled in by the author. This one has two readings and you must decide
  which applies. In 0520 and 0527 the author never entered a valuation multiple, so the
  graded figure is zero while the instruction promises a real valuation — unwinnable, since
  no agent will answer zero. In 0462 five growth-rate targets are zero because the prior-year
  column is empty and an `IFERROR` catches the division; zero is the natural answer there, so
  those are free marks rather than lost ones. Read the formula and the label before ruling.

A task with an unwinnable target needs the source model repaired or the target dropped. Do
not paper over it with a convention.

### 3. Detect

Runs the detectors in [DICTIONARY.md](DICTIONARY.md) over the closure. Each record carries
the family, the value, the cells it governs, the golden formula that evidences it, the
alternatives it rules out, and a note naming the row label.

Read the records against the workbook before trusting them. A detector asserts only what the
formula or the structure shows, but a row label can be misleading.

### 4. Audit

Two checks.

**Completeness.** How many deleted cells in the closure no record explains. Low coverage is
not automatically a failure — a great many deleted cells are ordinary arithmetic that needs
no disclosure — but a load-bearing decision hiding among the unexplained is exactly the
failure mode this is meant to catch. Read the unexplained sample.

**Answer leakage.** Any record naming a graded cell directly is reported. Resolve every one:
either suppress the record, or keep the driver cell as a visible input, or drop the target
from grading.

### 5. Emit and apply

There are **two artifacts and they are not interchangeable**.

`tests/conventions.json` is the reviewer's copy, written by `emit`. It carries the `evidence`
field holding the golden formula, plus the audit block. **It must never be shown to the
agent** — it lives in `tests/` beside `answer_key.json`, which is grader-side and not part of
the agent's working directory.

The agent-facing disclosure is a section inside `instruction.md`, written by `apply`. It
carries family, cells, value and the row label, and nothing else. `apply` strips `evidence`,
withholds every record the audit flagged as naming a graded cell, and refuses to write if any
line still looks like a formula. Use `--force` only after resolving the flags by hand.

```bash
python3 $S emit  --task-dir <task>                 # reviewer copy
python3 $S apply --task-dir <task> --dry-run       # preview what the agent sees
python3 $S apply --task-dir <task> --out <newdir>  # clone, then disclose
```

`apply` is idempotent: re-running replaces the section it wrote before. Prefer `--out` to
clone the bundle rather than mutating a task whose runs have already been scored, which is
the same convention `xl_formula_hint_tasks.py` follows.

### 6. Review

The manifest is a draft until a human resolves the flagged records. Two rules:

**Disclose the choice, never the construction.** If a decision fits a dictionary family it is
a choice among known alternatives and is safe to state. If it fits none, the author built
something bespoke — describe it and you are writing the answer key. Preserve the driver cell
as a visible input instead.

**Preserve upstream drivers, never graded targets.** Handing over 0463's 2025 profit anchor
is safe because the anchor is not graded and the whole deal still has to be built from it.
Handing over a graded cell is not.

## Wording rules for the few prose cases

Drawn from the hints that cost marks:

- Name the function when the choice is a function. 0525's "each counted staffed position"
  was ambiguous between `COUNT()` over seven rows and the three actually paid.
- State period bands explicitly and match them to the formula. 0528's instruction said
  "(2027 - 2036)" where the original averages 2026 to 2030.
- Say which sheet a rule applies to. 0530's tax hint read a threshold that applies only to
  the audited column into every forecast year.
- Give sign and direction for deal cash flows. 0530's earnout is money paid out; all five
  attempts treated it as received.

## Reference

- [DICTIONARY.md](DICTIONARY.md) — the closed vocabulary, its detection rules, and the
  families still to be implemented.
- `scripts/conventions.py` — execute it, do not read it. Requires `openpyxl`.
