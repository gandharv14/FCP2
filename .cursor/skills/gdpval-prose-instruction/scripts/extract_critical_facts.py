#!/usr/bin/env python3
"""Extract the machine-critical facts of a task instruction into a manifest.

The manifest has two jobs. It tells the writer what must survive a
heading-free rewrite, and it gives the validator a source-of-truth to check
the candidate against. It never contains answer values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

FACTS_VERSION = "gdpval-prose-facts-v2"
NOTES_NAME = "additional-assumptions.md"
DISCLOSURE_HEADING = "## Workbook disclosure"
NOTES_MARKER_START = "<!-- workbook-notes:start -->"
NOTES_MARKER_END = "<!-- workbook-notes:end -->"

JUNIOR_TITLES = ("Analyst", "Associate")
SENIOR_TITLES = ("VP", "Director", "Managing Director")
ALLOWED_TITLES = JUNIOR_TITLES + SENIOR_TITLES

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*(?:#+[ \t]*)?$")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+\S")
LIST_CONT_RE = re.compile(r"^\s{2,}\S")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
URL_RE = re.compile(r"https?://[^\s)`]+")
CELL_RE = re.compile(
    r"(?:'[^'\n]+'|[A-Za-z0-9_][A-Za-z0-9_.]*)!"
    r"\$?[A-Z]{1,3}\$?\d{1,7}"
)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?%?"
    r"(?![A-Za-z0-9_])"
)
SPEAKER_RE = re.compile(r"(?m)^\*\*([A-Za-z][A-Za-z ]{0,30}):\*\*")
WORKBOOK_FILE_RE = re.compile(r"`([^`\n]+\.xls[xm])`")
# Older tasks write to /workspace/answers.json; the pipeline now writes to
# /app/answers.json. The path is a fact of the source, not of this skill.
DEFAULT_DELIVERABLE_PATH = "/app/answers.json"
DELIVERABLE_RE = re.compile(r"`(/[^`\s]*answers\.json)`")
SOURCE_CATEGORIES = (
    "market rates",
    "tax rates",
    "macro assumptions",
    "contractual terms",
    "opening balances",
)
# Template sentences from xl_output_task.py (and the old naturalizer's
# rewordings of them) that restate what the MCP server already documents in
# its tool docstrings, server instructions, and error messages, or that are
# pipeline narration. The writer may omit them whole. Each pattern is bounded
# to a single sentence so it can never swallow task-specific guidance that
# follows it (a greedy form once captured `period = Model horizon` filters).
DROPPABLE_SENTENCE_RES = (
    # \s+ between words: the source is hard-wrapped, so any space may be a newline.
    re.compile(r"(?:It|The\s+server)\s+exposes\s+five\s+tools:"),
    re.compile(r"Records\s+(?:vary|may\s+vary|can\s+vary|carry)\s+across\s+those\s+dimensions[^.]*?releases\."),
    re.compile(r"`query_records`\s+requires\s+at\s+least\s+two[^.]*?result\s+sets\."),
    re.compile(r"Records\s+exist\s+in\s+multiple\s+releases:[^.]*?authoritative\."),
    re.compile(r"Filter\s+every\s+dimension\s+that\s+matters[^.]*?\."),
    re.compile(r"Any\s+MCP\s+client\s+works,\s+for\s+example\s+from\s+Python:"),
    re.compile(r"The\s+(?:artifact|workbook|file)\s+has\s+been\s+(?:checked|verified)\s+(?:to\s+contain|and\s+contains)[^.]*?\."),
    re.compile(r"Report\s+\d+\s+values\s+in\s+total,\s+one\s+per\s+cell\s+listed\."),
)
# The count sentence is the last pattern above. It is only droppable when the
# table has one cell per row, so "the N figures" already says how many values
# to report. When a row lists several cells, the count is the one place the
# reader learns to report one value per cell, so it stays required.
COUNT_SENTENCE_RE = DROPPABLE_SENTENCE_RES[-1]
# The four tools the research server exposes, in template order. As list items
# they are protected blocks; as a group they may be omitted whole (the server's
# own list_tools reply carries the same text) but never trimmed or edited.
TOOL_LIST_ITEM_PREFIXES = (
    "- `list_sources`",
    "- `list_datasets`",
    "- `search_documents`",
    "- `query_records`",
)
TOOL_LIST_GROUP = "tool_list"
FAMILY_PHRASE = {
    "three_statement": "an integrated three-statement and valuation model",
    "scenario_driven": "a scenario-driven investment model",
    "dcf_valuation": "a discounted cash flow valuation model",
    "lbo_returns": "a leveraged buyout returns model",
    "real_estate_pf": "a real estate project finance model",
}


class FactsError(ValueError):
    def __init__(self, reason_code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "valid": False,
            "reason_codes": [self.reason_code],
            "error": str(self),
        }
        if self.details:
            out["details"] = self.details
        return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- scanning


def decode(source: bytes) -> tuple[str, bool, str]:
    """Return (text, had_bom, newline)."""
    bom = source.startswith(b"\xef\xbb\xbf")
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FactsError("invalid_utf8", "instruction is not valid UTF-8", offset=exc.start)
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, bom, newline


def scan_lines(text: str) -> list[dict[str, Any]]:
    """Classify every line: fence, heading, table, list, marker, or prose."""
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    fence_char = ""
    fence_len = 0
    for index, line in enumerate(lines):
        kind = "prose"
        if fence_char:
            kind = "fence"
            match = FENCE_RE.match(line)
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_len
                and not match.group(2).strip()
            ):
                fence_char = ""
                fence_len = 0
                kind = "fence_close"
        else:
            match = FENCE_RE.match(line)
            if match:
                fence_char = match.group(1)[0]
                fence_len = len(match.group(1))
                kind = "fence_open"
            elif HEADING_RE.match(line):
                kind = "heading"
            elif line.strip() in (NOTES_MARKER_START, NOTES_MARKER_END):
                kind = "marker"
            elif TABLE_LINE_RE.match(line):
                kind = "table"
            elif LIST_ITEM_RE.match(line):
                kind = "list"
            elif out and out[-1]["kind"] in ("list", "list_cont") and LIST_CONT_RE.match(line):
                kind = "list_cont"
        out.append({"index": index, "kind": kind, "text": line})
    if fence_char:
        raise FactsError("unclosed_fence", "instruction has an unclosed code fence")
    return out


def headings(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for entry in lines:
        if entry["kind"] != "heading":
            continue
        match = HEADING_RE.match(entry["text"])
        assert match is not None
        found.append(
            {
                "index": entry["index"],
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
            }
        )
    return found


def section_ranges(lines: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Map lowercase heading title -> (start_line, end_line) for level-2 sections."""
    heads = [h for h in headings(lines) if h["level"] == 2]
    ranges: dict[str, tuple[int, int]] = {}
    for position, head in enumerate(heads):
        end = heads[position + 1]["index"] if position + 1 < len(heads) else len(lines)
        ranges[head["title"].lower()] = (head["index"], end)
    return ranges


def _section_of(line: int, ranges: dict[str, tuple[int, int]], lines: list[dict[str, Any]]) -> str:
    for title, (start, end) in ranges.items():
        if start <= line < end:
            return title
    # Heading-free source: a block sits inside the notes markers or nowhere.
    depth = 0
    for entry in lines[:line]:
        if entry["kind"] == "marker":
            depth = 1 if entry["text"].strip() == NOTES_MARKER_START else 0
    return "workbook disclosure" if depth else ""


def protected_blocks(
    lines: list[dict[str, Any]],
    newline: str,
    ranges: dict[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Fences, tables, and list items in source order. Text is exact."""
    ranges = ranges or {}
    blocks: list[dict[str, Any]] = []
    index = 0
    counters: Counter[str] = Counter()
    while index < len(lines):
        entry = lines[index]
        kind = entry["kind"]
        if kind == "fence_open":
            start = index
            index += 1
            while index < len(lines) and lines[index]["kind"] != "fence_close":
                index += 1
            end = index + 1
            counters["fence"] += 1
            blocks.append(
                {
                    "id": "fence-%d" % counters["fence"],
                    "kind": "fence",
                    "text": newline.join(x["text"] for x in lines[start:end]),
                    "line": start,
                }
            )
            index = end
            continue
        if kind == "table":
            start = index
            while index < len(lines) and lines[index]["kind"] == "table":
                index += 1
            counters["table"] += 1
            rows = [x["text"].rstrip() for x in lines[start:index]]
            blocks.append(
                {
                    "id": "table-%d" % counters["table"],
                    "kind": "table",
                    "text": newline.join(rows),
                    "rows": len(rows),
                    "line": start,
                }
            )
            continue
        if kind == "list":
            start = index
            index += 1
            while index < len(lines) and lines[index]["kind"] == "list_cont":
                index += 1
            counters["list"] += 1
            blocks.append(
                {
                    "id": "list-%d" % counters["list"],
                    "kind": "list",
                    "text": newline.join(x["text"].rstrip() for x in lines[start:index]),
                    "line": start,
                }
            )
            continue
        index += 1
    for block in blocks:
        block["section"] = _section_of(block["line"], ranges, lines)
    return blocks


def prose_text(lines: list[dict[str, Any]]) -> str:
    """Everything that is not a fence, table, list item, heading, or marker."""
    return "\n".join(x["text"] for x in lines if x["kind"] == "prose")


def strip_inline_code(text: str) -> str:
    return INLINE_CODE_RE.sub(" ", text)


def token_counts(text: str) -> dict[str, dict[str, int]]:
    """Inline code and URLs over the given text; numbers over code-stripped text."""
    stripped = strip_inline_code(text)
    return {
        "inline_code": dict(Counter(m.group(0) for m in INLINE_CODE_RE.finditer(text))),
        "urls": dict(Counter(m.group(0) for m in URL_RE.finditer(text))),
        "numbers": dict(Counter(m.group(0) for m in NUMBER_RE.finditer(stripped))),
    }


def cell_ref_counts(text: str) -> dict[str, int]:
    return dict(Counter(m.group(0) for m in CELL_RE.finditer(text)))


def droppable_spans(prose: str, count_droppable: bool = True) -> list[str]:
    """Template sentences in the prose that the writer may omit whole."""
    found: list[str] = []
    for pattern in DROPPABLE_SENTENCE_RES:
        if pattern is COUNT_SENTENCE_RE and not count_droppable:
            continue
        for match in pattern.finditer(prose):
            found.append(match.group(0))
    return found


def optional_token_counts(spans: list[str]) -> dict[str, dict[str, int]]:
    """Token counts inside droppable spans: the writer may drop these, at most
    this many times each. Anything not counted here stays required exactly."""
    return token_counts("\n".join(spans))


def mark_tool_list_group(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag the consecutive research tool-list items with `optional_group`.

    Detection is by item text, not by section, so it also works on a source
    that is already heading-free. Only the exact four template tools qualify;
    a fifth, task-specific item next to them stays mandatory.
    """
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block["kind"] != "list" or not block["text"].startswith(TOOL_LIST_ITEM_PREFIXES[0]):
            index += 1
            continue
        run = blocks[index : index + len(TOOL_LIST_ITEM_PREFIXES)]
        if len(run) == len(TOOL_LIST_ITEM_PREFIXES) and all(
            b["kind"] == "list" and b["text"].startswith(prefix)
            for b, prefix in zip(run, TOOL_LIST_ITEM_PREFIXES)
        ):
            for b in run:
                b["optional_group"] = TOOL_LIST_GROUP
            index += len(run)
            continue
        index += 1
    return blocks


def table_output_names(blocks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return (figure names, cell refs) from the first `| # | figure | cells |` table."""
    for block in blocks:
        if block["kind"] != "table":
            continue
        rows = block["text"].split("\n") if "\n" in block["text"] else block["text"].split("\r\n")
        header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
        if "figure" not in header or "cells" not in header:
            continue
        fig = header.index("figure")
        cells_col = header.index("cells")
        names: list[str] = []
        refs: list[str] = []
        for row in rows[2:]:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) <= max(fig, cells_col):
                continue
            names.append(cells[fig])
            refs.extend(m.group(0) for m in CELL_RE.finditer(cells[cells_col]))
        return names, refs
    return [], []


# --------------------------------------------------------------------------- task context


def notes_speakers(task_dir: Path | None) -> list[str]:
    if task_dir is None:
        return []
    notes = task_dir / "environment" / NOTES_NAME
    if not notes.is_file():
        return []
    seen: list[str] = []
    for match in SPEAKER_RE.finditer(notes.read_text(encoding="utf-8")):
        title = re.sub(r"\s+", " ", match.group(1).strip())
        canonical = {t.lower(): t for t in ALLOWED_TITLES}.get(title.lower(), title)
        if canonical not in seen:
            seen.append(canonical)
    return seen


def workbook_sheets(task_dir: Path | None, workbook_file: str) -> list[str]:
    if task_dir is None or not workbook_file:
        return []
    path = task_dir / "environment" / workbook_file
    if not path.is_file():
        return []
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return []
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return [str(name) for name in book.sheetnames]
        finally:
            book.close()
    except Exception:
        return []


def task_metadata(task_dir: Path | None) -> dict[str, Any]:
    if task_dir is None or not (task_dir / "task.toml").is_file():
        return {}
    try:
        parsed = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeError):
        return {}
    metadata = parsed.get("metadata") or {}
    return {
        "taxonomy_primary": str(metadata.get("taxonomy_primary") or ""),
        "output_names": list(metadata.get("output_names") or []),
        "n_answer_cells": metadata.get("n_answer_cells"),
        "research_service": bool((task_dir / "environment" / "mcp-server").is_dir()),
    }


# --------------------------------------------------------------------------- manifest


def extract_facts(source: bytes, task_dir: Path | None = None) -> dict[str, Any]:
    text, bom, newline = decode(source)
    lines = scan_lines(text)
    heads = headings(lines)
    ranges = section_ranges(lines)
    blocks = mark_tool_list_group(protected_blocks(lines, newline, ranges))
    prose = prose_text(lines)
    names, table_refs = table_output_names(blocks)

    lower = text.lower()
    has_disclosure = "workbook disclosure" in ranges or (
        NOTES_MARKER_START in text and NOTES_MARKER_END in text
    )
    has_research = "research data service" in lower or "mcp-server" in lower
    has_pointer = NOTES_NAME in text
    workbook_match = WORKBOOK_FILE_RE.search(text)
    workbook_file = workbook_match.group(1) if workbook_match else ""
    deliverable_match = DELIVERABLE_RE.search(text)
    deliverable_path = (
        deliverable_match.group(1) if deliverable_match else DEFAULT_DELIVERABLE_PATH
    )

    meta = task_metadata(task_dir)
    speakers = notes_speakers(task_dir)
    notes_present = bool(task_dir) and (task_dir / "environment" / NOTES_NAME).is_file()
    sheets = workbook_sheets(task_dir, workbook_file)
    family = meta.get("taxonomy_primary") or ""

    if has_pointer and notes_present:
        mode = "retrofit_after_assumptions"
    elif has_disclosure:
        mode = "pipeline_with_disclosure"
    else:
        mode = "pipeline_plain"

    categories = [c for c in SOURCE_CATEGORIES if c in lower]
    counts = token_counts(prose)
    # The count sentence may be droppable, so a heading-free source may not
    # carry it; task.toml is the next most reliable count (the table
    # undercounts multi-cell and wrapped rows in a few corpus files).
    n_cells_match = re.search(r"Report (\d+) values", text)
    if n_cells_match:
        n_cells = int(n_cells_match.group(1))
    elif isinstance(meta.get("n_answer_cells"), int) and meta["n_answer_cells"] > 0:
        n_cells = int(meta["n_answer_cells"])
    else:
        n_cells = len(table_refs)

    output_names = names or list(meta.get("output_names") or [])
    # One cell per table row: "the N figures" already gives the count and the
    # count sentence may go. Several cells on a row: the count sentence is the
    # only place that says one value per cell, so it stays.
    count_droppable = not output_names or n_cells == len(output_names)
    droppable = droppable_spans(prose, count_droppable=count_droppable)
    optional_counts = optional_token_counts(droppable)
    required_anchors = {
        "working_directory": True,
        # The reader must learn the calculations are still to be done (else it
        # may read cached values), in template framing rather than "blank".
        "calculations_pending": True,
        "install_packages": "install" in lower,
        "research_only": has_research and "only" in lower,
        # One plain sentence stands in for the superseded_by mechanics.
        "latest_release": has_research,
        "output_no_round": "round" in lower,
        "output_units_scale": "units" in lower and "scale" in lower,
        "output_percentages": "percentage" in lower,
        "output_keyed": "key" in lower,
        "assumptions_pointer": has_pointer or notes_present,
    }

    return {
        "version": FACTS_VERSION,
        "source_sha256": sha256_bytes(source),
        "source_size_bytes": len(source),
        "bom": bom,
        "newline": "crlf" if newline == "\r\n" else "lf",
        "final_newline": text.endswith(("\n", "\r")),
        "mode": mode,
        "has_headings": bool(heads),
        "source_headings": [h["title"] for h in heads],
        "has_research_service": has_research,
        "has_disclosure": has_disclosure,
        "has_assumptions_pointer": has_pointer,
        "notes_present": notes_present,
        "notes_speakers": speakers,
        "allowed_titles": list(ALLOWED_TITLES),
        "junior_titles": list(JUNIOR_TITLES),
        "workbook_file": workbook_file,
        "deliverable_path": deliverable_path,
        "workbook_sheets": sheets,
        "taxonomy_primary": family,
        "family_phrase": FAMILY_PHRASE.get(family, "a financial model"),
        "output_names": output_names,
        "n_cells": n_cells,
        "count_required": not count_droppable,
        "cell_refs": cell_ref_counts(text),
        "source_categories_present": categories,
        "required_anchors": required_anchors,
        "protected_blocks": blocks,
        "prose_tokens": counts,
        "droppable_spans": droppable,
        "optional_tokens": optional_counts,
        "allowed_extra_inline_code": sorted(
            {"`%s`" % s for s in sheets} | {"`%s`" % NOTES_NAME, "`openpyxl`"}
        ),
    }


def writer_brief(facts: dict[str, Any]) -> str:
    """Plain-language brief for the writer subagent. No answer values."""
    lines = [
        "WRITER BRIEF (grounded facts only; do not invent beyond these)",
        "",
        "Mode: %s" % facts["mode"],
        "Workbook file: %s" % (facts["workbook_file"] or "(unknown)"),
        "Deliverable path (use exactly this): %s"
        % facts.get("deliverable_path", DEFAULT_DELIVERABLE_PATH),
        "Model family: %s" % facts["family_phrase"],
        "Sheet names (grounding only: pick the setting from them, do not list"
        " them; quote at most the answer tab): %s"
        % (", ".join(facts["workbook_sheets"]) if facts["workbook_sheets"] else "(unknown)"),
        "Curated output names, in order: %s" % ", ".join(facts["output_names"]),
        "Number of graded cells: %d" % facts["n_cells"],
        "Research service present: %s" % ("yes" if facts["has_research_service"] else "no"),
        "Disclosure bullets present: %s" % ("yes" if facts["has_disclosure"] else "no"),
        "Notes file %s present: %s"
        % (NOTES_NAME, "yes" if facts["notes_present"] else "not yet"),
        "Titles allowed anywhere: %s" % ", ".join(facts["allowed_titles"]),
        "The reader is a junior: open with 'You are' + one of %s"
        % ", ".join(facts["junior_titles"]),
    ]
    if facts["notes_speakers"]:
        lines.append(
            "Titles that speak in %s (use only these): %s"
            % (NOTES_NAME, ", ".join(facts["notes_speakers"]))
        )
        juniors = [t for t in facts["notes_speakers"] if t in JUNIOR_TITLES]
        if juniors:
            lines.append("The reader must be one of: %s" % ", ".join(juniors))
        else:
            lines.append("No junior speaks in the notes: the reader is an Analyst")
    if facts["has_disclosure"]:
        lines.append(
            "Disclosure block: put <!-- workbook-notes:start --> and "
            "<!-- workbook-notes:end --> on their own lines with a blank line "
            "before the start marker and after the end marker"
        )

    lines += [
        "",
        "VOICE",
        "  - Workbook state, template framing: the inputs, historicals and layout",
        "    are in place; the calculations have not been carried out yet. Never say",
        "    blank cells, removed, no formulas, remaining inputs, mock, masked.",
        "  - Ask the question the way the senior colleague would ask it; do not",
        "    write 'The business question is'.",
        "  - Do not list the workbook's tabs. Name at most the tab the graded cells",
        "    sit on; when they sit on several tabs, point to the table. The validator",
        "    rejects more than three sheet names beyond the answer tabs.",
    ]
    if facts.get("count_required"):
        lines += [
            "  - A table row lists more than one cell, so keep one sentence saying",
            "    %d values in all, one per cell listed (the number %d is required)."
            % (facts["n_cells"], facts["n_cells"]),
        ]
    else:
        lines.append("  - The total count of values is optional; if you keep it, one short aside.")
    lines += [
        "  - The units/scale/unrounded/percentages rule goes AFTER the answers JSON",
        "    code block, as the last paragraph.",
    ]
    if facts["required_anchors"].get("assumptions_pointer"):
        lines += [
            "  - Notes pointer, one paragraph, this wording: Before you begin, read",
            "    `%s` in your working directory. It is a record of a" % NOTES_NAME,
            "    conversation between colleagues that provides additional guidance on",
            "    how several rows in this model should be worked out; follow the",
            "    assumptions it records.",
        ]

    lines.append("")
    lines.append("Protected blocks (copy verbatim, in this order):")
    grouped_ids = [b["id"] for b in facts["protected_blocks"] if b.get("optional_group")]
    for block in facts["protected_blocks"]:
        suffix = ""
        if block.get("optional_group"):
            suffix = "  [research tool list: omit ALL of %s or keep ALL verbatim]" % ", ".join(grouped_ids)
        lines.append("  - %s (%s)%s" % (block["id"], block["kind"], suffix))
    if facts["has_research_service"]:
        lines.append(
            "  Any other research list item or sentence not named below is task-specific"
            " guidance and must stay."
        )

    droppable = facts.get("droppable_spans") or []
    if droppable:
        lines.append("")
        lines.append("Droppable sentences (omit whole, or keep verbatim; never partially):")
        for span in droppable:
            lines.append("  - %s" % re.sub(r"\s+", " ", span.strip()))

    lines.append("")
    lines.append("Token budget (prose only; tables, lists and code blocks are separate):")
    optional = facts.get("optional_tokens") or {}
    grounding = set(facts.get("allowed_extra_inline_code") or [])
    for kind in ("urls", "inline_code", "numbers"):
        for token, count in sorted(facts["prose_tokens"].get(kind, {}).items()):
            may_drop = optional.get(kind, {}).get(token, 0)
            if kind == "inline_code" and token in grounding:
                rule = "keep at least 1 (grounding span; repeats allowed)"
            elif may_drop >= count:
                rule = "may be dropped entirely (up to %d)" % count
            elif may_drop:
                rule = "keep at least %d (up to %d)" % (count - may_drop, count)
            else:
                rule = "required exactly %d" % count
            lines.append("  - %s: %s" % (token, rule))
    lines.append(
        "  - Add no other numbers. New inline code only for: the sheet names above"
        " (grounding only; at most the answer tab), `%s`, `openpyxl`" % NOTES_NAME
    )

    if facts["has_research_service"]:
        url = next(iter(facts["prose_tokens"].get("urls", {})), "http://mcp-server:8000/mcp")
        lines += [
            "",
            "MODEL PARAGRAPH, research service (adapt the wording, keep the facts):",
            "  The market rates, tax rates, macro assumptions, contractual terms and",
            "  opening balances the model needs are not in the sheets; they are only",
            "  available from the firm's research service, an MCP server at `%s`" % url,
            "  (streamable HTTP transport). Records come in releases; use the latest",
            "  release of each one (a row whose `superseded_by` field is set has been",
            "  replaced). Convert each value into the units and scale of the sheet it",
            "  feeds: rates come back in percent while the sheets may store decimal",
            "  fractions, and monetary amounts state their unit on the record.",
            "  Then both code blocks, verbatim.",
        ]
    if facts["has_disclosure"]:
        lines += [
            "",
            "MODEL LEAD-IN, disclosure block (first line inside the markers):",
            "  A few modelling choices are not obvious from the workbook, so they are",
            "  written down here. They describe how a row is worked out, not a figure",
            "  you are asked to report.",
        ]
    lines += [
        "",
        "MODEL PARAGRAPHS, deliverable (this order; the rule comes after the fence):",
        "  Put your answers in a JSON object at `%s`, keyed by cell reference"
        % facts.get("deliverable_path", DEFAULT_DELIVERABLE_PATH),
        "  exactly as written in the table:",
        "  [the answers JSON code block, verbatim]",
        "  Give each figure as the model produces it, in the units and scale of the",
        "  sheet it sits on, unrounded; leave percentages as the cell holds them.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instruction", type=Path)
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--out", type=Path, help="write manifest JSON here")
    parser.add_argument("--brief", type=Path, help="write the writer brief here")
    args = parser.parse_args()
    try:
        facts = extract_facts(args.instruction.read_bytes(), args.task_dir)
    except FactsError as exc:
        print(json.dumps(exc.as_dict(), indent=2, sort_keys=True))
        return 1
    rendered = json.dumps(facts, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    if args.brief:
        args.brief.parent.mkdir(parents=True, exist_ok=True)
        args.brief.write_text(writer_brief(facts), encoding="utf-8")
    if not args.out and not args.brief:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
