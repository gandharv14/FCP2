"""roles-validate and .xlsm-routing regression tests.

Blocked-workbook classes covered: 0350/0468/0522/0527/0534 (malformed
role-arbitration JSON) and 0635/0654 (.xlsm delivered workbooks invisible to
disclosure selection).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import disclose


CASES = [
    {"case_id": "case-a", "label": "Interest", "roles": ["rate_sheet", "cost_line"]},
    {"case_id": "case-b", "label": "Tax", "roles": ["tax_rate", "tax_charge"]},
]


class ValidateRoleResolutionsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def _file(self, text):
        path = self.root / "role_resolutions.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_clean_object_validates_and_normalizes(self):
        path = self._file(json.dumps({
            "resolutions": [{"case_id": "case-a", "chosen": "rate_sheet",
                             "reason": "row is a rate"}],
        }))
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertTrue(normalized["validated"])
        self.assertEqual(normalized["resolutions"][0]["chosen"], "rate_sheet")

    def test_prose_wrapped_object_is_salvaged_with_warning(self):
        path = self._file(
            "Here is my arbitration:\n"
            + json.dumps({"resolutions": [
                {"case_id": "case-a", "chosen": "cost_line"}]})
            + "\nHope this helps!")
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        self.assertTrue(normalized["validated"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("exactly one JSON object", warnings[0].replace(
            "one JSON object and nothing else", "exactly one JSON object"))

    def test_concatenated_objects_salvage_first(self):
        # The observed 0350/0522/0527 failure: "Extra data at line 2 column 1".
        first = json.dumps({"resolutions": [
            {"case_id": "case-b", "chosen": "tax_rate"}]})
        second = json.dumps({"note": "duplicate object"})
        path = self._file(first + "\n" + second)
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["resolutions"][0]["case_id"], "case-b")
        self.assertTrue(warnings and "Extra data" in warnings[0])

    def test_schema_faults_are_precise_errors(self):
        path = self._file(json.dumps({"resolutions": [
            {"case_id": "case-zzz", "chosen": "rate_sheet"},
            {"case_id": "case-a", "chosen": "not_a_candidate"},
            {"case_id": "case-b", "chosen": "tax_rate", "model": "x"},
            {"case_id": "case-b", "chosen": "tax_rate"},
            {"case_id": "case-b", "chosen": "tax_charge"},
        ]}))
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertIsNone(normalized)
        text = "\n".join(errors)
        self.assertIn("case-zzz", text)
        self.assertIn("not_a_candidate", text)
        self.assertIn("unknown key(s) ['model']", text)
        self.assertIn("duplicate case_id", text)

    def test_no_json_at_all(self):
        path = self._file("I could not decide.")
        normalized, errors, _ = disclose.validate_role_resolutions(path, CASES)
        self.assertIsNone(normalized)
        self.assertIn("no JSON object found", errors[0])

    def test_null_chosen_is_a_valid_abstain(self):
        path = self._file(json.dumps({"resolutions": [
            {"case_id": "case-a", "label": "Interest", "chosen": None,
             "reason": "generic label"}]}))
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertIsNone(normalized["resolutions"][0]["chosen"])

    def test_legacy_chosen_role_key_is_normalized(self):
        # The real 0600 arbitration file (older prompt era) uses chosen_role.
        path = self._file(json.dumps({"resolutions": [
            {"case_id": "case-a", "label": "Interest",
             "chosen_role": "rate_sheet", "reason": "rate row"}]}))
        normalized, errors, warnings = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        self.assertTrue(any("legacy key" in w for w in warnings))
        self.assertEqual(normalized["resolutions"][0]["chosen"], "rate_sheet")

    def test_detect_refuses_unvalidated_file(self):
        path = self._file(json.dumps({"resolutions": [
            {"case_id": "case-a", "chosen": "rate_sheet"}]}))
        with self.assertRaises(SystemExit) as ctx:
            disclose.load_role_resolutions(path)
        self.assertIn("roles-validate", str(ctx.exception))
        # After validation the same file loads fine.
        normalized, errors, _ = disclose.validate_role_resolutions(path, CASES)
        self.assertEqual(errors, [])
        path.write_text(json.dumps(normalized), encoding="utf-8")
        payload = disclose.load_role_resolutions(path)
        self.assertEqual(payload["by_id"]["case-a"]["chosen"], "rate_sheet")


class XlsmRoutingTests(unittest.TestCase):
    def test_find_environment_accepts_xlsm_and_prefers_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "0654-outputs"
            (task / "environment").mkdir(parents=True)
            xlsm = task / "environment" / "0654-inputs.xlsm"
            xlsm.write_bytes(b"PK\x03\x04")
            self.assertEqual(disclose.find_environment(task), xlsm)
            xlsx = task / "environment" / "0654-inputs.xlsx"
            xlsx.write_bytes(b"PK\x03\x04")
            self.assertEqual(disclose.find_environment(task), xlsx)

    def test_find_golden_resolves_xlsm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "tasks" / "0654-outputs"
            task.mkdir(parents=True)
            golden_dir = root / "FCP Workbooks" / "batch"
            golden_dir.mkdir(parents=True)
            golden = golden_dir / "0654.xlsm"
            golden.write_bytes(b"PK\x03\x04")
            self.assertEqual(disclose.find_golden(task), golden.resolve())


if __name__ == "__main__":
    unittest.main()
