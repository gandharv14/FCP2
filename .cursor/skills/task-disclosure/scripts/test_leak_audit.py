"""Regression tests for provenance-aware numeric disclosure auditing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import disclose


def write_key(task_dir: Path, targets: dict[str, float]) -> None:
    tests = task_dir / "tests"
    tests.mkdir(parents=True)
    (tests / "answer_key.json").write_text(
        json.dumps({"targets": targets, "tolerance": {}}),
        encoding="utf-8",
    )


def custom_record(number: float, references: list[str] | None = None) -> dict:
    return {
        "band": "`Model!B2`",
        "cells": ["Model!B2"],
        "cell_keys": ["Model!B2"],
        "entry": "method_revenue",
        "value": "out_of_catalogue",
        "disposition": "disclosed",
        "source": "custom_method_detector",
        "leak_flag": False,
        "coverage_complete": True,
        "fields": {
            "label": '"Revenue"',
            "band": "Model!B2",
            "representative": "Model!B2",
            "calculation_kind": "calculation",
            "steps": f"multiply (cell Model!B1) by ({number:g})",
        },
        "method_profile": {
            "complete": True,
            "cells": ["Model!B2"],
            "references": references or ["Model!B1"],
            "numbers": [number],
        },
    }


@pytest.mark.parametrize("constant", [0.05, 365.0])
def test_independent_formula_constants_do_not_count_as_target_leaks(
    tmp_path: Path, constant: float
) -> None:
    write_key(tmp_path, {"Outputs!Z9": constant})
    record = custom_record(constant)

    faults = disclose.audit_text(
        disclose.render_section([record]), tmp_path, [record]
    )

    assert not any("matches target" in fault for fault in faults)


def test_unproven_numeric_target_still_fails(tmp_path: Path) -> None:
    write_key(tmp_path, {"Outputs!Z9": 365.0, "Outputs!Z10": 365.0})

    faults = disclose.audit_text(
        "## Workbook disclosure\n- The target result is 365.\n",
        tmp_path,
    )

    assert faults == ["numeric literal 365 matches target 365.0"]


def test_target_connected_formula_literal_still_fails(tmp_path: Path) -> None:
    write_key(tmp_path, {"Outputs!Z9": 365.0})
    record = custom_record(365.0, references=["Outputs!Z9"])

    faults = disclose.audit_text(
        disclose.render_section([record]), tmp_path, [record]
    )

    assert any("numeric literal 365 matches target 365.0" in fault for fault in faults)


def test_literal_exemption_does_not_hide_an_extra_real_leak(tmp_path: Path) -> None:
    write_key(tmp_path, {"Outputs!Z9": 365.0})
    record = custom_record(365.0)
    section = disclose.render_section([record]) + "- The target result is 365.\n"

    faults = disclose.audit_text(section, tmp_path, [record])

    assert faults == ["numeric literal 365 matches target 365.0"]
