from __future__ import annotations

import json
import os
import subprocess
import sys
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
                '{"applied": true, "review_passed": true, '
                '"draft_passed": true}\n',
                encoding="utf-8",
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
                '{"applied": true, "review_passed": true, '
                '"draft_passed": true}\n',
                encoding="utf-8",
            )
            report = plain_eligibility.check_plain_environment(bundle, "0001")
            self.assertFalse(report["valid"])
            self.assertIn("additional-assumptions.md", report["unknown_files"])


if __name__ == "__main__":
    unittest.main()


class NormalizerOutputRedirectionTests(unittest.TestCase):
    """Gate 6 runs the generated normalizer twice to prove byte-stability.

    Redirecting only the outputs lets the two runs overlap instead of racing on
    a shared normalized.json. Inputs must keep coming from the script's own
    directory, or a redirected run would silently read nothing.
    """

    VAR_DEFS = [
        {
            "id": "v1",
            "name": "Revenue",
            "value": 100,
            "unit": "USD",
            "period": "FY24",
            "basis": "actual",
            "question": "What is revenue?",
            "cells": ["Sheet1!B4"],
        }
    ]

    def _render(self, run):
        (run / "draft.json").write_text(
            json.dumps({"rows": [{"draft_id": "d1"}]}), encoding="utf-8"
        )
        script = run / "normalize_0001.py"
        script.write_text(
            gn.TEMPLATE.format(
                wb="0001",
                seed=1,
                variables=repr(self.VAR_DEFS),
                included=repr({"d1": ["v1"]}),
                excluded=repr({}),
                reasons=repr({}),
                codes=repr({}),
                first_atomic=1,
                forced=repr([]),
                pipeline_bindings=repr(None),
            ),
            encoding="utf-8",
        )
        return script

    def _run(self, script, out_dir=None):
        env = dict(os.environ)
        if out_dir is not None:
            env["NORMALIZE_OUT_DIR"] = str(out_dir)
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_writes_beside_the_script(self):
        run = Path(tempfile.mkdtemp())
        script = self._render(run)

        self._run(script)

        for name in ("normalized.json", "exclusions.json", "normalization_report.json"):
            self.assertTrue((run / name).is_file(), name)

    def test_redirected_outputs_are_byte_identical(self):
        run = Path(tempfile.mkdtemp())
        script = self._render(run)
        other = run / "b"

        self._run(script)
        self._run(script, other)

        for name in ("normalized.json", "exclusions.json", "normalization_report.json"):
            self.assertEqual(
                (run / name).read_bytes(), (other / name).read_bytes(), name
            )

    def test_an_empty_override_falls_back_to_the_script_dir(self):
        # Gate 6 clears NORMALIZE_OUT_DIR for its first run so an inherited
        # value cannot redirect it, leaving a stale normalized.json for the
        # byte-stability cmp to compare against and pass. That relies on an
        # empty string falling back to RUN rather than resolving to ".".
        run = Path(tempfile.mkdtemp())
        script = self._render(run)
        (run / "normalized.json").write_text("stale\n", encoding="utf-8")

        self._run(script, out_dir="")

        self.assertNotEqual(
            (run / "normalized.json").read_text(encoding="utf-8"), "stale\n"
        )

    def test_inputs_are_read_from_the_script_dir_not_the_output_dir(self):
        run = Path(tempfile.mkdtemp())
        script = self._render(run)
        (run / "source_profiles.json").write_text(
            json.dumps({"profiles": [{"profile_id": "p1"}]}), encoding="utf-8"
        )
        other = run / "b"

        self._run(script, other)

        spec = json.loads((other / "normalized.json").read_text(encoding="utf-8"))
        self.assertEqual(spec.get("source_profiles"), [{"profile_id": "p1"}])
        self.assertFalse((other / "draft.json").exists())
