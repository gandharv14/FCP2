from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xl_passk_score import summarize_task


class PassKContinuousStatisticsTests(unittest.TestCase):
    def test_task_summary_uses_population_variance(self):
        rows = []
        for index, score in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
            rows.append(
                {
                    "workbook": "0001",
                    "family": "dcf_valuation",
                    "assessment": {
                        "kind": "cell_value",
                        "score": score,
                        "accuracy_exact": score,
                        "accuracy_close_1pct": score,
                        "coverage": 1.0,
                        "cells": [],
                    },
                    "discrete_score": 1.0 if index == 4 else 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                    "elapsed_sec": 0.0,
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            task_dir = Path(temp) / "0001-outputs" / "tests"
            task_dir.mkdir(parents=True)
            (task_dir / "outputs.json").write_text("[]", encoding="utf-8")
            summary = summarize_task(
                "0001-outputs",
                rows,
                temp,
            )

        self.assertEqual(summary["continuous_score_mean"], 0.5)
        self.assertEqual(summary["continuous_score_variance"], 0.125)
        self.assertEqual(summary["n_continuous_scores"], 5)
        self.assertEqual(summary["n_passed"], 1)
        self.assertEqual(summary["pass_at_5"], 1.0)


if __name__ == "__main__":
    unittest.main()
