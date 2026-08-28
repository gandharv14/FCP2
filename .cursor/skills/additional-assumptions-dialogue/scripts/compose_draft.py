#!/usr/bin/env python3
"""Deterministically render draft_template.md and slots.json for the dialogue writer.

The template fixes every structural line of the draft — claim comments,
speaker lines, first-mention sheet sentences, and blank-line layout. The
writer model may only replace the {{SLOT:<id>}} lines with prose. slots.json
maps each slot id to the must_say facts that prose must express, the sheet
context, and the claim it belongs to.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from aa_lib import require_cast, write_json


TEMPLATE_NAME = "draft_template.md"
SLOTS_NAME = "slots.json"
# A complete sentence (not an "On {sheet}," opener) so it can never become a
# redundant sheet lead-in even when the junior prose also names the tab.
SHEET_INTRO = "This one lives on the {sheet} tab."
SLOT_LINE_RE = re.compile(r"^\{\{SLOT:([A-Za-z0-9_-]+)\}\}$")
SLOT_COMMENT_RE = re.compile(r"(?m)^<!--\s*slot:.*-->[ \t]*\n?")


def slot_token(slot_id: str) -> str:
    return "{{SLOT:%s}}" % slot_id


def _comment_safe(text: str) -> str:
    return str(text or "").replace("-->", "->")


def strip_slot_scaffold(text: str) -> str:
    """Remove <!-- slot:... --> guidance lines; prose and claim comments stay."""
    return SLOT_COMMENT_RE.sub("", text)


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

    lines: list[str] = []
    slots: dict[str, dict] = {}
    slot_order: list[str] = []
    sheets_seen: set[str] = set()
    for index, claim in enumerate(claims, 1):
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
                "the %s tab is named for you in the speaker line above; "
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
        if first_mention:
            senior_header += " " + SHEET_INTRO.format(sheet=sheet)

        lines.extend([
            "<!-- claim:%s -->" % record_id,
            "<!-- slot:%s | %s -->" % (junior_id, _comment_safe(junior_guidance)),
            "**%s:**" % junior,
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
                "claim_ids": [record_id],
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument(
        "--out", required=True, type=Path,
        help="run directory for draft_template.md + slots.json",
    )
    args = parser.parse_args(argv)
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
    print(
        "%d claim(s), %d slot(s) -> %s"
        % (len(claims_payload.get("claims") or []), len(manifest["slots"]), out / TEMPLATE_NAME)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
