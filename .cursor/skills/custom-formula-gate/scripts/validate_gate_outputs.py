#!/usr/bin/env python3
"""Fail-closed validation for Terra custom-formula reports and safe hints."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import openpyxl


EXPECTED_MODEL = "gpt-5.6-terra-high"
EXPECTED_PROMPT_VERSION = "custom-formula-gate-v2"
CLASSES = {
    "standard",
    "standard_variant",
    "custom_logic",
    "definitional",
    "structural",
    "literal_embedded",
    "unclassified",
}
FLAG_CLASSES = {"custom_logic", "literal_embedded"}
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?"
)
CATALOG_ID_RE = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*(standard|variant)\s*\|", re.MULTILINE
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def catalog_variants(path: Path):
    text = path.read_text(encoding="utf-8")
    variants = dict(CATALOG_ID_RE.findall(text))
    if not variants:
        raise ValueError(f"{path} contains no catalog variants")
    return variants


def answer_values(context: dict):
    workbook = Path(context["sources"]["raw_workbook"])
    book = openpyxl.load_workbook(workbook, data_only=True, read_only=True)
    try:
        values = []
        for ref in context["task"].get("answer_cells") or []:
            sheet, separator, coordinate = str(ref).rpartition("!")
            if not separator or sheet not in book.sheetnames:
                raise ValueError(f"invalid answer cell in context: {ref}")
            value = book[sheet][coordinate].value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if math.isfinite(float(value)):
                    values.append(float(value))
        return values
    finally:
        book.close()


def validate_generator(report, context_path, catalog_path):
    generator = report.get("generator") or {}
    expected = {
        "model": EXPECTED_MODEL,
        "prompt_version": EXPECTED_PROMPT_VERSION,
        "context_sha256": sha256(context_path),
        "catalog_sha256": sha256(catalog_path),
    }
    for key, value in expected.items():
        if generator.get(key) != value:
            raise ValueError(
                f"report generator.{key} must be {value!r}, "
                f"got {generator.get(key)!r}"
            )


def validate_agreement(item, catalog):
    classification = item["class"]
    variant = item.get("catalog_variant")
    agreement = item.get("agreement") or {}
    if classification not in {"standard", "standard_variant"}:
        if variant is not None:
            raise ValueError(
                f"{item['band']}: {classification} must not claim a catalog variant"
            )
        return
    expected_catalog_class = (
        "standard" if classification == "standard" else "variant"
    )
    if catalog.get(variant) != expected_catalog_class:
        raise ValueError(
            f"{item['band']}: {variant!r} is not a {expected_catalog_class} "
            "catalog variant"
        )
    periods_tested = agreement.get("periods_tested")
    periods_matched = agreement.get("periods_matched")
    symbolic = agreement.get("exact_symbolic_match") is True
    numeric = (
        isinstance(periods_tested, int)
        and periods_tested > 0
        and periods_matched == periods_tested
    )
    if not numeric and not symbolic:
        raise ValueError(
            f"{item['band']}: catalog match lacks all-period numeric agreement "
            "or an exact symbolic match"
        )
    mapping = item.get("variable_mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError(f"{item['band']}: catalog match lacks variable_mapping")


def validate_report(context, report, catalog):
    expected_task = context["task"]["name"]
    if report.get("schema_version") != "2.0":
        raise ValueError("report schema_version must be '2.0'")
    if report.get("task") != expected_task:
        raise ValueError(
            f"report task must be {expected_task!r}, got {report.get('task')!r}"
        )

    expected = {
        item["band"]: item for item in context.get("key_variables") or []
    }
    if not expected:
        raise ValueError("context contains no key_variables")
    series = report.get("series")
    if not isinstance(series, list):
        raise ValueError("report series must be a list")
    bands = [str(item.get("band") or "") for item in series]
    duplicates = sorted(band for band, count in Counter(bands).items() if count > 1)
    if set(bands) != set(expected) or duplicates:
        raise ValueError(
            "report key-variable coverage mismatch; "
            f"missing={sorted(set(expected) - set(bands))!r} "
            f"extra={sorted(set(bands) - set(expected))!r} "
            f"duplicates={duplicates!r}"
        )

    counts = Counter()
    for item in series:
        band = item["band"]
        classification = item.get("class")
        if classification not in CLASSES:
            raise ValueError(f"{band}: unsupported class {classification!r}")
        counts[classification] += 1
        if item.get("key_rank") != expected[band].get("key_rank"):
            raise ValueError(f"{band}: key_rank does not match context")
        if not str(item.get("role") or "").strip():
            raise ValueError(f"{band}: role is required")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"{band}: reason is required")
        validate_agreement(item, catalog)

    reported_counts = report.get("counts") or {}
    if {key: value for key, value in reported_counts.items() if value} != dict(counts):
        raise ValueError(
            f"report counts do not match series: {reported_counts!r} != {dict(counts)!r}"
        )
    expected_verdict = (
        "REVIEW"
        if counts["unclassified"]
        else "FLAG"
        if any(counts[value] for value in FLAG_CLASSES)
        else "PASS"
    )
    if report.get("verdict") != expected_verdict:
        raise ValueError(
            f"report verdict must be {expected_verdict}, got {report.get('verdict')!r}"
        )
    return {
        item["band"]: item["class"]
        for item in series
        if item["class"] in FLAG_CLASSES
    }


def validate_hints(context, hints, flagged):
    if hints.get("schema_version") != "1.0":
        raise ValueError("hints schema_version must be '1.0'")
    if hints.get("task") != context["task"]["name"]:
        raise ValueError("hints task does not match context")
    rows = hints.get("hints")
    if not isinstance(rows, list):
        raise ValueError("hints.hints must be a list")

    seen = []
    formulas = {
        formula.lstrip("=").strip()
        for variable in context.get("key_variables") or []
        for formula in variable.get("formula_samples") or []
        if formula
    }
    targets = answer_values(context)
    for index, hint in enumerate(rows):
        for key in ("title", "guidance", "bands", "classes"):
            if not hint.get(key):
                raise ValueError(f"hint {index}: missing {key}")
        guidance = str(hint["guidance"]).strip()
        if "=" in guidance:
            raise ValueError(f"hint {index}: guidance contains a formula")
        if any(formula and formula in guidance for formula in formulas):
            raise ValueError(f"hint {index}: guidance reproduces a golden formula")
        classes = set(hint["classes"])
        if not classes <= FLAG_CLASSES:
            raise ValueError(f"hint {index}: non-custom class included")
        hint_bands = [str(band) for band in hint["bands"]]
        expected_classes = {
            flagged[band] for band in hint_bands if band in flagged
        }
        if classes != expected_classes:
            raise ValueError(
                f"hint {index}: classes {sorted(classes)!r} do not match "
                f"covered bands {sorted(expected_classes)!r}"
            )
        seen.extend(hint_bands)
        for raw in NUMBER_RE.findall(guidance):
            number = float(raw.replace(",", ""))
            for target in targets:
                if float(target).is_integer() and abs(target) < 10000:
                    continue
                if abs(number - target) <= 1e-12 * max(1.0, abs(target)):
                    raise ValueError(
                        f"hint {index}: numeric literal {raw} reproduces an answer"
                    )
    duplicates = sorted(band for band, count in Counter(seen).items() if count > 1)
    flagged_bands = set(flagged)
    if set(seen) != flagged_bands or duplicates:
        raise ValueError(
            "hint coverage mismatch; "
            f"missing={sorted(flagged_bands - set(seen))!r} "
            f"extra={sorted(set(seen) - flagged_bands)!r} duplicates={duplicates!r}"
        )


def validate(context_path: Path, report_path: Path, hints_path: Path, catalog_path: Path):
    context = load_json(context_path)
    report = load_json(report_path)
    hints = load_json(hints_path)
    if context.get("schema_version") != "2.0":
        raise ValueError("context schema_version must be '2.0'")
    validate_generator(report, context_path, catalog_path)
    flagged = validate_report(context, report, catalog_variants(catalog_path))
    validate_hints(context, hints, flagged)
    if report.get("verdict") == "REVIEW":
        raise ValueError("unclassified key variables require manual review")
    return {
        "valid": True,
        "task": context["task"]["name"],
        "model": EXPECTED_MODEL,
        "verdict": report["verdict"],
        "key_variables": len(context["key_variables"]),
        "flagged_variables": len(flagged),
        "hint_groups": len(hints["hints"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("hints", type=Path)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "CATALOG.md",
    )
    args = parser.parse_args(argv)
    result = validate(args.context, args.report, args.hints, args.catalog)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
