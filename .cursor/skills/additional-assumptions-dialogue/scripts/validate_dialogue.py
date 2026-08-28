#!/usr/bin/env python3
"""Validate an additional-assumptions dialogue and apply it to a Harbor task."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from compose_draft import SLOT_LINE_RE, strip_slot_scaffold
from aa_lib import (
    ALLOWED_TITLES,
    APPLIED_MARKER,
    ASSUMPTIONS_HEADING,
    DISCLOSURE_HEADING,
    DO_NOT_RERUN,
    DOCKER_IMAGE_RE,
    HASH_RE,
    INPUT_HEADING,
    LEFTOVER_BULLET_RE,
    NOTES_COPY,
    NOTES_NAME,
    OUTPUT_HEADING,
    artifact_name,
    cell_refs_in,
    clause_covered,
    is_junior,
    is_senior,
    normalize,
    parse_turns,
    require_cast,
    section_body,
    sha256_text,
    split_sections,
    strip_cell_refs,
    strip_claim_comments,
    strip_heading_block,
    write_json,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
DISCLOSE_SCRIPTS = SKILL_ROOT.parent / "task-disclosure" / "scripts"
if str(DISCLOSE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DISCLOSE_SCRIPTS))

import disclose  # noqa: E402


INPUT_POINTER = (
    "Also in your working directory is `{notes}`. Read it before you build. "
    "It records a conversation between colleagues containing assumptions you "
    "need to follow."
)
ASSUMPTIONS_SECTION = (
    "{heading}\n\n"
    "Read `{notes}` in your working directory. It records a conversation "
    "between colleagues containing assumptions you need to follow. "
    "Use those assumptions when you build.\n"
)
LEFTOVER_TITLE_RE = re.compile(r"senior (?:banker|investor)", re.I)
REBUILD_FRAME_RE = re.compile(
    r"\b(?:re-?build(?:ing|s|t)?|re-?stor(?:e|es|ed|ing)|"
    r"original (?:model|logic))\b",
    re.I,
)
AST_DUMP_RE = re.compile(
    r"copied-column calculation|multiply \(take the",
    re.I,
)
COPIED_FORECAST_RE = re.compile(r"is copied across the forecast", re.I)
# Whole-column (A:A, $V:$V, 'Op Loan 1'!$D:$D) and whole-row (3:3, Sheet!3:5)
# references. Cell/range tokens with row digits (V12, J15:S15) are handled by
# cell_refs_in; these regexes only match digit-less column pairs and bare
# row-number pairs, optionally sheet-qualified.
_SHEET_PREFIX = r"(?:(?:'[^']+'|[A-Za-z0-9_][A-Za-z0-9_ .&-]*)!)?"
WHOLE_COL_RE = re.compile(
    r"(?<![A-Za-z0-9$!:])" + _SHEET_PREFIX +
    r"\$?[A-Z]{1,3}:\$?[A-Z]{1,3}(?![A-Za-z0-9:(])"
)
WHOLE_ROW_RE = re.compile(
    r"(?<![A-Za-z0-9$!:.])" + _SHEET_PREFIX +
    r"\$?\d{1,7}:\$?\d{1,7}(?![A-Za-z0-9:])"
)


def whole_axis_refs(text: str) -> list[str]:
    hits = [match.group(0).strip() for match in WHOLE_COL_RE.finditer(text or "")]
    hits.extend(match.group(0).strip() for match in WHOLE_ROW_RE.finditer(text or ""))
    return hits


def sheet_named_in(text: str, sheet: str) -> bool:
    if not sheet or not text:
        return False
    return sheet.lower() in text.lower()


def sheet_leadin(text: str, sheet: str) -> bool:
    if not sheet or not text:
        return False
    cleaned = re.sub(r"^[\s*`]+", "", text.strip())
    return bool(
        re.match(
            r"^(?:on|for|in)\s+(?:the\s+)?[`']?%s[`']?(?:\s+tab)?\b" % re.escape(sheet),
            cleaned,
            re.I,
        )
    )


def spoken_is_pasted(spoken: str, senior_blob: str) -> bool:
    needle = normalize(spoken)
    blob = normalize(senior_blob)
    if not needle or len(needle) < 32:
        return False
    return needle in blob


class DialogueError(ValueError):
    pass


def load_claims(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require_cast(payload)
    return payload


def load_review(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def expected_record_ids(claims_payload: dict) -> list[str]:
    return [claim["record_id"] for claim in (claims_payload.get("claims") or [])]


MUST_SAY_VERDICTS = ("entailed", "missing", "contradicted")
CLAIM_ENTRY_FRAGMENT = (
    '{"record_id": "<record_id copied from claims.json>", '
    '"must_say": "entailed|missing|contradicted"}'
)
ACCURACY_FRAGMENT = (
    '"accuracy": {"verdict": "pass|fail", "claims": [%s, ...], '
    '"extras": [], "cell_refs_in_senior_turns": []}' % CLAIM_ENTRY_FRAGMENT
)
NATURALNESS_FRAGMENT = '"naturalness": {"verdict": "pass|fail", "findings": []}'
REVIEW_SCHEMA_TEMPLATE = (
    "{\n"
    '  "agent_model": "gpt-5.6-sol-high",\n'
    '  "round": <1 or 2>,\n'
    '  "accuracy": {\n'
    '    "verdict": "pass" or "fail",\n'
    '    "claims": [%s, ... one entry per claim, in claims.json order],\n'
    '    "extras": [],\n'
    '    "cell_refs_in_senior_turns": []\n'
    "  },\n"
    '  %s,\n'
    '  "passed": true or false\n'
    "}" % (CLAIM_ENTRY_FRAGMENT, NATURALNESS_FRAGMENT)
)


def review_accuracy_faults(review: dict | None, claims_payload: dict) -> list[str]:
    """Diagnose the accuracy gate. Empty list == review_accuracy_passed.

    Every fault names the offending/missing field and the schema fragment the
    reviewer was expected to produce, so a malformed review is fixable from
    the message alone. The conditions are exactly the historical gate: same
    things fail, they just fail with a diagnosis.
    """
    if not review:
        return [
            "no review JSON was loaded; expected an object with top-level keys "
            '"agent_model", "round", "accuracy", "naturalness", "passed"'
        ]
    faults: list[str] = []
    accuracy = review.get("accuracy")
    if not isinstance(accuracy, dict):
        faults.append(
            'review is missing the "accuracy" object (do not rename it or use '
            "claim_coverage/ordered_claims); expected fragment: %s" % ACCURACY_FRAGMENT
        )
        accuracy = {}
    rows = accuracy.get("claims") or []
    if not isinstance(rows, list):
        faults.append(
            'accuracy.claims is %r; expected an array of %s'
            % (type(rows).__name__, CLAIM_ENTRY_FRAGMENT)
        )
        rows = []
    elif "claims" not in accuracy and isinstance(review.get("accuracy"), dict):
        faults.append(
            'accuracy is missing the "claims" array; expected fragment: %s '
            "(per-claim lists named must_say/ordered_claims are not read)"
            % ACCURACY_FRAGMENT
        )
    expected = expected_record_ids(claims_payload)
    if not expected:
        faults.append("claims pack lists no claims; cannot cross-check review coverage")
    elif len(rows) != len(expected):
        faults.append(
            "accuracy.claims has %d entries; expected exactly %d — one %s per "
            "claim in claims.json order" % (len(rows), len(expected), CLAIM_ENTRY_FRAGMENT)
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            faults.append(
                "accuracy.claims[%d] is %r; expected an object %s"
                % (index, row, CLAIM_ENTRY_FRAGMENT)
            )
            continue
        if index < len(expected) and row.get("record_id") != expected[index]:
            faults.append(
                "accuracy.claims[%d].record_id is %r; expected %r (claims.json order)"
                % (index, row.get("record_id"), expected[index])
            )
        if "must_say" not in row:
            faults.append(
                'accuracy.claims[%d] (record_id %r) is missing the required '
                '"must_say" field; expected fragment: %s'
                % (index, row.get("record_id"), CLAIM_ENTRY_FRAGMENT)
            )
        elif row.get("must_say") != "entailed":
            verdict = row.get("must_say")
            if verdict in MUST_SAY_VERDICTS:
                faults.append(
                    "accuracy.claims[%d] (record_id %r) has must_say %r; every "
                    'claim must be "entailed" for accuracy to pass'
                    % (index, row.get("record_id"), verdict)
                )
            else:
                faults.append(
                    "accuracy.claims[%d] (record_id %r) has must_say %r; expected "
                    'one of the strings "entailed"|"missing"|"contradicted" '
                    "(booleans, lists, and objects are invalid)"
                    % (index, row.get("record_id"), verdict)
                )
    if accuracy.get("cell_refs_in_senior_turns"):
        faults.append(
            "accuracy.cell_refs_in_senior_turns is non-empty: %r; senior turns "
            "must not contain cell refs"
            % list(accuracy.get("cell_refs_in_senior_turns"))[:6]
        )
    return faults


def review_accuracy_passed(review: dict | None, claims_payload: dict) -> bool:
    return not review_accuracy_faults(review, claims_payload)


def review_faults(review: dict | None, claims_payload: dict) -> list[str]:
    """Diagnose the full round-1 gate. Empty list == review_passed."""
    if not review:
        return [
            "no review JSON was loaded; expected: %s" % REVIEW_SCHEMA_TEMPLATE
        ]
    faults = review_accuracy_faults(review, claims_payload)
    accuracy = review.get("accuracy") or {}
    if isinstance(accuracy, dict):
        if accuracy.get("verdict") != "pass":
            faults.append(
                'accuracy.verdict is %r; expected the string "pass" (fragment: '
                '"verdict": "pass|fail")' % accuracy.get("verdict")
            )
        extras = accuracy.get("extras") or []
        if extras:
            faults.append(
                "accuracy.extras is non-empty: %r; extras must be []" % extras[:4]
            )
    naturalness = review.get("naturalness")
    if not isinstance(naturalness, dict):
        faults.append(
            'review is missing the "naturalness" object; expected fragment: %s'
            % NATURALNESS_FRAGMENT
        )
    elif naturalness.get("verdict") != "pass":
        faults.append(
            'naturalness.verdict is %r; expected the string "pass" (fragment: %s)'
            % (naturalness.get("verdict"), NATURALNESS_FRAGMENT)
        )
    if review.get("passed") is not True:
        faults.append(
            '"passed" is %r; expected the JSON boolean true (only when both '
            "verdicts pass, every must_say is entailed, extras is empty, and "
            "cell_refs_in_senior_turns is empty)" % review.get("passed")
        )
    return faults


def review_passed(review: dict | None, claims_payload: dict | None = None) -> bool:
    if claims_payload is None:
        claims_payload = {}
    return not review_faults(review, claims_payload)


def map_turns_to_claims(turns: list[dict], claims: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    """Claim comments are authoritative. Turns before the first comment stay unclaimed."""
    mapped = {claim["record_id"]: [] for claim in claims}
    faults = []
    pending = None
    seen_comment = False
    for turn in turns:
        record_id = turn.get("record_id")
        if record_id:
            seen_comment = True
            if record_id not in mapped:
                faults.append(
                    "unknown claim comment: %s; expected RECORD_ID copied exactly "
                    "from claims.json, one of: %s"
                    % (record_id, ", ".join(sorted(mapped)[:4]) or "(none)")
                )
                pending = None
                continue
            mapped[record_id].append(turn)
            pending = record_id
            continue
        if seen_comment and pending:
            mapped[pending].append(turn)
    if claims and not seen_comment:
        faults.append(
            "no claim comments; mapping is authoritative — expected a literal "
            "<!-- claim:RECORD_ID --> line (lowercase 'claim:', RECORD_ID copied "
            "character-for-character from claims.json) immediately before each "
            "claim's first turn"
        )
    return mapped, faults


def licensed_formula_literals(claim: dict, task_dir: Path) -> Counter:
    """Return target-safe AST constants licensed for this one claim."""
    reviewer = claim.get("reviewer_only") or {}
    if reviewer.get("source") != "custom_method_detector":
        return Counter()
    targets, _ = disclose.load_key(task_dir)
    target_cells = {
        disclose.parse_ref(ref, "")
        for ref in targets
        if "!" in ref
    }
    cells = {
        disclose.parse_ref(ref, "")
        for ref in reviewer.get("cells") or []
        if "!" in ref
    }
    if cells & target_cells:
        return Counter()
    evidence = reviewer.get("evidence") or ""
    sheet = claim.get("sheet") or ""
    if set(disclose.refs_in(evidence, sheet)) & target_cells:
        return Counter()
    try:
        if str(disclose.REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(disclose.REPO_ROOT))
        from xl_ast_graph import parse_formula  # type: ignore

        ast = parse_formula(evidence)
    except Exception:
        return Counter()
    allowed: Counter = Counter()
    for node in disclose.walk_ast(ast):
        if node.kind != "const" or node.name != "number":
            continue
        try:
            allowed[float(node.value)] += 1
        except (TypeError, ValueError):
            continue
    return allowed


def numeric_faults_with_budget(
    text: str, task_dir: Path, allowed: Counter | None = None
) -> list[str]:
    """Audit target collisions while consuming only proven literal occurrences."""
    budget = Counter(allowed or {})
    faults = []
    for fault in disclose.audit_text(text, task_dir):
        match = re.fullmatch(r"numeric literal ([^ ]+) matches target .+", fault)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            faults.append(fault)
            continue
        permitted = next(
            (
                number
                for number, count in budget.items()
                if count and disclose.same_number(value, number)
            ),
            None,
        )
        if permitted is None:
            faults.append(fault)
        else:
            budget[permitted] -= 1
    return faults


def audit_dialogue(
    dialogue: str,
    claims: list[dict],
    turns: list[dict],
    mapped: dict[str, list[dict]],
    task_dir: Path,
) -> list[str]:
    """Audit prose globally, but numeric literals claim-by-claim."""
    cleaned = strip_cell_refs(strip_claim_comments(dialogue))
    faults = [
        fault
        for fault in disclose.audit_text(cleaned, task_dir)
        if not fault.startswith("numeric literal ")
    ]
    assigned_senior_turns: set[int] = set()
    for claim in claims:
        senior_turns = [
            turn
            for turn in mapped.get(claim["record_id"], [])
            if is_senior(turn["speaker"])
        ]
        assigned_senior_turns.update(id(turn) for turn in senior_turns)
        text = "\n".join(turn["text"] for turn in senior_turns)
        faults.extend(
            numeric_faults_with_budget(
                text, task_dir, licensed_formula_literals(claim, task_dir)
            )
        )
    # Juniors, kickoff turns, and any unmapped senior prose receive no budget.
    for turn in turns:
        if id(turn) not in assigned_senior_turns:
            faults.extend(numeric_faults_with_budget(turn["text"], task_dir))
    return faults


def check_draft(dialogue: str, claims_payload: dict, task_dir: Path) -> dict:
    faults = []
    accuracy_faults = []
    cast_faults = []
    claims = claims_payload.get("claims") or []
    require_cast(claims_payload)
    turns = parse_turns(dialogue)
    if not turns:
        no_turns = (
            "dialogue has no speaker turns; expected every turn to start at "
            "column 0 as **Title:** text (for example **Analyst:** How should I "
            "build this row?) — numbered, bulleted, or indented turns are not "
            "parsed"
        )
        faults.append(no_turns)
        accuracy_faults.append(no_turns)
        return {
            "passed": False,
            "faults": faults,
            "accuracy_faults": accuracy_faults,
            "cast_faults": cast_faults,
            "turns": 0,
            "senior_cell_refs": [],
            "senior_axis_refs": [],
        }

    for turn in turns:
        speaker = turn["speaker"]
        if speaker not in ALLOWED_TITLES:
            cast_faults.append(f"unexpected speaker title {speaker!r}")
    if cast_faults:
        faults.extend(cast_faults)

    senior_text = "\n".join(turn["text"] for turn in turns if is_senior(turn["speaker"]))
    # A visible row label may itself contain an A1-shaped product code
    # (0261 IS: 'Atrial Fibrillation Best (Chest Belt) (B031)'). The label
    # check requires the literal label in senior prose, so such tokens are
    # label text, not cell addresses, and must not count as leaks.
    label_tokens = {
        token.strip()
        for claim in claims
        for token in cell_refs_in(claim.get("row_label") or "")
    }
    leaks = [
        token for token in cell_refs_in(senior_text)
        if token.strip() not in label_tokens
    ]
    if leaks:
        faults.append("senior turns contain cell refs: %s" % ", ".join(leaks[:6]))
    # The same label-text exemption applies to whole-axis tokens: a visible
    # row label may itself be shaped like a column pair (0524 Multiples lists
    # comparables by ticker rows 'UK:BBY', 'ES:ANA'). The label check requires
    # those literal labels in senior prose, so they are label text, not
    # whole-column references, and must not count as leaks.
    axis_label_tokens = {
        token.strip()
        for claim in claims
        for token in whole_axis_refs(claim.get("row_label") or "")
    }
    # must_say atoms are mandatory prose; a ticker-shaped label the card
    # demands (the row labelled "UK:BBY") can never be a reportable leak.
    axis_label_tokens.update(
        token.strip()
        for claim in claims
        for atom in (claim.get("must_say") or [])
        for token in whole_axis_refs(atom or "")
    )
    axis_leaks = [
        token for token in whole_axis_refs(senior_text)
        if token.strip() not in axis_label_tokens
    ]
    if axis_leaks:
        accuracy_faults.append(
            "senior turns contain whole-column/whole-row references: %s; "
            "expected the tab name plus the visible row label (say: the row "
            'labelled "..." on that tab) — a senior may never say A:A, $V:$V, '
            "'Sheet'!D:D, or 3:3"
            % ", ".join(list(dict.fromkeys(axis_leaks))[:6])
        )

    mapped, map_faults = map_turns_to_claims(turns, claims)
    accuracy_faults.extend(map_faults)
    for claim in claims:
        senior_turns = [
            turn for turn in mapped.get(claim["record_id"], [])
            if is_senior(turn["speaker"])
        ]
        blob = "\n".join(turn["text"] for turn in senior_turns)
        if not senior_turns:
            accuracy_faults.append(
                f"{claim['record_id']}: no senior turn; expected at least one "
                "**VP:** / **Director:** / **Managing Director:** turn between "
                "this claim's <!-- claim:... --> comment and the next"
            )
            continue
        juniors = [
            turn for turn in mapped.get(claim["record_id"], [])
            if is_junior(turn["speaker"])
        ]
        first = (mapped.get(claim["record_id"]) or [None])[0]
        prior = []
        for turn in turns:
            if first is not None and turn is first:
                break
            prior.append(turn)
        context = "\n".join(turn["text"] for turn in prior + juniors)
        sheet = claim.get("sheet") or ""
        sheet_known = sheet_named_in(context, sheet)
        if sheet and not sheet_known and not sheet_named_in(blob, sheet):
            accuracy_faults.append(
                f"{claim['record_id']}: senior turn missing sheet {sheet!r}"
            )
        if sheet and sheet_known:
            for turn in senior_turns:
                if sheet_leadin(turn["text"], sheet):
                    accuracy_faults.append(
                        f"{claim['record_id']}: senior repeats sheet lead-in "
                        f"{sheet!r} already in context"
                    )
                    break
        if claim["row_label"] and claim["row_label"].lower() not in blob.lower():
            accuracy_faults.append(
                f"{claim['record_id']}: senior turn missing row label {claim['row_label']!r}"
            )
        for clause in claim.get("must_say") or []:
            if not clause_covered(clause, blob):
                accuracy_faults.append(
                    f"{claim['record_id']}: must_say not covered: {clause[:80]}"
                )
        if COPIED_FORECAST_RE.search(blob):
            accuracy_faults.append(
                f"{claim['record_id']}: senior turn uses copied-across-forecast template"
            )
        if spoken_is_pasted(claim.get("spoken") or "", blob):
            accuracy_faults.append(
                f"{claim['record_id']}: senior turn is a near-copy of spoken"
            )

    faults.extend(accuracy_faults)
    faults.extend(audit_dialogue(dialogue, claims, turns, mapped, task_dir))
    if DISCLOSURE_HEADING.lower() in dialogue.lower():
        faults.append("dialogue uses the word disclosure")
    rebuild_hits = sorted({m.group(0) for m in REBUILD_FRAME_RE.finditer(dialogue)})
    if rebuild_hits:
        accuracy_faults.append(
            "dialogue frames the work as a rebuild/restore: %s" % ", ".join(rebuild_hits[:6])
        )
        faults.append(accuracy_faults[-1])
    ast_hits = sorted({m.group(0) for m in AST_DUMP_RE.finditer(dialogue)})
    if ast_hits:
        accuracy_faults.append(
            "dialogue dumps copied-column / paren-AST English: %s" % ", ".join(ast_hits[:4])
        )
        faults.append(accuracy_faults[-1])
    return {
        "passed": not faults,
        "faults": faults,
        "accuracy_faults": accuracy_faults,
        "cast_faults": list(dict.fromkeys(cast_faults)),
        "turns": len(turns),
        "senior_cell_refs": leaks,
        "senior_axis_refs": axis_leaks,
    }


STRUCTURAL_FILL_RE = re.compile(
    r"^(?:<!--|#{1,6}\s|\*\*[^*\n]{1,60}:\*\*|"
    r"(?:Analyst|Associate|VP|Director|Managing Director)\s*:)"
)


def fill_faults(template: str, filled: str) -> list[str]:
    """Byte-compare the writer-filled draft against the template outside slots.

    Returns at most one fault: a precise error naming the first differing
    line. Slots ({{SLOT:<id>}} lines) may be replaced by one or more
    non-blank prose lines; every other line must match byte-for-byte.
    """
    t_lines = template.split("\n")
    f_lines = filled.split("\n")
    fi = 0
    ti = 0
    while ti < len(t_lines):
        t_line = t_lines[ti]
        slot = SLOT_LINE_RE.match(t_line)
        if not slot:
            if fi >= len(f_lines):
                return [
                    "structural drift: the draft ended before template line %d; "
                    "expected %r" % (ti + 1, t_line)
                ]
            if f_lines[fi] != t_line:
                return [
                    "structural drift at draft line %d: expected %r (template "
                    "line %d), found %r" % (fi + 1, t_line, ti + 1, f_lines[fi])
                ]
            fi += 1
            ti += 1
            continue
        slot_id = slot.group(1)
        boundary = t_lines[ti + 1] if ti + 1 < len(t_lines) else None
        fill: list[tuple[int, str]] = []
        while fi < len(f_lines) and (boundary is None or f_lines[fi] != boundary):
            fill.append((fi, f_lines[fi]))
            fi += 1
        if not [line for _, line in fill if line.strip()]:
            return [
                "slot %s was deleted or left empty; replace the line "
                "{{SLOT:%s}} (template line %d) with one or more prose lines"
                % (slot_id, slot_id, ti + 1)
            ]
        for lineno, line in fill:
            if "{{SLOT:" in line:
                return [
                    "draft line %d still contains the placeholder %r; replace "
                    "the whole slot line with prose" % (lineno + 1, line)
                ]
            if STRUCTURAL_FILL_RE.match(line):
                return [
                    "draft line %d: slot %s fill adds a structural line %r; "
                    "slots may only be replaced with prose (no new headings, "
                    "speaker lines, or HTML comments)" % (lineno + 1, slot_id, line)
                ]
        ti += 1
    if fi < len(f_lines) and any(line.strip() for line in f_lines[fi:]):
        return [
            "structural drift at draft line %d: content after the template "
            "end: %r" % (fi + 1, f_lines[fi])
        ]
    return []


def expected_pointer(task_dir: Path, claims_payload: dict) -> tuple[str, str]:
    input_pointer = INPUT_POINTER.format(notes=NOTES_NAME)
    assumptions = ASSUMPTIONS_SECTION.format(heading=ASSUMPTIONS_HEADING, notes=NOTES_NAME)
    return input_pointer, assumptions


def strip_input_pointer(input_body: str) -> str:
    lines = []
    skip_blank = False
    for line in input_body.splitlines(keepends=True):
        if NOTES_NAME in line and "working directory" in line.lower():
            skip_blank = True
            continue
        if skip_blank and not line.strip():
            skip_blank = False
            continue
        skip_blank = False
        lines.append(line)
    return "".join(lines)


def rewrite_instruction(instruction: str, task_dir: Path, claims_payload: dict) -> str:
    input_pointer, assumptions = expected_pointer(task_dir, claims_payload)
    text = strip_heading_block(instruction, DISCLOSURE_HEADING)
    text = strip_heading_block(text, ASSUMPTIONS_HEADING)
    preamble, sections = split_sections(text)
    rebuilt = [preamble]
    found_input = False
    inserted_assumptions = False
    for heading, body in sections:
        name = heading.strip()
        if name.lower() == INPUT_HEADING.lower():
            found_input = True
            cleaned = strip_input_pointer(body)
            if not cleaned.endswith("\n"):
                cleaned += "\n"
            cleaned = cleaned.rstrip() + "\n\n" + input_pointer + "\n\n"
            rebuilt.append(cleaned)
            continue
        if name.lower() == OUTPUT_HEADING.lower() and not inserted_assumptions:
            rebuilt.append(assumptions.rstrip() + "\n\n")
            inserted_assumptions = True
        rebuilt.append(body)
    if not found_input:
        raise DialogueError("instruction has no ## Input section")
    if not inserted_assumptions:
        raise DialogueError("instruction has no ## Output section to place the pointer before")
    return "".join(rebuilt)


def patch_dockerfile(text: str, workbook: str) -> str:
    if "WORKDIR /app" not in text:
        raise DialogueError("Dockerfile has no WORKDIR /app")
    workbook_copy = f"COPY {workbook} /app/{workbook}"
    if workbook_copy not in text:
        raise DialogueError(f"Dockerfile has no {workbook_copy}")
    if NOTES_COPY in text:
        return text if text.endswith("\n") else text + "\n"
    if not text.endswith("\n"):
        text += "\n"
    return text + NOTES_COPY + "\n"


def update_instruction_hash(task_toml: str, digest: str) -> str:
    if HASH_RE.search(task_toml):
        return HASH_RE.sub(r"\g<1>%s\2" % digest, task_toml, count=1)
    return task_toml


def instruction_faults(instruction: str, claims_payload: dict, task_dir: Path) -> list[str]:
    faults = []
    if DISCLOSURE_HEADING in instruction:
        faults.append("instruction still has ## Workbook disclosure")
    if LEFTOVER_BULLET_RE.search(instruction):
        faults.append("instruction still has Sheet!Range disclosure bullets")
    preamble, sections = split_sections(instruction)
    headings = [name.strip().lower() for name, _ in sections]
    if INPUT_HEADING.lower() not in headings:
        faults.append("instruction missing ## Input")
    if ASSUMPTIONS_HEADING.lower() not in headings:
        faults.append("instruction missing ## Additional assumptions")
    input_body = section_body(sections, INPUT_HEADING) or ""
    assumptions = section_body(sections, ASSUMPTIONS_HEADING) or ""
    for label, body in (("Input", input_body), ("Additional assumptions", assumptions)):
        if NOTES_NAME not in body:
            faults.append(f"{label} does not name {NOTES_NAME}")
        if "working directory" not in body.lower():
            faults.append(f"{label} does not say working directory")
    if "read" not in input_body.lower() or "before" not in input_body.lower():
        faults.append("Input pointer is not must-read language")
    for label, body in (("Input", input_body), ("Additional assumptions", assumptions)):
        if LEFTOVER_TITLE_RE.search(body):
            faults.append(f"{label} still names a senior banker/investor")
        hits = sorted({m.group(0) for m in REBUILD_FRAME_RE.finditer(body)})
        if hits:
            faults.append(f"{label} frames the work as a rebuild/restore: %s" % ", ".join(hits))
    return faults


def identity_faults(original: str, rewritten: str) -> list[str]:
    old_preamble, old_sections = split_sections(original)
    new_preamble, new_sections = split_sections(rewritten)
    if old_preamble != new_preamble:
        return ["opening prose was rewritten"]
    old_keep = {
        name.strip().lower(): body
        for name, body in old_sections
        if name.strip().lower() not in {
            INPUT_HEADING.lower(),
            DISCLOSURE_HEADING.lower(),
            ASSUMPTIONS_HEADING.lower(),
        }
    }
    new_keep = {
        name.strip().lower(): body
        for name, body in new_sections
        if name.strip().lower() not in {
            INPUT_HEADING.lower(),
            DISCLOSURE_HEADING.lower(),
            ASSUMPTIONS_HEADING.lower(),
        }
    }
    faults = []
    if set(old_keep) != set(new_keep):
        faults.append("protected headings changed")
    for name in sorted(old_keep):
        if old_keep[name] != new_keep.get(name):
            faults.append(f"protected section changed: {name}")
    return faults


def packaging_faults(task_dir: Path, dockerfile: str, workbook: str) -> list[str]:
    faults = []
    toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    if DOCKER_IMAGE_RE.search(toml):
        faults.append("task.toml still declares docker_image; refuse apply")
    if "WORKDIR /app" not in dockerfile:
        faults.append("Dockerfile missing WORKDIR /app")
    if f"COPY {workbook} /app/{workbook}" not in dockerfile:
        faults.append("Dockerfile missing workbook COPY")
    if NOTES_COPY not in dockerfile:
        faults.append("Dockerfile missing additional-assumptions COPY")
    if not (task_dir / "environment" / NOTES_NAME).is_file():
        faults.append(f"missing environment/{NOTES_NAME}")
    return faults


def copy_semantics_smoke(task_dir: Path, dockerfile: str) -> None:
    """Prove every COPY source exists and lands under /app as the Dockerfile says."""
    env = task_dir / "environment"
    copies = re.findall(r"^COPY\s+(\S+)\s+(/app/\S+)\s*$", dockerfile, re.M)
    if not copies:
        raise DialogueError("Dockerfile has no COPY lines into /app")
    dests = {dest for _, dest in copies}
    if f"/app/{NOTES_NAME}" not in dests:
        raise DialogueError(f"Dockerfile does not COPY {NOTES_NAME} to /app")
    missing = [src for src, _ in copies if not (env / src).is_file()]
    if missing:
        raise DialogueError("COPY source missing in environment/: %s" % ", ".join(missing))


def docker_smoke(task_dir: Path) -> None:
    tag = "aa-dialogue-smoke-%s-%s" % (task_dir.name.split("-")[0], os.getpid())
    env = task_dir / "environment"
    probe = subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise DialogueError(
            "docker daemon is not running; start Docker and re-run apply "
            "without --skip-smoke. COPY semantics were still checked."
        )
    try:
        build = subprocess.run(
            ["docker", "build", "-t", tag, str(env)],
            check=False,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise DialogueError("docker build failed: %s" % (build.stderr or build.stdout)[-800:])
        run = subprocess.run(
            ["docker", "run", "--rm", tag, "test", "-f", f"/app/{NOTES_NAME}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode != 0:
            raise DialogueError(f"/app/{NOTES_NAME} missing from built image")
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], check=False, capture_output=True)


def atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".aa.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def apply(task_dir: Path, draft: Path, claims_payload: dict, review: dict | None,
          require_review_pass: bool, skip_smoke: bool) -> dict:
    if claims_payload.get("empty") or not claims_payload.get("claims"):
        raise DialogueError("empty agent_records: do not apply")
    if require_review_pass:
        round_faults = review_faults(review, claims_payload)
        if round_faults:
            raise DialogueError(
                "review did not pass; not applying after round 1: "
                + "; ".join(round_faults[:6])
            )
    accuracy_gate_faults = review_accuracy_faults(review, claims_payload)
    if accuracy_gate_faults:
        raise DialogueError(
            "reviewer accuracy did not pass; not applying: "
            + "; ".join(accuracy_gate_faults[:6])
        )
    if DOCKER_IMAGE_RE.search((task_dir / "task.toml").read_text(encoding="utf-8")):
        raise DialogueError("refuse apply: bare docker_image task")

    original_instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
    original_docker = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
    original_toml = (task_dir / "task.toml").read_text(encoding="utf-8")
    notes_path = task_dir / "environment" / NOTES_NAME
    original_notes = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else None
    marker_path = task_dir / APPLIED_MARKER
    original_marker = marker_path.read_text(encoding="utf-8") if marker_path.is_file() else None

    raw_dialogue = draft.read_text(encoding="utf-8")
    draft_report = check_draft(raw_dialogue, claims_payload, task_dir)
    # Cell-address leaks in senior turns must not replace working bullets,
    # even after the two-round force-ship. Reviewer naturalness can fail.
    if draft_report.get("senior_cell_refs"):
        raise DialogueError(
            "refuse apply: senior turns still contain cell refs: %s"
            % ", ".join(draft_report["senior_cell_refs"][:6])
        )
    dialogue = strip_claim_comments(raw_dialogue)
    instruction = rewrite_instruction(original_instruction, task_dir, claims_payload)
    workbook = artifact_name(task_dir)
    dockerfile = patch_dockerfile(original_docker, workbook)
    toml = update_instruction_hash(original_toml, sha256_text(instruction))

    faults = list(draft_report["faults"])
    blocking = []
    blocking.extend(draft_report.get("accuracy_faults") or [])
    blocking.extend(draft_report.get("cast_faults") or [])
    blocking.extend(instruction_faults(instruction, claims_payload, task_dir))
    blocking.extend(identity_faults(original_instruction, instruction))
    try:
        notes_path.write_text(dialogue, encoding="utf-8")
        atomic_write(task_dir / "environment" / "Dockerfile", dockerfile)
        atomic_write(task_dir / "instruction.md", instruction)
        atomic_write(task_dir / "task.toml", toml)
        write_json(marker_path, {
            "skill": "additional-assumptions-dialogue",
            "applied": True,
            "notes": f"environment/{NOTES_NAME}",
            "instruction_sha256": sha256_text(instruction),
            "review_passed": review_passed(review, claims_payload),
            "draft_passed": draft_report["passed"],
            "do_not_rerun": list(DO_NOT_RERUN),
        })
        blocking.extend(packaging_faults(task_dir, dockerfile, workbook))
        if blocking:
            raise DialogueError("; ".join(blocking[:8]))
        copy_semantics_smoke(task_dir, dockerfile)
        if not skip_smoke:
            docker_smoke(task_dir)
    except Exception:
        atomic_write(task_dir / "instruction.md", original_instruction)
        atomic_write(task_dir / "environment" / "Dockerfile", original_docker)
        atomic_write(task_dir / "task.toml", original_toml)
        if original_notes is None:
            if notes_path.exists():
                notes_path.unlink()
        else:
            atomic_write(notes_path, original_notes)
        if original_marker is None:
            if marker_path.exists():
                marker_path.unlink()
        else:
            atomic_write(marker_path, original_marker)
        raise

    return {
        "applied": True,
        "draft_passed": draft_report["passed"],
        "draft_faults": draft_report["faults"],
        "review_passed": review_passed(review, claims_payload),
        "instruction_sha256": sha256_text(instruction),
        "notes": str(notes_path),
        "marker": str(marker_path),
        "copy_semantics_smoke": True,
        "docker_smoke": (not skip_smoke),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check-draft", help="mechanical draft checks")
    check.add_argument("--task-dir", required=True, type=Path)
    check.add_argument("--draft", required=True, type=Path)
    check.add_argument("--claims", required=True, type=Path)
    check.add_argument("--report", default="")

    fill_cmd = sub.add_parser(
        "fill-check",
        help="verify the writer kept the composed template byte-identical "
             "outside the slots, then run the mechanical draft checks",
    )
    fill_cmd.add_argument("--task-dir", required=True, type=Path)
    fill_cmd.add_argument("--template", required=True, type=Path)
    fill_cmd.add_argument("--draft", required=True, type=Path)
    fill_cmd.add_argument("--claims", required=True, type=Path)
    fill_cmd.add_argument(
        "--out", default="",
        help="write the clean dialogue (slot comments stripped) here once "
             "the structure holds",
    )
    fill_cmd.add_argument("--report", default="")

    review_cmd = sub.add_parser(
        "check-review",
        help="validate the reviewer JSON against the required schema and round gate",
    )
    review_cmd.add_argument("--claims", required=True, type=Path)
    review_cmd.add_argument("--review", required=True, type=Path)
    review_cmd.add_argument("--round", type=int, default=1)
    review_cmd.add_argument("--report", default="")

    apply_cmd = sub.add_parser("apply", help="write notes, patch Dockerfile, rewrite instruction")
    apply_cmd.add_argument("--task-dir", required=True, type=Path)
    apply_cmd.add_argument("--draft", required=True, type=Path)
    apply_cmd.add_argument("--claims", required=True, type=Path)
    apply_cmd.add_argument("--review", type=Path, default=None)
    apply_cmd.add_argument("--round", type=int, default=2)
    apply_cmd.add_argument("--skip-smoke", action="store_true")
    apply_cmd.add_argument("--report", default="")

    smoke_cmd = sub.add_parser("smoke", help="docker-build the main image and prove /app notes exist")
    smoke_cmd.add_argument("--task-dir", required=True, type=Path)

    args = parser.parse_args(argv)
    if args.cmd == "smoke":
        task_dir = args.task_dir.resolve()
        dockerfile = (task_dir / "environment" / "Dockerfile").read_text(encoding="utf-8")
        try:
            copy_semantics_smoke(task_dir, dockerfile)
            docker_smoke(task_dir)
        except DialogueError as exc:
            print("FAIL:", exc, file=sys.stderr)
            return 2
        print("smoke PASS: /app/%s" % NOTES_NAME)
        return 0
    if args.cmd == "check-review":
        claims_payload = load_claims(args.claims.resolve())
        try:
            review = load_review(args.review)
        except json.JSONDecodeError as exc:
            review = None
            faults = [
                "review file is not valid JSON (%s); expected: %s"
                % (exc, REVIEW_SCHEMA_TEMPLATE)
            ]
        else:
            if review is None:
                faults = [
                    "review file %s does not exist; the reviewer must write it "
                    "before the round can be scored" % args.review
                ]
            elif args.round < 2:
                faults = review_faults(review, claims_payload)
            else:
                faults = review_accuracy_faults(review, claims_payload)
        report = {
            "passed": not faults,
            "round": args.round,
            "faults": faults,
            "schema": REVIEW_SCHEMA_TEMPLATE,
        }
        if args.report:
            write_json(Path(args.report), report)
        print("PASS" if not faults else "FAIL")
        for fault in faults[:12]:
            print("  -", fault)
        return 0 if not faults else 2

    task_dir = args.task_dir.resolve()
    claims_payload = load_claims(args.claims.resolve())
    if args.cmd == "fill-check":
        template = args.template.read_text(encoding="utf-8")
        filled = args.draft.read_text(encoding="utf-8")
        structure_faults = fill_faults(template, filled)
        report = {
            "passed": False,
            "structure_passed": not structure_faults,
            "structure_faults": structure_faults,
            "draft_report": None,
            "out": args.out or None,
        }
        if structure_faults:
            if args.report:
                write_json(Path(args.report), report)
            print("FAIL (structural drift)")
            for fault in structure_faults:
                print("  -", fault)
            return 2
        cleaned = strip_slot_scaffold(filled)
        if args.out:
            Path(args.out).write_text(cleaned, encoding="utf-8")
        draft_report = check_draft(cleaned, claims_payload, task_dir)
        report["passed"] = draft_report["passed"]
        report["draft_report"] = draft_report
        if args.report:
            write_json(Path(args.report), report)
        print("PASS" if draft_report["passed"] else "FAIL (check-draft)")
        for fault in draft_report["faults"][:12]:
            print("  -", fault)
        return 0 if draft_report["passed"] else 3
    if args.cmd == "check-draft":
        report = check_draft(args.draft.read_text(encoding="utf-8"), claims_payload, task_dir)
        if args.report:
            write_json(Path(args.report), report)
        print("PASS" if report["passed"] else "FAIL")
        for fault in report["faults"][:12]:
            print("  -", fault)
        return 0 if report["passed"] else 2

    review = load_review(args.review)
    require_pass = args.round < 2
    try:
        result = apply(
            task_dir, args.draft.resolve(), claims_payload, review,
            require_review_pass=require_pass, skip_smoke=args.skip_smoke,
        )
    except (DialogueError, ValueError) as exc:
        print("FAIL:", exc, file=sys.stderr)
        return 2
    if args.report:
        write_json(Path(args.report), result)
    print("applied %s" % result["notes"])
    if result["draft_faults"] and not result["draft_passed"]:
        print("draft faults recorded (naturalness / non-blocking):")
        for fault in result["draft_faults"][:8]:
            print("  -", fault)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
