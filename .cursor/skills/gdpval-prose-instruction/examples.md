# Example: task 0243 before and after

Source has disclosure and a research service. One graded cell.

## Before (staged after gate 12)

````markdown
A colleague has sent you a financial model with every calculated figure stripped out, and this time the externally-sourced input assumptions have been removed from the sheets as well: market rates, tax rates, macro assumptions, contractual terms and opening balances have to be researched through the data service described below before anything can be computed. Rebuild the calculations from the remaining inputs plus your research, and report the 1 headline figures the model exists to produce, such as Term Loan to CALIF (Total). The full list of figures and the cells they belong in is given below.

## Input

The workbook `0243-inputs.xlsx` is in your working directory. Every cell the model is meant
to work out is blank, and a set of externally-sourced input assumptions has
additionally been removed from the sheets. Those removed inputs are only
retrievable through the research data service described in the next section;
every other input - drivers, historicals and labels - is present. The artifact
has been checked to contain no formulas and to preserve identified inputs,
labels, and sheet structure. You may install Python packages (for example
`openpyxl`) to read it.

## Research data service

The removed input assumptions (rates, tax and macro assumptions, contractual
terms, opening balances, dates and similar externally-sourced values) are
served by a mock research MCP server at `http://mcp-server:8000/mcp` (streamable HTTP transport). It
exposes five tools:

- `list_sources` - the organizations and data platforms available
- `list_datasets` - structured datasets and their filter dimensions
- `search_documents` / `fetch_document` - keyword search over source
  documents, then fetch one by id
- `query_records` - filter dataset records by entity, metric, period,
  scenario, basis, unit and status

Records vary across those dimensions and broad queries return conflicting
candidate values from adjacent periods, other scenario cases and earlier
releases. `query_records` requires at least two filter dimensions and returns
at most 5 rows per page; follow the returned `next_cursor` to page through
larger result sets. Records exist in multiple releases: a row whose
`superseded_by` field names a later release has been replaced and must not be
used - only the unsuperseded release of a record is authoritative. Filter
every dimension that matters to your question. Convert each retrieved value
into the units and scale of the sheet it belongs on (rates are reported in
percent while sheets may store decimal fractions; monetary amounts state
their unit on the record).

Any MCP client works, for example from Python:

```
pip install fastmcp
```

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://mcp-server:8000/mcp") as client:
        print(await client.call_tool("list_sources", {}))

asyncio.run(main())
```

## What to compute

| # | figure | cells |
| --- | --- | --- |
| 1 | Term Loan to CALIF (Total) | `Dashboard!C43` |



## Workbook disclosure

These are choices the original model made that the delivered file no longer shows.
They describe how the model works, not the figures you are asked to report.

- `Financing!F73:HC73`: For Financing!F73:HC73 on the row labelled "Drawings", use this copied-column calculation ... (bullet continues verbatim)

## Output

Write a JSON object to `/app/answers.json` mapping each cell reference above to
the value the model computes for it, keyed exactly as written in the table:

```
{"Dashboard!C43": <number>}
```

Report 1 values in total, one per cell listed. Give each number exactly as the
model computes it, in the same units and scale as the sheet it sits on - do not
round, rescale or convert percentages.
````

## After (validated candidate, v2 voice)

````markdown
You are an Analyst on the project finance team of an infrastructure advisory firm. The team advises operators of regional transport concessions on how they fund vehicle fleets and depots, and you support the Director on each mandate with the financing schedules and the dashboards that go to the client. Work is assigned to you by the Director.

The Director wants to know how much term debt the concession vehicle will draw in total to fund its asset company, which the dashboard reports as Term Loan to CALIF (Total). The working file, `0243-inputs.xlsx`, is in your working directory; the figure is reported on the `Dashboard` tab. The concession's traffic, revenue, cost, capex and financing drivers, the historicals and the layout are in place; the calculations across the schedules have not been carried out yet. You may install Python packages (for example `openpyxl`) to read it.

The market rates, tax rates, macro assumptions, contractual terms and opening balances the model needs are not in the sheets; they are only available from the firm's research service, an MCP server at `http://mcp-server:8000/mcp` (streamable HTTP transport). Records come in releases; use the latest release of each one. Convert each value into the units and scale of the sheet it feeds: rates come back in percent while the sheets may store decimal fractions, and monetary amounts state their unit on the record. Complete the necessary analysis from the inputs that are there plus what you retrieve, and report the 1 headline figure the Director is waiting on.

Any MCP client works, for example from Python:

```
pip install fastmcp
```

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://mcp-server:8000/mcp") as client:
        print(await client.call_tool("list_sources", {}))

asyncio.run(main())
```

The figure the client needs, and the cell it belongs in, is:

| # | figure | cells |
| --- | --- | --- |
| 1 | Term Loan to CALIF (Total) | `Dashboard!C43` |

<!-- workbook-notes:start -->
One modelling choice on the financing schedule is not obvious from the workbook, so the Director has written it down for you. It describes how that row is worked out, not the figure you are asked to report.

- `Financing!F73:HC73`: For Financing!F73:HC73 on the row labelled "Drawings", use this copied-column calculation ... (bullet continues verbatim)
<!-- workbook-notes:end -->

Put your answer in a JSON object at `/app/answers.json`, keyed by cell reference exactly as written in the table above:

```
{"Dashboard!C43": <number>}
```

Give the figure as the model produces it, in the units and scale of the sheet it sits on, unrounded; leave percentages as the cell holds them.
````

## What changed

- Persona from sheet names (`Hyp_Traffic`, `Financing`, `FS A_CALIF`): a project-finance Analyst on a transport-concession mandate. No dates, no client names. The sheet names ground the setting; the prose does not list them. Only the answer tab, `Dashboard`, is named (the validator allows at most three sheet names beyond the answer tabs).
- The question is asked as the Director would ask it and names the first curated output, Term Loan to CALIF (Total).
- Template framing: the drivers and layout are in place and the calculations have not been carried out yet. "stripped out", "Rebuild", "removed", "blank", "no formulas", "remaining", "mock", "delivered file", "artifact" are all gone.
- Research service: URL, transport, only-source, "latest release", units/scale warning, both code blocks. The tool list, "five tools", paging, "at least two filter dimensions" and the `superseded_by` mechanics are dropped; the server documents them itself.
- Every table row and code block is byte-identical and in the same order. The four tool-list items were omitted as a group (allowed); the disclosure bullet is verbatim.
- The disclosure bullet sits inside `<!-- workbook-notes:start/end -->` so step 15.5 can swap it for `additional-assumptions.md` without headings.
- Deliverable: JSON path and keying, then the code block, then the rule in one sentence. The "Report 1 values in total" count is dropped (optional).
- Prose numbers: one `1` (the "1 headline figure"); the source's second `1` and its `5` lived in droppable sentences.
