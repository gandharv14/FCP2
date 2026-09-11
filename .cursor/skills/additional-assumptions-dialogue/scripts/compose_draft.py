#!/usr/bin/env python3
"""Deterministically render draft_template.md and slots.json for the dialogue writer.

The template fixes every structural line of the draft — claim comments,
speaker lines, first-mention sheet context, and blank-line layout. The
writer model may only replace the {{SLOT:<id>}} lines with prose. slots.json
maps each slot id to the must_say facts that prose must express, the sheet
context, and the claim it belongs to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from aa_lib import junior_scope_text, require_cast, write_json


TEMPLATE_NAME = "draft_template.md"
SLOTS_NAME = "slots.json"
SLOT_LINE_RE = re.compile(r"^\{\{SLOT:([A-Za-z0-9_-]+)\}\}$")
SLOT_COMMENT_RE = re.compile(r"(?m)^<!--\s*slot:.*-->[ \t]*\n?")


def slot_token(slot_id: str) -> str:
    return "{{SLOT:%s}}" % slot_id


def _comment_safe(text: str) -> str:
    return str(text or "").replace("-->", "->")


def strip_slot_scaffold(text: str) -> str:
    """Remove <!-- slot:... --> guidance lines; prose and claim comments stay."""
    cleaned = SLOT_COMMENT_RE.sub("", text)
    # Speaker headers are immutable in the fill template; delivery formatting
    # joins their prose without asking the writer to alter structural lines.
    return re.sub(r"(?m)^(\*\*[^\n]+:\*\*[^\n]*)\n(?=\S)(?!<!--|\*\*)", r"\1 ", cleaned)


def compose(claims_payload: dict, writer_pack: dict) -> tuple[str, dict]:
    """Render the draft's required structure from claims.json + writer_pack.json."""
    juniors, seniors = require_cast(claims_payload)
    claims = claims_payload.get("claims") or []
    if claims_payload.get("empty") or not claims:
        raise ValueError("empty agent_records: nothing to compose")
    pack_ids = [claim.get("record_id") for claim in (writer_pack.get("claims") or [])]
    claim_ids = [claim["record_id"] for claim in claims]
    if pack_ids != claim_ids:
        raise ValueError(
            "writer_pack.json claims do not match claims.json (order and "
            "record_ids must be identical); re-run extract_claims.py"
        )
    if any(c.get('junior_scope') != w.get('junior_scope')
           for c, w in zip(claims, writer_pack.get('claims') or [])):
        raise ValueError('writer pack changed exact junior cell scope')
    by_id = {claim['record_id']: claim for claim in claims}
    groups = [[identity] for identity in claim_ids]
    shared = claims_payload.get('shared_source_links')
    if shared is not None:
        if shared != writer_pack.get('shared_source_links') or shared.get('schema_version') != 'dialogue-shared-source-links/v1':
            raise ValueError('shared source-link proof differs from writer pack')
        groups = shared['groups']
        flattened = [identity for group in groups for identity in group]
        if sorted(flattened) != sorted(claim_ids) or not all(groups):
            raise ValueError('shared exchanges must cover every original claim exactly once')
        for group in groups:
            if any(any(by_id[identity].get(key) != by_id[group[0]].get(key)
                       for key in ('sheet', 'row_label', 'spoken', 'must_say')) for identity in group):
                raise ValueError('shared exchange changes a source-link rule')

    lines: list[str] = []
    slots: dict[str, dict] = {}
    slot_order: list[str] = []
    sheets_seen: set[str] = set()
    for index, group in enumerate(groups, 1):
        claim = by_id[group[0]]
        junior = juniors[(index - 1) % len(juniors)]
        senior = seniors[(index - 1) % len(seniors)]
        record_id = claim["record_id"]
        sheet = claim.get("sheet") or ""
        row_label = claim.get("row_label") or ""
        must_say = [str(atom) for atom in (claim.get("must_say") or [])]
        first_mention = bool(sheet) and sheet not in sheets_seen
        if first_mention:
            sheets_seen.add(sheet)
        if first_mention:
            sheet_context = "introduced_by_template"
            sheet_note = (
                "the %s tab is named for you in the junior question above; "
                "do not repeat it" % sheet
            )
        elif sheet:
            sheet_context = "already_established"
            sheet_note = (
                "the %s tab is already established above; never open with "
                '"On %s" / "For %s" / "In %s"' % (sheet, sheet, sheet, sheet)
            )
        else:
            sheet_context = ""
            sheet_note = ""

        junior_id = "c%03d-junior" % index
        senior_id = "c%03d-senior" % index
        junior_guidance = (
            'ask how to build the row labelled "%s"; prose only, no new '
            "modelling facts" % row_label
        )
        senior_guidance = (
            sheet_note or "answer covers every must_say fact and the row label"
        )
        senior_bits = [
            "must_say: " + ("; ".join(must_say) if must_say else "(none)")
        ]
        if sheet_note:
            senior_bits.append(sheet_note)
        senior_header = "**%s:**" % senior
        junior_header = "**%s:**" % junior
        if first_mention:
            junior_header += " " + sheet + ":"
        scoped = [junior_scope_text(by_id[identity]) for identity in group
                  if by_id[identity].get("junior_scope")]
        if scoped:
            junior_header += " " + "; ".join(scoped) + ":"

        lines.extend([
            *("<!-- claim:%s -->" % identity for identity in group),
            "<!-- slot:%s | %s -->" % (junior_id, _comment_safe(junior_guidance)),
            junior_header,
            slot_token(junior_id),
            "",
            "<!-- slot:%s | %s -->" % (senior_id, _comment_safe(" | ".join(senior_bits))),
            senior_header,
            slot_token(senior_id),
            "",
        ])
        for slot_id, kind, speaker, facts, guidance in (
            (junior_id, "junior", junior, [], junior_guidance),
            (senior_id, "senior", senior, must_say, senior_guidance),
        ):
            slots[slot_id] = {
                "kind": kind,
                "speaker": speaker,
                "claim_ids": group,
                "row_label": row_label,
                "sheet": sheet,
                "sheet_context": sheet_context,
                "must_say": facts,
                "guidance": guidance,
            }
            slot_order.append(slot_id)

    template = "\n".join(lines)
    manifest = {
        "schema_version": "1.0",
        "template": TEMPLATE_NAME,
        "junior_titles": list(juniors),
        "senior_titles": list(seniors),
        "slot_order": slot_order,
        "slots": slots,
    }
    return template, manifest


def migrate_scaffold(previous_template, previous_filled, previous_slots, template, slots):
    """Carry only validated authored slot bytes across a scaffold code repair."""
    from validate_dialogue import fill_faults
    faults = fill_faults(previous_template, previous_filled)
    if faults:
        raise ValueError("previous filled template is invalid: " + faults[0])
    order = slots["slot_order"]
    if previous_slots.get("slot_order") != order or len(order) != len(set(order)):
        raise ValueError("scaffold migration changed slot order or identity")
    for slot_id in order:
        for key in ("kind", "speaker", "claim_ids", "row_label", "sheet", "must_say"):
            if previous_slots["slots"][slot_id].get(key) != slots["slots"][slot_id].get(key):
                raise ValueError("scaffold migration changed claim content: " + slot_id)
    tokens = re.compile(r"(\{\{SLOT:[A-Za-z0-9_-]+\}\})")
    parts = tokens.split(previous_template)
    old_order = [part[7:-2] for part in parts if tokens.fullmatch(part)]
    if old_order != order:
        raise ValueError("previous template does not match slot manifest")
    pattern = "".join("(.*?)" if tokens.fullmatch(part) else re.escape(part) for part in parts)
    match = re.fullmatch(pattern, previous_filled, re.S)
    if match is None:
        raise ValueError("cannot extract exact authored slot bytes")
    fills = dict(zip(order, match.groups()))
    rewritten = tokens.sub(lambda token: fills[token.group()[7:-2]], template)
    faults = fill_faults(template, rewritten)
    if faults:
        raise ValueError("migrated filled template is invalid: " + faults[0])
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return rewritten, {
        "schema_version": "dialogue-scaffold-migration/v1",
        "previous_template_sha256": digest(previous_template),
        "previous_filled_sha256": digest(previous_filled),
        "template_sha256": digest(template), "filled_sha256": digest(rewritten),
        "slot_prose_sha256": {slot_id: digest(fills[slot_id]) for slot_id in order},
        "authored_slot_bytes_preserved": True,
        "fresh_mechanical_and_semantic_review_required": True,
    }


def assemble_slots(template, slots, payload):
    """Render model-authored prose into code-owned structure without trimming."""
    from validate_dialogue import fill_faults
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "slots"}
            or payload["schema_version"] != "dialogue-slot-prose/v1"
            or not isinstance(payload["slots"], dict)
            or set(payload["slots"]) != set(slots["slot_order"])
            or any(not isinstance(value, str) or not value.strip()
                   for value in payload["slots"].values())):
        raise ValueError("slot prose must cover every template slot exactly once")
    filled = re.sub(r"\{\{SLOT:([A-Za-z0-9_-]+)\}\}",
                    lambda match: payload["slots"][match.group(1)], template)
    faults = fill_faults(template, filled)
    if faults:
        raise ValueError("assembled slot prose is invalid: " + faults[0])
    return filled


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in slot prose: " + key)
        result[key] = value
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument(
        "--out", required=True, type=Path,
        help="run directory for draft_template.md + slots.json",
    )
    parser.add_argument("--previous-template", type=Path)
    parser.add_argument("--previous-filled", type=Path)
    parser.add_argument("--previous-slots", type=Path)
    parser.add_argument("--previous-claims", type=Path)
    parser.add_argument("--previous-pack", type=Path)
    parser.add_argument("--slot-values", type=Path,
                        help="assemble JSON slot prose; all claim and semantic checks still apply")
    args = parser.parse_args(argv)
    previous = (args.previous_template, args.previous_filled, args.previous_slots,
                args.previous_claims, args.previous_pack)
    if args.slot_values and any(previous):
        parser.error("slot assembly and scaffold migration are separate operations")
    if any(previous) and not all(previous):
        parser.error("scaffold migration requires all five previous artifacts")
    if args.previous_claims and (
            args.previous_claims.read_bytes() != args.claims.read_bytes()
            or args.previous_pack.read_bytes() != args.pack.read_bytes()):
        parser.error("scaffold migration requires byte-identical claim and writer-pack evidence")
    claims_payload = json.loads(args.claims.read_text(encoding="utf-8"))
    writer_pack = json.loads(args.pack.read_text(encoding="utf-8"))
    try:
        template, manifest = compose(claims_payload, writer_pack)
    except ValueError as exc:
        print("FAIL:", exc, file=sys.stderr)
        return 2
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / TEMPLATE_NAME).write_text(template, encoding="utf-8")
    write_json(out / SLOTS_NAME, manifest)
    if args.slot_values:
        payload = json.loads(args.slot_values.read_text(), object_pairs_hook=_unique_json_object)
        filled = assemble_slots(template, manifest, payload)
        (out / "assembled.filled.md").write_text(filled, encoding="utf-8")
        write_json(out / "slot-assembly.json", {
            "schema_version": "dialogue-slot-assembly/v1",
            "slot_values_sha256": hashlib.sha256(args.slot_values.read_bytes()).hexdigest(),
            "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
            "filled_sha256": hashlib.sha256(filled.encode()).hexdigest(),
            "claims_sha256": hashlib.sha256(args.claims.read_bytes()).hexdigest(),
            "writer_pack_sha256": hashlib.sha256(args.pack.read_bytes()).hexdigest(),
            "fresh_mechanical_and_semantic_review_required": True,
        })
    if args.previous_template:
        filled, proof = migrate_scaffold(args.previous_template.read_text(),
            args.previous_filled.read_text(), json.loads(args.previous_slots.read_text()),
            template, manifest)
        proof["claims_sha256"] = hashlib.sha256(args.claims.read_bytes()).hexdigest()
        proof["writer_pack_sha256"] = hashlib.sha256(args.pack.read_bytes()).hexdigest()
        (out / "migrated.filled.md").write_text(filled, encoding="utf-8")
        write_json(out / "scaffold-migration.json", proof)
    print(
        "%d claim(s), %d slot(s) -> %s"
        % (len(claims_payload.get("claims") or []), len(manifest["slots"]), out / TEMPLATE_NAME)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
