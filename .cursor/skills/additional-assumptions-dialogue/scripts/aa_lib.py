#!/usr/bin/env python3
"""Shared helpers for additional-assumptions claim cards and dialogue checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


NOTES_NAME = "additional-assumptions.md"
NOTES_COPY = f"COPY {NOTES_NAME} /app/{NOTES_NAME}"
APPLIED_MARKER = "tests/dialogue-applied.json"
DO_NOT_RERUN = (
    "create-harbor-task",
    "disclose.py write",
    "disclose.py verify",
    "plain_eligibility.check_plain_environment",
    "xl_mcp_oracle.check_environment",
    "xl_harbor_prep.py",
    "xl_output_task.py",
)
DISCLOSURE_HEADING = "## Workbook disclosure"
ASSUMPTIONS_HEADING = "## Additional assumptions"
OUTPUT_HEADING = "## Output"
INPUT_HEADING = "## Input"
INVESTOR_FAMILIES = frozenset({"lbo_returns", "fund_pe"})
JUNIOR_TITLES = ("Analyst", "Associate")
SENIOR_TITLES = ("VP", "Director", "Managing Director")
ALLOWED_TITLES = JUNIOR_TITLES + SENIOR_TITLES
LEGACY_TITLES = ("Senior banker", "Senior investor")
TITLE_CANON = {
    "analyst": "Analyst",
    "associate": "Associate",
    "vp": "VP",
    "vice president": "VP",
    "director": "Director",
    "managing director": "Managing Director",
    "senior banker": "Senior banker",
    "senior investor": "Senior investor",
}
YEARISH_COLS = frozenset({"FY", "CY", "QY", "H", "Y", "Q"})
STOPWORDS = frozenset({
    "the", "a", "an", "on", "of", "in", "to", "for", "and", "or", "is", "are",
    "this", "that", "it", "its", "as", "from", "with", "by", "be", "than",
    "rather", "row", "labelled", "labeled", "describes", "describe",
    "books", "book",
})

HEADING_RE = re.compile(r"(?m)^(#{1,6}\s+.+)$")
SHEET_CELL_RE = re.compile(
    r"(?:cell |range )?"
    r"(?:'[^']+'|[A-Za-z0-9_][A-Za-z0-9_ .&-]*)!"
    r"\$?[A-Z]{1,3}\$?\d{1,7}"
    r"(?::\$?[A-Z]{1,3}\$?\d{1,7})?",
    re.I,
)
BARE_A1_RE = re.compile(
    r"(?<![A-Za-z0-9])(\$?[A-Z]{1,3}\$?\d{1,7})(?::(\$?[A-Z]{1,3}\$?\d{1,7}))?(?![A-Za-z0-9])"
)
SPEAKER_PREFIX_RE = re.compile(
    r"^\*{0,2}([A-Za-z][A-Za-z0-9 .&/-]{0,40})\*{0,2}:\s*(.*)$"
)
SKIP_PREFIXES = frozenset({"http", "https", "ftp", "mailto"})
CLAIM_COMMENT_RE = re.compile(r"<!--\s*claim:([^>]+?)\s*-->")
TAXONOMY_RE = re.compile(r'(?m)^taxonomy_primary\s*=\s*"([^"]+)"')
DOCKER_IMAGE_RE = re.compile(r'(?m)^docker_image\s*=')
HASH_RE = re.compile(r'(?m)^(instruction_sha256\s*=\s*")[0-9a-f]+(")')
LEFTOVER_BULLET_RE = re.compile(
    r"(?m)^-\s+`?(?:'[^']+'|[A-Za-z0-9_ .&-]+)!\$?[A-Z]{1,3}"
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def humanize_token(token: str) -> str:
    return re.sub(r"_+", " ", str(token or "")).strip()


def senior_title(taxonomy_primary: str) -> str:
    # Legacy helper. New packs use junior_titles / senior_titles.
    if (taxonomy_primary or "").strip() in INVESTOR_FAMILIES:
        return "Senior investor"
    return "Senior banker"


def canonicalize_title(raw: str) -> str | None:
    key = re.sub(r"\s+", " ", str(raw or "").strip()).lower()
    return TITLE_CANON.get(key)


def is_junior(speaker: str) -> bool:
    return speaker in JUNIOR_TITLES


def is_senior(speaker: str) -> bool:
    return speaker in SENIOR_TITLES


def require_cast(payload: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if "senior_title" in payload and "junior_titles" not in payload:
        raise ValueError("stale claims pack; re-run extract_claims.py")
    juniors = tuple(payload.get("junior_titles") or JUNIOR_TITLES)
    seniors = tuple(payload.get("senior_titles") or SENIOR_TITLES)
    if not juniors or not seniors:
        raise ValueError("claims pack missing junior_titles / senior_titles")
    return juniors, seniors


def taxonomy_from_task_toml(task_dir: Path) -> str:
    path = task_dir / "task.toml"
    if not path.is_file():
        return ""
    match = TAXONOMY_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def clean_label(raw: str) -> str:
    text = str(raw or "").strip()
    text = text.strip("\"'")
    text = re.sub(r"^Row labelled\s+", "", text, flags=re.I)
    text = text.strip("\"'")
    text = re.split(r";", text, maxsplit=1)[0]
    return text.strip(" \"'")


def sheet_from_cells(cells: list) -> str:
    for cell in cells or []:
        text = str(cell).strip().strip("`")
        if "!" not in text:
            continue
        sheet, _ = text.rsplit("!", 1)
        return sheet.strip().strip("'")
    return ""


def strip_cell_refs(text: str) -> str:
    out = SHEET_CELL_RE.sub(" ", str(text or ""))
    pieces = []
    last = 0
    for match in BARE_A1_RE.finditer(out):
        col = re.sub(r"[\d$:]", "", match.group(1))
        if col.upper() in YEARISH_COLS and not match.group(2):
            continue
        pieces.append(out[last:match.start()])
        last = match.end()
    pieces.append(out[last:])
    out = "".join(pieces)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+,", ",", out)
    return out.strip(" \t:-")


def cell_refs_in(text: str) -> list[str]:
    found = [match.group(0).strip() for match in SHEET_CELL_RE.finditer(text or "")]
    for match in BARE_A1_RE.finditer(text or ""):
        col = re.sub(r"[\d$:]", "", match.group(1))
        if col.upper() in YEARISH_COLS and not match.group(2):
            continue
        found.append(match.group(0))
    return found


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def clause_covered(clause: str, haystack: str) -> bool:
    needle = normalize(clause)
    blob = normalize(haystack)
    if not needle:
        return True
    if needle in blob:
        return True
    tokens = [_stem(tok) for tok in needle.split() if tok not in STOPWORDS and len(tok) > 2]
    hay = {_stem(tok) for tok in blob.split()}
    return bool(tokens) and all(tok in hay for tok in tokens)


def split_must_say(sentence: str) -> list[str]:
    cleaned = strip_cell_refs(sentence)
    parts = re.split(r"(?<=[.;])\s+", cleaned)
    out = []
    seen = set()
    for part in parts:
        clause = part.strip(" .;")
        if len(normalize(clause)) < 8:
            continue
        key = normalize(clause)
        if key in seen:
            continue
        seen.add(key)
        out.append(clause)
    return out


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[match.start():end]))
    return preamble, sections


def section_body(sections: list[tuple[str, str]], heading: str) -> str | None:
    target = heading.strip().lower()
    for name, body in sections:
        if name.strip().lower() == target:
            return body
    return None


def strip_heading_block(text: str, heading: str) -> str:
    while heading in text:
        head, rest = text.split(heading, 1)
        parts = rest.split("\n## ", 1)
        text = head + (("## " + parts[1]) if len(parts) > 1 else "")
    return text


def parse_turns(dialogue: str) -> list[dict]:
    lines = (dialogue or "").splitlines()
    turns = []
    current = None
    pending_claim = None
    for line in lines:
        comment = CLAIM_COMMENT_RE.search(line)
        if comment:
            pending_claim = comment.group(1).strip()
        stripped = CLAIM_COMMENT_RE.sub("", line).rstrip()
        match = SPEAKER_PREFIX_RE.match(stripped)
        if match:
            role = match.group(1).strip()
            if role.lower() in SKIP_PREFIXES:
                if current:
                    extra = stripped.strip()
                    if extra:
                        current["text"] = (current["text"] + " " + extra).strip()
                continue
            if current:
                current["text"] = current["text"].strip()
                turns.append(current)
            speaker = canonicalize_title(role) or role
            current = {
                "speaker": speaker,
                "text": match.group(2).strip(),
                "record_id": pending_claim,
                "allowed": speaker in ALLOWED_TITLES,
            }
            pending_claim = None
            continue
        if current:
            extra = stripped.strip()
            if extra:
                current["text"] = (current["text"] + " " + extra).strip()
    if current:
        current["text"] = current["text"].strip()
        turns.append(current)
    return turns


def strip_claim_comments(text: str) -> str:
    out = CLAIM_COMMENT_RE.sub("", text)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n"


def disclosure_body(instruction: str) -> str:
    if DISCLOSURE_HEADING not in instruction:
        return ""
    return DISCLOSURE_HEADING + instruction.split(DISCLOSURE_HEADING, 1)[1].split("\n## ", 1)[0]


def artifact_name(task_dir: Path) -> str:
    env = task_dir / "environment"
    books = sorted(
        path for path in env.glob("*.xls*")
        if path.is_file() and path.suffix.casefold() in {".xlsx", ".xlsm"}
    )
    if len(books) != 1:
        raise ValueError("expected exactly one workbook in environment/")
    return books[0].name
