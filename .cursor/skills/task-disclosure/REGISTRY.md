# Disclosure Registry

The single authority for what may reach the agent. A decision ships only when an entry here
covers it and that entry's `Ship when` permits disclosure for the specific band in hand.
Nothing else ships, and nothing may be hinted that is not written down here.

Two sections, one schema. Convention families come from the old `DICTIONARY.md`, finance
methods from the old `CATALOG.md`. They are merged because the decision tree asks a single
question - is this covered - and a single question needs a single list.

## Entry schema

Every entry, in either section, carries the same seven fields.

- `Id` - the stable name a record cites. A record without one cannot ship.
- `Question` - the decision the entry settles.
- `Alternatives` - the enumerated values. These are also what the divergence test swaps in.
- `Ship when` - the condition under which this entry may reach the agent. `always` means the
  entry has no suppressing condition.
- `Sentence` - the agent-facing wording, one per value. Placeholders are filled from the
  workbook. A sentence may never contain an entry id, a value token, a formula, or a graded
  answer.
- `Detection` - how a program reads the entry off the golden.
- `Seen in` - the tasks that motivated it.

---

# Section 1: Convention families

Records from this section ship a value. Their record metadata is `convention`.

## `discount_period`

- **Id.** `discount_period`
- **Question.** Are cash flows discounted from the middle of each period or the end?
- **Alternatives.** `mid_year` | `year_end` | `day_weighted`
- **Ship when.** `always`. The row label survives the emptying pass and the formula does not,
  mid-year and year-end are both ordinary practice, and nothing in the delivered file
  disambiguates them. There is no condition under which the agent could work this out.
- **Sentence.**
  - `mid_year` - "Cash flows on the row labelled {label} are discounted from the middle of each
    period rather than the end."
  - `year_end` - "Cash flows on the row labelled {label} are discounted from the end of each
    period."
  - `day_weighted` - "Cash flows on the row labelled {label} are discounted on a day-weighted
    basis."
- **Detection.** Find the row labelled "Discount Period" or "Discount Factor Period". Mid-year
  shows as a fractional cached value or a halving token in the formula.
- **Seen in.** 0514, 0515, 0518, 0519, 0525, 0526, 0528, 0531 - the largest single family.
- **Trap.** A bare search for a halving token also fires on sensitivity-grid axis steps in
  0517, 0520, 0524, 0527, 0529, 0530. Detection must anchor on the row label, not the token.

## `inert_line`

- **Id.** `inert_line`
- **Question.** Is a labelled waterfall line a guard that never fires, or a real charge?
- **Alternatives.** `always_zero` | `charged_once` | `charged_every_period`
- **Ship when.** `always`. In every observed case the row label plus a surviving input of the
  same name invited the opposite reading, so the delivered file actively misleads.
- **Implemented scope.** The detector fires only on labels containing "minimum cash" or
  "less: minimum", so `charged_once` and `charged_every_period` are currently unreachable.
  Widening the label match is backlog; the values are listed so the vocabulary stays stable.
- **Sentence.**
  - `always_zero` - "The row labelled {label} evaluates to zero in every period; the charge it
    describes never applies in this model."
  - `charged_once` - "The row labelled {label} is charged in one period only."
  - `charged_every_period` - "The row labelled {label} is charged in every period."
- **Detection.** The row is populated but every cached value is zero.
- **Seen in.** 0514, 0515, 0526, 0531.

## `terminal_value`

- **Id.** `terminal_value`
- **Question.** Does the model book a terminal or exit value, and on what basis?
- **Alternatives.** `absent` | `perpetuity_growth` | `exit_multiple` | `other`
- **Ship when.** `always`. Whether a valuation books a terminal value is a modelling choice
  with no tell in an emptied file.
- **Implemented scope.** The detector scans cells the emptying pass blanked, so a row that was
  never populated is never reached and `absent` is currently unreachable. Those rows surface
  through `row_populated` instead - 0517 and 0529 both disclose an unused Exit Value row that
  way. Reaching `absent` here needs the detector to scan labelled rows rather than blanked
  cells, which is backlog.
- **Sentence.**
  - `absent` - "This model books no terminal or exit value. The row labelled {label} is never
    populated."
  - `perpetuity_growth` - "The row labelled {label} books a terminal value on a perpetuity
    growth basis."
  - `exit_multiple` - "The row labelled {label} books an exit value using a multiple of a
    terminal metric."
  - `other` - withheld. `other` means the detector could not name the basis, so there is no
    sentence to write. Treat as uncovered.
- **Detection.** Rows labelled "Terminal Value" or "Exit Value". No formula anywhere on the row
  means absent. A growth-over-spread shape means perpetuity growth; a reference to a multiple
  means exit multiple.
- **Seen in.** 0517 and 0529 absent; 0522 and 0524 exit multiple; 0514, 0515, 0518, 0519, 0526,
  0528, 0531 perpetuity growth.

## `row_populated`

- **Id.** `row_populated`
- **Question.** Is a labelled row used at all in the original?
- **Alternatives.** `unused` | `populated`
- **Ship when.** `always`, because the detector only emits a record once every condition below
  already holds. `populated` is never emitted; it tells the agent nothing it cannot see.
- **Detection conditions.** All of these, applied when the record is built:
  - The row is empty in the golden and shows nothing beyond its label in the delivered file.
    An emptied row and a never-populated row are otherwise indistinguishable, because the
    emptying pass drops `xl/calcChain.xml`.
  - Some `SUM` range spans the row, so its emptiness changes a total. Elsewhere an empty
    labelled row is a block header and saying it is unused is noise.
  - Rows immediately above and below both carry data. A header has its block below it and
    nothing above, which the `SUM` test alone does not separate.
  - The label is at least four characters, is not all capitals, and does not read as a section
    heading.
  - The row sits on a sheet carrying a graded cell, with closure rows within two above and
    below.
  - No disclosed total names the same label as a member. Saying a row is unused while another
    bullet counts it into a sum tells the agent to build and not build the same row. Resolved
    after dispositions are set. This guard is now belt-and-braces: `aggregate_scope` requires
    every member to carry data and this entry requires the row to be empty, so the two can no
    longer name the same row.
- **Sentence.**
  - `unused` - "The row labelled {label} is not used in this model. Leave it empty."
- **Detection.** The row carries a label, has no formula and no value anywhere, and sits inside
  the block that feeds a graded answer.
- **Seen in.** 0462, 0517, 0520, 0522, 0525, 0527, 0529.
- **Restriction.** Only reported on sheets carrying a graded cell. Elsewhere an empty labelled
  row is nearly always a section header.

## `npv_timing`

- **Id.** `npv_timing`
- **Question.** Does the first cash flow sit at time zero, or one period out?
- **Alternatives.** `excel_default_one_period_out` | `t0_added_separately`
- **Ship when.** The band is not itself a graded cell. Where the `NPV(` call *is* the graded
  answer, stating its shape describes the target directly; preserve the driver instead.
- **Sentence.**
  - `excel_default_one_period_out` - "The present value on the row labelled {label} treats its
    first cash flow as falling one full period out."
  - `t0_added_separately` - "The present value on the row labelled {label} adds the period-zero
    cash flow separately, outside the discounted series."
- **Detection.** Read the `NPV(` call. A trailing addition outside the closing bracket means
  the period-zero flow is added separately.
- **Seen in.** 0523, 0530.

## `aggregate_scope`

- **Id.** `aggregate_scope`
- **Question.** Which rows are members of a total?
- **Alternatives.** the member row set.
- **Ship when.** All of the following.
  - The total is a bare `SUM` over a single span. A formula that merely starts with `SUM` -
    `=SUM(a-b/c*d)`, or a ratio of two sums - performs an operation the sentence would
    misdescribe as addition.
  - Every spanned row can be named, and there are at most six distinct names. An unnameable
    row means the member list the agent reads is not the set the workbook sums.
  - Every spanned row carries data somewhere in the golden. A row that is empty in every
    column is not a member worth naming: listing it tells the agent to build something that
    must stay blank, and these totals are frequently graded cells.
  - At least one member still has to be built.
  - The record's own cells are not graded.

  The sentence names the total and its members by their **visible labels**, never by the graded
  cell's reference: naming a label the agent can already read is not a leak, naming the target
  cell is. The record's cells carry only the members still to be built, so a surviving member
  is described but never attached.
- **Detection note.** Only graded cells are examined as candidate totals.
- **Sentence.**
  - member set - "The total labelled {label} is the sum of {members}."
- **Detection.** The total's `SUM` argument, resolved to the labels of the rows it spans.
- **Seen in.** 0520, 0522, 0527.

## `projection_rule`

- **Id.** `projection_rule`
- **Question.** How is a forecast row carried forward?
- **Alternatives.** `hold_level` | `hold_growth` | `average_window` | `ratio_to_driver`
- **Ship when.** The ingredient the rule needs is **not** available to the agent. Specifically:
  - **declines** when every ingredient row is either visible in the delivered file or sits
    within fifteen rows on the same sheet. The agent can see the structure, so choosing the
    rule is ordinary spreadsheet reasoning and getting it wrong is an agent mistake. The
    proximity test looks at position only; it does not require the nearby row to be labelled.
  - for `average_window`, **declines** unless the formula is a bare `AVERAGE` over one range.
    Anything wrapped around it - a multiplier, an offset - makes "is the average of" false.
  - **declines** unless the run covers every cell on the row the agent has to build. The
    sentences say "in each period" and "across the forecast"; a row that switches rule partway
    would otherwise get two bullets, each claiming the whole horizon. Coverage is measured
    against cells blank in the delivered file, not against the graded closure, because a period
    outside the closure still has to be built.
  - **declines** when the evidence formula is empty, since nothing can be said about it.
  - **declines** when the formula carries a branch - `IF`, `IFS`, `CHOOSE`, or a lookup. That
    is not a projection rule at all; the shape classifier fires on the multiplication inside
    the branch and mislabels genuinely custom logic.
  - **declines** when the ingredient rows carry no label, when there are more than two of
    them, or when one merely repeats the target's own label. In each case the sentence would
    be mush, and mush is worse than silence.
  - **ships** when the ingredient is absent, unlabelled at reach, or on another sheet with no
    pointer.
  - for `hold_level`, which names no ingredient, **ships** only when no value on the row is
    visible to the agent, so nothing indicates the level should stay constant.
- **Known gap.** A window that is structurally reachable but semantically non-obvious is not
  detected - 0523 averages historical years while the surrounding text implies forecasts. The
  reachability test cannot see that, so 0523's records currently decline. Detecting it needs a
  notion of which columns are historical, which is backlog.
- **Sentence.**
  - `hold_level` - "The row labelled {label} is held flat across the forecast at its last
    known level."
  - `hold_growth` - "The row labelled {label} grows off its prior period using the rate in
    {ingredient}."
  - `average_window` - "The row labelled {label} is the average of {ingredient}."
  - `ratio_to_driver` - "The row labelled {label} is worked out in each period from
    {ingredient}."
- **Detection.** Classify each cell on the row, then split the row into contiguous runs of one
  kind. A bare reference to the previous column is hold level; a `*(1+x)` factor is hold
  growth; an `AVERAGE(` is average window; a product against a driver is ratio to driver.
  Report each run separately - a row may change rule partway across.
- **Seen in.** 0462, 0463, 0468, 0517, 0520, 0523, 0525, 0530.
- **Note.** This entry was the source of the over-disclosure that motivated the registry. With
  no `Ship when` it produced 677 of 751 agent-facing records.

## `stake_scaling`

- **Id.** `stake_scaling`
- **Question.** Is a deal line multiplied by the buyer's ownership share?
- **Alternatives.** `applied` | `not_applied`
- **Ship when.** `always`. Observed agents applied the share inconsistently across rows of the
  same page, and nothing in the delivered file says which lines carry it.
- **Implemented scope.** Only `applied` is emitted; the detector recognises the multiplication,
  not its absence, so `not_applied` is unreachable. Note also that a row may scale by the share
  and then add an unscaled term, which the sentence does not capture.
- **Sentence.**
  - `applied` - "The row labelled {label} is scaled by the buyer's ownership share in
    {ingredient}."
  - `not_applied` - "The row labelled {label} is stated in full, not scaled by the buyer's
    ownership share."
- **Detection.** The formula references a cell whose row label matches equity investment,
  ownership, stake, or percent acquired.
- **Seen in.** 0522, 0523, 0524, 0529, 0530.

## `source_selection`

- **Id.** `source_selection`
- **Question.** Which of several valuations does the purchase price read from?
- **Alternatives.** the source row, named by its label and sheet.
- **Ship when.** The source is itself rebuildable from surviving inputs. **Declines** when the
  source cell is one kept literal away from the answer, which makes naming it equivalent to
  handing the answer over.
- **Sentence.**
  - source - "The line labelled {label} takes its value from {ingredient}."
- **Detection.** A row whose label contains initial investment, purchase price, entry,
  consideration, or `cv`, and whose formula reads from another sheet.
- **Seen in.** 0517, 0520, 0523, 0524, 0527, 0529, 0530.

---

# Section 2: Finance methods

The deterministic custom-method detector runs before convention detection. It assigns one role
from the row label, matches the formula against this section's catalogued variants, and claims
the band only on a confident no-match. Standard, structural, ambiguous, and unsupported cases
stay reviewer-visible but produce no custom hint. The reverse drift check requires every entry
in this section to have a role mapping, signature path, shared `Ship when`, and
`out_of_catalogue` sentence.

Records from this section ship a rule sentence. Their record metadata is `method`. The
alternatives are the catalogued variants for the role; only `out_of_catalogue` reaches the
writer.

Match economic meaning, not exact Excel syntax. The same parsed formula profile supplies both
signature matching and sentence wording; no model or separate paraphraser is involved.

Shared `Ship when` for this section: neither the band nor any referenced ingredient is graded,
and the deterministic sentence covers every parsed reference and literal. Numeric thresholds,
long ingredient lists, mixed formula shapes, constants, and complete operator sequences are
rendered and audited rather than declined.

## `method_depreciation`

- **Id.** `method_depreciation`
- **Question.** How is depreciation or amortisation calculated?
- **Alternatives.** `bop_over_life` | `bop_plus_capex_over_life` | `midyear_capex` |
  `average_balance` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `bop_over_life` - "Depreciation on the row labelled {label} is the opening depreciable
    balance spread over the labelled useful life."
  - `bop_plus_capex_over_life` - "Depreciation on the row labelled {label} is the opening
    depreciable balance plus the period's capital spend, spread over the labelled useful life."
  - `midyear_capex` - "Depreciation on the row labelled {label} charges a full year on the
    opening balance and a half year on the period's capital spend."
  - `average_balance` - "Depreciation on the row labelled {label} is struck on the average of
    the opening and closing depreciable balances."
- **Detection.** Role from the row label and its neighbours - asset opening balance, capital
  spend, asset closing balance. Then match the formula against the four variants.
- **Out of catalog.** A flat total-capex-over-life repeated forever, an asset-exists predicate,
  or a different life selected by an unlabelled branch.

## `method_interest`

- **Id.** `method_interest`
- **Question.** What balance is interest struck on?
- **Alternatives.** `opening_balance` | `average_balance` | `full_draw` | `midyear_flow` |
  `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `opening_balance` - "Interest on the row labelled {label} is charged on the opening
    balance."
  - `average_balance` - "Interest on the row labelled {label} is charged on the average of the
    opening and closing balances."
  - `full_draw` - "Interest on the row labelled {label} is charged on the opening balance
    adjusted for the period's draws and repayments in full."
  - `midyear_flow` - "Interest on the row labelled {label} is charged on the opening balance
    adjusted for half of the period's draws and repayments."
- **Detection.** Role from roll-forward neighbours - opening balance, draw, repayment, and a
  rate row. Applies equally to interest income on cash.
- **Out of catalog.** A predicate switching between full-year and half-year treatment based on
  whether the opening balance is zero, unless that switch is a labelled timing assumption.

## `method_tax`

- **Id.** `method_tax`
- **Question.** What base is tax struck on?
- **Alternatives.** `pretax_profit` | `taxable_income` | `taxable_income_after_losses` |
  `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `pretax_profit` - "Tax on the row labelled {label} is charged on pre-tax profit at the
    labelled rate."
  - `taxable_income` - "Tax on the row labelled {label} is charged on taxable income at the
    labelled rate."
  - `taxable_income_after_losses` - "Tax on the row labelled {label} is charged on taxable
    income after deducting the labelled loss balance, floored at zero."
- **Detection.** Role from the row label and a neighbouring rate row.
- **Out of catalog.** Bespoke loss-offset predicates without a labelled loss balance. A bare
  zero clamp, sign flip, or payment lag is not a method choice.

## `method_revenue`

- **Id.** `method_revenue`
- **Question.** How is revenue built?
- **Alternatives.** `prior_period_growth` | `price_times_volume` | `segment_sum` |
  `capacity_utilisation` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `prior_period_growth` - "Revenue on the row labelled {label} grows off the prior period at
    the labelled growth rate."
  - `price_times_volume` - "Revenue on the row labelled {label} is price multiplied by volume."
  - `segment_sum` - "Revenue on the row labelled {label} is the sum of the labelled revenue
    segments."
  - `capacity_utilisation` - "Revenue on the row labelled {label} is capacity multiplied by
    utilisation and price."
- **Detection.** Role from the row label; variant from the formula shape.
- **Out of catalog.** Unlabelled thresholds, year-specific overrides, or embedded prices.

## `method_operating_expense`

- **Id.** `method_operating_expense`
- **Question.** How is an operating cost line built?
- **Alternatives.** `prior_period_growth` | `percent_of_revenue` | `fixed_plus_variable` |
  `component_sum` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `prior_period_growth` - "The cost on the row labelled {label} grows off the prior period at
    the labelled growth rate."
  - `percent_of_revenue` - "The cost on the row labelled {label} is a labelled percentage of
    revenue."
  - `fixed_plus_variable` - "The cost on the row labelled {label} is a fixed amount plus a
    labelled variable rate applied to its driver."
  - `component_sum` - "The cost on the row labelled {label} is the sum of the labelled cost
    components."
- **Detection.** Role from the row label; variant from the formula shape.
- **Note.** Whether an expense sits inside EBITDA or below it is a composition choice, not a
  method choice, and is not covered by this entry.

## `method_working_capital`

- **Id.** `method_working_capital`
- **Question.** How is a working-capital balance struck?
- **Alternatives.** `percent_of_driver` | `days_of_driver` | `balance_delta` |
  `average_driver_days` | `out_of_catalogue`
- **Ship when.** Section default. A hardcoded 365 or 360 is ordinary arithmetic plumbing and
  is classified before the gate rather than treated as a custom method.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `percent_of_driver` - "The balance on the row labelled {label} is a labelled percentage of
    its driver."
  - `days_of_driver` - "The balance on the row labelled {label} is stated as a labelled number
    of days of its driver."
  - `balance_delta` - "The cash-flow effect on the row labelled {label} is the movement between
    the opening and closing balances."
  - `average_driver_days` - "The balance on the row labelled {label} is a labelled number of
    days applied to the average of the opening and closing driver."
- **Detection.** Role from the row label - receivables, inventory, payables, or a days
  assumption. The driver must be semantically appropriate and labelled.

## `method_capex`

- **Id.** `method_capex`
- **Question.** How is capital expenditure set?
- **Alternatives.** `percent_of_revenue` | `prior_period_growth` | `maintenance_plus_growth` |
  `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `percent_of_revenue` - "Capital spend on the row labelled {label} is a labelled percentage
    of revenue."
  - `prior_period_growth` - "Capital spend on the row labelled {label} grows off the prior
    period at the labelled growth rate."
  - `maintenance_plus_growth` - "Capital spend on the row labelled {label} is maintenance spend
    plus growth spend, taken separately."
- **Detection.** Role from the row label.
- **Out of catalog.** A flat embedded amount or a year-specific threshold. Capital spend
  inferred as the balancing item in an asset roll-forward is not a method choice.

## `method_debt_movement`

- **Id.** `method_debt_movement`
- **Question.** How are debt draws and repayments sized?
- **Alternatives.** `required_funding` | `fixed_amortisation` | `maturity_repayment` |
  `cash_sweep` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `required_funding` - "The draw on the row labelled {label} is whatever funding the period
    requires, floored at zero."
  - `fixed_amortisation` - "The repayment on the row labelled {label} is the opening principal
    multiplied by the labelled amortisation rate."
  - `maturity_repayment` - "The row labelled {label} repays the opening principal in full in
    the labelled maturity period."
  - `cash_sweep` - "The repayment on the row labelled {label} sweeps a labelled share of
    available cash, capped at the repayable balance."
- **Detection.** Role from roll-forward neighbours.
- **Out of catalog.** Instrument priority or waterfalls beyond these four. The identity closing
  equals opening plus draw less repayment is not a method choice.

## `method_discounting`

- **Id.** `method_discounting`
- **Question.** How is a present or terminal value calculated?
- **Alternatives.** `periodic_discount` | `npv_plus_t0` | `perpetuity_growth` |
  `exit_multiple` | `midyear_discount` | `out_of_catalogue`
- **Ship when.** Section default, and declines when the band is itself a graded cell.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `periodic_discount` - "The row labelled {label} discounts each period's cash flow at the
    labelled discount rate over that period's count."
  - `npv_plus_t0` - "The row labelled {label} discounts the future cash flows and adds the
    period-zero flow separately."
  - `perpetuity_growth` - "The row labelled {label} values the tail as the next period's cash
    flow over the spread between the discount rate and the growth rate."
  - `exit_multiple` - "The row labelled {label} values the tail as a multiple of a terminal
    metric."
  - `midyear_discount` - "The row labelled {label} discounts on a labelled half-period
    convention."
- **Detection.** Role from the row label - discount factor, present value, terminal value, NPV.
- **Out of catalog.** An embedded terminal multiple or discount rate, or an unexplained period
  shift.

## `method_returns`

- **Id.** `method_returns`
- **Question.** How is a return measure calculated?
- **Alternatives.** `irr_on_series` | `xirr_on_dated_series` | `multiple_of_money` |
  `out_of_catalogue`
- **Ship when.** Section default, and declines when the band is itself a graded cell, which is
  the common case for a returns row.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this copied-column
    calculation, shown for {representative}: {steps}."
  - `irr_on_series` - "The row labelled {label} takes the internal rate of return of the
    labelled investor cash-flow series."
  - `xirr_on_dated_series` - "The row labelled {label} takes the internal rate of return of the
    labelled investor cash flows against their labelled dates."
  - `multiple_of_money` - "The row labelled {label} is total equity proceeds over invested
    equity."
- **Detection.** Role from the row label - IRR, XIRR, MOIC, money-on-money.
- **Note.** The contents and signs of the investor cash-flow series are a composition choice
  and are not covered here. See `cash_flow_sign` in the backlog.

---

# Section 3: Backlog

Observed in the gap reports, not yet implemented. Listed with observed values so the vocabulary
stays stable when they are built. A band that would match one of these currently exits the tree
uncovered, which is exactly the signal that promotes it into Section 1.

- `first_period_carries_cash` - `yes` | `no`. Whether the purchase year also collects a full
  year of cash flow. 0520, 0529.
- `period0_equity_cheque` - `capex_sized` | `cash_float` | `plug`, with a separate
  `year1_capex_netted` boolean. 0518, 0526, 0531.
- `exit_proceeds_basis` - `stake_x_metric_x_multiple` | `after_net_debt_bridge`. 0522, 0524.
- `earnout_years` - the periods that pay. 0522, 0523.
- `cash_flow_sign` - `outflow_negative` | `inflow_positive`. 0530.
- `fy_column_semantics` - `trailing_actual_window` | `forward_forecast`. 0520, 0521, 0523, 0524.
- `working_capital_basis` - driver row, denominator year, member rows. 0462, 0468, 0518, 0524,
  0530.
- `fee_charge_base` - `enterprise_value` | `equity` | `proceeds`, and `year_end` | `average`
  for the balance it is struck on. 0463, 0468.
- `tax_threshold_scope` - which periods a threshold is tested on, and at what frequency. 0515,
  0518, 0525, 0530.
- `summary_statistic` - `mean` | `median`. 0468.
- `scenario_switch` - the active branch of a labelled scenario toggle. 0526.

---

# Section 4: Out of vocabulary

Two kinds of decision cannot be expressed here, and neither gets an entry.

**Bespoke constructions.** Where the author did not choose from a menu but built something.
0463's 2025 profit column is the case: rebuilt line by line from monthly accounts across 51
deleted cells, with four plausible labelled alternatives on a neighbouring sheet and the real
figure matching none of them. There is no enum for this, so no entry covers it and it exits the
tree uncovered. That is the correct outcome - a description precise enough to help would be the
answer.

**Baked-in constants.** A number that lived only inside a deleted formula. Out of scope by
design: keep the rule, drop the constant. Where a constant is genuinely load-bearing, the
remedy belongs to the segmentation stage, which promotes notable literals to supplied inputs.
