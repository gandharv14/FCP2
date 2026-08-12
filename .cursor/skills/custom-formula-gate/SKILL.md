---
name: custom-formula-gate
description: Classifies golden workbook formula series against a closed finance-method catalog after Harbor model rollouts, and flags custom logic, embedded literals, definitional choices, and structural plumbing. Use when reviewing spreadsheet reconstruction tasks after rollouts or when asked to identify non-standard formulas in raw financial workbooks.
---

# Custom Formula Gate

Run this gate only after at least one Harbor rollout has completed for the task. The
raw workbook is a post-rollout answer key; never expose it to the model that performs
the task.

## Inputs

- Harbor task bundle, for example `tasks_outputs/0256-outputs`
- Harbor job directory containing completed attempts, for example
  `jobs/new10-pass5`
- Raw workbook, normally `4-10 100/<workbook>.xlsx`
- Segmentation artifacts, normally `seg_out/<workbook>/`

## Prepare the evidence

From the repository root:

```bash
python3 .cursor/skills/custom-formula-gate/scripts/extract_gate_context.py \
  tasks_outputs/0256-outputs \
  --job-dir jobs/new10-pass5 \
  --output runs/custom-formula-gate/0256-outputs-context.json
```

The extractor fails closed if it cannot find a completed matching rollout. It takes
the union of formula bands in the curated outputs' lineage, then records the golden
formula, normalized series pattern, cached values, labels, neighboring rows, direct
references, downstream outputs, and custom-logic signals.

Read [CATALOG.md](CATALOG.md) before classifying. Treat it as closed for the current
review. Do not add a new variant merely because the golden workbook uses it.

## Classification workflow

### 1. Assign the finance role

Assign a role from the row label and neighboring rows first. Use roll-forward
neighbors such as BOP/EOP, CAPEX, debt draw, repayment, EBT, and tax rate. Formula
shape and sheet name may break ties but must not override clear labels.

Record `unclassified` when the role is not confident. Do not infer a role solely
because a formula happens to multiply by a percentage.

### 2. Select only catalog variants for that role

Compare the series with the variants in `CATALOG.md`. A formula is not standard
because it looks financially plausible. It must match an enumerated variant.

Separate method from presentation:

- sign flips, lags, annualization, zero clamps, and aggregation are structural;
- metric composition such as what Net Profit or FCF includes is definitional;
- sheet-to-sheet references and copied subtotals are plumbing, not methods.

### 3. Test agreement and recoverability

For every plausible catalog variant:

1. Express the variant using referenced rows and labeled assumptions only.
2. Compute it over every period with usable golden cached values.
3. Compare each computed value `a` with golden value `e` using:

   `abs(a - e) <= max(1e-6, 1e-6 * abs(e))`

4. Record matched periods, mismatches, and maximum absolute and relative error.
5. List every parameter and the labeled assumption that supplies it.

Agreement in one period is insufficient. Zero balances and no-draw years often make
different methods coincide. A match is recoverable only when all rates, useful
lives, timing conventions, and thresholds come from labeled assumptions.

If cached values are unavailable, accept an exact symbolic catalog match. Otherwise
return `unclassified`; do not claim numeric agreement that was not tested.

### 4. Assign exactly one primary class

Use these classes:

| Class | Rule |
| --- | --- |
| `standard` | Matches a catalog variant marked standard using only labeled assumptions. |
| `standard_variant` | Matches a catalog variant marked variant using only labeled assumptions. |
| `custom_logic` | Role is confident, but no catalog variant matches, or a match requires an extra predicate/branch not represented by an assumption. |
| `definitional` | The choice is what a metric includes rather than how a finance method is calculated. |
| `structural` | The difference is timing, sign, aggregation, lag, annualization, clamp, or sheet plumbing rather than a domain method. |
| `literal_embedded` | An otherwise recognizable method embeds a rate, life, amount, or threshold in the formula instead of a labeled assumption. |
| `unclassified` | No confident role, no applicable catalog, or insufficient evidence. |

Use this precedence when more than one description applies:

1. `unclassified` if the role or evidence is insufficient
2. `definitional` or `structural` when there is no distinct domain method to judge
3. `custom_logic` when out-of-catalog predicates or branches alter the method
4. `literal_embedded` when the embedded value is the only reason it is not recoverable
5. `standard_variant`
6. `standard`

Keep secondary evidence in `signals`; do not turn every signal into another class.
For example, a custom `IF` containing `0.5` is `custom_logic` with a
`literal_embedded` signal, not two primary classes.

### 5. Apply the gate

- `FLAG`: at least one `custom_logic` or `literal_embedded` series is in a curated
  output's lineage.
- `REVIEW`: no flagged series, but at least one relevant series is `unclassified`.
- `PASS`: all relevant domain-method series are `standard` or `standard_variant`;
  `definitional` and `structural` rows are documented but do not fail the gate.

Never use rollout failure by itself as evidence of custom logic. Rollout results
control when the post-hoc gate may run and can prioritize investigation, but the
classification must come from golden formula/catalog agreement.

## Required report

Write both:

- `runs/custom-formula-gate/<task>-report.json`
- `runs/custom-formula-gate/<task>-report.md`

The JSON must contain:

```json
{
  "task": "0256-outputs",
  "rollouts_observed": 5,
  "verdict": "FLAG",
  "counts": {"custom_logic": 1},
  "series": [
    {
      "band": "CalcA!H26:Q26",
      "label": "Interests",
      "role": "interest_expense_income",
      "class": "custom_logic",
      "catalog_variant": null,
      "golden_formula": "=IF(...)",
      "assumptions": [],
      "agreement": {"periods_tested": 10, "periods_matched": 0},
      "signals": ["boolean_gate", "asymmetric_if", "literal_embedded:0.5"],
      "downstream_outputs": ["CalcA!C62", "CalcA!C64"],
      "reason": "The draw-year predicate changes full-year versus half-year interest without a labeled convention."
    }
  ]
}
```

The Markdown report should lead with the verdict, then list flagged and review rows,
catalog matches, and rollout evidence. Include formulas and cell references so every
decision is auditable.

## Calibration example

`CalcA!H26:Q26` in workbook `0256` is labeled `Interests`; its neighbors are
`Debt - bop`, `Debt - drawdown`, and an interest-rate row. Its role is therefore
`interest_expense_income`, not depreciation.

The formula applies full-year interest when opening debt is zero and drawdown is
positive, but half-year interest otherwise:

```text
=IF(AND(H24=0,H25>0),(H24+H25)*rate,(H24+H25)*0.5*rate)
```

That is `custom_logic`: the asymmetric predicate is not a catalog assumption.
`0.5` is also a supporting `literal_embedded` signal unless a labeled half-year
convention exists. The actual depreciation series nearby is `CalcA!H40:Q40`, labeled
`Depreciation` between Asset BOP, CAPEX, and Asset EOP.
