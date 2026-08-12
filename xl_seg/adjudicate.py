"""Optional LLM pass over the output shortlist.

Off by default. The heuristic ranking already writes ``curation.toml``; this only
rewrites the ``include`` flags and gives the outputs human-readable names. It
reads the same file it writes, so the deterministic and adjudicated paths stay
interchangeable and nothing downstream needs to know which one ran.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

PROMPT = """You are auditing a financial model that has been parsed into a dependency graph.

Below are candidate OUTPUT line items -- the values a reader of this model would
consider its conclusions (valuations, returns, headline totals), as opposed to the
intermediate steps used to reach them.

Select the ones that are genuine headline outputs and give each a short, clear name.
Reject unit stamps, date axes, intermediate subtotals and duplicated presentation copies.

Workbook: {wb}

Candidates:
{rows}

Reply with JSON only, no prose:
{{"outputs": [{{"band": "<band id>", "include": true, "name": "<short name>"}}]}}
Include an entry for every candidate listed above."""


def normalize_band(value) -> str:
    """Recover a candidate id when the model echoes the ``band=`` prefix."""
    band = str(value or "").strip().strip("`").strip()
    if band.lower().startswith("band="):
        band = band.split("=", 1)[1].strip()
    return band.strip("`").strip()


def read_key(env_file: Path) -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "anthropic_api_key":
            return value.strip().strip("'\"")
    return ""


def adjudicate(wb: str, candidates, api_key: str, model=DEFAULT_MODEL, timeout=180):
    rows = "\n".join(
        f"- band={c.band} | sheet={c.sheet} | label={c.label!r} | score={c.score} | "
        f"sink={c.features['sink']} | dashboard_copies={c.features['mirror_fanin']} | "
        f"collapses_timeseries={c.features['scalar_collapse']} | depth={c.features['depth']}"
        for c in candidates
    )
    body = {
        "model": model,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": PROMPT.format(wb=wb, rows=rows)}],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = "".join(block.get("text", "") for block in payload.get("content", []))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("adjudicator returned no JSON")
    parsed = json.loads(text[start : end + 1])
    decisions = {
        normalize_band(entry.get("band")): (
            bool(entry.get("include")),
            entry.get("name", ""),
        )
        for entry in parsed.get("outputs", [])
        if normalize_band(entry.get("band"))
    }
    expected = {candidate.band for candidate in candidates}
    missing = sorted(expected - set(decisions))
    if missing:
        raise ValueError(
            "adjudicator omitted or changed %d candidate band(s): %s"
            % (len(missing), ", ".join(missing[:5]))
        )
    return decisions


def apply_to_curation(path: Path, decisions: dict) -> int:
    """Rewrite include/name in place, keeping the heuristic scores as comments."""
    if not decisions or not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    out, band, changed = [], None, 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("band = "):
            band = stripped[len("band = ") :].strip().strip('"')
        if band in decisions:
            include, name = decisions[band]
            if stripped.startswith("include = "):
                previous = stripped.split("=", 1)[1].strip()
                new = "true" if include else "false"
                if previous != new:
                    changed += 1
                out.append(f"include = {new}  # heuristic: {previous}")
                continue
            if stripped.startswith("name = ") and name:
                out.append(f'name = "{name}"')
                continue
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed
