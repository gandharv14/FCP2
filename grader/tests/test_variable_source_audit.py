from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl

import xl_variable_source_audit
from xl_variable_source_audit import (
    build_inventory,
    resolve_table,
    validate_row_ids,
    validate_table,
)

try:
    # The real downstream consumer; requires fastmcp at import time.
    import xl_variable_mcp

    def run_downstream_import(markdown_path, output_path):
        with contextlib.redirect_stdout(io.StringIO()):
            xl_variable_mcp.main(
                ["import", str(markdown_path), str(output_path)])
        return json.loads(Path(output_path).read_text(encoding="utf-8"))
except ModuleNotFoundError:
    # Same parser xl_variable_mcp.py's import subcommand delegates to.
    from mcp_env.import_table import import_table

    def run_downstream_import(markdown_path, output_path):
        result = import_table(Path(markdown_path))
        Path(output_path).write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return result


SEGMENTS = {
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
}


def save_workbook(path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Inputs"
    sheet["A1"] = "Tax rate"
    sheet["B1"] = 0.25
    book.save(path)


MODEL_HEADER = "| Variable | Inventory row IDs | Plausible external source(s) |"
FINAL_HEADER = (
    "| Variable | Workbook cells and values | Plausible external source(s) |"
)

# Sorted inventory order for the two-variable fixture below:
#   R0001 = Inputs!B2:D2 "Revenue / 000$"  (values 100, 110, 121)
#   R0002 = Inputs!B1    "Tax rate / %"    (value 0.25)
GOOD_MODEL_TABLE = "\n".join([
    MODEL_HEADER,
    "|---|---|---|",
    "| Tax assumptions | R0002 | Tax authority (https://example.gov/rates) |",
    "| Historical revenue | R0001 | Audited financial statements |",
])

EXPECTED_FINAL_TABLE = "\n".join([
    FINAL_HEADER,
    "|---|---|---|",
    "| Tax assumptions | `Inputs!B1` Tax rate / %: `0.25` "
    "| Tax authority (https://example.gov/rates) |",
    "| Historical revenue | `Inputs!B2:D2` Revenue / 000$: `[100, 110, 121]` "
    "| Audited financial statements |",
])


def write_two_variable_fixture(root):
    """Workbook + segments with one label, one scalar, and one series band."""
    artifact = root / "0001-inputs.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Inputs"
    sheet["A1"] = "Tax rate"
    sheet["B1"] = 0.25
    sheet["A2"] = "Revenue"
    sheet["B2"] = 100
    sheet["C2"] = 110
    sheet["D2"] = 121
    book.save(artifact)
    seg_dir = root / "seg"
    seg_dir.mkdir(exist_ok=True)
    (seg_dir / "segments.json").write_text(json.dumps({
        "inputs": [
            {"band": "Inputs!A1", "sheet": "Inputs", "label": "Tax rate",
             "kind": "label", "vtype": "text", "cells": ["Inputs!A1"]},
            {"band": "Inputs!B1", "sheet": "Inputs", "label": "Tax rate / %",
             "kind": "input", "vtype": "numeric", "cells": ["Inputs!B1"]},
            {"band": "Inputs!B2:D2", "sheet": "Inputs",
             "label": "Revenue / 000$", "kind": "input", "vtype": "numeric",
             "cells": ["Inputs!B2", "Inputs!C2", "Inputs!D2"]},
        ],
        "embedded_literals": [],
    }), encoding="utf-8")
    return artifact, seg_dir


def make_model_stub(replies):
    """Sequential _call_model replacement; records every call's messages."""
    calls = []

    def stub(endpoint, api_key, model, project_id, messages):
        calls.append(messages)
        return replies[min(len(calls) - 1, len(replies) - 1)], "stop"

    stub.calls = calls
    return stub


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
            self.assertEqual(first["variables"][0]["row_id"], "R0001")
            self.assertEqual(first["variables"][0]["value_summary"],
                             {"count": 1, "value": 0.25})

    def test_inventory_rows_carry_deterministic_row_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact, seg_dir = write_two_variable_fixture(root)
            first = build_inventory("0001", artifact, seg_dir)
            second = build_inventory("0001", artifact, seg_dir)
            self.assertEqual(first, second)
            self.assertEqual(
                [(row["row_id"], row["band"]) for row in first["variables"]],
                [("R0001", "Inputs!B2:D2"), ("R0002", "Inputs!B1")],
            )

    def _run_inventory_main(self, inputs, seg_root, audit_root):
        with contextlib.redirect_stdout(io.StringIO()):
            return xl_variable_source_audit.main([
                "0001",
                "--inputs-root", str(inputs),
                "--seg-root", str(seg_root),
                "--audit-root", str(audit_root),
                "--inventory-only",
            ])

    def test_main_resolves_xlsm_inputs_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / "inputs"
            inputs.mkdir()
            save_workbook(inputs / "0001-inputs.xlsm")
            seg_root = root / "seg"
            (seg_root / "0001").mkdir(parents=True)
            (seg_root / "0001" / "segments.json").write_text(
                json.dumps(SEGMENTS), encoding="utf-8")
            audit_root = root / "runs"

            rc = self._run_inventory_main(inputs, seg_root, audit_root)
            self.assertEqual(rc, 0)
            inventory = json.loads(
                (audit_root / "0001-variable-sources"
                 / "0001-inputs-variable-sources.inventory.json"
                 ).read_text(encoding="utf-8"))
            self.assertEqual(len(inventory["variables"]), 1)

    def test_main_prefers_xlsx_when_both_suffixes_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / "inputs"
            inputs.mkdir()
            save_workbook(inputs / "0001-inputs.xlsx")
            # A stale sibling with a different value must lose to the .xlsx.
            book = openpyxl.Workbook()
            sheet = book.active
            sheet.title = "Inputs"
            sheet["A1"] = "Tax rate"
            sheet["B1"] = 0.99
            book.save(inputs / "0001-inputs.xlsm")
            seg_root = root / "seg"
            (seg_root / "0001").mkdir(parents=True)
            (seg_root / "0001" / "segments.json").write_text(
                json.dumps(SEGMENTS), encoding="utf-8")
            audit_root = root / "runs"

            rc = self._run_inventory_main(inputs, seg_root, audit_root)
            self.assertEqual(rc, 0)
            inventory = json.loads(
                (audit_root / "0001-variable-sources"
                 / "0001-inputs-variable-sources.inventory.json"
                 ).read_text(encoding="utf-8"))
            self.assertEqual(inventory["variables"][0]["value_summary"],
                             {"count": 1, "value": 0.25})

    def test_main_missing_artifact_raises_same_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / "inputs"
            inputs.mkdir()
            seg_root = root / "seg"
            (seg_root / "0001").mkdir(parents=True)
            (seg_root / "0001" / "segments.json").write_text(
                json.dumps(SEGMENTS), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                self._run_inventory_main(inputs, seg_root, root / "runs")

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

    def _generate(self, root, stub):
        artifact, seg_dir = write_two_variable_fixture(root)
        out_dir = root / "audit"
        with mock.patch.object(xl_variable_source_audit, "_call_model", stub):
            metadata = xl_variable_source_audit.generate_audit(
                "0001", artifact, seg_dir, out_dir, "test-key",
                log=lambda *args: None)
        return metadata, out_dir / "0001-inputs-variable-sources.md"

    def test_stubbed_reply_resolves_to_downstream_compatible_table(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub = make_model_stub([GOOD_MODEL_TABLE])
            metadata, markdown_path = self._generate(root, stub)

            self.assertEqual(len(stub.calls), 1)
            self.assertEqual(metadata["prompt_version"],
                             "variable-source-audit-v2")
            self.assertTrue(metadata["row_id_resolution"])
            self.assertEqual(metadata["validation"]["status"], "passed")
            self.assertIn("row_id_citations",
                          metadata["validation"]["checks"])

            markdown = markdown_path.read_text(encoding="utf-8")
            # Byte-identical final table in the historical downstream format.
            self.assertIn(EXPECTED_FINAL_TABLE, markdown)
            # No row_id ever leaks into the downstream artifact.
            self.assertIsNone(
                xl_variable_source_audit.ROW_ID_RE.search(markdown))
            # The import-parser header contract still matches: the header row
            # names a variable, a value, and a source column.
            header = next(line for line in markdown.splitlines()
                          if line.startswith("|"))
            self.assertEqual(header, FINAL_HEADER)
            lowered = header.lower()
            self.assertTrue("variable" in lowered and "value" in lowered
                            and "source" in lowered)

            draft = run_downstream_import(markdown_path,
                                          root / "draft.json")
            self.assertEqual(draft["row_count"], 2)
            self.assertEqual(
                [(row["draft_id"], row["value_text"]) for row in draft["rows"]],
                [("tax-assumptions", "Inputs!B1 Tax rate / %: 0.25"),
                 ("historical-revenue",
                  "Inputs!B2:D2 Revenue / 000$: [100, 110, 121]")],
            )
            self.assertEqual(draft["rows"][0]["source_urls"],
                             ["https://example.gov/rates"])

    def test_unknown_row_id_fails_validation_with_precise_message(self):
        bad = GOOD_MODEL_TABLE.replace("R0002", "R9999")
        violations = validate_row_ids(
            bad, {"R0001", "R0002"}, {"R0001", "R0002"})
        self.assertIn("unknown row_id: R9999 (row Tax assumptions)",
                      violations)

        out_of_batch = validate_row_ids(
            GOOD_MODEL_TABLE, {"R0001"}, {"R0001", "R0002"})
        self.assertIn("row_id not in this batch: R0002 (row Tax assumptions)",
                      out_of_batch)

        duplicate = validate_row_ids(
            GOOD_MODEL_TABLE.replace("R0001", "R0002"),
            {"R0001", "R0002"}, {"R0001", "R0002"})
        self.assertIn("duplicate row_id citation: R0002", duplicate)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub = make_model_stub([bad])
            with self.assertRaises(RuntimeError) as ctx:
                self._generate(root, stub)
            self.assertIn("unknown row_id: R9999", str(ctx.exception))
            # One retry through the correction loop before failing.
            self.assertEqual(len(stub.calls), 2)
            failure = json.loads(
                (root / "audit" / "0001-inputs-variable-sources.metadata.json"
                 ).read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")

    def test_model_written_reference_is_never_trusted(self):
        # A hand-written reference in the row-id column is rejected outright.
        invented = "\n".join([
            MODEL_HEADER,
            "|---|---|---|",
            "| Staff expenses | Staff Exp!G66 | HR system |",
        ])
        violations = validate_row_ids(
            invented, {"R0001", "R0002"}, {"R0001", "R0002"})
        self.assertTrue(any(
            "row_id column may cite only supplied row_ids" in item
            for item in violations))

        # In a table where other rows cite ids, a row without any row_id
        # is flagged too.
        two_rows = invented + "\n| Tax assumptions | R0002 | Tax authority |"
        self.assertIn(
            "row cites no row_id: Staff expenses",
            validate_row_ids(two_rows, {"R0001", "R0002"},
                             {"R0001", "R0002"}))

        mixed = invented.replace("Staff Exp!G66", "R0002, Staff Exp!G66")
        self.assertTrue(any(
            "row_id column may cite only supplied row_ids" in item
            for item in validate_row_ids(
                mixed, {"R0001", "R0002"}, {"R0001", "R0002"})))

        # End to end: the correction loop recovers, and the invented
        # reference never reaches the downstream Markdown.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub = make_model_stub([mixed, GOOD_MODEL_TABLE])
            metadata, markdown_path = self._generate(root, stub)
            self.assertEqual(len(stub.calls), 2)
            self.assertEqual(metadata["validation"]["status"], "passed")
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertNotIn("Staff Exp!G66", markdown)
            self.assertIn("`Inputs!B1` Tax rate / %: `0.25`", markdown)

    def test_no_candidate_sentinel_row_is_allowed(self):
        table = "\n".join([
            MODEL_HEADER,
            "|---|---|---|",
            "| No externally sourced candidates identified | — | — |",
        ])
        self.assertEqual(validate_row_ids(table, set(), set()), [])
        final = resolve_table(table, {})
        self.assertIn(
            "| No externally sourced candidates identified | — | — |", final)
        self.assertTrue(final.startswith(FINAL_HEADER))

    def test_cache_reuse_and_prompt_version_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata, _ = self._generate(root, make_model_stub([GOOD_MODEL_TABLE]))
            self.assertFalse(metadata["cache_hit"])

            # A valid v2 cache is reused without calling the model.
            second_stub = make_model_stub([GOOD_MODEL_TABLE])
            reused, _ = self._generate(root, second_stub)
            self.assertTrue(reused["cache_hit"])
            self.assertEqual(len(second_stub.calls), 0)

            # A cache written by the older prompt version is invalidated.
            metadata_path = (root / "audit"
                             / "0001-inputs-variable-sources.metadata.json")
            stale = json.loads(metadata_path.read_text(encoding="utf-8"))
            stale["prompt_version"] = "variable-source-audit-v1"
            metadata_path.write_text(json.dumps(stale, indent=2) + "\n",
                                     encoding="utf-8")
            third_stub = make_model_stub([GOOD_MODEL_TABLE])
            refreshed, _ = self._generate(root, third_stub)
            self.assertFalse(refreshed["cache_hit"])
            self.assertEqual(len(third_stub.calls), 1)


if __name__ == "__main__":
    unittest.main()
