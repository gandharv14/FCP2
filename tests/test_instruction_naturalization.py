from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / ".cursor"
    / "skills"
    / "naturalize-finance-task-instruction"
    / "scripts"
    / "validate_instruction_rewrite.py"
)
SPEC = importlib.util.spec_from_file_location("instruction_validator", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)

SOURCE = """\
A colleague has sent you an integrated three-statement and valuation model with
every calculated figure stripped out. Rebuild the calculations and report the
2 headline figures, including Equity IRR and Equity value (DDM). The full list
is below.

## Input

The workbook `0001-inputs.xlsx` is in your working directory. Every calculated
cell is blank, while the remaining inputs are present. Removed assumptions are
only available through the research data service. The workbook has no formulas
or derived numbers. You may install Python packages to read it.

## What to compute

| # | figure | cells |
| --- | --- | --- |
| 1 | Equity IRR | `Valuation!C10` |
| 2 | Equity value (DDM) | `Valuation!C20` |

## Output

Write a JSON object to `/app/answers.json` using the cell references exactly.
Report 2 values without rounding.
"""

CANDIDATE = """\
You are taking over an integrated three-statement and valuation model with
every calculated figure removed. Rebuild the calculations and report the
2 headline figures, including Equity IRR and Equity value (DDM). The full list
is below.

## Input

The workbook `0001-inputs.xlsx` is available in your working directory. Every
calculated cell is blank, and the remaining inputs remain present. Removed
assumptions are available only through the research data service. The workbook
contains no formulas or derived numbers. You may install Python packages to
inspect it.

## What to compute

| # | figure | cells |
| --- | --- | --- |
| 1 | Equity IRR | `Valuation!C10` |
| 2 | Equity value (DDM) | `Valuation!C20` |

## Output

Write a JSON object to `/app/answers.json` using the cell references exactly.
Report 2 values without rounding.
"""


class InstructionNaturalizationTests(unittest.TestCase):
    def test_accepts_lossless_opening_and_input_rewrite(self):
        report = VALIDATOR.validate(
            SOURCE,
            CANDIDATE,
            {"targets": {"Valuation!C10": 0.123, "Valuation!C20": 456.7}},
        )
        self.assertTrue(report["valid"])
        self.assertNotEqual(report["source_sha256"], report["candidate_sha256"])

    def test_rejects_modified_protected_section(self):
        changed = CANDIDATE.replace(
            "Report 2 values without rounding.",
            "Report 2 rounded values.",
        )
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "protected section",
        ):
            VALIDATOR.validate(SOURCE, changed)

    def test_rejects_modified_table(self):
        changed = CANDIDATE.replace("Valuation!C20", "Valuation!C21")
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "protected section",
        ):
            VALIDATOR.validate(SOURCE, changed)

    def test_rejects_missing_input_exclusivity(self):
        changed = CANDIDATE.replace(
            "are available only through",
            "are available through",
        )
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "exclusivity",
        ):
            VALIDATOR.validate(SOURCE, changed)

    def test_cli_applies_atomically_and_updates_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.md"
            candidate = root / "candidate.md"
            instruction = root / "instruction.md"
            answer_key = root / "answer_key.json"
            task_toml = root / "task.toml"
            report = root / "validation.json"
            source.write_text(SOURCE, encoding="utf-8")
            candidate.write_text(CANDIDATE, encoding="utf-8")
            instruction.write_text(SOURCE, encoding="utf-8")
            answer_key.write_text(
                json.dumps({"targets": {"Valuation!C10": 0.123}}),
                encoding="utf-8",
            )
            task_toml.write_text(
                """\
[metadata.naturalizer]
model = "openai/gpt-5.6-luna"
endpoint = "https://example.test"
attempts = 0
naturalized = false
fallback_reason = "disabled"

[agent]
timeout_sec = 100
""",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(source),
                    str(candidate),
                    "--answer-key",
                    str(answer_key),
                    "--report",
                    str(report),
                    "--apply-to",
                    str(instruction),
                    "--task-toml",
                    str(task_toml),
                    "--attempts",
                    "1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(instruction.read_text(encoding="utf-8"), CANDIDATE)
            task_text = task_toml.read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6-sol-high"', task_text)
            self.assertIn('endpoint = "cursor-subagent"', task_text)
            self.assertIn("naturalized = true", task_text)
            self.assertIn("[agent]", task_text)
            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["applied"])


if __name__ == "__main__":
    unittest.main()
