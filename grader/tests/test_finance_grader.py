from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from grader.finance_grader import grade, grade_continuous, grade_discrete
from grader.run_grader import run


def answer_key(targets):
    return {
        "kind": "cell_value",
        "tolerance": {"numeric_abs": 1e-6, "numeric_rel": 1e-6},
        "targets": targets,
    }


class ContinuousGradeTests(unittest.TestCase):
    def test_exact_answers_score_one(self):
        key = answer_key({"Sheet!A1": 100.0, "Sheet!A2": -2.5})
        result = grade_continuous(
            {"Sheet!A1": 100.0, "Sheet!A2": -2.5},
            key,
        )

        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.metadata["n_exact"], 2)
        self.assertTrue(result.metadata["passed"])

    def test_normalized_linear_closeness(self):
        key = answer_key({"Sheet!A1": 100.0})

        high = grade_continuous({"Sheet!A1": 150.0}, key)
        low = grade_continuous({"Sheet!A1": 50.0}, key)
        opposite = grade_continuous({"Sheet!A1": -100.0}, key)

        self.assertAlmostEqual(high.score, 2.0 / 3.0)
        self.assertAlmostEqual(low.score, 0.5)
        self.assertEqual(opposite.score, 0.0)

    def test_partial_coverage_is_part_of_reward(self):
        key = answer_key({"Sheet!A1": 10.0, "Sheet!A2": 20.0})
        result = grade_continuous({"Sheet!A1": 10.0}, key)

        self.assertEqual(result.score, 0.5)
        self.assertEqual(result.metadata["coverage"], 0.5)
        self.assertEqual(result.subscores["Sheet!A2"], 0.0)

    def test_zero_expected_requires_absolute_closeness(self):
        key = answer_key({"Sheet!A1": 0.0})

        within_tolerance = grade_continuous({"Sheet!A1": 5e-7}, key)
        nonzero = grade_continuous({"Sheet!A1": 0.01}, key)

        self.assertEqual(within_tolerance.score, 1.0)
        self.assertEqual(nonzero.score, 0.0)

    def test_reference_and_numeric_string_normalization(self):
        key = answer_key({"'Input & Assumptions'!$E$195": 1234.5})
        result = grade_continuous(
            {"Input & Assumptions!E195": "1,234.5%"},
            key,
        )

        self.assertEqual(result.score, 1.0)

    def test_malformed_and_nonfinite_answers_score_zero(self):
        key = answer_key({"Sheet!A1": 10.0, "Sheet!A2": 20.0})
        result = grade_continuous(
            {"Sheet!A1": "not a number", "Sheet!A2": math.inf},
            key,
        )

        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.metadata["n_answered"], 2)
        self.assertEqual(result.metadata["n_exact"], 0)

    def test_extra_answers_are_ignored(self):
        key = answer_key({"Sheet!A1": 10.0})
        result = grade_continuous(
            {"Sheet!A1": 10.0, "Sheet!Z99": -999.0},
            key,
        )

        self.assertEqual(result.score, 1.0)
        self.assertEqual(set(result.subscores), {"Sheet!A1"})


class DiscreteGradeTests(unittest.TestCase):
    def test_discrete_requires_every_target(self):
        key = answer_key({"Sheet!A1": 10.0, "Sheet!A2": 20.0})

        partial = grade_discrete({"Sheet!A1": 10.0}, key)
        exact = grade_discrete({"Sheet!A1": 10.0, "Sheet!A2": 20.0}, key)

        self.assertEqual(partial.score, 0.0)
        self.assertEqual(partial.scoring_mode, "binary")
        self.assertEqual(exact.score, 1.0)

    def test_mode_selecting_api(self):
        key = answer_key({"Sheet!A1": 100.0})

        self.assertAlmostEqual(
            grade({"Sheet!A1": 150.0}, key, mode="continuous").score,
            2.0 / 3.0,
        )
        self.assertEqual(
            grade({"Sheet!A1": 150.0}, key, mode="discrete").score,
            0.0,
        )
        with self.assertRaises(ValueError):
            grade({"Sheet!A1": 100.0}, key, mode="unknown")


class SerializationAndRunnerTests(unittest.TestCase):
    def test_canonical_serialization_has_structured_subscores(self):
        key = answer_key({"Sheet!A1": 100.0})
        payload = grade_continuous({"Sheet!A1": 50.0}, key).to_dict()

        self.assertEqual(payload["score"], 0.5)
        self.assertEqual(payload["scoring_mode"], "weighted")
        self.assertEqual(payload["structured_subscores"][0]["name"], "Sheet!A1")
        self.assertEqual(payload["metadata"]["headline_score"], 0.5)

    def test_runner_writes_harbor_outputs(self):
        key = answer_key({"Sheet!A1": 100.0})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            output = root / "logs"
            workspace.mkdir()
            (workspace / "answers.json").write_text(
                json.dumps({"Sheet!A1": 50.0}),
                encoding="utf-8",
            )
            key_path = root / "answer_key.json"
            key_path.write_text(json.dumps(key), encoding="utf-8")

            status = run(
                workspace=workspace,
                answer_key_path=key_path,
                output_dir=output,
                mode="continuous",
            )

            self.assertEqual(status, 0)
            self.assertEqual((output / "reward.txt").read_text(), "0.500000\n")
            reward = json.loads((output / "reward.json").read_text())
            details = json.loads((output / "reward-details.json").read_text())
            assessment = json.loads((output / "score_details.json").read_text())
            self.assertEqual(reward["score"], 0.5)
            self.assertEqual(details["score"], 0.5)
            self.assertEqual(assessment["grader_mode"], "continuous")
            self.assertTrue((output / "answers.json").is_file())


if __name__ == "__main__":
    unittest.main()
