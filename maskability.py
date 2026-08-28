#!/usr/bin/env python3
"""Gate 8 maskability check: masked research values must be maskable cleanly.

build_one.sh used to invoke an untracked /tmp/maskability.py here, so the
answer-leak gate silently depended on whatever happened to sit in a
world-writable temp directory (and vanished on every reboot). The gate now
lives in the repository and delegates to the exact duplicate/leak detector
the HTTP oracle applies to delivered workbooks
(``xl_mcp_oracle.scan_value_leaks``).

The scan runs against the *golden* workbook before any masking, so masked
cells are expected to still hold their raw values; only unapproved duplicate
representations outside the mask (and allowlist hygiene) decide validity.
An optional per-run ``leak_allowlist.json`` documents reviewed, legitimate
duplicates.

Usage:  maskability.py <workbook-id> [--source DIR] [--runs-root DIR]

Writes ``runs/<wb>-variable-sources/maskability_report.json`` and exits
non-zero on any unapproved leak.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from xl_variable_mcp import expand_cells, raw_value
from xl_mcp_oracle import scan_value_leaks


def spec_mask_and_audit(spec):
    """(mask_refs, audit rows) derived exactly the way the MCP build does."""
    mask_refs = sorted({
        ref
        for variable in spec.get("variables") or []
        for ref in expand_cells(
            ((variable.get("workbook") or {}).get("cells") or [])
            + ((variable.get("workbook") or {}).get("extra_cells") or []))
    })
    audit = []
    for variable in spec.get("variables") or []:
        workbook = variable.get("workbook") or {}
        refs = workbook.get("cells") or []
        if not refs:
            continue
        raw = raw_value(variable)
        if isinstance(raw, (datetime.date, datetime.datetime)):
            raw = raw.isoformat()
        audit.append({
            "variable_id": variable["id"],
            "refs": expand_cells(refs + (workbook.get("extra_cells") or [])),
            "cell_value": raw,
            "mcp_value": variable["value"],
            "unit": variable["unit"],
        })
    return mask_refs, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="workbook id, e.g. 0233")
    parser.add_argument("--source", default="4-10 100",
                        help="folder holding the golden <wb>.xlsx")
    parser.add_argument("--runs-root", default="runs")
    args = parser.parse_args(argv)

    run = Path(args.runs_root) / ("%s-variable-sources" % args.workbook)
    spec_path = run / "normalized.json"
    if not spec_path.is_file():
        sys.exit("maskability: %s is missing" % spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    mask_refs, audit = spec_mask_and_audit(spec)
    if not mask_refs or not audit:
        sys.exit("maskability: %s yields no masked cells to check" % spec_path)

    golden = None
    for suffix in (".xlsx", ".xlsm"):
        candidate = Path(args.source) / (args.workbook + suffix)
        if candidate.is_file():
            golden = candidate
            break
    if golden is None:
        sys.exit("maskability: no golden workbook %s under %s"
                 % (args.workbook, args.source))

    allowlist = run / "leak_allowlist.json"
    report = scan_value_leaks(
        golden, mask_refs, audit,
        allowlist if allowlist.is_file() else None,
        require_blank_mask=False)
    report["schema_version"] = "2.0"
    report["workbook"] = args.workbook
    report["workbook_scanned"] = str(golden)
    report["total_masked_cells"] = len(mask_refs)
    report["variables_reviewed"] = len(audit)

    out = run / "maskability_report.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
