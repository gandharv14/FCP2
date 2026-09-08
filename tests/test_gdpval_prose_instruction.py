"""Tests for the gdpval-prose-instruction skill and its pipeline hooks."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".cursor" / "skills" / "gdpval-prose-instruction"
SCRIPTS = SKILL / "scripts"
AA_SCRIPTS = ROOT / ".cursor" / "skills" / "additional-assumptions-dialogue" / "scripts"
DISCLOSE_SCRIPTS = ROOT / ".cursor" / "skills" / "task-disclosure" / "scripts"
for path in (SCRIPTS, AA_SCRIPTS, DISCLOSE_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FACTS = _load("prose_facts", SCRIPTS / "extract_critical_facts.py")
VALIDATOR = _load("prose_validator", SCRIPTS / "validate_prose_rewrite.py")
RECOVERY = _load("prose_recovery_mod", SCRIPTS / "prose_recovery.py")
import aa_lib  # noqa: E402
import validate_dialogue as AA  # noqa: E402
import disclose as DISCLOSE  # noqa: E402

PROMPT_VERSION = "gdpval-prose-instruction-v2"

TOOL_LIST = """\
- `list_sources` - the organizations and data platforms available
- `list_datasets` - structured datasets and their filter dimensions
- `search_documents` / `fetch_document` - keyword search over source
  documents, then fetch one by id
- `query_records` - filter dataset records by entity, metric, period,
  scenario, basis, unit and status
"""
TASK_ITEM = "- `list_periods` - the fiscal periods this deal's datasets cover\n"
TABLE = """\
| # | figure | cells |
| --- | --- | --- |
| 1 | Equity IRR | `Returns!C10` |
| 2 | Exit MOIC | `Returns!C12` |
"""

# Faithful to xl_output_task.py's template plus one task-specific list item
# and one task-specific research sentence, so the tests can prove that only
# the template mechanics become droppable.
SOURCE = """\
A colleague has sent you a leveraged buyout returns model with every calculated figure stripped out, and this time the externally-sourced input assumptions have been removed from the sheets as well: market rates, tax rates, macro assumptions, contractual terms and opening balances have to be researched through the data service described below before anything can be computed. Rebuild the calculations from the remaining inputs plus your research, and report the 2 headline figures the model exists to produce, such as Equity IRR, Exit MOIC. The full list of figures and the cells they belong in is given below.

## Input

The workbook `0001-inputs.xlsx` is in your working directory. Every cell the model is meant
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

""" + TOOL_LIST + TASK_ITEM + """
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
their unit on the record). Query the `LBO Base` scenario for every record.

Any MCP client works, for example from Python:

```
pip install fastmcp
```

## What to compute

""" + TABLE + """
## Workbook disclosure

These are choices the original model made that the delivered file no longer shows.
They describe how the model works, not the figures you are asked to report.

- `Returns!C8`: For Returns!C8 on the row labelled "Exit proceeds", multiply the row labelled "Exit EBITDA" by the row labelled "Exit multiple".

## Output

Write a JSON object to `/app/answers.json` mapping each cell reference above to
the value the model computes for it, keyed exactly as written in the table:

```
{"Returns!C10": <number>, "Returns!C12": <number>}
```

Report 2 values in total, one per cell listed. Give each number exactly as the
model computes it, in the same units and scale as the sheet it sits on - do not
round, rescale or convert percentages.
"""

RESEARCH_PARAGRAPH = (
    "The research data service runs as an MCP server at `http://mcp-server:8000/mcp` "
    "(streamable HTTP transport). Records come in releases; use the latest release of each one. "
    "Convert each value into the units and scale of the sheet it feeds: rates come back in percent "
    "while the sheets may store decimal fractions, and monetary amounts state their unit on the record. "
    "Query the `LBO Base` scenario for every record."
)

# v2 voice: template framing, no tool list, no paging, natural deliverable.
CANDIDATE = """\
You are an Analyst at a private equity firm. The team buys control positions in founder-owned businesses, and you support the Director on each deal with the returns model and the memo that goes to the Managing Director. Work on each transaction is assigned to you by the Director.

The Director wants to know what the Equity IRR on this acquisition is, together with the Exit MOIC. The working file, `0001-inputs.xlsx`, is in your working directory. The drivers, historicals and layout are in place; the calculations have not been carried out yet. You may install Python packages (for example `openpyxl`) to read it.

The market rates, tax rates, macro assumptions, contractual terms and opening balances the model needs are not in the sheets; they are only available from the firm's research data service. Complete the necessary analysis from the inputs that are there plus what you retrieve, and report the 2 headline figures the Director is waiting on.

""" + RESEARCH_PARAGRAPH + """ The service also offers one deal-specific tool:

""" + TASK_ITEM + """
Any MCP client works, for example from Python:

```
pip install fastmcp
```

The figures the Director needs, and the cells they belong in, are:

""" + TABLE + """
<!-- workbook-notes:start -->
One modelling choice is not obvious from the workbook, so the Director has written it down. It describes how a row is worked out, not a figure you are asked to report.

- `Returns!C8`: For Returns!C8 on the row labelled "Exit proceeds", multiply the row labelled "Exit EBITDA" by the row labelled "Exit multiple".
<!-- workbook-notes:end -->

Put your answers in a JSON object at `/app/answers.json`, keyed by cell reference exactly as written in the table above:

```
{"Returns!C10": <number>, "Returns!C12": <number>}
```

Give each figure as the model produces it, in the units and scale of the sheet it sits on, unrounded; leave percentages as the cell holds them.
"""

TASK_TOML = """\
schema_version = "1.4"

[task]
name = "fcp/0001-outputs"

[metadata]
workbook = "0001"
output_names = ["Equity IRR", "Exit MOIC"]
n_answer_cells = 2
taxonomy_primary = "lbo_returns"

[metadata.naturalizer]
model = "gpt-5.6-sol-high"
endpoint = "cursor-subagent"
attempts = 0
naturalized = false
fallback_reason = ""
prompt_version = "finance-instruction-naturalizer-v1"
source_sha256 = ""
instruction_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

[agent]
timeout_sec = 100
"""

ANSWER_KEY = {"targets": {"Returns!C10": 0.2345, "Returns!C12": 2.7}}

NOTES = """\
**Analyst:** How should I size Exit proceeds?

**VP:** Multiply the row labelled "Exit EBITDA" by the row labelled "Exit multiple".
"""


class TaskFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gdpval-prose-"))
        self.task = self.tmp / "0001-outputs"
        (self.task / "environment").mkdir(parents=True)
        (self.task / "tests").mkdir()
        self.instruction = self.task / "instruction.md"
        self.task_toml = self.task / "task.toml"
        self.instruction.write_text(SOURCE, encoding="utf-8")
        self.task_toml.write_text(TASK_TOML, encoding="utf-8")
        (self.task / "tests" / "answer_key.json").write_text(json.dumps(ANSWER_KEY))
        self.recovery = self.tmp / "run"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def validate(self, candidate: str, source: str = SOURCE) -> dict:
        return VALIDATOR.validate(source, candidate, ANSWER_KEY, task_dir=self.task)

    def reason(self, candidate: str, source: str = SOURCE) -> str:
        with self.assertRaises(VALIDATOR.ProseValidationError) as ctx:
            self.validate(candidate, source)
        return ctx.exception.reason_codes[0]


class FactsTests(TaskFixture):
    def test_manifest_classifies_blocks_and_mode(self):
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        self.assertEqual(facts["version"], "gdpval-prose-facts-v2")
        self.assertEqual(facts["mode"], "pipeline_with_disclosure")
        self.assertTrue(facts["has_disclosure"])
        self.assertTrue(facts["has_research_service"])
        self.assertEqual(facts["output_names"], ["Equity IRR", "Exit MOIC"])
        self.assertEqual(facts["n_cells"], 2)
        kinds = [(b["kind"], b["section"]) for b in facts["protected_blocks"]]
        self.assertIn(("list", "workbook disclosure"), kinds)
        self.assertIn(("table", "what to compute"), kinds)
        self.assertIn(("fence", "output"), kinds)
        self.assertEqual(facts["prose_tokens"]["numbers"], {"2": 2, "5": 1})
        self.assertNotIn("0.2345", json.dumps(facts))

    def test_tool_list_is_an_optional_group_and_task_item_is_not(self):
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        grouped = [b["id"] for b in facts["protected_blocks"] if b.get("optional_group") == "tool_list"]
        self.assertEqual(grouped, ["list-1", "list-2", "list-3", "list-4"])
        task_item = next(b for b in facts["protected_blocks"] if "list_periods" in b["text"])
        self.assertIsNone(task_item.get("optional_group"))
        # Heading-free source: detection is by item text, not section.
        headless = SOURCE.replace("## Research data service\n\n", "")
        facts2 = FACTS.extract_facts(headless.encode("utf-8"), self.task)
        self.assertEqual(
            sum(1 for b in facts2["protected_blocks"] if b.get("optional_group") == "tool_list"), 4
        )

    def test_droppable_spans_are_sentence_bounded(self):
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        spans = " ".join(facts["droppable_spans"])
        self.assertIn("exposes five tools:", spans)
        self.assertIn("at most 5 rows per page", spans)
        self.assertIn("Report 2 values in total, one per cell listed.", spans)
        self.assertIn("has been checked to contain no formulas", spans)
        # The task-specific sentence right after the mechanics stays required.
        self.assertNotIn("LBO Base", spans)
        self.assertEqual(facts["optional_tokens"]["numbers"], {"2": 1, "5": 1})
        self.assertEqual(
            set(facts["optional_tokens"]["inline_code"]),
            {"`query_records`", "`next_cursor`", "`superseded_by`"},
        )
        self.assertEqual(facts["prose_tokens"]["inline_code"]["`LBO Base`"], 1)
        self.assertNotIn("`LBO Base`", facts["optional_tokens"]["inline_code"])

    def test_anchors_use_template_framing(self):
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        anchors = facts["required_anchors"]
        self.assertTrue(anchors["calculations_pending"])
        self.assertTrue(anchors["latest_release"])
        for gone in ("blank_state", "no_formulas", "inputs_present"):
            self.assertNotIn(gone, anchors)
        plain = (
            SOURCE.split("## Research data service")[0] + "## What to compute" + SOURCE.split("## What to compute")[1]
        ).replace("research data service", "market data desk")
        plain_facts = FACTS.extract_facts(plain.encode("utf-8"), self.task)
        self.assertFalse(plain_facts["required_anchors"]["latest_release"])

    def test_n_cells_falls_back_to_task_toml_before_table(self):
        wide_table = TABLE.replace(
            "| 2 | Exit MOIC | `Returns!C12` |",
            "| 2 | Exit MOIC | `Returns!C12`, `Returns!D12`, `Returns!E12` |",
        )
        source = SOURCE.replace(TABLE, wide_table).replace(
            "Report 2 values in total, one per cell listed. ", ""
        )
        self.task_toml.write_text(TASK_TOML.replace("n_answer_cells = 2", "n_answer_cells = 4"), encoding="utf-8")
        facts = FACTS.extract_facts(source.encode("utf-8"), self.task)
        self.assertEqual(facts["n_cells"], 4)
        self.assertEqual(FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)["n_cells"], 2)

    def test_count_sentence_stays_required_on_multi_cell_rows(self):
        # One cell per row: the count sentence and its numeral are droppable.
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        self.assertFalse(facts["count_required"])
        self.assertIn("Report 2 values in total, one per cell listed.", facts["droppable_spans"])
        self.assertEqual(facts["optional_tokens"]["numbers"].get("2"), 1)
        self.assertIn("total count of values is optional", FACTS.writer_brief(facts))
        # Several cells on a row: the count is the only "one per cell" signal.
        wide_table = TABLE.replace(
            "| 2 | Exit MOIC | `Returns!C12` |",
            "| 2 | Exit MOIC | `Returns!C12`, `Returns!D12`, `Returns!E12` |",
        )
        source = SOURCE.replace(TABLE, wide_table).replace("Report 2 values", "Report 4 values")
        facts = FACTS.extract_facts(source.encode("utf-8"), self.task)
        self.assertTrue(facts["count_required"])
        self.assertEqual(facts["n_cells"], 4)
        self.assertNotIn("Report 4 values in total, one per cell listed.", facts["droppable_spans"])
        self.assertNotIn("4", facts["optional_tokens"]["numbers"])
        brief = FACTS.writer_brief(facts)
        self.assertIn("4 values in all, one per cell listed", brief)
        self.assertNotIn("total count of values is optional", brief)

    def test_checked_and_contains_variant_is_droppable(self):
        variant = SOURCE.replace(
            "The artifact\nhas been checked to contain no formulas",
            "The artifact\nhas been checked and contains no formulas",
        )
        self.assertNotEqual(variant, SOURCE)
        spans = FACTS.extract_facts(variant.encode("utf-8"), self.task)["droppable_spans"]
        self.assertTrue(any("checked and contains no formulas" in s for s in spans), spans)

    def test_brief_lists_budget_and_never_contains_answer_values(self):
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        brief = FACTS.writer_brief(facts)
        self.assertIn("You are", brief)
        self.assertIn("may be dropped entirely", brief)
        self.assertIn("`LBO Base`: required exactly 1", brief)
        self.assertIn("omit ALL of list-1, list-2, list-3, list-4", brief)
        self.assertIn("latest", brief)
        self.assertIn("rule comes after the fence", brief)
        self.assertNotIn("0.2345", brief)
        self.assertNotIn("2.7", brief)


class ValidatorTests(TaskFixture):
    def test_valid_candidate_passes(self):
        report = self.validate(CANDIDATE)
        self.assertTrue(report["valid"])
        self.assertEqual(report["mode"], "pipeline_with_disclosure")
        self.assertEqual(report["prompt_version"], PROMPT_VERSION)

    def test_rejects_heading(self):
        self.assertEqual(
            self.reason(CANDIDATE.replace("The research data service runs", "## Research\n\nThe research data service runs")),
            "heading_present",
        )

    def test_rejects_rebuild_and_reconstruct(self):
        for word in ("Rebuild", "Reconstruct", "Restore"):
            self.assertEqual(
                self.reason(CANDIDATE.replace("Complete the necessary analysis", "%s the model" % word)),
                "banned_framing",
            )
        self.assertEqual(
            self.reason(CANDIDATE.replace("is not obvious from the workbook", "the original model made")),
            "banned_framing",
        )

    def test_rejects_gutted_workbook_framing(self):
        anchor = "The drivers, historicals and layout are in place; the calculations have not been carried out yet."
        self.assertIn(anchor, CANDIDATE)
        for phrase in (
            "Every calculated cell is blank; the calculations have not been carried out yet.",
            "The removed input assumptions are gone; the calculations have not been carried out yet.",
            "The file carries no formulas; the calculations have not been carried out yet.",
            "The remaining drivers are present; the calculations have not been carried out yet.",
            "The workbook was reverse-engineered; the calculations have not been carried out yet.",
            "The masked cells are yours; the calculations have not been carried out yet.",
            "The delivered file is yours; the calculations have not been carried out yet.",
            "The values were cleared out; the calculations have not been carried out yet.",
        ):
            self.assertEqual(self.reason(CANDIDATE.replace(anchor, phrase)), "banned_framing", phrase)
        self.assertEqual(
            self.reason(CANDIDATE.replace("an MCP server at", "a mock MCP server at")),
            "banned_framing",
        )

    def test_legitimate_blank_and_output_names_are_not_banned(self):
        anchor = "The drivers, historicals and layout are in place; the calculations have not been carried out yet."
        with_blank = CANDIDATE.replace(anchor, anchor + " Treat blank input cells as zero.")
        self.assertTrue(self.validate(with_blank)["valid"])
        source = SOURCE.replace("Exit MOIC", "Remaining Equity Value")
        candidate = CANDIDATE.replace("Exit MOIC", "Remaining Equity Value")
        self.assertTrue(self.validate(candidate, source)["valid"])
        source = SOURCE.replace("Exit MOIC", "Cleared Capacity Revenues")
        candidate = CANDIDATE.replace("Exit MOIC", "Cleared Capacity Revenues")
        self.assertTrue(self.validate(candidate, source)["valid"])

    def test_droppable_tokens_may_be_dropped_but_not_added(self):
        # CANDIDATE already drops 5, next_cursor, superseded_by, query_records.
        self.assertTrue(self.validate(CANDIDATE)["valid"])
        # The preamble count is required (source has two 2s, one droppable).
        self.assertEqual(
            self.reason(CANDIDATE.replace("report the 2 headline figures", "report the headline figures")),
            "token_mismatch",
        )
        # A kept count aside is allowed, up to the source total.
        aside = CANDIDATE.replace("Put your answers", "That is 2 numbers in all. Put your answers")
        self.assertTrue(self.validate(aside)["valid"])
        self.assertEqual(
            self.reason(aside.replace("That is 2 numbers", "That is 2 numbers, 2 cells")),
            "token_mismatch",
        )
        # Dropped mechanics may be kept verbatim instead.
        kept = CANDIDATE.replace(
            "Records come in releases; use the latest release of each one.",
            "Records come in releases; use the latest release of each one (a row whose `superseded_by` field is set has been replaced). "
            "`query_records` returns at most 5 rows per page; follow `next_cursor`.",
        )
        self.assertTrue(self.validate(kept)["valid"])
        self.assertEqual(self.reason(kept.replace("at most 5 rows", "at most 5 rows or 5 columns")), "token_mismatch")

    def test_task_specific_research_sentence_is_required(self):
        self.assertEqual(
            self.reason(CANDIDATE.replace("Query the `LBO Base` scenario for every record. ", "")),
            "token_mismatch",
        )

    def test_tool_list_group_is_all_or_nothing(self):
        lead_in = " The service also offers one deal-specific tool:\n\n"
        with_tools = CANDIDATE.replace(lead_in, " It exposes these tools:\n\n" + TOOL_LIST)
        self.assertTrue(self.validate(with_tools)["valid"])
        three = with_tools.replace("- `list_datasets` - structured datasets and their filter dimensions\n", "")
        self.assertEqual(self.reason(three), "optional_group_partial")
        edited = with_tools.replace("the organizations and data platforms available", "the sources")
        self.assertEqual(self.reason(edited), "optional_group_partial")
        lone = CANDIDATE.replace(
            lead_in,
            " One tool matters most:\n\n- `query_records` - filter dataset records by entity, metric, period,\n  scenario, basis, unit and status\n",
        )
        self.assertEqual(self.reason(lone), "optional_group_partial")
        # Stray items of any list shape are caught, not only "- `tool`".
        numbered = CANDIDATE.replace(lead_in, " Start here:\n\n1. `list_sources` - the sources\n\n")
        self.assertEqual(self.reason(numbered), "optional_group_partial")
        prefixed = CANDIDATE.replace(lead_in, " Start here:\n\n- The `list_sources` tool lists the sources\n\n")
        self.assertEqual(self.reason(prefixed), "optional_group_partial")
        # A kept group must also keep its order.
        first_two = TOOL_LIST.split("\n")[0:2]
        swapped = TOOL_LIST.replace("\n".join(first_two), "\n".join(reversed(first_two)))
        self.assertNotEqual(swapped, TOOL_LIST)
        reordered = CANDIDATE.replace(lead_in, " It exposes these tools:\n\n" + swapped)
        self.assertEqual(self.reason(reordered), "optional_group_partial")

    def test_sheet_names_are_grounding_not_a_tab_list(self):
        # The fixture has no workbook, so inject the sheet list the extractor
        # would have read. Returns holds the graded cells; the rest are extras.
        sheets = ["Returns", "Inputs", "Debt", "Tax", "Opex", "Capex", "Summary"]
        facts = FACTS.extract_facts(SOURCE.encode("utf-8"), self.task)
        facts["workbook_sheets"] = sheets
        facts["allowed_extra_inline_code"] = sorted(
            set(facts["allowed_extra_inline_code"]) | {"`%s`" % s for s in sheets}
        )

        def run(candidate):
            return VALIDATOR.validate(SOURCE, candidate, ANSWER_KEY, facts=facts)

        def reason(candidate):
            with self.assertRaises(VALIDATOR.ProseValidationError) as ctx:
                run(candidate)
            return ctx.exception.reason_codes[0]

        anchor = "The working file, `0001-inputs.xlsx`, is in your working directory."
        self.assertIn(anchor, CANDIDATE)
        self.assertTrue(run(CANDIDATE)["valid"])
        # Answer tab plus three extras: fine.
        three = CANDIDATE.replace(
            anchor,
            anchor + " The returns sit on `Returns`, fed by `Inputs`, `Debt` and `Tax`.",
        )
        self.assertTrue(run(three)["valid"])
        # Four extras beyond the answer tab: a recital.
        four = CANDIDATE.replace(
            anchor,
            anchor + " It holds the tabs `Returns`, `Inputs`, `Debt`, `Tax`, and `Opex`.",
        )
        self.assertEqual(reason(four), "sheet_list_dump")
        # Repeating a sheet name does not count twice.
        repeated = CANDIDATE.replace(
            anchor,
            anchor + " `Inputs` feeds `Debt`, `Debt` feeds `Tax`, and `Tax` feeds `Inputs`.",
        )
        self.assertTrue(run(repeated)["valid"])
        # A sheet name the source already quotes in prose is not new.
        source_names = SOURCE.replace(
            "The workbook `0001-inputs.xlsx` is in your working directory.",
            "The workbook `0001-inputs.xlsx` is in your working directory; start on `Summary`.",
        )
        facts_named = FACTS.extract_facts(source_names.encode("utf-8"), self.task)
        facts_named["workbook_sheets"] = sheets
        facts_named["allowed_extra_inline_code"] = facts["allowed_extra_inline_code"]
        named = CANDIDATE.replace(
            anchor, anchor + " Start on `Summary`; `Inputs`, `Debt` and `Tax` feed it."
        )
        self.assertTrue(
            VALIDATOR.validate(source_names, named, ANSWER_KEY, facts=facts_named)["valid"]
        )
        # The reason code lets the run continue to attempt two.
        self.assertIn("sheet_list_dump", RECOVERY.RETRYABLE_REASON_CODES)
        self.assertIn("optional_group_partial", RECOVERY.RETRYABLE_REASON_CODES)
        # Brief no longer hands the writer the sheet list as permission.
        brief = FACTS.writer_brief(facts)
        self.assertIn("grounding only", brief)
        self.assertIn("Do not list the workbook's tabs", brief)
        self.assertNotIn("New inline code only for: `", brief)

    def test_rejects_dropped_or_reordered_protected_blocks(self):
        self.assertEqual(self.reason(CANDIDATE.replace(TASK_ITEM, "")), "protected_block_missing")
        self.assertEqual(
            self.reason(CANDIDATE.replace("| 2 | Exit MOIC | `Returns!C12` |\n", "")),
            "protected_block_missing",
        )
        moved = CANDIDATE.replace(TABLE, "").replace("The Director wants to know", TABLE + "\nThe Director wants to know")
        self.assertEqual(self.reason(moved), "protected_block_order_changed")

    def test_rejects_foreign_titles_and_missing_you_are(self):
        self.assertEqual(self.reason(CANDIDATE.replace("You are an Analyst", "You are an engineer")), "persona_title_invalid")
        self.assertEqual(self.reason(CANDIDATE.replace("You are an Analyst", "You are an intern")), "persona_title_invalid")
        self.assertEqual(self.reason(CANDIDATE.replace("You are an Analyst at", "The Analyst at")), "persona_title_invalid")

    def test_rejects_new_numbers_cells_and_lost_anchors(self):
        self.assertEqual(
            self.reason(CANDIDATE.replace("assigned to you by the Director.", "assigned to you by the Director within 3 days.")),
            "token_mismatch",
        )
        self.assertEqual(
            self.reason(CANDIDATE.replace("what the Equity IRR on this", "what the Equity IRR in `Returns!C10` on this")),
            "cell_refs_changed",
        )
        self.assertEqual(
            self.reason(CANDIDATE.replace("the calculations have not been carried out yet", "the calculations are complete")),
            "semantic_anchor_lost",
        )
        self.assertEqual(
            self.reason(CANDIDATE.replace("use the latest release of each one", "pick the record you need")),
            "semantic_anchor_lost",
        )
        self.assertEqual(
            self.reason(CANDIDATE.replace("unrounded; leave percentages", "as is; leave percentages")),
            "semantic_anchor_lost",
        )
        self.assertEqual(
            self.reason(CANDIDATE.replace("market rates, tax rates,", "tax rates,")),
            "source_category_lost",
        )

    def test_anchor_regexes_accept_natural_phrasings(self):
        pending_ok = (
            "the calculations have not been carried out yet",
            "the model has not been run yet",
            "nothing has been computed yet",
            "the workings are yet to be done",
            "the outputs remain to be completed",
            "the figures have yet to be produced",
            "no calculation has been performed yet; the model has not been run",
            "the model still needs to be worked through",
            "the calculation cells still have to be worked through",
            "the calculated cells are not filled in",
            "the calculations are outstanding",
            "the calculations haven't been done",
            "the calculations remain to be run",
            "the schedules are still to be completed",
            "the outputs are not yet populated",
            "the model has yet to be built",
        )
        pending_bad = ("the calculations are complete", "every figure is already there", "the model is finished")
        for text in pending_ok:
            self.assertTrue(VALIDATOR.CALCULATIONS_PENDING_RE.search(text), text)
        for text in pending_bad:
            self.assertFalse(VALIDATOR.CALCULATIONS_PENDING_RE.search(text), text)
        release_ok = (
            "use the latest release of each record",
            "take the most recent release",
            "the newest release only",
            "a row that a later release has replaced is stale",
            "rows whose superseded_by field is set have been replaced",
            "use the current release",
            "only the unsuperseded release counts",
            "the latest version of each record",
            "stick to the most recent version",
            "ignore anything that has been replaced by a newer issue",
        )
        release_bad = ("use the data", "pick the right record", "filter carefully")
        for text in release_ok:
            self.assertTrue(VALIDATOR.LATEST_RELEASE_RE.search(text), text)
        for text in release_bad:
            self.assertFalse(VALIDATOR.LATEST_RELEASE_RE.search(text), text)

    def test_deliverable_rule_must_follow_the_fence(self):
        rule = "Give each figure as the model produces it, in the units and scale of the sheet it sits on, unrounded; leave percentages as the cell holds them.\n"
        moved = CANDIDATE.replace(rule, "").replace(
            "Put your answers in a JSON object",
            rule.strip() + " Put your answers in a JSON object",
        )
        self.assertEqual(self.reason(moved), "deliverable_not_last")

    def test_rejects_answer_value_leak(self):
        leaked = CANDIDATE.replace("the Director is waiting on", "the Director is waiting on (the IRR is about 0.2345)")
        self.assertIn(self.reason(leaked), {"token_mismatch", "answer_value_leak"})

    def test_rejects_missing_markers_and_lead_output(self):
        no_markers = CANDIDATE.replace("<!-- workbook-notes:start -->\n", "").replace("<!-- workbook-notes:end -->\n", "")
        self.assertEqual(self.reason(no_markers), "disclosure_markers_invalid")
        self.assertEqual(
            self.reason(CANDIDATE.replace("what the Equity IRR on this acquisition is, together with the Exit MOIC", "what the returns are")),
            "named_output_lost",
        )

    def test_rejects_markers_glued_to_neighbouring_prose(self):
        glued_start = CANDIDATE.replace(
            "| 2 | Exit MOIC | `Returns!C12` |\n\n<!-- workbook-notes:start -->",
            "| 2 | Exit MOIC | `Returns!C12` |\n<!-- workbook-notes:start -->",
        )
        self.assertEqual(self.reason(glued_start), "disclosure_markers_invalid")
        glued_end = CANDIDATE.replace(
            "<!-- workbook-notes:end -->\n\nPut your answers",
            "<!-- workbook-notes:end -->\nPut your answers",
        )
        self.assertEqual(self.reason(glued_end), "disclosure_markers_invalid")
        inline_end = CANDIDATE.replace(
            "<!-- workbook-notes:end -->\n\nPut your answers",
            "<!-- workbook-notes:end --> Put your answers",
        )
        self.assertEqual(self.reason(inline_end), "disclosure_markers_invalid")

    def test_rejects_invented_titles_without_notes(self):
        for phrase in ("the Partner", "the CFO", "a Portfolio Manager", "the head of M&A", "your boss", "a senior analyst"):
            self.assertEqual(
                self.reason(CANDIDATE.replace("assigned to you by the Director", "assigned to you by %s" % phrase)),
                "persona_title_invalid",
                phrase,
            )
        self.assertTrue(self.validate(CANDIDATE.replace("on each deal", "on each deal for the limited partners"))["valid"])
        source = SOURCE.replace("Exit MOIC", "Exit MOIC on CFO")
        candidate = CANDIDATE.replace("Exit MOIC", "Exit MOIC on CFO")
        self.assertTrue(self.validate(candidate, source)["valid"])
        self.assertEqual(
            self.reason(candidate.replace("assigned to you by the Director", "assigned to you by the CFO"), source),
            "persona_title_invalid",
        )

    def test_deliverable_path_comes_from_source(self):
        source = SOURCE.replace("/app/answers.json", "/workspace/answers.json")
        candidate = CANDIDATE.replace("/app/answers.json", "/workspace/answers.json")
        facts = FACTS.extract_facts(source.encode("utf-8"), self.task)
        self.assertEqual(facts["deliverable_path"], "/workspace/answers.json")
        self.assertIn("/workspace/answers.json", FACTS.writer_brief(facts))
        self.assertTrue(self.validate(candidate, source)["valid"])
        self.assertIn(self.reason(CANDIDATE, source), {"token_mismatch", "semantic_anchor_lost"})

    def test_large_table_is_kept_inline(self):
        rows = "".join(
            "| %d | Figure %d | `Sheet%d!C%d` |\n" % (i, i, i % 3, 10 + i) for i in range(1, 41)
        )
        base_rows = "| 1 | Equity IRR | `Returns!C10` |\n| 2 | Exit MOIC | `Returns!C12` |\n"
        example = ", ".join('"Sheet%d!C%d": <number>' % (i % 3, 10 + i) for i in range(1, 41))
        source = (
            SOURCE.replace(base_rows, rows)
            .replace("report the 2 headline figures", "report the 40 headline figures")
            .replace("such as Equity IRR, Exit MOIC", "such as Figure 1, Figure 2")
            .replace('{"Returns!C10": <number>, "Returns!C12": <number>}', "{%s}" % example)
            .replace("Report 2 values", "Report 40 values")
        )
        candidate = (
            CANDIDATE.replace(base_rows, rows)
            .replace("what the Equity IRR on this acquisition is, together with the Exit MOIC", "what Figure 1 on this acquisition is, together with Figure 2")
            .replace("report the 2 headline figures", "report the 40 headline figures")
            .replace('{"Returns!C10": <number>, "Returns!C12": <number>}', "{%s}" % example)
        )
        facts = FACTS.extract_facts(source.encode("utf-8"), self.task)
        self.assertEqual(facts["n_cells"], 40)
        self.assertTrue(self.validate(candidate, source)["valid"])
        self.assertEqual(self.reason(candidate.replace(rows.splitlines()[19] + "\n", ""), source), "protected_block_missing")

    def test_rejects_unsupported_inline_code(self):
        self.assertEqual(
            self.reason(CANDIDATE.replace("The working file", "The `Debt Schedule` tab feeds it. The working file")),
            "unsupported_inline_code",
        )

    def test_plain_source_without_disclosure_forbids_markers(self):
        plain_source = SOURCE.split("## Workbook disclosure")[0] + "## Output" + SOURCE.split("## Output")[1]
        plain_candidate = CANDIDATE.split("<!-- workbook-notes:start -->")[0] + CANDIDATE.split("<!-- workbook-notes:end -->\n")[1]
        self.assertTrue(self.validate(plain_candidate, plain_source)["valid"])
        self.assertEqual(self.reason(CANDIDATE, plain_source), "disclosure_markers_invalid")


class RoleAlignmentTests(TaskFixture):
    def setUp(self):
        super().setUp()
        (self.task / "environment" / "additional-assumptions.md").write_text(NOTES, encoding="utf-8")
        pointer = (
            "Before you begin, read `additional-assumptions.md` in your working directory. "
            "It is a record of a conversation between colleagues that provides additional guidance; "
            "follow the assumptions it records.\n\n"
        )
        self.source = SOURCE.replace(
            "## Workbook disclosure", "## Additional assumptions\n\nRead `additional-assumptions.md` in your working directory before you build.\n\n## Workbook disclosure"
        )
        self.candidate = CANDIDATE.replace("The research data service runs", pointer + "The research data service runs")

    def test_titles_must_come_from_notes_speakers(self):
        facts = FACTS.extract_facts(self.source.encode("utf-8"), self.task)
        self.assertEqual(facts["notes_speakers"], ["Analyst", "VP"])
        self.assertEqual(facts["mode"], "retrofit_after_assumptions")
        self.assertEqual(self.reason(self.candidate, self.source), "title_not_in_notes")
        aligned = (
            self.candidate.replace("the Managing Director", "the VP")
            .replace("support the Director on each deal", "support the VP on each deal")
            .replace("assigned to you by the Director", "assigned to you by the VP")
            .replace("The Director wants", "The VP wants")
            .replace("the Director is waiting on", "the VP is waiting on")
            .replace("The figures the Director needs", "The figures the VP needs")
            .replace("so the Director has written it down", "so the VP has written it down")
        )
        self.assertTrue(self.validate(aligned, self.source)["valid"])
        self.assertEqual(
            self.reason(aligned.replace("You are an Analyst", "You are an Associate"), self.source),
            "title_not_in_notes",
        )

    def test_reader_falls_back_to_analyst_when_no_junior_speaks(self):
        (self.task / "environment" / "additional-assumptions.md").write_text(
            "**VP:** Size Exit proceeds as EBITDA times multiple.\n\n**Director:** Agreed.\n",
            encoding="utf-8",
        )
        facts = FACTS.extract_facts(self.source.encode("utf-8"), self.task)
        self.assertEqual(facts["notes_speakers"], ["VP", "Director"])
        self.assertIn("No junior speaks in the notes: the reader is an Analyst", FACTS.writer_brief(facts))
        aligned = self.candidate.replace("the Managing Director", "the VP")
        self.assertTrue(self.validate(aligned, self.source)["valid"])
        self.assertEqual(
            self.reason(aligned.replace("You are an Analyst", "You are an Associate"), self.source),
            "title_not_in_notes",
        )

    def test_managing_director_wrapped_across_lines_is_not_director(self):
        (self.task / "environment" / "additional-assumptions.md").write_text(
            "**Analyst:** How?\n\n**Managing Director:** Multiply EBITDA by the multiple.\n",
            encoding="utf-8",
        )
        wrapped = (
            self.candidate.replace("Director", "Managing Director")
            .replace("Managing Managing Director", "Managing Director")
            .replace("goes to the Managing Director", "goes to the Managing\nDirector")
        )
        self.assertIn("Managing\nDirector", wrapped)
        self.assertTrue(self.validate(wrapped, self.source)["valid"])

    def test_pointer_wording_is_required_when_notes_exist(self):
        aligned = self.candidate.replace("Director", "VP").replace("Managing VP", "VP")
        weak = aligned.replace(
            "It is a record of a conversation between colleagues that provides additional guidance;",
            "It has assumptions;",
        )
        self.assertEqual(self.reason(weak, self.source), "assumptions_pointer_lost")
        # The source says the assumptions must be followed; a pointer that
        # only says "provides additional guidance" loses that imperative.
        read_only = aligned.replace("; follow the assumptions it records.", ".")
        self.assertNotEqual(read_only, aligned)
        self.assertEqual(self.reason(read_only, self.source), "assumptions_pointer_lost")
        self.assertIn("follow the\n    assumptions it records", FACTS.writer_brief(
            FACTS.extract_facts(self.source.encode("utf-8"), self.task)
        ))

    def test_aa_rewrite_leaves_a_valid_prose_pointer_in_place(self):
        # After a retrofit the pointer sits where the writer put it (paragraph
        # 2 here, not directly before the deliverable). A second AA apply must
        # not move or reword it.
        aligned = self.candidate.replace("Director", "VP").replace("Managing VP", "VP")
        self.assertTrue(self.validate(aligned, self.source)["valid"])
        # Post-retrofit shape: the disclosure block is gone, the pointer stays.
        head, rest = aligned.split("<!-- workbook-notes:start -->", 1)
        aligned = head.rstrip("\n") + "\n\n" + rest.split("<!-- workbook-notes:end -->\n", 1)[1].lstrip("\n")
        self.assertEqual(AA.prose_instruction_faults(aligned), [])
        self.assertEqual(AA.rewrite_prose_instruction(aligned), aligned)
        # A faulty pointer is still replaced by the canonical one.
        weak = aligned.replace("It is a record of a conversation between colleagues", "It is a note")
        self.assertTrue(AA.prose_instruction_faults(weak))
        rewritten = AA.rewrite_prose_instruction(weak)
        self.assertNotEqual(rewritten, weak)
        self.assertEqual(AA.prose_instruction_faults(rewritten), [])


class RecoveryTests(TaskFixture):
    def init(self):
        return RECOVERY.init_recovery(
            self.instruction,
            self.recovery,
            instruction_path=self.instruction,
            task_toml_path=self.task_toml,
            answer_key_path=self.task / "tests" / "answer_key.json",
        )

    def approve(self):
        RECOVERY.accept_semantic_review(self.recovery, message="ok", reviewer="test")

    def test_init_submit_accept_apply_verify(self):
        state = self.init()
        self.assertEqual(state["mode"], "pipeline_with_disclosure")
        self.assertTrue((self.recovery / "writer_brief.md").is_file())
        report = RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        self.assertTrue(report["valid"])
        self.approve()
        applied = RECOVERY.apply_recovery(self.recovery)
        self.assertTrue(applied["applied"])
        self.assertEqual(self.instruction.read_text(encoding="utf-8"), CANDIDATE)
        toml_text = self.task_toml.read_text(encoding="utf-8")
        self.assertIn("[metadata.prose_instruction]", toml_text)
        self.assertIn('prompt_version = "%s"' % PROMPT_VERSION, toml_text)
        digest = RECOVERY.sha256_bytes(CANDIDATE.encode("utf-8"))
        self.assertEqual(toml_text.count('instruction_sha256 = "%s"' % digest), 2)
        verified = RECOVERY.verify_applied_recovery(self.recovery)
        self.assertTrue(verified["valid"])
        metadata = RECOVERY.verify_prose_metadata(self.instruction, self.task_toml)
        self.assertEqual(metadata["prompt_version"], PROMPT_VERSION)

    def test_v1_metadata_fails_verify_after_bump(self):
        self.init()
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        self.approve()
        RECOVERY.apply_recovery(self.recovery)
        stale = self.task_toml.read_text(encoding="utf-8").replace(PROMPT_VERSION, "gdpval-prose-instruction-v1")
        self.task_toml.write_text(stale, encoding="utf-8")
        with self.assertRaises(RECOVERY.RecoveryError) as ctx:
            RECOVERY.verify_prose_metadata(self.instruction, self.task_toml)
        self.assertIn("prompt_version", ctx.exception.details.get("fields", []))

    def test_invalid_submission_is_retryable_then_exhausts(self):
        self.init()
        bad = CANDIDATE.replace("Complete the necessary analysis", "Rebuild the model")
        first = RECOVERY.submit_attempt(self.recovery, bad)
        self.assertFalse(first["valid"])
        self.assertEqual(first["state"], "retry_ready")
        second = RECOVERY.submit_attempt(self.recovery, bad + "\n")
        self.assertEqual(second["state"], "exhausted")
        with self.assertRaises(RECOVERY.RecoveryError):
            RECOVERY.submit_attempt(self.recovery, CANDIDATE)

    def test_apply_requires_semantic_approval(self):
        self.init()
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        with self.assertRaises(RECOVERY.RecoveryError) as ctx:
            RECOVERY.apply_recovery(self.recovery)
        self.assertEqual(ctx.exception.reason_code, "semantic_review_required")

    def test_interrupted_apply_rolls_back_then_retries(self):
        self.init()
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        self.approve()
        original_task = self.task_toml.read_bytes()

        def interrupt(point):
            if point == "after_instruction_replace":
                raise RECOVERY.RecoveryError("simulated_interruption", point)

        with self.assertRaises(RECOVERY.RecoveryError):
            RECOVERY.apply_recovery(self.recovery, failpoint=interrupt)
        status = RECOVERY.recovery_status(self.recovery)
        self.assertEqual(status["status"], "validated")
        self.assertTrue(status["transaction_recovery"]["rolled_back"])
        self.assertEqual(self.instruction.read_text(encoding="utf-8"), SOURCE)
        self.assertEqual(self.task_toml.read_bytes(), original_task)

        result = RECOVERY.apply_recovery(self.recovery)
        repeated = RECOVERY.apply_recovery(self.recovery)
        self.assertTrue(result["applied"])
        self.assertTrue(repeated["idempotent"])

    def test_retrofit_refreshes_dialogue_marker(self):
        marker = self.task / "tests" / "dialogue-applied.json"
        marker.write_text(json.dumps({"applied": True, "instruction_sha256": "old", "do_not_rerun": []}))
        self.init()
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        self.approve()
        RECOVERY.apply_recovery(self.recovery)
        payload = json.loads(marker.read_text())
        self.assertEqual(payload["instruction_sha256"], RECOVERY.sha256_bytes(CANDIDATE.encode("utf-8")))
        self.assertIn("gdpval-prose-instruction", payload["do_not_rerun"])

    def test_marker_refresh_survives_interruption_after_commit(self):
        marker = self.task / "tests" / "dialogue-applied.json"
        marker.write_text(json.dumps({"applied": True, "instruction_sha256": "old", "do_not_rerun": []}))
        self.init()
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        self.approve()

        def interrupt(point):
            if point == "after_commit":
                raise RECOVERY.RecoveryError("simulated_interruption", point)

        with self.assertRaises(RECOVERY.RecoveryError):
            RECOVERY.apply_recovery(self.recovery, failpoint=interrupt)
        self.assertEqual(self.instruction.read_text(encoding="utf-8"), CANDIDATE)
        self.assertEqual(json.loads(marker.read_text())["instruction_sha256"], "old")

        verified = RECOVERY.verify_applied_recovery(self.recovery)
        self.assertTrue(verified["valid"])
        digest = RECOVERY.sha256_bytes(CANDIDATE.encode("utf-8"))
        payload = json.loads(marker.read_text())
        self.assertEqual(payload["instruction_sha256"], digest)
        self.assertIn("gdpval-prose-instruction", payload["do_not_rerun"])

        marker.write_text(json.dumps({"applied": True, "instruction_sha256": "old", "do_not_rerun": []}))
        self.assertTrue(RECOVERY.apply_recovery(self.recovery)["idempotent"])
        self.assertEqual(json.loads(marker.read_text())["instruction_sha256"], digest)

    def test_verify_metadata_ignores_comment_lines_inside_fences(self):
        source = SOURCE.replace("pip install fastmcp", "# install the client\npip install fastmcp")
        candidate = CANDIDATE.replace("pip install fastmcp", "# install the client\npip install fastmcp")
        self.instruction.write_text(source, encoding="utf-8")
        self.init()
        self.assertTrue(RECOVERY.submit_attempt(self.recovery, candidate)["valid"])
        self.approve()
        RECOVERY.apply_recovery(self.recovery)
        self.assertTrue(RECOVERY.verify_prose_metadata(self.instruction, self.task_toml)["valid"])
        self.assertTrue(RECOVERY.verify_applied_recovery(self.recovery)["valid"])

    def test_cli_round_trip(self):
        script = SCRIPTS / "prose_recovery.py"
        run = lambda *args: subprocess.run(  # noqa: E731
            [sys.executable, str(script), *args], capture_output=True, text=True
        )
        init = run(
            "init", str(self.instruction), "--state-dir", str(self.recovery),
            "--instruction", str(self.instruction), "--task-toml", str(self.task_toml),
            "--answer-key", str(self.task / "tests" / "answer_key.json"),
        )
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        body = self.recovery / "attempt-01" / "body.md"
        body.write_text(CANDIDATE, encoding="utf-8")
        submit = run("submit", str(self.recovery), "--body", str(body))
        self.assertEqual(submit.returncode, 0, submit.stdout)
        self.assertEqual(run("accept", str(self.recovery), "--message", "ok").returncode, 0)
        self.assertEqual(run("apply", str(self.recovery)).returncode, 0)
        self.assertEqual(run("verify-applied", str(self.recovery)).returncode, 0)
        meta = run("verify-metadata", "--instruction", str(self.instruction), "--task-toml", str(self.task_toml))
        self.assertEqual(meta.returncode, 0, meta.stdout)
        self.assertIn("You are", run("brief", str(self.recovery)).stdout)


class DownstreamTests(TaskFixture):
    def test_disclose_verify_reads_marker_block(self):
        section = DISCLOSE.disclosure_section_text(CANDIDATE)
        self.assertIn("`Returns!C8`", section)
        self.assertTrue(section.startswith(DISCLOSE.NOTES_MARKER_START))
        headed = DISCLOSE.disclosure_section_text(SOURCE)
        self.assertTrue(headed.startswith("## Workbook disclosure"))
        self.assertEqual(DISCLOSE.disclosure_section_text("no markers here"), "")

    def test_additional_assumptions_stays_heading_free(self):
        self.assertTrue(aa_lib.is_heading_free(CANDIDATE))
        self.assertFalse(aa_lib.is_heading_free(SOURCE))
        rewritten = AA.rewrite_instruction(CANDIDATE, self.task, {"claims": [1]})
        self.assertTrue(aa_lib.is_heading_free(rewritten))
        self.assertNotIn("workbook-notes", rewritten)
        self.assertNotIn("`Returns!C8`", rewritten)
        self.assertIn("record of a conversation between colleagues that provides additional guidance", rewritten)
        self.assertEqual(AA.instruction_faults(rewritten, {}, self.task), [])
        self.assertEqual(AA.identity_faults(CANDIDATE, rewritten), [])
        again = AA.rewrite_instruction(rewritten, self.task, {"claims": [1]})
        self.assertEqual(again, rewritten)

    def test_additional_assumptions_apply_keeps_prose_metadata_valid(self):
        RECOVERY.init_recovery(
            self.instruction, self.recovery,
            instruction_path=self.instruction, task_toml_path=self.task_toml,
            answer_key_path=self.task / "tests" / "answer_key.json",
        )
        RECOVERY.submit_attempt(self.recovery, CANDIDATE)
        RECOVERY.accept_semantic_review(self.recovery, message="ok", reviewer="test")
        RECOVERY.apply_recovery(self.recovery)
        rewritten = AA.rewrite_instruction(self.instruction.read_text(encoding="utf-8"), self.task, {"claims": [1]})
        self.instruction.write_text(rewritten, encoding="utf-8")
        digest = RECOVERY.sha256_bytes(rewritten.encode("utf-8"))
        self.task_toml.write_text(
            AA.update_instruction_hash(self.task_toml.read_text(encoding="utf-8"), digest), encoding="utf-8"
        )
        self.assertEqual(AA.prose_instruction_faults(rewritten), [])
        self.assertTrue(RECOVERY.verify_prose_metadata(self.instruction, self.task_toml)["valid"])
        # Re-extracting the heading-free result still finds the right cell count
        # without the "Report N values" sentence, via task.toml.
        facts = FACTS.extract_facts(rewritten.encode("utf-8"), self.task)
        self.assertEqual(facts["n_cells"], 2)
        self.assertEqual(facts["mode"], "retrofit_after_assumptions" if (self.task / "environment" / "additional-assumptions.md").is_file() else "pipeline_plain")

    def test_additional_assumptions_handles_workspace_deliverable_path(self):
        candidate = CANDIDATE.replace("/app/answers.json", "/workspace/answers.json")
        rewritten = AA.rewrite_instruction(candidate, self.task, {"claims": [1]})
        self.assertEqual(AA.prose_instruction_faults(rewritten), [])
        again = AA.rewrite_instruction(rewritten, self.task, {"claims": [1]})
        self.assertEqual(again, rewritten)
        no_pointer_no_markers = rewritten.replace(AA.prose_pointer() + "\n\n", "")
        self.assertNotIn("additional-assumptions.md", no_pointer_no_markers)
        with self.assertRaises(AA.DialogueError):
            AA.rewrite_instruction(no_pointer_no_markers, self.task, {"claims": [1]})

    def test_additional_assumptions_flags_regressions(self):
        rewritten = AA.rewrite_instruction(CANDIDATE, self.task, {"claims": [1]})
        with_heading = rewritten.replace("Put your answers", "## Output\n\nPut your answers")
        self.assertIn("prose instruction gained a heading", AA.prose_instruction_faults(with_heading))
        self.assertTrue(AA.instruction_faults(with_heading, {}, self.task))
        drifted = rewritten.replace("You are an Analyst", "You are an Associate")
        self.assertEqual(AA.identity_faults(CANDIDATE, drifted), ["prose outside the notes pointer was rewritten"])
        self.assertIn("gdpval-prose-instruction", aa_lib.DO_NOT_RERUN)

    def test_hash_refresh_touches_every_instruction_hash_line(self):
        text = VALIDATOR.updated_task_toml(
            TASK_TOML, {"source_sha256": "a" * 64, "candidate_sha256": "b" * 64}, 1
        )
        self.assertEqual(text.count('instruction_sha256 = "%s"' % ("b" * 64)), 2)
        refreshed = AA.update_instruction_hash(text, "c" * 64)
        self.assertEqual(refreshed.count('instruction_sha256 = "%s"' % ("c" * 64)), 2)


class PipelineFlagTests(unittest.TestCase):
    def test_build_one_branches_gate_13_on_flag(self):
        script = (ROOT / "build_one.sh").read_text(encoding="utf-8")
        self.assertIn("--gdpval-prose) GDPVAL_PROSE=1", script)
        self.assertIn('GDPVAL_PROSE="${HARBOR_GDPVAL_PROSE:-0}"', script)
        self.assertIn("gdpval-prose-instruction/scripts/prose_recovery.py", script)
        self.assertIn("naturalize-finance-task-instruction/scripts/naturalize_recovery.py", script)
        self.assertIn('runs/$WB-instruction-prose', script)
        self.assertIn('runs/$WB-instruction-naturalization', script)
        self.assertIn('python3 "$NAT_SCRIPT" verify-applied "$NAT_RUN"', script)
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "build_one.sh")]).returncode, 0)

    def test_skill_is_auto_invocable_and_documented(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("disable-model-invocation: true", text)
        self.assertIn("name: gdpval-prose-instruction", text)
        self.assertIn("record of a conversation between colleagues", text)
        self.assertIn("latest release", text)
        self.assertIn(PROMPT_VERSION, text)
        self.assertNotIn("keep the URL, the\n  tool list", text)
        self.assertLess(len(text.splitlines()), 500)
        harbor = (ROOT / ".cursor/skills/create-harbor-task/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--gdpval-prose", harbor)
        self.assertIn("prose_recovery.py", harbor)
        worker = (ROOT / "scripts/batch/recovery_worker.py").read_text(encoding="utf-8")
        self.assertIn("research tool-list items, which may be omitted", worker)
        examples = (SKILL / "examples.md").read_text(encoding="utf-8")
        after = examples.split("## After")[1]
        for banned in ("cells are blank", "no formulas", "five tools", "next_cursor", "delivered file"):
            self.assertNotIn(banned, after.split("## What changed")[0], banned)


if __name__ == "__main__":
    unittest.main()
