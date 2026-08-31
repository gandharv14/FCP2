#!/usr/bin/env python3
"""Fail-closed validation and atomic application of instruction rewrites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from instruction_spans import (  # noqa: E402
    InstructionSpanError,
    assemble_instruction,
    extract_editable_bodies,
    scan_instruction,
    sha256_bytes,
)


PROMPT_VERSION = "finance-instruction-naturalizer-v3"
MODEL = "gpt-5.6-sol-high"

HEADING_RE = re.compile(r"(?m)^(#{1,6}\s+.+)$")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
URL_RE = re.compile(r"https?://[^\s)`]+")
CELL_RE = re.compile(
    r"(?:'[^'\n]+'|[A-Za-z0-9_][A-Za-z0-9_ .&()-]*)!"
    r"\$?[A-Z]{1,3}\$?\d{1,7}"
)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?%?"
    r"(?![A-Za-z0-9_])"
)
FENCE_RE = re.compile(r"(?ms)^```[^\n]*\n.*?^```\s*$")
TABLE_RE = re.compile(r"(?m)(?:^\|.*\|\s*\n?){2,}")
LIST_RE = re.compile(r"(?m)^(?:[-*+]|\d+\.)\s+.+$")

# freeze_protected_spans.py derives frozen token spans from this same table.
EXACT_TOKEN_CHECKS = (
    ("fenced code blocks", FENCE_RE),
    ("Markdown tables", TABLE_RE),
    ("list items", LIST_RE),
    ("inline-code spans", INLINE_CODE_RE),
    ("URLs", URL_RE),
    ("cell references", CELL_RE),
    ("numbers", NUMBER_RE),
)
FROZEN_TOKEN_LABELS = ("inline-code spans", "URLs", "cell references", "numbers")

SOURCE_CATEGORIES = (
    "market rates",
    "tax rates",
    "macro assumptions",
    "contractual terms",
    "opening balances",
)

SEMANTIC_ANCHORS = (
    ("rebuild", ("rebuild",)),
    ("working directory", ("working directory",)),
    ("blank", ("blank", "cleared")),
    ("no formulas", ("no formulas",)),
    ("derived", ("derived",)),
    ("install", ("install",)),
    ("packages", ("packages",)),
    ("research data service", ("research data service",)),
)
REMOVED_TERMS = ("removed", "stripped out")
REMOVED_ACCEPTED = ("removed", "stripped out", "cleared", "blanked")
INPUT_AVAILABILITY_ACCEPTED = ("present", "remain", "retained")
REGION_LABELS = {
    "preamble": "opening prose (text before the first heading)",
    "input": "## Input section",
    "mutable": "opening prose or ## Input section",
}


class RewriteValidationError(ValueError):
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
        report = {
            "valid": False,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "reason_codes": self.reason_codes,
            "error": str(self),
        }
        if self.details:
            report["details"] = self.details
        return report


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validation_byte_context(source: bytes, candidate: bytes) -> dict:
    context = {
        "source_sha256": sha256_bytes(source),
        "candidate_sha256": sha256_bytes(candidate),
        "source_size_bytes": len(source),
        "candidate_size_bytes": len(candidate),
    }
    try:
        context["source_spans"] = scan_instruction(source).as_dict()["spans"]
    except InstructionSpanError:
        pass
    try:
        context["candidate_spans"] = scan_instruction(candidate).as_dict()["spans"]
    except InstructionSpanError:
        pass
    return context


def counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0) for match in pattern.finditer(text))


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    raw = text.encode("utf-8")
    try:
        spans = scan_instruction(raw)
    except InstructionSpanError as exc:
        raise RewriteValidationError(
            str(exc), "candidate_structure_invalid", **exc.details
        ) from exc
    headings = spans.headings
    preamble = raw[: headings[0].start].decode("utf-8-sig")
    sections: list[tuple[str, str]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start if index + 1 < len(headings) else len(raw)
        body = raw[heading.start:end].decode("utf-8")
        heading_line = body.splitlines()[0]
        sections.append((heading_line, body))
    return preamble, sections


def input_section(sections: list[tuple[str, str]]) -> str:
    for heading, body in sections:
        if heading.strip().lower() == "## input":
            return body
    raise RewriteValidationError("instruction has no level-two Input section")


def mutable_text(text: str) -> str:
    preamble, sections = split_sections(text)
    return preamble + input_section(sections)


def require(
    condition: bool,
    message: str,
    checks: list[str],
    reason_code: str = "validation_constraint_failed",
    expected: str | None = None,
    found: str | None = None,
) -> None:
    """Record a passing check, or fail with a reason code plus expected/found."""
    if not condition:
        detail = message
        if expected is not None:
            detail += " | expected: %s" % expected
        if found is not None:
            detail += " | found: %s" % found
        raise RewriteValidationError(detail, reason_code)
    checks.append(message)


def first_diff(source_body: str, candidate_body: str) -> str:
    source_lines = source_body.splitlines()
    candidate_lines = candidate_body.splitlines()
    for index, (old, new) in enumerate(zip(source_lines, candidate_lines)):
        if old != new:
            return "line %d differs | source: %r | candidate: %r" % (
                index + 1,
                old[:160],
                new[:160],
            )
    return "line counts differ: source has %d lines, candidate has %d" % (
        len(source_lines),
        len(candidate_lines),
    )


def model_type_phrase(source_preamble: str) -> str | None:
    matches = re.findall(
        r"(?=\b(?:an?|the)\s+([A-Za-z][^.\n]{0,120}?\bmodel)\b)",
        source_preamble,
        re.IGNORECASE,
    )
    return min(matches, key=len) if matches else None


def named_outputs_phrase(source_preamble: str) -> str | None:
    match = re.search(
        r"(?:including|such as)\s+(.+?)(?=\.\s+(?:The full list|Every required)|$)",
        source_preamble,
        re.IGNORECASE | re.DOTALL,
    )
    return " ".join(match.group(1).split()) if match else None


def protected_anchor_specs(source: str) -> list[dict]:
    """Anchor list validate() enforces; freeze_protected_spans.py uses this too."""
    preamble, sections = split_sections(source)
    input_body = input_section(sections)
    mutable_lower = (preamble + input_body).lower()
    input_lower = input_body.lower()
    specs: list[dict] = []

    phrase = model_type_phrase(preamble)
    if phrase:
        specs.append({
            "check": "financial model type preserved: %s" % phrase,
            "phrase": phrase,
            "accepted": (phrase.lower(),),
            "region": "preamble",
            "normalize_ws": False,
        })
    phrase = named_outputs_phrase(preamble)
    if phrase:
        specs.append({
            "check": "named example outputs preserved",
            "phrase": phrase,
            "accepted": (phrase.lower(),),
            "region": "preamble",
            "normalize_ws": True,
        })
    for category in SOURCE_CATEGORIES:
        if category in mutable_lower:
            specs.append({
                "check": "source category preserved: %s" % category,
                "phrase": category,
                "accepted": (category,),
                "region": "mutable",
                "normalize_ws": False,
            })
    for anchor, accepted in SEMANTIC_ANCHORS:
        if anchor in mutable_lower:
            specs.append({
                "check": "semantic anchor preserved: %s" % anchor,
                "phrase": anchor,
                "accepted": accepted,
                "region": "mutable",
                "normalize_ws": False,
            })
    if "only" in input_lower:
        specs.append({
            "check": "Input exclusivity preserved",
            "phrase": "only",
            "accepted": ("only",),
            "region": "input",
            "normalize_ws": False,
        })
    if "may " in input_lower:
        specs.append({
            "check": "Input permission modality preserved",
            "phrase": "may ",
            "accepted": ("may ",),
            "region": "input",
            "normalize_ws": False,
        })
    if "present" in input_lower:
        specs.append({
            "check": "Input availability preserved",
            "phrase": "present",
            "accepted": INPUT_AVAILABILITY_ACCEPTED,
            "region": "input",
            "normalize_ws": False,
        })
    removed = next((term for term in REMOVED_TERMS if term in mutable_lower), None)
    if removed:
        specs.append({
            "check": "removed-content scope preserved",
            "phrase": removed,
            "accepted": REMOVED_ACCEPTED,
            "region": "mutable",
            "normalize_ws": False,
        })
    return specs


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


def _protected_construct_manifest(
    raw: bytes,
    spans,
    region_name: str,
    region_text: str,
) -> list[tuple[str, bytes]]:
    region = getattr(spans, region_name)
    events: list[tuple[int, str, bytes]] = []
    for fence in spans.fenced_blocks:
        if region.start <= fence.start < region.end:
            events.append(
                (fence.start - region.start, "fence", raw[fence.start : fence.end])
            )
    for kind, pattern in (("table", TABLE_RE), ("list", LIST_RE)):
        for match in pattern.finditer(region_text):
            byte_offset = len(region_text[: match.start()].encode("utf-8"))
            events.append((byte_offset, kind, match.group(0).encode("utf-8")))
    events.sort(key=lambda item: (item[0], item[1]))
    return [(kind, content) for _, kind, content in events]


def validate(
    source: str | bytes,
    candidate: str | bytes,
    answer_key: dict | None = None,
) -> dict:
    source_bytes = source.encode("utf-8") if isinstance(source, str) else source
    candidate_bytes = (
        candidate.encode("utf-8") if isinstance(candidate, str) else candidate
    )
    try:
        source_span_map = scan_instruction(source_bytes)
    except InstructionSpanError as exc:
        raise RewriteValidationError(
            str(exc), exc.reason_code, source=True, **exc.details
        ) from exc
    try:
        candidate_span_map = scan_instruction(candidate_bytes)
    except InstructionSpanError as exc:
        raise RewriteValidationError(
            str(exc), "candidate_structure_invalid", underlying_reason=exc.reason_code
        ) from exc

    source_heading_keys = [
        (heading.level, heading.title) for heading in source_span_map.headings
    ]
    candidate_heading_keys = [
        (heading.level, heading.title) for heading in candidate_span_map.headings
    ]
    if candidate_heading_keys != source_heading_keys:
        raise RewriteValidationError(
            "headings and section order preserved",
            "heading_structure_changed",
        )
    source_preamble_bytes, source_input_bytes = extract_editable_bodies(
        source_bytes, source_span_map
    )
    candidate_preamble_bytes, candidate_input_bytes = extract_editable_bodies(
        candidate_bytes, candidate_span_map
    )
    try:
        expected_candidate = assemble_instruction(
            source_bytes,
            source_span_map,
            candidate_preamble_bytes,
            candidate_input_bytes,
        )
    except InstructionSpanError as exc:
        raise RewriteValidationError(
            str(exc), exc.reason_code, **exc.details
        ) from exc
    if candidate_bytes != expected_candidate:
        expected_text = expected_candidate.decode("utf-8-sig", "replace")
        candidate_text = candidate_bytes.decode("utf-8-sig", "replace")
        raise RewriteValidationError(
            "protected section preserved byte-for-byte"
            " | expected: %s | found: candidate protected bytes differ"
            % first_diff(expected_text, candidate_text),
            "protected_section_changed",
        )

    try:
        source_text = source_bytes.decode("utf-8-sig")
        candidate_text = candidate_bytes.decode("utf-8-sig")
        source_editable_texts = (
            source_preamble_bytes.decode("utf-8"),
            source_input_bytes.decode("utf-8"),
        )
        candidate_editable_texts = (
            candidate_preamble_bytes.decode("utf-8"),
            candidate_input_bytes.decode("utf-8"),
        )
    except UnicodeDecodeError as exc:
        raise RewriteValidationError(
            "instruction is not valid UTF-8", "invalid_utf8", offset=exc.start
        ) from exc

    checks: list[str] = [
        "headings and section order preserved",
        "protected section preserved byte-for-byte",
        "fenced code blocks preserved by editable region",
    ]
    for region_name, source_region, candidate_region in zip(
        ("preamble_body", "input_body"),
        source_editable_texts,
        candidate_editable_texts,
    ):
        require(
            _protected_construct_manifest(
                source_bytes,
                source_span_map,
                region_name,
                source_region,
            )
            == _protected_construct_manifest(
                candidate_bytes,
                candidate_span_map,
                region_name,
                candidate_region,
            ),
            "fenced code blocks, tables, and lists preserved by region and cross-type order",
            checks,
            "protected_construct_order_changed",
        )

    source_preamble, source_sections = split_sections(source_text)
    candidate_preamble, candidate_sections = split_sections(candidate_text)

    source_headings = [heading for heading, _ in source_sections]
    candidate_headings = [heading for heading, _ in candidate_sections]
    require(
        candidate_headings == source_headings,
        "headings and section order preserved",
        checks,
        "heading_structure_changed",
        expected="the source headings in order: %r" % source_headings,
        found="%r" % candidate_headings,
    )

    for (heading, source_body), (_, candidate_body) in zip(
        source_sections, candidate_sections
    ):
        if heading.strip().lower() == "## input":
            continue
        require(
            candidate_body == source_body,
            "protected section preserved byte-for-byte: %s" % heading,
            checks,
            "protected_section_changed",
            expected="a byte-identical copy of the source section",
            found=(
                None
                if candidate_body == source_body
                else first_diff(source_body, candidate_body)
            ),
        )

    if answer_key:
        targets = answer_key.get("targets") or {}
        for value in targets.values():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            source_hits = _numeric_occurrences(source_text, value)
            candidate_hits = _numeric_occurrences(candidate_text, value)
            require(
                candidate_hits <= source_hits,
                "no new answer-value occurrence: %s" % value,
                checks,
                "answer_value_leak",
                expected="at most %d occurrence(s), matching the source" % source_hits,
                found="%d occurrence(s) in the candidate" % candidate_hits,
            )

    for label, pattern in EXACT_TOKEN_CHECKS:
        source_counts = counter(pattern, source_text)
        candidate_counts = counter(pattern, candidate_text)
        found = None
        if candidate_counts != source_counts:
            missing = dict((source_counts - candidate_counts).most_common(8))
            added = dict((candidate_counts - source_counts).most_common(8))
            found = "missing from candidate: %r; new in candidate: %r" % (
                missing,
                added,
            )
        require(
            candidate_counts == source_counts,
            "%s preserved exactly" % label,
            checks,
            "token_mismatch",
            expected="the same %s tokens with the same counts as the source" % label,
            found=found,
        )

    source_mutable = mutable_text(source_text)
    candidate_mutable = mutable_text(candidate_text)
    source_input = input_section(source_sections).lower()
    candidate_input = input_section(candidate_sections).lower()
    source_mutable_lower = source_mutable.lower()
    candidate_mutable_lower = candidate_mutable.lower()

    model_matches = re.findall(
        r"(?=\b(?:an?|the)\s+([A-Za-z][^.\n]{0,120}?\bmodel)\b)",
        source_preamble,
        re.IGNORECASE,
    )
    if model_matches:
        model_phrase = min(model_matches, key=len)
        require(
            model_phrase.lower() in candidate_preamble.lower(),
            "financial model type preserved: %s" % model_phrase,
            checks,
            "semantic_mismatch",
        )

    named_outputs = re.search(
        r"(?:including|such as)\s+(.+?)(?=\.\s+(?:The full list|Every required)|$)",
        source_preamble,
        re.IGNORECASE | re.DOTALL,
    )
    if named_outputs:
        phrase = " ".join(named_outputs.group(1).split())
        require(
            phrase.lower() in " ".join(candidate_preamble.split()).lower(),
            "named example outputs preserved",
            checks,
            "named_outputs_changed",
            expected=repr(phrase.lower()),
            found=repr(" ".join(candidate_preamble.split()).lower()),
        )

    for category in SOURCE_CATEGORIES:
        if category in source_mutable_lower:
            require(
                category in candidate_mutable_lower,
                "source category preserved: %s" % category,
                checks,
                "source_category_lost",
            )

    semantic_anchors = (
        ("rebuild", ("rebuild",)),
        ("working directory", ("working directory",)),
        ("blank", ("blank", "cleared")),
        ("no formulas", ("no formulas",)),
        ("derived", ("derived",)),
        ("install", ("install",)),
        ("packages", ("packages",)),
        ("research data service", ("research data service",)),
    )
    for source_anchor, accepted in semantic_anchors:
        if source_anchor in source_mutable_lower:
            require(
                any(term in candidate_mutable_lower for term in accepted),
                "semantic anchor preserved: %s" % source_anchor,
                checks,
                "semantic_anchor_lost",
                expected=repr(source_anchor),
                found=repr(candidate_mutable_lower),
            )

    if "only" in source_input:
        require(
            "only" in candidate_input,
            "Input exclusivity preserved",
            checks,
            "input_exclusivity_lost",
        )
    if "may " in source_input:
        require(
            "may " in candidate_input,
            "Input permission modality preserved",
            checks,
            "input_modality_lost",
            expected=repr("may "),
            found=repr(candidate_input),
        )
    if "present" in source_input:
        require(
            any(term in candidate_input for term in ("present", "remain", "retained")),
            "Input availability preserved",
            checks,
            "input_availability_lost",
        )
    if any(term in source_mutable_lower for term in ("removed", "stripped out")):
        require(
            any(
                term in candidate_mutable_lower
                for term in ("removed", "stripped out", "cleared", "blanked")
            ),
            "removed-content scope preserved",
            checks,
            "removed_scope_changed",
        )

    require(
        "{{" not in candidate_text and "}}" not in candidate_text,
        "no unresolved template placeholders",
        checks,
        "template_placeholder",
        expected="no '{{' or '}}' anywhere in the candidate",
        found="unresolved '{{'/'}}' placeholder text",
    )
    require(
        candidate_bytes.endswith((b"\n", b"\r"))
        == source_bytes.endswith((b"\n", b"\r")),
        "candidate final-newline state preserved",
        checks,
        "final_newline_changed",
        expected="candidate final-newline state matching the source",
        found="candidate ends with %r" % candidate_text[-8:],
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
        "source_spans": source_span_map.as_dict()["spans"],
        "candidate_spans": candidate_span_map.as_dict()["spans"],
        "checks": checks,
    }


def atomic_write(path: Path, text: str) -> Path:
    temp = path.with_name(path.name + ".naturalize.tmp")
    if temp.exists():
        raise RewriteValidationError("temporary path already exists: %s" % temp)
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return temp


def updated_task_toml(text: str, report: dict, attempts: int) -> str:
    table_re = re.compile(
        r"(?ms)^\[metadata\.naturalizer\]\n.*?(?=^\[|\Z)"
    )
    if not table_re.search(text):
        raise RewriteValidationError("task.toml has no metadata.naturalizer table")
    replacement = "\n".join(
        [
            "[metadata.naturalizer]",
            'model = "%s"' % MODEL,
            'endpoint = "cursor-subagent"',
            "attempts = %d" % attempts,
            "naturalized = true",
            'fallback_reason = ""',
            'prompt_version = "%s"' % PROMPT_VERSION,
            'source_sha256 = "%s"' % report["source_sha256"],
            'instruction_sha256 = "%s"' % report["candidate_sha256"],
            "",
            "",
        ]
    )
    return table_re.sub(replacement, text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a lossless finance-instruction rewrite"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--answer-key", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    source = args.source.read_bytes()
    candidate = args.candidate.read_bytes()
    answer_key = (
        json.loads(args.answer_key.read_text(encoding="utf-8"))
        if args.answer_key
        else None
    )

    try:
        report = validate(source, candidate, answer_key)
        report["applied"] = False
    except RewriteValidationError as exc:
        report = exc.as_report()
        report.update(validation_byte_context(source, candidate))
    except InstructionSpanError as exc:
        report = {
            "valid": False,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "reason_codes": [exc.reason_code],
            "error": str(exc),
            "details": exc.details,
        }
        report.update(validation_byte_context(source, candidate))
    except Exception as exc:
        # Recovery errors expose a stable reason code without coupling validation
        # to the recovery module's implementation.
        reason_code = getattr(exc, "reason_code", None)
        if reason_code is None:
            raise
        report = {
            "valid": False,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "reason_codes": [reason_code],
            "error": str(exc),
        }
        report.update(validation_byte_context(source, candidate))

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temp = atomic_write(args.report, rendered)
        os.replace(temp, args.report)
    print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
