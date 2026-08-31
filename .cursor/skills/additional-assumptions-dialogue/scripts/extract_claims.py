#!/usr/bin/env python3
"""Build claim cards for the additional-assumptions dialogue writer."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from aa_lib import (
    JUNIOR_TITLES,
    SENIOR_TITLES,
    clean_label,
    disclosure_body,
    humanize_token,
    sheet_from_cells,
    strip_cell_refs,
    write_json,
)
from spoken_formula import needs_spoken, speak_record


LABEL_RE = re.compile(r'"([^"]+)"')
SOURCE_TAB_RE = re.compile(
    r"\bon the ([A-Za-z0-9][A-Za-z0-9 _&.'()/-]*?) tab\b",
    re.I,
)
LOCATOR_ATOMS = (
    "last period",
    "this period",
    "next period",
    "locked input",
)
OPERATOR_ATOMS = (
    "smaller",
    "greater",
    "at least",
    "at most",
    "floor",
    "flip",
    "minus",
    "plus",
    "average",
    "held flat",
    "lower bound",
    "upper bound",
    "result locked input",
    "otherwise",
    "errors",
)
NON_LABEL_LITERALS = frozenset({"", "N/A", "#N/A", "NA"})


SKILL_ROOT = Path(__file__).resolve().parents[1]
DISCLOSE_SCRIPTS = SKILL_ROOT.parent / "task-disclosure" / "scripts"
if str(DISCLOSE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DISCLOSE_SCRIPTS))

import disclose  # noqa: E402


def load_disclosure(task_dir: Path) -> dict:
    path = task_dir / "tests" / "disclosure.json"
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def writer_disclosure_body(records: list[dict], raw_body: str) -> str:
    lines = [
        "These are modelling assumptions the seniors already know.",
        "They describe how the new model should work, not the figures you are asked to report.",
        "",
    ]
    if records:
        for rec in records:
            line = rec.get("spoken") or rec.get("rendered") or ""
            lines.append(f"- On `{rec['sheet']}`, the row labelled \"{rec['row_label']}\": {line}")
        return "\n".join(lines).rstrip() + "\n"
    return strip_cell_refs(raw_body).rstrip() + "\n"


def must_say_atoms(spoken: str, sheet: str = "", row_label: str = "") -> list[str]:
    """Fact atoms only. Never the spoken paragraph or 'copied across the forecast'."""
    blob = spoken or ""
    low = blob.lower()
    out: list[str] = []
    seen: set[str] = set()

    def add(atom: str) -> None:
        text = (atom or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen or "copied across the forecast" in key:
            return
        seen.add(key)
        out.append(text)

    if row_label:
        add(f'the row labelled "{row_label}"')
    for label in LABEL_RE.findall(blob):
        if label.strip() in NON_LABEL_LITERALS:
            continue
        add(f'the row labelled "{label}"')
    for tab in SOURCE_TAB_RE.findall(blob):
        if tab.strip().casefold() != sheet.strip().casefold():
            add(f"the {tab.strip()} tab")
    for atom in LOCATOR_ATOMS:
        if atom in low:
            add(atom)
    for atom in OPERATOR_ATOMS:
        if atom in low:
            add(atom)
    return out


def build_claim(record: dict, index: int) -> dict:
    fields = record.get("fields") or {}
    row_label = clean_label(fields.get("label") or record.get("label") or "")
    sheet = sheet_from_cells(record.get("cells") or [])
    rendered = disclose.render_sentence(record)
    if not rendered:
        raise SystemExit(f"record {index} has no renderable sentence")
    raw_steps = str(fields.get("steps") or "")
    if needs_spoken(record) or re.search(r"\b(?:copied-column|cell |range )", raw_steps + " " + rendered, re.I):
        spoken = speak_record(record)
        if not spoken:
            spoken = strip_cell_refs(rendered)
            spoken = re.sub(r"\bFor\s+on the row\b", "On the row", spoken)
        must_say = must_say_atoms(
            spoken + "\n" + raw_steps,
            sheet,
            row_label,
        )
    else:
        spoken = strip_cell_refs(rendered)
        spoken = re.sub(r"\bFor\s+on the row\b", "On the row", spoken)
        must_say = must_say_atoms(spoken, sheet, row_label)
    entry = disclose.registry().get(record.get("entry") or "", {})
    alternatives = [
        humanize_token(token)
        for token in (record.get("alternatives") or entry.get("alternatives") or [])
        if humanize_token(token)
    ]
    record_id = "%s::%s::%s::%03d" % (
        record.get("entry") or f"rec{index:03d}",
        sheet or "sheet",
        row_label or f"row{index:03d}",
        index,
    )
    return {
        "record_id": record_id,
        "sheet": sheet,
        "row_label": row_label,
        "question": (entry.get("question") or "").strip(),
        "must_say": must_say,
        "alternatives": alternatives,
        "chosen_alternative": humanize_token(record.get("value") or ""),
        "spoken": spoken,
        "reviewer_only": {
            "band": record.get("band") or "",
            "cells": list(record.get("cells") or []),
            "entry": record.get("entry") or "",
            "value": record.get("value") or "",
            "source": record.get("source") or "",
            "steps": raw_steps,
            "evidence": record.get("evidence") or (record.get("method_profile") or {}).get("formula") or "",
            "rendered": rendered,
        },
    }


def writer_claim(claim: dict) -> dict:
    keep = (
        "record_id",
        "sheet",
        "row_label",
        "question",
        "must_say",
        "alternatives",
        "chosen_alternative",
        "spoken",
    )
    return {key: claim[key] for key in keep if key in claim}


def extract(task_dir: Path) -> dict:
    payload = load_disclosure(task_dir)
    shipped = list(payload.get("agent_records") or [])
    instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
    claims = [build_claim(record, index) for index, record in enumerate(shipped, 1)]
    raw_body = disclosure_body(instruction)
    body = writer_disclosure_body(claims, raw_body)
    juniors = list(JUNIOR_TITLES)
    seniors = list(SENIOR_TITLES)
    return {
        "schema_version": "1.1",
        "task": task_dir.name,
        "workbook": task_dir.name.split("-")[0],
        "junior_titles": juniors,
        "senior_titles": seniors,
        "empty": not claims,
        "claims": claims,
        "disclosure_body": body,
        "writer_pack": {
            "junior_titles": juniors,
            "senior_titles": seniors,
            "disclosure_body": body,
            "claims": [writer_claim(claim) for claim in claims],
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="run directory for claims.json")
    args = parser.parse_args(argv)
    task_dir = args.task_dir.resolve()
    if not (task_dir / "instruction.md").is_file():
        raise SystemExit(f"not a task bundle: {task_dir}")
    payload = extract(task_dir)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "claims.json", payload)
    write_json(out / "writer_pack.json", payload["writer_pack"])
    print("%s: %d claim(s) -> %s" % (payload["task"], len(payload["claims"]), out / "claims.json"))
    if payload["empty"]:
        print("empty agent_records: no-op")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
