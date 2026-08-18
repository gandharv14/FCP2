from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

from xl_formula_hint_tasks import load_formula_artifacts
from xl_output_task import add_custom_formula_hints


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".cursor" / "skills" / "custom-formula-gate"
EXTRACTOR = SKILL / "scripts" / "extract_gate_context.py"
VALIDATOR = SKILL / "scripts" / "validate_gate_outputs.py"
CATALOG = SKILL / "CATALOG.md"


def load_validator():
    spec = importlib.util.spec_from_file_location("formula_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_fixture(tmp_path):
    source = tmp_path / "source"
    seg = tmp_path / "seg"
    source.mkdir()
    seg.mkdir()

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Model"
    sheet["A2"] = "Revenue"
    sheet["B2"] = 100
    sheet["C2"] = 110
    sheet["A3"] = "EBITDA"
    sheet["B3"] = "=B2*0.2"
    sheet["C3"] = "=C2*0.2"
    sheet["A4"] = "Enterprise value"
    sheet["B4"] = "=B3*5"
    sheet["C4"] = "=C3*5"
    workbook.save(source / "0001.xlsx")

    (seg / "curation.toml").write_text(
        """\
[[output]]
band = "Model!B4:C4"
sheet = "Model"
label = "Enterprise value"
include = true
name = "Enterprise value"
""",
        encoding="utf-8",
    )
    (seg / "bands.csv").write_text(
        """\
band,kind,component,width,bucket,label,pattern
Model!B2:C2,input,,2,input,Revenue,input
Model!B3:C3,formula,,2,middle,EBITDA,=RC[-1]*0.2
Model!B4:C4,formula,,2,output,Enterprise value,=RC[-1]*5
""",
        encoding="utf-8",
    )
    steps = [
        {
            "order": 0,
            "node": "Model!B2:C2",
            "bucket": "input",
            "sheet": "Model",
            "label": "Revenue",
            "formula": "input",
            "depth": 0,
            "inputs": [],
            "values": [100, 110],
        },
        {
            "order": 1,
            "node": "Model!B3:C3",
            "bucket": "middle",
            "sheet": "Model",
            "label": "EBITDA",
            "formula": "=RC[-1]*0.2",
            "depth": 1,
            "inputs": ["Model!B2:C2"],
            "values": [20, 22],
        },
        {
            "order": 2,
            "node": "Model!B4:C4",
            "bucket": "output",
            "sheet": "Model",
            "label": "Enterprise value",
            "formula": "=RC[-1]*5",
            "depth": 2,
            "inputs": ["Model!B3:C3"],
            "values": [100, 110],
        },
    ]
    (seg / "lineage.json").write_text(
        json.dumps(
            {
                "workbook": "0001",
                "outputs": [{"output": "Model!B4:C4"}],
                "traces": [
                    {
                        "output": "Model!B4:C4",
                        "label": "Enterprise value",
                        "band_steps": steps,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return source, seg


def test_extractor_needs_no_rollout_and_ranks_all_key_variables(tmp_path):
    source, seg = write_fixture(tmp_path)
    output = tmp_path / "context.json"
    subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            "0001",
            "--repo-root",
            str(tmp_path),
            "--source",
            str(source),
            "--seg-dir",
            str(seg),
            "--output",
            str(output),
        ],
        check=True,
    )
    context = json.loads(output.read_text(encoding="utf-8"))
    assert "post_rollout_check" not in context
    assert [row["band"] for row in context["key_variables"]] == [
        "Model!B4:C4",
        "Model!B3:C3",
    ]
    assert [row["key_rank"] for row in context["key_variables"]] == [1, 2]
    assert context["key_variables"][0]["direct_drivers"][0]["label"] == "EBITDA"
    assert context["key_variables"][1]["direct_drivers"][0]["label"] == "Revenue"


def gate_artifacts(tmp_path, classification="custom_logic"):
    workbook = openpyxl.Workbook()
    workbook.active.title = "Model"
    workbook["Model"]["B4"] = 123456.75
    golden = tmp_path / "golden.xlsx"
    workbook.save(golden)
    context = {
        "schema_version": "2.0",
        "task": {
            "name": "0001-outputs",
            "workbook": "0001",
            "answer_cells": ["Model!B4"],
        },
        "sources": {"raw_workbook": str(golden)},
        "key_variables": [
            {
                "key_rank": 1,
                "band": "Model!B3",
                "formula_samples": ["=IF(B2>0,B2*0.5,0)"],
            }
        ],
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    report = {
        "schema_version": "2.0",
        "task": "0001-outputs",
        "generator": {
            "model": "gpt-5.6-terra-high",
            "prompt_version": "custom-formula-gate-v2",
            "context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        },
        "verdict": "REVIEW" if classification == "unclassified" else "FLAG",
        "counts": {classification: 1},
        "series": [
            {
                "key_rank": 1,
                "band": "Model!B3",
                "label": "Interest",
                "role": "interest_expense_income",
                "class": classification,
                "catalog_variant": None,
                "variable_mapping": {},
                "agreement": {
                    "periods_tested": 1,
                    "periods_matched": 0,
                    "exact_symbolic_match": False,
                },
                "reason": "The formula uses a workbook-specific predicate.",
            }
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    hints = {
        "schema_version": "1.0",
        "task": "0001-outputs",
        "hints": (
            []
            if classification == "unclassified"
            else [
                {
                    "title": "Interest timing",
                    "guidance": "Use half-year timing only when the balance is positive.",
                    "bands": ["Model!B3"],
                    "classes": ["custom_logic"],
                }
            ]
        ),
    }
    hints_path = tmp_path / "hints.json"
    hints_path.write_text(json.dumps(hints), encoding="utf-8")
    return context_path, report_path, hints_path


def test_validator_enforces_terra_and_accepts_safe_custom_hints(tmp_path):
    validator = load_validator()
    context, report, hints = gate_artifacts(tmp_path)
    result = validator.validate(context, report, hints, CATALOG)
    assert result["valid"] is True
    assert result["model"] == "gpt-5.6-terra-high"
    assert result["verdict"] == "FLAG"

    data = json.loads(report.read_text(encoding="utf-8"))
    data["generator"]["model"] = "gpt-5.6-sol-high"
    report.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="generator.model"):
        validator.validate(context, report, hints, CATALOG)


def test_validator_blocks_unclassified_variables(tmp_path):
    validator = load_validator()
    context, report, hints = gate_artifacts(tmp_path, "unclassified")
    with pytest.raises(ValueError, match="manual review"):
        validator.validate(context, report, hints, CATALOG)


def test_packaging_reuses_validated_method_hints(tmp_path):
    context_path, report_path, hints_path = gate_artifacts(tmp_path)
    report, hints = load_formula_artifacts(
        "0001-outputs",
        report_path,
        hints_path,
        [123456.75],
        context_path=context_path,
    )
    instruction = "Task\n\n## Output\n\nWrite answers."
    updated = add_custom_formula_hints(instruction, hints)
    assert report["generator"]["model"] == "gpt-5.6-terra-high"
    assert "## Custom formula hints" in updated
    assert updated.index("## Custom formula hints") < updated.index("## Output")
    assert "=IF(" not in updated
