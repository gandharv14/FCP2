---
name: custom-formula-gate
description: Identifies key formula variables in curated-output lineage before packaging, compares them with a closed textbook finance catalog, and emits audited method-only hints for custom logic. Use during Harbor task creation after verified segmentation and before packaging.
---

# Pre-package Custom Formula Gate

This is a build-time gate. It does not require or inspect Harbor rollouts.

The analysis model is pinned: exactly one `generalPurpose` subagent using
`gpt-5.6-terra-high` must perform the semantic mapping and classification. If
this skill is loaded by another model, delegate the work once to that pinned
subagent and do not duplicate the classification in the parent.

The raw workbook is a build-time answer key. It may be read only by the gate and
must never enter the packaged task environment. Only validated method guidance
from the hints artifact may be shown to the task-solving agent.

## Inputs and artifacts

For workbook `$WB`, use:

```bash
FORMULA_RUN="runs/$WB-custom-formula-gate"
CONTEXT="$FORMULA_RUN/context.json"
REPORT="$FORMULA_RUN/report.json"
HINTS="$FORMULA_RUN/hints.json"

python3 .cursor/skills/custom-formula-gate/scripts/extract_gate_context.py \
  "$WB" --source "4-10 100" --seg-dir "seg_out/$WB" --output "$CONTEXT"
```

The extractor starts from every curated output and retains the complete reverse
closure of formula series as `key_variables`. It ranks them by output coverage,
dependency depth, and downstream fan-out, but ranking never filters a variable.
Each row includes formulas, cached period values, labels, neighboring rows,
direct formula references, and labeled direct lineage drivers.

Read `CATALOG.md` before classifying and treat it as closed for this run. Do not
add a variant merely because the golden workbook uses it.

## Required order of analysis

### 1. Identify the key variable and its drivers

Process `key_variables` in `key_rank` order. For each one:

1. Name the financial variable represented by the row.
2. Assign a finance role from the row label, neighboring labels, and direct
   drivers before considering formula shape.
3. Map labeled workbook drivers to textbook variable slots such as `revenue`,
   `rate`, `bop_balance`, `draw`, `life`, or `period`.
4. Use `unclassified` when the role or mapping is not supportable from supplied
   evidence. Do not infer a role merely because a formula multiplies a percent.

### 2. Match values against textbook formulas

Consider only catalog variants for the assigned role. For each plausible
variant:

1. Express its named slots using referenced rows or labeled assumptions.
2. Compute the variant over every period having usable golden cached values.
3. Compare computed `a` with expected `e` using
   `abs(a - e) <= max(1e-6, 1e-6 * abs(e))`.
4. Record periods tested/matched and maximum absolute/relative error.
5. Require every rate, life, timing convention, and threshold to be recoverable
   from a labeled assumption.

A one-period match is insufficient. Zero balances and inactive years often make
different methods coincide. If cached values are unavailable, accept only an
exact symbolic catalog match and record `exact_symbolic_match: true`; otherwise
use `unclassified`.

### 3. Assign one primary class

| Class | Rule |
| --- | --- |
| `standard` | Matches a catalog variant marked standard using labeled assumptions. |
| `standard_variant` | Matches a catalog variant marked variant using labeled assumptions. |
| `custom_logic` | Role is known, but no catalog variant matches, or an unlabeled predicate/branch changes the method. |
| `definitional` | The choice determines what a metric includes rather than how a finance method is calculated. |
| `structural` | Timing, sign, aggregation, lag, annualization, clamp, or sheet plumbing only. |
| `literal_embedded` | A recognizable method embeds a required rate, life, amount, or threshold instead of using a labeled assumption. |
| `unclassified` | Role, mapping, catalog applicability, or evidence is insufficient. |

Precedence is: insufficient evidence → definitional/structural → custom branch →
embedded literal → standard variant → standard. Keep secondary observations in
`signals`.

The verdict is:

- `REVIEW` when any key variable is `unclassified`; packaging must stop.
- `FLAG` when there is no review row and at least one `custom_logic` or
  `literal_embedded` row; validated hints are added and packaging continues.
- `PASS` otherwise; packaging continues without custom hints.

## Required Terra outputs

Write `report.json`:

```json
{
  "schema_version": "2.0",
  "task": "0256-outputs",
  "generator": {
    "model": "gpt-5.6-terra-high",
    "prompt_version": "custom-formula-gate-v2",
    "context_sha256": "<sha256 of context.json bytes>",
    "catalog_sha256": "<sha256 of CATALOG.md bytes>"
  },
  "verdict": "FLAG",
  "counts": {"custom_logic": 1, "structural": 4},
  "series": [
    {
      "key_rank": 1,
      "band": "CalcA!H26:Q26",
      "label": "Interests",
      "role": "interest_expense_income",
      "class": "custom_logic",
      "catalog_variant": null,
      "variable_mapping": {
        "bop_balance": "CalcA!H24:Q24",
        "draw": "CalcA!H25:Q25",
        "rate": "Assumptions!H8:Q8"
      },
      "agreement": {
        "periods_tested": 10,
        "periods_matched": 0,
        "exact_symbolic_match": false,
        "max_absolute_error": 12.3,
        "max_relative_error": 0.5
      },
      "signals": ["boolean_gate", "literal_embedded:0.5"],
      "reason": "An unlabeled predicate switches full-year and half-year interest."
    }
  ]
}
```

Include exactly one `series` row for every context `key_variables` row, preserving
`band` and `key_rank`. A standard match must name a catalog variant and provide
a non-empty `variable_mapping`.

Write `hints.json`:

```json
{
  "schema_version": "1.0",
  "task": "0256-outputs",
  "hints": [
    {
      "title": "Draw-year interest timing",
      "guidance": "Use full-year interest in an initial draw year and half-year treatment otherwise.",
      "bands": ["CalcA!H26:Q26"],
      "classes": ["custom_logic"]
    }
  ]
}
```

Hints cover every and only `custom_logic`/`literal_embedded` band exactly once.
They describe the method in words. They must not contain Excel formulas,
requested answer values, or copied golden expressions.

## Validate before packaging

```bash
python3 .cursor/skills/custom-formula-gate/scripts/validate_gate_outputs.py \
  "$CONTEXT" "$REPORT" "$HINTS"
```

The validator checks the Terra model pin, prompt/context/catalog hashes, complete
key-variable coverage, closed classes and catalog IDs, all-period agreement,
verdict/count consistency, hint coverage, and answer/formula leakage. Any
validation error or `REVIEW` verdict is a blocker.
