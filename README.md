# How a Financial Spreadsheet Gets Taken Apart

### A complete walkthrough of the pipeline, from `.xlsx` file to four labelled buckets

---

## What is in this repository

The pipeline source, and this report describing it. The workbooks it was run
against are client financial models, so neither they nor the ~800 MB of graphs
and segment artifacts derived from them are published here. Paths such as
`ast_out/0248/` and `seg_out/0248/` appear throughout the report as a record of
the run; you will not find those directories in a fresh clone.

| Path | What it does |
| --- | --- |
| `xl_ast_graph.py` | Stage 0. Parses `.xlsx` into the AST graph (`nodes.csv`, `edges.csv`). |
| `xl_seg/` | Stages 1-11. Projection, banding, condensation, scoring, evaluation, lineage. |
| `xl_segment.py` | The CLI that runs the stages and verifies the result. |
| `xl_input_mask.py` | Turns the segmentation into an inputs-only workbook (section 17). |
| `xl_level_split.py` | Writes one workbook per dependency level; `xl_input_mask.py` reuses its XML rewriter. |
| `/create-harbor-task` | Fail-closed Cursor skill: raw workbook → verified, source-profiled MCP Harbor task (section 21). |
| `/profile-mcp-sources` | GPT 5.6 Sol subagent skill: bounded public-source reads → reviewed terminology, dataset, cadence, and excerpt profiles; auth/blocked pages are skipped. |
| `/custom-formula-gate` | Post-Harbor review skill: classifies golden formulas against a closed finance catalog (section 22). |
| `xl_variable_source_audit.py` | GPT 5.6 Sol audit stage: inputs workbook → variable/source Markdown, deterministic inventory, and generation metadata. |
| `xl_variable_mcp.py` + `mcp_env/` | Variable-source MCP environments: externally-sourced inputs masked from the workbook and served through a mock research service (section 23). |
| `xl_mcp_oracle.py` | Generic live-sidecar oracle: exact retrieval, provenance, conflicts, masking, leak isolation, and profile-attribution checks. |
| `requirements.txt` | One dependency, `openpyxl`. |

```bash
pip install -r requirements.txt

# Stage 0: a folder of .xlsx -> ast_out/<id>/{nodes.csv, edges.csv, ...}
python3 xl_ast_graph.py "4-10 100" -o ast_out

# Stages 1-11: ast_out/<id> -> seg_out/<id>, one id per workbook
python3 xl_segment.py 0248 0262 0449 0450 --source "4-10 100" -o seg_out

# Optional: seg_out/<id> -> a copy of the workbook holding only its inputs
python3 xl_input_mask.py 0248 0262 0449 0450 -o inputs_out

# Optional: mask externally-sourced inputs and serve them through a mock MCP
# research sidecar instead (full walkthrough in section 23)
python3 xl_variable_mcp.py validate-spec \
    runs/0233-variable-sources/normalized.json
python3 xl_variable_mcp.py build runs/0233-variable-sources/normalized.json \
    runs/0233-variable-sources/mcp --workbook 0233 --source "4-10 100"
python3 xl_input_mask.py 0233 --source "4-10 100" -o inputs_out_mcp \
    --mask-cells runs/0233-variable-sources/mcp/mask_cells.json
# Package in staging; the audit always reads the unredacted inputs_out copy.
python3 xl_output_task.py 0233 --source "4-10 100" --inputs-root inputs_out_mcp \
    --variable-source-audit-inputs-root inputs_out \
    --mcp runs/0233-variable-sources/mcp -o tasks_outputs_mcp
```

`xl_segment.py` takes workbook ids rather than paths: it reads the graph from
`--ast-dir/<id>/` (default `ast_out`) and the original `<id>.xlsx` from
`--source`, which it needs in order to check its own arithmetic. Add `--llm` to
have the output shortlist named and ranked by a model instead of by the scoring
heuristic, or edit the `curation.toml` that each run writes and re-run to apply
the edits by hand.

The run is self-checking: `xl_segment.py` recomputes every output from the
inputs it selected and compares against the values Excel cached in the file. If
the numbers disagree, or if any cell inside the output cone had to be seeded
rather than derived, it exits non-zero. Section 14 explains why that check is
the thing that makes the segmentation trustworthy.

---

## Table of contents

1. [The problem in one page](#1-the-problem-in-one-page)
2. [The big picture](#2-the-big-picture)
3. [Stage 0 — Reading the raw workbook](#3-stage-0--reading-the-raw-workbook)
4. [What the graph actually looks like](#4-what-the-graph-actually-looks-like)
5. [Why the obvious idea fails](#5-why-the-obvious-idea-fails)
6. [Stage 1 — Collapsing the AST](#6-stage-1--collapsing-the-ast)
7. [Stage 2 — Typing every cell](#7-stage-2--typing-every-cell)
8. [Stage 3 — Bands: the key idea](#8-stage-3--bands-the-key-idea)
9. [Stage 4 — Bypassing the mirrors](#9-stage-4--bypassing-the-mirrors)
10. [Stage 5 — Contracting the loops](#10-stage-5--contracting-the-loops)
11. [Stage 6 — Islands](#11-stage-6--islands)
12. [Stage 7 — Choosing the outputs](#12-stage-7--choosing-the-outputs)
13. [Stage 8 — Cutting the four buckets](#13-stage-8--cutting-the-four-buckets)
14. [Stage 9 — Proving it, by rebuilding the workbook](#14-stage-9--proving-it-by-rebuilding-the-workbook)
15. [Stage 10 — Lineage](#15-stage-10--lineage)
16. [Stage 11 — The files that come out](#16-stage-11--the-files-that-come-out)
17. [Handing back an inputs-only workbook](#17-handing-back-an-inputs-only-workbook)
18. [Results](#18-results)
19. [Bugs found along the way](#19-bugs-found-along-the-way)
20. [Glossary](#20-glossary)
21. [`/create-harbor-task`](#21-create-harbor-task)
22. [`/custom-formula-gate`](#22-custom-formula-gate)
23. [Variable-source MCP environments](#23-variable-source-mcp-environments)

---

## 1. The problem in one page

A financial model is an Excel file. Someone builds it to answer a question like
*"if we buy this company, what return do we make?"*

Inside, it looks like a mess of thousands of numbers. But every financial model,
no matter what it is about, has the same three-part shape:

```
    THINGS YOU              THINGS THE MODEL           THINGS THE MODEL
    HAVE TO SUPPLY   ──►    WORKS OUT ALONG    ──►     IS TRYING TO
                            THE WAY                    TELL YOU

    "Revenue grew           "Gross profit"             "The investment
     8% last year"          "EBITDA"                    returns 28% a year"
    "We pay 21% tax"        "Free cash flow"           "The company is
    "Exit multiple 9x"      "Discount factor"           worth $115m"
```

The left column is the **inputs**. The right column is the **outputs**. The
middle is the **middle**.

Our job: given the Excel file, work out automatically which cells belong in
which column. Then, for every output, write down the complete chain of
calculation that produced it.

That sounds easy. It is not, and section 5 explains exactly why.

**A note on what "cell" means.** A spreadsheet is a grid. Each box in the grid is
a cell, named by its column letter and row number. `B12` is column B, row 12.
Because a workbook has several sheets (tabs), we write the full name as
`Valuation!C30` — sheet `Valuation`, column C, row 30.

---

## 2. The big picture

There are two programs. The first one already existed; the second one is the
subject of this report.

```
        4-10 100/0248.xlsx                      the raw Excel file
                │
                │   xl_ast_graph.py             STAGE 0  (pre-existing)
                │   "read the file and turn every
                │    formula into a wiring diagram"
                ▼
        ast_out/0248/                           8,783 nodes
          nodes.csv                             9,895 edges
          edges.csv
                │
                │   xl_segment.py               STAGES 1-11  (new)
                ▼
    ┌───────────────────────────────────────────────────────────┐
    │  1  collapse the AST         8,783 nodes → 5,448 cells    │
    │  2  type every cell          number? text? date? unit?    │
    │  3  group cells into bands   5,448 cells → 1,739 bands    │
    │  3½ promote formula literals 1,739 → 1,747 bands          │
    │  4  bypass the mirrors       1,747 bands →   756 real     │
    │  5  contract the loops       756 → 756 components         │
    │  6  find the islands         145 islands, 1 big one       │
    │  7  score the outputs        528 candidates, 15 chosen    │
    │  8  cut the four buckets     input/middle/output/scaffold │
    │  9  rebuild and check        20 of 20 outputs correct     │
    │ 10  trace the lineage        454-step derivation          │
    │ 11  write the files                                       │
    └───────────────────────────────────────────────────────────┘
                │
                ▼
        seg_out/0248/
          segments.json          the four buckets
          bands.csv              every band, labelled
          output_candidates.csv  the ranked shortlist
          curation.toml          the bit a human can edit
          lineage/*.md           one derivation per output
          lineage.json           the same, machine-readable
```

The numbers above are real, from workbook `0248`. The whole thing runs in about
five seconds for all four workbooks.

---

## 3. Stage 0 — Reading the raw workbook

This is `xl_ast_graph.py`. It was already written. Understanding it matters,
because everything later depends on the shape of what it produces.

### 3.1 What is inside an `.xlsx` file

An `.xlsx` file is secretly a ZIP archive full of XML. Unzip it and you find one
XML file per sheet. Each cell that has anything in it appears as a little tag:

```xml
<c r="L8">              the cell L8
  <f>K8+1</f>           its formula
  <v>2025</v>           the answer Excel last calculated  ("cached value")
</c>
```

Two things to notice, because both matter later:

- **Excel stores the answer as well as the formula.** That saved answer is
  called the *cached value*. It is what you see on screen. We use it as the
  correct answer to check our own work against.
- **Empty cells are simply absent.** There is no tag for them at all. This one
  fact causes three separate bugs later on. Keep it in mind.

### 3.2 Sorting cells into three kinds

Every cell that exists gets sorted into one of three kinds:

```
   Does the cell have a formula?
            │
      ┌─────┴─────┐
     YES          NO
      │            │
   formula     Is the value text?
                    │
              ┌─────┴─────┐
             YES          NO
              │            │
            label        input
```

- **`formula`** — the cell computes something. `=K8+1`
- **`input`** — someone typed a number in by hand. `2024`
- **`label`** — someone typed words in. `"Revenues"`

That last distinction is the seed of the whole thing. An **`input` cell is, by
definition, something the model cannot work out for itself.** Nothing feeds it.
If you want the model to run, you have to supply it.

### 3.3 Turning a formula into a tree

This is the clever part of stage 0. A formula is not stored as a tree, it is
stored as a line of text. To understand it, you have to parse it.

Take `=K8+1`. As text it is just four characters. As a *structure*, it is:

```
                 ( + )
                /     \
              K8       1
```

That shape is called an **AST** — an *Abstract Syntax Tree*. "Abstract" because
it throws away things that do not matter, like brackets and spaces. "Syntax
tree" because it shows how the parts of the formula fit together.

A bigger example. `=SUM(T215:T231)*C214` becomes:

```
                    ( * )
                   /     \
              ( SUM )     C214
                 |
        ┌────┬───┴───┬────┐
      T215  T216 ... T230  T231
```

You read it from the bottom up: add up the seventeen cells `T215` to `T231`,
then multiply that total by whatever is in `C214`.

The parser uses an algorithm called **shunting-yard**, invented by Edsger
Dijkstra. It reads the formula left to right and uses the normal rules of
precedence — multiply before add, brackets first — to decide the shape of the
tree. Excel has its own quirks the parser has to respect, for example that
`-2^2` is `4` in Excel and not `-4`.

### 3.4 From tree to graph

Now the trees for all 3,887 formulas get glued into one big **graph**.

A graph is just dots and arrows. The dots are called **nodes**, the arrows are
called **edges**. An arrow from A to B means *"A is used to work out B"*.

Every piece of every tree becomes a node:

| node kind | what it is | example id |
|---|---|---|
| `formula` | a cell with a formula | `Dashboard!L8` |
| `input` | a hand-typed number | `Dashboard!K8` |
| `label` | a text cell | `Historicals!B6` |
| `op` | one operator or function inside a formula | `Dashboard!L8#1:+` |
| `const` | a number written inside a formula | `Dashboard!L8#0:const` |
| `range` | a whole block like `C6:C125`, kept in one piece | `SOFR Curve!C6:C125` |

The odd-looking ids like `Dashboard!L8#1:+` mean "the operator numbered 1 inside
cell `Dashboard!L8`, which is a `+`". Every `op` and `const` node records which
cell it belongs to, in a field called `owner`.

So the single cell `L8` containing `=K8+1` produces **three** nodes and **three**
edges:

```
   Dashboard!K8 ────────lhs──────►┐
   (the input, 2024)              │
                                  ├──► Dashboard!L8#1:+ ──result──► Dashboard!L8
   Dashboard!L8#0:const ──rhs────►┘         (the operator)          (the cell, 2025)
   (the number 1)
```

This is why the file has 8,783 nodes for only 5,448 cells. The extra 3,335 are
the innards of the formulas.

### 3.5 Edges carry meaning, not just direction

Each edge records **which slot** of the operator it feeds. This matters more
than it sounds.

Consider `=A1-B1` and `=B1-A1`. Both have the same two cells and the same
operator. Without slot information, both would look identical as a graph, and
one of them would be wrong. So each edge stores:

- **`role`** — a name for the slot: `lhs`, `rhs`, `condition`, `then`, `else`,
  `summand`, `criteria`, `value`, `fallback`, and so on.
- **`arg_index`** — the slot number: 0, 1, 2...

For `IF(K8=1, "Historical", "Projection")`:

```
   (K8=1)      ──condition──► ┐
   "Historical"──then───────► ├──► IF ──result──► the cell
   "Projection"──else───────► ┘
      role         arg_index
```

### 3.6 Ranges get spread out

When a formula says `SUM(K28:T28)`, the parser does not create one arrow from a
blob called "K28:T28". It creates **ten separate arrows**, one from each cell in
the range, all pointing at the same `SUM` node, all sharing `arg_index = 0`, and
all tagged with `via_range = "K28:T28"` so we know they came from one range.

```
   K28 ─┐
   L28 ─┤
   M28 ─┤
   N28 ─┤
   O28 ─┼──all with arg_index=0, via_range="K28:T28"──► SUM ──► Valuation!C30
   P28 ─┤
   ...  │
   T28 ─┘
```

**Except** — remember that empty cells do not exist in the file. If `M28` were
empty, there would be only nine arrows, not ten. Section 19 explains the damage
this caused.

### 3.7 Guessing what each cell is called

A cell like `Valuation!C30` holds the number `115597226.3`. On its own that is
meaningless. But a human reading the spreadsheet knows what it is, because they
can see the words nearby.

Stage 0 copies that trick. For each cell it looks:

```
                      column header, searching upwards
                              │
                              ▼
              ┌──────────────────────────────┐
     row  ──► │ Revenues │ AED │ ... │ 43,200 │
   label      └──────────────────────────────┘
      ▲              ▲                   ▲
      │              │                   │
  leftmost text   nearest text        our cell
  in the row      to the left
```

It takes the **leftmost** text in the row (the line-item name, `"Revenues"`),
the **nearest** text to the left (a qualifier, usually a currency: `"AED"`), and
the **nearest** text above in the same column (the column header,
`"Projection"`). It joins them with slashes:

> `Revenues / AED / Projection`

The leftmost cell is preferred over the nearest one because in these models the
far-left column holds the real name and the nearer columns hold units.

This label guessing works extremely well. In workbook `0248`, **8,782 of 8,783
nodes** get a label. It turns out to be the single most useful piece of
information in the whole dataset, and stages 7 and 10 both lean on it heavily.

### 3.8 What comes out

Two CSV files (plus JSON, GraphML and an HTML viewer we do not use).

`nodes.csv`, one row per node:

```
id, kind, sheet, coordinate, row, col, owner, op, op_kind, arity,
expr, label, formula, value, array_formula, in_degree, out_degree, in_cycle
```

`edges.csv`, one row per arrow:

```
source, target, role, arg_index, op, cell, ref, via_range, cross_sheet, in_cycle
```

### 3.9 References the parser cannot see: `INDIRECT`

Everything so far assumes a formula names its precedents in plain text, so the
parser can read them straight off. One function breaks that assumption, and it
breaks it badly: **`INDIRECT`** builds its target out of *text assembled at run
time*.

```
   Summary!AV72  =VLOOKUP($D72, INDIRECT("'" & AV$4 & "'!$A:$I"), 9, 0) / 1000
```

`AV4` holds the word `"AGI"`. At calculation time Excel glues it into the string
`'AGI'!$A:$I` and reads column I of the `AGI` sheet — but a *static* parser sees
only the pieces of the string, never the sheet they point at. So the natural
graph records a dependency on `AV4` (the word) and nothing at all on the sheet
the number actually comes from. The trail goes cold.

This is not a corner case. The workbook `0251` has **744** such formulas, one
for every division tab (`AGI`, `LRL`, `CB1`, …). Left unresolved, every one of
those tabs looked like disconnected junk, and stage 17 wiped their hand-typed
inputs out of the rebuild file.

The fix: when an `INDIRECT` argument is made only of things we already know —
string constants, `&` concatenations, and single-cell references whose cached
values are in hand — assemble the string ourselves, parse it as a reference, and
draw the real edges. These arrows carry the role `resolved` and a `via_range` so
they are traceable back to the dynamic formula that produced them. On `0251` all
744 resolve, reconnecting the division tabs to the summary sheet that reads them.

When the string is *not* statically knowable (its target depends on a value only
known mid-calculation), the formula is flagged rather than silently dropped, so a
genuine hole is visible instead of masquerading as a clean parse.

### 3.10 Cells no formula mentions

The walk in section 3.4 only makes a node for a cell some formula refers to. That
is fine for the maths, but it means a **hand-typed number nothing references
never enters the graph at all** — and a cell the graph has never heard of cannot
be classified, so stage 17 cannot know to keep it. Pasted subtotals like
`AGI!J13` are exactly this: a person typed the number in, but every formula reads
the monthly columns beside it rather than the total.

So after the walk, every populated non-formula cell that still has no node gets
one, as an `input`. An unreferenced typed value is a supplied number whether or
not the model happens to read it here; making it a node lets the later stages
decide honestly whether it is needed, instead of losing it by omission.

### 3.11 Whole-column ranges

A reference like `$A:$I` nominally spans a million rows. Expanding that literally
would be absurd, so a range that resolves to more populated cells than
`--max-range-expand` collapses to a single `range` node — but its coordinates are
now clamped to the **populated bounding box** (`AGI!A3:I449`, not `AGI!A1:I1048576`).
Downstream stages expand range nodes cell by cell, and the tight box keeps that
from ballooning.

---

## 4. What the graph actually looks like

Four workbooks were used throughout. Here is what stage 0 produced for each.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| what kind of model | DCF valuation | acquisition model | fund / DCF | LBO |
| nodes | 8,783 | 8,289 | 30,769 | 5,440 |
| edges | 9,895 | 9,584 | 43,646 | 7,205 |
| real cells | 5,448 | 4,897 | 11,502 | 2,501 |
| `formula` cells | 3,887 | 4,614 | 9,594 | 1,976 |
| `input` cells | 1,380 | 149 | 1,902 | 506 |
| `label` cells | 181 | 134 | 6 | 19 |
| `op` nodes | 2,684 | 2,643 | 15,204 | 2,262 |

Something worth noticing: **the maths in these files is simple.** Across all
four workbooks there are only **24 different functions** and **14 different
operators**. The twelve most common functions cover 98.4% of all use:

```
   SUM      3237  ████████████████████████████████████████  43.1%  (running total)
   IF        870  ███████████                               54.6%
   MIN       621  ████████                                  62.9%
   OFFSET    612  ████████                                  71.0%
   MAX       605  ███████                                   79.1%
   COUNTIF   540  ███████                                   86.2%
   IFERROR   386  █████                                     91.4%
   EOMONTH   290  ████                                      95.2%
   AVERAGE   108  █                                         96.7%
   YEAR       60                                            97.5%
   CHOOSE     38                                            98.0%
   ROUND      29                                            98.4%
```

There is no statistics, no matrix algebra, no simulation. It is addition,
multiplication and `IF`. This is what makes stage 9 — rebuilding the entire
workbook from scratch — a realistic thing to attempt.

---

## 5. Why the obvious idea fails

Here is the idea everybody has first.

> In a graph, some dots have no arrows coming in. Nothing feeds them, so they
> must be the inputs. Some dots have no arrows going out. Nothing uses them, so
> they must be the outputs. Everything else is the middle. Done.

The vocabulary: a dot with no arrows in is a **source**, a dot with no arrows
out is a **sink**.

It is a good idea. It is also completely wrong in practice. I measured it:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| sources found ("inputs") | 1,845 | 1,029 | 1,908 | 532 |
| sinks found ("outputs") | 1,189 | 1,104 | 959 | 371 |

A model does not have 1,189 answers. It has about fifteen. Something is badly
wrong. There are three separate causes.

### Cause 1 — the same idea repeated across ten columns

A financial model is laid out as a grid of *line items* (rows) against *time
periods* (columns).

```
                2020    2021    2022    2023    2024    2025    2026
   Revenue      1,000   1,100   1,210   1,331   1,464   1,611   1,772
   Costs         (600)   (650)   (705)   (764)   (829)   (899)   (975)
   Profit         400     450     505     567     635     712     797
```

The row "Profit" is **one idea**. But it is twenty-one separate cells in the
graph, each with its own arrows. Counting them separately is like counting the
word "Profit" as twenty-one different words because it appears twenty-one times.

I checked how similar the cells in a row really are, by rewriting each formula
in a form that ignores its position (see section 8.1). Between **79% and 95%**
of rows contain at most two distinct formulas. In other words, a row really is
one or two ideas, copied sideways.

### Cause 2 — the dashboard lies about the topology

Modellers build a summary tab. Its cells do no work; they just point at a real
cell somewhere else:

```
   Dashboard!V10  =  Valuation!C$30
```

That is a **mirror**: a formula whose entire content is "go and fetch that other
cell".

There are a lot of them. In `0248`, **2,091 of 5,448 cells** are mirrors. And
they wreck the topology in the worst possible way:

```
   BEFORE                                     what we want to conclude
   ──────                                     ────────────────────────
   ...  ──►  Valuation!C30  ──►  Dashboard!V10
             ▲                   ▲
             │                   │
   this is the real answer.      this computes nothing at all.
   but it has an arrow OUT,      but it has no arrows out,
   so it is NOT a sink,          so it IS a sink,
   so we would not call it       so we WOULD call it
   an output.                    an output.
```

Exactly backwards, on both counts, for the single most important cell in the
model.

### Cause 3 — junk that looks structural

Spreadsheets are full of cells that exist for the reader, not the maths:

- unit stamps: a cell whose value is just `"AED"` or `"000$"`
- titles: `"LBO"`, `"($ in Thousands)"`
- year axes: `=K8+1` repeated sideways to draw `2024, 2025, 2026...`
- reconciliation checks: `"Check - Balance Sheet"`, which should always be zero

To the graph these look exactly like real values. `"Check - Balance Sheet"` is a
`SUM` on a summary sheet with nothing downstream — a textbook output by every
structural measure. It is not an output. It exists to prove the model adds up.

### The plan

Fix all three, in order:

```
   Cause 1  →  stop looking at cells, look at BANDS         (stage 3)
   Cause 2  →  lift the mirrors out of the graph            (stage 4)
   Cause 3  →  type the cells, and score rather than assume (stages 2 and 7)
```

---

## 6. Stage 1 — Collapsing the AST

The first job is to get rid of the formula innards. We do not need to know that
`L8` contains a `+` node; we need to know that `L8` depends on `K8`.

Every `op` and `const` node has an `owner` field pointing at its cell. So: take
every arrow, replace each end with its owner, throw away arrows that now point
from a cell to itself.

```
   BEFORE (AST level)                       AFTER (cell level)

   K8 ──lhs──►┐                             K8 ──► L8
              ├──► L8#1:+ ──result──► L8
   const(1) ──┘                             (the const vanishes — it lives
                                             inside L8, so its arrow became
                                             L8 → L8 and was dropped)
```

The self-pointing arrows are not errors. Two `op` nodes inside the same cell are
both owned by that cell, so the arrow between them collapses to a loop and is
correctly discarded. In `0248` this discards 1,223 `*` arrows, 536 `IF` arrows
and so on — all of them internal plumbing.

Result: **8,783 nodes become 5,448 cells.**

### One thing that had to be repaired here

`range` nodes are the exception. A `range` node stands for a whole block like
`SOFR Curve!C6:C125`, and — unlike everything else — it has **no arrows coming
in from its own member cells**. It also has no `owner`.

So collapsing would silently delete it, and with it the dependency:

```
   BROKEN                                   FIXED

   SOFR Curve!C6 ... C125                   SOFR Curve!C6 ─┐
        (no arrows to the range node)                 C7 ─┤
                                                     ...  ├──► LBO!G144
   [SOFR Curve!C6:C125] ──► XLOOKUP ──► LBO!G144     C125 ─┘
        ▲
        └── owner is empty, so this whole
            arrow gets dropped, and LBO!G144
            appears to depend on nothing
```

The fix: whenever an arrow starts at a `range` node, work out which cells the
range covers and draw an arrow from each of them instead.

This was not a cosmetic bug. It meant an entire dependency was missing from the
graph, which broke the ordering in stage 9 and stopped `0450`'s circular block
from ever settling down.

---

## 7. Stage 2 — Typing every cell

Now label every cell with what sort of thing it holds. Five types:

```
   ┌──────────┬────────────────────────────────┬──────────────────────┐
   │ NUMERIC  │ a real number                  │ 115597226.3          │
   │ AXIS     │ a year or a date               │ 2025, 2024-12-31     │
   │ UNIT     │ a currency or unit stamp       │ "AED", "000$", "x"   │
   │ TEXT     │ any other words                │ "Base Case"          │
   │ BLANK    │ nothing                        │                      │
   └──────────┴────────────────────────────────┴──────────────────────┘
```

The rules, applied in order:

1. Is it a `label` cell? → **TEXT**
2. Is the value empty? → **BLANK**
3. Does the value look like `2024-12-31T00:00:00`? → **AXIS** (a date)
4. Is the value not a number at all? → **UNIT** if it is a known unit word,
   otherwise **TEXT**
5. Does the label mention "year", "date", "period", "month" or "quarter"?
   → **AXIS**
6. Is it a whole number between 1900 and 2200 whose formula is `=<cell>+1`?
   → **AXIS** (a year counter)
7. Otherwise → **NUMERIC**

Rule 3 is there because Excel stores dates as text like `2024-12-31T00:00:00` in
this export. Without it, every date in workbook `0450` would have been filed as
meaningless text.

Only **NUMERIC** cells can be outputs. That single restriction removes an
enormous amount of noise — including, as it turns out, the highest-scoring
candidate in two of the four workbooks, which was a cell containing the word
`"AED"` that 559 dashboard cells happened to point at.

Two more flags get worked out here, both of which matter later:

- **`is_mirror`** — the cell has exactly one incoming arrow and its role is
  `identity`. That means the formula is nothing but a reference: `=Valuation!C30`.
- **`is_literal`** — the cell's formula is just a written-in number, like `=5`.
  Nothing feeds it, so it is really an input in disguise.

---

## 8. Stage 3 — Bands: the key idea

This is the heart of the pipeline.

### 8.1 Making formulas comparable

In `0248`, `Calculations!K238` contains `=K234` and `L238` contains `=L234`. As
text these are different. As *ideas* they are identical: "take the cell four
rows above me".

To see that, rewrite every formula in **R1C1 form**, where references are
described by how far away they are rather than by their name:

```
   cell   formula      R1C1 form      meaning
   ────   ───────      ─────────      ───────
   K238   =K234        =R[-4]C[0]     four rows up, same column
   L238   =L234        =R[-4]C[0]     four rows up, same column   ← identical
   M238   =M234        =R[-4]C[0]     four rows up, same column   ← identical
```

`R[-4]` means "four rows up from me". `C[0]` means "my own column". A dollar
sign in the original means the reference is locked, so it is written as a plain
absolute number instead. Further along the same row, `O238` holds
`=IF($C$214, SUM(O235:O237), O234)`, which becomes:

```
   =IF( R214C3, SUM(R[-3]C[0]:R[-1]C[0]), R[-4]C[0] )
```

`C214` was locked in both directions, so it survives rewriting as the outright
`R214C3` rather than as an offset.

Two cells with the same R1C1 form are doing the same job.

### 8.2 What a band is

> A **band** is a run of side-by-side cells in one row that have the same kind
> and the same R1C1 formula.

Every numeric cell in row 238 is a formula, and they all sit in one row, yet
they do not make one band:

```
   Calculations row 238 — "Admin & general expenses"

            D238    K238    L238    M238    N238    O238   P238  ...  T238
            ─────   ─────   ─────   ─────   ─────   ─────  ─────      ─────
   kind     form    form    form    form    form    form   form       form
   R1C1     R[-1]   R[-4]   R[-4]   R[-4]   R[-4]   IF(…   IF(…       IF(…
            C[0]    C[0]    C[0]    C[0]    C[0]
            └───┘   └───────────────────────────┘   └─────────────────────┘
            band 1            band 2                        band 3
            unit         copies of row 234            projection formula
```

Instead of eleven separate dots in the graph, that row contributes three.

The effect is large:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| cells | 5,448 | 4,897 | 11,502 | 2,501 |
| bands | 1,739 | 1,242 | 1,438 | 845 |
| shrink | 3.1× | 3.9× | 8.0× | 3.0× |

### 8.3 Why bands and not just whole rows

Grouping by whole rows would be simpler. It would also destroy the answer.

Look at `Valuation` row 23 of `0248`, the cost-of-equity discount factor:

```
   Valuation row 23:

      B23         D23    O23     P23      Q23      R23      S23      T23
      ──────────  ───    ───    ──────   ──────   ──────   ──────   ──────
     "Cost of      "x"    1     0.9050   0.8190   0.7412   0.6707   0.6070
      equity -            │       └─────────── formulas ────────────┘
      discount            │        =O23/(1+$C$21), carried rightwards
      factor"          typed in
                     (kind = input)
```

`O23` is the base year, where someone simply typed a 1. Everything to its right
is that number discounted one more year at a time.

**That boundary, in the middle of the row, is the input frontier.** It is the
exact line between "supplied" and "worked out", and the pipeline does put `O23`
in the input bucket and `P23:T23` in the middle bucket.

Banding finds it for free, because the two halves have different kinds and
different formulas, so they land in different bands:

```
   whole-row grouping:   [ O23 P23 Q23 R23 S23 T23 ]     ← boundary destroyed
   band grouping:        [O23] [P23 Q23 R23 S23 T23]     ← boundary preserved
                         input            middle
```

This is not a rare case: in `0248`, **252 of 657 rows** mix supplied and
computed cells. They do not all split the way row 23 does, though, and the
commonest shape is in fact the reverse of it.

| shape of the row, left to right | rows |
|---|---|
| formula, then inputs | 223 |
| formula, then inputs, then formula | 23 |
| inputs, then formulas | 5 |
| alternating | 1 |

Those 223 are mostly rows like `Historicals` row 5, where the unit stamp on the
left is computed — `D5` is `=BC`, which resolves to `"AED"` — while the ten
year-columns `K5:T5` beside it are all hand-entered revenue figures. The split
falls in a different place, but the lesson is the same: take the row whole and
you fold a presentation cell into a band of raw inputs.

### 8.4 Naming and splitting

A band is named after the cells it covers: `Historicals!K5:T5`, or just
`Valuation!C30` when it is a single cell. If two matching groups in the same row
are not touching, they become separate bands, because a gap usually means a
genuine break.

### 8.5 Stage 3½ — Constants hiding inside formulas

`Calculations!N538` in `0248` holds `=1134847*$C$519`. The flag it references
makes the cell a non-source, so by topology alone it would land in **middle** —
and the `1134847`, the number someone actually typed, would vanish from the
input set entirely. A rebuilder handed "the inputs" could never reproduce the
cell.

So after banding, every non-mirror formula band is scanned for **notable
literals**, and each one is promoted to a synthetic source band of its own:

```
   Calculations!N538#lit=1134847          a band with no cells of its own,
        │                                 kind "literal", one edge out
        ▼
   Calculations!N538   =1134847*$C$519    the host, still a formula band
```

Nothing downstream is special-cased. The synthetic band is a source, so the
ordinary frontier rule (stage 8) files it as an **input** whenever its host
sits in the output cone; the host cell itself stays **middle**, because
multiplying by the flag genuinely is work the model does. The self-closing-cone
proof survives untouched, since literals are sources by construction.

*Notable* means the constant reads as an assumption rather than plumbing: any
non-integer (rates, multiples), or an integer of 100 or more (balances,
hardcoded line items), excluding a small boring set — `0`, `1`, `2`, `12`,
`100`, `365`, `1000`, `10000` and friends — that is arithmetic, not data.

In `0248` this catches 8 constants, 6 of them inside the cone: three hardcoded
working-capital balances on `Calculations` (`N532`, `N538`, `N549`), the two
components of `Historicals!N38`'s `=-16800000-4026`, and the terminal-value
anchor in `Valuation!V10`. The other three workbooks come up clean — their
modellers kept assumptions in cells, where they belong.

---

## 9. Stage 4 — Bypassing the mirrors

Now deal with cause 2 from section 5.

A **mirror band** is a band where every cell is a pure reference. We take those
bands out of the working graph and reconnect around them.

Projected revenue in `0248` travels exactly this way. It is worked out once on
`Calculations`, then relayed across two sheets before anything uses it again:

```
   BEFORE

   Calculations!O38             =IF($C$32,SUM(O35:O37),O33)    52,997,175   ← real work
        │
        ▼ mirror
   'Financial Statements'!O6    =Calculations!O$38             52,997,175
        │
        ▼ mirror
   Dashboard!K12                ='Financial Statements'!O$6    52,997,175
        │
        ▼
   Dashboard!V23                =(V22/K12)*365                      41.46   ← real work


   AFTER

   Calculations!O38 ──────────────────────────────────────────► Dashboard!V23

   recorded separately:
   Calculations!O38 was copied onto 3 presentation bands
```

The same number, 52,997,175, sits in the first three cells; only the last one
does any arithmetic. After the bypass `Calculations!O38` is correctly the cell
that feeds `Dashboard!V23`, and the two relay cells that compute nothing are
correctly out of the running.

The same thing happens at the other end of the model, where `Valuation!C30`
("Equity value (DDM)", one of the chosen outputs) is copied onto
`Dashboard!V10` and `Valuation!C87` and then used by nothing at all. Bypassing
those two makes `C30` correctly a sink.

### The mirrors are not thrown away — they are evidence

Here is the nice part. When a modeller copies a number onto the dashboard, they
are telling you something: *this number matters*. It is their own annotation of
what the model is for.

So each mirror pays a compliment back to the real cell it reflects. We count
those compliments as **`mirror_fanin`**, and stage 7 uses it as a scoring
signal. Roughly: *how many times did the author think this was worth showing?*

Mirrors are re-labelled `presentation` and reported separately.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| bands (incl. stage 3½ literals) | 1,747 | 1,242 | 1,438 | 845 |
| of which mirrors | 991 | 788 | 330 | 148 |
| working bands left | **756** | **454** | **1,108** | **697** |

In `0262`, 63% of all bands were presentation. The real model is much smaller
than it looks.

---

## 10. Stage 5 — Contracting the loops

So far everything has been a **DAG** — a *Directed Acyclic Graph*, meaning you
can never follow the arrows in a circle. That property is what lets you compute
things in a sensible order.

Financial models break it on purpose.

### Why a spreadsheet would contain a circle

In a leveraged buyout model:

```
        ┌──────────────────────────────────────────────┐
        │                                              │
        ▼                                              │
   cash at start of year                               │
        │                                              │
        ▼                                              │
   average cash balance                                │
        │                                              │
        ▼                                              │
   interest earned on cash                             │
        │                                              │
        ▼                                              │
   profit ──► cash at end of year ─────────────────────┘
```

Interest depends on your cash. Your cash depends on your interest. There is no
starting point. This is called a **circular reference**, and Excel handles it
with a setting called *iterative calculation*: guess, recalculate, repeat until
the numbers stop moving.

Workbook `0450` has exactly this. The loop is **186 cells** long.

### Handling it

Use **Tarjan's algorithm** to find every group of nodes that can all reach each
other — a *strongly connected component*, or SCC. Squash each group into one
node:

```
   BEFORE                          AFTER

   A ──► B ──► C                   A ──► [ B C D ] ──► E
         ▲     │                         a single node,
         │     ▼                         which we treat as
         └──── D ──► E                   one indivisible block
```

Now the graph is a DAG again and can be ordered.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| working bands | 756 | 454 | 1,108 | 697 |
| after contracting | 756 | 429 | 973 | 664 |
| biggest loop | none | 18 bands | small | **30 bands (186 cells)** |

### Depth

With a DAG we can measure **depth**: the length of the longest chain of arrows
from any starting point down to this node. Depth is a rough measure of "how far
into the calculation are we", and it feeds the output scoring.

`0450` reaches depth **133**, which reflects how long an LBO debt schedule is.

---

## 11. Stage 6 — Islands

Not all of a workbook is connected. Split the graph into **islands** — groups
where you can get from any node to any other, ignoring arrow direction.

```
        ┌───────────────────────────────┐   ┌───────┐   ┌───┐
        │                               │   │       │   │   │
        │      the main model           │   │ small │   │ 2 │  ...
        │      (555 of 756 bands)       │   │ side  │   │   │
        │                               │   │ calc  │   │   │
        └───────────────────────────────┘   └───────┘   └───┘
             island 0                        island 1    island 2
```

Every workbook has one dominant island and a long tail of little ones — leftover
scratch calculations, unused tables, orphaned notes.

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| islands | 145 | 141 | 14 | 29 |
| main island (bands) | 555 | 310 | 1,056 | 598 |
| share of working bands | 73% | 68% | 95% | 86% |

Being on the main island is a small positive signal for being an output.

---

## 12. Stage 7 — Choosing the outputs

Inputs can be found by pure logic (stage 8). Outputs cannot. There is no
structural fact that separates "the answer" from "a subtotal near the end". It
is a judgement about meaning.

So instead of a rule, we use a **score** built from eight signals, and then let
a human confirm it.

### The eight signals

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │ SIGNAL                WHY IT SUGGESTS "OUTPUT"                  POINTS │
  ├────────────────────────────────────────────────────────────────────────┤
  │ is a sink             nothing uses it, so it is the end          +3.0  │
  │ mirror_fanin          the author copied it onto summaries     +0.6 each│
  │                                                              (max +3.0)│
  │ on a summary sheet    the copies live on a Dashboard             +2.0  │
  │ strong keyword        "IRR", "MOIC", "NPV", "equity value"       +2.5  │
  │ weak keyword          "EBITDA", "margin", "cash flow"            +0.8  │
  │ output-ish sheet      sheet is Dashboard / Valuation / DCF       +1.5  │
  │ depth                 far along the chain of calculation    up to +1.5 │
  │ collapses a series    one number summarising a whole row         +1.5  │
  │ on the main island                                               +0.5  │
  ├────────────────────────────────────────────────────────────────────────┤
  │ looks like a check    "Check", "tie out", "error", "control"     −6.0  │
  │ has no label                                                     −1.5  │
  └────────────────────────────────────────────────────────────────────────┘
```

Two of these are worth explaining properly.

**"Collapses a series"** turned out to be the sharpest structural signal in the
whole pipeline. A band that is one cell wide, fed by a band that is many cells
wide, is squeezing a whole timeline into a single figure:

```
   K28   L28   M28   N28   O28   P28   Q28   R28   S28   T28     ten years of
    │     │     │     │     │     │     │     │     │     │      discounted
    └─────┴─────┴─────┴──┬──┴─────┴─────┴─────┴─────┴─────┘      cash flow
                         ▼
                 Valuation!C30  =SUM(K28:T28)                    ONE number:
                                                                 the valuation
```

Nobody collapses ten years into one number unless that number is the point.

**The check penalty** exists because reconciliation cells are perfect
counterfeits. `"Check - Balance Sheet"` in `0248` scored 11.09 — second highest
in the workbook — on being a terminal, named, series-collapsing cell on a
summary sheet. It is not an output. The −6.0 removes it.

### Two hard exclusions

Before scoring, two kinds of band are removed entirely:

1. **Not NUMERIC** — a unit stamp is never an answer.
2. **No arrows coming in** — *an output is something the model works out.* A
   band nothing feeds is a given, no matter how prominently it is displayed.

Rule 2 alone removed a batch of false positives in `0248`: historical revenue
rows that scored well only because the dashboard displays them. They are inputs
being shown off, not outputs.

Literal bands — `=5` wearing formula clothes, and the synthetic literal sources
from stage 3½ — are barred the same way. A hardcoded constant is a given by
definition.

### The result

For `0248`, 528 bands scored above zero. The top of the list:

```
   rank  score  band              label                          why
   ────  ─────  ────              ─────                          ───
    1    13.90  Valuation!C103    Equity IRR                     sink, keyword, collapse, depth 22
    2    11.09  Valuation!C30     Equity value (DDM)             copied ×2, collapse, depth 16
    3     9.66  Valuation!C65     Equity value (FCFEE)           copied ×2, keyword
    4     8.91  Valuation!C82     Equity value (EV/EBITDA)       copied ×2, keyword
    5     8.39  Valuation!I10     Terminal value                 sink, keyword
    6     8.32  Valuation!I14     Net cash flows to equity       sink, collapse
    7     8.12  Valuation!I8      Free Cash Flow to Equity       sink, collapse
    8     7.89  Valuation!C62     Enterprise value               keyword, collapse
    9     7.64  Valuation!C43     Cost of debt                   sink, collapse
   10     7.14  Valuation!C79     Enterprise value               keyword, collapse
```

That is a valuation model's answer sheet, in the right order.

### The human in the loop

Anything scoring 6.0 or more is auto-included, and the top 40 are written to an
editable file, `curation.toml`:

```toml
[[output]]
band = "Investment!C63"
sheet = "Investment"
label = "Equity IRR"
score = 11.47
include = true
name = "Equity IRR"
# sink=True mirror_fanin=1 strong_term=True scalar_collapse=True depth=21
```

Change `include` to `false`, re-run, and everything downstream rebuilds from
your choice. The scores stay visible as comments so you can see what you
overrode. An optional `--llm` flag hands the same shortlist to a language model,
which rewrites the same file — so the automatic and manual paths are literally
the same interface, and nothing later needs to know which one ran.

---

## 13. Stage 8 — Cutting the four buckets

Now the actual segmentation, and it is short, because all the hard work is done.

### Two ideas

**Ancestors** of a node: everything that can reach it by following arrows
forwards. Everything it depends on, however indirectly.

**Descendants** of a node: everything it can reach. Everything that depends on
it.

### The cone

Take all the chosen outputs. Collect all their ancestors. Call it the **cone**.

```
                 ┌─────────────────────────────────────┐
                 │              THE CONE               │
                 │   everything the outputs depend on  │
                 │                                     │
    ○ ─────────► ●─────►●──────►●──────►●              │
    ○ ─────────► ●─────►●──────►●───┐   │              │
    ○ ─────────► ●─────►●──────►●───┴──►★  ◄── OUTPUT  │
    ▲            │                      │              │
    │            └──────────────────────┘              │
  sources                                              │
  INSIDE the                                           │
  cone = INPUTS                                        │
                 └─────────────────────────────────────┘

    ○ ─────────► ●──────►●        outside the cone entirely
    ▲            ▲
    │            └── DEAD: fed by inputs, reaches no output
    └── UNUSED INPUT: a given that no chosen output needs
```

### The definitions

```
   INPUT   =  nodes in the cone with no arrows coming in
   OUTPUT  =  the chosen outputs
   MIDDLE  =  everything else in the cone
   SCAFFOLDING = everything not in the cone
```

One consequence of stage 3½ falls out here for free: a constant hardcoded
inside a formula is a source, so when its host sits in the cone the constant
lands in **input** while the host cell stays **middle** — which is exactly the
split the formula deserves. A promoted constant no chosen output needs becomes
`unused_input`, like any other ignored assumption.

### Why this is self-closing

Here is the neat part, and it is worth being precise about.

*Could an output depend on something that is not in our input set?*

No, and you can prove it. Suppose some node **X** helps produce an output. Then
X is an ancestor of that output, so X is in the cone. Now walk backwards from X.
Every step stays in the cone, because an ancestor of an ancestor is an ancestor.
The graph is finite and acyclic (we contracted the loops in stage 5), so the
walk must stop. It stops at a node with no arrows in — which is in the cone,
which is therefore in our input set.

So **the input frontier cannot have holes**. It is not a heuristic; it follows
from how it is defined.

The code still checks, because a proof about the design is not a proof about the
implementation. The check is called `unfed_components` and it came out **zero**
for all four workbooks.

### Scaffolding is not a bin — it is a diagnostic

The leftovers get split into three, because each means something different:

| class | what it is | what it tells you |
|---|---|---|
| `unused_input` | a source no chosen output needs | an assumption nobody uses, or an output you did not select |
| `dead` | fed by inputs, reaches no output | an abandoned branch of the model |
| `detached` | touches neither end | notes, scratch work, formatting |
| `presentation` | the mirrors from stage 4 | the summary layer |

If `dead` were huge, it would mean the output selection had missed something.
Reporting it separately turns the leftovers into a quality measure instead of a
dumping ground.

### The result

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| **input** | 266 | 51 | 86 | 129 |
| **middle** | 184 | 145 | 392 | 264 |
| **output** | 15 | 16 | 19 | 40 |
| **scaffolding** | 291 | 217 | 476 | 231 |
| — unused input | 173 | 151 | 182 | 48 |
| — dead | 60 | 51 | 134 | 117 |
| — detached | 58 | 15 | 160 | 66 |
| presentation (from stage 4) | 991 | 788 | 330 | 148 |
| **unfed (must be 0)** | **0** | **0** | **0** | **0** |

> **Since sections 3.9–3.11.** These counts describe the run before typed-cell
> adoption. Giving every unreferenced hand-typed cell a node (section 3.10) adds
> real sources, so the current run has more `input` and `unused_input` cells —
> `0248`'s scaffolding, for instance, rises to 664 (546 of it `unused_input`) as
> the pasted numbers on `Calculations` finally get counted, and `0450`'s `input`
> frontier grows from 129 to 248. `output`, `middle` and the unfed-count guarantee
> are unchanged. `INDIRECT`-heavy workbooks like `0251`, invisible before, now
> segment too.

The input sheets it identifies are the right ones without being told: `InputH`
and `Scenario` for `0262`, `Input & Assumptions` and `CoE` for `0449`,
`Assumptions` and `Historical Financials & KPIs` for `0450`.

`0248` is the odd one: 235 of its 266 inputs sit on a sheet called
`Calculations`. That looked wrong, so I checked individual cells. They are
genuinely hand-typed numbers — the modeller hardcoded forecast line items like
"Other income: 200,000 rising to 380,000" directly into the calculation tab
instead of an assumptions sheet. The segmentation is right; the workbook is just
built untidily. **That is a finding, not a bug** — and it is exactly the kind of
thing this pipeline should surface. The six promoted literals of stage 3½ are
the same habit in its most extreme form: assumptions typed not just on the
wrong sheet, but inside the formulas themselves.

---

## 14. Stage 9 — Proving it, by rebuilding the workbook

Everything so far is an argument. This stage is a test that can fail.

### The test

```
   1.  Take ONLY the input cells. Fill in their saved values.
   2.  Delete every other value. Pretend we never saw them.
   3.  Recompute the entire workbook from its formulas.
   4.  Compare our answers to Excel's saved answers.
```

If the outputs come back right, the input set is **provably sufficient** — you
really can rebuild the model from it. If they come back wrong, either the
frontier has a hole or the evaluator is broken. Either way we find out.

```
        input cells                    everything else
        ───────────                    ───────────────
      ┌─────────────┐                ┌─────────────────┐
      │ 43,200      │                │      ?          │
      │ 8.0%        │   ────────►    │      ?          │  ───► recompute
      │ 9.0x        │                │      ?          │        in order
      └─────────────┘                └─────────────────┘
       seeded from                    blanked out
       saved values                                            │
                                                               ▼
                                                    ┌──────────────────┐
                                                    │  our answer      │
                                                    │  115,597,226.30  │
                                                    └────────┬─────────┘
                                                             │ compare
                                                    ┌────────▼─────────┐
                                                    │  Excel's answer  │
                                                    │  115,597,226.30  │
                                                    └──────────────────┘
```

### Building an Excel

This means writing a small Excel. It has to handle 14 operators and 24
functions, and it works straight off the AST from stage 0.

For each cell: find the one incoming arrow, which points at the top of its
formula tree. Walk down the tree. A `const` node gives a number. A cell node
gives that cell's value. An `op` node gathers its arguments by `arg_index` and
applies the operation.

Some parts needed real care:

**Order.** Compute a cell only after everything it depends on. Find the cell-level
SCCs, sort them topologically, and work through them.

**Circles.** For a contracted loop, do what Excel does: set everything to zero
and go round and round until the numbers stop moving. `0450`'s 186-cell loop
settles after **67 passes**, to a relative change below 1e-12. The cells are
relaxed in sorted order, so that count is the same on every run — left to its
natural order it wandered between 300 and 600 passes from one run to the next.

**`OFFSET`.** Used 612 times. `OFFSET(G36, Case, 0)` means "start at G36 and move
down by however many rows the scenario selector says". The target depends on a
*value*, so it cannot be known in advance. We resolve it at run time, and fall
back to reading the original `.xlsx` if the target cell is not in the graph.

**Dates.** Excel counts days since 30 December 1899. `EOMONTH` and `YEAR` need
that, and so does `XIRR`.

**`IRR` and `XIRR`.** These have no formula; you have to solve for the rate
numerically. Newton's method from a starting guess of 0.1, with bisection as a
backstop.

**Nothing is guessed.** If the evaluator meets something it does not understand,
it returns a marker called `Unresolved` which spreads to everything downstream
and gets reported. It never quietly substitutes zero.

### What actually happened

Seeding **nothing at all** except the hand-typed leaves, the evaluator rebuilds:

| workbook | formulas rebuilt | correct |
|---|---|---|
| 0248 | 3,887 | **3,887 (100%)** |
| 0262 | 4,614 | **4,614 (100%)** |
| 0449 | 9,594 | **9,594 (100%)** |
| 0450 | 1,976 | **1,976 (100%)** |
| **total** | **20,071** | **20,071 (100%)** |

Every formula in all four workbooks, to within one part in a million, with zero
unresolved and zero unsupported functions.

Then the real test, seeding only the **input frontier**:

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| input cells seeded | 1,348 | 123 | 117 | 431 |
| output cells checked | 20 | 272 | 23 | 58 |
| output cells correct | **20** | **272** | **23** | **58** |
| middle sample checked | 400 | 400 | 400 | 400 |
| middle sample correct | **400** | **400** | **400** | **400** |
| verdict | **PASS** | **PASS** | **PASS** | **PASS** |

### The anti-cheating check

There is one way this test could fool itself. Some cells cannot be computed by
anybody — hand-typed numbers, labels, formulas that are just `=5`. Those have to
be seeded. But if such a cell sat *inside* the output cone, an output would have
been handed a saved answer instead of recomputing it, and the test would pass
without meaning anything.

So the pipeline computes the ancestors of every output cell and checks that none
of the extra seeded cells are in there.

**It failed the first time it ran.** 49 cells in `0248`, 234 in `0262`.

The culprits were formulas like `=O604` where `O604` is empty. Empty cells do
not exist in the file (section 3.1), so no arrow was drawn, so the cell looked
uncomputable and got seeded.

But Excel has a clear answer for a reference to an empty cell: **zero**. I
checked all 736 such cells across the corpus — every single one has a saved
value of 0 or blank, confirming it. So they are now computed as 0 rather than
seeded, and the leak disappears honestly rather than by relaxing the check.

---

## 15. Stage 10 — Lineage

For each output, write down the full chain of calculation behind it. Two
resolutions.

### Line-item level — the readable one

Take the output, collect its ancestors, sort them into dependency order, and
print them. `Equity IRR` in `0248` comes out as **454 steps**, of which 266 are
inputs and 178 are intermediate. The last few:

```
 428 | middle | WACC                          | Valuation!C50      | =R[-4]C*R[-3]C+R[-2]C*R[-6]C*(1-R[-1]C)
 430 | middle | Free Cash Flow to Equity      | Valuation!O8:T8    | =SUM(R[-2]C:R[-1]C)
 432 | middle | WACC - discount factor        | Valuation!P52:T52  | =R[0]C[-1]/(1+R50C3)
 445 | middle | Net cash flows disc.          | Valuation!K28:T28  | =R[-2]C*R[-1]C
 447 | output | Equity value (DDM)            | Valuation!C30      | =SUM(R[-2]C[8]:R[-2]C[17])
 449 | output | Equity value                  | Valuation!C90      | =CHOOSE(R[-4]C,R[-3]C,R[-2]C,R[-1]C)
 452 | middle | Cash flow                     | Valuation!O102:T102| =SUM(R[-3]C:R[-1]C)
 453 | output | Equity IRR                    | Valuation!C103     | =IRR(R[-1]C[12]:R[-1]C[17])
```

Read bottom-up and it is a textbook DCF: work out the cost of capital, discount
the cash flows, sum them to a value, then solve for the return. This is a
derivation a human can actually follow, and it is **complete** — nothing is cut.

### Cell level — the exact one

The same thing without the band abstraction: every individual cell, its formula,
its recomputed value, its saved value, and its precedents. `Valuation!C103` has
**2,495 ancestor cells**. The Markdown shows the first 150 for readability;
`lineage.json` holds all of them.

### Why two levels

The band trace answers *"how does this model work?"* — and 454 readable lines
can answer that. The cell trace answers *"where exactly did this number come
from?"* — and for that you need all 2,495.

---

## 16. Stage 11 — The files that come out

Everything lands in `seg_out/<workbook>/`:

```
   seg_out/0248/
   │
   ├── segments.json           the segmentation itself: which bands are input,
   │                           middle, output, scaffolding; islands; embedded
   │                           constants; the full verification report
   │
   ├── bands.csv               one row per band, with bucket, sheet, span,
   │                           depth, island, mirror_fanin, R1C1 pattern;
   │                           stage 3½ literals appear as kind "literal"
   │
   ├── output_candidates.csv   every scored candidate, ranked, with each
   │                           scoring signal in its own column
   │
   ├── curation.toml           THE EDITABLE ONE. include flags and names.
   │                           everything downstream reads only from here
   │
   ├── lineage/                one Markdown derivation per output
   │   ├── equity-irr-valuation.md
   │   ├── equity-value-ddm-valuation.md
   │   └── ...
   │
   └── lineage.json            the same, complete and machine-readable
```

And the code:

```
   xl_segment.py          the command-line entry point, orchestrating everything
   xl_seg/
     model.py             load nodes.csv and edges.csv, index them
     project.py           stage 1-2: collapse the AST, type the cells
     bands.py             stage 3-3½: the band quotient, literal promotion
     condense.py          stage 4-6: mirrors, loops, islands
     frontier.py          stage 7-8: score outputs, find the input frontier
     partition.py         stage 8: cut the four buckets
     evaluate.py          stage 9: the Excel evaluator
     lineage.py           stage 10: derivation traces
     adjudicate.py        the optional LLM pass
     emit.py              stage 11: write everything out
   xl_input_mask.py       section 17: the inputs-only workbook
```

Running it:

```bash
python3 xl_segment.py 0248 0262 0449 0450     # all four
python3 xl_segment.py 0248 --llm              # let an LLM pick the outputs
python3 xl_segment.py 0248 --recurate         # discard hand edits, re-score
python3 xl_segment.py 0248 --threshold 8.0    # be pickier about outputs
```

---

## 17. Handing back an inputs-only workbook

Everything so far has been description: files that say what the model is made of.
This turns the verdict back into a spreadsheet. `xl_input_mask.py` writes a copy of
the workbook in which every hand-typed value is present and every cell the model is
meant to *work out* is empty — the file you would hand to someone told to rebuild
the model.

```bash
python3 xl_input_mask.py 0248 0262 0449 0450       # -> inputs_out/<id>-inputs.xlsx
```

The point of doing this from the segmentation rather than by hand is that the input
set has already been proved sufficient. Stage 9 recomputed every output from the
frontier alone and got the workbook's own numbers back, so the masked file is known
to contain enough to rebuild everything that was removed. Nothing else offers that
guarantee — *when the proof ran.* Not every workbook gets one: a file whose formulas
use a function the evaluator does not implement (`INDIRECT`, `HLOOKUP`, `MATCH`, …)
is segmented with verification skipped, and then the proof of sufficiency simply is
not there. The rule below is what keeps the mask safe with or without it.

### The golden rule: never blank a hand-typed cell

The mask used to blank any number outside the input frontier. That rested entirely
on the frontier being complete — and section 3.9 is a live example of it *not* being
complete: an `INDIRECT` hole meant a whole sheet of typed inputs looked unneeded, so
the old mask deleted them, and the model could no longer be rebuilt.

The safe rule follows from one fact: **a hand-typed value can never be recomputed.**
A formula cell that is blanked will be rebuilt from the inputs; a typed cell that is
blanked is gone for good. Deleting one is only ever safe under a completeness
guarantee the graph cannot always give. So the mask now blanks **only formula
cells**. Every static, hand-entered value survives, whether or not the graph
believes an output needs it. Minimality is traded away for a guarantee that matters
more: the file can always be rebuilt.

There is exactly one exception. A typed cell whose value is a full-precision
duplicate of a chosen output is a *pasted answer* — keeping it would hand the
rebuild its target. Those are redacted and reported (trivial round numbers are left
alone; they collide by accident, not by paste). In `0449` this catches 49 cells and
in `0450` 98, all of them cells in a sensitivity grid that were pasted copies of an
IRR the model is supposed to compute.

### Two rules that matter

**Surviving formulas are replaced by their cached value, so the output holds no
formulas at all.** This is not tidiness. A kept formula's precedents have just been
blanked, so Excel would recalculate it to zero on open — it would not merely be
wrong, it would look confidently wrong. Freezing the value is the only safe move,
and it makes the file completely static, so `xl/calcChain.xml` is dropped with it.

**Blanked cells keep their style, and the rewrite happens on the sheet XML rather
than through openpyxl.** Round-tripping a workbook through openpyxl silently drops
charts; editing the XML in place means column widths, number formats, conditional
formatting and charts all survive. This borrows the machinery `xl_level_split.py`
already used for its dependency-level snapshots.

### Constants that live inside formulas

The promoted literals of stage 3½ own no cell, so the mask has nothing to keep —
and yet without them the workbook cannot be rebuilt. They get carried across two
ways:

1. **The host cell is kept and frozen** to its cached value, the same treatment
   as any other formula-shaped input, so `Calculations!N538` shows `1,134,847`
   instead of sitting empty. Those cells join the inputs-intact check.
2. **An `Embedded Assumptions` sheet is appended** — sheet, cells, line item,
   exact constant, original formula — because the frozen value alone cannot
   always separate the constants. `Historicals!N38` is `=-16800000-4026`: one
   cell, two distinct hardcodes, and only the sheet records both.

The sheet is injected at the XML level like everything else, so it costs no
charts. Only `0248` gets one; the other three workbooks have nothing to report.

### How much survives

Every hand-typed cell survives now regardless of `--keep` (the golden rule above),
so the setting no longer governs whether *typed* numbers or labels live — they
always do. What it controls is how much **formula-derived** presentation content is
frozen back in:

| Mode | What derived content lives (on top of every typed cell) |
| --- | --- |
| `inputs` | Only the input frontier's frozen values. Derived labels and headers go. |
| `labels` | Also formula-*computed* text — a unit stamp like `=BC` resolving to `"AED"`. Typed labels survived anyway. |
| `headers` | **(default)** Also computed period headers: axis formula cells that read as a year or carry a date format. |

The default needs explaining, because it is the one place where this tool
deliberately overrides the segmentation.

A year row is typically `2020` typed once with `=K1+1` dragged across. Nothing in
the model ever computes off those year *values* — the sheets align by column
position — so the row has no path to any output and hangs off the model as a
satellite. In 0248 the segmenter puts `Historicals!K1` in `unused_input` and
`L1:T1` in `detached`, which is topologically correct and, used as a masking rule,
useless: it strips the period axis off every sheet. A period header is not a value
anyone is meant to derive, so it stays.

What it cannot do is trust the `axis` class wholesale. `axis` is assigned from a
cell's shape, not its role, so it also holds real results — in 0248, keeping all of
it would leak `middle` values like `490,000` from `Calculations` row 450. Only cells
that read as a year (an integer in 1990-2100) or carry a date number format get
through. A sweep of all four workbooks found no period header left behind by that
filter, and no non-period value let in by it.

The honest cost of the default: a handful of *computed* cells labelled `middle` are
frozen back in — 15 in 0262 and 163 in 0450. Every one is a year (`2016`-`2027`) or
a month-end date (`2025-06-30`), computed as `previous + 1`. **No output cell
survives in any of the four workbooks.** Use `--keep labels` to drop those computed
headers; it does not, and cannot, drop hand-typed numbers, which the golden rule
keeps in every mode.

### Checked, not asserted

Every run re-opens what it just wrote and compares it against the original, in the
same spirit as stage 9. Four things would be fatal: a formula surviving and giving
the answer away, a formula-derived number surviving outside the keep set, a
redacted pasted answer surviving, or **any typed cell changing or going missing** so
the workbook can no longer be rebuilt. A run that fails any of them exits non-zero.
Each run also prints the stage 9 verdict (`PASS` / `SKIPPED`) so a missing
sufficiency proof is stated openly rather than assumed away.

```
0248  kept 1957  frozen 945  blanked 2942  redacted 0  ->  0248-inputs.xlsx (144.4KB)
      inputs 1357 cells over 5 sheets; every typed cell preserved
      frontier sufficiency proof (stage 9): PASS
      6 hardcoded formula constants -> sheet 'Embedded Assumptions'
      period headers 56 kept though not inputs
      verified: no formulas, no derived numbers, typed cells intact
```

Across the four workbooks, with the default setting:

| Workbook | Input cells | Embedded constants | Period headers kept | Cells blanked | Redacted | Formulas left |
| --- | --- | --- | --- | --- | --- | --- |
| 0248 | 1,357 | 6 | 56 | 2,942 | 0 | 0 |
| 0262 | 123 | 0 | 87 | 3,883 | 0 | 0 |
| 0449 | 145 | 0 | 136 | 9,458 | 49 | 0 |
| 0450 | 551 | 0 | 253 | 1,733 | 98 | 0 |

The *input* count is still just the frontier — what actually feeds the chosen
outputs — and it varies far more than the workbook sizes do. But the file now keeps
every typed cell besides, so an assumption the frontier happens to ignore is still
present to be found, and the mask no longer stakes the file's rebuildability on the
frontier being complete. On `0251`, whose `INDIRECT` formulas leave verification
skipped, that is the whole ballgame: the earlier frontier-only mask had silently
dropped the hand-typed division tabs (`AGI`, `LRL`, …), and keeping every typed cell
is what puts them back.

---

## 18. Results

### Every stage, every workbook

| | 0248 | 0262 | 0449 | 0450 |
|---|---|---|---|---|
| AST nodes | 8,783 | 8,289 | 30,769 | 5,440 |
| AST edges | 9,895 | 9,584 | 43,646 | 7,205 |
| → cells (stage 1) | 5,448 | 4,897 | 11,502 | 2,501 |
| → bands (stage 3) | 1,739 | 1,242 | 1,438 | 845 |
| → after literal promotion (stage 3½) | 1,747 | 1,242 | 1,438 | 845 |
| → working bands (stage 4) | 756 | 454 | 1,108 | 697 |
| → components (stage 5) | 756 | 429 | 973 | 664 |
| max depth | 22 | 23 | 21 | 133 |
| islands / main island bands | 145 / 555 | 141 / 310 | 14 / 1,056 | 29 / 598 |
| **input** | 266 | 51 | 86 | 129 |
| **middle** | 184 | 145 | 392 | 264 |
| **output** | 15 | 16 | 19 | 40 |
| **scaffolding** | 291 | 217 | 476 | 231 |
| verification | PASS | PASS | PASS | PASS |
| full rebuild accuracy | 100% | 100% | 100% | 100% |

The figures above are the original four-workbook run. Sections 3.9–3.11 change the
inputs (typed-cell adoption raises the source counts; `INDIRECT` resolution
reconnects hidden sheets) — see the note under section 13 — but every workbook still
verifies `PASS` and rebuilds at 100%.

Overall shrink from raw graph to decision graph: **8,783 → 756**, about 12×.

### The outputs it found

```
   0248  (DCF valuation)      Equity IRR · Equity value (DDM) · Equity value
                              (FCFEE) · Equity value (EV/EBITDA) · Enterprise
                              value · Terminal value · Free Cash Flow to Equity
                              · Cost of debt

   0262  (acquisition)        Equity IRR · MOIC · Exit equity value · Equity
                              cash flows · Sources of funds · Uses of funds ·
                              EBITDA margin

   0449  (fund / DCF)         Present Enterprise Value (perpetuity method) ·
                              Present Enterprise Value (multiple method) ·
                              Terminal Value · Terminal Value as % of EV ·
                              comparable-company means and medians

   0450  (LBO)                IRR · MOIC · Term · Net Debt / EBITDA · FCCR ·
                              the full IRR and MOIC sensitivity grid
```

These are, in each case, the numbers the model exists to produce.

---

## 19. Bugs found along the way

Building the evaluator flushed out six real defects. All of them affected the
*segmentation* too, not just the arithmetic — a missing dependency is a missing
dependency whichever stage reads it.

**1. Range nodes had no incoming arrows.** `XLOOKUP` against the SOFR curve had
no recorded dependency on it. The topological order was therefore wrong, and
`0450`'s circular block never converged. This was a hole in the graph itself.

**2. Empty operands shifted argument positions.** Because empty cells produce no
arrow, `=J3+1` arrived as a one-argument addition when `J3` was empty. Arguments
are now indexed by slot number rather than packed in order.

```
   BROKEN                              FIXED
   arg list = [ 1 ]                    arg list = [ None, 1 ]
                ▲                                    ▲
        the const slid into                  the empty slot is
        the lhs position                     kept, and reads as 0
```

**3. Paired ranges lost their alignment the same way.**
`XIRR(F235:K235, F229:K229)` with blank cash flows in the middle paired each
amount with the wrong date:

```
   values:  -4396   (blank) (blank) (blank) (blank)  15138
   dates:   2024    2025    2026    2027    2028     2029

   BROKEN pairing:  (-4396, 2024), (15138, 2025)   →  one year   →  244%
   CORRECT pairing: (-4396, 2024), (15138, 2029)   →  five years →   28%
```

Range arguments are now rebuilt from their A1 span rather than from the arrows.

**4. Excel's cancellation rule.** When a `SUM` of values that cancel comes out to
zero, Excel gives exactly `0`. Plain computer arithmetic gives about `5.8e-15`.
Tiny — except it was feeding `IF(x > 0)`, which flipped the branch and changed
three cells in `0449` from 0 to 100.17. The evaluator now snaps a sum to zero
when it is negligible against its own operands, which is what Excel does.

**5. `XIRR` needs Newton's method, not bisection.** A cash-flow series can cross
zero more than once. Bisecting a wide range finds a mathematically valid rate
that Excel would never report. Excel starts from a guess and takes the nearest
root, so now we do too.

**6. Blank dates in `XIRR` must be kept, not dropped.** A blank date is serial 0,
which is 30 December 1899. Discounting a cash flow over 120 years makes it
almost nothing — but once every other flow is discounted that far too, the tiny
stub becomes decisive. `0262` gave 26.6% instead of 20.9% until blanks were kept
as zeros on both sides.

And one defect found after the pipeline was running: **lineage files were
overwriting each other**, because the filename came from the label and a model
can hold several distinct lines called "Enterprise value". `0450` was writing 31
files for 40 outputs. Filenames now fall back to the band reference on a clash.

**7. `INDIRECT` hid whole sheets from the graph.** Found in `0251`, and the worst
of the lot because it was silent. `INDIRECT` names its target as text built at run
time, so a static parse recorded no dependency on the sheet the text points at. The
summary tab reads twelve division sheets through `INDIRECT("'"&AV$4&"'!$A:$I")`,
and every one of those 744 references was invisible — so the division tabs looked
like disconnected scaffolding and their hand-typed inputs (`AGI!J10`, the pasted
totals, all of column J) were dropped from the rebuild file. Two things came out of
fixing it. The graph now resolves an `INDIRECT` whenever cached values pin its
string down (section 3.9), reconnecting the sheets. And the input mask stopped
trusting the graph to be complete at all: it now keeps **every** hand-typed cell
(section 17), so even a dependency the graph still cannot see can no longer cost a
supplied number. The same change surfaced that unreferenced typed cells were never
in the graph to begin with (section 3.10).

---

## 20. Glossary

| term | meaning |
|---|---|
| **AST** | Abstract Syntax Tree. A formula written as a tree instead of a line of text. |
| **band** | A run of side-by-side cells in one row with the same kind and the same R1C1 formula. One line item for one stretch of time. |
| **cached value** | The answer Excel saved in the file. What you see on screen. Our correct answer to check against. |
| **cone** | Every node the chosen outputs depend on, directly or indirectly. |
| **DAG** | Directed Acyclic Graph. Arrows, no circles. |
| **depth** | Longest chain of arrows from any starting point down to this node. |
| **edge** | An arrow. "A is used to work out B". |
| **embedded literal** | A constant hardcoded inside a formula, like the `1134847` in `=1134847*$C$519`. Stage 3½ promotes each notable one to its own source band so the frontier can catch it. |
| **INDIRECT** | An Excel function that builds a cell reference out of text at run time. Its target is invisible to a static parse; stage 0 resolves it from cached values when it can (section 3.9). |
| **resolved edge** | An arrow drawn from an `INDIRECT` target the parser worked out from cached values, rather than one read directly off the formula. |
| **pasted answer** | A hand-typed cell whose value duplicates a chosen output. The mask redacts it so the inputs file cannot hand the rebuild its target. |
| **island** | A group of nodes connected to each other but not to the rest. |
| **input frontier** | The boundary between what must be supplied and what can be worked out. |
| **mirror** | A formula that is nothing but a reference to another cell. `=Valuation!C30` |
| **node** | A dot in the graph: a cell, or a piece of a formula. |
| **R1C1** | A way of writing references by distance instead of by name. `R[-4]C[0]` = four rows up. |
| **SCC** | Strongly Connected Component. A group of nodes that can all reach each other — a circular reference. |
| **shunting-yard** | Dijkstra's algorithm for turning a line of text into a tree, respecting precedence. |
| **sink** | A node with no arrows out. |
| **source** | A node with no arrows in. |
| **topological order** | An ordering where everything comes after the things it depends on. |

---

## 21. `/create-harbor-task`

Package a raw golden workbook into a verified MCP-backed Harbor task. The
Cursor skill at `.cursor/skills/create-harbor-task/` is fail-closed: no plain
task fallback, no skipped segmentation proof, and no promotion before the live
sidecar oracle and grader pass. The golden `.xlsx` stays outside the task
environment.

Accepts a path like `4-10 100/0256.xlsx`, a file under the default source
folder, or a workbook id such as `0256`.

### Pipeline

1. **AST and segment**; preserve curation and require `verification.passed`.
2. **Baseline mask** to `inputs_out/`; this unredacted-input copy drives the
   source audit and is never replaced by the MCP-masked copy.
3. **Audit** with GPT 5.6 Sol through Labelbox LiteLLM.
4. **Import and normalize** every audit row into an atomic variable or an
   explicit exclusion; discover duplicate representations and `extra_cells`.
5. **Profile public sources** with `/profile-mcp-sources`. GPT 5.6 Sol reads at
   most three public pages per canonical URL and retains only reviewed source
   vocabulary, dataset names, field conventions, cadence, and short attributed
   excerpts. Login, SSO, 401/403, paywall, bot-challenge, unreachable, and
   unsupported pages are skipped and keep the generic source renderer.
6. **Validate and build** the network-free, seed-deterministic MCP environment;
   golden-value mismatch aborts.
7. **Smoke-test** the generated FastMCP server.
8. **MCP mask** to a separate `inputs_out_mcp/` workbook.
9. **Package one workbook** with `--mcp` under `tasks_outputs_mcp/`.
10. **Live oracle** the shipped Docker sidecar with `xl_mcp_oracle.py`.
11. **Grade an exact submission** and structurally inspect the bundle.
12. **Promote** into `tasks_outputs/` only after every requested workbook passes.

Naturalized instructions and variable/source audits need `lbx_api_key` in
`.env`. The audit is pinned by default to `openai/gpt-5.6-sol`; public-source
profiling delegates only a redacted URL/name/kind worklist to
`gpt-5.6-sol-high`. `/create-harbor-task` never uses
`--no-variable-source-audit`. Do not invent a taxonomy entry when a workbook is
missing from `workbooks.json`.

In Cursor, invoke `/create-harbor-task` with the workbook path or id.

---

## 22. `/custom-formula-gate`

After Harbor rollouts for a reconstruction task, some golden formulas are
standard finance methods and others are custom logic a model is unlikely to
invent. `/custom-formula-gate` is the Cursor skill that classifies those series
against a closed catalog and decides whether the task should stay flagged.

Run it only after at least one matching Harbor job has completed. The raw
workbook is the answer key for this review; it must not be shown to the model
that performs the task.

### Inputs

- Harbor task bundle, e.g. `tasks_outputs/0256-outputs`
- Harbor job directory with completed attempts, e.g. `jobs/new10-pass5`
- Raw workbook, normally `4-10 100/<workbook>.xlsx`
- Segmentation artifacts, normally `seg_out/<workbook>/`

### Extract context, then classify

```bash
python3 .cursor/skills/custom-formula-gate/scripts/extract_gate_context.py \
  tasks_outputs/0256-outputs \
  --job-dir jobs/new10-pass5 \
  --output runs/custom-formula-gate/0256-outputs-context.json
```

The extractor fails closed if it cannot find a completed matching rollout. It
takes the union of formula bands in the curated outputs' lineage and records
golden formulas, normalized patterns, cached values, labels, neighbors,
references, downstream outputs, and custom-logic signals.

In Cursor, invoke `/custom-formula-gate` on that task. The skill reads
`.cursor/skills/custom-formula-gate/CATALOG.md` as a closed set for the review,
assigns a finance role from labels and neighbors, tests catalog variants against
cached values when available, and writes:

- `runs/custom-formula-gate/<task>-report.json`
- `runs/custom-formula-gate/<task>-report.md`

### Verdicts

| Verdict | Meaning |
| --- | --- |
| `FLAG` | At least one `custom_logic` or `literal_embedded` series sits in a curated output's lineage. |
| `REVIEW` | Nothing flagged, but at least one relevant series is `unclassified`. |
| `PASS` | Relevant domain-method series are `standard` or `standard_variant`. `definitional` and `structural` rows are documented and do not fail the gate. |

Rollout failure alone is not evidence of custom logic. It only controls when the
gate may run and what to look at first; the classification comes from golden
formula versus catalog agreement.

---

## 23. Variable-source MCP environments

A rebuild task normally hands the agent every input. This stage removes the
*externally-sourced* inputs — market rates, tax rates, macro assumptions,
contractual terms, opening balances — and serves them through a mock MCP
research service running as a docker-compose sidecar next to the agent
container. The agent has to research the missing assumptions with generic
retrieval tools before it can rebuild the model. Broad queries return
plausible distractors; resolving a value takes a query that pins down every
dimension (entity, metric, period, scenario, basis, unit, status) *and*, as
of the v2 hardening below, picking the unsuperseded release from a provenance
chain of otherwise identical records, five paginated rows at a time.

The design is adapted from the `build-variable-source-mcp` skill and follows
the [Harbor MCP server task pattern](https://www.harborframework.com/docs/tutorials/mcp-server-task).

The Markdown is a model-generated audit candidate, not evidence that a named
source supplied an exact workbook value. Its warning, deterministic inventory,
inventory SHA-256, model id, endpoint, prompt version, timestamp, API-call
count, and finish reasons are retained so a reviewer can reproduce and audit
the generation. `xl_output_task.py` runs or cache-reuses this stage by default;
pass `--refresh-variable-source-audit` to force a new GPT response. Before a
response is accepted, the generator mechanically rejects qualified cell
references or workbook-value numbers that do not occur in that inventory
batch; one corrective retry is allowed.

### Pipeline

```bash
# 0. Generate the review artifacts from the inputs-only workbook.
#    The deterministic inventory is sent to GPT 5.6 Sol through Labelbox's
#    LiteLLM proxy; lbx_api_key is read from .env and never written to disk.
python3 xl_variable_source_audit.py 0233 \
    --inputs-root inputs_out --seg-root seg_out --audit-root runs
#
#    Emits:
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.md
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.inventory.json
#    runs/<wb>-variable-sources/<wb>-inputs-variable-sources.metadata.json

# 1. Preserve every table row as a draft review queue
python3 xl_variable_mcp.py import \
    runs/0233-variable-sources/0233-inputs-variable-sources.md \
    runs/0233-variable-sources/draft.json

# 2. Normalize by hand/agent into atomic variables (one per entity, metric,
#    period, scenario, basis, unit, status tuple), with workbook cell refs.
#    For 0233 the authoring script is runs/0233-variable-sources/normalize_0233.py
#    and exclusions are documented in exclusions.json.

# 3. Profile deduplicated public source URLs with /profile-mcp-sources.
#    GPT 5.6 Sol receives only a redacted URL/name/kind worklist. Each source
#    is capped at three public reads. Authenticated, paywalled, challenged,
#    unreachable, and unsupported pages are skipped and retain generic source
#    rendering. The normalization script embeds only reviewed profiles.

# 4. Validate the atomic spec and reviewed source-profile references.
python3 xl_variable_mcp.py validate-spec \
    runs/0233-variable-sources/normalized.json

# 5. Build offline and deterministically. Every variable's raw cell value is
#    verified against the golden workbook; any mismatch aborts.
python3 xl_variable_mcp.py build \
    runs/0233-variable-sources/normalized.json \
    runs/0233-variable-sources/mcp --workbook 0233 --source "4-10 100"

# 6. Exercise the generated server with a real MCP client.
uv run --python 3.12 --with fastmcp --with openpyxl \
    python xl_variable_mcp.py smoke runs/0233-variable-sources/mcp

# 7. Mask served variables into a separate inputs workbook.
python3 xl_input_mask.py 0233 --source "4-10 100" -o inputs_out_mcp \
    --mask-cells runs/0233-variable-sources/mcp/mask_cells.json

# 8. Package into staging; audit the unredacted baseline inputs.
python3 xl_output_task.py 0233 --source "4-10 100" \
    --inputs-root inputs_out_mcp --mcp runs/0233-variable-sources/mcp \
    --variable-source-audit-inputs-root inputs_out \
    -o tasks_outputs_mcp

# 9. Oracle-check the shipped bundle against the live sidecar container.
docker build -t mcp-0233-oracle tasks_outputs_mcp/0233-outputs/environment/mcp-server
docker run -d --name mcp-0233-oracle-run -p 18233:8000 mcp-0233-oracle
uv run --python 3.12 --with fastmcp --with openpyxl \
    python xl_mcp_oracle.py \
    --bundle tasks_outputs_mcp/0233-outputs \
    --mcp runs/0233-variable-sources/mcp --url http://127.0.0.1:18233/mcp
```

### What `build` emits

```
runs/<wb>-variable-sources/mcp/
  runtime/            served data: sources, documents, datasets, records
  eval/tasks.jsonl    prompts, typed answers, evidence ids — NEVER shipped
  server.py           standalone FastMCP server (streamable HTTP :8000/mcp)
  Dockerfile          sidecar image (python:3.12-slim + fastmcp)
  mask_cells.json     every cell the masker must additionally blank
  masked_inputs.json  audit map: variable -> refs, raw value, MCP evidence
```

Authoring artifacts next to `mcp/` include `draft.json`, `normalized.json`,
`exclusions.json`, `source_profiles.json`, and bounded profile captures. They
never enter `environment/`.

### Public-source realism without live-value substitution

Accepted profiles shape source descriptions, dataset names and aliases, field
labels, document kinds, release cadence/labels, and at most two short
attributed excerpts. They cannot alter record dimensions, workbook values,
distractor logic, or the generic MCP tools. The builder makes no network calls;
identical normalized specs and seeds produce byte-identical runtime artifacts.
Real URLs remain `origin_url`, while served documents use `mock://`.

Only `status: profiled` plus `review.status: accepted` affects rendering.
Skipped, pending, rejected, internal, and unprofiled sources use the prior
generic treatment. Validation rejects broken references, unsafe URLs,
authentication-derived content, source-ID collisions, unattributed or overlong
excerpts, and workbook-value leakage.

Each variable produces one supported record plus two layers of noise, both
seed-controlled:

- **~18 dimension distractors** (`distractors_per_variable`) that each differ
  on at least one meaningful dimension — adjacent period, another scenario
  case, another basis or unit, another status. No *unsuperseded* distractor
  shares every dimension with any supported record; the generator enforces
  this globally, and `alternate_unit` is careful to emit wrong units that do
  not contain the true unit as a token (the server matches dimensions by
  token containment, so "alternate years" would have made queries for
  "years" ambiguous).
- **2 stale provenance releases** (`provenance_releases_per_variable`) that
  share *every* dimension with the supported record — even `status: final` —
  and differ only in `release`, `published_at`, and a `superseded_by` link
  naming the successor. Their values are perturbed, so dimension filters
  alone return several conflicting rows; the deterministic rule is *use the
  release whose* `superseded_by` *is null*.

`validate` re-proves per task, using the live server's matching semantics:
the dimension filter plus the supersession rule select exactly the evidence
record, every chain is acyclic and ends at it, no stale release carries the
current value, and no evaluation key leaks into the runtime. Supported values
are exact raw workbook values (the grader tolerance is 1e-6, so the service
must serve exactly what the masked cells held); the golden-value check makes
a mismatched spec impossible to build.

The server exposes one generic tool surface across all domains —
`list_sources`, `list_datasets`, `search_documents`, `fetch_document`,
`query_records` — with no target-named tools, no truth flags, and `mock://`
document URLs so synthetic documents cannot be mistaken for genuine
publications. Real source URLs survive only as `origin_url` metadata.
`query_records` requires at least two filter dimensions and pages at five
rows per `next_cursor`, so harvesting the corpus wholesale costs many calls.

### Masking

`xl_input_mask.py --mask-cells` merges the listed refs into the pasted-answer
`deny` set (typed cells are blanked) and removes them from the keep/frontier
sets (frozen formula hosts are blanked, and `verify` accepts the intentional
blanks). This is the one sanctioned exception to the golden rule that a typed
cell is never removed: the value stays retrievable, just through the research
tools instead of the sheet.

Normalization has to hunt down duplicate representations, or the mask is
theatre. For 0233 that meant: sensitivity-table centres holding the same 9%
cost of equity (`DDM!BG68` etc.), the active-case debt-term column
(`Control!F61:F63`), and text renderings like `"20 years"` on the Term
facilities sheet (masked via `workbook.extra_cells`, which are blanked and
audited but not value-checked). Variables whose values are exposed on the
Embedded Assumptions sheet (the 85% availability factor, grid/insurance cost
literals, the reserves bridge) are *excluded* from masking rather than half
masked — see `runs/0233-variable-sources/exclusions.json`.

### Harbor integration

With `--mcp`, `xl_output_task.py` adds to the bundle:

- `environment/mcp-server/` — sidecar Dockerfile, `server.py`, and the
  `runtime/` data. `eval/` never enters the bundle; the audit map goes to
  `tests/masked_inputs.json`, which like `answer_key.json` is only mounted at
  verification time.
- `environment/docker-compose.yaml` — the Harbor compose override: `main`
  depends on a healthy `mcp-server`, which exposes port 8000 with a TCP
  healthcheck.
- `task.toml` — `[[environment.mcp_servers]]` with
  `url = "http://mcp-server:8000/mcp"` (streamable HTTP). The openhands-sdk
  agent registers it natively via `MCP_SERVERS_JSON`; the instruction's
  "Research data service" section also documents the URL and tools so any
  shell-capable agent can connect with `fastmcp`.
- agent timeout extended by 15 s per served variable.

The grader is unchanged: the answer key still holds only the curated output
cells, and retrieval quality is graded implicitly — wrong or unretrieved
inputs surface as wrong outputs.

### Verification before rollout

`xl_mcp_oracle.py` runs against any live sidecar exactly as the agent reaches
it — paginating through `next_cursor` and applying the supersession rule — and
asserts: every masked variable resolves to its evidence record; every
provenance chain is visible; every broad metric query conflicts; every masked
cell is blank; no unapproved numeric, string, date, or formatted duplicate
survives; profile excerpts are attributed and value-safe; and neither the
golden workbook, `eval/`, normalized specs, nor profile captures are in
`environment/`. Legitimate duplicate representations require an explicit
cell/variable/reason allowlist; unknown or unused allowlist rows fail. The
historical 0233 oracle reported:
70/70 resolved, 70/70 chains seen, 70/70 broad conflicts, 0 leaks beyond
documented axis-header coincidences and the excluded Group Tax rate rows
(reports in `runs/0233-variable-sources/oracle_report*.json`).

### Source-profiled rebuild results

The strict staged rebuild promoted three MCP tasks after deterministic
double-builds, in-memory smoke tests, live-container oracles, and exact-answer
grader checks:

| workbook | MCP variables | masked cells | runtime records | accepted / skipped public profiles | result |
| --- | ---: | ---: | ---: | ---: | --- |
| 0248 | 48 | 184 | 1,008 | 1 / 3 | promoted; 48/48 exact resolutions, chains, and broad conflicts |
| 0251 | 1 | 108 | 21 | 0 / 0 | promoted; 1/1 exact resolution, chain, and broad conflict |
| 0255 | 15 | 35 | 315 | 0 / 0 | promoted; 15/15 exact resolutions, chains, and broad conflicts |

All three segmentation proofs pass with no cells seeded inside an output cone;
all strict oracles report zero masked cells left populated, zero unapproved
value leaks, and no allowlist. Workbook 0248 also validates the Damodaran
source-shaped terminology and short attributed excerpts in the shipped
runtime. Reports are under `runs/<wb>-variable-sources/oracle-report.json`.

Workbook 0233 was not promoted. General lazy-branch, dynamic-reference, and
iterative-cycle fixes recompute seven of its eight curated outputs, but
`Term facilities!E88` evaluates `IF(F4=B80,E17,E88)` with `2 != 3`, selecting
the recurrence `E88 = E88`. Every value is a fixed point; the saved
`2.156135461674124` is persisted workbook state rather than a result determined
by frontier inputs. Recovering it would require the cached formula seeding that
the strict pipeline forbids, so the existing 0233 task remains unchanged.

### Difficulty hardening (v2): pagination caps and provenance chains

Trajectory analysis of the first rerun showed the retrieval layer was too
cheap: agents dumped whole datasets with one broad `query_records` call
(limit 200-300) and disambiguated the distractors locally by reading the
dimension labels off each row. Two changes close that route:

**Capped, filter-gated, paginated queries.** `query_records` now requires at
least two non-empty filter dimensions and returns at most 5 rows per page
with an opaque `next_cursor`. Bulk dumps stop working; the agent has to issue
one deliberately-filtered query per variable (or page through many screens).

**Provenance release chains.** Each variable's supported record now ships
with stale releases that share *every* dimension - entity, metric, period,
scenario, basis, unit, even status `final` - and differ only in provenance:
`release`, `published_at`, and a `superseded_by` link naming the successor
release. Stale releases carry perturbed values, so a fully-filtered query
legitimately returns several conflicting rows and the dimension labels no
longer decide. The deterministic resolution rule - *use the release whose*
`superseded_by` *is null* - is stated in the server instructions, every
dataset description, the document text ("SUPERSEDED: replaced by release X"),
and the task instruction, so the environment stays fair: exactly one record
survives the rule, and no judgment call is required.

Determinism is enforced mechanically, not hoped for. The validator replays
the *live server's* token-containment matching semantics (a lesson from the
first build of v2: a generic "alternate years" distractor unit substring-
matched queries for "years" and made 18 tasks ambiguous until
`alternate_unit` was taught to emit non-colliding units) and asserts per
task: the dimension filter plus the supersession rule select exactly the
evidence record, every stale chain is acyclic and terminates at it, and no
stale release accidentally carries the current value. The smoke test and
oracle check resolve every variable the same way over real streamable HTTP,
paginating like an agent would.

### 0233 rerun: opus-5, formula hints, 5 attempts

The 0233 task was rebuilt with 70 atomic variables masked (573 cells) and
re-evaluated with the same harness as the baseline: `harbor run` (docker),
openhands-sdk, `anthropic/claude-opus-5`, 5 attempts, formula-hinted bundle,
scored by `xl_passk_score.py`. One harness change was forced by the docker
backend: containers on user-defined compose networks could not reach
`host.docker.internal`, so the Labelbox attribution proxy now runs as a
per-trial compose sidecar (`runs/llm-proxy-compose.yaml`, layered in through
the job config's `environment.extra_docker_compose`) and the agent points at
`http://llm-proxy:8019`.

The task was evaluated three times with the same harness: the baseline with
every input on the sheet, MCP v1 (70 variables masked, unlimited queries),
and MCP v2 (same masking plus pagination caps and provenance chains). All
runs: 5 attempts, no errors, coverage 100%.

| metric (0233-outputs) | baseline | MCP v1 | MCP v2 (hardened) |
| --- | --- | --- | --- |
| continuous score mean | 0.8159 | 0.7964 | 0.8469 |
| continuous score variance | 0.00216 | 0.00316 | 0.00409 |
| pass@5 (all 8 cells exact) | 0% | 0% | 0% |
| mean cells exact | 30.0% | 30.0% | 25.0% |
| best attempt exact | 50% | 50% | 25% |
| mean within 1% | 45.0% | 45.0% | 50.0% |
| agent steps per attempt | 59-90 | 55-91 | 82-123 |
| cost | $37.37 | $32.10 | $43.72 |

Per-cell continuous means and the full attempt detail are in
`runs/opus5-mcp-hinted-0233/` and `runs/opus5-mcp2-hinted-0233/`
(`percell_comparison.json` in the latter holds the three-way table) next to
the baseline in `runs/opus5-hinted-pass5/`.

What the three runs say, from the trajectories:

- **v1 made retrieval too cheap.** Agents dumped whole datasets in 1-5 broad
  `query_records` calls and read the dimension labels locally; zero failed
  queries, zero distractor values used, but attempts skipped 0-13 of the 28
  hard-to-guess values as not worth retrieving (the H2 2021 capex was skipped
  in 4/5 attempts). Mean fell ~2 points to 0.796.
- **v2 forced engagement and got it.** Every attempt wrote a pagination-aware
  helper (looping `next_cursor` with entity+metric filters), paged through
  each metric, and explicitly kept only unsuperseded releases. Zero stale
  release values appear in any attempt's model code. Retrieval got *more*
  thorough - all five attempts recovered 28/28 distinctive masked values -
  at the price of ~30 more steps and ~$2 more per attempt.
- **The continuous mean rose above the baseline (0.847 vs 0.816)** because
  the forced thoroughness fixed v1's skipped-variable losses (DSCR 0.77 ->
  0.88, Implied EV 0.68 -> 0.90 per-cell means), while exact solves narrowed
  (best attempt 25% exact vs 50%): more inputs were right, but small unit
  and convention slips in the longer research pipeline kept cells just
  outside the 1e-6 exact band - within-1% rose to 50%. Variance roughly
  doubled versus baseline (0.0022 -> 0.0041), driven by one 0.75 and one
  0.95 attempt.

Net: the hardened environment measurably raises the cost of research (steps,
tokens, dollars) and defeats bulk-dump shortcuts without making the task
unfair - the supersession rule was applied correctly 5/5 times. On this task
the binding constraint remains the deep calculation chains (DSCR, FCF after
debt service, enterprise value bridge), not retrieval. One residual softness
to tighten next: `search_documents` snippets can expose a document's reported
value without its supersession notice; no agent exploited it, but trimming
the snippet before the value line would close it.
