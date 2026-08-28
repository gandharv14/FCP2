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
