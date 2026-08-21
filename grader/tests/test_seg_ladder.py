from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xl_seg.adjudicate import apply_to_curation
from xl_seg.emit import apply_fallback, read_curation
from xl_seg.frontier import Candidate, fallback_outputs


def candidate(band, label, score, *, sheet="Model", check=False,
              strong=False, weak=False):
    return Candidate(
        comp=band, band=band, label=label, sheet=sheet, score=score,
        features={"check_cell": check, "strong_term": strong, "weak_term": weak,
                  "sink": True, "mirror_fanin": 0, "scalar_collapse": False,
                  "depth": 1},
        values=[[band]],
    )


class FallbackSelectionTests(unittest.TestCase):
    def test_excludes_check_cells_and_unlabelled(self):
        picks = fallback_outputs([
            candidate("Model!A1", "Balance check", 5.9, check=True),
            candidate("Model!A2", "", 5.8),
            candidate("Model!A3", "   ", 5.7),
            candidate("Model!A4", "Net income", 5.6, weak=True),
        ])
        self.assertEqual([c.band for c in picks], ["Model!A4"])

    def test_dedupes_identical_label_and_sheet(self):
        picks = fallback_outputs([
            candidate("Model!A1", "Net income margin", 5.9, weak=True),
            candidate("Model!A2", "Net income margin", 5.8, weak=True),
            candidate("Model!B2", "Net income margin", 5.7, weak=True, sheet="Other"),
        ])
        self.assertEqual([c.band for c in picks], ["Model!A1", "Model!B2"])

    def test_term_matches_fill_slots_before_axis_rows(self):
        # 0119-shaped: a convention/axis row outscores real margin rows but
        # must not consume a fallback slot ahead of them.
        picks = fallback_outputs([
            candidate("FS!C327", "Midpoint convention", 5.67),
            candidate("FS!C100", "Net income margin", 5.6, weak=True),
            candidate("FS!C101", "EBITDA margin", 5.5, weak=True),
            candidate("FS!C102", "Equity value", 5.4, strong=True),
            candidate("FS!C103", "FCF", 5.3, weak=True),
            candidate("FS!C104", "Gross profit", 5.2),
        ])
        self.assertEqual(
            [c.band for c in picks],
            ["FS!C100", "FS!C101", "FS!C102", "FS!C103"],
        )

    def test_caps_at_four_and_backfills_with_non_term_rows(self):
        picks = fallback_outputs([
            candidate("M!A1", "Net income", 5.9, weak=True),
            candidate("M!A2", "Some ratio", 5.8),
            candidate("M!A3", "Another line", 5.7),
        ])
        self.assertEqual([c.band for c in picks], ["M!A1", "M!A2", "M!A3"])
        self.assertEqual(len(fallback_outputs([
            candidate(f"M!A{i}", f"Line {i}", 5.0) for i in range(9)
        ])), 4)


CURATION = """\
# Output curation for workbook 0000.
[[output]]
band = "Model!A1"
sheet = "Model"
label = "Net income"
score = 5.6
include = false
name = "Net income"

[[output]]
band = "Model!A2"
sheet = "Model"
label = "Check"
score = 0.5
include = false
name = "Check"
"""


class ApplyFallbackTests(unittest.TestCase):
    def _roundtrip(self, bands):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "curation.toml"
            path.write_text(CURATION, encoding="utf-8")
            changed = apply_fallback(path, bands)
            return changed, path.read_text(encoding="utf-8"), read_curation(path)

    def test_marks_only_requested_bands_on_boolean_line(self):
        changed, text, entries = self._roundtrip(["Model!A1"])
        self.assertEqual(changed, 1)
        self.assertIn("include = true  # fallback: top-4 auto-include", text)
        self.assertIs(entries[0]["include"], True)
        self.assertIs(entries[1]["include"], False)
        # The marker never rides a quoted line, which the reader cannot strip.
        self.assertIn('name = "Net income"', text)

    def test_no_matching_band_changes_nothing(self):
        changed, text, entries = self._roundtrip(["Model!ZZ99"])
        self.assertEqual(changed, 0)
        self.assertEqual(text, CURATION)
        self.assertFalse(any(e["include"] for e in entries))


class AdjudicatorNameEscapingTests(unittest.TestCase):
    def test_llm_name_with_quotes_survives_the_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "curation.toml"
            path.write_text(CURATION, encoding="utf-8")
            apply_to_curation(path, {"Model!A1": (True, 'Net "core" income')})
            entries = read_curation(path)
        self.assertIs(entries[0]["include"], True)
        self.assertEqual(entries[0]["name"], 'Net "core" income')


if __name__ == "__main__":
    unittest.main()
