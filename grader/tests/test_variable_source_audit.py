from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl

from xl_task_build import read_env_key
from xl_variable_source_audit import (
    build_inventory,
    resolve_segmentation_directory,
    validate_table,
)


class VariableSourceAuditTests(unittest.TestCase):
    def test_rl_key_takes_precedence_over_other_audit_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env"
            env_file.write_text(
                "lbx_api_key=stale-key\n"
                "aligerr_org_key=org-key\n"
                "rl_api_key=current-key\n",
                encoding="utf-8",
            )
            self.assertEqual(read_env_key(env_file), "current-key")

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

    def test_pinned_inactive_segmentation_generation_is_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "source-generation"
            generation_dir = root / "segmentation-generation"
            generation_dir.mkdir()
            manifest = generation_dir / "generation-manifest.json"
            manifest.write_text('{"generation_id":"seg-id"}', encoding="utf-8")

            with (
                mock.patch(
                    "xl_source_publication.resolve_source_generation_by_id",
                    return_value=(source_dir, {"generation_id": "source-id"}),
                ) as source_resolver,
                mock.patch(
                    "xl_seg.publication.resolve_generation_by_id",
                    return_value=(generation_dir, {"generation_id": "seg-id"}),
                ) as segmentation_resolver,
            ):
                resolved, provenance = resolve_segmentation_directory(
                    root / "seg-root",
                    "0001",
                    segmentation_generation_id="seg-id",
                    source_generation_root=root / "source-root",
                    source_generation_id="source-id",
                )

            self.assertEqual(resolved, generation_dir)
            self.assertEqual(provenance["generation_id"], "seg-id")
            self.assertEqual(
                provenance["source_generation_id"], "source-id"
            )
            source_resolver.assert_called_once_with(
                root / "source-root" / "0001", "source-id"
            )
            segmentation_resolver.assert_called_once_with(
                root / "seg-root" / "0001",
                "seg-id",
                source_generation_dir=source_dir,
                require_pass=True,
            )

    def test_partial_generation_binding_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "pinned audit requires"):
            resolve_segmentation_directory(
                "seg_out",
                "0001",
                segmentation_generation_id="seg-id",
            )


if __name__ == "__main__":
    unittest.main()
