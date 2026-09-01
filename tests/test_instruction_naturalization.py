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
SCRIPTS = SCRIPT.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import instruction_spans as SPANS
import naturalize_recovery as RECOVERY

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

    def test_rejects_scientific_notation_answer_value(self):
        changed = CANDIDATE.replace(
            "inspect it.",
            "inspect it. The answer is 1e+20.",
        )
        with self.assertRaises(VALIDATOR.RewriteValidationError) as caught:
            VALIDATOR.validate(
                SOURCE,
                changed,
                {"targets": {"Valuation!C10": 1e20}},
            )
        self.assertEqual(caught.exception.reason_codes, ["answer_value_leak"])

    def test_rejects_percentage_form_of_answer_value(self):
        changed = CANDIDATE.replace(
            "inspect it.",
            "inspect it. The answer is 10%.",
        )
        with self.assertRaises(VALIDATOR.RewriteValidationError) as caught:
            VALIDATOR.validate(
                SOURCE,
                changed,
                {"targets": {"Valuation!C10": 0.1}},
            )
        self.assertEqual(caught.exception.reason_codes, ["answer_value_leak"])

    def test_rejects_fence_relocated_between_editable_regions(self):
        fence = "\n```text\nPRESERVE THIS\n```\n"
        source = SOURCE.replace("\n## Input", fence + "\n## Input")
        candidate = CANDIDATE.replace(
            "\n## Input\n",
            "\n## Input\n" + fence,
        )
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "region and cross-type order",
        ):
            VALIDATOR.validate(source, candidate)

    def test_rejects_cross_type_table_list_reordering(self):
        source_constructs = "\n| A | B |\n| --- | --- |\n- keep this item\n"
        candidate_constructs = "\n- keep this item\n| A | B |\n| --- | --- |\n"
        source = SOURCE.replace("\n## Input", source_constructs + "\n## Input")
        candidate = CANDIDATE.replace(
            "\n## Input",
            candidate_constructs + "\n## Input",
        )

        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "cross-type order",
        ):
            VALIDATOR.validate(source, candidate)

    def test_rejects_modified_trailing_fence_at_region_boundary(self):
        fence = "\n```text\nPRESERVE THIS\n```"
        source = SOURCE.replace("\n\n## Input", fence + "\n\n## Input")
        candidate = source.replace("PRESERVE THIS", "CHANGED CONTENT")

        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "fenced code blocks",
        ):
            VALIDATOR.validate(source, candidate)

    def test_rejects_named_output_loss(self):
        changed = CANDIDATE.replace(
            "including Equity IRR and Equity value (DDM).",
            "including the headline outputs.",
        )
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "named example outputs",
        ):
            VALIDATOR.validate(SOURCE, changed)

    def test_rejects_permission_modality_loss(self):
        changed = CANDIDATE.replace(
            "You may install Python packages",
            "You can install Python packages",
        )
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "permission modality",
        ):
            VALIDATOR.validate(SOURCE, changed)

    def test_validator_cli_is_validation_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.md"
            candidate = root / "candidate.md"
            instruction = root / "instruction.md"
            answer_key = root / "answer_key.json"
            report = root / "validation.json"
            source.write_text(SOURCE, encoding="utf-8")
            candidate.write_text(CANDIDATE, encoding="utf-8")
            instruction.write_text(SOURCE, encoding="utf-8")
            answer_key.write_text(
                json.dumps({"targets": {"Valuation!C10": 0.123}}),
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
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(instruction.read_text(encoding="utf-8"), SOURCE)
            validation = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(validation["valid"])
            self.assertFalse(validation["applied"])

    def test_bom_crlf_round_trip_preserves_raw_format(self):
        source = SPANS.UTF8_BOM + SOURCE.replace("\n", "\r\n").encode("utf-8")
        candidate = SPANS.UTF8_BOM + CANDIDATE.replace("\n", "\r\n").encode("utf-8")
        span_map = SPANS.scan_instruction(source)
        preamble, input_body = SPANS.extract_editable_bodies(
            candidate, SPANS.scan_instruction(candidate)
        )

        assembled = SPANS.assemble_instruction(source, span_map, preamble, input_body)

        self.assertEqual(assembled, candidate)
        self.assertTrue(assembled.startswith(SPANS.UTF8_BOM))
        self.assertNotIn(b"\n", assembled.replace(b"\r\n", b""))
        self.assertTrue(VALIDATOR.validate(source, assembled)["valid"])

    def test_fenced_input_heading_is_not_structural(self):
        source = (
            "A model needs work.\n\n"
            "```markdown\n"
            "## Input\n"
            "example only\n"
            "```\n\n"
            "## Input\n\n"
            "The workbook is only in your working directory.\n\n"
            "## Output\n\n"
            "Return the result.\n"
        ).encode("utf-8")

        span_map = SPANS.scan_instruction(source)

        self.assertEqual(
            [(heading.level, heading.title) for heading in span_map.headings],
            [(2, "Input"), (2, "Output")],
        )
        self.assertEqual(
            source[
                span_map.input_body.start : span_map.input_body.end
            ].decode("utf-8"),
            "The workbook is only in your working directory.",
        )
        tilde_source = source.replace(b"```", b"~~~")
        tilde_spans = SPANS.scan_instruction(tilde_source)
        self.assertEqual(
            [(heading.level, heading.title) for heading in tilde_spans.headings],
            [(2, "Input"), (2, "Output")],
        )

    def test_duplicate_input_heading_is_structured_error(self):
        duplicate = (SOURCE + "\n## Input\n\nSecond input.\n").encode("utf-8")
        with self.assertRaises(SPANS.InstructionSpanError) as caught:
            SPANS.scan_instruction(duplicate)
        self.assertEqual(caught.exception.reason_code, "duplicate_input_heading")
        self.assertEqual(caught.exception.details["count"], 2)

    def test_fixed_bytes_produce_fixed_validation_report(self):
        source = SOURCE.encode("utf-8")
        candidate = CANDIDATE.encode("utf-8")
        first = VALIDATOR.validate(source, candidate)
        second = VALIDATOR.validate(source, candidate)
        self.assertEqual(first, second)

    def test_source_without_final_newline_stays_without_one(self):
        source = SOURCE.rstrip("\n").encode("utf-8")
        candidate = CANDIDATE.rstrip("\n").encode("utf-8")
        report = VALIDATOR.validate(source, candidate)
        self.assertTrue(report["valid"])
        self.assertFalse(candidate.endswith(b"\n"))

    def test_longer_indented_fence_cannot_be_modified(self):
        source = SOURCE.replace(
            "The workbook `0001-inputs.xlsx` is in your working directory.",
            "````text\nprotected\n  ````\n\n"
            "The workbook `0001-inputs.xlsx` is in your working directory.",
        )
        candidate = source.replace("protected", "changed")

        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "fenced code blocks",
        ):
            VALIDATOR.validate(source, candidate)

    def test_heading_separator_bytes_are_protected(self):
        changed = CANDIDATE.replace("## Input\n\n", "## Input\n", 1)
        with self.assertRaisesRegex(
            VALIDATOR.RewriteValidationError,
            "protected section",
        ):
            VALIDATOR.validate(SOURCE, changed)


class NaturalizationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "instruction.md"
        self.task_toml = self.root / "task.toml"
        self.recovery = self.root / "recovery"
        self.source.write_bytes(SOURCE.encode("utf-8"))
        self.task_toml.write_text(
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

    def tearDown(self):
        self.temp.cleanup()

    def candidate_bodies(self, candidate: str = CANDIDATE):
        raw = candidate.encode("utf-8")
        return SPANS.extract_editable_bodies(raw, SPANS.scan_instruction(raw))

    def init(self):
        return RECOVERY.init_recovery(
            self.source,
            self.recovery,
            task_toml_path=self.task_toml,
        )

    def approve(self):
        return RECOVERY.accept_semantic_review(
            self.recovery,
            message="Clause-by-clause semantic review passed.",
            reviewer="test-reviewer",
        )

    def test_retry_isolated_from_first_attempt_and_then_applies(self):
        self.init()
        preamble, good_input = self.candidate_bodies()
        bad_input = good_input.replace(b"only", b"")

        first = RECOVERY.submit_attempt(self.recovery, preamble, bad_input)
        second = RECOVERY.submit_attempt(self.recovery, preamble, good_input)
        self.approve()
        applied = RECOVERY.apply_recovery(self.recovery)

        self.assertFalse(first["valid"])
        self.assertEqual(first["state"], "retry_ready")
        self.assertTrue(second["valid"])
        self.assertEqual(second["state"], "validated")
        self.assertEqual(self.source.read_bytes(), CANDIDATE.encode("utf-8"))
        self.assertEqual(applied["state"], "applied")
        first_source = (self.recovery / "attempt-01" / "source.snapshot.md").read_bytes()
        second_source = (self.recovery / "attempt-02" / "source.snapshot.md").read_bytes()
        self.assertEqual(first_source, SOURCE.encode("utf-8"))
        self.assertEqual(second_source, SOURCE.encode("utf-8"))
        metadata = self.task_toml.read_text(encoding="utf-8")
        self.assertIn("attempts = 2", metadata)
        self.assertIn(
            'instruction_sha256 = "%s"' % SPANS.sha256_bytes(CANDIDATE.encode()),
            metadata,
        )

    def test_two_invalid_attempts_exhaust_recovery(self):
        self.init()
        preamble, good_input = self.candidate_bodies()
        bad_input = good_input.replace(b"only", b"")
        other_bad_input = bad_input.replace(b"available", b"provided")

        first = RECOVERY.submit_attempt(self.recovery, preamble, bad_input)
        repeated = RECOVERY.submit_attempt(self.recovery, preamble, bad_input)
        second = RECOVERY.submit_attempt(self.recovery, preamble, other_bad_input)

        self.assertEqual(first["state"], "retry_ready")
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(second["state"], "exhausted")
        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.submit_attempt(self.recovery, preamble, good_input)
        self.assertEqual(caught.exception.reason_code, "terminal_state")

    def test_protected_construct_rejection_is_retryable(self):
        source_constructs = "\n| A | B |\n| --- | --- |\n- keep this item\n"
        candidate_constructs = "\n- keep this item\n| A | B |\n| --- | --- |\n"
        source = SOURCE.replace("\n## Input", source_constructs + "\n## Input")
        candidate = CANDIDATE.replace(
            "\n## Input",
            candidate_constructs + "\n## Input",
        )
        self.source.write_text(source, encoding="utf-8")
        self.init()
        preamble, input_body = self.candidate_bodies(candidate)

        report = RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        self.assertFalse(report["valid"])
        self.assertIn(
            "protected_construct_order_changed",
            report["reason_codes"],
        )
        self.assertEqual(report["state"], "retry_ready")

    def test_source_drift_fails_closed(self):
        self.init()
        self.source.write_text(SOURCE.replace("colleague", "manager"), encoding="utf-8")
        preamble, input_body = self.candidate_bodies()

        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        self.assertEqual(caught.exception.reason_code, "source_drift")

    def test_missing_bound_answer_key_fails_closed(self):
        answer_key = self.root / "answer_key.json"
        answer_key.write_text(
            json.dumps({"targets": {"Valuation!C10": 1e20}}),
            encoding="utf-8",
        )
        RECOVERY.init_recovery(
            self.source,
            self.recovery,
            task_toml_path=self.task_toml,
            answer_key_path=answer_key,
        )
        (self.recovery / "answer_key.snapshot.json").unlink()
        preamble, input_body = self.candidate_bodies()

        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        self.assertEqual(
            caught.exception.reason_code,
            "answer_key_snapshot_missing",
        )

    def test_bound_answer_key_hash_drift_fails_status(self):
        answer_key = self.root / "answer_key.json"
        answer_key.write_text(
            json.dumps({"targets": {"Valuation!C10": 1e20}}),
            encoding="utf-8",
        )
        RECOVERY.init_recovery(
            self.source,
            self.recovery,
            task_toml_path=self.task_toml,
            answer_key_path=answer_key,
        )
        answer_snapshot = self.recovery / "answer_key.snapshot.json"
        answer_snapshot.chmod(0o600)
        answer_snapshot.write_text(
            json.dumps({"targets": {}}),
            encoding="utf-8",
        )

        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.recovery_status(self.recovery)

        self.assertEqual(
            caught.exception.reason_code,
            "answer_key_snapshot_drift",
        )

    def test_semantic_review_rejection_opens_only_second_attempt(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        first = RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        rejected = RECOVERY.reject_semantic_review(
            self.recovery,
            message="The rewrite weakened one requirement.",
        )
        second = RECOVERY.submit_attempt(
            self.recovery,
            preamble.replace(b"taking over", b"rebuilding"),
            input_body,
        )

        self.assertTrue(first["valid"])
        self.assertEqual(rejected["state"], "retry_ready")
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(second["state"], "validated")
        self.assertTrue(
            (self.recovery / "attempt-01" / "semantic-review.json").is_file()
        )

    def test_apply_requires_semantic_approval_bound_to_candidate(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.apply_recovery(self.recovery)

        self.assertEqual(caught.exception.reason_code, "semantic_review_required")
        review = self.approve()
        self.assertEqual(
            review["candidate_sha256"],
            json.loads(
                (self.recovery / "state.json").read_text(encoding="utf-8")
            )["final_instruction_sha256"],
        )
        review_path = self.recovery / "attempt-01" / "semantic-review.json"
        tampered = json.loads(review_path.read_text(encoding="utf-8"))
        tampered["message"] = "tampered after approval"
        review_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(RECOVERY.RecoveryError) as tamper:
            RECOVERY.apply_recovery(self.recovery)
        self.assertEqual(
            tamper.exception.reason_code,
            "semantic_review_mismatch",
        )

    def test_identical_candidate_after_semantic_rejection_uses_attempt_two(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        RECOVERY.reject_semantic_review(
            self.recovery,
            message="Independent reviewer rejected this wording.",
        )

        second = RECOVERY.submit_attempt(self.recovery, preamble, input_body)

        self.assertEqual(second["attempt"], 2)
        self.assertEqual(
            json.loads(
                (self.recovery / "state.json").read_text(encoding="utf-8")
            )["status"],
            "validated",
        )

    def test_interrupted_apply_rolls_back_then_retries(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        self.approve()
        original_task = self.task_toml.read_bytes()

        with self.assertRaises(RECOVERY.RecoveryInterruption):
            RECOVERY.apply_recovery(
                self.recovery,
                failpoint=lambda point: (
                    (_ for _ in ()).throw(
                        RECOVERY.RecoveryInterruption(
                            "simulated_interruption", point
                        )
                    )
                    if point == "after_instruction_replace"
                    else None
                ),
            )
        status = RECOVERY.recovery_status(self.recovery)

        self.assertEqual(status["status"], "validated")
        self.assertTrue(status["transaction_recovery"]["rolled_back"])
        self.assertEqual(self.source.read_bytes(), SOURCE.encode("utf-8"))
        self.assertEqual(self.task_toml.read_bytes(), original_task)

        result = RECOVERY.apply_recovery(self.recovery)
        repeated = RECOVERY.apply_recovery(self.recovery)
        self.assertTrue(result["applied"])
        self.assertTrue(repeated["idempotent"])
        terminal = json.loads(
            (self.recovery / "validation.json").read_text(encoding="utf-8")
        )
        self.assertTrue(terminal["applied"])

    def test_interrupted_rollback_resumes_without_mixed_pair(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        self.approve()
        original_task = self.task_toml.read_bytes()
        with self.assertRaises(RECOVERY.RecoveryInterruption):
            RECOVERY.apply_recovery(
                self.recovery,
                failpoint=lambda point: (
                    (_ for _ in ()).throw(
                        RECOVERY.RecoveryInterruption(
                            "simulated_interruption", point
                        )
                    )
                    if point == "after_task_replace"
                    else None
                ),
            )

        with self.assertRaises(RECOVERY.RecoveryInterruption):
            RECOVERY.recover_transaction(
                self.recovery / "apply-transaction",
                expected_instruction_path=self.source,
                expected_task_toml_path=self.task_toml,
                failpoint=lambda point: (
                    (_ for _ in ()).throw(
                        RECOVERY.RecoveryInterruption(
                            "simulated_rollback_interruption", point
                        )
                    )
                    if point == "after_rollback_instruction_restore"
                    else None
                ),
            )

        self.assertEqual(self.source.read_bytes(), SOURCE.encode("utf-8"))
        self.assertNotEqual(self.task_toml.read_bytes(), original_task)
        status = RECOVERY.recovery_status(self.recovery)
        self.assertEqual(status["status"], "validated")
        self.assertTrue(status["transaction_recovery"]["rolled_back"])
        self.assertEqual(self.source.read_bytes(), SOURCE.encode("utf-8"))
        self.assertEqual(self.task_toml.read_bytes(), original_task)

    def test_committed_interruption_is_reconciled_idempotently(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        self.approve()

        def interrupt_after_commit(point):
            if point == "after_commit":
                raise RECOVERY.RecoveryInterruption(
                    "simulated_interruption", point
                )

        with self.assertRaises(RECOVERY.RecoveryInterruption):
            RECOVERY.apply_recovery(
                self.recovery,
                failpoint=interrupt_after_commit,
            )

        status = RECOVERY.recovery_status(self.recovery)
        repeated = RECOVERY.apply_recovery(self.recovery)
        self.assertEqual(status["status"], "applied")
        self.assertTrue(status["transaction_recovery"]["committed"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.source.read_bytes(), CANDIDATE.encode("utf-8"))

    def test_applied_restart_repairs_stale_root_report(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        self.approve()
        RECOVERY.apply_recovery(self.recovery)
        (self.recovery / "validation.json").write_text("{}\n", encoding="utf-8")

        restarted = self.init()
        verified = RECOVERY.verify_applied_recovery(self.recovery)

        self.assertEqual(restarted["status"], "applied")
        self.assertTrue(verified["applied"])
        self.assertTrue(
            json.loads(
                (self.recovery / "validation.json").read_text(encoding="utf-8")
            )["applied"]
        )

    def test_recovery_refuses_to_overwrite_unrelated_post_crash_edit(self):
        self.init()
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        self.approve()
        with self.assertRaises(RECOVERY.RecoveryInterruption):
            RECOVERY.apply_recovery(
                self.recovery,
                failpoint=lambda point: (
                    (_ for _ in ()).throw(
                        RECOVERY.RecoveryInterruption(
                            "simulated_interruption", point
                        )
                    )
                    if point == "after_instruction_replace"
                    else None
                ),
            )
        unrelated = self.task_toml.read_text(encoding="utf-8") + "\n# external edit\n"
        self.task_toml.write_text(unrelated, encoding="utf-8")

        with self.assertRaises(RECOVERY.RecoveryError) as caught:
            RECOVERY.recovery_status(self.recovery)

        self.assertEqual(caught.exception.reason_code, "transaction_target_drift")
        self.assertEqual(self.task_toml.read_text(encoding="utf-8"), unrelated)

    def test_separate_recovery_roots_share_target_lock(self):
        second_root = self.root / "recovery-two"
        self.init()
        RECOVERY.init_recovery(
            self.source,
            second_root,
            task_toml_path=self.task_toml,
        )
        preamble, input_body = self.candidate_bodies()
        RECOVERY.submit_attempt(self.recovery, preamble, input_body)
        RECOVERY.submit_attempt(second_root, preamble, input_body)

        with RECOVERY.target_pair_lock(self.source, self.task_toml):
            with self.assertRaises(RECOVERY.RecoveryError) as caught:
                RECOVERY.apply_recovery(second_root)

        self.assertEqual(caught.exception.reason_code, "recovery_locked")

    def test_concurrent_state_mutation_fails_closed(self):
        self.init()
        with RECOVERY.recovery_lock(self.recovery):
            with self.assertRaises(RECOVERY.RecoveryError) as caught:
                RECOVERY.recovery_status(self.recovery)
        self.assertEqual(caught.exception.reason_code, "recovery_locked")


if __name__ == "__main__":
    unittest.main()
