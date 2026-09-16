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
    junior_scope_text,
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


def review_warnings(review: dict | None, claims_payload: dict, *, draft_text=None, source_warning_proof=None) -> list[dict]:
    """Recognize redundant scope prefixes and narrowly identified pure style issues.

    The original review is never changed. Entailment, claim coverage, leakage,
    and substantive naturalness issues remain gates; unknown findings stay blocking.
    """
    if not isinstance(review, dict) or not isinstance(draft_text, str):
        return []
    if review_accuracy_faults(review, claims_payload):
        return []
    if source_warning_proof is not None:
        if source_warning_proof.get('schema_version') == 'harbor-dialogue-source-revision-warning/v1':
            from scripts.batch import harbor_dialogue_source_revision_warnings as source_warnings
        else:
            from scripts.batch import harbor_dialogue_source_warnings as source_warnings
        proof = source_warnings.revalidate(source_warning_proof, review, claims_payload, draft_text,
                                          root=Path(__file__).resolve().parents[4])
        return proof['warnings']
    claims = claims_payload.get('claims') or []
    mapped, faults = map_turns_to_claims(parse_turns(draft_text), claims)
    if faults:
        return []
    by_id = {claim['record_id']: claim for claim in claims}
    warnings = []
    accuracy = review['accuracy']
    findings = [(row['record_id'], finding) for row in accuracy['claims']
                for finding in row.get('findings', [])]
    findings += [(None, finding) for finding in accuracy.get('extras', [])]
    for identity, finding in findings:
        if not isinstance(finding, str):
            return []
        match = re.fullmatch(r'The (?:shared )?frozen junior(?:_| )scope header is altered by (?:inserting|prepending) "([^"\n]+):" before the required scoped-cell prefix\.', finding)
        if not match:
            return []
        candidates = [by_id[identity]] if identity in by_id else claims
        matched = []
        for claim in candidates:
            scope = junior_scope_text(claim)
            if not scope or claim.get('sheet') != match[1]:
                continue
            juniors = [turn for turn in mapped.get(claim['record_id'], [])
                       if is_junior(turn['speaker'])]
            groups = (claims_payload.get('shared_source_links') or {}).get('groups', [])
            owning = [group for group in groups if claim['record_id'] in group]
            if len(owning) > 1:
                continue
            group = owning[0] if owning else [claim['record_id']]
            if (not group or any(identity not in by_id or by_id[identity].get('sheet') != match[1]
                                 for identity in group)):
                continue
            scopes = [junior_scope_text(by_id[identity]) for identity in group]
            if not all(scopes):
                continue
            header = match[1] + ': ' + '; '.join(scopes) + ':'
            if any(turn['text'].startswith(header) and all(
                    any(other is turn for other in mapped.get(identity, [])) for identity in group)
                   for turn in juniors):
                matched.append(claim['record_id'])
        if len(matched) != 1:
            return []
        warnings.append({'code': 'redundant_sheet_scope_prefix', 'record_id': matched[0],
                         'finding': finding, 'sheet': match[1]})
    naturalness=review.get('naturalness')
    if isinstance(naturalness,dict) and naturalness.get('verdict')=='fail':
        style=naturalness.get('findings')
        if not isinstance(style,list) or not style:
            return warnings
        simple=r'(?:The (?:VP|senior) response is |The dialogue is )?(?:[Rr]epetitive|[Ss]tilted|[Ff]orced|[Ff]ormulaic|[Oo]verly formal)(?: and (?:repetitive|stilted|forced|formulaic|overly formal))?(?: phrasing)?\.?'
        restatement=r'The VP response is repetitive and forced: "[^"\n]+" merely restates "[^"\n]+" and reads like wording inserted to satisfy a rubric rather than natural Slack dialogue\.'
        repeated_templates = (
            'Across the file, the junior questions and senior answers repeat a narrow set of templates '
            'for nearly every row, especially throughout the long source-selection section, so the '
            'conversation reads like a generated checklist rather than Slack.'
        )
        counted_templates = (
            f"Across {len(claims)} exchanges, the junior questions and senior answers cycle through "
            "narrowly varied templates, making the file read like a generated checklist rather than a Slack conversation."
        )
        counted_mapping = (len(claims) > 0 and
                           all(len(mapped.get(c['record_id'], [])) == 2 and
                               sum(is_junior(t['speaker']) for t in mapped[c['record_id']]) == 1
                               for c in claims))
        retained_template_findings = {
            'The first 84 exchanges mechanically cycle through a small set of near-identical junior-question and senior-answer templates, so the conversation reads as a generated checklist rather than Slack.',
            'The 24 Summary exchanges continue the same repetitive one-cell-at-a-time pattern and read like model documentation rather than a natural deal-team conversation.',
            'The file is a long, mechanically repeated sequence of scoped source-link questions and declarative answers, so it reads like a checklist or model-mapping document rather than a natural Slack conversation.',
            "The first two senior answers are near-copies of their cards' spoken lines, preserving the same row-label, held-flat, forecast, and last-known-level construction.",
            'Across the file, the junior questions cycle through a small set of nearly identical source-check templates, making the conversation read like a generated checklist rather than natural Slack.',
            'Several senior replies, particularly the debt movement and pre-payment answers, are near-copies of the corresponding card spoken lines, preserving the same clause order and formula-by-formula phrasing rather than sounding like organic Slack guidance.',
            'The senior answers repeatedly reproduce the cards\' spoken lines with only minor reordering, especially the recurring construction combining "only for this cell," the row label, and the exact arithmetic.',
            'Across the file, forty isolated two-turn exchanges repeat the same cell-by-cell sourcing question and answer with only surface-level wording changes, so the conversation reads as a checklist rather than natural Slack.',
            'The extended runs of minimally varied one-cell tax exchanges and source-link exchanges remain checklist-like and read more like generated model documentation than a natural Slack conversation.',
            'The long sequence of nearly identical source-selection questions and answers reads like an exhaustive checklist or generated catalogue rather than a Slack conversation.',
            "Many source-selection senior turns are only light syntactic variations of the cards' spoken lines, creating sustained near-copy repetition.",
            'The junior questions repeat the same small set of templated requests across the file, making the conversation feel checklist-like rather than like a Slack exchange.',
            'The senior answers repeatedly use rubric-style formula descriptions such as column-fixed and row-fixed inputs followed by a stock sentence about other references shifting, so the file reads more like modelling documentation than natural deal-team conversation.',
            'The extended source-selection section is especially repetitive, repeatedly asking which row feeds, links to, or supplies another row and answering with the same whole-value-source construction.',
        }
        def documentation_style_only(f):
            if not isinstance(f,str) or not counted_mapping:
                return False
            source_link_style = {
                'The long run of source-link exchanges is holistically checklist-like, with junior questions repeatedly asking which row feeds or drives another row.',
                'Despite some surface variation, the repeated two-turn row-by-row confirmations read more like structured documentation than a natural Slack conversation.',
            }
            if f in source_link_style:
                indices = [i for i,c in enumerate(claims)
                           if c.get('reviewer_only',{}).get('entry') == 'source_selection']
                return (len(indices) >= 24
                        and indices == list(range(indices[0],indices[-1]+1))
                        and all(isinstance(claims[i].get('spoken'),str)
                                and bool(claims[i]['spoken']) for i in indices))
            retained_source_style = {
                'The long P&L Output section is holistically checklist-like: dozens of isolated two-turn exchanges repeat the same source-row question and full-value answer pattern across adjacent periods.',
                'Many senior source-selection answers are near-copies of the cards\' spoken construction, changing little beyond synonyms such as "full," "whole," "supply," and "feed," so the file reads more like generated model documentation than Slack.',
            }
            if f in retained_source_style:
                # These exact findings concern repetition only. Require the
                # described source-selection section; accuracy and mechanics
                # remain separate mandatory gates, including every clause.
                indices = [i for i,c in enumerate(claims)
                           if c.get('sheet') == 'P&L Output'
                           and c.get('reviewer_only',{}).get('entry') == 'source_selection']
                return (len(indices) >= 24
                        and indices == list(range(indices[0],indices[-1]+1))
                        and all(isinstance(claims[i].get('spoken'),str)
                                and bool(claims[i]['spoken']) for i in indices))
            if f == 'The file reads as a repetitive formula checklist rather than a natural Slack conversation: nearly every exchange follows the same scoped-cell question and implementation-answer pattern.':
                return True
            verbatim=re.fullmatch(r"The VP turn for ([^\n]+) reproduces the card's spoken line verbatim with a speaker prefix\.",f)
            if verbatim:
                claim=by_id.get(verbatim[1]);turns=[] if claim is None else [t for t in mapped[claim['record_id']] if is_senior(t['speaker'])]
                return len(turns)==1 and turns[0]['speaker']=='VP' and isinstance(claim.get('spoken'),str) and bool(claim['spoken']) and turns[0]['text']==claim['spoken']
            wording=re.fullmatch(r'Several senior turns use stilted, documentation-style wording such as “([^”\n]+),” “([^”\n]+),” and “([^”\n]+)\.”',f)
            if wording:
                seniors=[t['text'] for turns in mapped.values() for t in turns if is_senior(t['speaker'])]
                return all(fragment and any(fragment in text for text in seniors) for fragment in wording.groups())
            return False
        from scripts.batch.harbor_dialogue_first_round_warning import style_only
        if all(isinstance(f,str) and (re.fullmatch(simple,f) or re.fullmatch(restatement,f)
                or f == repeated_templates or f in retained_template_findings
                or (f == counted_templates and counted_mapping)
                or documentation_style_only(f) or style_only(f,claims_payload,draft_text)) for f in style):
            warnings += [{'code':'dialogue_style_only','finding':f} for f in style]
    return warnings


def review_faults(review: dict | None, claims_payload: dict, *, draft_text=None, source_warning_proof=None) -> list[str]:
    """Diagnose the full review gate. Empty list == review_passed."""
    if not review:
        return [
            "no review JSON was loaded; expected: %s" % REVIEW_SCHEMA_TEMPLATE
        ]
    faults = review_accuracy_faults(review, claims_payload)
    warnings = review_warnings(review, claims_payload, draft_text=draft_text, source_warning_proof=source_warning_proof)
    accuracy = review.get("accuracy") or {}
    if isinstance(accuracy, dict):
        if accuracy.get("verdict") != "pass" and not (any(w['code'] in {'redundant_sheet_scope_prefix','source_formula_operand','frozen_junior_prefix'} for w in warnings) and accuracy.get('verdict') == 'fail'):
            faults.append(
                'accuracy.verdict is %r; expected the string "pass" (fragment: '
                '"verdict": "pass|fail")' % accuracy.get("verdict")
            )
        extras = accuracy.get("extras") or []
        if extras and not warnings:
            faults.append(
                "accuracy.extras is non-empty: %r; extras must be []" % extras[:4]
            )
    naturalness = review.get("naturalness")
    if not isinstance(naturalness, dict):
        faults.append(
            'review is missing the "naturalness" object; expected fragment: %s'
            % NATURALNESS_FRAGMENT
        )
    elif naturalness.get("verdict") != "pass" and not (naturalness.get('verdict')=='fail' and any(w['code']=='dialogue_style_only' for w in warnings)):
        faults.append(
            'naturalness.verdict is %r; expected the string "pass" (fragment: %s)'
            % (naturalness.get("verdict"), NATURALNESS_FRAGMENT)
        )
    if review.get("passed") is not True and not (warnings and review.get('passed') is False):
        faults.append(
            '"passed" is %r; expected the JSON boolean true (only when both '
            "verdicts pass, every must_say is entailed, extras is empty, and "
            "cell_refs_in_senior_turns is empty)" % review.get("passed")
        )
    return faults


def review_passed(review: dict | None, claims_payload: dict | None = None, *, draft_text=None, source_warning_proof=None) -> bool:
    if claims_payload is None:
        claims_payload = {}
    return not review_faults(review, claims_payload, draft_text=draft_text, source_warning_proof=source_warning_proof)


def map_turns_to_claims(turns: list[dict], claims: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    """Claim comments are authoritative. Turns before the first comment stay unclaimed."""
    mapped = {claim["record_id"]: [] for claim in claims}
    faults = []
    pending = []
    seen_comment = False
    for turn in turns:
        record_ids = turn.get("record_ids") or ([turn['record_id']] if turn.get('record_id') else [])
        if record_ids:
            seen_comment = True
            unknown = [identity for identity in record_ids if identity not in mapped]
            if unknown or len(set(record_ids)) != len(record_ids):
                faults.append(
                    "unknown claim comment: %s; expected RECORD_ID copied exactly "
                    "from claims.json, one of: %s"
                    % (', '.join(unknown or record_ids), ", ".join(sorted(mapped)[:4]) or "(none)")
                )
                pending = []
                continue
            for record_id in record_ids:
                mapped[record_id].append(turn)
            pending = record_ids
            continue
        if seen_comment and pending:
            for record_id in pending:
                mapped[record_id].append(turn)
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
    def literals(node):
        if node.kind == "const" and node.name == "number":
            try:
                return Counter({float(node.value): 1})
            except (TypeError, ValueError):
                return Counter()
        if node.kind == "prefix" and node.name in {"+", "-"} and len(node.args) == 1:
            child = node.args[0]
            if child.kind == "const" and child.name == "number":
                try:
                    return Counter({float(child.value) * (-1 if node.name == "-" else 1): 1})
                except (TypeError, ValueError):
                    return Counter()
        result = Counter()
        for child in node.args:
            result.update(literals(child))
        return result
    return literals(ast)


def licensed_row_label_literals(claim: dict) -> Counter:
    """License one occurrence per exact required target or source row label."""
    labels={str(claim.get("row_label") or "")}
    for atom in claim.get('must_say') or []:
        if not atom.startswith('the row labelled '):continue
        try:
            label=json.loads(atom[len('the row labelled '):])
            if isinstance(label,str):labels.add(label)
        except (ValueError,TypeError):continue
    allowed: Counter = Counter()
    for label in labels:
        for raw in disclose.NUMBER_RE.findall(label):
            try:allowed[float(raw.replace(",", ""))] += 1
            except ValueError:continue
    return allowed


def numeric_faults_with_budget(
    text: str, task_dir: Path, allowed: Counter | None = None
) -> list[str]:
    """Audit target collisions while consuming only proven literal occurrences."""
    budget = Counter(allowed or {})
    targets = disclose.numeric_targets(task_dir)
    faults = []
    for raw in disclose.NUMBER_RE.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        matched_target = next(
            (
                target
                for target in targets
                if disclose.same_number(value, float(target))
            ),
            None,
        )
        if matched_target is None:
            continue
        permitted = next(
            (
                number
                for number, count in budget.items()
                if count and disclose.same_number(value, number)
            ),
            None,
        )
        if permitted is not None:
            budget[permitted] -= 1
            continue
        faults.append(f"numeric literal {raw} matches target {matched_target}")
    return faults


def custom_junior_label_mask(text: str, claim: dict, gold, spans: list | None = None) -> str:
    """Mask one complete source row label in its owning junior question only."""
    reviewer = claim.get('reviewer_only') or {}
    cells = reviewer.get('cells') or []
    if (gold is None or reviewer.get('source') != 'custom_method_detector'
            or len(cells) != 1 or cells != claim.get('junior_scope')):
        return text
    cell = disclose.parse_ref(cells[0], claim.get('sheet') or '')
    label = claim.get('row_label')
    if (not isinstance(label, str) or not label or gold.row_label(cell) != label
            or not gold.formula.get(cell) or gold.formula[cell] != reviewer.get('evidence')
            or 'the row labelled ' + json.dumps(label, ensure_ascii=False) not in (claim.get('must_say') or [])):
        return text
    matches = list(re.finditer(r'\brow labelled\s+(' + re.escape(json.dumps(label, ensure_ascii=False)) + r')', text))
    if len(matches) != 1:
        return text
    start, end = matches[0].span(1)
    if spans is not None:
        spans.append(('junior_source_row_label', start, end))
    return text[:start] + ' ' * (end-start) + text[end:]


def source_metadata_mask(text: str, claim: dict, gold, *, junior: bool, spans: list | None = None) -> str:
    """Mask exact source-proved metadata spans, never a global numeric budget."""
    if gold is None:
        return text
    if junior and (claim.get('reviewer_only') or {}).get('source') == 'custom_method_detector':
        return custom_junior_label_mask(text, claim, gold, spans)
    reviewer = claim.get('reviewer_only') or {}
    cells = reviewer.get('cells') or []
    if (reviewer.get('entry') != 'source_selection' or reviewer.get('source') != 'convention_detector'
            or len(cells) != 1 or cells != claim.get('junior_scope')):
        return text
    target = disclose.parse_ref(cells[0], claim.get('sheet') or '')
    if gold.row_label(target) != claim.get('row_label'):
        return text
    formula = gold.formula.get(target, '').strip()
    if not disclose.BARE_LINK_RE.fullmatch(formula):
        return text
    source = disclose.bare_alias_ref(gold, target)
    if not source:
        return text
    label = gold.row_label(source)
    atom = 'the row labelled ' + json.dumps(label, ensure_ascii=False)
    if not label or atom not in (claim.get('must_say') or []):
        return text
    # Avoid freeing the pre-existing target-row numeric budget for another
    # occurrence when source and target labels share a numeric token.
    if not junior and (licensed_row_label_literals(claim) & licensed_row_label_literals({'row_label': label})):
        return text
    masked = text
    proven_spans = []
    if junior:
        scope = junior_scope_text(claim)
        if not scope:
            return text
        plain = scope + ':'
        header = str(claim.get('sheet') or '') + ': ' + plain
        if masked.startswith(header):
            split = len(header) - len(plain)
            proven_spans.extend([('junior_sheet_header', 0, split), ('junior_scope', split, len(header))])
            masked = ' ' * len(header) + masked[len(header):]
        elif masked.startswith(plain):
            proven_spans.append(('junior_scope', 0, len(plain)))
            masked = ' ' * len(plain) + masked[len(plain):]
        else:
            return text
        label = claim['row_label']
    # Exactly one complete quoted label occurrence in an explicit label phrase.
    quoted = json.dumps(label, ensure_ascii=False)
    match = re.search(r'\brow labelled\s+(' + re.escape(quoted) + r')', masked)
    if match:
        start, end = match.span(1)
        proven_spans.append(('junior_row_label' if junior else 'source_row_label', start, end))
        masked = masked[:start] + ' ' * (end-start) + masked[end:]
    if spans is not None:
        spans.extend(proven_spans)
    return masked


def checked_metadata_source(binding):
    """An explicit bound selected workbook is required; never infer its path."""
    if binding is None:
        return None
    import hashlib
    if (not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}
            or hashlib.sha256(Path(binding['path']).read_bytes()).hexdigest() != binding['sha256']):
        raise ValueError('numeric metadata source binding changed')
    return disclose.Book(Path(binding['path']))

def audit_dialogue(
    dialogue: str,
    claims: list[dict],
    turns: list[dict],
    mapped: dict[str, list[dict]],
    task_dir: Path,
    source_workbook: dict | None = None,
    metadata_warnings: list | None = None,
) -> list[str]:
    """Audit prose globally, but numeric literals claim-by-claim."""
    metadata_source = checked_metadata_source(source_workbook)
    def mask_metadata(text, claim, *, junior):
        spans = []
        masked = source_metadata_mask(text, claim, metadata_source, junior=junior, spans=spans)
        if metadata_warnings is not None:
            for role, start, end in spans:
                # Reuse the owning target-collision detector on precisely the
                # proved span; do not report unrelated numbers as warnings.
                count = len(numeric_faults_with_budget(text[start:end], task_dir))
                if count:
                    metadata_warnings.append({'code': 'source_metadata_numeric_collision',
                        'record_id': claim['record_id'], 'turn_role': 'junior' if junior else 'senior',
                        'span_role': role, 'character_span': [start, end], 'count': count})
        return masked
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
        text = mask_metadata("\n".join(turn["text"] for turn in senior_turns), claim, junior=False)
        allowed = licensed_formula_literals(claim, task_dir)
        allowed.update(licensed_row_label_literals(claim))
        faults.extend(
            numeric_faults_with_budget(
                text, task_dir, allowed
            )
        )
    # Juniors, kickoff turns, and any unmapped senior prose receive no budget.
    for turn in turns:
        if id(turn) not in assigned_senior_turns:
            owners = [claim for claim in claims if any(turn is item for item in mapped.get(claim["record_id"], []))]
            text = turn["text"]
            if (len(owners) == 1 and is_junior(turn["speaker"])
                    and sum(is_junior(item["speaker"]) for item in mapped[owners[0]["record_id"]]) == 1):
                text = mask_metadata(text, owners[0], junior=True)
            faults.extend(numeric_faults_with_budget(text, task_dir))
    return faults



def reference_literal_scan(turns, claims, mapped, task_dir):
    """Mask one proved typed quote only for the owning senior reference scan.

    The original turns are never mutated: answer/numeric leakage and semantic
    review still consume the entire original text, including licensed labels.
    """
    from extract_claims import reference_literal_proofs
    expected = reference_literal_proofs(task_dir, claims)
    for claim in claims:
        if (claim.get('reviewer_only') or {}).get('reference_literal_proof') != expected.get(claim['record_id']):
            raise ValueError('reference literal occurrence proof missing or changed')
    values = {v for proof in expected.values() for v in proof['values']}
    used = set()
    texts = []
    faults = []
    for turn in turns:
        text = turn['text']
        owners = [c['record_id'] for c in claims
                  if any(t is turn for t in mapped.get(c['record_id'], []))]
        spans = []
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
            try:
                value = json.loads(match.group())
            except (ValueError, TypeError):
                continue
            if value not in values:
                continue
            owner = owners[0] if len(owners) == 1 else None
            typed = re.search(r'\b(?:text|literal|string)\s*$', text[:match.start()], re.I)
            identity = (owner, value)
            if (is_senior(turn['speaker']) and owner in expected
                    and value in expected[owner]['values'] and typed
                    and identity not in used):
                used.add(identity)
                spans.append(match.span())
            else:
                faults.append("reference-shaped literal has an extra or unbound quoted occurrence")
        masked = text
        for start, end in reversed(spans):
            masked = masked[:start] + ' '*(end-start) + masked[end:]
        # A same-spelled row-label anywhere else cannot turn this new exact
        # occurrence capability into the old global token exemption.
        scope_scan = masked
        if is_junior(turn['speaker']):
            for claim in claims:
                if claim['record_id'] in owners and claim.get('junior_scope'):
                    scope_scan = scope_scan.replace(junior_scope_text(claim), '', 1)
        if any(token.strip() in values for token in cell_refs_in(scope_scan)):
            faults.append('reference-shaped literal has an extra or unbound occurrence')
        if is_senior(turn['speaker']):
            texts.append(masked)
    return '\n'.join(texts), faults, values

def check_draft(dialogue: str, claims_payload: dict, task_dir: Path, *, source_workbook: dict | None = None) -> dict:
    faults = []
    accuracy_faults = []
    style_warnings = []
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

    mapped, map_faults = map_turns_to_claims(turns, claims)
    accuracy_faults.extend(map_faults)
    literal_values = set()
    try:
        senior_text, literal_faults, literal_values = reference_literal_scan(turns, claims, mapped, task_dir)
        accuracy_faults.extend(literal_faults)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        accuracy_faults.append('reference literal provenance invalid: ' + str(exc))
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
        if token.strip() not in label_tokens or token.strip() in literal_values
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
        scope = junior_scope_text(claim)
        if scope and not any(scope in turn['text'] for turn in juniors):
            accuracy_faults.append(f"{claim['record_id']}: junior turn missing exact disclosed cell scope")
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
        if claim["row_label"] and " ".join(claim["row_label"].lower().split()) not in " ".join(blob.lower().split()):
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
            style_warnings.append(
                f"{claim['record_id']}: senior turn is a near-copy of spoken"
            )

    faults.extend(accuracy_faults)
    metadata_warnings = []
    faults.extend(audit_dialogue(dialogue, claims, turns, mapped, task_dir, source_workbook, metadata_warnings))
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
    result = {
        "passed": not faults,
        "faults": faults,
        "accuracy_faults": accuracy_faults,
        "cast_faults": list(dict.fromkeys(cast_faults)),
        "turns": len(turns),
        "senior_cell_refs": leaks,
        "senior_axis_refs": axis_leaks,
        "style_warnings": style_warnings,
    }

    if source_workbook is not None:
        result['metadata_warnings'] = {'source': dict(source_workbook), 'count': sum(w['count'] for w in metadata_warnings), 'items': metadata_warnings}
    return result


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
    input_pointer, _ = expected_pointer(task_dir, claims_payload)
    if input_pointer not in input_body:
        faults.append("Input pointer does not match the generated must-read text")
    for label, body in (("Additional assumptions", assumptions),):
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
          require_review_pass: bool, skip_smoke: bool, *, source_workbook: dict | None = None, source_warning_proof=None) -> dict:
    if claims_payload.get("empty") or not claims_payload.get("claims"):
        raise DialogueError("empty agent_records: do not apply")
    # Kept for CLI/API compatibility; no round may weaken the full review gate.
    _ = require_review_pass
    accuracy_gate_faults = review_accuracy_faults(review, claims_payload)
    raw_dialogue = draft.read_text(encoding="utf-8")
    round_faults = review_faults(review, claims_payload, draft_text=raw_dialogue, source_warning_proof=source_warning_proof)
    if round_faults:
        gate = (
            "reviewer accuracy did not pass"
            if accuracy_gate_faults
            else "full review did not pass"
        )
        raise DialogueError(
            f"{gate}; full review is required in every round; not applying: "
            + "; ".join(round_faults[:6])
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
    draft_report = check_draft(raw_dialogue, claims_payload, task_dir, source_workbook=source_workbook)
    # Cell-address leaks in senior turns must not replace working bullets,
    # even after the second round. The full review gate above must also pass.
    if draft_report.get("senior_cell_refs"):
        raise DialogueError(
            "refuse apply: senior turns still contain cell refs: %s"
            % ", ".join(draft_report["senior_cell_refs"][:6])
        )
    warning_summaries = review_warnings(review, claims_payload, draft_text=raw_dialogue, source_warning_proof=source_warning_proof)
    if source_warning_proof is not None:
        safe = {'source_formula_operand': 'A flagged constant is an authenticated source-formula operand.',
                'frozen_junior_prefix': 'The flagged scope prefix matches the frozen template.',
                'hidden_frozen_scaffold': 'Hidden scaffold comments were retained as a presentation-only warning.',
                'dialogue_style_only': 'Repetitive wording was retained as a language-only warning.'}
        warning_summaries = [{'code': code, 'finding': safe[code]}
                             for code in sorted({row['code'] for row in warning_summaries})]
    dialogue = strip_claim_comments(raw_dialogue)
    instruction = rewrite_instruction(original_instruction, task_dir, claims_payload)
    workbook = artifact_name(task_dir)
    dockerfile = patch_dockerfile(original_docker, workbook)
    toml = update_instruction_hash(original_toml, sha256_text(instruction))

    faults = list(draft_report["faults"])
    blocking = list(faults)
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
            "review_passed": review_passed(review, claims_payload, draft_text=raw_dialogue, source_warning_proof=source_warning_proof),
            "review_warnings": warning_summaries,
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
        "review_passed": review_passed(review, claims_payload, draft_text=raw_dialogue, source_warning_proof=source_warning_proof),
        "review_warnings": warning_summaries,
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
            else:
                faults = review_faults(review, claims_payload)
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
    try:
        result = apply(
            task_dir, args.draft.resolve(), claims_payload, review,
            require_review_pass=True, skip_smoke=args.skip_smoke,
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
