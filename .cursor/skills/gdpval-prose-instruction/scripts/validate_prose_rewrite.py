#!/usr/bin/env python3
"""Fail-closed validation of a heading-free GDPval-style instruction rewrite.

The candidate may reorganize and rephrase ordinary prose. It may not drop or
change a protected block (fenced code, table, list item), any cell reference,
URL, or number, and it may not add headings, answer values, invented titles,
or task-construction language such as "rebuild".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_critical_facts import (  # noqa: E402
    ALLOWED_TITLES,
    CELL_RE,
    DEFAULT_DELIVERABLE_PATH,
    INLINE_CODE_RE,
    JUNIOR_TITLES,
    NOTES_MARKER_END,
    NOTES_MARKER_START,
    NOTES_NAME,
    NUMBER_RE,
    URL_RE,
    FactsError,
    decode,
    extract_facts,
    prose_text,
    protected_blocks,
    scan_lines,
    sha256_bytes,
)

# v2: MCP mechanics the server documents itself are droppable; the prose must
# not describe the workbook as gutted ("removed", "cells are blank", "no
# formulas", "remaining inputs", "mock", "masked"); the count sentence is
# optional; the deliverable rule is phrased naturally. Applied v1 tasks fail
# verify-metadata after this bump and need a re-run (only the two demo copies
# existed at the time of the change).
# The sheet_list_dump cap (at most three sheet names beyond the answer tabs)
# was added without a bump: no applied prose task existed outside the demo,
# and verify-applied / verify-metadata compare hashes and versions only, so
# applied tasks cannot start failing.
PROMPT_VERSION = "gdpval-prose-instruction-v2"
MODEL = "gpt-5.6-sol-high"

BANNED_FRAMING_RE = re.compile(
    r"\b(?:"
    r"re-?buil(?:d|ds|t|ding)|"
    r"re-?construct(?:s|ed|ing|ion|ions)?|"
    r"re-?stor(?:e|es|ed|ing|ation)|"
    r"re-?creat(?:e|es|ed|ing|ion)|"
    r"reverse[- ]engineer\w*|"
    r"original\s+(?:model|logic|workbook|file|spreadsheet)|"
    r"delivered\s+file|"
    r"stripped(?:\s+out)?|"
    r"removed|removal|"
    r"masked|masking|"
    r"leakage[- ]control|"
    r"blanked|left\s+blank|"
    # The state description is banned, not the word: "treat blank cells as
    # zero" is a legitimate modelling fact that some sources carry.
    r"(?:cell|cells|figure|figures|value|values|row|rows|outputs?)\s+(?:is|are|were|remain|remains)\s+(?:left\s+|still\s+)?blank|"
    r"(?:cells?|values?|figures?|rows?|outputs?)\s+(?:was|were|have\s+been|has\s+been|are|is)\s+cleared|cleared\s+out|"
    r"no\s+formulas?|formula-free|carries\s+no|"
    r"remaining\s+(?:drivers|inputs|cells|values|labels)|"
    r"identified\s+inputs|sheet\s+structure|"
    r"has\s+been\s+(?:checked|verified)\s+to|"
    r"mock|"
    r"build(?:ing)?\s+(?:a|the)\s+spreadsheet|"
    r"spreadsheet\s+reconstruction|"
    r"artifact"
    r")\b",
    re.IGNORECASE,
)
# Any job title that is not one of the five allowed ones. Checked in every
# mode, since the notes-speaker subset check only runs when notes exist.
FOREIGN_TITLE_RE = re.compile(
    r"\b(?:"
    r"interns?|"
    r"(?:senior|junior|lead|principal|managing|executive)\s+(?:banker|investor|analyst|associate|partner|manager)s?|"
    r"bankers?|"
    r"c[ef]o|coo|cio|cro|chief\s+\w+\s+officer|"
    r"head\s+of\s+\w+|"
    r"(?:portfolio|fund|asset|risk|deal|relationship|engagement|project|product|general)\s+managers?|"
    r"(?:vice\s+president|senior\s+vice\s+president|executive\s+director)|"
    r"(?:your|the|a|an)\s+(?:boss|supervisor|manager|team\s+lead|mentor|partner)"
    r")\b",
    re.IGNORECASE,
)
YOU_ARE_RE = re.compile(
    r"\byou\s+are\s+(?:an?|the)\s+(?:[A-Za-z-]+\s+){0,4}?(analyst|associate)\b",
    re.IGNORECASE,
)
POINTER_PHRASE_RE = re.compile(r"record of a conversation between colleagues", re.IGNORECASE)
POINTER_GUIDANCE_RE = re.compile(r"additional guidance", re.IGNORECASE)
# The headed source says the notes hold "assumptions you need to follow". The
# prose pointer keeps that imperative (the AA canonical pointer ends "follow
# the assumptions it records"); "provides additional guidance" alone reads as
# optional reading.
POINTER_FOLLOW_RE = re.compile(r"\b(?:follow|apply|use)\b", re.IGNORECASE)
# Template framing: the reader must learn the calculations are still to be
# done. Natural phrasings, not "blank".
CALCULATIONS_PENDING_RE = re.compile(
    r"(?:"
    r"not\s+(?:yet\s+)?been\s+(?:carried\s+out|run|done|computed|performed|calculated|worked\s+(?:out|through)|completed|filled\s+in|populated)|"
    r"(?:has|have|had)\s+not\s+been\s+(?:done|calculated|computed|worked\s+(?:out|through)|run|completed|carried\s+out)|"
    r"(?:has|have)n't\s+been\s+(?:done|calculated|computed|worked\s+(?:out|through)|run|completed|carried\s+out|filled\s+in)|"
    r"nothing\s+has\s+been\s+(?:computed|calculated|worked\s+out)|"
    # Bare forms: "remain to be run", "has yet to be built", "still to be
    # completed", "still need to be done". Any trailing verb counts.
    r"remains?\s+to\s+be\b|"
    r"(?:has|have|is|are)\s+(?:still\s+)?yet\s+to\s+be\b|"
    r"(?:is|are)\s+still\s+to\s+be\b|"
    r"still\s+(?:need|needs|have|has)\s+to\s+be\b|"
    r"not\s+(?:yet\s+)?(?:filled\s+in|populated|computed|calculated|worked\s+out|carried\s+out|complete|completed)\b|"
    r"left\s+to\s+(?:do|complete|calculate|compute)|"
    r"(?:are|is|remain|remains)\s+outstanding|"
    r"awaiting\s+calculation|"
    r"(?:has|have)\s+not\s+(?:yet\s+)?been\s+run|"
    r"not\s+(?:yet\s+)?run\b"
    r")",
    re.IGNORECASE,
)
# One plain sentence stands in for the server's superseded_by mechanics.
LATEST_RELEASE_RE = re.compile(
    r"(?:"
    r"latest\s+(?:release|version)|most\s+recent\s+(?:release|version)|newest\s+(?:release|version)|"
    r"current\s+release|unsuperseded|superseded|"
    r"(?:has|have|had)\s+(?:not\s+)?been\s+replaced|later\s+release"
    r")",
    re.IGNORECASE,
)
NO_ROUND_RE = re.compile(
    r"(?:\b(?:do not|don't|never|without|no)\s+round|\bunrounded\b|\bnot\s+round)",
    re.IGNORECASE,
)
HEADING_LINE_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+\S")


class ProseValidationError(ValueError):
    def __init__(
        self,
        message: str,
        reason_code: str = "validation_constraint_failed",
        **details: object,
    ) -> None:
        super().__init__(message)
        self.reason_codes = [reason_code]
        self.details = details

    def as_report(self) -> dict:
        report: dict[str, Any] = {
            "valid": False,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "reason_codes": self.reason_codes,
            "error": str(self),
        }
        if self.details:
            report["details"] = self.details
        return report


def require(condition: bool, message: str, checks: list[str], reason_code: str) -> None:
    if not condition:
        raise ProseValidationError(message, reason_code)
    checks.append(message)


def _normalize_block(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))


def _normalize_doc(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))


def _numeric_occurrences(text: str, value: int | float) -> int:
    target = Decimal(str(value))
    count = 0
    for match in NUMBER_RE.finditer(text):
        token = match.group(0).replace(",", "")
        percentage = token.endswith("%")
        if percentage:
            token = token[:-1]
        try:
            parsed = Decimal(token)
        except InvalidOperation:
            continue
        if percentage:
            parsed /= Decimal(100)
        if parsed == target:
            count += 1
    return count


def _title_pattern(title: str) -> re.Pattern[str]:
    if title == "Director":
        return re.compile(r"(?<!Managing )(?<!managing )\bDirector\b", re.IGNORECASE)
    return re.compile(r"\b%s\b" % re.escape(title), re.IGNORECASE)


def _markers_are_own_paragraph(doc: str, start: int, end: int) -> bool:
    """Start marker begins a line after a blank line (or file start); end
    marker ends a line before a blank line (or file end)."""
    before = doc[:start]
    if before.strip() and not before.endswith("\n\n"):
        return False
    if before and not before.endswith("\n"):
        return False
    after = doc[end + len(NOTES_MARKER_END):]
    if after.strip() and not after.startswith("\n\n"):
        return False
    if after and not after.startswith("\n"):
        return False
    return True


def _first_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        if paragraph.strip():
            return paragraph.strip()
    return ""


def validate(
    source: str | bytes,
    candidate: str | bytes,
    answer_key: dict | None = None,
    *,
    task_dir: Path | None = None,
    facts: dict | None = None,
) -> dict:
    source_bytes = source.encode("utf-8") if isinstance(source, str) else source
    candidate_bytes = candidate.encode("utf-8") if isinstance(candidate, str) else candidate
    if facts is None:
        try:
            facts = extract_facts(source_bytes, task_dir)
        except FactsError as exc:
            raise ProseValidationError(str(exc), exc.reason_code, source=True) from exc
    checks: list[str] = []

    # ---- bytes and structure -------------------------------------------------
    try:
        source_text, source_bom, _ = decode(source_bytes)
        candidate_text, candidate_bom, _ = decode(candidate_bytes)
    except FactsError as exc:
        raise ProseValidationError(str(exc), "invalid_utf8") from exc
    require(
        candidate_bom == source_bom,
        "byte-order-mark state preserved",
        checks,
        "replacement_contains_bom",
    )
    require(
        candidate_bytes.endswith((b"\n", b"\r")) == source_bytes.endswith((b"\n", b"\r")),
        "candidate final-newline state preserved",
        checks,
        "final_newline_changed",
    )
    require(
        "{{" not in candidate_text and "}}" not in candidate_text,
        "no unresolved template placeholders",
        checks,
        "template_placeholder",
    )
    try:
        candidate_lines = scan_lines(candidate_text)
    except FactsError as exc:
        raise ProseValidationError(str(exc), "candidate_structure_invalid") from exc
    require(
        not any(entry["kind"] == "heading" for entry in candidate_lines),
        "candidate has no Markdown headings",
        checks,
        "heading_present",
    )
    require(
        len(candidate_text.split()) >= 80,
        "candidate is a full brief, not a stub",
        checks,
        "candidate_too_short",
    )

    # ---- protected blocks: exactly once, in order ---------------------------
    # Blocks in an optional_group (the research tool list) are all-or-nothing:
    # omit every member, or keep every member verbatim in source order.
    doc = _normalize_doc(candidate_text)
    groups: dict[str, list[dict]] = {}
    for block in facts["protected_blocks"]:
        if block.get("optional_group"):
            groups.setdefault(block["optional_group"], []).append(block)
    omitted_groups: set[str] = set()
    for name, members in groups.items():
        present = [doc.find(_normalize_block(b["text"])) >= 0 for b in members]
        if not any(present):
            omitted_groups.add(name)
            checks.append("optional group omitted whole: %s" % name)
            continue
        require(
            all(present),
            "optional group %s kept whole (missing: %s)"
            % (name, ", ".join(b["id"] for b, ok in zip(members, present) if not ok)),
            checks,
            "optional_group_partial",
        )
        group_positions = [doc.find(_normalize_block(b["text"])) for b in members]
        require(
            group_positions == sorted(group_positions),
            "optional group %s kept in source order" % name,
            checks,
            "optional_group_partial",
        )
    candidate_blocks = protected_blocks(candidate_lines, "\n")
    positions: list[int] = []
    for block in facts["protected_blocks"]:
        if block.get("optional_group") in omitted_groups:
            continue
        needle = _normalize_block(block["text"])
        first = doc.find(needle)
        require(
            first >= 0,
            "protected block preserved verbatim: %s" % block["id"],
            checks,
            "protected_block_missing",
        )
        require(
            doc.find(needle, first + len(needle)) < 0,
            "protected block appears once: %s" % block["id"],
            checks,
            "protected_block_duplicated",
        )
        positions.append(first)
    require(
        positions == sorted(positions),
        "protected blocks keep source order",
        checks,
        "protected_block_order_changed",
    )
    if omitted_groups:
        # No half-kept tool item may survive as a list item of any shape
        # ("- `x`", "1. `x`", "- The `x` tool ...").
        candidate_list_text = "\n".join(
            b["text"] for b in candidate_blocks if b["kind"] == "list"
        )
        for name in omitted_groups:
            for block in groups[name]:
                head = block["text"].split("`")[1] if "`" in block["text"] else block["text"][:20]
                require(
                    ("`%s`" % head) not in candidate_list_text,
                    "omitted tool list leaves no partial item: %s" % block["id"],
                    checks,
                    "optional_group_partial",
                )
    source_fences = [b for b in facts["protected_blocks"] if b["kind"] == "fence"]
    candidate_fences = [b for b in candidate_blocks if b["kind"] == "fence"]
    require(
        len(candidate_fences) == len(source_fences),
        "no fenced code blocks added or removed",
        checks,
        "protected_construct_order_changed",
    )
    source_tables = [b for b in facts["protected_blocks"] if b["kind"] == "table"]
    candidate_tables = [b for b in candidate_blocks if b["kind"] == "table"]
    require(
        len(candidate_tables) == len(source_tables),
        "no tables added or removed",
        checks,
        "protected_construct_order_changed",
    )

    # ---- disclosure markers ---------------------------------------------------
    disclosure_blocks = [
        b for b in facts["protected_blocks"] if b.get("section") == "workbook disclosure"
    ]
    start_count = candidate_text.count(NOTES_MARKER_START)
    end_count = candidate_text.count(NOTES_MARKER_END)
    if facts["has_disclosure"] and disclosure_blocks:
        require(
            start_count == 1 and end_count == 1,
            "workbook-notes markers present exactly once",
            checks,
            "disclosure_markers_invalid",
        )
        start = doc.find(NOTES_MARKER_START)
        end = doc.find(NOTES_MARKER_END)
        require(start < end, "workbook-notes markers ordered", checks, "disclosure_markers_invalid")
        # The additional-assumptions step later swaps the whole marker span for
        # one paragraph. Markers glued to neighbouring prose would take that
        # prose with them, so each marker must be its own paragraph boundary.
        require(
            _markers_are_own_paragraph(doc, start, end),
            "workbook-notes markers sit on their own lines with a blank line on each side",
            checks,
            "disclosure_markers_invalid",
        )
        inside = doc[start:end]
        for block in disclosure_blocks:
            require(
                _normalize_block(block["text"]) in inside,
                "disclosure item sits between the markers: %s" % block["id"],
                checks,
                "disclosure_markers_invalid",
            )
        for block in facts["protected_blocks"]:
            if block in disclosure_blocks:
                continue
            require(
                _normalize_block(block["text"]) not in inside,
                "non-disclosure block stays outside the markers: %s" % block["id"],
                checks,
                "disclosure_markers_invalid",
            )
    else:
        require(
            start_count == 0 and end_count == 0,
            "no workbook-notes markers without disclosure",
            checks,
            "disclosure_markers_invalid",
        )

    # ---- tokens -------------------------------------------------------------------
    require(
        Counter(m.group(0) for m in CELL_RE.finditer(candidate_text))
        == Counter(facts["cell_refs"]),
        "cell references preserved exactly",
        checks,
        "cell_refs_changed",
    )
    candidate_prose = prose_text(candidate_lines)
    source_tokens = facts["prose_tokens"]
    candidate_urls = Counter(m.group(0) for m in URL_RE.finditer(candidate_prose))
    require(
        candidate_urls == Counter(source_tokens["urls"]),
        "URLs preserved exactly",
        checks,
        "token_mismatch",
    )
    candidate_code = Counter(m.group(0) for m in INLINE_CODE_RE.finditer(candidate_prose))
    source_code = Counter(source_tokens["inline_code"])
    optional_tokens = facts.get("optional_tokens") or {}
    optional_code = Counter(optional_tokens.get("inline_code") or {})
    optional_numbers = Counter(optional_tokens.get("numbers") or {})
    allowed_extra = set(facts["allowed_extra_inline_code"])
    for span, count in source_code.items():
        if span in allowed_extra:
            # Grounding spans (notes file, sheet names, openpyxl) may be
            # mentioned once or several times; the headed source often repeats
            # the notes pointer, the prose form says it once.
            require(
                candidate_code.get(span, 0) >= 1,
                "inline-code span preserved: %s" % span,
                checks,
                "token_mismatch",
            )
            continue
        # Spans that only occur inside droppable mechanics sentences may be
        # dropped that many times; never added.
        floor = count - optional_code.get(span, 0)
        require(
            floor <= candidate_code.get(span, 0) <= count,
            "inline-code span preserved: %s (%d..%d)" % (span, floor, count),
            checks,
            "token_mismatch",
        )
    for span in candidate_code:
        if span in source_code:
            continue
        require(
            span in allowed_extra,
            "inline-code span is grounded: %s" % span,
            checks,
            "unsupported_inline_code",
        )
    # Sheet names are permitted so the writer can ground the setting, not so
    # it can recite the workbook's tabs. Tabs the graded cells sit on are
    # already in the table and do not count; a few more may frame the work.
    answer_sheets = {ref.split("!")[0].strip("'") for ref in facts["cell_refs"]}
    sheet_spans = {
        "`%s`" % s for s in (facts.get("workbook_sheets") or []) if s not in answer_sheets
    }
    new_sheets = sorted((sheet_spans & set(candidate_code)) - set(source_code))
    require(
        len(new_sheets) <= 3,
        "at most three sheet names beyond the answer tabs"
        + (" (have %d: %s)" % (len(new_sheets), ", ".join(new_sheets[:5])) if len(new_sheets) > 3 else ""),
        checks,
        "sheet_list_dump",
    )
    candidate_numbers = Counter(
        m.group(0) for m in NUMBER_RE.finditer(INLINE_CODE_RE.sub(" ", candidate_prose))
    )
    source_numbers = Counter(source_tokens["numbers"])
    for number in set(source_numbers) | set(candidate_numbers):
        count = source_numbers.get(number, 0)
        floor = count - optional_numbers.get(number, 0)
        have = candidate_numbers.get(number, 0)
        require(
            floor <= have <= count,
            "number in prose preserved: %s (%d..%d, have %d)" % (number, floor, count, have),
            checks,
            "token_mismatch",
        )
    if answer_key:
        for value in (answer_key.get("targets") or {}).values():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            require(
                _numeric_occurrences(candidate_text, value)
                <= _numeric_occurrences(source_text, value),
                "no new answer-value occurrence: %s" % value,
                checks,
                "answer_value_leak",
            )

    # ---- framing, titles, and grounding ------------------------------------------
    editable = candidate_prose
    # Output names and sheet names are quoted facts, not people or framing:
    # "Cash Flow from Operations (CFO)", "Cleared PJM Capacity Revenues" and
    # "Remaining Equity Value" must not read as an invented title or as
    # task-construction language.
    title_scan = re.sub(r"\s+", " ", editable)
    for quoted in [*(facts.get("output_names") or []), *(facts.get("workbook_sheets") or [])]:
        if quoted:
            title_scan = re.sub(re.escape(re.sub(r"\s+", " ", quoted)), " ", title_scan, flags=re.IGNORECASE)
    banned = sorted({m.group(0) for m in BANNED_FRAMING_RE.finditer(title_scan)})
    require(
        not banned,
        "no task-construction framing (rebuild, removed, cells are blank, no formulas, mock, artifact)"
        + (": %s" % ", ".join(banned[:6]) if banned else ""),
        checks,
        "banned_framing",
    )
    foreign = sorted({m.group(0) for m in FOREIGN_TITLE_RE.finditer(title_scan)})
    require(
        not foreign,
        "no foreign job titles" + (": %s" % ", ".join(foreign) if foreign else ""),
        checks,
        "persona_title_invalid",
    )
    first_paragraph = _first_paragraph(candidate_text)
    you_are = YOU_ARE_RE.search(first_paragraph)
    require(
        you_are is not None,
        "opens with 'You are' and a junior title (Analyst or Associate)",
        checks,
        "persona_title_invalid",
    )
    assert you_are is not None
    reader_title = you_are.group(1).capitalize()
    speakers = list(facts.get("notes_speakers") or [])
    # Whitespace-collapsed so "Managing\nDirector" is still Managing Director.
    title_text = title_scan
    if speakers:
        junior_speakers = [t for t in speakers if t in JUNIOR_TITLES]
        allowed_here = set(speakers)
        if junior_speakers:
            require(
                reader_title in junior_speakers,
                "reader title speaks in %s: %s" % (NOTES_NAME, reader_title),
                checks,
                "title_not_in_notes",
            )
        else:
            # No junior speaks in the notes: the reader defaults to Analyst.
            require(
                reader_title == "Analyst",
                "reader title falls back to Analyst when no junior speaks in %s" % NOTES_NAME,
                checks,
                "title_not_in_notes",
            )
            allowed_here.add(reader_title)
        for title in ALLOWED_TITLES:
            if title in allowed_here:
                continue
            require(
                not _title_pattern(title).search(title_text),
                "title absent from %s is not used: %s" % (NOTES_NAME, title),
                checks,
                "title_not_in_notes",
            )

    lower = candidate_text.lower()
    prose_lower = editable.lower()
    for category in facts["source_categories_present"]:
        require(
            category in lower,
            "source category preserved: %s" % category,
            checks,
            "source_category_lost",
        )
    anchors = facts["required_anchors"]
    anchor_checks = (
        ("working_directory", "working directory" in lower, "working directory"),
        (
            "calculations_pending",
            bool(CALCULATIONS_PENDING_RE.search(prose_lower)),
            "the calculations have not been carried out yet",
        ),
        ("install_packages", "install" in prose_lower, "may install packages"),
        ("research_only", "only" in prose_lower, "missing inputs only via research service"),
        (
            "latest_release",
            bool(LATEST_RELEASE_RE.search(prose_lower)),
            "use the latest release of each research record",
        ),
        ("output_no_round", bool(NO_ROUND_RE.search(prose_lower)), "no rounding"),
        ("output_units_scale", "unit" in prose_lower and "scale" in prose_lower, "units and scale"),
        ("output_percentages", "percentage" in prose_lower, "percentages not converted"),
        ("output_keyed", "key" in prose_lower, "answers keyed by cell reference"),
    )
    for name, present, label in anchor_checks:
        if anchors.get(name):
            require(present, "semantic anchor preserved: %s" % label, checks, "semantic_anchor_lost")
    if facts["has_research_service"]:
        require(
            "research" in prose_lower,
            "research service described",
            checks,
            "semantic_anchor_lost",
        )
    deliverable = "`%s`" % facts.get("deliverable_path", DEFAULT_DELIVERABLE_PATH)
    require(
        deliverable in candidate_text,
        "deliverable path present: %s" % deliverable,
        checks,
        "semantic_anchor_lost",
    )

    output_names = facts.get("output_names") or []
    table_text = "\n".join(_normalize_block(b["text"]) for b in source_tables)
    if output_names:
        lead = output_names[0]
        require(
            lead.lower() in prose_lower,
            "lead output named in prose: %s" % lead,
            checks,
            "named_output_lost",
        )
        for name in output_names:
            require(
                name.lower() in lower,
                "curated output present: %s" % name,
                checks,
                "named_output_lost",
            )
    del table_text

    if anchors.get("assumptions_pointer"):
        require(
            "`%s`" % NOTES_NAME in candidate_text,
            "notes file named: %s" % NOTES_NAME,
            checks,
            "assumptions_pointer_lost",
        )
        require(
            re.search(r"\bread\b", prose_lower) is not None
            and re.search(r"\bbefore\b", prose_lower) is not None,
            "notes pointer is must-read language",
            checks,
            "assumptions_pointer_lost",
        )
        require(
            POINTER_PHRASE_RE.search(editable) is not None
            and POINTER_GUIDANCE_RE.search(editable) is not None,
            "notes described as a record of a conversation between colleagues that provides additional guidance",
            checks,
            "assumptions_pointer_lost",
        )
        pointer_paragraphs = [
            re.sub(r"\s+", " ", p)
            for p in re.split(r"\n\s*\n", doc)
            if ("`%s`" % NOTES_NAME) in p
        ]
        require(
            any(POINTER_FOLLOW_RE.search(p) for p in pointer_paragraphs),
            "notes pointer tells the reader to follow the assumptions it records",
            checks,
            "assumptions_pointer_lost",
        )

    # ---- deliverable closes the brief --------------------------------------------
    answers_pos = doc.find(deliverable)
    table_positions = [doc.find(_normalize_block(b["text"])) for b in source_tables]
    require(
        all(answers_pos > p for p in table_positions),
        "deliverable follows the table of figures",
        checks,
        "deliverable_not_last",
    )
    if candidate_fences:
        last_fence = _normalize_block(candidate_fences[-1]["text"])
        require(
            "<number>" in last_fence or '{"' in last_fence,
            "answers JSON example is the last code block",
            checks,
            "deliverable_not_last",
        )
        tail = doc[doc.find(last_fence) + len(last_fence):]
        require(
            "round" in tail.lower(),
            "no-rounding rule closes the brief",
            checks,
            "deliverable_not_last",
        )

    return {
        "valid": True,
        "reason_codes": [],
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "source_sha256": sha256_bytes(source_bytes),
        "candidate_sha256": sha256_bytes(candidate_bytes),
        "source_size_bytes": len(source_bytes),
        "candidate_size_bytes": len(candidate_bytes),
        "facts_version": facts["version"],
        "mode": facts["mode"],
        "checks": checks,
    }


PROSE_TABLE_RE = re.compile(r"(?ms)^\[metadata\.prose_instruction\]\n.*?(?=^\[|\Z)")
NATURALIZER_TABLE_RE = re.compile(r"(?ms)^\[metadata\.naturalizer\]\n.*?(?=^\[|\Z)")
METADATA_TABLE_RE = re.compile(r"(?ms)^\[metadata\]\n.*?(?=^\[|\Z)")


def render_prose_table(report: dict, attempts: int) -> str:
    return "\n".join(
        [
            "[metadata.prose_instruction]",
            'model = "%s"' % MODEL,
            'endpoint = "cursor-subagent"',
            "attempts = %d" % attempts,
            "heading_free = true",
            'prompt_version = "%s"' % PROMPT_VERSION,
            'source_sha256 = "%s"' % report["source_sha256"],
            'instruction_sha256 = "%s"' % report["candidate_sha256"],
            "",
            "",
        ]
    )


INSTRUCTION_HASH_RE = re.compile(r'(?m)^(instruction_sha256\s*=\s*")[0-9a-f]{64}(")')


def updated_task_toml(text: str, report: dict, attempts: int) -> str:
    """Write [metadata.prose_instruction] and align every instruction hash line.

    Other tables (naturalizer, dialogue marker) also record the live
    instruction hash. Refreshing them keeps their own verifiers truthful.
    """
    table = render_prose_table(report, attempts)
    if PROSE_TABLE_RE.search(text):
        text = PROSE_TABLE_RE.sub(lambda _m: table, text, count=1)
    else:
        anchor = NATURALIZER_TABLE_RE.search(text) or METADATA_TABLE_RE.search(text)
        if anchor is None:
            raise ProseValidationError("task.toml has no [metadata] table", "invalid_task_toml")
        head = text[: anchor.end()]
        if not head.endswith("\n\n"):
            head = head.rstrip("\n") + "\n\n"
        text = head + table + text[anchor.end():].lstrip("\n")
    return INSTRUCTION_HASH_RE.sub(r"\g<1>%s\2" % report["candidate_sha256"], text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a heading-free prose rewrite")
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--answer-key", type=Path)
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = args.source.read_bytes()
    candidate = args.candidate.read_bytes()
    answer_key = (
        json.loads(args.answer_key.read_text(encoding="utf-8")) if args.answer_key else None
    )
    try:
        report = validate(source, candidate, answer_key, task_dir=args.task_dir)
        report["applied"] = False
    except ProseValidationError as exc:
        report = exc.as_report()
        report.update(
            {
                "source_sha256": sha256_bytes(source),
                "candidate_sha256": sha256_bytes(candidate),
            }
        )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
