# Closed Finance Formula Catalog

Catalog version: `textbook-finance-v1`.

This catalog is deliberately small. Its variants are standard identities and
schedule methods found in corporate-finance, valuation, and financial-modeling
texts: present value, NPV and IRR; perpetuity-growth and exit-multiple valuation;
price-volume, margin and growth schedules; working-capital days; depreciation,
debt and interest roll-forwards; and returns on invested equity. This is a
method catalog, not evidence that any workbook followed a particular publication.

Match economic meaning and all-period values, not exact Excel syntax. Equivalent
sign conventions and sheet references are structural wrappers. Parameters such
as `rate`, `life`, `days`, and timing conventions must come from labeled
assumptions.

For every match, map each named variable slot in the selected variant to a
labeled workbook row or assumption. A variant is recoverable only when all
required slots are mapped and it agrees with every usable cached period under
the gate tolerance. The catalog is closed during a run; workbook-specific logic
must not be promoted into it.

Notation:

- `bop`, `eop`: beginning- and end-of-period balances
- `add`, `draw`, `repay`: current-period additions, debt draws, and repayments
- `r`, `g`, `life`: labeled rate, growth, and useful-life assumptions
- `D`: days in the modeled period, normally 365 or a labeled day-count basis
- `standard` / `variant`: the primary class if agreement and recoverability pass

## Depreciation and amortization

Role aliases: depreciation, D&A, amortization, asset depreciation.

| ID | Class | Variant |
| --- | --- | --- |
| `dep-bop-life` | standard | `bop_depreciable_basis / life` |
| `dep-bop-capex-life` | standard | `(bop_depreciable_basis + capex) / life` |
| `dep-midyear-capex` | variant | `bop_depreciable_basis / life + 0.5 * capex / life` |
| `dep-average-balance` | variant | `average(bop_depreciable_basis, eop_depreciable_basis) / life` |

A flat `total_capex / life` repeated forever, an asset-exists predicate, or a
different life selected by an unlabeled branch is out of catalog.

## Interest expense or income

Role aliases: interest, finance cost, interest expense, interest income.

| ID | Class | Variant |
| --- | --- | --- |
| `int-bop` | standard | `bop_balance * r` |
| `int-average` | variant | `average(bop_balance, eop_balance) * r` |
| `int-full-draw` | variant | `(bop_balance + draw - repay) * r` |
| `int-midyear-flow` | variant | `(bop_balance + 0.5 * draw - 0.5 * repay) * r` |

Use the same variants for interest income on cash. A predicate that switches between
full-year and half-year treatment based on whether BOP equals zero is out of catalog
unless the switch itself is a labeled timing assumption.

## Taxes

Role aliases: tax, taxes, income tax, tax expense, cash tax.

| ID | Class | Variant |
| --- | --- | --- |
| `tax-ebt` | standard | `EBT * tax_rate` |
| `tax-taxable-income` | standard | `taxable_income * tax_rate` |
| `tax-nol-balance` | variant | `max(taxable_income - available_NOL, 0) * tax_rate`, with a labeled NOL schedule |

A bare zero clamp, sign flip, or payment lag is structural. Bespoke loss-offset
predicates without a labeled NOL balance are out of catalog.

## Revenue

Role aliases: revenue, sales, turnover.

| ID | Class | Variant |
| --- | --- | --- |
| `rev-growth` | standard | `prior_period_revenue * (1 + g)` |
| `rev-price-volume` | standard | `price * volume` |
| `rev-segment-sum` | standard | `sum(labeled revenue segments)` |
| `rev-capacity-utilization` | variant | `capacity * utilization * price` |

Scenario selection is structural when it simply chooses among labeled assumption
series. Unlabeled thresholds, year-specific overrides, or embedded prices are not
recoverable.

## Operating expense

Role aliases: OPEX, operating expense, SG&A, salaries, rent, cost line.

| ID | Class | Variant |
| --- | --- | --- |
| `opex-growth` | standard | `prior_period_expense * (1 + g)` |
| `opex-percent-revenue` | standard | `revenue * expense_margin` |
| `opex-fixed-variable` | variant | `fixed_cost + variable_rate * volume_or_revenue` |
| `opex-line-sum` | standard | `sum(labeled operating-cost components)` |

Whether an expense is included in EBITDA or Net Profit is definitional, not an OPEX
method variant.

## Working capital

Role aliases: working capital, receivables, inventory, payables, DSO, DIO, DPO.

| ID | Class | Variant |
| --- | --- | --- |
| `wc-percent-driver` | standard | `driver * working_capital_percent` |
| `wc-days` | standard | `driver * days_assumption / D` |
| `wc-balance-delta` | standard | `bop_balance - eop_balance` for cash-flow impact |
| `wc-average-driver-days` | variant | `average(driver_bop, driver_eop) * days_assumption / D` |

The driver must be semantically appropriate and labeled: revenue for receivables,
COGS or purchases for inventory/payables. A lag or sign reversal is structural.

## Capital expenditure

Role aliases: CAPEX, capital expenditure, fixed-asset additions.

| ID | Class | Variant |
| --- | --- | --- |
| `capex-percent-revenue` | standard | `revenue * capex_percent` |
| `capex-growth` | standard | `prior_period_capex * (1 + g)` |
| `capex-maintenance-growth` | variant | `maintenance_capex + growth_capex` |

CAPEX inferred as the balancing item in an asset roll-forward is structural. A flat
embedded amount or year-specific threshold is not recoverable.

## Debt draws and repayment

Role aliases: debt draw, debt repayment, debt amortization, cash sweep.

| ID | Class | Variant |
| --- | --- | --- |
| `debt-required-funding` | standard | `max(funding_requirement, 0)` |
| `debt-fixed-amortization` | standard | `opening_principal * labeled_amortization_rate` |
| `debt-maturity` | variant | `opening_principal` in a labeled maturity period |
| `debt-cash-sweep` | variant | `min(available_cash * sweep_percent, repayable_balance)` |

The identity `eop = bop + draw - repay` is structural. Instrument priority or
waterfalls beyond the four variants are out of catalog unless separately cataloged.

## Discounting and valuation

Role aliases: discount factor, present value, terminal value, NPV.

| ID | Class | Variant |
| --- | --- | --- |
| `pv-periodic` | standard | `cash_flow / (1 + discount_rate)^period` |
| `npv-periodic` | standard | `NPV(discount_rate, future_cash_flows) + time_zero_cash_flow` |
| `terminal-perpetuity` | standard | `next_period_cash_flow / (discount_rate - growth_rate)` |
| `terminal-exit-multiple` | standard | `terminal_metric * exit_multiple` |
| `pv-midyear` | variant | periodic discounting with a labeled half-period convention |

Which cash-flow definition or terminal metric is used is definitional. An embedded
terminal multiple, discount rate, or unexplained period shift is not recoverable.

## Returns

Role aliases: IRR, XIRR, MOIC, money-on-money return.

| ID | Class | Variant |
| --- | --- | --- |
| `return-irr` | standard | `IRR(labeled investor cash-flow series)` |
| `return-xirr` | variant | `XIRR(labeled investor cash flows, labeled dates)` |
| `return-moic` | standard | `total_equity_proceeds / invested_equity` |

The contents and signs of the investor cash-flow series are definitional. An IRR
guess argument is structural unless it changes which valid root is selected.

## Always definitional

Classify these as `definitional` when the formula chooses included components rather
than applying a finance method:

- EBITDA, EBIT, EBT, Net Profit
- CFO, investing cash flow, financing cash flow, FCF, FCFF, FCFE
- enterprise-to-equity bridge and net debt
- coverage ratios and margin numerators/denominators

## Always structural when no domain method is changed

- BOP/EOP roll-forward identities
- sign flips and unit scaling
- period lags and annualization
- `SUM` aggregation of already labeled components
- `MAX(..., 0)` or `MIN` clamps
- scenario lookup, sheet links, copied subtotals, and presentation mirrors
