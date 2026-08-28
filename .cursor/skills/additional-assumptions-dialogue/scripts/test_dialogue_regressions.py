#!/usr/bin/env python3
"""Fixture checks for the additional-assumptions dialogue skill."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from aa_lib import parse_turns, require_cast
from compose_draft import compose, strip_slot_scaffold
from extract_claims import DRAFT_FORMAT_RULES, draft_skeleton, must_say_atoms
from spoken_formula import speak_steps
from validate_dialogue import (
    DialogueError,
    apply,
    check_draft,
    fill_faults,
    map_turns_to_claims,
    review_accuracy_faults,
    review_accuracy_passed,
    review_faults,
    review_passed,
    whole_axis_refs,
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


# Self-contained fixture task dir: check_draft only needs tests/answer_key.json
# (for the graded-target collision audit) and an environment/ directory.
_FIXTURE = tempfile.TemporaryDirectory(prefix="aa-dialogue-fixture-")
TASK = Path(_FIXTURE.name) / "0042-outputs"
(TASK / "tests").mkdir(parents=True)
(TASK / "environment").mkdir()
(TASK / "tests" / "answer_key.json").write_text(
    json.dumps({"targets": {}}), encoding="utf-8"
)

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


_ASSERTIONS = 0


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise SystemExit(f"FAIL {name}: {detail}")
    _ASSERTIONS += 1
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


def test_stale_pack() -> None:
    try:
        require_cast({"senior_title": "Senior banker", "claims": []})
    except ValueError:
        _ok("stale-pack", True)
    else:
        _ok("stale-pack", False, "did not raise")


def _build_apply_fixture(tmp: Path) -> tuple[Path, Path]:
    task = tmp / "0042-outputs"
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
    draft = tmp / "draft.md"
    draft.write_text(
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Fee?\n**VP:** On Operations I will get back to you.\n",
        encoding="utf-8",
    )
    return task, draft


def test_apply_blocks_missing_must_say(tmp: Path) -> None:
    task, draft = _build_apply_fixture(tmp)
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


def test_apply_error_names_absent_must_say_field(tmp: Path) -> None:
    # A 0532-shaped review (claim entries without must_say) must still block
    # apply, and the error must name the missing field and schema fragment.
    task, draft = _build_apply_fixture(tmp)
    review = _good_review()
    review["accuracy"]["claims"] = [
        {"record_id": "projection_rule::Operations::Fee", "verdict": "pass", "findings": []}
    ]
    try:
        apply(task, draft, CLAIMS, review, require_review_pass=False, skip_smoke=True)
    except DialogueError as exc:
        message = str(exc)
        _ok("apply-schema-error-blocks", "reviewer accuracy did not pass" in message, message)
        _ok(
            "apply-schema-error-names-field",
            'missing the required "must_say" field' in message,
            message,
        )
        _ok(
            "apply-schema-error-shows-fragment",
            "entailed|missing|contradicted" in message,
            message,
        )
    else:
        _ok("apply-schema-error-blocks", False, "apply did not raise")


def _good_review() -> dict:
    return {
        "agent_model": "gpt-5.6-sol-high",
        "round": 1,
        "passed": True,
        "accuracy": {
            "verdict": "pass",
            "claims": [
                {"record_id": "projection_rule::Operations::Fee", "must_say": "entailed"}
            ],
            "extras": [],
            "cell_refs_in_senior_turns": [],
        },
        "naturalness": {"verdict": "pass", "findings": []},
    }


def test_review_missing_must_say_field() -> None:
    # Observed in workbook 0532: claim entries carried verdict/findings but no
    # must_say verdict string.
    review = _good_review()
    review["accuracy"]["claims"] = [
        {
            "record_id": "projection_rule::Operations::Fee",
            "verdict": "pass",
            "findings": [],
        }
    ]
    _ok("missing-must-say-still-fails", not review_accuracy_passed(review, CLAIMS))
    faults = review_accuracy_faults(review, CLAIMS)
    blob = " ".join(faults)
    _ok("missing-must-say-names-field", 'missing the required "must_say" field' in blob, blob)
    _ok("missing-must-say-shows-schema", "entailed|missing|contradicted" in blob, blob)
    _ok("missing-must-say-names-record", "projection_rule::Operations::Fee" in blob, blob)


def test_review_boolean_must_say() -> None:
    # Observed in workbook 0531: must_say was the boolean true.
    review = _good_review()
    review["accuracy"]["claims"] = [
        {"record_id": "projection_rule::Operations::Fee", "must_say": True}
    ]
    _ok("bool-must-say-still-fails", not review_accuracy_passed(review, CLAIMS))
    blob = " ".join(review_accuracy_faults(review, CLAIMS))
    _ok("bool-must-say-shows-found", "True" in blob, blob)
    _ok(
        "bool-must-say-shows-allowed",
        '"entailed"|"missing"|"contradicted"' in blob and "boolean" in blob.lower(),
        blob,
    )


def test_review_claim_coverage_shape() -> None:
    # Observed in workbook 0514: top-level claim_coverage instead of accuracy.
    review = {
        "agent_model": "gpt-5.6-sol-high",
        "round": 2,
        "claim_coverage": [
            {
                "record_id": "projection_rule::Operations::Fee",
                "verdict": "pass",
                "must_say": [{"text": "the row labelled \"Fee\"", "status": "entailed"}],
            }
        ],
        "passed": True,
    }
    _ok("claim-coverage-still-fails", not review_accuracy_passed(review, CLAIMS))
    blob = " ".join(review_accuracy_faults(review, CLAIMS))
    _ok("claim-coverage-names-accuracy", 'missing the "accuracy" object' in blob, blob)
    _ok("claim-coverage-shows-fragment", '"accuracy":' in blob, blob)


def test_review_ordered_claims_shape() -> None:
    # Observed in workbook 0594: accuracy carried ordered_claims / must_say
    # arrays instead of the claims array.
    review = _good_review()
    review["accuracy"] = {
        "verdict": "fail",
        "ordered_claims": ["projection_rule::Operations::Fee"],
        "must_say": [
            {"record_id": "projection_rule::Operations::Fee", "verdict": "fail", "missing": []}
        ],
        "extras": [],
        "cell_refs_in_senior_turns": [],
    }
    review["passed"] = False
    _ok("ordered-claims-still-fails", not review_accuracy_passed(review, CLAIMS))
    blob = " ".join(review_accuracy_faults(review, CLAIMS))
    _ok("ordered-claims-names-claims", '"claims" array' in blob, blob)


def test_review_faults_empty_on_compliant() -> None:
    review = _good_review()
    _ok("compliant-accuracy-faults-empty", review_accuracy_faults(review, CLAIMS) == [])
    _ok("compliant-round1-faults-empty", review_faults(review, CLAIMS) == [])
    _ok("compliant-still-passes", review_passed(review, CLAIMS))


def test_draft_numbered_indented_turns() -> None:
    # Observed in workbook 0531 round 1: numbered list items with indented
    # speaker lines parse to zero turns.
    text = (
        "1. <!-- Claim: Fee treatment -->\n"
        "   **Analyst:** How was Fee carried?\n"
        "   **VP:** The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    _ok("indented-draft-fails", not report["passed"])
    blob = " ".join(report["faults"])
    _ok("indented-draft-no-turns", "dialogue has no speaker turns" in blob, blob)
    _ok("indented-draft-format-hint", "column 0" in blob and "**Title:**" in blob, blob)


def test_draft_capitalized_claim_comment() -> None:
    # Observed in workbook 0531 round 2: '<!-- Claim: human title -->' comments
    # are not claim comments.
    text = (
        "<!-- Claim: Fee treatment -->\n"
        "**Analyst:** How was Fee carried?\n"
        "**VP:** On Operations, the row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    _ok("capitalized-comment-fails", not report["passed"])
    blob = " ".join(report["accuracy_faults"])
    _ok("capitalized-comment-detected", "no claim comments" in blob, blob)
    _ok(
        "capitalized-comment-shows-expected",
        "<!-- claim:RECORD_ID -->" in blob and "lowercase" in blob,
        blob,
    )


def test_no_senior_turn_message_names_titles() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Analyst:** How was Fee carried?\n"
        "**Associate:** Not sure either.\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("no-senior-turn-still-fails", "no senior turn" in blob, blob)
    _ok("no-senior-turn-names-titles", "**Managing Director:**" in blob, blob)


def test_writer_pack_skeleton() -> None:
    skeleton = draft_skeleton(
        CLAIMS["claims"], ["Analyst", "Associate"], ["VP", "Director", "Managing Director"]
    )
    _ok(
        "skeleton-has-claim-comment",
        "<!-- claim:projection_rule::Operations::Fee -->" in skeleton,
        skeleton,
    )
    _ok("skeleton-has-junior-turn", "**Analyst:**" in skeleton, skeleton)
    _ok("skeleton-has-senior-turn", "**VP:**" in skeleton, skeleton)
    _ok("skeleton-ends-newline", skeleton.endswith("\n"), skeleton[-10:])
    _ok("format-rules-cover-column0", any("column 0" in rule for rule in DRAFT_FORMAT_RULES))
    _ok(
        "format-rules-cover-comment",
        any("<!-- claim:RECORD_ID -->" in rule for rule in DRAFT_FORMAT_RULES),
    )


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


def test_check_review_cli_reports_missing_field(tmp: Path) -> None:
    import subprocess
    import validate_dialogue

    claims_path = tmp / "claims.json"
    claims_path.write_text(json.dumps(CLAIMS), encoding="utf-8")
    review = _good_review()
    review["accuracy"]["claims"] = [
        {"record_id": "projection_rule::Operations::Fee", "verdict": "pass"}
    ]
    review_path = tmp / "review.r1.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    report_path = tmp / "review-check.r1.json"
    proc = subprocess.run(
        [
            sys.executable,
            validate_dialogue.__file__,
            "check-review",
            "--claims",
            str(claims_path),
            "--review",
            str(review_path),
            "--round",
            "1",
            "--report",
            str(report_path),
        ],
        capture_output=True,
        text=True,
    )
    _ok("check-review-exits-2", proc.returncode == 2, proc.stdout + proc.stderr)
    _ok("check-review-prints-fail", proc.stdout.startswith("FAIL"), proc.stdout)
    _ok('check-review-names-must-say', '"must_say"' in proc.stdout, proc.stdout)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    _ok("check-review-report-fails", report["passed"] is False)
    _ok("check-review-report-schema", "agent_model" in report["schema"], report["schema"])

    good_path = tmp / "review.good.json"
    good_path.write_text(json.dumps(_good_review()), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            validate_dialogue.__file__,
            "check-review",
            "--claims",
            str(claims_path),
            "--review",
            str(good_path),
            "--round",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    _ok("check-review-good-passes", proc.returncode == 0, proc.stdout + proc.stderr)


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


COMPOSE_CLAIMS = {
    "schema_version": "1.1",
    "junior_titles": ["Analyst", "Associate"],
    "senior_titles": ["VP", "Director", "Managing Director"],
    "empty": False,
    "claims": [
        {
            "record_id": "method_exit::CalcA::Exit EV::001",
            "sheet": "CalcA",
            "row_label": "Exit EV",
            "must_say": ['the row labelled "Exit EV"', "last period"],
        },
        {
            "record_id": "method_exit::CalcA::Interests::002",
            "sheet": "CalcA",
            "row_label": "Interests",
            "must_say": ['the row labelled "Interests"', "locked input"],
        },
        {
            "record_id": "projection::Summary::Fee::003",
            "sheet": "Summary",
            "row_label": "Fee",
            "must_say": ['the row labelled "Fee"'],
        },
    ],
}
COMPOSE_PACK = {
    "claims": [{"record_id": claim["record_id"]} for claim in COMPOSE_CLAIMS["claims"]]
}
_FILL_PROSE = {
    "c001-junior": 'How should I set the row labelled "Exit EV"?',
    "c001-senior": 'Hold the row labelled "Exit EV" at last period\'s value.',
    "c002-junior": 'And the row labelled "Interests"?',
    "c002-senior": 'The row labelled "Interests" takes the locked input each month.',
    "c003-junior": "Where does the Fee number come from?",
    "c003-senior": 'The row labelled "Fee" is a straight pull, nothing fancy.',
}


def _fill_template(template: str, prose: dict[str, str] | None = None) -> str:
    out = template
    for slot_id, text in (prose or _FILL_PROSE).items():
        out = out.replace("{{SLOT:%s}}" % slot_id, text)
    return out


def test_compose_draft_structure() -> None:
    template, manifest = compose(COMPOSE_CLAIMS, COMPOSE_PACK)
    _ok(
        "compose-claim-comments-exact",
        all(
            "<!-- claim:%s -->" % claim["record_id"] in template
            for claim in COMPOSE_CLAIMS["claims"]
        ),
        template,
    )
    _ok(
        "compose-claims-in-order",
        template.index("::001 -->") < template.index("::002 -->") < template.index("::003 -->"),
    )
    _ok(
        "compose-first-mention-leadin",
        "**VP:** This one lives on the CalcA tab." in template,
        template,
    )
    _ok(
        "compose-calca-leadin-once",
        template.count("lives on the CalcA tab") == 1,
        template,
    )
    _ok("compose-second-claim-bare-senior", "\n**Director:**\n" in template, template)
    _ok(
        "compose-summary-leadin",
        "**Managing Director:** This one lives on the Summary tab." in template,
        template,
    )
    _ok(
        "compose-slot-lines",
        "{{SLOT:c001-junior}}" in template and "{{SLOT:c003-senior}}" in template,
        template,
    )
    senior1 = manifest["slots"]["c001-senior"]
    _ok(
        "compose-slot-must-say",
        senior1["must_say"] == ['the row labelled "Exit EV"', "last period"],
        str(senior1),
    )
    _ok(
        "compose-slot-claim-ids",
        senior1["claim_ids"] == ["method_exit::CalcA::Exit EV::001"],
        str(senior1),
    )
    _ok(
        "compose-slot-sheet-context",
        senior1["sheet_context"] == "introduced_by_template"
        and manifest["slots"]["c002-senior"]["sheet_context"] == "already_established",
        str(manifest["slots"]["c002-senior"]),
    )
    lines = template.split("\n")
    slot_idx = lines.index("{{SLOT:c001-senior}}")
    comment = lines[slot_idx - 2]
    _ok("compose-facts-comment-adjacent", comment.startswith("<!-- slot:c001-senior"), comment)
    _ok(
        "compose-facts-comment-carries-must-say",
        "last period" in comment and 'the row labelled "Exit EV"' in comment,
        comment,
    )
    _ok("compose-ends-single-newline", template.endswith("}}\n"), repr(template[-30:]))


def test_fill_check_valid_and_rejects() -> None:
    template, _ = compose(COMPOSE_CLAIMS, COMPOSE_PACK)
    filled = _fill_template(template)
    _ok("fill-valid-structure", fill_faults(template, filled) == [], str(fill_faults(template, filled)))
    cleaned = strip_slot_scaffold(filled)
    _ok("fill-cleaned-no-slot-comments", "<!-- slot:" not in cleaned, cleaned)
    _ok(
        "fill-cleaned-keeps-claim-comments",
        "<!-- claim:method_exit::CalcA::Exit EV::001 -->" in cleaned,
        cleaned,
    )
    report = check_draft(cleaned, COMPOSE_CLAIMS, TASK)
    _ok("fill-valid-passes-check-draft", report["passed"], str(report["faults"]))

    added = filled.replace(
        "<!-- claim:method_exit::CalcA::Interests::002 -->",
        "## Notes\n<!-- claim:method_exit::CalcA::Interests::002 -->",
    )
    faults = fill_faults(template, added)
    _ok(
        "fill-added-heading-rejected",
        faults and "structural drift" in faults[0] and "## Notes" in faults[0],
        str(faults),
    )
    _ok("fill-added-heading-names-line", "draft line" in faults[0], str(faults))

    removed = filled.replace("<!-- claim:method_exit::CalcA::Interests::002 -->\n", "")
    faults = fill_faults(template, removed)
    _ok(
        "fill-removed-comment-rejected",
        faults and "claim:method_exit::CalcA::Interests::002" in faults[0],
        str(faults),
    )

    edited = filled.replace(
        "**VP:** This one lives on the CalcA tab.",
        "**VP:** On CalcA, here you go.",
    )
    faults = fill_faults(template, edited)
    _ok(
        "fill-edited-leadin-rejected",
        faults and "This one lives on the CalcA tab." in faults[0],
        str(faults),
    )

    faults = fill_faults(template, template)
    _ok(
        "fill-leftover-placeholder-rejected",
        faults and "{{SLOT:" in faults[0],
        str(faults),
    )

    turned = filled.replace(
        'Hold the row labelled "Exit EV" at last period\'s value.',
        "**Intern:** Hold it at last period's value.",
    )
    faults = fill_faults(template, turned)
    _ok(
        "fill-new-speaker-line-rejected",
        faults and "structural line" in faults[0],
        str(faults),
    )


def test_whole_column_and_row_refs() -> None:
    hits = whole_axis_refs("Sum 'Op Loan 1'!$D:$D wherever CalcA!V:V matches, plus 3:3.")
    _ok("axis-quoted-sheet-column", any(h.endswith("'Op Loan 1'!$D:$D") for h in hits), str(hits))
    _ok("axis-bare-sheet-column", any(h.endswith("CalcA!V:V") for h in hits), str(hits))
    _ok("axis-whole-row", "3:3" in hits, str(hits))
    _ok("axis-dollar-column", any(h.endswith("$V:$V") for h in whole_axis_refs("check $V:$V there")))
    _ok(
        "axis-ignores-cells-and-ranges",
        whole_axis_refs("CalcA!V12 and B7:S7 and a 9am start look fine") == [],
        str(whole_axis_refs("CalcA!V12 and B7:S7 and a 9am start look fine")),
    )

    flagged = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** On Operations, how do I pull Fee?\n"
        "**Director:** Sum CalcA!V:V where it matches — the row labelled \"Fee\" "
        "is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(flagged, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("axis-check-draft-fires", "whole-column" in blob and "CalcA!V:V" in blob, blob)
    _ok("axis-check-draft-expected-format", "row label" in blob and "$V:$V" in blob, blob)
    _ok("axis-report-lists-refs", any("CalcA!V:V" in h for h in report["senior_axis_refs"]), str(report))

    plain_cell = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** On Operations, how do I pull Fee?\n"
        "**Director:** Check CalcA!V12 — the row labelled \"Fee\" "
        "is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(plain_cell, CLAIMS, TASK)
    blob = " ".join(report["accuracy_faults"])
    _ok("axis-not-fired-on-cell-ref", "whole-column" not in blob, blob)
    _ok("axis-cell-ref-still-caught", bool(report["senior_cell_refs"]), str(report))


def test_compose_and_fill_check_cli(tmp: Path) -> None:
    import subprocess
    import compose_draft
    import validate_dialogue

    claims_path = tmp / "claims.json"
    claims_path.write_text(json.dumps(COMPOSE_CLAIMS), encoding="utf-8")
    pack_path = tmp / "writer_pack.json"
    pack_path.write_text(json.dumps(COMPOSE_PACK), encoding="utf-8")
    run = tmp / "run"
    proc = subprocess.run(
        [
            sys.executable, compose_draft.__file__,
            "--claims", str(claims_path), "--pack", str(pack_path), "--out", str(run),
        ],
        capture_output=True,
        text=True,
    )
    _ok("compose-cli-exit-0", proc.returncode == 0, proc.stdout + proc.stderr)
    template_path = run / "draft_template.md"
    _ok("compose-cli-writes-both", template_path.is_file() and (run / "slots.json").is_file())
    slots = json.loads((run / "slots.json").read_text(encoding="utf-8"))
    _ok("compose-cli-slots-schema", "slots" in slots and "slot_order" in slots, str(slots)[:200])

    task = tmp / "0042-outputs"
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "tests" / "answer_key.json").write_text(json.dumps({"targets": {}}), encoding="utf-8")

    template = template_path.read_text(encoding="utf-8")
    filled_path = run / "draft.filled.md"
    filled_path.write_text(_fill_template(template), encoding="utf-8")
    out_path = run / "draft.md"

    def run_fill_check(draft_path: Path) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [
                sys.executable, validate_dialogue.__file__, "fill-check",
                "--task-dir", str(task), "--template", str(template_path),
                "--draft", str(draft_path), "--claims", str(claims_path),
                "--out", str(out_path), "--report", str(run / "fill-check.json"),
            ],
            capture_output=True,
            text=True,
        )

    proc = run_fill_check(filled_path)
    _ok("fill-check-cli-pass", proc.returncode == 0, proc.stdout + proc.stderr)
    _ok(
        "fill-check-cli-out-clean",
        out_path.is_file() and "<!-- slot:" not in out_path.read_text(encoding="utf-8"),
    )

    drifted_path = run / "draft.drifted.md"
    drifted_path.write_text(
        _fill_template(template).replace("**Associate:**", "**Intern:**", 1),
        encoding="utf-8",
    )
    proc = run_fill_check(drifted_path)
    _ok("fill-check-cli-drift-exit-2", proc.returncode == 2, proc.stdout + proc.stderr)
    _ok("fill-check-cli-drift-names-line", "draft line" in proc.stdout, proc.stdout)

    weak_prose = dict(_FILL_PROSE)
    weak_prose["c001-senior"] = 'Hold the row labelled "Exit EV" flat.'  # drops "last period"
    weak_path = run / "draft.weak.md"
    weak_path.write_text(_fill_template(template, weak_prose), encoding="utf-8")
    proc = run_fill_check(weak_path)
    _ok("fill-check-cli-content-exit-3", proc.returncode == 3, proc.stdout + proc.stderr)
    _ok("fill-check-cli-content-names-must-say", "must_say" in proc.stdout, proc.stdout)


def test_senior_cell_refs() -> None:
    text = (
        "<!-- claim:projection_rule::Operations::Fee -->\n"
        "**Associate:** Fee?\n"
        "**VP:** On Operations J15, The row labelled \"Fee\" is worked out from the row labelled \"Revenue\".\n"
    )
    report = check_draft(text, CLAIMS, TASK)
    _ok("senior-cell", bool(report["senior_cell_refs"]), str(report))


def main() -> int:
    test_senior_kickoff_unclaimed()
    test_associate_junior_and_ack()
    test_unknown_and_legacy_titles()
    test_marker_coverage()
    test_review_coverage()
    test_stale_pack()
    test_review_missing_must_say_field()
    test_review_boolean_must_say()
    test_review_claim_coverage_shape()
    test_review_ordered_claims_shape()
    test_review_faults_empty_on_compliant()
    test_draft_numbered_indented_turns()
    test_draft_capitalized_claim_comment()
    test_no_senior_turn_message_names_titles()
    test_writer_pack_skeleton()
    test_senior_cell_refs()
    test_rebuild_frame_blocked()
    test_rcf_spoken_disambiguated()
    test_rcf_must_say_atoms()
    test_pasted_spoken_fails()
    test_sheet_only_when_unclear()
    test_paren_ast_draft_fails()
    test_compose_draft_structure()
    test_fill_check_valid_and_rejects()
    test_whole_column_and_row_refs()
    with tempfile.TemporaryDirectory() as raw:
        test_apply_blocks_missing_must_say(Path(raw))
    with tempfile.TemporaryDirectory() as raw:
        test_apply_error_names_absent_must_say_field(Path(raw))
    with tempfile.TemporaryDirectory() as raw:
        test_check_review_cli_reports_missing_field(Path(raw))
    with tempfile.TemporaryDirectory() as raw:
        test_compose_and_fill_check_cli(Path(raw))
    print("all regressions passed (%d assertions)" % _ASSERTIONS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
