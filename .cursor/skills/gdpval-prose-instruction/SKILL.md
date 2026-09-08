---
name: gdpval-prose-instruction
description: Rewrites a Harbor finance-task instruction.md into heading-free GDPval-style prose (a junior finance role, the business question, the workbook, the research service, the table of figures, the answers.json deliverable) while every cell reference, code block, table, URL, and disclosure sentence survives byte-exact; MCP mechanics the server documents itself and pipeline narration ("removed", "blank", "no formulas") are dropped. Use when the pipeline runs with --gdpval-prose at gate 13, when asked to make an instruction "GDPval style" or "read like a real brief", or when retrofitting an existing task's instruction.md in place.
---

# GDPval-style prose instruction

The current instruction is a form: `## Input`, `## Research data service`,
`## What to compute`, `## Output`. This skill turns it into the brief a
Director would hand an Analyst. No headings. A real role. The business
question first. Every machine-critical fact kept. Nothing that tells the
reader the workbook was gutted for a test, and nothing the research server
already explains about itself.

Two modes, one CLI:

- **Pipeline** (`build_one.sh <WB> ... --gdpval-prose`): replaces the
  naturalize gate (13). Source is the staged, disclosure-bearing instruction.
  Additional-assumptions (15.5) runs later and stays heading-free.
- **Retrofit** (standalone on any task dir): source is the shipped
  instruction, which may already point at `additional-assumptions.md`.

## Paths

```bash
TASK=tasks_outputs_mcp/0253-outputs        # or a shipped task directory
WB=$(basename "$TASK" | cut -d- -f1)
PROSE_RUN="runs/$WB-instruction-prose"
S=.cursor/skills/gdpval-prose-instruction/scripts
```

## 1. Initialize (code-owned, idempotent)

```bash
python3 $S/prose_recovery.py init "$TASK/instruction.md" \
  --state-dir "$PROSE_RUN" \
  --instruction "$TASK/instruction.md" \
  --task-toml "$TASK/task.toml" \
  --answer-key "$TASK/tests/answer_key.json"
python3 $S/prose_recovery.py brief "$PROSE_RUN"
```

`init` snapshots the source, writes `critical_facts.json` (protected blocks,
tokens, output names, sheet names, notes speakers) and `writer_brief.md`.
It never reads answer values into the brief.

## 2. Write (at most two attempts)

Each attempt is a fresh `generalPurpose` subagent, model `gpt-5.6-sol-high`.
Give it three files and nothing else: `$PROSE_RUN/source.snapshot.md`,
`$PROSE_RUN/writer_brief.md`, and the prompt below. It writes one file,
`$PROSE_RUN/attempt-01/body.md` (then `attempt-02`). It must not touch the
task directory.

```text
You are a Director at a finance firm writing a one-page brief for a junior
colleague. Rewrite the supplied task instruction as continuous prose in the
style of a GDPval task prompt. The reader is an Analyst or Associate.

VOICE
- Open with "You are an Analyst..." or "You are an Associate..." and say what
  the team does and who assigns the work. Ground the firm and the work in the
  sheet names and output names in the brief: use them to pick the setting,
  not to recite. If they do not support a specific setting, say "a finance
  team" and stop there. Never invent deadlines, committee dates, client
  names, or numbers.
- Second paragraph: the question, asked the way the senior colleague would
  ask it and named by the first curated output ("the Director wants to know
  what return the deal makes", "how much term debt the vehicle draws"). Do
  not write "The business question is". Then the workbook: its file name
  and, if it helps, the tab the graded figures sit on; when they sit on
  several tabs, point to the table instead of naming them. Never list the
  workbook's tabs; the reader opens the file and sees them. Then that the
  inputs, historicals and layout are in place and the calculations have not
  been carried out yet, and that Python packages such as openpyxl may be
  installed to read it.
- The workbook is a template to complete, not a file that was emptied. Never
  write: removed, stripped, blank cells / cells are blank, cleared, no
  formulas, remaining drivers/inputs, identified inputs, sheet structure,
  mock, masked, delivered file, artifact, rebuild, reconstruct, restore,
  recreate, reverse-engineer, original model, "build a spreadsheet".
  Describe the work as completing the analysis and reporting the figures.
- People are only: Analyst, Associate, VP, Director, Managing Director. If the
  brief lists titles that speak in additional-assumptions.md, use only those,
  and the reader is the junior who speaks there (Analyst if none does). No
  other title anywhere: no partner, CFO, banker, head of, portfolio manager.
- Research service, when the brief says one is present: one paragraph. The
  five categories (market rates, tax rates, macro assumptions, contractual
  terms, opening balances) are not in the sheets and are only available from
  the service, an MCP server at the URL given (streamable HTTP transport).
  Records come in releases; use the latest release of each one. Convert each
  value into the units and scale of the sheet it feeds (rates come back in
  percent, sheets may store decimals; monetary amounts state their unit).
  Then both code blocks, verbatim. Leave out the tool list, "five tools",
  paging, "at least two filter dimensions", and the superseded_by mechanics;
  the server tells the agent all of that itself. The brief marks these
  sentences droppable. Anything else in the source's research prose (extra
  list items, task-specific filter advice) is required.
- If the source has a "Workbook disclosure" section, keep its bullet lines
  exactly, wrapped as its own paragraph (blank line before the start marker
  and after the end marker; each marker alone on its line):
  <!-- workbook-notes:start -->
  one lead-in sentence in your own words (no "original model", no "delivered file")
  - the bullets, verbatim
  <!-- workbook-notes:end -->
- If the source names additional-assumptions.md, keep one paragraph with
  this wording: "Before you begin, read `additional-assumptions.md` in your
  working directory. It is a record of a conversation between colleagues
  that provides additional guidance on how several rows in this model should
  be worked out; follow the assumptions it records." The source says the
  reader must follow those assumptions; keep that imperative ("follow").
- Close with the deliverable, in this order: the JSON object at the path the
  brief gives, keyed by cell reference exactly as in the table; the example
  code block; then one last sentence giving the rule as a Director would:
  each figure as the model produces it, in the units and scale of its sheet,
  unrounded, percentages as the cell holds them. The rule must follow the
  code block. The total count of values is optional when every table row
  lists one cell; if kept, one short aside. When a row lists several cells
  the brief says so, and one sentence giving the count ("15 values in all,
  one per cell listed") is required: it is the only place the reader learns
  to report one value per cell, not one per row.
- If the source carries the analyst-hints intro ("... deliberately provide
  no formulas ..."), it is prose, not a protected block: rephrase it as
  guidance on how the rows are meant to be worked out. "no formulas" is
  banned.

HARD RULES (a validator rejects the file otherwise)
1. No Markdown headings of any level.
2. Every fenced code block, every table row, and every list item in the
   source appears once, verbatim, in the same order. One exception: the four
   research tool-list items may be omitted as a group. Never keep some, edit
   one, or shorten one.
3. Every cell reference and URL appears the same number of times. Numbers
   and inline-code spans keep their counts too, except tokens the brief marks
   "may be dropped" (they live only in droppable sentences). Add no new
   numbers. New inline code only for sheet names from the brief,
   additional-assumptions.md, and openpyxl; at most three sheet names beyond
   the ones the graded cells sit on (`sheet_list_dump`).
4. Do not add facts, hints, formulas, assumptions, or answer values.
5. Keep "working directory", "only" for the research-service inputs,
   "install", "key", "units", "scale", "percentage", a no-rounding word
   (unrounded / do not round), that the calculations are not yet done, and,
   when a research service exists, "latest release" (or equivalent). The
   notes pointer, when present, says "follow".
6. Output the full instruction as one Markdown body. No preamble, no notes.
```

The brief (`writer_brief.md`) carries the per-token budget, the droppable
sentences, and model paragraphs for the research service, the disclosure
lead-in, and the deliverable. Writers that follow the brief pass on the
first attempt; writers that copy the source's mechanics sentences fail on
`banned_framing`.

## 3. Validate, review, apply

```bash
python3 $S/prose_recovery.py submit "$PROSE_RUN" --body "$PROSE_RUN/attempt-01/body.md"
```

Exit `2` means the validator rejected it; `validation.json` names the reason
codes. If `state.json` says `retry_ready`, launch attempt two from the same
source. Never feed the failed body to the next writer. If it says
`exhausted`, `instruction.md` is still the untouched source: remove
`$PROSE_RUN` and start again from init.

Then read the candidate yourself and check clause by clause against the
source: same scope, same permissions, same prohibitions, no new claims, the
role fits the workbook, the first curated output is the question. Two things
the validator cannot see: it counts numerals, not meanings, so a dropped
required number ("15 values") is masked when the same numeral appears
elsewhere ("15 rows"); check every count and every number in place. And it
accepts any pointer paragraph that says "follow"; check that the pointer
still tells the reader to apply the notes, not just read them. Record the
verdict:

```bash
python3 $S/prose_recovery.py accept "$PROSE_RUN" --reviewer main-agent \
  --message "Clause-by-clause review passed"
# or
python3 $S/prose_recovery.py reject "$PROSE_RUN" --reason-code semantic_mismatch \
  --message "<what was added, dropped, or weakened>"
```

```bash
python3 $S/prose_recovery.py apply "$PROSE_RUN"
python3 $S/prose_recovery.py verify-applied "$PROSE_RUN"
```

`apply` replaces `instruction.md` and `task.toml` as one journaled
transaction (backups and `journal.json` under
`$PROSE_RUN/apply-transaction/`). An interrupted apply rolls back on the next
command. `task.toml` gains `[metadata.prose_instruction]` and every
`instruction_sha256` line is refreshed. If `tests/dialogue-applied.json`
exists, its hash is refreshed and this skill is added to `do_not_rerun`.

## Retrofit notes

- The source may already have `## Additional assumptions`. Keep the pointer
  sentence in prose with the exact colleague-conversation wording.
- Do not restore `## Workbook disclosure` or the disclosure bullets on a task
  whose `tests/dialogue-applied.json` exists.
- After apply, promotion checks use
  `prose_recovery.py verify-metadata --instruction ... --task-toml ...`.

## Done when

- `verify-applied` reports `valid: true`, `applied: true`
- `instruction.md` has no line starting with `#`
- it opens with `You are an Analyst` or `You are an Associate`
- first curated output is named in the prose before the table
- no "removed", "blank cells", "no formulas", "remaining inputs", "mock",
  tool list, paging or filter-count sentences; the research paragraph says
  "latest release"; the deliverable rule follows the JSON code block; the
  notes pointer says "follow"; a multi-cell row keeps the value count
- `task.toml` has `[metadata.prose_instruction]` with matching hashes and
  `prompt_version = "gdpval-prose-instruction-v2"` (tasks applied under v1
  fail `verify-metadata` and must be re-run through this skill)
- `$PROSE_RUN` keeps the source snapshot, facts, brief, both attempt dirs,
  reviews, and the apply journal

For a worked before/after, read [examples.md](examples.md).
