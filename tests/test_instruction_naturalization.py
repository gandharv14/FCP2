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

FREEZE_SCRIPT = SCRIPT.with_name("freeze_protected_spans.py")
FREEZE_SPEC = importlib.util.spec_from_file_location("freeze_protected_spans", FREEZE_SCRIPT)
FREEZER = importlib.util.module_from_spec(FREEZE_SPEC)
assert FREEZE_SPEC.loader is not None
FREEZE_SPEC.loader.exec_module(FREEZER)

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


class ProtectedAnchorSpecTests(unittest.TestCase):
    def test_specs_cover_validator_protected_anchors(self):
        specs = VALIDATOR.protected_anchor_specs(SOURCE)
        by_check = {spec["check"]: spec for spec in specs}
        self.assertIn("named example outputs preserved", by_check)
        self.assertEqual(
            by_check["named example outputs preserved"]["phrase"],
            "Equity IRR and Equity value (DDM)",
        )
        self.assertIn("semantic anchor preserved: rebuild", by_check)
        self.assertIn("semantic anchor preserved: research data service", by_check)
        self.assertIn("Input permission modality preserved", by_check)
        self.assertEqual(by_check["Input permission modality preserved"]["phrase"], "may ")
        self.assertIn("Input exclusivity preserved", by_check)

    def test_specs_regions_match_validator_regions(self):
        for spec in VALIDATOR.protected_anchor_specs(SOURCE):
            self.assertIn(spec["region"], ("preamble", "input", "mutable"))
            self.assertTrue(spec["accepted"])
            self.assertEqual(
                [term for term in spec["accepted"] if term != term.lower()], []
            )


class FreezeRestoreTests(unittest.TestCase):
    def test_freeze_marks_every_protected_anchor(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        self.assertTrue(spans)
        joined_checks = [
            check for span in spans.values() for check in span["checks"]
        ]
        for spec in VALIDATOR.protected_anchor_specs(SOURCE):
            self.assertIn(spec["check"], joined_checks)
        for span_id, span in spans.items():
            self.assertIn(
                "[[%s]]%s[[/%s]]" % (span_id, span["text"], span_id), writer_source
            )

    def test_identity_roundtrip_restores_source(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        restored, errors, drift = FREEZER.restore(writer_source, spans)
        self.assertEqual(errors, [])
        self.assertEqual(drift, [])
        self.assertEqual(restored, SOURCE)

    def test_rewrite_around_spans_passes_validator(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        marked = writer_source.replace(
            "A colleague has sent you", "You are taking over"
        )
        self.assertNotEqual(marked, writer_source)
        restored, errors, _ = FREEZER.restore(marked, spans)
        self.assertEqual(errors, [])
        report = VALIDATOR.validate(SOURCE, restored)
        self.assertTrue(report["valid"])

    def test_tampered_span_is_reinserted_and_validator_passes(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        rebuild_id = next(
            span_id for span_id, span in spans.items() if span["text"] == "Rebuild"
        )
        tampered = writer_source.replace(
            "[[%s]]Rebuild[[/%s]]" % (rebuild_id, rebuild_id),
            "[[%s]]Reconstruct[[/%s]]" % (rebuild_id, rebuild_id),
        )
        restored, errors, drift = FREEZER.restore(tampered, spans)
        self.assertEqual(errors, [])
        self.assertTrue(any(rebuild_id in note for note in drift))
        self.assertIn("Rebuild", restored)
        self.assertNotIn("Reconstruct", restored)
        report = VALIDATOR.validate(SOURCE, restored)
        self.assertTrue(report["valid"])

    def test_dropped_span_fails_restore_with_expected_text(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        rebuild_id = next(
            span_id for span_id, span in spans.items() if span["text"] == "Rebuild"
        )
        dropped = writer_source.replace(
            "[[%s]]Rebuild[[/%s]] " % (rebuild_id, rebuild_id), ""
        )
        _, errors, _ = FREEZER.restore(dropped, spans)
        self.assertTrue(errors)
        blob = " ".join(errors)
        self.assertIn(rebuild_id, blob)
        self.assertIn("[[%s]]Rebuild[[/%s]]" % (rebuild_id, rebuild_id), blob)
        self.assertIn("semantic anchor preserved: rebuild", blob)

    def test_duplicated_span_fails_restore(self):
        writer_source, spans = FREEZER.freeze(SOURCE)
        rebuild_block = None
        for span_id, span in spans.items():
            if span["text"] == "Rebuild":
                rebuild_block = "[[%s]]%s[[/%s]]" % (span_id, span["text"], span_id)
        assert rebuild_block is not None
        duplicated = writer_source.replace(
            rebuild_block, rebuild_block + " and " + rebuild_block
        )
        _, errors, _ = FREEZER.restore(duplicated, spans)
        self.assertTrue(any("2 times" in error for error in errors))

    def test_cli_freeze_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.md"
            writer_source = root / "writer_source.md"
            span_map = root / "frozen_spans.json"
            marked = root / "candidate.marked.md"
            candidate = root / "candidate.md"
            source.write_text(SOURCE, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_SCRIPT),
                    "freeze",
                    str(source),
                    "--writer-source",
                    str(writer_source),
                    "--span-map",
                    str(span_map),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            marked.write_text(
                writer_source.read_text(encoding="utf-8").replace(
                    "A colleague has sent you", "You have inherited"
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(FREEZE_SCRIPT),
                    "restore",
                    str(marked),
                    "--span-map",
                    str(span_map),
                    "--output",
                    str(candidate),
                    "--report",
                    str(root / "restore.json"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            restored = candidate.read_text(encoding="utf-8")
            self.assertIn("You have inherited", restored)
            self.assertNotIn("[[F", restored)
            report = VALIDATOR.validate(SOURCE, restored)
            self.assertTrue(report["valid"])
            restore_report = json.loads(
                (root / "restore.json").read_text(encoding="utf-8")
            )
            self.assertTrue(restore_report["restored"])


NUMERIC_SOURCE = """\
A partner has sent you a leveraged buyout model with every derived figure
stripped out. Rebuild the calculations and report the 2 headline figures,
such as Exit equity and Sponsor IRR. The full list is below. Round to 2
decimals only where a sheet already does, and take the 27.5% tax rate from
https://tax.example.com/rates/2026 when a schedule needs it.

## Input

The workbook `0517-inputs.xlsx` is in your working directory. Every derived
cell is blank, while the remaining inputs are present.
Balance Sheet!C7 holds the opening balance, and 2 assumption blocks feed it.
You may install Python packages to read the workbook.

## Output

Write a JSON object to `/app/answers.json`. Report every value without
rounding.
"""


class FrozenTokenTests(unittest.TestCase):
    """Numbers, cell refs, URLs, and inline code are frozen, not just anchors.

    Regression for the harbor reruns where the rewriter dropped one occurrence
    of a prose-embedded numeric token (0517: '3', 0526: '2', 0536: '4') and
    the loss surfaced only at the post-hoc validator.
    """

    def spans_by_text(self, spans):
        by_text = {}
        for span_id, span in spans.items():
            by_text.setdefault(span["text"], []).append((span_id, span))
        return by_text

    def test_every_mutable_validator_token_is_frozen(self):
        writer_source, _ = FREEZER.freeze(NUMERIC_SOURCE)
        stripped = FREEZER.MARKER_PAIR_RE.sub("", writer_source)
        leftover_mutable = VALIDATOR.mutable_text(stripped)
        for label in VALIDATOR.FROZEN_TOKEN_LABELS:
            pattern = dict(VALIDATOR.EXACT_TOKEN_CHECKS)[label]
            leaked = [m.group(0) for m in pattern.finditer(leftover_mutable)]
            self.assertEqual(
                leaked, [], "unfrozen %s left in rewriteable regions" % label
            )

    def test_each_numeric_occurrence_gets_its_own_span(self):
        _, spans = FREEZER.freeze(NUMERIC_SOURCE)
        twos = self.spans_by_text(spans).get("2", [])
        self.assertEqual(len(twos), 3)
        for _, span in twos:
            self.assertIn("numbers preserved exactly", span["checks"])
        percents = self.spans_by_text(spans).get("27.5%", [])
        self.assertEqual(len(percents), 1)

    def test_url_inline_code_and_cell_ref_spans_frozen(self):
        _, spans = FREEZER.freeze(NUMERIC_SOURCE)
        all_checks = {
            check for span in spans.values() for check in span["checks"]
        }
        self.assertIn("URLs preserved exactly", all_checks)
        self.assertIn("inline-code spans preserved exactly", all_checks)
        self.assertIn("cell references preserved exactly", all_checks)
        self.assertIn("numbers preserved exactly", all_checks)
        texts = list(self.spans_by_text(spans))
        self.assertTrue(
            any("https://tax.example.com/rates/2026" in text for text in texts)
        )
        self.assertTrue(any("`0517-inputs.xlsx`" in text for text in texts))
        self.assertTrue(any("Balance Sheet!C7" in text for text in texts))

    def test_identity_roundtrip_stays_byte_identical(self):
        writer_source, spans = FREEZER.freeze(NUMERIC_SOURCE)
        restored, errors, drift = FREEZER.restore(writer_source, spans)
        self.assertEqual(errors, [])
        self.assertEqual(drift, [])
        self.assertEqual(restored, NUMERIC_SOURCE)

    def test_dropping_one_numeric_occurrence_fails_at_restore(self):
        writer_source, spans = FREEZER.freeze(NUMERIC_SOURCE)
        span_id, span = self.spans_by_text(spans)["2"][0]
        block = "[[%s]]2[[/%s]]" % (span_id, span_id)
        dropped = writer_source.replace(block + " ", "", 1)
        self.assertNotEqual(dropped, writer_source)
        _, errors, _ = FREEZER.restore(dropped, spans)
        self.assertTrue(errors, "restore must fail before the validator runs")
        blob = " ".join(errors)
        self.assertIn(span_id, blob)
        self.assertIn(block, blob)
        self.assertIn("numbers preserved exactly", blob)

    def test_dropping_url_span_fails_at_restore(self):
        writer_source, spans = FREEZER.freeze(NUMERIC_SOURCE)
        url_id, url_span = next(
            (span_id, span)
            for span_id, span in spans.items()
            if "https://tax.example.com/rates/2026" in span["text"]
        )
        block = "[[%s]]%s[[/%s]]" % (url_id, url_span["text"], url_id)
        _, errors, _ = FREEZER.restore(writer_source.replace(block, ""), spans)
        blob = " ".join(errors)
        self.assertIn(url_id, blob)
        self.assertIn(url_span["text"], blob)
        self.assertIn("URLs preserved exactly", blob)

    def test_moving_frozen_number_within_sentence_passes(self):
        writer_source, spans = FREEZER.freeze(NUMERIC_SOURCE)
        span_id = next(
            span_id
            for span_id, span in spans.items()
            if span["text"] == "2"
            and "report the [[%s]]" % span_id in writer_source
        )
        block = "[[%s]]2[[/%s]]" % (span_id, span_id)
        moved = writer_source.replace(
            "report the %s headline figures" % block,
            "report the headline figures (%s in total)" % block,
        )
        self.assertNotEqual(moved, writer_source)
        restored, errors, drift = FREEZER.restore(moved, spans)
        self.assertEqual(errors, [])
        self.assertEqual(drift, [])
        self.assertIn("report the headline figures (2 in total)", restored)
        report = VALIDATOR.validate(NUMERIC_SOURCE, restored)
        self.assertTrue(report["valid"])

    def test_marker_ids_beyond_two_digits_roundtrip(self):
        spans = {"F100": {"text": "42", "checks": ["numbers preserved exactly"]}}
        restored, errors, drift = FREEZER.restore(
            "keep [[F100]]42[[/F100]] here", spans
        )
        self.assertEqual(errors, [])
        self.assertEqual(drift, [])
        self.assertEqual(restored, "keep 42 here")

    def test_prompt_version_bumped_for_token_freezing_contract(self):
        self.assertEqual(
            VALIDATOR.PROMPT_VERSION, "finance-instruction-naturalizer-v3"
        )


class DiagnosticMessageTests(unittest.TestCase):
    def _error_for(self, candidate: str) -> str:
        with self.assertRaises(VALIDATOR.RewriteValidationError) as ctx:
            VALIDATOR.validate(SOURCE, candidate)
        return str(ctx.exception)

    def test_altered_named_outputs_rejected_with_expected_and_found(self):
        changed = CANDIDATE.replace(
            "including Equity IRR and Equity value (DDM)",
            "including Equity IRR and the Equity value (DDM)",
        )
        message = self._error_for(changed)
        self.assertIn("named example outputs preserved", message)
        self.assertIn("expected:", message)
        self.assertIn("equity irr and equity value (ddm)", message)
        self.assertIn("found:", message)

    def test_altered_rebuild_anchor_rejected_with_expected_and_found(self):
        changed = CANDIDATE.replace("Rebuild the calculations", "Redo the calculations")
        message = self._error_for(changed)
        self.assertIn("semantic anchor preserved: rebuild", message)
        self.assertIn("expected:", message)
        self.assertIn("'rebuild'", message)
        self.assertIn("found:", message)

    def test_lost_may_modality_rejected_with_expected_and_found(self):
        changed = CANDIDATE.replace("You may install", "You can install")
        message = self._error_for(changed)
        self.assertIn("Input permission modality preserved", message)
        self.assertIn("'may '", message)
        self.assertIn("expected:", message)
        self.assertIn("found:", message)

    def test_protected_section_failure_names_first_diff(self):
        changed = CANDIDATE.replace(
            "Report 2 values without rounding.",
            "Report 2 rounded values.",
        )
        message = self._error_for(changed)
        self.assertIn("protected section preserved byte-for-byte", message)
        self.assertIn("expected:", message)
        self.assertIn("found:", message)
        self.assertIn("Report 2 values without rounding.", message)

    def test_failing_checks_still_fail(self):
        # The messages became richer; the verdicts must be unchanged.
        for mutation in (
            ("are available only through", "are available through"),
            ("Rebuild the calculations", "Reconstruct the calculations"),
            ("You may install", "You might want to install"),
        ):
            changed = CANDIDATE.replace(*mutation)
            with self.assertRaises(VALIDATOR.RewriteValidationError):
                VALIDATOR.validate(SOURCE, changed)


if __name__ == "__main__":
    unittest.main()
