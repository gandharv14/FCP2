#!/usr/bin/env python3
"""Fail-closed validation and atomic application of instruction rewrites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path


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
    r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z])"
)
FENCE_RE = re.compile(r"(?ms)^```[^\n]*\n.*?^```\s*$")
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
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0) for match in pattern.finditer(text))


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[match.start() : end]))
    return preamble, sections


def input_section(sections: list[tuple[str, str]]) -> str:
    for heading, body in sections:
        if heading.strip().lower() == "## input":
            return body
    raise RewriteValidationError("instruction has no level-two Input section")


def mutable_text(text: str) -> str:
    preamble, sections = split_sections(text)
    return preamble + input_section(sections)


def require(condition: bool, message: str, checks: list[str]) -> None:
    if not condition:
        raise RewriteValidationError(message)
    checks.append(message)


def validate(source: str, candidate: str, answer_key: dict | None = None) -> dict:
    checks: list[str] = []
    source_preamble, source_sections = split_sections(source)
    candidate_preamble, candidate_sections = split_sections(candidate)

    source_headings = [heading for heading, _ in source_sections]
    candidate_headings = [heading for heading, _ in candidate_sections]
    require(
        candidate_headings == source_headings,
        "headings and section order preserved",
        checks,
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
        )

    for label, pattern in (
        ("fenced code blocks", FENCE_RE),
        ("Markdown tables", TABLE_RE),
        ("list items", LIST_RE),
        ("inline-code spans", INLINE_CODE_RE),
        ("URLs", URL_RE),
        ("cell references", CELL_RE),
        ("numbers", NUMBER_RE),
    ):
        require(
            counter(pattern, candidate) == counter(pattern, source),
            "%s preserved exactly" % label,
            checks,
        )

    source_mutable = mutable_text(source)
    candidate_mutable = mutable_text(candidate)
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
        )

    for category in SOURCE_CATEGORIES:
        if category in source_mutable_lower:
            require(
                category in candidate_mutable_lower,
                "source category preserved: %s" % category,
                checks,
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
            )

    if "only" in source_input:
        require("only" in candidate_input, "Input exclusivity preserved", checks)
    if "may " in source_input:
        require(
            "may " in candidate_input,
            "Input permission modality preserved",
            checks,
        )
    if "present" in source_input:
        require(
            any(term in candidate_input for term in ("present", "remain", "retained")),
            "Input availability preserved",
            checks,
        )
    if any(term in source_mutable_lower for term in ("removed", "stripped out")):
        require(
            any(
                term in candidate_mutable_lower
                for term in ("removed", "stripped out", "cleared", "blanked")
            ),
            "removed-content scope preserved",
            checks,
        )

    if answer_key:
        targets = answer_key.get("targets") or {}
        for value in targets.values():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            variants = {str(value), repr(value)}
            if isinstance(value, float) and value.is_integer():
                variants.add(str(int(value)))
            for variant in variants:
                pattern = re.compile(r"(?<![\d.])%s(?![\d.])" % re.escape(variant))
                require(
                    len(pattern.findall(candidate)) <= len(pattern.findall(source)),
                    "no new answer-value occurrence: %s" % variant,
                    checks,
                )

    require(
        "{{" not in candidate and "}}" not in candidate,
        "no unresolved template placeholders",
        checks,
    )
    require(candidate.endswith("\n"), "candidate has final newline", checks)

    return {
        "valid": True,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "source_sha256": sha256_text(source),
        "candidate_sha256": sha256_text(candidate),
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
    parser.add_argument("--apply-to", type=Path)
    parser.add_argument("--task-toml", type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8")
    candidate = args.candidate.read_text(encoding="utf-8")
    answer_key = (
        json.loads(args.answer_key.read_text(encoding="utf-8"))
        if args.answer_key
        else None
    )

    try:
        report = validate(source, candidate, answer_key)
        if args.apply_to or args.task_toml:
            if not args.apply_to or not args.task_toml:
                raise RewriteValidationError(
                    "--apply-to and --task-toml are required together"
                )
            current = args.apply_to.read_text(encoding="utf-8")
            if sha256_text(current) != report["source_sha256"]:
                raise RewriteValidationError(
                    "apply target changed after the source snapshot"
                )
            task_text = args.task_toml.read_text(encoding="utf-8")
            new_task_text = updated_task_toml(task_text, report, args.attempts)
            instruction_temp = atomic_write(args.apply_to, candidate)
            task_temp = atomic_write(args.task_toml, new_task_text)
            os.replace(task_temp, args.task_toml)
            os.replace(instruction_temp, args.apply_to)
            report["applied"] = True
            report["instruction"] = str(args.apply_to)
            report["task_toml"] = str(args.task_toml)
        else:
            report["applied"] = False
    except RewriteValidationError as exc:
        report = {
            "valid": False,
            "prompt_version": PROMPT_VERSION,
            "model": MODEL,
            "error": str(exc),
        }

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temp = atomic_write(args.report, rendered)
        os.replace(temp, args.report)
    print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
