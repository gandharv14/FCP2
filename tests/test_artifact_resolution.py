from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import openpyxl

from xl_artifact_paths import resolve_workbook_artifact


class ResolveWorkbookArtifactTests(unittest.TestCase):
    def test_xlsx_resolution_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0001-inputs.xlsx").write_bytes(b"x")
            resolved = resolve_workbook_artifact(root, "0001", "%s-inputs")
            self.assertEqual(resolved, root / "0001-inputs.xlsx")

    def test_xlsm_resolved_when_xlsx_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0624-inputs.xlsm").write_bytes(b"x")
            resolved = resolve_workbook_artifact(root, "0624", "%s-inputs")
            self.assertEqual(resolved, root / "0624-inputs.xlsm")

    def test_both_present_prefers_xlsx(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0001-inputs.xlsx").write_bytes(b"x")
            (root / "0001-inputs.xlsm").write_bytes(b"m")
            resolved = resolve_workbook_artifact(root, "0001", "%s-inputs")
            self.assertEqual(resolved, root / "0001-inputs.xlsx")

    def test_default_pattern_matches_golden_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "0643.xlsm").write_bytes(b"m")
            self.assertEqual(resolve_workbook_artifact(root, "0643"),
                             root / "0643.xlsm")

    def test_neither_present_keeps_old_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolved = resolve_workbook_artifact(root, "0001", "%s-inputs")
            # Callers used to build the .xlsx path unconditionally and fail on
            # open; the helper must preserve that exact failure mode.
            self.assertEqual(resolved, root / "0001-inputs.xlsx")
            self.assertFalse(resolved.exists())
            with self.assertRaises(FileNotFoundError):
                openpyxl.load_workbook(resolved)


if __name__ == "__main__":
    unittest.main()
