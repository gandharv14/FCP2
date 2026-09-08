"""Output retention: how many curated candidates survived, and why the rest did not.

Everything here is derived from ``curation.toml`` alone, so the record is a
pure function of the file that already fingerprints the generation. It carries
no timings or round numbers; those live in ``seg_out/<wb>/pruning-log.json``,
outside the immutable generation.

Marker vocabulary on the ``include`` line:

- ``include = false  # pruned: <code>[,<code>]`` -- written by ``xl_segment
  --prune-failing-outputs``; codes are verifier reason codes.
- ``include = false  # heuristic: true`` -- the LLM adjudicator overrode the
  heuristic (``adjudicate.apply_to_curation``).
- ``include = true  # fallback: ...`` -- the zero-include fallback rung.
- ``include = false  # excluded: <code> [evidence]`` -- a manual exclusion by
  the recovery agent; ``code`` must be one of ``MANUAL_EXCLUSION_CODES``.

An auto-included candidate (score at or above the file's threshold) that is
``include = false`` with none of these markers is ``unexplained``.
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA_VERSION = "output-retention/v1"
DEFAULT_THRESHOLD = 6.0

# Manual exclusion codes a recovery agent may write, and the evidence each
# needs (checked by the worker gate, described here for the prompt).
MANUAL_EXCLUSION_CODES = {
    "non_financial": "label, axis, date/year, unit or flag cell; cite bands.csv kind/vtype",
    "check_cell": "label matches the check-cell terms",
    "duplicate_value": "name the retained band that carries the same values",
    "answer_leak": "cite the disclosure or oracle record",
    "fairness_directed": "cite the fairness report path and the quoted line",
    "memory": "prior attempt ended in cgroup_oom / oom_kill > 0 (worker-verified)",
    "budget": "prior attempt timed out or proof exceeded half the inner budget (worker-verified)",
    "ast_scope": "cone crosses sheets dropped by --ast-sheet; cite unresolved cells",
    "cone_wide": "cite last-verification.json blocking reasons, as cone_wide:<code>",
}
# Codes that justify excluding more than the manual cap.
RESOURCE_OR_CONE_WIDE_CODES = frozenset({"memory", "budget", "ast_scope", "cone_wide"})
# Manual exclusions above max(MANUAL_CAP_MIN, MANUAL_CAP_FRACTION * auto-included)
# need one of the codes above.
MANUAL_CAP_MIN = 3
MANUAL_CAP_FRACTION = 0.25

_THRESHOLD_RE = re.compile(r"Auto-included at score >= ([0-9]+(?:\.[0-9]+)?)")
_ENTRY_RE = re.compile(r"^\[\[output\]\]\s*$")


def _unquote(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def read_entries(path: Path) -> tuple[float, list[dict]]:
    """``(threshold, entries)``; each entry keeps its include-line marker."""
    path = Path(path)
    threshold = DEFAULT_THRESHOLD
    entries: list[dict] = []
    current: dict | None = None
    if not path.is_file():
        return threshold, entries
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            found = _THRESHOLD_RE.search(line)
            if found and current is None:
                threshold = float(found.group(1))
            continue
        if _ENTRY_RE.match(line):
            current = {"band": None, "score": None, "include": None, "marker": None}
            entries.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key == "band":
            stripped = value.strip()
            if not stripped.startswith('"'):
                stripped = stripped.split("#", 1)[0]
            current["band"] = _unquote(stripped)
        elif key == "score":
            try:
                current["score"] = float(value.split("#", 1)[0].strip())
            except ValueError:
                current["score"] = None
        elif key == "include":
            flag, _, comment = value.partition("#")
            current["include"] = flag.strip() == "true"
            current["marker"] = _parse_marker(comment.strip())
    return threshold, entries


def _parse_marker(comment: str) -> dict | None:
    if not comment:
        return None
    if comment.startswith("pruned:"):
        codes = [c.strip() for c in comment[len("pruned:"):].split(",") if c.strip()]
        return {"kind": "pruned", "codes": codes}
    if comment.startswith("heuristic:"):
        return {"kind": "adjudicated", "codes": []}
    if comment.startswith("fallback:"):
        return {"kind": "fallback", "codes": []}
    if comment.startswith("excluded:"):
        rest = comment[len("excluded:"):].strip()
        code, _, note = rest.partition(" ")
        code = code.strip()
        base = code.split(":", 1)[0]
        return {"kind": "excluded", "codes": [code], "base": base, "note": note.strip()}
    return {"kind": "other", "codes": [], "note": comment}


def output_retention(path: Path) -> dict:
    """Deterministic retention record for one ``curation.toml``."""
    threshold, entries = read_entries(path)
    auto = [entry for entry in entries if (entry["score"] or 0.0) >= threshold]
    retained = [entry["band"] for entry in entries if entry["include"]]
    pruned, manual, adjudicated, unexplained = [], [], [], []
    for entry in auto:
        if entry["include"]:
            continue
        marker = entry["marker"] or {}
        kind = marker.get("kind")
        if kind == "pruned":
            pruned.append({"band": entry["band"], "reasons": sorted(marker.get("codes", []))})
        elif kind == "excluded":
            manual.append({
                "band": entry["band"],
                "code": marker["codes"][0] if marker.get("codes") else "",
                "base": marker.get("base", ""),
                "note": marker.get("note", ""),
            })
        elif kind == "adjudicated":
            adjudicated.append(entry["band"])
        else:
            unexplained.append(entry["band"])
    codes = sorted(
        {code for item in pruned for code in item["reasons"]}
        | {item["code"] for item in manual if item["code"]}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "threshold": threshold,
        "candidates_listed": len(entries),
        "candidates_auto_included": len(auto),
        "retained": len(retained),
        "pruned": len(pruned),
        "manual_excluded": len(manual),
        "adjudicated_excluded": len(adjudicated),
        "unexplained_excluded": len(unexplained),
        "reason_codes": codes,
        "bands": {
            "retained": retained,
            "pruned": pruned,
            "manual_excluded": manual,
            "adjudicated_excluded": adjudicated,
            "unexplained_excluded": unexplained,
        },
    }


def manual_cap(candidates_auto_included: int) -> int:
    return max(MANUAL_CAP_MIN, int(MANUAL_CAP_FRACTION * candidates_auto_included + 0.999999))


def gate_findings(record: dict, *, resource_evidence: dict | None = None) -> list[str]:
    """Why a retention record fails the worker gate; empty when it passes.

    ``resource_evidence`` may carry ``{"memory": bool, "budget": bool}`` from
    the worker's own attempt records; a ``memory``/``budget`` exclusion without
    the matching evidence is rejected.
    """
    findings: list[str] = []
    evidence = resource_evidence or {}
    bands = record.get("bands") or {}
    for band in bands.get("unexplained_excluded") or ():
        findings.append(f"{band}: auto-included candidate excluded without a marker")
    manual = list(bands.get("manual_excluded") or ())
    for item in manual:
        base = item.get("base") or ""
        if base not in MANUAL_EXCLUSION_CODES:
            findings.append(
                f"{item.get('band')}: exclusion code {item.get('code')!r} is not in the vocabulary"
            )
        elif base in ("memory", "budget") and not evidence.get(base):
            findings.append(
                f"{item.get('band')}: '{base}' exclusion has no matching attempt evidence"
            )
    cap = manual_cap(int(record.get("candidates_auto_included") or 0))
    if len(manual) > cap and not any(
        (item.get("base") or "") in RESOURCE_OR_CONE_WIDE_CODES for item in manual
    ):
        findings.append(
            f"{len(manual)} manual exclusions exceed the cap of {cap} without a "
            "memory/budget/ast_scope/cone_wide cause"
        )
    return findings
