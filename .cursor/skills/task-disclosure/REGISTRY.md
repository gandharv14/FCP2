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

## Reading a label

Every `Detection` below resolves a role from a row label, so the label reader is global and no
entry can correct it locally. The label is the row's **description** cell, not simply the first
non-empty cell to the left of the band. A candidate matching a units, currency or scale pattern
is rejected and the next cell tried.

The rejection test must match token **classes**, not literal strings. The implemented filter is an
exact-match set of unit tokens plus a minimum length, so it rejects `000$` and accepts anything
whose spelling it does not already carry. Every token in the Trap below passed it for that reason:
none is a transposition of a listed token, each is simply absent from the list. Required classes: a
currency code with an optional scale suffix (`EURm`, `USD k`), a bare unit word (`days`, `x`,
`yrs`), a statement abbreviation (`p&L`), and a marker word (`flag`).

- **Trap.** Tokens observed being read as labels: `flag` where `B80` reads "FCF discount rate"
  (0596), `EURm` where `B119` reads "Dividends" (0638), `USD k` where `C388` reads "Cash Closing
  Balance" (0644), `days` where `B42` reads "DIO" (0632), `p&L` where `B137` reads "Finance
  costs" (0632). Three of the five left the band with no role at all. The other two - 0632's
  `days` and `p&L` bands - were roled off a neighbouring row and then died in the formula
  classifier, so a label fix alone does not rescue them.
- **Trap.** The reader walks leftward and takes the first accepted cell, so rejecting one token
  only moves the read one column. On 0632 row 137 the filter correctly rejected `000$` in `D137`
  and then accepted `p&L` in `C137`. A class-based test has to cover every column it may land on,
  not just the first.
- **Limit.** Where no cell on the row yields a description, the band carries no label and no
  entry may claim it. This is a limit, not a defect to widen around: 0630's row 219 and 0644's
  row 416 are genuinely empty across their label columns, and reaching them needs `calcChain`
  or position-based detection rather than any label rule.

## Arbitration between entries

Where a band matches more than one entry by label, take the candidates in order and evaluate the
first against its `Ship when`; if that declines, fall through to the next candidate. A band is left
undisclosed only when **every** candidate declines.

This replaces failing closed on collision. A band that two entries claim is not thereby
undisclosable, and it does not need a `role_resolutions.json` entry to proceed.

**The order is within a section, and Section 2 is evaluated before Section 1.** File order and
evaluation order are not the same thing: the method detector runs first, as the Section 2 preamble
says, while every Section 2 entry appears below every Section 1 entry here. So "the order they
appear" governs candidates drawn from the same section only, and a Section 2 candidate is always
considered ahead of a Section 1 one regardless of position in this file.

**A candidate resolving to a withheld value counts as a decline.** Where an entry claims a band and
then lands on a value it does not word - `terminal_value`'s `other` is the standing case - it has
disclosed nothing, so the band falls through to the next candidate rather than being consumed.
Without this, an entry whose `Ship when` is `always` could silently swallow a band it cannot
describe.

What remains is that a candidate which passes its `Ship when` **and** resolves to a value it does
word still wins, even if it describes the band wrongly. That has to be beaten on order: where a
specific entry must precede a general one, it is placed first and says so in its own `Ordering`
field.

## Writing a sentence

A rendered sentence that collides with one already written for another band is re-rendered with
a distinguishing anchor - the sheet name, or the label of the row above - or else reported. It
is never silently dropped. Two records, on 0618 `OpCo!L169:W169` and 0632 `Calc!L169:W169`, were
marked `disclosed`, appeared in `agent_records`, and reached neither instruction file, because
each rendered byte-identical to the bullet for a neighbouring row carrying the same label.

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
- **Implemented scope.** The label match is no longer restricted to "minimum cash" and
  "less: minimum". Any labelled waterfall, guard or flag row whose cached values are all zero is
  a candidate, which is what `Detection` below already describes; the old restriction was
  narrower than the entry's own test. `charged_once` and `charged_every_period` become reachable
  once the detector counts non-zero periods rather than only testing for all-zero.
- **Sentence.**
  - `always_zero` - "The row labelled {label} evaluates to zero in every period; the charge it
    describes never applies in this model."
  - `charged_once` - "The row labelled {label} is charged in one period only."
  - `charged_every_period` - "The row labelled {label} is charged in every period."
- **Detection.** The row is populated but every cached value is zero.
- **Seen in.** 0514, 0515, 0526, 0531; 0644 - `Flags!H388`, "Cash Closing Balance", populated
  and zero in every modelled year, matching this `Detection` word for word while the old label
  restriction excluded it; 0638 - the misleading neighbour rows above the dividend line.

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
- **Alternatives.** `unused` | `populated` | `populated_but_unread`
- **Ship when.** `always`, because the detector only emits a record once every condition below
  already holds. `populated` is never emitted; it tells the agent nothing it cannot see.
  `populated_but_unread` is emitted under its own conditions, listed after the `unused` set.
- **Detection conditions for `unused`.** All of these, applied when the record is built:
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
    every member to carry data and the `unused` value requires the row to be empty, while
    `populated_but_unread` requires that no formula reference the row at all - which a spanning
    `SUM` necessarily does. Neither value can name a row `aggregate_scope` counts as a member.
- **Detection conditions for `populated_but_unread`.** The mirror case, and the reason this
  entry's Question is "used at all" rather than "empty". All of these:
  - The row carries values or formulas in the golden, so the `unused` conditions do not apply.
  - No formula anywhere in the workbook references any cell on the row. The row is a dead end,
    not a driver. Range arguments must be expanded before this is tested: a `SUM` over a span
    counts as referencing every row inside it. A reference scan that skips oversized ranges must
    treat the result as unknown and decline, not as an absence of references.
  - The label reads as an assumption or a rate rather than as a section heading, so an agent
    would plausibly pick it up and apply it.

  The graded-sheet test that governs `unused` deliberately does **not** apply here. It exists to
  stop empty labelled rows being reported as noise on sheets that do not matter, and a populated
  row nothing reads is a live mis-signal wherever it sits. Applying it here would exclude the only
  observed case: 0677's payout-ratio row is on `'Ratios and Assumptions'`, and all fourteen of that
  task's graded cells are on `'Financial Model'`.

  Without this value, a surviving assumption row that nothing reads is indistinguishable from a
  live driver, and an agent that applies it diverges everywhere downstream.
- **Sentence.**
  - `unused` - "The row labelled {label} is not used in this model. Leave it empty."
  - `populated_but_unread` - "The row labelled {label} carries values in the original, but no
    formula reads it. Nothing downstream depends on it."
- **Detection.** For `unused`, the row carries a label, has no formula and no value anywhere,
  and sits inside the block that feeds a graded answer. For `populated_but_unread`, the row is
  populated and no reference anywhere resolves onto it.
- **Seen in.** 0462, 0517, 0520, 0522, 0525, 0527, 0529; 0677 - `'Ratios and
  Assumptions'!H17:L17`, a payout-ratio row that both scored runs applied and that no formula in
  the golden reads.
- **Restriction.** `unused` is only reported on sheets carrying a graded cell, because elsewhere an
  empty labelled row is nearly always a section header. `populated_but_unread` is not restricted
  this way, for the reason given in its conditions.

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
- **Seen in.** 0523, 0530; 0596 - declined.
- **Ownership.** This entry owns the Question for a discounted series whose first flagged period
  anchors the factor at 1, 0596's `DCF` row 80 being the observed case. Recorded so the
  vocabulary is not forked into a second entry, even though that band declines today: 12 of its
  13 cells are graded, and both this `Ship when` and the Section 2 policy withhold it. A future
  revisit lands here rather than coining a new id.

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
- **Detection.** The total's `SUM` argument, resolved to the labels of the rows it spans. The
  span must be **vertical** and must cover at least two distinct labelled rows. A same-row
  horizontal `SUM` spans one row only, so the member list resolves to a single row and the sentence
  degenerates to naming one member, most visibly as "the total labelled X is the sum of the row
  labelled X". Measured on the 08_24 pack, **62 of the 75** `aggregate_scope` records across 0618,
  0632, 0638 and 0650 rendered that shape, each carrying evidence like `= SUM(K169:W169)`. 0596
  carries two more.

  Decline when the total's own formula wraps the `SUM` in anything further, `MAX(SUM(),0)`
  included. The `Ship when` above already implies this by requiring a bare `SUM`; the failure
  was that the detector resolved a neighbouring period total across columns instead of reading
  the formula that actually builds the band.
- **Seen in.** 0520, 0522, 0527. The correction has a real cost on the first two, recorded so it is
  a decision rather than a surprise. Each keeps its genuine vertical record - `=SUM(B35:B36)` on
  0520 and `=SUM(B36:B37)` on 0527, both over 'Enterprise value' and 'Net (Debt)/Cash' - and each
  **loses a currently disclosed bullet**: the same-row period sums `=SUM(B16:F16)` on 0520 and
  `=SUM(B17:G17)` on 0527. Those two are the defect shape, naming one member off a horizontal sum,
  but note they do not read as tautologies - both render "Total labelled 'Total Free Cash (Inflow)'
  sums the rows labelled 'Free Cash Flow'", where the two labels differ. The degenerate
  same-label form is the visible symptom, not the boundary of the defect. 0522 carries no
  `aggregate_scope` record at all, so its citation was already stale before this change.
- **Note.** The correction is not uniform in effect. On 0618, 0638 and 0650 the single-member shape
  was the whole of the `aggregate_scope` disclosure. On 0632 it was not: 13 genuine two-member
  vertical records survive, `=SUM(K171:K172)` over 'Retained earnings - bop' and 'Net profit'
  among them.

## `projection_rule`

- **Id.** `projection_rule`
- **Question.** How is a forecast row carried forward?
- **Alternatives.** `hold_level` | `hold_growth` | `step_increment` | `average_window` |
  `ratio_to_driver`
- **Ship when.** The ingredient the rule needs is **not** available to the agent. Specifically:
  - **declines** when every ingredient row is either visible in the delivered file or sits
    within fifteen rows on the same sheet. The agent can see the structure, so choosing the
    rule is ordinary spreadsheet reasoning and getting it wrong is an agent mistake. The
    proximity test looks at position only; it does not require the nearby row to be labelled.

    **Carve-out.** Visibility of an ingredient is not visibility of the rule. Where the golden's
    value is `step_increment` and the increment's own label matches a rate-like pattern -
    "growth", "p.a.", "per annum", "%" - two alternatives stay consistent with everything the
    agent can see, and the default reading of that label is the wrong one. The proximity test
    does not decline in that case. Scoped to this one alternative pair, `step_increment` against
    `hold_growth`, so the test stays mechanical rather than becoming a judgement about how
    obvious a rule is.
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
    them, or when one merely repeats the target's own label **on the same sheet**. A
    same-string label on another sheet is a different row and may be named. In the
    same-sheet / unlabelled / too-many cases the sentence would be mush, and mush is
    worse than silence.
  - **ships** when the ingredient is absent, unlabelled at reach, or on another sheet with no
    pointer.
  - for `hold_level`, which names no ingredient, **ships** only when no value on the row is
    visible to the agent, so nothing indicates the level should stay constant.
- **Known gap.** Two, both left to backlog rather than fixed here.
  - A window that is structurally reachable but semantically non-obvious is not detected - 0523
    averages historical years while the surrounding text implies forecasts. The reachability test
    cannot see that, so 0523's records currently decline. Detecting it needs a notion of which
    columns are historical.
  - `hold_level` names no ingredient, so it cannot say **which** column a flat run anchors on.
    0632 freezes its working-capital day counts at the 2022 column with `=$K$42` on rows 42, 50
    and 58, and the `hold_level` sentence could not express that even if it shipped - which it
    does not, because the three historical columns survive on the row. That decision belongs to
    `working_capital_basis` in the backlog, which already lists a denominator year. Deliberately
    not fixed by widening `hold_level`.
  - `hold_level` declines twice over on 0677's hold-level row: values on the row survive, and the
    row switches rule partway across, so the full-coverage clause refuses it as well. Both
    declines are correct as written and neither is loosened here; recorded so the band is not
    mistaken for an unclassified miss.
- **Sentence.**
  - `hold_level` - "The row labelled {label} is held flat across the forecast at its last
    known level."
  - `hold_growth` - "The row labelled {label} grows off its prior period using the rate in
    {ingredient}."
  - `step_increment` - "The row labelled {label} carries forward by adding {ingredient} to the
    prior period. It is added each period, not compounded."
  - `average_window` - "The row labelled {label} is the average of {ingredient}."
  - `ratio_to_driver` - "The row labelled {label} is worked out in each period from
    {ingredient}."
- **Detection.** Classify each cell on the row, then split the row into contiguous runs of one
  kind. A bare reference to the previous column is hold level; a `*(1+x)` factor is hold
  growth; a reference to the previous column plus or minus exactly one further term is a step
  increment; an `AVERAGE(` is average window; a product against a driver is ratio to driver.
  Report each run separately - a row may change rule partway across.

  A formula whose first operand is the immediately-prior column of the **same row**, carrying
  exactly one further term, is a projection rule and not an aggregation. `=+H59+$F$17` is the
  observed failure: the leading `+` made it read as a component aggregation, the band was
  assessed `structural`, and it never reached this entry at all. The test is mechanical -
  prior-cell-first, exactly one other term - and it does not catch a genuine `SUM` over member
  rows.
- **Seen in.** 0462, 0463, 0468, 0517, 0520, 0523, 0525, 0530; 0644 - `Info!I59:M59`, the
  additive market-share roll-forward, which its gap report calls the single largest gain
  available on that task and measures at 20 of 25 graded cells once a second fix lands alongside
  it.
- **Known limit.** A window whose start period is wired to a labelled event is **not** covered
  here, and no alternative was added for it. 0638's `CalcA!C133`, labelled "Hold start", is
  `=C7` - a single cell equal to another named cell, not a run of held values - so it belongs to
  `source_selection`, which already words a row taking its whole value from another named row.
  Adding a window-anchor alternative here would have widened the broadest entry in this file for a
  band a narrower entry already reaches.
- **Trap.** A rate-like label on a step increment makes compounding the default reading. 0644's
  `Info!F17` is labelled "Market share growth p.a." and holds 0.01, which in most models is a
  compounding input, and the golden adds it: `=+H59+$F$17`. The `step_increment` sentence
  therefore has to negate the alternative reading rather than merely state the rule, which is
  why its wording carries "not compounded". Silence here is not neutral - it leaves the wrong
  reading standing.
- **Note.** This entry was the source of the over-disclosure that motivated the registry. With
  no `Ship when` it produced 677 of 751 agent-facing records.

## `distribution_policy`

- **Id.** `distribution_policy`
- **Question.** What sizes the distribution paid to holders in each period?
- **Alternatives.** `residual_cash_floored` | `residual_cash_unfloored` | `payout_ratio` |
  `capped_at_retained_earnings` | `first_period_only`
- **Ship when.** The distribution row is blank in the delivered file and no surviving labelled
  input states the policy. **Declines** when a payout rate survives on a labelled row and the
  distribution is that rate applied to a single visible driver, which is ordinary reasoning. No
  unread-row test belongs here; whether a surviving rate row is actually read is
  `row_populated`'s Question, and duplicating it would put two bullets on one decision.
- **Sentence.**
  - `residual_cash_floored` - "The row labelled {label} distributes whatever cash the period
    leaves over, and never falls below zero."
  - `residual_cash_unfloored` - "The row labelled {label} distributes whatever cash the period
    leaves over, including where that figure is negative."
  - `payout_ratio` - "The row labelled {label} distributes a labelled share of the period's
    earnings."
  - `capped_at_retained_earnings` - "The row labelled {label} distributes no more than the
    retained earnings available at the time."
  - `first_period_only` - "The row labelled {label} pays in the first period only."
- **Detection.** Rows whose label contains dividend, distribution, or shareholder payment. A
  `MAX` against zero over a cash-available term is the floored residual; the same term without
  the `MAX` is unfloored; a product against an earnings row is a payout ratio; a `MIN` against a
  retained-earnings balance is the cap. **Every value needs a positive signal and there is no
  default**: a dividend row whose shape matches none of the four is left uncovered. Asserting a
  rule from the absence of a token is how this entry would become the next `projection_rule`, whose
  own Note records it once produced 677 of 751 records.
- **Known limit.** The values are written as siblings but the shapes are not exclusive. 0677's
  `=MAX(MIN( H167:H168),0)` is a floored residual **and** a cap at retained earnings at once - the
  lesser of cash available and retained earnings, held at zero - and only one value can be
  reported. The floor wins, because it is the reading the task's own report attributes to the cause
  and the one the observed agents got wrong. A row where the cap is the load-bearing half would be
  described incompletely, and expressing both would need the cap to become a modifier rather than
  an alternative.
- **Seen in.** 0638 - `CalcA!H119:AC119`, where one bullet is the only single disclosure measured
  to move anything on that task, at 12 of 24 graded cells rising to 18. 0677 -
  `'Financial Model'!H169:L169`, labelled "Dividends to be distributed", where the golden is
  `=MAX(MIN(H167:H168),0)` and the value is `residual_cash_floored`; the band carries no entry at
  all today and is not graded, so nothing suppresses it.
- **Note.** Requires the label-reader correction at the head of this file: the 0638 band resolved
  to `EURm` and so never reached a role at all, which is why nothing shipped rather than something
  shipping wrongly.
- **Scope.** 0677 is one cause across two bands on two sheets. The dividend row above carries the
  measurement: its report's fix table applies that cause alone and moves 4 of 14 graded cells to 8,
  the only one of six causes that moves anything by itself. The payout-ratio row at
  `'Ratios and Assumptions'!H17:L17` is the mis-signal both scored runs picked up, and it goes to
  `row_populated` as `populated_but_unread`; the report's generous-ceiling note makes clear that the
  8 of 14 credits an agent which both applies the residual rule **and** disregards that surviving
  row, so the second bullet is belt-and-braces against the mis-signal rather than an independently
  measured gain. This is not the double-counting an earlier draft was corrected for: that was one
  band claimed by two entries, this is two bands.

## `liquidation_preference`

- **Id.** `liquidation_preference`
- **Question.** How do exit proceeds divide between preferred and common holders?
- **Alternatives.** `participating` | `non_participating` | `pro_rata_no_preference` |
  `capped_participation`
- **Ship when.** The waterfall rows are blank in the delivered file and the preference terms are
  not stated in the instruction. **Declines** when a surviving labelled row states the
  preference multiple, which makes naming the convention equivalent to handing over the split.
- **Sentence.**
  - `participating` - "Exit proceeds on the row labelled {label} pay the preferred holders their
    preference and then let them share in the remainder alongside the common holders."
  - `non_participating` - "Exit proceeds on the row labelled {label} pay the preferred holders
    the greater of their preference and their converted pro-rata share, not both."
  - `pro_rata_no_preference` - "Exit proceeds on the row labelled {label} divide pro rata by
    holding, with no preference paid ahead."
  - `capped_participation` - "Exit proceeds on the row labelled {label} pay the preferred holders
    their preference and a share of the remainder, capped at a labelled multiple."
- **Detection.** Rows sitting inside an exit or cap-table block whose label names an exit
  distribution: preference, preferred, liquidation, waterfall, senior equity, **exit equity value,
  exit proceeds, or proceeds to holders**. The label test alone is not enough to pick the value, so
  read the shape: participation shows as a preference term added to a pro-rata term;
  non-participation as a `MAX` or `IF` between the two; and a preference **solved for** rather than
  added shows as the pro-rata denominator appearing on both sides, as in
  `=(C42-$C$21+$C$24*$C$21)/$C$24`.
- **Seen in.** 0668 - `'Cap table'!D43` and `D49`, which share the row label "Exit Equity Value"
  and which its gap report calls otherwise unwinnable.
- **Trap.** The observed labels are the reason the label list above is wider than the entry's name
  suggests. `'Cap table'!B43` and `B49` read "Exit Equity Value" - none of preference, preferred,
  liquidation, waterfall or senior equity appears anywhere on the row, and a detector built from
  the entry's name alone reaches nothing in the pack. Note also that `terminal_value` does **not**
  claim these rows: its detector tests for the literal substrings "terminal value" and "exit
  value", and "exit equity value" contains neither.
  Classify on the most specific formula on the row, not the leftmost. These rows carry a plain
  pro-rata column beside the column holding the preference: 0668 row 43 is `=C42/$C$24` in `C` and
  the solved preference in `D`. Reading the first formula found reports `pro_rata_no_preference`
  and states the opposite of the convention in force.
- **Measurement.** The 0668 report measures a bullet on these two cells at 3 of 5 graded cells
  rising to 5 of 5, but that bullet stated the **arithmetic identity** between the two columns and
  explicitly did not name the mechanism. This entry's sentences name the mechanism instead, so the
  3 to 5 figure is not evidence for this wording. Whether a convention sentence wins the same cells
  is untested.
- **Ordering.** Placed **ahead of** `stake_scaling`. This is a hazard rather than an observed
  collision: `stake_scaling` matches a formula referencing a row labelled equity investment,
  ownership, stake or percent acquired, and `'Cap table'!B24` reads "New investors shares", which
  matches none of them - so on the observed evidence it does not in fact fire here. Its one
  disclosed record on 0668 is `C59`, off "Ownership post-Series B". The ordering is kept because
  the two entries describe overlapping surfaces and `stake_scaling` ships on `always`, so if its
  label match were ever widened it would win the band and describe it as an ownership-share
  multiplication.
- **Note.** The band being graded does not suppress this entry. It is a Section 1 convention: it
  names which of four defensible splits the author chose, and does not state a construction over
  inputs the agent still holds. The Section 2 policy on graded targets does not reach here.

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
- **Question.** Which named row does this row take its whole value from?
- **Alternatives.** the source row, named by its label and sheet.
- **Ship when.** The source is itself rebuildable from surviving inputs. **Declines** when the
  source cell is one kept literal away from the answer, which makes naming it equivalent to
  handing the answer over.
- **Sentence.**
  - source - "The line labelled {label} takes its value from {ingredient}."
- **Detection.** A row whose formula is a single reference - optionally sign-flipped, or scaled
  by one labelled factor - to another named row, on this sheet or another. The original
  purchase-price scope is the subset that motivated the entry: labels containing initial
  investment, purchase price, entry, consideration, or `cv`, reading from another sheet. The
  broader test is what reaches a plain mirror.
- **Seen in.** 0517, 0520, 0523, 0524, 0527, 0529, 0530; 0618 - `DCF!C59` takes the OpCo blended
  rate; 0632 - `Calc!N142` is `=N140`; 0638 - `CalcA!C133`, labelled "Hold start", is `=C7`, which
  is where the hold window's start period comes from. That last case is why `projection_rule` did
  not need a window-anchor alternative.
- **Note.** A mirrored row needs no separate entry. An earlier proposal for a `row_mirrors_row`
  id would have collided with this entry on both cells it cited, and with `method_tax` on 0632's
  deferred-tax row, producing a three-way claim. No two disclosed records from **different**
  entries currently share a cell. Same-entry sharing does occur and is not checked: on 0632, 13
  cells on row 172 are each claimed by two `aggregate_scope` records, one for the "Net profit"
  total and one for the per-column "Retained earnings" totals.

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

## No method on a graded target

The graded condition above is **not relaxed** for any entry in this section. It is restated here
because the temptation to relax it recurs, and the argument for relaxing it is plausible and
wrong.

The argument runs: naming a method discloses a technique, not a number. That holds where the
technique still leaves work to do - naming XIRR over a dated series leaves the whole cash-flow
series to build. It fails where the method plus the inputs the agent still holds reconstructs the
band directly. Naming a discount-factor construction over a kept WACC and a kept period window
yields the factors themselves, at which point the method *is* the answer.

Rather than grade that risk case by case, the line sits at whether the band is graded, which is
mechanical and checkable. Measured on the 08_24 pack: 0596's `DCF` row 80 is graded in 12 of its
13 cells and declines; 0652's `Calculation!D229` and `D239` are both graded and decline. The
control case is 0618's `DCF` row 77 - the same construction, no graded cell on the row, and it
ships. The condition discriminates rather than blanket-suppressing.

Cost of the rule, recorded so it is not rediscovered as a defect: on 0596 the withheld bullet was
measured at 63 of 88 graded cells rising to 70, the largest single-bullet gain observed in that
pack. Section 1 conventions are outside this policy, because naming which convention an author
chose is not a construction over kept inputs.

## `method_depreciation`

- **Id.** `method_depreciation`
- **Question.** How is depreciation or amortisation calculated?
- **Alternatives.** `bop_over_life` | `bop_plus_capex_over_life` | `midyear_capex` |
  `average_balance` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  or a different life selected by an unlabelled branch. Also a flat prior-year amount capped by a
  balance, `=MIN($K$198,L205)`, and a rate struck over a **closing** rather than opening
  balance. These stay out of catalogue deliberately: naming either as a catalogued variant would
  convert a confident no-match into a match, route the band to `standard`, and ship nothing. The
  role assignment here is already correct for amortisation labels - the failure was the
  catalogue match, not the role.
- **Seen in.** 0618 - the capped flat amount and the closing-balance rate, both
  `out_of_catalogue`.

## `method_interest`

- **Id.** `method_interest`
- **Question.** What balance is interest struck on?
- **Alternatives.** `opening_balance` | `average_balance` | `full_draw` | `midyear_flow` |
  `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  whether the opening balance is zero, unless that switch is a labelled timing assumption. Also a
  prior-period balance carried without a roll-forward, a historical-year yield used as the rate,
  and a rate struck over a closing balance. For naming the balance a rate is struck on, reuse the
  `year_end` | `average` vocabulary already carried by `fee_charge_base` in the backlog rather
  than coining new terms. These stay out of catalogue for the reason given on
  `method_depreciation`.
- **Seen in.** 0618, 0632 - both `out_of_catalogue`.
- **Arbitration.** This entry's `Detection` already covers interest income on cash, which puts it
  in collision with `method_working_capital` on finance-income labels - that entry already owns
  `OpCo!L36` and `Calc!N36`. Under the arbitration rule at the head of this file the collision no
  longer fails closed: the earlier entry is evaluated first and a decline falls through to the
  other, so neither band is lost to the collision itself.

## `method_tax`

- **Id.** `method_tax`
- **Question.** What base is tax struck on?
- **Alternatives.** `pretax_profit` | `taxable_income` | `taxable_income_after_losses` |
  `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
  - `pretax_profit` - "Tax on the row labelled {label} is charged on pre-tax profit at the
    labelled rate."
  - `taxable_income` - "Tax on the row labelled {label} is charged on taxable income at the
    labelled rate."
  - `taxable_income_after_losses` - "Tax on the row labelled {label} is charged on taxable
    income after deducting the labelled loss balance, floored at zero."
- **Detection.** Role from the row label and a neighbouring rate row.
- **Out of catalog.** Bespoke loss-offset predicates without a labelled loss balance. A bare
  zero clamp, sign flip, or payment lag is not a method choice. A deferred-tax row that mirrors
  another row is claimed here as `out_of_catalogue`; it needs no separate mirror entry.
- **Seen in.** 0632 - the deferred-tax row at `=N79`, where the role is already assigned
  correctly and only the classification of the mirror was missing.

## `method_revenue`

- **Id.** `method_revenue`
- **Question.** How is revenue built?
- **Alternatives.** `prior_period_growth` | `price_times_volume` | `segment_sum` |
  `capacity_utilisation` | `out_of_catalogue`
- **Ship when.** Section default.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
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
- **Note.** The graded-cell decline is not relaxed. 0596's `DCF` row 80 is the case that tested
  it: the construction is identical to 0618's row 77, which ships, and the only difference is
  that 12 of the 13 row-80 cells are graded. Stating the rule over the kept WACC and the kept
  window would produce those 12 values. See the policy at the head of this section.
- **Seen in.** 0618 - `out_of_catalogue`, disclosed; 0596 - same shape, declined as graded.

## `method_returns`

- **Id.** `method_returns`
- **Question.** How is a return measure calculated?
- **Alternatives.** `irr_on_series` | `xirr_on_dated_series` | `multiple_of_money` |
  `out_of_catalogue`
- **Ship when.** Section default, and declines when the band is itself a graded cell, which is
  the common case for a returns row.
- **Sentence.**
  - `out_of_catalogue` - "For {band} on the row labelled {label}, use this
    {calculation_kind}, shown for {representative}: {steps}."
  - `irr_on_series` - "The row labelled {label} takes the internal rate of return of the
    labelled investor cash-flow series."
  - `xirr_on_dated_series` - "The row labelled {label} takes the internal rate of return of the
    labelled investor cash flows against their labelled dates."
  - `multiple_of_money` - "The row labelled {label} is total equity proceeds over invested
    equity."
- **Detection.** Role from the row label - IRR, XIRR, MOIC, money-on-money.
- **Note.** The contents and signs of the investor cash-flow series are a composition choice
  and are not covered here. See `cash_flow_sign` in the backlog. The graded-cell decline is
  likewise not relaxed: 0652's `Calculation!D229` is a graded target and declines correctly.
  Naming a return measure is closer to harmless than the discounting case, which is exactly why
  the line is drawn at whether the band is graded rather than at an estimate of leak risk - a
  per-entry judgement would not stay stable. See the policy at the head of this section.
- **Seen in.** 0652 - declined as graded.

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
  0530, 0632 - where rows 42, 50 and 58 of `Calc` freeze their day counts at the 2022 column with
  `=$K$42`. This is the denominator-year decision already listed here; `projection_rule` cannot
  express it, since the shape routes to `hold_level`, which names no ingredient. Building this
  would win no graded cell on 0632 - its report puts the strict ceiling at 41 of 76 with or without
  it - so it is listed for vocabulary stability rather than for yield.
- `fee_charge_base` - `enterprise_value` | `equity` | `proceeds`, and `year_end` | `average`
  for the balance it is struck on. 0463, 0468.
- `tax_threshold_scope` - which periods a threshold is tested on, and at what frequency. 0515,
  0518, 0525, 0530.
- `summary_statistic` - `mean` | `median`. 0468.
- `scenario_switch` - the active branch of a labelled scenario toggle. 0526.
- `fcf_composition` - which lines sit inside free cash flow and which sit below it.
  `method_operating_expense` already notes that composition is not a method choice, so no entry
  covers it today. 0603.
- `sweep_pool_scope` - which cash pool a sweep draws on, and the order instruments are repaid in.
  Two orthogonal decisions, and no existing entry expresses ordering. 0677.
- `multi_run_row` - a row changing rule more than once across the forecast, so it carries three or
  more contiguous runs. No single entry claims it, and `projection_rule`'s full-coverage clause
  declines it by design rather than ship one bullet per run each claiming the whole horizon. 0632 -
  `DCF` row 49 starts at the implied price, repeats it once, then indexes on net-profit growth:
  three runs, two changes. Part of the three-convention gain its report measures at 41 of 76 graded
  cells rising to 62.

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
0630 is the observed 08_24 case: the divisor of a graded ratio survived only inside the deleted
formula, so no registry entry can reach it and no backlog id should be coined for it. The fix is
segmentation promoting that literal to a supplied input.
