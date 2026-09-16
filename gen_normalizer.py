#!/usr/bin/env python3
"""Generate a per-workbook atomic normalizer from a reviewed variable-source audit.

Applies the review rules established on the 0438 pilot:

* each referenced cell is assessed independently; retained typed inputs require
  a hash-bound semantic review of research eligibility and financial dimensions;
* a value that also appears in an uncovered typed cell is a leak.  The leak is
  covered with ``extra_cells`` only when the duplicate sits on a data-validation
  or picklist sheet, which carries no modelling content.  Any other leak excludes
  the variable outright, because partial masking is forbidden;
* a multi-cell audit row is split into one atomic variable per cell.

The emitted script writes ``normalized.json``, ``exclusions.json`` and
``normalization_report.json`` atomically and is byte-stable across re-runs.
"""

from __future__ import annotations

import argparse
import collections
import copy
import decimal
import hashlib
import json
import math
import os
import pprint
import re
import sys
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string as ci, get_column_letter as gl

from xl_artifact_paths import resolve_workbook_artifact

# Locate !A1 / !A1:B2, then take the sheet name immediately before the bang.
# Excel sheet names are at most 31 characters and cannot contain \ / ? * [ ] :
CELL = r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?"
CELL_AT = re.compile(r"!(" + CELL + r")")
SHEET_BREAK = re.compile(r"`|<br\s*/?>|[\n;]", re.I)
PICKLIST = re.compile(r"data\s*validation|validation|dropdown|^lists?$|picklist"
                      r"|embedded\s*assumptions", re.I)

CAUSE_PRIORITY = (
    "unparsable_reference",
    "unknown_sheet",
    "formula_cell",
    "blank_in_baseline",
    "duplicate_value_leak",
    "forced_exclusion",
)

CAUSE_PROSE = {
    "unparsable_reference": (
        "No workbook cell reference could be parsed from the audit row."
    ),
    "unknown_sheet": (
        "Audit row references a sheet that does not exist in the golden workbook."
    ),
    "formula_cell": (
        "Audit row references a formula cell; it cannot be masked as a typed input."
    ),
    "blank_in_baseline": (
        "Audit row references a cell already blank in the baseline inputs; "
        "it cannot be masked as a typed input."
    ),
    "duplicate_value_leak": (
        "Value also occurs in typed cell(s) that carry unrelated modelling "
        "content, so the variable cannot be masked completely."
    ),
    "forced_exclusion": (
        "The delivered bundle exposes this value in a packager-generated "
        "cell that cannot be masked without removing task disclosure, so "
        "the variable is excluded (oracle feedback)."
    ),
}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_refs(text, sheets=None):
    """Return ``Sheet!A1`` / ``Sheet!A1:B2`` strings from audit prose."""
    refs = []
    seen = set()
    keys = {k.strip().upper(): k for k in (sheets or {})}
    for match in CELL_AT.finditer(text or ""):
        cell = match.group(1)
        prefix = text[: match.start()]
        quoted = re.search(r"'((?:[^']|'')+)'\s*$", prefix)
        if quoted:
            sheet = quoted.group(1).replace("''", "'")
        else:
            chunk = SHEET_BREAK.split(prefix)[-1]
            sheet = chunk[-31:].strip(" \t\"=[")
        if not sheet or not cell:
            continue
        if keys:
            upper = sheet.strip().upper()
            if upper not in keys:
                resolved = None
                for length in range(len(upper), 0, -1):
                    cand = upper[-length:].strip()
                    if cand in keys:
                        resolved = keys[cand]
                        break
                if resolved is not None:
                    sheet = resolved
        ref = f"{sheet}!{cell}"
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def expand(ref, sheets):
    ref = ref.strip().strip("`")
    if "!" not in ref:
        return None, []
    sheet, address = ref.rsplit("!", 1)
    ref = sheet + "!" + address.replace("$", "")
    m = re.match(r"^(.+)!([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?$", ref, re.I)
    if not m:
        return None, []
    sh, c1, r1, c2, r2 = m.groups()
    real = sheets.get(sh.strip().upper())
    if real is None:
        return None, []
    c1, c2 = c1.upper(), (c2.upper() if c2 else None)
    if not c2:
        return real, [f"{c1}{r1}"]
    return real, [
        f"{gl(c)}{r}"
        for c in range(ci(c1), ci(c2) + 1)
        for r in range(int(r1), int(r2) + 1)
    ]


def primary_code(codes):
    present = [c for c in codes if c]
    for code in CAUSE_PRIORITY:
        if code in present:
            return code
    return present[0] if present else None


def slug(text, fallback="variable"):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or fallback


def coerce(value):
    """Spec values must be JSON-serialisable; dates become ISO strings."""
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        return value.date().isoformat(), True
    if isinstance(value, _dt.date):
        return value.isoformat(), True
    return value, False


def unit_for(value, name):
    low = name.lower()
    if isinstance(value, (int, float)) and -1.5 <= value <= 1.5 and value != int(value):
        if any(k in low for k in ("rate", "margin", "share", "premium", "growth",
                                  "percent", "%", "inflation", "discount", "tax")):
            return "ratio", True
    if any(k in low for k in ("days", "dso", "dpo")):
        return "days", False
    if any(k in low for k in ("year", "life", "term")):
        return "years", False
    if any(k in low for k in ("month",)):
        return "months", False
    return "value", False


def atomic_json(path, payload):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def analyse(wb, source_file, expected_source_sha256, *, runs_root="runs", inputs_root="inputs_out"):
    return _analyse(wb, source_file, expected_source_sha256, runs_root=runs_root,
                    inputs_root=inputs_root, baseline_primitives=True)


def historical_source_analysis(wb, source_file, expected_source_sha256, *, runs_root="runs", inputs_root="inputs_out"):
    """Reconstruct retained pre-baseline-index review evidence, never new work.

    Owning refinement validators compare this result with the exact original
    submitted analysis. All current production continues through ``analyse``.
    """
    return _analyse(wb, source_file, expected_source_sha256, runs_root=runs_root,
                    inputs_root=inputs_root, baseline_primitives=False)


_VISIBLE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![A-Za-z0-9.])")


def visible_numeric_values(text):
    """Index explicit numeric renderings in delivered text without masking it.

    Percent text exposes both its written number and its ratio. These are the
    same closed number boundaries used by the final workbook leak scanner.
    A disclosed label must remain visible, so its matches are exclusion-only.
    """
    for match in _VISIBLE_NUMBER.finditer(text):
        token=match.group();percent=token.endswith('%')
        value=decimal.Decimal(token.rstrip('%').replace(',',''))
        for number in (value,value/100) if percent else (value,):
            numeric=float(number)
            if math.isfinite(numeric):yield numeric


def _analyse(wb, source_file, expected_source_sha256, *, runs_root, inputs_root, baseline_primitives):
    run = Path(runs_root) / f"{wb}-variable-sources"
    draft = json.loads((run / "draft.json").read_text(encoding="utf-8"))
    source = Path(source_file)
    if source.name != f"{wb}.xlsx" or not source.is_file() or source.is_symlink():
        raise SystemExit("normalizer requires the activated <workbook>.xlsx source")
    actual_sha256 = _sha256(source)
    if actual_sha256 != expected_source_sha256:
        raise SystemExit(
            "normalizer source hash mismatch: expected %s, got %s"
            % (expected_source_sha256, actual_sha256)
        )
    G = openpyxl.load_workbook(source, data_only=False)
    GV = openpyxl.load_workbook(source, data_only=True)
    BI = openpyxl.load_workbook(
        resolve_workbook_artifact(inputs_root, wb, "%s-inputs"),
        data_only=False)
    sheets = {s.strip().upper(): s for s in G.sheetnames}

    def vkey(v):
        """Any value the agent could read back, not just numbers."""
        import datetime as _dt
        if v is True or v is False or v is None:
            return None
        if isinstance(v, (int, float)):
            return ("n", round(float(v), 10))
        if isinstance(v, (_dt.datetime, _dt.date)):
            return ("d", str(v)[:10])
        if isinstance(v, str) and v.strip():
            return ("s", v.strip())
        return None

    val_index = collections.defaultdict(list)
    for s in GV.sheetnames:
        for c in GV[s]._cells.values():
            k = vkey(c.value)
            if k is not None and s in BI.sheetnames and BI[s][c.coordinate].value is not None:
                val_index[k].append(f"{s}!{c.coordinate}")
    # Baseline preparation can introduce visible primitives on new sheets,
    # notably extracted Embedded Assumptions. They can reveal a research answer
    # even though no cell at that location exists in the source workbook.
    source_value_refs = {key:set(refs) for key,refs in val_index.items()}
    baseline_only_refs = set()
    for sheet in BI if baseline_primitives else ():
        for cell in sheet._cells.values():
            if cell.data_type == 'f':
                continue
            key = vkey(cell.value)
            if key is not None:
                reference = f"{sheet.title}!{cell.coordinate}"
                val_index[key].append(reference)
                if reference not in source_value_refs.get(key,set()):
                    baseline_only_refs.add(reference)
            if isinstance(cell.value,str):
                reference=f"{sheet.title}!{cell.coordinate}"
                for number in visible_numeric_values(cell.value):
                    val_index[vkey(number)].append(reference)
                    # Even a picklist label carries source meaning; do not
                    # erase its text to conceal a number embedded inside it.
                    baseline_only_refs.add(reference)
    # Each typed-value relationship is stored once, shared by every candidate.
    val_index = {k: sorted(set(refs)) for k, refs in val_index.items()}

    baseline = resolve_workbook_artifact(inputs_root, wb, "%s-inputs")
    evidence = {"draft_sha256": _sha256(run / "draft.json"),
                "source_sha256": actual_sha256,
                "baseline_sha256": _sha256(baseline)}
    rows = []
    for row in draft["rows"]:
        atoms = []
        refs = extract_refs(row["value_text"], sheets)
        if not refs:
            atoms.append((None, None, "unparsable_reference"))
        seen = set()
        for ref in refs:
            sh, cs = expand(ref, sheets)
            if sh is None:
                atoms.append((None, ref, "unknown_sheet"))
                continue
            for c in cs:
                if (sh, c) in seen:
                    continue
                seen.add((sh, c))
                code = None
                if G[sh][c].data_type == "f":
                    code = "formula_cell"
                elif sh not in BI.sheetnames or BI[sh][c].value is None:
                    code = "blank_in_baseline"
                atoms.append((sh, c, code))
        for sh, c, code in atoms:
            rows.append({
                "row": row,
                "cells": [(sh, c, GV[sh][c].value)] if sh and not code else [],
                "reference": f"{sh}!{c}" if sh else c,
                "multi": len(atoms) > 1,
                "codes": [code] if code else [],
                "clean": code is None,
                "evidence": evidence,
                "evidence_paths": {"draft_sha256": str((run / "draft.json").absolute()),
                                   "source_sha256": str(source.absolute()),
                                   "baseline_sha256": str(baseline.absolute())},
                "duplicate_refs": val_index.get(vkey(GV[sh][c].value), []) if sh and not code else [],
            })

    for r in rows:
        if not r["clean"]:
            code = primary_code(r["codes"])
            r["code"] = code
            r["reason"] = CAUSE_PROSE.get(code, CAUSE_PROSE["unparsable_reference"])
        r["extra"] = []

    # Dropping an atomic cell uncovers its value, which can create a new
    # leak elsewhere. Iterate until the surviving set is leak-free.
    while True:
        in_scope = {f"{sh}!{c}" for r in rows if r["clean"] for sh, c, _ in r["cells"]}
        covered = set(in_scope)
        for r in rows:
            if r["clean"]:
                covered |= set(r["extra"])
        newly_dropped = False
        closure_cache = {}
        for r in rows:
            if not r["clean"]:
                continue
            group = r["duplicate_refs"]
            cache_key = (id(group), tuple(r["extra"]))
            if cache_key not in closure_cache:
                extra_set = set(r["extra"])
                leaks = []
                for other in group:
                    if other in covered or other in extra_set:
                        continue
                    osh, _ = other.rsplit("!", 1)
                    # A sheet label cannot prove a newly generated primitive
                    # is the same semantic input as an unrelated source value.
                    if PICKLIST.search(osh) and other not in baseline_only_refs: extra_set.add(other)
                    else: leaks.append(other)
                closure_cache[cache_key] = (sorted(extra_set), sorted(set(leaks)))
            extra, leaks = closure_cache[cache_key]
            r["extra"] = extra
            if leaks:
                r["clean"] = False
                r["leaks"] = leaks
                r["code"] = "duplicate_value_leak"
                r["codes"] = list(r.get("codes") or []) + ["duplicate_value_leak"]
                r["reason"] = (
                    "Value also occurs in typed cell(s) %s that carry unrelated "
                    "modelling content, so the variable cannot be masked completely."
                    % ", ".join(leaks[:4])
                )
                newly_dropped = True
        if not newly_dropped:
            break
    G.close()
    GV.close()
    BI.close()
    return draft, rows


def audit_sources(row):
    """Preserve audited URLs; identify simulated private evidence explicitly."""
    notes = str(row.get("source_notes") or "").strip()
    if not notes:
        raise SystemExit("included input has no audited source description")
    urls = row.get("source_urls") or []
    return [
        {"id": "audit-source-" + hashlib.sha256((url + "\n" + notes).encode()).hexdigest()[:16],
         "name": notes, "url": url, "role": "primary", "kind": "external"}
        for url in dict.fromkeys(urls)
    ] if urls else [{
        "id": "audit-private-" + slug(row["draft_id"]),
        "name": notes,
        "url": "internal://supplied-workbook/" + slug(row["draft_id"]),
        "role": "primary", "kind": "internal",
    }]


def apply_semantic_review(rows, run):
    """Maskability does not establish that a cell is a research assumption."""
    candidates = [r for r in rows if r["clean"]]
    if not candidates:
        return None
    path = Path(run) / "normalization-review.json"
    if not path.is_file():
        raise SystemExit("maskable cells require normalization-review.json before emission")
    review = json.loads(path.read_text(encoding="utf-8"))
    if (review.get("schema_version") != "normalization-review/v1"
            or not str(review.get("reviewer") or "").strip()
            or not isinstance(review.get("cells"), dict)):
        raise SystemExit("invalid normalization semantic review")
    for r in candidates:
        if any(review.get(k) != v for k, v in r["evidence"].items()):
            raise SystemExit("stale normalization semantic review")
        decision = review["cells"].get(r["reference"], {})
        if (type(decision.get("eligible")) is not bool
                or not str(decision.get("reason") or "").strip()
                or not isinstance(decision.get("evidence_refs"), list)
                or not decision["evidence_refs"]
                or any(not isinstance(v, str) or not v.strip()
                       for v in decision["evidence_refs"])):
            raise SystemExit("missing or incomplete cell review: " + r["reference"])
        if decision["eligible"]:
            for key in ("entity", "period", "scenario", "basis"):
                if not isinstance(decision.get(key), str) or not decision[key].strip():
                    raise SystemExit("missing reviewed dimension: " + key)
            if "variable_name" in decision and (
                    not isinstance(decision["variable_name"], str)
                    or not decision["variable_name"].strip()):
                raise SystemExit("invalid reviewed variable_name: " + r["reference"])
            r["semantic_review"] = decision
        else:
            r.update(clean=False, code="not_research_input",
                     codes=["not_research_input"], reason=decision["reason"])
    return _sha256(path)


def apply_source_display_review(rows, run):
    """Apply independently reviewed private-source display labels only.

    Audit text, source IDs/URIs, financial dimensions and eligibility are immutable.
    This optional addendum never substitutes for the normalization review.
    """
    path = Path(run) / "source-display-review.json"
    if not path.exists():
        return {}
    review = json.loads(path.read_text(encoding="utf-8"))
    keys = {"schema_version", "reviewer", "normalization_review_sha256",
            "draft_sha256", "source_sha256", "baseline_sha256", "cells"}
    if (not isinstance(review, dict) or set(review) != keys
            or review["schema_version"] != "normalization-source-display/v1"
            or not isinstance(review["reviewer"], str) or not review["reviewer"].strip()
            or not isinstance(review["cells"], dict) or not review["cells"]
            or review["normalization_review_sha256"] != _sha256(Path(run) / "normalization-review.json")):
        raise SystemExit("invalid source display review")
    candidates = {r["reference"]: r for r in rows if r["clean"]}
    if set(review["cells"]) - set(candidates):
        raise SystemExit("source display review names an unaccepted cell")
    for ref, decision in review["cells"].items():
        if sum(r["clean"] and r["reference"] == ref for r in rows) != 1:
            raise SystemExit("ambiguous source display cell: " + ref)
        row = candidates[ref]
        if any(review.get(k) != value for k, value in row["evidence"].items()):
            raise SystemExit("stale source display review")
        original = audit_sources(row["row"])
        if (not isinstance(decision, dict)
                or set(decision) != {"original_sources", "display_name", "reason", "evidence_refs"}
                or not original
                or decision["original_sources"] != original
                or any(s["kind"] != "internal" or not s["url"].startswith("internal://supplied-workbook/") for s in original)
                or not isinstance(decision["display_name"], str)
                or not decision["display_name"].startswith("Supplied-workbook ")
                or not decision["display_name"][len("Supplied-workbook "):].strip()
                or not isinstance(decision["reason"], str) or not decision["reason"].strip()
                or not isinstance(decision["evidence_refs"], list)
                or ref not in decision["evidence_refs"]
                or any(not isinstance(v, str) or not v.strip() for v in decision["evidence_refs"])):
            raise SystemExit("invalid private-source display decision: " + ref)
        row["reviewed_sources"] = [{**s, "name": decision["display_name"]} for s in original]
    return {str(path.absolute()): _sha256(path)}


def coalesce_same_atom_aliases(variables, included):
    """One physical atom can have several independently reviewed source routes.

    Coalescing changes neither eligibility nor financial meaning. Conflicting
    descriptions of the same cells need diagnosis, never invented dimensions.
    Every original ID/source set remains recoverable from explicit provenance.
    """
    def canonical(value):
        return json.dumps(value,sort_keys=True,separators=(",", ":"),ensure_ascii=False,allow_nan=False).encode()
    if len({v["id"] for v in variables})!=len(variables):raise ValueError("duplicate emitted ID requires diagnosis")
    groups={};order=[]
    for variable in variables:
        cells=variable.get("cells")
        if not isinstance(cells,list) or not cells or len(cells)!=len(set(cells)):
            raise ValueError("same-atom alias requires a complete unique physical cell set")
        key=tuple(sorted(cells))
        if key not in groups:groups[key]=[];order.append(key)
        groups[key].append(variable)
    merged=[];mapping={};provenance=[]
    for key in order:
        rows=groups[key]
        if len(rows)==1:
            merged.append(copy.deepcopy(rows[0]));mapping[rows[0]["id"]]=rows[0]["id"];continue
        comparable=[]
        # Repeated source routes can place their source caption in the notes
        # column. Preserve every caption; other note differences still fail.
        notes = {row.get("source_notes") for row in rows}
        attribution_union = len(notes) > 1 and all(
            isinstance(row.get("source_notes"), str) and row["source_notes"].strip()
            and row["source_notes"] in {source.get("name") for source in row["sources"]}
            for row in rows
        )
        for row in rows:
            ignored = {"id", "sources"} | ({"source_notes"} if attribution_union else set())
            item={k:v for k,v in row.items() if k not in ignored}
            item["cells"]=sorted(item["cells"])
            if "extra_cells" in item:item["extra_cells"]=sorted(item["extra_cells"])
            comparable.append(canonical(item))
        if len(set(comparable))!=1:
            raise ValueError("same physical atom has conflicting semantic dimensions/value/question/notes; diagnosis required")
        chosen=min(rows,key=lambda row:row["id"]);value=copy.deepcopy(chosen)
        if attribution_union:
            value["source_notes"] = "\n".join(sorted(notes))
        sources={canonical(source):copy.deepcopy(source) for row in rows for source in row["sources"]}
        value["sources"]=[sources[k] for k in sorted(sources)];merged.append(value)
        aliases=[]
        for row in sorted(rows,key=lambda row:row["id"]):
            mapping[row["id"]]=chosen["id"]
            aliases.append(dict(id=row["id"],sources=copy.deepcopy(row["sources"]),definition_sha256=hashlib.sha256(canonical(row)).hexdigest()))
        group = dict(canonical_id=chosen["id"],cells=list(key),aliases=aliases)
        if attribution_union:
            group["source_note_union"] = [dict(id=row["id"], source_notes=row["source_notes"])
                                          for row in sorted(rows, key=lambda row: row["id"])]
        provenance.append(group)
    projected={draft:list(dict.fromkeys(mapping[vid] for vid in ids)) for draft,ids in included.items()}
    if set(mapping)!={v["id"] for v in variables}:raise ValueError("alias provenance does not cover emitted identities")
    return merged,projected,dict(schema_version="same-physical-atom-aliases/v1",original_entries=len(variables),canonical_atoms=len(merged),groups=provenance)


def emit(wb, draft, rows, *, pipeline_bindings=None, runs_root="runs", dry_run=False):
    # Cells the packager creates (e.g. the Embedded Assumptions disclosure sheet)
    # only exist in the delivered bundle, so the oracle is the first thing that can
    # see those duplicates. Feed its verdict back in here.
    forced = set()
    fp = Path(runs_root) / f"{wb}-variable-sources/oracle_forced_exclusions.json"
    if fp.exists():
        raw = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            forced = set(raw)
        elif isinstance(raw, dict):
            forced = set(raw.get("ids") or raw.get("exclusions") or [])

    for r in rows:
        if r["clean"] and (r["row"]["draft_id"] in forced
                            or slug(r["row"]["draft_id"]) in forced):
            r.update(clean=False, code="forced_exclusion", codes=["forced_exclusion"],
                     reason=CAUSE_PROSE["forced_exclusion"])
    review_sha256 = apply_semantic_review(rows, Path(runs_root) / f"{wb}-variable-sources")
    display_evidence = apply_source_display_review(rows, Path(runs_root) / f"{wb}-variable-sources")
    # A semantic exclusion restores that input in the delivered workbook.
    # Reclose duplicates so no remaining research variable leaks through it.
    while True:
        covered = {r["reference"] for r in rows if r["clean"]}
        covered.update(ref for r in rows if r["clean"] for ref in r["extra"])
        dropped = False
        uncovered = {}
        for r in rows:
            group = r.get("duplicate_refs", [])
            if id(group) not in uncovered:
                uncovered[id(group)] = any(ref not in covered for ref in group)
            if r["clean"] and uncovered[id(group)]:
                r.update(clean=False, code="duplicate_value_leak", codes=["duplicate_value_leak"],
                         reason=CAUSE_PROSE["duplicate_value_leak"])
                dropped = True
        if not dropped:
            break
    included = [r for r in rows if r["clean"]]

    var_defs, inc_map, reasons = [], {}, {}
    used = set()
    for r in included:
        ids = []
        multi = r.get("multi", len(r["cells"]) > 1)
        base = slug(r["row"]["draft_id"])
        if base in forced or r["row"]["draft_id"] in forced:
            r["clean"] = False
            r["code"] = "forced_exclusion"
            r["codes"] = list(r.get("codes") or []) + ["forced_exclusion"]
            r["reason"] = CAUSE_PROSE["forced_exclusion"]
            continue
        for idx, (sh, c, raw_val) in enumerate(r["cells"]):
            val, is_date = coerce(raw_val)
            vid = f"{base}-{slug(sh)}-{c.lower()}" if multi else base
            n = 2
            while vid in used:
                vid = f"{base}-{slug(sh)}-{c.lower()}-{n}"
                n += 1
            used.add(vid)
            ids.append(vid)
            name = r["semantic_review"].get("variable_name", r["row"]["variable_text"]).strip()
            unit, _ = ("date", False) if is_date else unit_for(val, name)
            display = val   # exact workbook value; never rescaled
            var_defs.append({
                "id": vid,
                "name": name if not multi else f"{name} ({sh}!{c})",
                "value": display,
                "raw": None,
                "unit": unit,
                "period": r["semantic_review"]["period"],
                "basis": r["semantic_review"]["basis"],
                "entity": r["semantic_review"]["entity"],
                "scenario": r["semantic_review"]["scenario"],
                "source_notes": r["row"].get("source_notes", ""),
                "sources": r.get("reviewed_sources", audit_sources(r["row"])),
                "question": (
                    "What %s should the model use, per the sourced research?"
                    % ((name[0].lower() + name[1:]) if name else "this assumption")
                ),
                "cells": [f"{sh}!{c}"],
                # the row's duplicate-covering cells ride on the first atomic
                # variable (downstream deduplicates refs); dropping them for
                # multi-cell rows would leave known duplicates unmasked
                "extra_cells": r["extra"] if idx == 0 else [],
            })
        inc_map.setdefault(r["row"]["draft_id"], []).extend(ids)
        for e in r["extra"]:
            reasons[e] = (
                "Data-validation / picklist cell repeating the sourced value; "
                "masked so it cannot reveal the removed assumption."
            )
    excluded = [r for r in rows if not r["clean"]]
    exc_map = {}
    cell_exclusions = []
    for r in excluded:
        code = r.get("code") or primary_code(r.get("codes") or []) or "unparsable_reference"
        item = {"reason": r.get("reason") or CAUSE_PROSE[code], "code": code}
        cell_exclusions.append({"draft_id": r["row"]["draft_id"],
                                "reference": r.get("reference"), **item})
        if r["row"]["draft_id"] not in inc_map:
            exc_map.setdefault(r["row"]["draft_id"], item)
    inc_map = {k: v for k, v in inc_map.items() if k not in exc_map}
    keep = {i for ids in inc_map.values() for i in ids}
    var_defs = [v for v in var_defs if v["id"] in keep]
    var_defs, inc_map, alias_provenance = coalesce_same_atom_aliases(var_defs, inc_map)

    histogram = collections.Counter(item["code"] for item in exc_map.values())

    run = Path(runs_root) / f"{wb}-variable-sources"
    first_path = run / "first_normalization.json"
    snapshot = {
        "schema_version": "1.0",
        "workbook": wb,
        "atomic_variables": len(var_defs),
        "draft_rows": len(draft["rows"]),
        "included_rows": len(inc_map),
        "excluded_rows": len(exc_map),
        "exclusion_reason_codes": dict(sorted(histogram.items())),
        "forced_exclusions": sorted(forced),
    }
    if not first_path.exists() and not dry_run:
        atomic_json(first_path, snapshot)
    first = json.loads(first_path.read_text(encoding="utf-8")) if first_path.exists() else snapshot

    script = run / f"normalize_{wb}.py"
    script_text = TEMPLATE.format(
        wb=wb,
        seed=int(wb),
        variables=pprint.pformat(var_defs, indent=4, width=96, sort_dicts=True),
        aliases=pprint.pformat(alias_provenance, indent=4, width=96, sort_dicts=True),
        included=pprint.pformat(inc_map, indent=4, width=96, sort_dicts=True),
        excluded=pprint.pformat(exc_map, indent=4, width=96, sort_dicts=True),
        reasons=pprint.pformat(reasons, indent=4, width=96, sort_dicts=True),
        codes=pprint.pformat(dict(sorted(histogram.items())), indent=4,
                             width=96, sort_dicts=True),
        first_atomic=int(first.get("atomic_variables") or 0),
        forced=pprint.pformat(sorted(forced), indent=4, width=96),
        cell_exclusions=pprint.pformat(cell_exclusions, sort_dicts=True),
        review_sha256=repr(review_sha256),
        evidence=pprint.pformat({**({path: rows[0]["evidence"][key]
                   for key, path in rows[0].get("evidence_paths", {}).items()}
                   if rows else {}), **display_evidence}, sort_dicts=True),
        pipeline_bindings=pprint.pformat(
            pipeline_bindings, indent=4, width=96, sort_dicts=True
        ),
    )
    if dry_run:
        return {"script": script_text, "first": first, "snapshot": snapshot}
    script.write_text(script_text, encoding="utf-8")
    print(f"{wb}: {len(draft['rows'])} draft rows -> {len(inc_map)} included, "
          f"{len(exc_map)} excluded, {len(var_defs)} atomic variables, "
          f"{len(reasons)} extra cells")
    return len(var_defs)


TEMPLATE = '''#!/usr/bin/env python3
"""Normalize the reviewed {wb} variable-source audit atomically (generated)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

RUN = Path(__file__).resolve().parent
DRAFT = RUN / "draft.json"
NORMALIZED = RUN / "normalized.json"
EXCLUSIONS = RUN / "exclusions.json"
REPORT = RUN / "normalization_report.json"
PROFILES = RUN / "source_profiles.json"

VAR_DEFS = {variables}

INCLUDED = {included}

EXCLUDED = {excluded}

EXTRA_CELL_REASONS = {reasons}

EXCLUSION_REASON_CODES = {codes}

FIRST_ATOMIC_VARIABLES = {first_atomic}

FORCED_EXCLUSIONS = {forced}

PIPELINE_BINDINGS = {pipeline_bindings}

CELL_EXCLUSIONS = {cell_exclusions}
ALIAS_PROVENANCE = {aliases}
REVIEW_SHA256 = {review_sha256}
EVIDENCE = {evidence}


def build_variables():
    out = []
    for d in VAR_DEFS:
        workbook = {{"cells": d["cells"]}}
        if d.get("raw") is not None:
            workbook["value"] = d["raw"]
        if d.get("extra_cells"):
            workbook["extra_cells"] = d["extra_cells"]
        out.append({{
            "id": d["id"],
            "name": d["name"],
            "value": d["value"],
            "unit": d["unit"],
            "entity": d["entity"],
            "period": d["period"],
            "scenario": d["scenario"],
            "basis": d["basis"],
            "status": "Supplied workbook input",
            "question": d["question"],
            "sources": d["sources"],
            "source_notes": d["source_notes"],
            "workbook": workbook,
        }})
    return out


def atomic_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def excluded_entry(draft_id):
    item = EXCLUDED[draft_id]
    if isinstance(item, dict):
        return item["reason"], item.get("code")
    return item, None


def main():
    evidence = dict(EVIDENCE)
    if REVIEW_SHA256:
        evidence[str(RUN / "normalization-review.json")] = REVIEW_SHA256
    for path, expected in evidence.items():
        if not Path(path).is_file() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise SystemExit("normalizer evidence changed: " + path)
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    draft_ids = [row["draft_id"] for row in draft["rows"]]
    dispositions = []
    for draft_id in draft_ids:
        if draft_id in INCLUDED:
            dispositions.append({{
                "draft_id": draft_id,
                "status": "included",
                "variable_ids": INCLUDED[draft_id],
            }})
        elif draft_id in EXCLUDED:
            reason, code = excluded_entry(draft_id)
            row = {{
                "draft_id": draft_id,
                "status": "excluded",
                "reason": reason,
            }}
            if code:
                row["code"] = code
            dispositions.append(row)
        else:
            raise SystemExit("unresolved draft row: %s" % draft_id)

    variables = build_variables()
    ids = {{v["id"] for v in variables}}
    referenced = {{
        vid for d in dispositions if d["status"] == "included"
        for vid in d["variable_ids"]
    }}
    if ids != referenced:
        raise SystemExit("variable/disposition mismatch")
    if len(dispositions) != len(set(d["draft_id"] for d in dispositions)):
        raise SystemExit("dispositions are not one-to-one")

    spec = {{
        "schema_version": "2.0.0",
        "environment_id": "fcp-{wb}",
        "seed": {seed},
        "variables": variables,
    }}
    if ALIAS_PROVENANCE["groups"]:
        spec["variable_aliases"] = ALIAS_PROVENANCE
    if PIPELINE_BINDINGS is not None:
        spec["pipeline_bindings"] = PIPELINE_BINDINGS
    if PROFILES.exists():
        spec["source_profiles"] = (
            json.loads(PROFILES.read_text(encoding="utf-8")).get("profiles") or [])

    atomic_json(NORMALIZED, spec)
    atomic_json(EXCLUSIONS, {{
        "schema_version": "1.0",
        "workbook": "{wb}",
        "exclusions": [
            {{
                "draft_id": k,
                **({{"reason": v, "code": None}} if not isinstance(v, dict) else v),
            }}
            for k, v in sorted(EXCLUDED.items())
        ],
    }})
    atomic_json(REPORT, {{
        "schema_version": "1.0",
        "workbook": "{wb}",
        "draft_rows": len(draft_ids),
        "included_rows": len(INCLUDED),
        "excluded_rows": len(EXCLUDED),
        "atomic_variables": len(variables),
        "first_atomic_variables": FIRST_ATOMIC_VARIABLES,
        "exclusion_reason_codes": EXCLUSION_REASON_CODES,
        "forced_exclusions": FORCED_EXCLUSIONS,
        "extra_cell_reasons": EXTRA_CELL_REASONS,
        "cell_exclusions": CELL_EXCLUSIONS,
        "semantic_review_sha256": REVIEW_SHA256,
        **({{"variable_aliases": ALIAS_PROVENANCE}} if ALIAS_PROVENANCE["groups"] else {{}}),
        "dispositions": dispositions,
        **({{"pipeline_bindings": PIPELINE_BINDINGS}}
           if PIPELINE_BINDINGS is not None else {{}}),
    }})
    print("{wb}: %d draft rows -> %d included, %d excluded, %d atomic variables"
          % (len(draft_ids), len(INCLUDED), len(EXCLUDED), len(variables)))


if __name__ == "__main__":
    main()
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook")
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--release-root")
    parser.add_argument("--release-id")
    parser.add_argument("--source-generation-root", default="source_out")
    parser.add_argument("--source-generation-id")
    parser.add_argument("--segmentation-root", default="seg_out")
    parser.add_argument("--segmentation-generation-id")
    parser.add_argument("--runs-root", default="runs")
    parser.add_argument("--inputs-root", default="inputs_out")
    args = parser.parse_args()
    if args.release_id and not args.release_root:
        parser.error("--release-id requires --release-root")
    if args.release_root and (
        args.source_generation_id or args.segmentation_generation_id
    ):
        parser.error(
            "--release-root is mutually exclusive with explicit candidate IDs"
        )
    if bool(args.source_generation_id) != bool(args.segmentation_generation_id):
        parser.error(
            "explicit staging requires both generation IDs"
        )
    pipeline_bindings = None
    if (
        args.release_root
        or args.source_generation_id
        or args.segmentation_generation_id
    ):
        from xl_release_publication import resolve_build_context

        context = resolve_build_context(
            args.workbook,
            release_root=(
                Path(args.release_root) / args.workbook
                if args.release_root else None
            ),
            release_id=args.release_id,
            source_root=Path(args.source_generation_root) / args.workbook,
            source_generation_id=args.source_generation_id,
            segmentation_root=Path(args.segmentation_root) / args.workbook,
            segmentation_generation_id=args.segmentation_generation_id,
        )
        resolved_source = Path(context["source_path"]).resolve()
        if Path(args.source_file).resolve() != resolved_source:
            raise SystemExit(
                "normalizer source path does not match pinned release/candidate"
            )
        if args.source_sha256 != context["bindings"].get(
            "source_health_sha256"
        ) and _sha256(resolved_source) != args.source_sha256:
            raise SystemExit("normalizer source hash does not match pinned context")
        pipeline_bindings = context["bindings"]
    draft, rows = analyse(
        args.workbook,
        args.source_file,
        args.source_sha256,
        runs_root=args.runs_root, inputs_root=args.inputs_root,
    )
    emit(args.workbook, draft, rows, pipeline_bindings=pipeline_bindings,
         runs_root=args.runs_root)
