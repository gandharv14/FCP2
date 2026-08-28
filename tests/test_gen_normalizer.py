from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import gen_normalizer as gn
import openpyxl
import plain_eligibility

ROOT = Path(__file__).resolve().parents[1]
# Representative sample, not the full nine: parenthesized sheets, ComEd
# fragments, a plus-in-name sheet, and a genuine zero-variable plain task.
SAMPLE_DRAFTS = ("0175", "0289", "0480", "0564")


class ExtractRefsTests(unittest.TestCase):
    def test_parenthesised_sheet(self):
        refs = gn.extract_refs("Financial Summary (Model)!D9:F9 = 1, 2, 3")
        self.assertEqual(refs, ["Financial Summary (Model)!D9:F9"])

    def test_plus_in_sheet(self):
        refs = gn.extract_refs("2021 (Actual + Proj)!C6:N6 — 12 values")
        self.assertEqual(refs, ["2021 (Actual + Proj)!C6:N6"])

    def test_comed_fragment_is_full_sheet(self):
        text = (
            "`IL (ComEd) 6kW 10kWh - Non LMI!C40` = 7139.59<br>"
            "`IL (ComEd) 6kW 10kWh 40% ITC !C40` = 7139.59"
        )
        refs = gn.extract_refs(text)
        self.assertEqual(refs, [
            "IL (ComEd) 6kW 10kWh - Non LMI!C40",
            "IL (ComEd) 6kW 10kWh 40% ITC!C40",
        ])
        self.assertFalse(any(r.startswith("ComEd)") for r in refs))

    def test_quoted_sheet(self):
        refs = gn.extract_refs("'Model (Original)'!F32:J32 = [1]")
        self.assertEqual(refs, ["Model (Original)!F32:J32"])

    def test_plain_sheet_still_matches(self):
        refs = gn.extract_refs("Blueprint Standalone!F17:G17 — [9627.69647]")
        self.assertEqual(refs, ["Blueprint Standalone!F17:G17"])


class DraftRegressionTests(unittest.TestCase):
    def test_every_draft_row_has_a_live_sheet_ref(self):
        skipped_no_cell = 0
        for wb in SAMPLE_DRAFTS:
            draft_path = ROOT / "runs" / f"{wb}-variable-sources" / "draft.json"
            golden = ROOT / "batch-src" / f"{wb}.xlsx"
            if not golden.exists():
                golden = ROOT / "4-10 100" / f"{wb}.xlsx"
            self.assertTrue(draft_path.is_file(), draft_path)
            self.assertTrue(golden.is_file(), golden)
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            book = openpyxl.load_workbook(golden, read_only=True, data_only=True)
            sheets = {s.strip().upper(): s for s in book.sheetnames}
            book.close()
            for row in draft["rows"]:
                refs = gn.extract_refs(row["value_text"])
                if not refs:
                    skipped_no_cell += 1
                    continue
                resolved = [gn.expand(ref, sheets)[0] for ref in refs]
                self.assertTrue(
                    any(resolved),
                    f"{wb} {row['draft_id']}: parsed {refs} but none resolved "
                    f"against {sorted(sheets)[:8]}",
                )
        # A few audit rows are source notes or sheet lists with no Sheet!Cell.
        self.assertLessEqual(skipped_no_cell, 6)


class ExistingMcpSpecTests(unittest.TestCase):
    def test_0438_stays_mcp(self):
        run = ROOT / "runs" / "0438-variable-sources"
        if not (run / "normalized.json").is_file():
            self.skipTest("0438 normalized spec not present")
        spec = json.loads((run / "normalized.json").read_text(encoding="utf-8"))
        self.assertGreater(len(spec.get("variables") or []), 0)
        if (run / "normalization_report.json").is_file():
            result = plain_eligibility.evaluate(run)
            self.assertEqual(result["mode"], "mcp")


class PlainEligibilityTests(unittest.TestCase):
    def test_extraction_defect_is_not_plain(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "draft.json").write_text(json.dumps({
                "rows": [
                    {"draft_id": f"r{i}", "variable_text": "x", "value_text": "A!B1"}
                    for i in range(5)
                ],
            }), encoding="utf-8")
            (run / "normalized.json").write_text(json.dumps({
                "variables": [],
            }), encoding="utf-8")
            (run / "normalization_report.json").write_text(json.dumps({
                "workbook": "0001",
                "atomic_variables": 0,
                "first_atomic_variables": 0,
                "dispositions": [
                    {"draft_id": f"r{i}", "status": "excluded",
                     "reason": "bad", "code": "unparsable_reference"}
                    for i in range(5)
                ],
                "exclusion_reason_codes": {"unparsable_reference": 5},
            }), encoding="utf-8")
            (run / "0001-inputs-variable-sources.metadata.json").write_text(
                json.dumps({"inventory_rows": 20}), encoding="utf-8")
            result = plain_eligibility.evaluate(run)
            self.assertEqual(result["mode"], "fail")
            self.assertIn("extraction defect", result["reason"])

    def test_first_nonempty_cannot_downgrade(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "draft.json").write_text(json.dumps({
                "rows": [{"draft_id": "a", "variable_text": "x", "value_text": "S!A1"}] * 5,
            }), encoding="utf-8")
            (run / "normalized.json").write_text(json.dumps({"variables": []}), encoding="utf-8")
            (run / "normalization_report.json").write_text(json.dumps({
                "atomic_variables": 0,
                "first_atomic_variables": 4,
                "dispositions": [
                    {"draft_id": "a", "status": "excluded", "reason": "leak",
                     "code": "duplicate_value_leak"}
                ] * 5,
            }), encoding="utf-8")
            (run / "first_normalization.json").write_text(json.dumps({
                "atomic_variables": 4,
            }), encoding="utf-8")
            result = plain_eligibility.evaluate(run)
            self.assertEqual(result["mode"], "fail")
            self.assertIn("downgraded", result["reason"])

    def test_clean_all_excluded_is_plain(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            rows = [
                {"draft_id": f"r{i}", "variable_text": "rate", "value_text": "S!A1"}
                for i in range(5)
            ]
            (run / "draft.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
            (run / "normalized.json").write_text(json.dumps({"variables": []}), encoding="utf-8")
            (run / "normalization_report.json").write_text(json.dumps({
                "atomic_variables": 0,
                "first_atomic_variables": 0,
                "dispositions": [
                    {"draft_id": f"r{i}", "status": "excluded", "reason": "leak",
                     "code": "duplicate_value_leak"}
                    for i in range(5)
                ],
                "exclusion_reason_codes": {"duplicate_value_leak": 5},
            }), encoding="utf-8")
            (run / "0001-inputs-variable-sources.metadata.json").write_text(
                json.dumps({"inventory_rows": 10}), encoding="utf-8")
            result = plain_eligibility.evaluate(run)
            self.assertEqual(result["mode"], "plain")
            self.assertTrue(result["eligible"])

    def test_forced_exclusions_block_plain(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            (run / "draft.json").write_text(json.dumps({
                "rows": [
                    {"draft_id": f"r{i}", "variable_text": "x", "value_text": "S!A1"}
                    for i in range(5)
                ],
            }), encoding="utf-8")
            (run / "normalized.json").write_text(json.dumps({"variables": []}), encoding="utf-8")
            (run / "normalization_report.json").write_text(json.dumps({
                "atomic_variables": 0,
                "first_atomic_variables": 0,
                "dispositions": [
                    {"draft_id": f"r{i}", "status": "excluded", "reason": "forced",
                     "code": "forced_exclusion"}
                    for i in range(5)
                ],
            }), encoding="utf-8")
            (run / "oracle_forced_exclusions.json").write_text(
                json.dumps(["r0", "r1", "r2", "r3", "r4"]), encoding="utf-8")
            (run / "0001-inputs-variable-sources.metadata.json").write_text(
                json.dumps({"inventory_rows": 10}), encoding="utf-8")
            result = plain_eligibility.evaluate(run)
            self.assertEqual(result["mode"], "fail")
            self.assertIn("oracle_forced_exclusions", result["reason"])

    def test_plain_environment_rejects_stray_files(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0001-inputs.xlsx").write_bytes(b"x")
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (env / "eval").mkdir()
            (env / "eval" / "tasks.jsonl").write_text("{}\n", encoding="utf-8")
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("eval/tasks.jsonl", report["unknown_files"])

    def test_plain_environment_allows_dialogue_notes_after_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0001-inputs.xlsx").write_bytes(b"x")
            (env / "Dockerfile").write_text(
                "FROM scratch\nCOPY additional-assumptions.md "
                "/app/additional-assumptions.md\n",
                encoding="utf-8",
            )
            (env / "additional-assumptions.md").write_text("notes\n", encoding="utf-8")
            (bundle / "tests").mkdir()
            (bundle / "tests" / "dialogue-applied.json").write_text(
                "{}\n", encoding="utf-8"
            )
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertTrue(report["valid"], report)

    def test_plain_environment_rejects_stray_dialogue_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0001-inputs.xlsx").write_bytes(b"x")
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (env / "additional-assumptions.md").write_text("notes\n", encoding="utf-8")
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("additional-assumptions.md", report["unknown_files"])

    def test_plain_environment_accepts_xlsm_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0624-inputs.xlsm").write_bytes(b"m")
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            report = plain_eligibility.check_plain_environment(bundle, "0624")
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["workbooks"], ["0624-inputs.xlsm"])

    def test_plain_environment_rejects_both_workbook_suffixes(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0001-inputs.xlsx").write_bytes(b"x")
            (env / "0001-inputs.xlsm").write_bytes(b"m")
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("exactly one workbook at environment root",
                          report["missing_files"])

    def test_plain_environment_missing_workbook_error_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("0001-inputs.xlsx", report["missing_files"])

    def test_plain_environment_rejects_notes_without_dockerfile_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            env = bundle / "environment"
            env.mkdir()
            (env / "0001-inputs.xlsx").write_bytes(b"x")
            (env / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (env / "additional-assumptions.md").write_text("notes\n", encoding="utf-8")
            (bundle / "tests").mkdir()
            (bundle / "tests" / "dialogue-applied.json").write_text(
                "{}\n", encoding="utf-8"
            )
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("additional-assumptions.md", report["unknown_files"])


if __name__ == "__main__":
    unittest.main()
