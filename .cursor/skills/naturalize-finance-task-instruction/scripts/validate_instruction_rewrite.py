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
    r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z])"
)
FENCE_RE = re.compile(r"(?ms)^```[^\n]*\n.*?^```\s*$")
TABLE_RE = re.compile(r"(?m)(?:^\|.*\|\s*\n?){2,}")
LIST_RE = re.compile(r"(?m)^(?:[-*+]|\d+\.)\s+.+$")

# The exact-count token checks validate() enforces, in evaluation order.
# freeze_protected_spans.py derives its frozen token spans from this same
# table (labels and compiled patterns), so the freezer and the validator can
# never disagree about what counts as a number, cell reference, URL, or
# inline-code span.
EXACT_TOKEN_CHECKS = (
    ("fenced code blocks", FENCE_RE),
    ("Markdown tables", TABLE_RE),
    ("list items", LIST_RE),
    ("inline-code spans", INLINE_CODE_RE),
    ("URLs", URL_RE),
    ("cell references", CELL_RE),
    ("numbers", NUMBER_RE),
)

# The subset of EXACT_TOKEN_CHECKS whose tokens the freezer wraps in immutable
# [[Fnn]] markers inside the two rewriteable regions. Structural checks
# (fences, tables, lists) live almost entirely in protected sections, which
# are already enforced byte-for-byte.
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


def require(
    condition: bool,
    message: str,
    checks: list[str],
    expected: str | None = None,
    found: str | None = None,
) -> None:
    """Record a passing check by name, or fail with a self-explanatory message.

    Failure messages always lead with the check name, then state what was
    expected and what was actually found, so a blocked run's validation.json
    is diagnosable without re-deriving the check.
    """
    if not condition:
        detail = "check failed: %s" % message
        if expected is not None:
            detail += " | expected: %s" % expected
        if found is not None:
            detail += " | found: %s" % found
        raise RewriteValidationError(detail)
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
    """The exact anchor list validate() enforces on the rewriteable regions.

    Each spec carries the check name, the source phrase that triggered it,
    the accepted candidate phrases (all lowercase), the region the candidate
    is searched in, and whether the comparison is whitespace-normalized.
    freeze_protected_spans.py uses this same list, so anything frozen before
    the rewrite is exactly what the validator later demands.
    """
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
            expected="a byte-identical copy of the source section",
            found=(
                None
                if candidate_body == source_body
                else first_diff(source_body, candidate_body)
            ),
        )

    for label, pattern in EXACT_TOKEN_CHECKS:
        source_counts = counter(pattern, source)
        candidate_counts = counter(pattern, candidate)
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
            expected="the same %s tokens with the same counts as the source" % label,
            found=found,
        )

    candidate_input = input_section(candidate_sections).lower()
    candidate_mutable_lower = mutable_text(candidate).lower()
    candidate_regions = {
        "preamble": candidate_preamble.lower(),
        "input": candidate_input,
        "mutable": candidate_mutable_lower,
    }
    for spec in protected_anchor_specs(source):
        haystack = candidate_regions[spec["region"]]
        if spec["normalize_ws"]:
            haystack = " ".join(haystack.split())
        accepted = spec["accepted"]
        require(
            any(term in haystack for term in accepted),
            spec["check"],
            checks,
            expected="one of %r (case-insensitive%s) somewhere in the candidate %s"
            % (
                list(accepted),
                ", whitespace-normalized" if spec["normalize_ws"] else "",
                REGION_LABELS[spec["region"]],
            ),
            found="none of those phrases occur there (source anchor was %r)"
            % spec["phrase"],
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
                source_hits = len(pattern.findall(source))
                candidate_hits = len(pattern.findall(candidate))
                require(
                    candidate_hits <= source_hits,
                    "no new answer-value occurrence: %s" % variant,
                    checks,
                    expected="at most %d occurrence(s), matching the source"
                    % source_hits,
                    found="%d occurrence(s) in the candidate" % candidate_hits,
                )

    require(
        "{{" not in candidate and "}}" not in candidate,
        "no unresolved template placeholders",
        checks,
        expected="no '{{' or '}}' anywhere in the candidate",
        found="unresolved '{{'/'}}' placeholder text",
    )
    require(
        candidate.endswith("\n"),
        "candidate has final newline",
        checks,
        expected="candidate text ending with a newline",
        found="candidate ends with %r" % candidate[-8:],
    )

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
