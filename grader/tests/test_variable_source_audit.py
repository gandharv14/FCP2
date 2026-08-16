from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import openpyxl

from xl_variable_source_audit import build_inventory, validate_table


class VariableSourceAuditTests(unittest.TestCase):
    def test_inventory_is_deterministic_and_skips_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "0001-inputs.xlsx"
            book = openpyxl.Workbook()
            sheet = book.active
            sheet.title = "Inputs"
            sheet["A1"] = "Tax rate"
            sheet["B1"] = 0.25
            book.save(artifact)

            seg_dir = root / "seg"
            seg_dir.mkdir()
            (seg_dir / "segments.json").write_text(json.dumps({
                "inputs": [
                    {
                        "band": "Inputs!A1",
                        "sheet": "Inputs",
                        "label": "Tax rate",
                        "kind": "label",
                        "vtype": "text",
                        "cells": ["Inputs!A1"],
                    },
                    {
                        "band": "Inputs!B1",
                        "sheet": "Inputs",
                        "label": "Tax rate / %",
                        "kind": "input",
                        "vtype": "numeric",
                        "cells": ["Inputs!B1"],
                    },
                ],
                "embedded_literals": [],
            }), encoding="utf-8")

            first = build_inventory("0001", artifact, seg_dir)
            second = build_inventory("0001", artifact, seg_dir)
            self.assertEqual(first, second)
            self.assertEqual(len(first["variables"]), 1)
            self.assertEqual(first["variables"][0]["value_summary"],
                             {"count": 1, "value": 0.25})

    def test_table_validation_rejects_unknown_refs_and_values(self):
        rows = [json.dumps({
            "band": "Inputs!B1",
            "sheet": "Inputs",
            "label": "Tax rate / %",
            "kind": "input",
            "value_type": "numeric",
            "value_summary": {"count": 1, "value": 0.25},
        }, separators=(",", ":"))]
        metadata = {"sheet_names": ["Inputs"]}
        valid = (
            "| Variable | Workbook cells and values | "
            "Plausible external source(s) |\n"
            "|---|---|---|\n"
            "| Tax rate | `Inputs!B1` = 0.25 | Tax authority |"
        )
        self.assertEqual(validate_table(valid, rows, metadata), [])

        unknown_ref = valid.replace("Inputs!B1", "Inputs!C1")
        self.assertIn(
            "reference not in inventory: Inputs!C1",
            validate_table(unknown_ref, rows, metadata),
        )

        unknown_value = valid.replace("0.25", "0.3")
        violations = validate_table(unknown_value, rows, metadata)
        self.assertTrue(any("value not in inventory: 0.3" in item
                            for item in violations))


if __name__ == "__main__":
    unittest.main()
