# Dictionary of logic conventions

The closed vocabulary a manifest entry may use. Derived from about 70 logic records mined
from the twenty gap reports under `08_12_34_samples_tasks_outputs_hinted/gap_reports/`.

A manifest entry may only take a value from one of these families. That constraint is the
main protection against handing over answers: a decision that fits a family is a choice
among known alternatives, while a decision that fits none is a bespoke construction, and
bespoke constructions cannot be disclosed safely (see *Out of vocabulary* at the end).

Each family below gives the question it answers, the allowed values, how a program reads it
off the original, and the tasks it was observed in.

---

## Implemented families

### `discount_period`
**Question.** Are cash flows discounted from the middle of each period or the end?
**Values.** `mid_year` | `year_end` | `day_weighted`
**Detection.** Find the row labelled "Discount Period". Mid-year shows as a fractional
cached value or a halving token in the formula, typically `=(J7-I7)/2`.
**Disclosure.** Safe. The convention does not imply the answer; the whole cash-flow chain
still has to be built.
**Seen in.** 0514, 0515, 0518, 0519, 0526, 0528, 0531 — the largest single family.
**Trap.** A bare search for a halving token also fires on sensitivity-grid axis steps
(`=J39-0.5%`) in 0517, 0520, 0524, 0527, 0529, 0530. Detection must be anchored on the row
label, not the token.

### `inert_line`
**Question.** Is a labelled waterfall line a conditional guard that never fires, or a real
charge?
**Values.** `always_zero` | `charged_once` | `charged_every_period`
**Detection.** The row is populated but every cached value is zero. Typically
`=-CFS!J27` pointing at a shortfall top-up that never triggers.
**Disclosure.** Safe.
**Seen in.** 0514, 0515, 0526, 0531. In every case the row label ("Less: Minimum Cash
Balance") plus a surviving input of the same name invited the opposite reading.

### `terminal_value`
**Question.** Does the model book a terminal or exit value, and on what basis?
**Values.** `absent` | `perpetuity_growth` | `exit_multiple`
**Detection.** Locate rows labelled "Terminal Value" or "Exit Value". No formula anywhere on
the row means `absent`. A growth-over-spread shape means `perpetuity_growth`; a reference to
a multiple means `exit_multiple`.
**Disclosure.** Safe.
**Seen in.** 0517 and 0529 (`absent`), 0522 and 0524 (populated), 0514, 0515, 0518, 0519,
0526, 0528, 0531 (`perpetuity_growth`), 0523 and 0520 (absent by omission from the flow row).

### `row_populated`
**Question.** Is a labelled row used at all in the original?
**Values.** `unused` | `populated`
**Detection.** The row carries a label, has no formula and no value anywhere, and sits
inside the block that feeds a graded answer.
**Disclosure.** Safe, and high value: an emptied row and a never-populated row are
indistinguishable in the delivered file because the emptying pass drops `xl/calcChain.xml`.
**Seen in.** 0462, 0517, 0520, 0522, 0525, 0527, 0529.
**Restriction.** Only reported on sheets carrying a graded cell. Elsewhere an empty labelled
row is nearly always a section header or a sub-heading in an input list.

### `npv_timing`
**Question.** Does the first cash flow sit at time zero, or one period out?
**Values.** `excel_default_one_period_out` | `t0_added_separately`
**Detection.** Read the `NPV(` call. A trailing `+ B20` outside the closing bracket means the
period-zero flow is added separately.
**Disclosure.** Safe.
**Seen in.** 0523, 0530.

### `aggregate_scope`
**Question.** Which columns and rows does a graded total span?
**Values.** The literal range, e.g. `I23:S23`.
**Detection.** The graded cell's own `SUM` argument.
**Disclosure.** **Flag for review.** This names the graded cell itself, so the audit reports
it as a potential leak. Disclose the span only where the row being summed still has to be
built; suppress it where the summed row survives in the delivered file.
**Seen in.** 0520, 0522, 0527.

### `projection_rule`
**Question.** How is a forecast row carried forward?
**Values.** `hold_level` | `hold_growth` | `average_window` | `ratio_to_driver`
**Detection.** Classify each cell on the row: a bare reference to the previous column is
`hold_level`; a `*(1+x)` factor is `hold_growth`; an `AVERAGE(` is `average_window`; a
product against a driver is `ratio_to_driver`. Report the rule holding for the majority of
the row.
**Disclosure.** Safe for the rule. The *window width* of an `average_window` and any ramp
step are constants and are out of scope.
**Seen in.** 0462, 0463, 0468, 0517, 0520, 0523, 0525, 0530.

### `stake_scaling`
**Question.** Is a deal line multiplied by the buyer's ownership share?
**Values.** `applied` | `not_applied`
**Detection.** The formula references a cell whose row label matches equity investment,
ownership, stake or percent acquired.
**Disclosure.** Safe.
**Seen in.** 0522, 0523, 0524, 0529, 0530. In 0529 the agent applied the share on two rows
of the same page and omitted it on the third.

### `source_selection`
**Question.** Which of several valuations does the purchase price read from?
**Values.** The source cell, e.g. `Multiples!D25`.
**Detection.** A row labelled initial investment, purchase price, entry or consideration
whose formula reads from another sheet.
**Disclosure.** **Flag for review.** Where the source cell is rebuildable from surviving
inputs this is a method; where it is one kept literal away from the answer it is a leak.
**Seen in.** 0517, 0520, 0523, 0524, 0527, 0529, 0530 — the second largest family.

---

## Families identified but not yet implemented

Present in the mined records, worth adding as detectors. Listed with the observed values so
the vocabulary stays stable when they are built.

- `first_period_carries_cash` — `yes` | `no`. Whether the purchase year also collects a full
  year of cash flow. 0529, 0520.
- `period0_equity_cheque` — `capex_sized` | `cash_float` | `plug`, with a separate
  `year1_capex_netted` boolean. 0518, 0526, 0531.
- `exit_proceeds_basis` — `stake_x_metric_x_multiple` | `after_net_debt_bridge`. 0522, 0524.
- `earnout_years` — the periods that pay. 0522, 0523.
- `cash_flow_sign` — `outflow_negative` | `inflow_positive`. 0530.
- `fy_column_semantics` — `trailing_actual_window` | `forward_forecast`. 0520, 0521, 0523, 0524.
- `working_capital_basis` — driver row, denominator year, member rows. 0462, 0468, 0518,
  0524, 0530.
- `fee_charge_base` — `enterprise_value` | `equity` | `proceeds`; and `year_end` | `average`
  for the balance it is struck on. 0463, 0468.
- `tax_threshold_scope` — which periods a threshold is tested on, and at what frequency.
  0515, 0518, 0525, 0530.
- `summary_statistic` — `mean` | `median`. 0468.
- `scenario_switch` — the active branch of a labelled scenario toggle. 0526.

---

## Out of vocabulary

Two kinds of decision cannot be expressed here, and both are handled elsewhere.

**Bespoke constructions.** Where the author did not choose from a menu but built something —
0463's 2025 profit column, rebuilt line by line from monthly accounts across 51 deleted
cells, with four plausible labelled alternatives sitting on a neighbouring sheet. There is no
enum for this. The remedy is to preserve the driver cell as a visible input, never to
describe it. Preserve upstream drivers only; never a graded target.

**Baked-in constants.** A number that lived only inside a deleted formula — a `/1000` on a
tax threshold, a half-step ramp, a 20% uplift. Deliberately out of scope for now. Keep the
rule, drop the constant.
