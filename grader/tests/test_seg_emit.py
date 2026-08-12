from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xl_seg.adjudicate import normalize_band
from xl_seg.emit import read_curation


class CurationReaderTests(unittest.TestCase):
    def test_normalizes_band_prefix_echoed_by_llm(self):
        self.assertEqual(
            normalize_band("`band=Project Moon_Contrib!G31`"),
            "Project Moon_Contrib!G31",
        )

    def test_reads_llm_decisions_with_heuristic_comments_as_booleans(self):
        text = """\
[[output]]
band = "Model!A1"
include = false  # heuristic: true
name = "Rejected # candidate"

[[output]]
band = "Model!A2"
include = true  # heuristic: false
name = "Selected"
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "curation.toml"
            path.write_text(text, encoding="utf-8")
            entries = read_curation(path)

        self.assertIs(entries[0]["include"], False)
        self.assertIs(entries[1]["include"], True)
        self.assertEqual(entries[0]["name"], "Rejected # candidate")


if __name__ == "__main__":
    unittest.main()
