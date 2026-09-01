"""Import a variable-source Markdown table into a draft review queue.

Every row of every table whose header names a variable, a value and a source
is preserved verbatim as one draft entry with ``normalization_status:
needs_review``. Nothing is dropped: rows that are later excluded from the MCP
environment keep an audit trail in the draft.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

URL_RE = re.compile(r"https?://[^\s;|)]+")


def clean(text: str) -> str:
    return re.sub(r"[`*]", "", text).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", clean(text).lower()).strip("-")[:80] or "variable"


def cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def import_table(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    asset = ""
    for line in lines:
        match = re.match(r"\*\*Asset:\*\*\s*(.+)", line)
        if match:
            asset = clean(match.group(1))
            break
    if not asset:
        title = next((line[2:] for line in lines if line.startswith("# ")), "")
        asset = clean(title)

    headers = [
        index
        for index, line in enumerate(lines)
        if line.startswith("|")
        and ("variable" in line.lower() or "input" in line.lower())
        and "value" in line.lower()
        and "source" in line.lower()
    ]
    if not headers:
        raise ValueError(
            "Could not find a Markdown table with variable, value, and source columns")

    rows = []
    id_counts: dict[str, int] = {}
    for header in headers:
        headings = [clean(value).lower() for value in cells(lines[header])]
        variable_col = next(index for index, value in enumerate(headings)
                            if "variable" in value or "input" in value)
        value_col = next(index for index, value in enumerate(headings)
                         if "value" in value)
        source_col = next(index for index, value in enumerate(headings)
                          if "source" in value)
        url_col = next((index for index, value in enumerate(headings)
                        if "url" in value), source_col)
        for line in lines[header + 2:]:
            if not line.startswith("|"):
                break
            row = cells(line)
            if len(row) <= max(variable_col, value_col, source_col, url_col):
                continue
            variable = clean(row[variable_col])
            base_id = slug(variable)
            id_counts[base_id] = id_counts.get(base_id, 0) + 1
            draft_id = base_id if id_counts[base_id] == 1 else (
                "%s-%d" % (base_id, id_counts[base_id]))
            urls = [url.rstrip(".,") for url in URL_RE.findall(row[url_col])]
            rows.append({
                "draft_id": draft_id,
                "variable_text": variable,
                "value_text": clean(row[value_col]),
                "source_notes": clean(row[source_col]),
                "source_urls": urls,
                "normalization_status": "needs_review",
            })

    if not rows:
        raise ValueError("The detected variable table contained no data rows")
    return {
        "input_file": str(path.resolve()),
        "asset": asset,
        "row_count": len(rows),
        "rows": rows,
    }
