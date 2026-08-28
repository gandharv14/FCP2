#!/usr/bin/env python3
"""Decide whether a zero-variable normalization may ship as a plain (no-MCP) task."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


EXTRACTION_CODES = frozenset({"unparsable_reference", "unknown_sheet"})
SENTINEL = re.compile(r"no candidates were identified", re.I)
MIN_DRAFT_ROWS = 3
MIN_DRAFT_FRACTION = 0.02
DIALOGUE_NOTES_NAME = "additional-assumptions.md"
DIALOGUE_NOTES_COPY = f"COPY {DIALOGUE_NOTES_NAME} /app/{DIALOGUE_NOTES_NAME}"
DIALOGUE_APPLIED_MARKER = "tests/dialogue-applied.json"


def dialogue_notes_expected(bundle: Path) -> bool:
    """True when apply left a marker and the main Dockerfile copies the notes."""
    marker = bundle / DIALOGUE_APPLIED_MARKER
    dockerfile = bundle / "environment" / "Dockerfile"
    if not marker.is_file() or not dockerfile.is_file():
        return False
    return DIALOGUE_NOTES_COPY in dockerfile.read_text(encoding="utf-8")


def _load(path: Path):
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_rows(run: Path, workbook: str) -> int:
    candidates = [
        run / f"{workbook}-inputs-variable-sources.metadata.json",
        *sorted(run.glob("*-inputs-variable-sources.metadata.json")),
    ]
    for meta in candidates:
        if meta.is_file():
            data = json.loads(meta.read_text(encoding="utf-8"))
            return int(data.get("inventory_rows") or 0)
    inventories = [
        run / f"{workbook}-inputs-variable-sources.inventory.json",
        *sorted(run.glob("*-inputs-variable-sources.inventory.json")),
    ]
    for inventory in inventories:
        if inventory.is_file():
            data = json.loads(inventory.read_text(encoding="utf-8"))
            return len(data.get("variables") or [])
    return 0


def forced_exclusion_ids(run: Path) -> list[str]:
    path = run / "oracle_forced_exclusions.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, dict):
        return [str(item) for item in (raw.get("ids") or raw.get("exclusions") or [])]
    return []


def exclusion_codes(report: dict, exclusions: dict | None) -> list[str]:
    codes = []
    for row in report.get("dispositions") or []:
        if row.get("status") != "excluded":
            continue
        if row.get("code"):
            codes.append(row["code"])
    if codes:
        return codes
    for item in (exclusions or {}).get("exclusions") or []:
        if item.get("code"):
            codes.append(item["code"])
    histogram = report.get("exclusion_reason_codes") or {}
    for code, count in histogram.items():
        codes.extend([code] * int(count or 0))
    return codes


def evaluate(run: Path) -> dict:
    report = _load(run / "normalization_report.json")
    spec = _load(run / "normalized.json")
    draft = _load(run / "draft.json")
    exclusions_path = run / "exclusions.json"
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8")) if exclusions_path.is_file() else {}
    # Metadata is the primary source of the workbook id; the directory name
    # is only a fallback. (An unconditional overwrite here used to discard
    # the metadata id and silently weaken the draft-row floor for
    # unconventionally named runs.)
    workbook = str(report.get("workbook") or "").strip()
    if not workbook:
        # draft.json carries "<id>-inputs.xlsx — ..." rather than a bare id
        asset = re.match(r"\s*(.+?)-inputs\.xlsx", str(draft.get("asset") or ""))
        workbook = asset.group(1) if asset else ""
    dir_id = re.sub(r"-variable-sources$", "", run.name) \
        if run.name.endswith("-variable-sources") else ""
    if workbook and dir_id and workbook != dir_id:
        return {"eligible": False, "mode": "fail",
                "reason": "workbook id mismatch: metadata says %r but the "
                          "run directory is %r" % (workbook, run.name)}
    workbook = workbook or dir_id or run.name
    variables = spec.get("variables") if isinstance(spec.get("variables"), list) else None
    if variables is None:
        return {"eligible": False, "mode": "fail", "reason": "normalized.json has no variables list"}
    n_vars = len(variables)
    draft_rows = len(draft.get("rows") or [])
    inv_rows = inventory_rows(run, workbook)
    first_path = run / "first_normalization.json"
    first_vars = report.get("first_atomic_variables")
    if first_path.is_file():
        first_vars = json.loads(first_path.read_text(encoding="utf-8")).get("atomic_variables", first_vars)
    forced = forced_exclusion_ids(run)
    codes = exclusion_codes(report, exclusions)
    extraction = sorted(set(codes) & EXTRACTION_CODES)
    sentinel = any(
        SENTINEL.search(" ".join([
            str(row.get("variable_text") or ""),
            str(row.get("value_text") or ""),
            str(row.get("source_notes") or ""),
        ]))
        for row in draft.get("rows") or []
    )
    floor = max(MIN_DRAFT_ROWS, int(MIN_DRAFT_FRACTION * inv_rows))

    result = {
        "mode": "mcp",
        "eligible": False,
        "reason": "",
        "atomic_variables": n_vars,
        "first_atomic_variables": first_vars,
        "draft_rows": draft_rows,
        "inventory_rows": inv_rows,
        "draft_row_floor": floor,
        "forced_exclusions": forced,
        "extraction_codes": extraction,
        "exclusion_reason_codes": dict(Counter(codes)),
        "sentinel_row": sentinel,
    }
    if n_vars > 0:
        result["mode"] = "mcp"
        result["reason"] = "non-empty spec"
        return result

    if first_vars not in (None, 0) and int(first_vars) > 0:
        result["mode"] = "fail"
        result["reason"] = (
            "spec was non-empty at first normalization "
            f"({first_vars} variables) and cannot be downgraded to plain"
        )
        return result
    if forced:
        result["mode"] = "fail"
        result["reason"] = "oracle_forced_exclusions.json is present; that is post-hoc evidence variables existed"
        return result
    if extraction:
        result["mode"] = "fail"
        result["reason"] = "extraction defect codes present: " + ", ".join(extraction)
        return result
    if sentinel:
        result["mode"] = "fail"
        result["reason"] = "audit sentinel row (no candidates were identified)"
        return result
    if draft_rows < floor:
        result["mode"] = "fail"
        result["reason"] = (
            f"draft_rows {draft_rows} below floor {floor} "
            f"(min {MIN_DRAFT_ROWS} and {MIN_DRAFT_FRACTION:.0%} of {inv_rows} inventory rows)"
        )
        return result
    dispositions = report.get("dispositions") or []
    if not dispositions or any(d.get("status") != "excluded" for d in dispositions):
        result["mode"] = "fail"
        result["reason"] = "zero variables but dispositions are not all excluded"
        return result
    if any(not str(d.get("reason") or "").strip() for d in dispositions):
        result["mode"] = "fail"
        result["reason"] = "an excluded disposition lacks a reason"
        return result
    result["mode"] = "plain"
    result["eligible"] = True
    dominant = Counter(codes).most_common(1)
    cause = dominant[0][0] if dominant else "excluded"
    result["reason"] = (
        f"all {draft_rows} audit rows excluded; dominant cause: {cause}"
    )
    result["plain_reason"] = result["reason"]
    return result


def check_plain_environment(bundle: Path, workbook_id: str) -> dict:
    """Closed-world check for a no-MCP staged bundle environment/."""
    environment = bundle / "environment"
    if not environment.is_dir():
        return {"valid": False, "missing_files": ["environment/"], "unknown_files": [],
                "symlinks": [], "workbooks": []}
    # The masker names the artifact after the golden source's suffix, so the
    # single required workbook may be either <id>-inputs.xlsx or .xlsm.
    artifact_names = {f"{workbook_id}-inputs.xlsx", f"{workbook_id}-inputs.xlsm"}
    expected = {"Dockerfile"}
    if dialogue_notes_expected(bundle):
        expected.add(DIALOGUE_NOTES_NAME)
    actual: set[str] = set()
    symlinks: list[str] = []
    for path in environment.rglob("*"):
        relative = path.relative_to(environment).as_posix()
        if path.is_symlink():
            symlinks.append(relative)
        if path.is_file() or path.is_symlink():
            actual.add(relative)
    workbooks = sorted(
        relative for relative in actual
        if Path(relative).suffix.casefold() in {".xlsx", ".xlsm"}
    )
    missing_set = expected - actual
    if not (actual & artifact_names):
        missing_set = missing_set | {f"{workbook_id}-inputs.xlsx"}
    missing = sorted(missing_set)
    unknown = sorted(actual - expected - artifact_names)
    if len(workbooks) != 1:
        missing.append("exactly one workbook at environment root")
    return {
        "valid": not (missing or unknown or symlinks) and len(workbooks) == 1,
        "missing_files": missing,
        "unknown_files": unknown,
        "symlinks": sorted(symlinks),
        "workbooks": workbooks,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    run = Path(args.run)
    result = evaluate(run)
    out = Path(args.report) if args.report else run / "plain_eligibility.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(result["mode"])
    if result["mode"] == "fail":
        print(result["reason"], file=sys.stderr)
        return 114
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
