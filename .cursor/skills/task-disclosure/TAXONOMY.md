# Unified Disclosure Taxonomy

This taxonomy replaces the old split between `custom-formula-gate` and
`workbook-conventions-manifest`. The controlling question is always:

> Does a graded answer depend on this missing choice?

A band is considered only when the golden workbook computes it, a graded answer reads it,
and the delivered workbook leaves it blank.

## Dispositions

### `drop`

The band is in the formula graph but should not be disclosed. Typical reasons:

- It is not blank in the delivered workbook.
- It cannot move a graded answer.
- It is only ordinary arithmetic with no modelling choice.

### `recoverable`

Plausible alternatives were enumerated and they all produce the same graded answers within
tolerance. Nothing is written to the instruction.

This is a measured conclusion, not a synonym for `structural` or `definitional`.

### `convention`

The band encodes a choice from a closed family. The instruction may state the chosen family
value, never the formula or computed number.

Example: `discount_period = mid_year`.

### `method`

The band encodes a non-catalogue method that can be stated as a short rule over labelled
inputs visible in the delivered workbook. The instruction may state the rule in prose.

The rule must bottom out in visible inputs. If it requires a number that exists only inside
a deleted formula, it is not a method.

### `supplied`

The band is load-bearing, but the construction is not safely describable. The remedy is to
rebuild the delivered workbook with an upstream driver preserved as an input, not to write a
hint.

### `defect`

The golden itself is internally inconsistent or carries a target that no fair disclosure can
repair. Escalate; never disclose.

### `unclassified`

The band appears load-bearing but no current family or method rule can name it. This blocks
shipping unless explicitly accepted. Each `unclassified` case is taxonomy backlog.

## Convention Families

The current closed families are:

- `discount_period`: `mid_year`, `year_end`, `day_weighted`
- `inert_line`: `always_zero`, `charged_once`, `charged_every_period`
- `terminal_value`: `absent`, `perpetuity_growth`, `exit_multiple`, `other`
- `row_populated`: `unused`, `populated`
- `npv_timing`: `excel_default_one_period_out`, `t0_added_separately`
- `aggregate_scope`: member span or member row set for a total, never the graded target
- `projection_rule`: `hold_level`, `hold_growth`, `average_window`, `ratio_to_driver`
- `stake_scaling`: `applied`, `not_applied`
- `source_selection`: source cell or source sheet family

## Catalogue Methods

`CATALOG.md` remains useful, but no longer decides pass or fail. It supplies roles and
plausible alternatives for the residue after deterministic convention detection.

The catalogue answers:

> What else might a competent modeller have done here?

The evaluator answers:

> Would that alternative move a graded answer?

## Ownership Rule

Each selected band gets exactly one record. Deterministic convention detectors run first.
Only unclaimed bands are eligible for method classification. This prevents the old
`projection_rule` double-hinting failure.

