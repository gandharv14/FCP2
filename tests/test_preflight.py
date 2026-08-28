import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xl_preflight import classify, preflight


def verification(**overrides):
    base = {
        "skipped": False,
        "passed": False,
        "outputs": {"match": 10, "mismatch": 0, "unresolved": 0, "unverifiable": 0},
        "seeded_inside_output_cone_count": 0,
        "oracle_fallback_inside_output_cone_count": 0,
        "iterative_blocks": [],
        "divergence_roots": [],
        "failures": [],
    }
    base.update(overrides)
    return base


class ClassifyTests(unittest.TestCase):
    def test_missing_source(self):
        self.assertEqual(classify(verification(), False)[0], "missing_source")

    def test_unverified_when_skipped_or_absent(self):
        self.assertEqual(classify(None, True)[0], "unverified")
        self.assertEqual(
            classify({"skipped": True}, True)[0], "unverified")

    def test_healthy(self):
        self.assertEqual(
            classify(verification(passed=True), True)[0], "healthy")

    def test_frontier_unsafe_beats_everything_else(self):
        v = verification(
            seeded_inside_output_cone_count=1762,
            outputs={"match": 23, "mismatch": 0, "unresolved": 0,
                     "unverifiable": 5},
        )
        self.assertEqual(classify(v, True)[0], "frontier_unsafe")

    def test_unsafe_circular_from_unconverged_block(self):
        v = verification(
            iterative_blocks=[{"size": 55, "converged": False}],
            outputs={"match": 8, "mismatch": 0, "unresolved": 15,
                     "unverifiable": 0},
        )
        self.assertEqual(classify(v, True)[0], "unsafe_circular")

    def test_unsafe_circular_from_failure_reason(self):
        v = verification(
            outputs={"match": 2, "mismatch": 0, "unresolved": 4,
                     "unverifiable": 0},
            failures=[{"cell": "Description!C44", "verdict": "unresolved",
                       "recomputed": "Unresolved(non-unique-circular-reference)"}],
        )
        self.assertEqual(classify(v, True)[0], "unsafe_circular")

    def test_stale_cache_repairable(self):
        v = verification(
            outputs={"match": 156, "mismatch": 0, "unresolved": 0,
                     "unverifiable": 230},
        )
        self.assertEqual(classify(v, True)[0], "stale_cache_repairable")

    def test_cached_mismatch(self):
        v = verification(
            outputs={"match": 43, "mismatch": 1, "unresolved": 0,
                     "unverifiable": 0},
            divergence_roots=[{"cell": "Calculations!T243",
                               "workbook": "-3025373.2", "recomputed": "-0.0"}],
        )
        classification, evidence = classify(v, True)
        self.assertEqual(classification, "cached_mismatch")
        self.assertEqual(len(evidence["divergence_roots"]), 1)


class PreflightIoTests(unittest.TestCase):
    def test_verdict_file_written_atomically_and_shaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "0001.xlsm").write_bytes(b"PK\x03\x04stub")
            seg = root / "seg" / "0001"
            seg.mkdir(parents=True)
            json.dump(
                {"verification": verification(passed=True)},
                open(seg / "segments.json", "w", encoding="utf-8"))
            verdict = preflight(
                "0001", root / "src", root / "seg", root / "out")
            self.assertTrue(verdict["healthy"])
            # .xlsm sources resolve through the shared resolver.
            self.assertTrue(verdict["source"].endswith("0001.xlsm"))
            on_disk = json.load(
                open(root / "out" / "0001.json", encoding="utf-8"))
            self.assertEqual(on_disk["classification"], "healthy")
            self.assertNotIn("0001.json.tmp", [p.name for p in (root / "out").iterdir()])

    def test_missing_everything_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            verdict = preflight(
                "0002", root / "src", root / "seg", root / "out")
            self.assertFalse(verdict["healthy"])
            self.assertEqual(verdict["classification"], "missing_source")


if __name__ == "__main__":
    unittest.main()
