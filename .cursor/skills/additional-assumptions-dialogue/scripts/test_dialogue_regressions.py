#!/usr/bin/env python3
"""Fixture checks for the additional-assumptions dialogue skill."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from aa_lib import parse_turns, require_cast
from extract_claims import must_say_atoms
from spoken_formula import speak_steps
from validate_dialogue import (
    DialogueError,
    apply,
    audit_dialogue,
    check_draft,
    map_turns_to_claims,
    review_accuracy_passed,
    review_passed,
)

RCF_STEPS = (
    "multiply (take the negative of (take the greater of (0) and "
    "(take the lesser of (cell LBO!O138 on the row labelled "
    '"Total Cash for Discretionary Debt Paydown") and '
    "(subtract (cell LBO!O146 on the row labelled "
    '"Revolving Credit Facility (L+4.50%)") from '
    "(cell LBO!N141 on the row labelled "
    '"Revolving Credit Facility (L+4.50%)"))))) by '
    '(cell LBO!J151 on the row labelled "Revolving Credit Facility (L+4.50%)")'
)
RCF_EVIDENCE = "=-MAX(0,MIN(O138,N141-O146))*$J$151"
BOUNDED_WINDOW_STEPS = (
    'when ((cell Model!C10 on the row labelled "Date") is at least '
    '(cell Model!C8 on the row labelled "Start Date") and '
    '(cell Model!C10 on the row labelled "Date") is at most '
    '(cell Model!C9 on the row labelled "End Date")) is true, use '
    '(cell Model!C11 on the row labelled "Operating Cost"); otherwise use (0)'
)
IFERROR_STEPS = (
    'use (cell Model!C20 on the row labelled "IRR"), or use ("N/A") '
    "if that calculation errors"
)
LOCKED_WINDOW_STEPS = (
    "when ((cell 'Monthly Build Projections'!C372 with its row fixed when copied) "
    "is greater than (fixed cell 'Assumptions Matrix'!F63 on the row labelled "
    '"MVP Development Phase Cost Total") and (cell '
    "'Monthly Build Projections'!C372 with its row fixed when copied) is at most "
    "(fixed cell 'Assumptions Matrix'!G63 on the row labelled "
    '"MVP Development Phase Cost Total")) is true, use (fixed cell '
    "'Assumptions Matrix'!I63 on the row labelled "
    '"MVP Development Phase Cost Total"); otherwise use (0)'
)
LOCKED_WINDOW_EVIDENCE = (
    "=IF(AND(C$372>'Assumptions Matrix'!$F$63,"
    "C$372<='Assumptions Matrix'!$G$63),"
    "'Assumptions Matrix'!$I$63,0)"
)
LOCKED_SUMPRODUCT_STEPS = (
    "sum the products of corresponding values in "
    '(fixed range DCF!N71:N75 on the rows labelled "Year 1", "Year 2", '
    '"Year 3", "Year 4" and "Year 5") and '
    '(fixed range DCF!O71:O75 on the rows labelled "Year 1", "Year 2", '
    '"Year 3", "Year 4" and "Year 5")'
)
SAME_LABEL_FACTORS_STEPS = (
    'multiply (fixed cell DCF!M71 on the row labelled "Discount Factor") by '
    '(fixed cell DCF!P71 on the row labelled "Discount Factor")'
)


TASK = Path()

CLAIMS = {
    "schema_version": "1.1",
    "junior_titles": ["Analyst", "Associate"],
    "senior_titles": ["VP", "Director", "Managing Director"],
    "empty": False,
    "claims": [
        {
            "record_id": "projection_rule::Operations::Fee",
            "sheet": "Operations",
            "row_label": "Fee",
            "must_say": [
                'The row labelled "Fee" is worked out from the row labelled "Revenue"'
            ],
        }
    ],
}


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise SystemExit(f"FAIL {name}: {detail}")
    print("ok", name)


def test_senior_kickoff_unclaimed() -> None:
    text = (
        "**Managing Director:** Where are we on this?\n\n"
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** How was Fee carried?\n"
        "**VP:** On Operations, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
        "**Associate:** Got it.\n"
    )
    turns = parse_turns(text)
    _ok("kickoff-first-is-md", turns[0]["speaker"] == "Managing Director")
    mapped, faults = map_turns_to_claims(turns, CLAIMS["claims"])
    _ok("kickoff-unclaimed", not faults)
    speakers = [t["speaker"] for t in mapped["projection_rule::Operations::Fee"]]
    _ok("kickoff-not-on-claim", speakers[0] != "Managing Director")


def test_associate_junior_and_ack() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Walk me through Fee.\n"
        "**Director:** On Operations, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
        "**Associate:** Makes sense.\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    _ok("associate-no-cast-fault", not report["cast_faults"], str(report["cast_faults"]))
    _ok("ack-allowed", not any("last turn" in f for f in report["faults"]))


def test_unknown_and_legacy_titles() -> None:
    unknown = "**Intern:** hi\n<!-- claim:projection_rule::Operations::Fee -->\n**VP:** x\n"
    legacy = "**Senior banker:** hi\n<!-- claim:projection_rule::Operations::Fee -->\n**VP:** x\n"
    for name, text in (("unknown", unknown), ("legacy", legacy)):
        report = check_draft(text, CLAIMS, TASK)
        _ok(f"{name}-cast", bool(report["cast_faults"]), str(report["cast_faults"]))


def test_marker_coverage() -> None:
    text = "**VP:** On Operations, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    mapped, faults = map_turns_to_claims(parse_turns(text), CLAIMS["claims"])
    _ok("no-comment-fault", any("no claim comments" in f for f in faults))
    _ok("unmarked-not-mapped", not mapped["projection_rule::Operations::Fee"])


def test_review_coverage() -> None:
    good = {
        "passed": True,
        "accuracy": {
            "verdict": "pass",
            "claims": [{"record_id": "projection_rule::Operations::Fee", "must_say": "entailed"}],
            "extras": [],
            "cell_refs_in_senior_turns": [],
        },
        "naturalness": {"verdict": "pass", "findings": []},
    }
    empty = {"passed": True, "accuracy": {"verdict": "pass", "claims": []}, "naturalness": {"verdict": "pass"}}
    _ok("review-good", review_passed(good, CLAIMS))
    _ok("review-empty-claims-fail", not review_accuracy_passed(empty, CLAIMS))


def test_formula_literal_target_collision_is_claim_scoped(tmp_path: Path) -> None:
    task = tmp_path / "0399-outputs"
    (task / "tests").mkdir(parents=True)
    (task / "tests" / "answer_key.json").write_text(
        json.dumps({"targets": {"Timing!I32": 365.0}, "tolerance": {}}),
        encoding="utf-8",
    )
    claim = {
        "record_id": "method_revenue::FM::Growth rate, %::001",
        "sheet": "FM",
        "row_label": "Growth rate, %",
        "reviewer_only": {
            "source": "custom_method_detector",
            "cells": ["FM!I117"],
            "evidence": "=IF(I25=1,I118*I119*I120*365,0)",
        },
    }
    claims = [claim]

    good = (
        "<!-- claim:method_revenue::FM::Growth rate, %::001 -->\n"
        "**Analyst:** How should I build Growth rate, %?\n"
        "**VP:** Multiply the operating drivers by 365 for Growth rate, %.\n"
    )
    good_turns = parse_turns(good)
    good_mapped, _ = map_turns_to_claims(good_turns, claims)
    _ok(
        "formula-literal-collision-allowed",
        not audit_dialogue(good, claims, good_turns, good_mapped, task),
    )

    leaked = good.replace("by 365 for", "by 365, and the target is 365 for")
    leaked_turns = parse_turns(leaked)
    leaked_mapped, _ = map_turns_to_claims(leaked_turns, claims)
    faults = audit_dialogue(leaked, claims, leaked_turns, leaked_mapped, task)
    _ok(
        "extra-target-occurrence-fails",
        faults == ["numeric literal 365 matches target 365.0"],
        str(faults),
    )

    junior_leak = good.replace(
        "How should I build Growth rate, %?",
        "The target is 365. How should I build Growth rate, %?",
    )
    junior_turns = parse_turns(junior_leak)
    junior_mapped, _ = map_turns_to_claims(junior_turns, claims)
    faults = audit_dialogue(
        junior_leak, claims, junior_turns, junior_mapped, task
    )
    _ok(
        "junior-target-occurrence-fails",
        faults == ["numeric literal 365 matches target 365.0"],
        str(faults),
    )


def test_stale_pack() -> None:
    try:
        require_cast({"senior_title": "Senior banker", "claims": []})
    except ValueError:
        _ok("stale-pack", True)
    else:
        _ok("stale-pack", False, "did not raise")


def test_apply_blocks_missing_must_say(tmp_path: Path) -> None:
    task = tmp_path / "0042-outputs"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text(
        "Preamble.\n\n## Input\n\nThe workbook `0042-inputs.xlsx` is in your working directory.\n\n"
        "## Output\n\nWrite answers.\n",
        encoding="utf-8",
    )
    (task / "task.toml").write_text("[metadata.naturalizer]\ninstruction_sha256 = \"abc\"\n", encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text(
        "FROM python:3.12-slim\nWORKDIR /app\nCOPY 0042-inputs.xlsx /app/0042-inputs.xlsx\n",
        encoding="utf-8",
    )
    (task / "environment" / "0042-inputs.xlsx").write_bytes(b"PK\x03\x04fake")
    draft = tmp_path / "draft.md"
    draft.write_text(
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Fee?\n**VP:** On Operations I will get back to you.\n",
        encoding="utf-8",
    )
    review = {
        "passed": False,
        "accuracy": {
            "verdict": "fail",
            "claims": [{"record_id": "projection_rule::Operations::Fee", "must_say": "missing"}],
            "extras": [],
            "cell_refs_in_senior_turns": [],
        },
        "naturalness": {"verdict": "pass", "findings": []},
    }
    try:
        apply(task, draft, CLAIMS, review, require_review_pass=False, skip_smoke=True)
    except DialogueError as exc:
        _ok("round2-missing-must-say", "must_say" in str(exc).lower() or "accuracy" in str(exc).lower(), str(exc))
    else:
        _ok("round2-missing-must-say", False, "apply did not raise")

    draft.write_text(
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** How should Fee work?\n"
        "**VP:** On Operations, The row labelled \"Fee\" is worked out from "
        "the row labelled \"Revenue\".\n",
        encoding="utf-8",
    )
    review = {
        "passed": False,
        "accuracy": {
            "verdict": "pass",
            "claims": [
                {
                    "record_id": "projection_rule::Operations::Fee",
                    "must_say": "entailed",
                }
            ],
            "extras": [],
            "cell_refs_in_senior_turns": [],
        },
        "naturalness": {"verdict": "fail", "findings": ["too repetitive"]},
    }
    try:
        apply(
            task,
            draft,
            CLAIMS,
            review,
            require_review_pass=False,
            skip_smoke=True,
        )
    except DialogueError as exc:
        _ok("round2-full-review-required", "full review" in str(exc).lower(), str(exc))
    else:
        _ok("round2-full-review-required", False, "apply accepted failed review")


def test_rcf_spoken_disambiguated() -> None:
    spoken = speak_steps(
        RCF_STEPS,
        representative="LBO!O151",
        evidence=RCF_EVIDENCE,
        sheet="LBO",
        row_label="Revolving Credit Facility (L+4.50%)",
    )
    _ok("rcf-last-period", "last period" in spoken, spoken)
    _ok("rcf-this-period", "this period" in spoken, spoken)
    _ok("rcf-locked", "locked input" in spoken, spoken)
    _ok("rcf-no-sheet-bang", "LBO!" not in spoken, spoken)
    _ok("rcf-no-o138", "O138" not in spoken, spoken)
    _ok("rcf-no-j151", "J151" not in spoken, spoken)
    cash = 'row labelled "Total Cash for Discretionary Debt Paydown"'
    _ok("rcf-cash-present", cash in spoken, spoken)
    _ok("rcf-cash-not-last", f"last period's {cash}" not in spoken, spoken)
    _ok("rcf-cash-not-this", f"this period's {cash}" not in spoken, spoken)


def test_rcf_must_say_atoms() -> None:
    spoken = speak_steps(
        RCF_STEPS,
        representative="LBO!O151",
        evidence=RCF_EVIDENCE,
        sheet="LBO",
        row_label="Revolving Credit Facility (L+4.50%)",
    )
    atoms = must_say_atoms(spoken, "LBO", "Revolving Credit Facility (L+4.50%)")
    blob = " ".join(atoms).lower()
    _ok("atoms-last", "last period" in blob, atoms)
    _ok("atoms-this", "this period" in blob, atoms)
    _ok("atoms-locked", "locked input" in blob, atoms)
    _ok("atoms-floor", "floor" in blob, atoms)
    _ok("atoms-flip", "flip" in blob, atoms)
    _ok("atoms-cash", "Total Cash for Discretionary Debt Paydown" in " ".join(atoms), atoms)
    _ok("atoms-rcf", "Revolving Credit Facility (L+4.50%)" in " ".join(atoms), atoms)
    _ok("atoms-no-copied", "copied across the forecast" not in blob, atoms)


def test_bounded_window_preserves_both_conditions_and_branches() -> None:
    spoken = speak_steps(
        BOUNDED_WINDOW_STEPS,
        representative="Model!C11",
        evidence="=IF(AND(C10>=C8,C10<=C9),C11,0)",
        sheet="Model",
        row_label="Operating Cost",
    )
    atoms = must_say_atoms(
        spoken + "\n" + BOUNDED_WINDOW_STEPS,
        "Model",
        "Operating Cost",
    )
    blob = " ".join(atoms).lower()
    _ok("bounds-lower-preserved", "at least" in spoken, spoken)
    _ok("bounds-upper-preserved", "at most" in spoken, spoken)
    _ok("bounds-true-branch-preserved", "Operating Cost" in spoken, spoken)
    _ok("bounds-false-branch-preserved", "otherwise 0" in spoken, spoken)
    _ok("bounds-required-lower", "at least" in blob, str(atoms))
    _ok("bounds-required-upper", "at most" in blob, str(atoms))
    _ok("bounds-required-branch", "otherwise" in blob, str(atoms))


def test_iferror_literal_is_not_a_row_label() -> None:
    spoken = speak_steps(
        IFERROR_STEPS,
        representative="Model!C20",
        evidence='=IFERROR(C20,"N/A")',
        sheet="Model",
        row_label="IRR",
    )
    atoms = must_say_atoms(
        spoken + "\n" + IFERROR_STEPS,
        "Model",
        "IRR",
    )
    _ok("iferror-keeps-fallback", "N/A" in spoken, spoken)
    _ok("iferror-no-literal-row", 'row labelled "N/A"' not in spoken, spoken)
    _ok(
        "iferror-no-literal-atom",
        not any('row labelled "N/A"' in atom for atom in atoms),
        str(atoms),
    )
    _ok("iferror-requires-errors", "errors" in " ".join(atoms).lower(), str(atoms))


def test_same_label_locked_inputs_keep_distinct_roles() -> None:
    spoken = speak_steps(
        LOCKED_WINDOW_STEPS,
        representative="'Monthly Build Projections'!C393",
        evidence=LOCKED_WINDOW_EVIDENCE,
        sheet="Monthly Build Projections",
        row_label="MVP Development Phase Costs",
    )
    atoms = must_say_atoms(
        spoken + "\n" + LOCKED_WINDOW_STEPS,
        "Monthly Build Projections",
        "MVP Development Phase Costs",
    )
    blob = " ".join(atoms).lower()
    _ok("locked-window-lower-role", "lower bound locked input" in spoken, spoken)
    _ok("locked-window-upper-role", "upper bound locked input" in spoken, spoken)
    _ok("locked-window-result-role", "result locked input" in spoken, spoken)
    _ok("locked-window-no-fixed-the", "fixed the locked input" not in spoken, spoken)
    _ok("locked-window-requires-lower", "lower bound" in blob, str(atoms))
    _ok("locked-window-requires-upper", "upper bound" in blob, str(atoms))
    _ok("locked-window-requires-result", "result locked input" in blob, str(atoms))
    _ok(
        "locked-window-requires-source-tab",
        "assumptions matrix tab" in blob,
        str(atoms),
    )


def test_locked_ranges_preserve_cardinality_and_operand_identity() -> None:
    spoken = speak_steps(
        LOCKED_SUMPRODUCT_STEPS,
        representative="DCF!Q80",
        evidence="=SUMPRODUCT($N$71:$N$75,$O$71:$O$75)",
        sheet="DCF",
        row_label="Product value",
    )
    atoms = must_say_atoms(spoken + "\n" + LOCKED_SUMPRODUCT_STEPS, "DCF", "Product value")
    blob = " ".join(atoms).lower()
    _ok("sumproduct-first-block", "first locked 5-row input block" in spoken, spoken)
    _ok("sumproduct-second-block", "second locked 5-row input block" in spoken, spoken)
    _ok("sumproduct-corresponding", "corresponding" in spoken, spoken)
    _ok("sumproduct-no-addresses", "N71" not in spoken and "O71" not in spoken, spoken)
    _ok("sumproduct-requires-first", "first locked input block" in blob, str(atoms))
    _ok("sumproduct-requires-second", "second locked input block" in blob, str(atoms))
    _ok("sumproduct-requires-cardinality", "5-row" in blob, str(atoms))
    _ok("sumproduct-requires-operation", "corresponding" in blob, str(atoms))


def test_same_label_factors_remain_distinct() -> None:
    spoken = speak_steps(
        SAME_LABEL_FACTORS_STEPS,
        representative="DCF!Q80",
        evidence="=$M$71*$P$71",
        sheet="DCF",
        row_label="Product value",
    )
    atoms = must_say_atoms(spoken + "\n" + SAME_LABEL_FACTORS_STEPS, "DCF", "Product value")
    blob = " ".join(atoms).lower()
    _ok("factor-first-input", "first locked input" in spoken, spoken)
    _ok("factor-second-input", "second locked input" in spoken, spoken)
    _ok("factor-no-addresses", "M71" not in spoken and "P71" not in spoken, spoken)
    _ok("factor-requires-first", "first locked input" in blob, str(atoms))
    _ok("factor-requires-second", "second locked input" in blob, str(atoms))


def test_pasted_spoken_fails() -> None:
    spoken = speak_steps(
        RCF_STEPS,
        representative="LBO!O151",
        evidence=RCF_EVIDENCE,
        sheet="LBO",
        row_label="Revolving Credit Facility (L+4.50%)",
    )
    claims = {
        "schema_version": "1.1",
        "junior_titles": ["Analyst", "Associate"],
        "senior_titles": ["VP", "Director", "Managing Director"],
        "empty": False,
        "claims": [
            {
                "record_id": "method_debt_movement::LBO::RCF::001",
                "sheet": "LBO",
                "row_label": "Revolving Credit Facility (L+4.50%)",
                "must_say": must_say_atoms(
                    spoken, "LBO", "Revolving Credit Facility (L+4.50%)"
                ),
                "spoken": spoken,
            }
        ],
    }
    pasted = (
        "<!-- claim:method_debt_movement::LBO::RCF::001 -->\n"
        "**Associate:** Revolver?\n"
        f"**VP:** {spoken}\n"
    )
    report = check_draft(pasted, claims, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok(
        "pasted-spoken-blocked",
        "copied-across-forecast" in blob or "near-copy of spoken" in blob,
        blob,
    )

    paraphrased = (
        "<!-- claim:method_debt_movement::LBO::RCF::001 -->\n"
        "**Associate:** Revolver?\n"
        "**VP:** On LBO the revolver paydown each period is the smaller of "
        'Total Cash for Discretionary Debt Paydown and last period\'s '
        '"Revolving Credit Facility (L+4.50%)" minus this period\'s '
        '"Revolving Credit Facility (L+4.50%)". Floor that at zero, flip the '
        "sign, and times the locked input on that RCF row.\n"
    )
    report = check_draft(paraphrased, claims, TASK)
    _ok("paraphrased-rcf-passes", report["passed"], str(report["accuracy_faults"]))


def test_sheet_only_when_unclear() -> None:
    named = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** On Operations, walk me through Fee.\n"
        "**Director:** The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(named, CLAIMS, TASK)
    _ok("sheet-already-in-question", report["passed"], str(report["accuracy_faults"]))

    leadin = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** On Operations, walk me through Fee.\n"
        "**Director:** On Operations, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(leadin, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("redundant-sheet-leadin", "sheet lead-in" in blob, blob)

    missing = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Walk me through Fee.\n"
        "**Director:** The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(missing, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("sheet-required-when-unclear", "missing sheet" in blob, blob)


def test_paren_ast_draft_fails() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Fee?\n"
        "**VP:** On Operations, use this copied-column calculation, : "
        "multiply (take the negative of (the row labelled \"Fee\")) "
        'by the row labelled "Revenue".\n'
    )
    report = check_draft(text, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("ast-dump-blocked", "paren-AST" in blob or "copied-column" in blob, blob)


def test_rebuild_frame_blocked() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** What's the status on the rebuild?\n"
        "**VP:** On Operations, restore the original logic: "
        'The row labelled "Fee" is worked out from the row labelled "Revenue".\n'
    )
    report = check_draft(text, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("rebuild-blocked", "rebuild/restore" in blob, blob)


def test_senior_cell_refs() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Fee?\n"
        "**VP:** On Operations J15, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    _ok("senior-cell", bool(report["senior_cell_refs"]), str(report))


def main() -> int:
    global TASK
    with tempfile.TemporaryDirectory() as raw:
        tmp_path = Path(raw)
        TASK = tmp_path / "dialogue-check-outputs"
        (TASK / "tests").mkdir(parents=True)
        (TASK / "tests" / "answer_key.json").write_text(
            '{"targets": {}, "tolerance": {}}\n',
            encoding="utf-8",
        )
        test_senior_kickoff_unclaimed()
        test_associate_junior_and_ack()
        test_unknown_and_legacy_titles()
        test_marker_coverage()
        test_review_coverage()
        test_stale_pack()
        test_senior_cell_refs()
        test_rebuild_frame_blocked()
        test_rcf_spoken_disambiguated()
        test_rcf_must_say_atoms()
        test_bounded_window_preserves_both_conditions_and_branches()
        test_iferror_literal_is_not_a_row_label()
        test_same_label_locked_inputs_keep_distinct_roles()
        test_locked_ranges_preserve_cardinality_and_operand_identity()
        test_same_label_factors_remain_distinct()
        test_pasted_spoken_fails()
        test_sheet_only_when_unclear()
        test_paren_ast_draft_fails()
        test_formula_literal_target_collision_is_claim_scoped(tmp_path)
        test_apply_blocks_missing_must_say(tmp_path)
    print("all regressions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
