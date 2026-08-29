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


PROMPT_VERSION = "finance-instruction-naturalizer-v1"
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
TABLE_RE = re.compile(r"(?m)(?:^\|.*\|\s*\n?){2,}")
LIST_RE = re.compile(r"(?m)^(?:[-*+]|\d+\.)\s+.+$")

SOURCE_CATEGORIES = (
    "market rates",
    "tax rates",
    "macro assumptions",
    "contractual terms",
    "opening balances",
)


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
) -> None:
    if not condition:
        raise RewriteValidationError(message, reason_code)
    checks.append(message)


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
        raise RewriteValidationError(
            "protected section preserved byte-for-byte",
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
        )

    if answer_key:
        targets = answer_key.get("targets") or {}
        for value in targets.values():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            require(
                _numeric_occurrences(candidate_text, value)
                <= _numeric_occurrences(source_text, value),
                "no new answer-value occurrence: %s" % value,
                checks,
                "answer_value_leak",
            )

    for label, pattern in (
        ("inline-code spans", INLINE_CODE_RE),
        ("URLs", URL_RE),
        ("cell references", CELL_RE),
        ("numbers", NUMBER_RE),
    ):
        require(
            counter(pattern, candidate_text) == counter(pattern, source_text),
            "%s preserved exactly" % label,
            checks,
            "token_mismatch",
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
    )
    require(
        candidate_bytes.endswith((b"\n", b"\r"))
        == source_bytes.endswith((b"\n", b"\r")),
        "candidate final-newline state preserved",
        checks,
        "final_newline_changed",
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
