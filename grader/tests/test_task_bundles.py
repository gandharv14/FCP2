from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from grader.run_grader import run

ROOT = Path(__file__).resolve().parents[2]
TASKS_ROOT = ROOT / "tasks_outputs"


@unittest.skipUnless(TASKS_ROOT.is_dir(), "generated task bundles are not present")
class ExistingTaskBundleTests(unittest.TestCase):
    def test_every_bundle_scores_exact_and_partial_submissions_continuously(self):
        task_dirs = sorted(TASKS_ROOT.glob("*-outputs"))
        self.assertGreater(len(task_dirs), 0)

        for task_dir in task_dirs:
            with self.subTest(task=task_dir.name):
                key_path = task_dir / "tests" / "answer_key.json"
                key = json.loads(key_path.read_text(encoding="utf-8"))
                targets = key["targets"]
                first_ref = next(iter(targets))
                partial_answers = {first_ref: targets[first_ref]}
                if len(targets) == 1:
                    expected = targets[first_ref]
                    partial_answers = (
                        {first_ref: expected * 1.1} if expected else {}
                    )

                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    exact_workspace = root / "exact"
                    partial_workspace = root / "partial"
                    exact_output = root / "exact-output"
                    partial_output = root / "partial-output"
                    exact_workspace.mkdir()
                    partial_workspace.mkdir()
                    (exact_workspace / "answers.json").write_text(
                        json.dumps(targets),
                        encoding="utf-8",
                    )
                    (partial_workspace / "answers.json").write_text(
                        json.dumps(partial_answers),
                        encoding="utf-8",
                    )

                    subprocess.run(
                        [
                            sys.executable,
                            str(task_dir / "tests" / "run_grader.py"),
                            "--workspace",
                            str(exact_workspace),
                            "--answer-key",
                            str(key_path),
                            "--output-dir",
                            str(exact_output),
                            "--mode",
                            "continuous",
                        ],
                        check=True,
                    )
                    self.assertEqual(
                        run(
                            workspace=partial_workspace,
                            answer_key_path=key_path,
                            output_dir=partial_output,
                            mode="continuous",
                        ),
                        0,
                    )

                    exact_reward = float(
                        (exact_output / "reward.txt").read_text(encoding="utf-8")
                    )
                    partial_reward = float(
                        (partial_output / "reward.txt").read_text(encoding="utf-8")
                    )
                    self.assertEqual(exact_reward, 1.0)
                    if partial_answers:
                        self.assertGreater(partial_reward, 0.0)
                    self.assertLess(partial_reward, 1.0)

                script = (task_dir / "tests" / "test.sh").read_text(encoding="utf-8")
                self.assertIn("--mode continuous", script)
                self.assertNotIn("--mode discrete", script)
                task_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
                self.assertIn(
                    'reward_type = "continuous_scoring_function"',
                    task_toml,
                )
                self.assertTrue((task_dir / "tests" / "run_grader.py").is_file())
                self.assertTrue(
                    (task_dir / "tests" / "finance_grader" / "core.py").is_file()
                )


if __name__ == "__main__":
    unittest.main()
