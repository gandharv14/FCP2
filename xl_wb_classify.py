#!/usr/bin/env python3
"""Classify workbooks into financial-model families for the task-prompt pipeline.

Reads sheet names and shared strings straight out of each xlsx zip (no openpyxl
load, so a folder of 100 workbooks classifies in under a second) and scores each
workbook against a keyword rulebook. The output JSON drives template eligibility
in the prompt assembler: a template that asks about IRR hurdles is only offered
to workbooks tagged `lbo_returns` or `dcf_valuation`, and so on.

Output: taxonomy_out/workbooks.json
  {
    "0248.xlsx": {
      "primary": "three_statement",
      "tags": ["three_statement", ...],          # every family scoring >= TAG_MIN
      "scores": {...},
      "sheets": [...],
      "metrics_found": ["ebitda", "revenue", ...] # lexicon hits in cell text
    }, ...
  }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

SHEET_RE = re.compile(rb'<sheet [^>]*name="([^"]*)"')
T_RE = re.compile(rb"<t(?:\s[^>]*)?>(.*?)</t>", re.S)

TAG_MIN = 3          # score at which a family becomes a tag
MAX_STRINGS = 800    # shared strings scanned per workbook

# family -> keywords; a hit on a sheet name scores 3, a hit in cell text scores 1
RULES = {
    "three_statement": [
        "income statement", "balance sheet", "cash flow statement", "3-statement",
        "is", "bs", "cfs", "p&l", "fs", "financial statements",
        "projected is", "projected bs", "projected cashflow",
    ],
    "dcf_valuation": [
        "dcf", "ufcf", "wacc", "npv", "terminal value", "discount rate", "ev",
    ],
    "multiples_comps": [
        "multiples", "comps", "comparable companies", "precedent transactions",
        "trading comps", "txn comps", "tcomps",
    ],
    "lbo_returns": [
        "lbo", "returns", "moic", "sources & uses", "s&u", "cap table",
        "capital structure", "term facilities", "equity waterfall", "waterfall",
        "irr",
    ],
    "merger_ma": [
        "merger", "accretion", "acquirer", "newco", "trxn structure",
        "contribution",
    ],
    "real_estate_pf": [
        "rent roll", "noi", "cap rate", "amort", "debt schedule", "loan amort",
        "monthly cfs", "term loan", "dscr", "development", "buildout",
    ],
    "fund_pe": [
        "pe distributions", "investment fund", "distributions",
        "carried interest", "use of inv funds",
    ],
    "budget_reporting": [
        "reforecast", "budget", "bud fy", "bva", "actuals",
        "management reporting", "proforma", "qoe", "variance",
    ],
    "startup_saas": [
        "cohort", "arr", "churn", "headcount", "fte", "seed funding", "mrr",
        "traction", "subs revenue",
    ],
    "scenario_driven": [
        "scenario", "case comparison", "downside case", "management case",
        "flags", "sc",
    ],
}

# metric lexicon: presence in cell text marks a workbook as able to host
# questions about that metric (template eligibility, label resolution)
METRIC_LEXICON = [
    "revenue", "ebitda", "ebit", "gross profit", "gross margin", "net income",
    "operating margin", "capex", "working capital", "free cash flow", "fcf",
    "irr", "npv", "moic", "wacc", "terminal value", "enterprise value",
    "equity value", "dscr", "noi", "cap rate", "occupancy", "arr", "mrr",
    "churn", "ltv", "cac", "leverage", "net debt", "interest coverage",
    "quick ratio", "current ratio", "inventory turnover", "receivable",
    "payable", "depreciation", "amortization", "share price", "eps",
    "payback", "dividend",
]


def unescape(text: str) -> str:
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


def read_workbook_text(path: Path):
    """(sheet names, shared strings) pulled from the zip without a full load."""
    with zipfile.ZipFile(path) as zf:
        sheets = [unescape(m.group(1).decode("utf-8", "replace"))
                  for m in SHEET_RE.finditer(zf.read("xl/workbook.xml"))]
        strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            raw = zf.read("xl/sharedStrings.xml")
            strings = [unescape(m.group(1).decode("utf-8", "replace"))
                       for m in T_RE.finditer(raw)][:MAX_STRINGS]
    return sheets, strings


def classify(sheets, strings):
    sheet_names = [s.lower().strip() for s in sheets]
    cell_text = " | ".join(strings).lower()
    scores = Counter()
    for family, keywords in RULES.items():
        for kw in keywords:
            if any(kw == name or (len(kw) > 2 and kw in name)
                   for name in sheet_names):
                scores[family] += 3
        for kw in keywords:
            if len(kw) >= 4 and kw in cell_text:
                scores[family] += 1
    metrics = [m for m in METRIC_LEXICON if m in cell_text]
    return scores, metrics


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Tag workbooks with financial-model families")
    parser.add_argument("path", help="workbook or directory of workbooks")
    parser.add_argument("--glob", default="*.xls[xm]")
    parser.add_argument("-o", "--out", default="taxonomy_out")
    args = parser.parse_args(argv)

    root = Path(args.path)
    targets = (sorted(p for p in root.glob(args.glob)
                      if not p.name.startswith("~$"))
               if root.is_dir() else [root])
    if not targets:
        sys.exit("no workbook matched %s" % args.path)

    result = {}
    primaries = Counter()
    for path in targets:
        try:
            sheets, strings = read_workbook_text(path)
        except (zipfile.BadZipFile, KeyError) as exc:
            print("%s: skipped (%s)" % (path.name, exc))
            continue
        scores, metrics = classify(sheets, strings)
        primary = scores.most_common(1)[0][0] if scores else "unknown"
        result[path.name] = {
            "primary": primary,
            "tags": sorted(f for f, s in scores.items() if s >= TAG_MIN),
            "scores": dict(scores),
            "sheets": sheets,
            "metrics_found": metrics,
        }
        primaries[primary] += 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "workbooks.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)

    print("%d workbooks -> %s" % (len(result), out_path))
    for family, count in primaries.most_common():
        print("  %3d  %s" % (count, family))


if __name__ == "__main__":
    main()
