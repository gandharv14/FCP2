#!/usr/bin/env python3
"""Regression checks for the 0233/0255 disclosure faithfulness failures."""

from __future__ import annotations

import json
from pathlib import Path

import disclose


ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    gold_0233 = disclose.Book(ROOT.parent / "4-10 100" / "0233.xlsx")
    expected_labels = {
        "Control!F58": "Facility Amount",
        "Control!F64": "Maturity",
        "DCF!AE31": "% net revenue",
        "DDM!H177": "Leverage (x)",
        "Model!AC86": "Personnel costs",
    }
    for cell, expected in expected_labels.items():
        actual = gold_0233.row_label(cell)
        if actual != expected:
            raise AssertionError(f"{cell}: {actual!r} != {expected!r}")

    task_0233 = ROOT.parent / "tasks_outputs_mcp" / "0233-outputs"
    delivered_0233 = disclose.Book(disclose.find_environment(task_0233))
    unread = disclose.detect_populated_but_unread(gold_0233, delivered_0233)
    forbidden = {
        "`DDM!F42:AE42`",
        "`DDM!F51:AE51`",
        "`Model!J13:BT13`",
    }
    if forbidden & {record.get("band") for record in unread}:
        raise AssertionError("blank removed rows were disclosed as populated")

    task_0255 = ROOT.parent / "tasks_outputs_mcp" / "0255-outputs"
    gold_0255 = disclose.Book(ROOT.parent / "4-10 100" / "0255.xlsx")
    delivered_0255 = disclose.Book(disclose.find_environment(task_0255))
    targets_0255 = [
        f"R assumptions!{column}50"
        for column in "DEFGHIJKLM"
    ]
    aggregates = disclose.detect_aggregate_scope(
        gold_0255, delivered_0255, targets_0255
    )
    if len(aggregates) != 1:
        raise AssertionError(f"expected one grouped aggregate, got {len(aggregates)}")
    aggregate = aggregates[0]
    if aggregate["band"] != "`'R assumptions'!D50:M50`":
        raise AssertionError(f"wrong aggregate band: {aggregate['band']}")
    labels = aggregate["aggregate_member_rows"]["labels"]
    if len(labels) != 9 or "Fulfillment revenue" not in labels:
        raise AssertionError(f"incomplete aggregate labels: {labels}")
    if "SAR '000" in labels:
        raise AssertionError("unit stamp was retained as a semantic label")

    selection = json.loads(
        (ROOT.parent / "runs" / "disclosure" / "0255-outputs" / "bands.json")
        .read_text(encoding="utf-8")
    )
    records, _, claimed = disclose.detect_custom_methods(
        gold_0255,
        delivered_0255,
        selection["bands"],
        set(selection["target_keys"]),
        resolutions_path=(
            ROOT.parent
            / "runs"
            / "disclosure"
            / "0255-outputs"
            / "role_resolutions.json"
        ),
    )
    if not records or not claimed:
        raise AssertionError("complete custom mechanics were dropped")
    if any(record.get("coverage_complete") is not True for record in records):
        raise AssertionError("incomplete custom method reached agent records")

    serialized_faults = disclose.audit_text(
        "## Workbook disclosure\n- X: multiply (A) by (B).\n", task_0255
    )
    if any("formula-serialized" in fault for fault in serialized_faults):
        raise AssertionError("literal formula translation remained a blocking fault")

    for workbook in ("0248", "0251"):
        task = ROOT.parent / "tasks_outputs_mcp" / f"{workbook}-outputs"
        if not (task / "tests" / "disclosure.json").is_file():
            continue
        report = disclose.verify_task(task)
        if any("non-blank" in fault for fault in report.get("faults", [])):
            raise AssertionError(
                f"{workbook}: verifier inspected records not visible to the agent"
            )

    print("0233/0255 disclosure faithfulness regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
