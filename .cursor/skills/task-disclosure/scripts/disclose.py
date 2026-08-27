#!/usr/bin/env python3
"""Unified pre-run disclosure for GDPval workbook rebuild tasks.

The hard invariant is selection by the task the agent actually sees:

  golden computes the cell
  + a graded answer reads the cell
  + the delivered workbook leaves the cell blank

Everything else is classification and rendering layered on top of that set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

try:
    import openpyxl
except ImportError:  # pragma: no cover
    sys.exit("openpyxl required: pip install openpyxl")


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TASKS_ROOT = REPO_ROOT.parent / "08_12_34_samples_tasks_outputs_hinted"
DEFAULT_RUNS_ROOT = REPO_ROOT / "runs" / "disclosure"
OUTPUT_HEADING = "\n## Output\n"
SECTION_START = "## Workbook disclosure"
STALE_AGENT_SECTIONS = (
    "## Custom formula hints",
    "## Modelling conventions in this workbook",
    SECTION_START,
)
FORMULA_RE = re.compile(r"`=|\b(?:IF|SUM|AVERAGE|NPV|IRR|XIRR|XNPV)\s*\(", re.I)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?"
)


# --------------------------------------------------------------------------- refs

REF_RE = re.compile(
    r"""(?:(?P<sheet>'(?:[^']|'')+'|[A-Za-z_\\][A-Za-z0-9_. &-]*)!)?
        (?P<a>\$?[A-Z]{1,3}\$?[0-9]{1,7})
        (?::(?P<b>\$?[A-Z]{1,3}\$?[0-9]{1,7}))?
        (?![A-Za-z0-9_(])""",
    re.X,
)
STRING_RE = re.compile(r'"(?:[^"]|"")*"')
COORD_RE = re.compile(r"^\$?([A-Z]{1,3})\$?([0-9]{1,7})$")
NOT_A_REF = {"LOG10", "T", "N", "TRUE", "FALSE"}


def col_to_num(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + ord(ch) - 64
    return n


def num_to_col(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def split_coord(coord: str):
    m = COORD_RE.match(coord.replace("$", ""))
    if not m:
        return None
    return m.group(1), int(m.group(2))


def unquote_sheet(name: str) -> str:
    if name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


def quote_sheet(name: str) -> str:
    return "'%s'" % name if re.search(r"[^A-Za-z0-9_.]", name) else name


def key(sheet: str, coord: str) -> str:
    return "%s!%s" % (unquote_sheet(sheet), coord.replace("$", ""))


def pretty(k: str) -> str:
    sheet, coord = k.split("!", 1)
    return "%s!%s" % (quote_sheet(sheet), coord)


def parse_ref(ref: str, default_sheet: str) -> str:
    ref = ref.strip()
    if "!" in ref:
        sheet, coord = ref.rsplit("!", 1)
        return key(sheet, coord)
    return key(default_sheet, ref)


def expand(sheet: str, a: str, b: str | None, cap: int = 50000,
           truncated: set | None = None) -> list[str]:
    pa = split_coord(a)
    if not pa:
        return []
    if not b:
        return [key(sheet, "%s%d" % pa)]
    pb = split_coord(b)
    if not pb:
        return [key(sheet, "%s%d" % pa)]
    c1, c2 = sorted([col_to_num(pa[0]), col_to_num(pb[0])])
    r1, r2 = sorted([pa[1], pb[1]])
    if (c2 - c1 + 1) * (r2 - r1 + 1) > cap:
        if truncated is not None:
            truncated.add(sheet)
        return []
    return [
        key(sheet, "%s%d" % (num_to_col(c), r))
        for c in range(c1, c2 + 1)
        for r in range(r1, r2 + 1)
    ]


def refs_in(formula: str, default_sheet: str, truncated: set | None = None) -> list[str]:
    if not isinstance(formula, str) or not formula.startswith("="):
        return []
    body = STRING_RE.sub('""', formula)
    out = []
    for m in REF_RE.finditer(body):
        a = m.group("a")
        if a.upper() in NOT_A_REF:
            continue
        sheet = unquote_sheet(m.group("sheet")) if m.group("sheet") else default_sheet
        out.extend(expand(sheet, a, m.group("b"), truncated=truncated))
    return list(dict.fromkeys(out))


def cells_from_band_ref(ref: str) -> set[str]:
    if "!" not in ref:
        return set()
    sheet, coord = ref.rsplit("!", 1)
    sheet = unquote_sheet(sheet)
    if ":" in coord:
        a, b = coord.split(":", 1)
        return set(expand(sheet, a, b))
    return {key(sheet, coord)}


# --------------------------------------------------------------------------- workbook


class Book:
    """Flatten an xlsx to formula/value maps keyed as Sheet!A1."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.formula: dict[str, str] = {}
        self.value: dict[str, object] = {}
        self.sheets: list[str] = []
        wbf = openpyxl.load_workbook(path, data_only=False)
        wbv = openpyxl.load_workbook(path, data_only=True)
        try:
            for ws in wbf.worksheets:
                self.sheets.append(ws.title)
                vs = wbv[ws.title]
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value is None:
                            continue
                        k = key(ws.title, cell.coordinate)
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            self.formula[k] = cell.value
                            self.value[k] = vs[cell.coordinate].value
                        else:
                            self.value[k] = cell.value
        finally:
            wbf.close()
            wbv.close()
        self._labels: dict[str, str] = {}
        self._label_cols: dict[str, set] = {}
        self._marker_cache: dict[str, dict] = {}

    def label_columns(self, sheet: str) -> set:
        """Columns that name rows, found from how the sheet is actually laid out.

        A label column carries text on many rows and numbers on almost none. This
        is structural rather than a blacklist, which matters because a scenario
        marker such as "Base" sitting between the label and the data is a real
        word and no token list will catch it. It also keeps side-by-side blocks
        working: each block's own label column qualifies on its own merits.
        """
        if sheet in self._label_cols:
            return self._label_cols[sheet]
        text = Counter()
        numeric = Counter()
        for k, v in self.value.items():
            s, coord = k.split("!", 1)
            if s != sheet:
                continue
            p = split_coord(coord)
            if not p:
                continue
            col = col_to_num(p[0])
            if isinstance(v, str) and v.strip() and not v.startswith("="):
                if not is_unit_stamp(v):
                    text[col] += 1
            elif isinstance(v, (int, float)):
                numeric[col] += 1
        cols = {c for c, n in text.items() if n >= 5 and n > numeric.get(c, 0)}
        self._label_cols[sheet] = cols
        return cols

    def is_row_name(self, sheet: str, col: int, text: str) -> bool:
        """A row name identifies one row; a scenario marker repeats down a column.

        0463 cycles Base / Base Plus / Downside / Lender / Pancake beneath every
        assumption block, in the same column that legitimately labels those
        blocks. Naming a row "Base" identifies nothing.
        """
        return not bool(re.fullmatch(
            r"(?:base(?: case| plus)?|downside|lender|pancake|optimistic|pessimistic)",
            text.strip(),
            re.I,
        ))

    def has(self, k: str) -> bool:
        return k in self.value or k in self.formula

    def row_label(self, k: str) -> str:
        """The label governing this cell: nearest real name to its left on the row.

        Side-by-side assumption blocks share a row (0042 Summary: B/C keys, D/F
        areas, G/H unit costs, I/K fees). A sheet-wide "label column" list misses
        a short block (I has five names and more numbers than text) and then the
        nearest *accepted* column is a different table (K9 → D9 Parking Spaces).
        So walk left from the cell itself. Skip blanks, numbers, formulas, unit
        stamps, and repeating scenario markers. Take the first real name. Do not
        jump to the leftmost column on the row.
        """
        if k in self._labels:
            return self._labels[k]
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        label = ""
        if p:
            col, row = col_to_num(p[0]), p[1]
            # Own cell first: a label sitting in a short block (I9) is the name.
            # Then walk left. Never jump to the leftmost column on the row.
            for c in range(col, 0, -1):
                candidate = key(sheet, "%s%d" % (num_to_col(c), row))
                # A formula's cached text/error is a display result, not its row
                # name. Start at the formula cell for positioning, but keep
                # walking left until a literal semantic label is found.
                if candidate in self.formula:
                    continue
                v = self.value.get(candidate)
                if isinstance(v, (int, float)) or v is None:
                    continue
                if not isinstance(v, str) or not v.strip() or v.startswith("="):
                    continue
                text = v.strip()
                if is_unit_stamp(text):
                    continue
                if re.fullmatch(r"scenario\s+\d+", text, re.I):
                    continue
                if not self.is_row_name(sheet, c, text):
                    continue
                label = text
                break
        self._labels[k] = label
        return label

    def row_cells(self, sheet: str, rownum: int, lo: int = 1, hi: int = 80) -> list[str]:
        return [key(sheet, "%s%d" % (num_to_col(c), rownum)) for c in range(lo, hi + 1)]


# Cells holding one of these are unit stamps, not row names. Taken from the
# segmentation stage's own list so the two agree on what a label is.
UNIT_TOKENS = {
    "aed", "usd", "eur", "gbp", "x", "%", "na", "n/a", "-", "bc",
    "000$", "$mm", "$bn", "$000s", "mm", "bn", "k", "m", "$", "yrs", "y",
    "none", "nil", "null", "yes", "tbd", "n.m.", "nm", "n.a.", "year",
    "years", "yr", "year(s)",
}


def is_unit_stamp(text: str) -> bool:
    """Reject units, scale, and marker tokens so the reader keeps walking left.

    Exact listed tokens plus length < 3 are the original filter. The class
    matches below are what that filter missed: `flag`, `days`, `EURm`, `USD k`,
    `p&L`.
    """
    raw = text.strip()
    lowered = raw.lower()
    if lowered in UNIT_TOKENS or len(raw) < 3:
        return True
    if re.fullmatch(r"flags?", lowered):
        return True
    if re.fullmatch(r"days?", lowered):
        return True
    if re.fullmatch(r"p\s*&\s*l", lowered):
        return True
    if re.fullmatch(r"#(?:div/0!|n/a|value!|ref!|name\?|num!|null!)", lowered):
        return True
    if re.fullmatch(r"(usd|eur|gbp|aed)[kmbn]+", lowered):
        return True
    if re.fullmatch(r"(usd|eur|gbp|aed|\$)\s*['’]?(k|m|mm|bn|000s?)?", lowered):
        return True
    if re.fullmatch(r"[$€£]\s+in\s+(?:k|m|mm|bn|000s?)", lowered):
        return True
    if re.fullmatch(
        r"(?:usd|eur|gbp|aed|sar|[$€£])\s*(?:['’]?\s*0{3}s?|k|m|mm|bn)",
        lowered,
    ):
        return True
    if re.fullmatch(r"(?:usd|eur|gbp|aed|sar|[$€£])\s*/\s*[a-z]+", lowered):
        return True
    if re.fullmatch(r"(?:usd|eur|gbp|aed|sar|[$€£])\s*\d{4}[kmbn]*", lowered):
        return True
    return False


def jsonable(value):
    if isinstance(value, (int, float, str, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    return str(value)


def load_key(task_dir: Path):
    data = json.loads((task_dir / "tests" / "answer_key.json").read_text(encoding="utf-8"))
    return data.get("targets", {}), data.get("tolerance", {})


def task_id(task_dir: Path) -> str:
    return task_dir.name.split("-")[0]


def find_environment(task_dir: Path) -> Path:
    env = task_dir / "environment"
    for path in sorted(env.glob("*.xlsx")):
        return path
    raise FileNotFoundError(f"no delivered workbook under {env}")


def find_golden(task_dir: Path, explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    tid = task_id(task_dir)
    root = task_dir.resolve().parents[1]
    for base in (root / "FCP Workbooks", REPO_ROOT.parent / "FCP Workbooks"):
        if not base.exists():
            continue
        for sub in sorted(base.iterdir()):
            cand = sub / f"{tid}.xlsx"
            if cand.exists():
                return cand.resolve()
    raise FileNotFoundError(f"golden workbook not found for {tid}; pass --golden")


def run_dir(task_dir: Path, runs_root: Path = DEFAULT_RUNS_ROOT) -> Path:
    return runs_root / task_dir.name


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=jsonable) + "\n", encoding="utf-8")


def read_stage(task_dir: Path, name: str, runs_root: Path = DEFAULT_RUNS_ROOT) -> dict:
    return json.loads((run_dir(task_dir, runs_root) / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- selection


def regex_closure(gold: Book, targets: list[str], limit: int = 300000) -> list[str]:
    seen, order, queue = set(), [], deque(targets)
    while queue and len(seen) < limit:
        k = queue.popleft()
        if k in seen:
            continue
        seen.add(k)
        order.append(k)
        formula = gold.formula.get(k)
        if not formula:
            continue
        sheet = k.split("!", 1)[0]
        for ref in refs_in(formula, sheet):
            if ref not in seen:
                queue.append(ref)
    return order


def ast_closure_if_available(task_dir: Path, targets: list[str], ast_dir: Path | None):
    if not ast_dir:
        ast_dir = REPO_ROOT / "ast_out"
    wb = task_id(task_dir)
    graph_dir = ast_dir / wb
    if not (graph_dir / "nodes.csv").exists():
        return None, "missing_ast"
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from xl_seg import model, project  # type: ignore

        graph = model.load(graph_dir, wb)
        cg = project.build(graph)
        seen, order, queue = set(), [], deque(targets)
        while queue:
            k = queue.popleft()
            if k in seen:
                continue
            seen.add(k)
            order.append(k)
            for pred in cg.radj.get(k, ()):
                if pred not in seen:
                    queue.append(pred)
        return order, "ok"
    except Exception as exc:  # fail closed to regex closure, but report it
        return None, f"ast_error:{exc}"


def blanked_formula_cells(gold: Book, delivered: Book, cells: list[str]) -> list[str]:
    return [k for k in cells if k in gold.formula and not delivered.has(k)]


def r1c1ish(formula: str, row: int, col: int) -> str:
    """Small local normalizer used only for grouping dragged formulas."""
    def repl(m):
        def one(raw: str) -> str:
            parsed = re.match(r"(\$?)([A-Z]{1,3})(\$?)(\d+)", raw)
            if not parsed:
                return "REF"
            abs_col, letters, abs_row, row_text = parsed.groups()
            ref_col, ref_row = col_to_num(letters), int(row_text)
            rpart = f"R{ref_row}" if abs_row else f"R[{ref_row - row}]"
            cpart = f"C{ref_col}" if abs_col else f"C[{ref_col - col}]"
            return rpart + cpart

        sheet = "S!" if m.group("sheet") else ""
        start = one(m.group("a"))
        end = one(m.group("b")) if m.group("b") else ""
        return sheet + start + (":" + end if end else "")
    return REF_RE.sub(repl, formula or "")


def group_bands(gold: Book, cells: list[str]) -> list[dict]:
    rows: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    for k in cells:
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p:
            continue
        col, row = p
        formula = gold.formula.get(k, "")
        pattern = r1c1ish(formula, row, col_to_num(col))
        rows[(sheet, row, pattern)].append((col_to_num(col), k))

    bands_out = []
    for (sheet, row, pattern), entries in sorted(rows.items()):
        entries.sort()
        run: list[tuple[int, str]] = []
        for item in entries:
            if run and item[0] != run[-1][0] + 1:
                bands_out.append(make_band(gold, sheet, row, pattern, run))
                run = []
            run.append(item)
        if run:
            bands_out.append(make_band(gold, sheet, row, pattern, run))
    return bands_out


def make_band(gold: Book, sheet: str, row: int, pattern: str, run: list[tuple[int, str]]) -> dict:
    cells = [k for _, k in run]
    first_col, last_col = run[0][0], run[-1][0]
    ref = (
        f"{quote_sheet(sheet)}!{num_to_col(first_col)}{row}"
        if first_col == last_col
        else f"{quote_sheet(sheet)}!{num_to_col(first_col)}{row}:{num_to_col(last_col)}{row}"
    )
    formulas = list(dict.fromkeys(gold.formula.get(k, "") for k in cells))
    return {
        "band": ref,
        "sheet": sheet,
        "row": row,
        "col_lo": first_col,
        "col_hi": last_col,
        "cells": [pretty(k) for k in cells],
        "cell_keys": cells,
        "label": gold.row_label(cells[0]) if cells else "",
        "pattern": pattern,
        "formula_samples": formulas[:3],
        "values": [jsonable(gold.value.get(k)) for k in cells[:5]],
    }


def select_payload(args) -> dict:
    task_dir = Path(args.task_dir).resolve()
    golden_path = find_golden(task_dir, args.golden)
    delivered_path = find_environment(task_dir)
    generation = None
    seg_root = getattr(args, "seg_root", None)
    if seg_root:
        from xl_seg.publication import (
            INPUTS_POINTER_SCHEMA_VERSION,
            resolve_for_consumer,
        )

        workbook = Path(golden_path).stem
        expected_generation_id = getattr(
            args, "expected_generation_id", None
        )
        packaged_manifest_path = (
            task_dir / "tests" / "segmentation_generation_manifest.json"
        )
        if packaged_manifest_path.is_file():
            packaged_manifest = json.loads(
                packaged_manifest_path.read_text(encoding="utf-8")
            )
            packaged_generation_id = packaged_manifest.get("generation_id")
            if (
                expected_generation_id is not None
                and packaged_generation_id != expected_generation_id
            ):
                raise SystemExit(
                    "packaged and requested segmentation generation IDs disagree"
                )
            expected_generation_id = packaged_generation_id
            inputs_generation_path = (
                task_dir / "tests" / "inputs_generation.json"
            )
            if not inputs_generation_path.is_file():
                raise SystemExit(
                    "packaged task has no inputs generation binding"
                )
            inputs_generation = json.loads(
                inputs_generation_path.read_text(encoding="utf-8")
            )
            delivered_sha256 = hashlib.sha256(
                delivered_path.read_bytes()
            ).hexdigest()
            packaged_manifest_sha256 = hashlib.sha256(
                packaged_manifest_path.read_bytes()
            ).hexdigest()
            if (
                inputs_generation.get("schema_version")
                != INPUTS_POINTER_SCHEMA_VERSION
                or inputs_generation.get("generation_id")
                != expected_generation_id
                or inputs_generation.get("inputs_file") != delivered_path.name
                or inputs_generation.get("inputs_sha256") != delivered_sha256
                or inputs_generation.get("generation_manifest_sha256")
                != packaged_manifest_sha256
            ):
                raise SystemExit(
                    "packaged inputs generation binding is invalid"
                )
        generation_dir, manifest = resolve_for_consumer(
            Path(seg_root) / workbook,
            mode=getattr(args, "segmentation_mode", "strict"),
            source_path=golden_path,
            ast_dir=(
                Path(args.ast_dir) / workbook
                if getattr(args, "ast_dir", None) else None
            ),
            require_pass=True,
            expected_generation_id=expected_generation_id,
        )
        generation = {
            "generation_id": (
                manifest.get("generation_id") if manifest is not None else None
            ),
            "directory": str(generation_dir),
            "mode": getattr(args, "segmentation_mode", "strict"),
        }
    gold = Book(golden_path)
    delivered = Book(delivered_path)
    targets_map, tolerance = load_key(task_dir)
    default_sheet = gold.sheets[0]
    targets = [parse_ref(t, default_sheet) for t in targets_map]

    regex = regex_closure(gold, targets)
    ast, ast_status = ast_closure_if_available(task_dir, targets, Path(args.ast_dir) if args.ast_dir else None)
    closure = ast if ast is not None else regex
    selected = blanked_formula_cells(gold, delivered, closure)

    bands = group_bands(gold, selected)
    payload = {
        "schema_version": "1.0",
        "task": task_dir.name,
        "task_dir": str(task_dir),
        "golden": str(golden_path),
        "delivered": str(delivered_path),
        "targets": list(targets_map),
        "target_keys": targets,
        "tolerance": tolerance,
        "selection": {
            "closure_source": "ast" if ast is not None else "regex",
            "ast_status": ast_status,
            "regex_closure_cells": len(regex),
            "closure_cells": len(closure),
            "selected_cells": len(selected),
            "bands": len(bands),
            "non_blank_selected_cells": 0,
        },
        "bands": bands,
    }
    if generation is not None:
        payload["segmentation_generation"] = generation
    return payload


def cmd_select(args):
    payload = select_payload(args)
    out = Path(args.out) if args.out else run_dir(Path(args.task_dir), Path(args.runs_root)) / "bands.json"
    write_json(out, payload)
    print(
        f"{payload['task']}: {payload['selection']['selected_cells']} selected cells, "
        f"{payload['selection']['bands']} bands ({payload['selection']['closure_source']} closure)"
    )


# --------------------------------------------------------------------------- custom methods

# Restored from the old custom-formula extractor, but used deterministically:
# the row label selects one method entry, then formula signatures decide whether
# the construction is a catalogued standard or genuinely out of catalogue.
METHOD_ROLE_PATTERNS = {
    "method_depreciation": (
        r"\bdepreciat", r"\bamorti[sz]", r"\bd&a\b", r"\basset depreciation\b",
    ),
    "method_interest": (
        r"\binterest", r"\bfinance cost", r"\bfinancing cost",
    ),
    "method_tax": (r"\btax(?:es|ation)?\b", r"\bcurrent tax\b", r"\bcash tax\b"),
    "method_revenue": (r"\brevenue", r"\bsales\b", r"\bturnover\b"),
    "method_operating_expense": (
        r"\bopex\b", r"\boperating expense", r"\bsg&a\b", r"\bsalar",
        r"\brent\b", r"\bcost of sales\b", r"\bmarketing\b", r"\bcrm\b",
        r"\bwebsite\b", r"\boffice\b", r"\bmaintenance\b", r"\bmonitoring fee\b",
        r"\bmanagement fee\b",
    ),
    "method_working_capital": (
        r"\bworking capital\b", r"\breceivable", r"\binventor",
        r"\bpayable", r"\bdso\b", r"\bdio\b", r"\bdpo\b",
    ),
    "method_capex": (
        r"\bcapex\b", r"\bcapital expenditure", r"\bfixed.asset addition",
    ),
    "method_debt_movement": (
        r"\bdebt\b", r"\bloan\b", r"\bdrawdown\b", r"\brepayment\b",
        r"\breimbursement\b", r"\bcash sweep\b",
    ),
    "method_discounting": (
        r"\bdiscount", r"\bpresent value\b", r"\bterminal value\b", r"\bnpv\b",
        r"\bvaluation\b", r"\benterprise value\b", r"\bequity value\b",
        r"\bmultiple\b",
    ),
    "method_returns": (
        r"\birr\b", r"\bxirr\b", r"\bmoic\b", r"\bmultiple of money\b",
    ),
}

# A role describes a calculated metric, not its assumption row. Without these
# exclusions "Interest Rate" becomes an interest calculation and "Tax Rate"
# becomes a tax calculation, flooding the custom path with assumptions.
METHOD_ROLE_EXCLUDES = {
    "method_interest": (r"\binterest rate\b", r"\brate\s*$"),
    "method_tax": (r"\btax rate\b",),
    "method_revenue": (r"\bgrowth", r"\bmargin", r"%"),
    "method_operating_expense": (r"\bgrowth", r"\bmargin", r"%"),
    "method_working_capital": (r"^\s*(dso|dio|dpo)\s*$", r"\bdays?\s*$"),
    "method_capex": (r"\bgrowth", r"\bmargin", r"%"),
    "method_discounting": (r"\bdiscount rate\b", r"\bdiscount period\b"),
}

METHOD_ENTRY_IDS = set(METHOD_ROLE_PATTERNS)
CONVENTION_ENTRY_IDS = {
    "discount_period", "inert_line", "terminal_value", "row_populated",
    "npv_timing", "aggregate_scope", "projection_rule", "distribution_policy",
    "liquidation_preference", "stake_scaling", "source_selection",
}
DISTRIBUTION_LABELS = ("dividend", "distribution", "shareholder payment")
# Wider than the entry's name, because the observed rows are labelled by what
# they pay out rather than by the convention that splits them: 0668's waterfall
# rows both read "Exit Equity Value".
EXIT_SPLIT_LABELS = (
    "preference", "preferred", "liquidation", "waterfall", "senior equity",
    "exit equity value", "exit proceeds", "proceeds to holders",
)
BORING_METHOD_LITERALS = {
    0.0, 1.0, -1.0, 2.0, 3.0, 4.0, 12.0, 100.0, 360.0, 365.0,
    1000.0, 10000.0, 1000000.0,
}


def method_roles(label: str) -> list[str]:
    """Registry method entries whose aliases match this visible row label."""
    lowered = (label or "").lower()
    if not lowered:
        return []
    hits = []
    for entry_id, patterns in METHOD_ROLE_PATTERNS.items():
        if not any(re.search(pattern, lowered) for pattern in patterns):
            continue
        if any(re.search(pattern, lowered) for pattern in METHOD_ROLE_EXCLUDES.get(entry_id, ())):
            continue
        hits.append(entry_id)
    return hits


def method_roles_for_band(gold: Book, band: dict) -> tuple[list[str], list[str]]:
    """Role from the row itself, falling back to two neighboring rows.

    Rows such as "Acq. 1" carry no finance word, while the section immediately
    above says Revenue. Neighborhood context is used only when the row itself
    gives no role, so it cannot override a clear label.
    """
    own = band.get("label") or ""
    direct = method_roles(own)
    if direct:
        return direct, [own]
    sheet, row = band.get("sheet"), band.get("row")
    col = band.get("col_lo", 1)
    context = []
    for nearby in range(max(1, int(row or 1) - 2), int(row or 1) + 3):
        cell = key(sheet, "%s%d" % (num_to_col(int(col)), nearby))
        label = gold.row_label(cell)
        if label and label not in context:
            context.append(label)
    context_text = " / ".join(context)
    if re.search(r"revenue.*ebitda.*margin", context_text, re.I):
        return ["method_revenue"], context
    hits = sorted({
        hit for context_label in context for hit in method_roles(context_label)
    })
    return hits, context


def role_triggers(label: str) -> dict[str, list[str]]:
    """Patterns that actually assigned a role after excludes."""
    lowered = (label or "").lower()
    if not lowered:
        return {}
    hits = {}
    for entry_id, patterns in METHOD_ROLE_PATTERNS.items():
        matched = [pattern for pattern in patterns if re.search(pattern, lowered)]
        if not matched:
            continue
        if any(re.search(pattern, lowered) for pattern in METHOD_ROLE_EXCLUDES.get(entry_id, ())):
            continue
        hits[entry_id] = matched
    return hits


def role_case_id(label: str, roles: list[str], role_context: list[str]) -> str:
    key = json.dumps(
        {
            "label": label or "",
            "roles": sorted(roles),
            "role_context": list(role_context or []),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def prepare_method_items(gold: Book, bands: list[dict]) -> list[dict]:
    """Assign roles with the existing neighborhood rules. No arbitration yet."""
    prepared = []
    for original in bands:
        for band in method_subbands(gold, original):
            label = band.get("label") or ""
            if not label:
                prepared.append({
                    "band": band,
                    "label": "",
                    "roles": [],
                    "role_context": [],
                    "blank_label": True,
                })
                continue
            roles, role_context = method_roles_for_band(gold, band)
            prepared.append({
                "band": band,
                "label": label,
                "roles": roles,
                "role_context": role_context,
                "blank_label": False,
            })
    for item in prepared:
        if item["blank_label"] or item["roles"]:
            continue
        band = item["band"]
        nearby_roles = {
            other["roles"][0]
            for other in prepared
            if not other.get("blank_label")
            and len(other["roles"]) == 1
            and other["band"].get("sheet") == band.get("sheet")
            and other["band"].get("pattern") == band.get("pattern")
            and abs(int(other["band"].get("row", 0)) - int(band.get("row", 0))) <= 6
        }
        if len(nearby_roles) == 1:
            item["roles"] = list(nearby_roles)
            item["role_context"] = item["role_context"] + [
                "role propagated from same-shape neighboring row"
            ]
    return prepared


def triggered_by_for_item(item: dict) -> dict:
    own = item.get("label") or ""
    own_hits = role_triggers(own)
    neighbor_hits = {}
    for ctx in item.get("role_context") or []:
        if ctx == own or ctx == "role propagated from same-shape neighboring row":
            continue
        hits = role_triggers(ctx)
        if hits:
            neighbor_hits[ctx] = hits
    return {"own_label": own_hits, "neighbors": neighbor_hits}


def collect_ambiguous_role_cases(prepared: list[dict]) -> list[dict]:
    """One case per unique (label, roles, role_context) with two or more roles."""
    groups: dict[str, dict] = {}
    for item in prepared:
        if item.get("blank_label"):
            continue
        roles = item.get("roles") or []
        if len(roles) <= 1:
            continue
        case_id = role_case_id(item["label"], roles, item.get("role_context") or [])
        if case_id not in groups:
            groups[case_id] = {
                "case_id": case_id,
                "label": item["label"],
                "roles": sorted(roles),
                "role_context": list(item.get("role_context") or []),
                "band_count": 0,
                "sample_bands": [],
                "triggered_by": triggered_by_for_item(item),
                "questions": {
                    entry_id: registry().get(entry_id, {}).get("question", "")
                    for entry_id in sorted(roles)
                },
            }
        groups[case_id]["band_count"] += 1
        band_id = item["band"].get("band")
        samples = groups[case_id]["sample_bands"]
        if band_id and band_id not in samples and len(samples) < 5:
            samples.append(band_id)
    return sorted(groups.values(), key=lambda c: (c["label"], c["case_id"]))


def default_role_resolutions_path(task_dir: Path, runs_root: Path) -> Path:
    return run_dir(task_dir, runs_root) / "role_resolutions.json"


def load_role_resolutions(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_id = {}
    for row in payload.get("resolutions") or []:
        case_id = row.get("case_id")
        if case_id:
            by_id[case_id] = row
    payload["by_id"] = by_id
    return payload


def require_role_resolutions(cases: list[dict], path: Path) -> dict:
    """Load resolutions when present. Missing or incomplete is not a hard stop.

    REGISTRY.md arbitration: try candidates in order and fall through on a
    decline. A resolutions file can still pin the first candidate.
    """
    payload = load_role_resolutions(path)
    if payload is None:
        return {"by_id": {}, "resolutions": []}
    return payload


def apply_role_resolution(item: dict, payload: dict) -> None:
    """Reduce a collision to one candidate when the agent picked a valid role."""
    roles = item.get("roles") or []
    if len(roles) <= 1:
        return
    case_id = role_case_id(item["label"], roles, item.get("role_context") or [])
    row = (payload.get("by_id") or {}).get(case_id) or {}
    chosen = row.get("chosen")
    if chosen in roles:
        item["roles"] = [chosen]
        item["role_context"] = list(item.get("role_context") or []) + [
            "role resolved by agent"
        ]


def walk_ast(node):
    if node is None:
        return
    yield node
    for arg in getattr(node, "args", ()):
        yield from walk_ast(arg)


def method_subbands(gold: Book, band: dict) -> list[dict]:
    """Selection already split mixed shapes by normalized formula pattern."""
    return [band]


def formula_profile(gold: Book, band: dict) -> dict:
    """One parsed representation shared by signature matching and prose.

    Parsing failure is reviewer-visible uncertainty. It never falls through to a
    guessed custom sentence.
    """
    cells = band.get("cell_keys", [])
    formulas = [gold.formula.get(c, "") for c in cells if gold.formula.get(c)]
    profile = {
        "complete": False,
        "band": band.get("band"),
        "cells": cells,
        "label": band.get("label") or (gold.row_label(cells[0]) if cells else ""),
        "formula": formulas[0] if formulas else "",
        "representative_cell": cells[0] if cells else "",
        "references": [],
        "reference_labels": [],
        "functions": [],
        "operators": [],
        "numbers": [],
        "pattern": band.get("pattern", ""),
        "error": None,
    }
    formula = profile["formula"]
    if not formula or not cells:
        profile["error"] = "missing_formula"
        return profile
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from xl_ast_graph import FormulaError, parse_formula  # type: ignore

        ast = parse_formula(formula)
    except Exception as exc:
        profile["error"] = f"parse_error:{exc}"
        return profile

    sheet = cells[0].split("!", 1)[0]
    references = refs_in(formula, sheet)
    labels = []
    for ref in references:
        label = gold.row_label(ref)
        if label and label not in labels:
            labels.append(label)
    functions, operators, numbers, text_literals, raw_references, literal_renderings = [], [], [], [], [], []
    for node in walk_ast(ast):
        if node.kind == "ref" and node.name not in raw_references:
            raw_references.append(node.name)
        if node.kind == "func" and node.name not in functions:
            functions.append(node.name)
        if node.kind in ("infix", "prefix", "postfix") and node.name not in operators:
            operators.append(node.name)
        if node.kind == "const" and node.name == "number":
            try:
                numbers.append(float(node.shape))
            except (TypeError, ValueError):
                pass
        if node.kind == "const" and node.name == "text":
            text_literals.append(str(node.shape))
        if node.kind == "const":
            rendered = describe_ast(node, gold, sheet, profile["label"])
            if rendered not in literal_renderings:
                literal_renderings.append(rendered)
    profile.update({
        "complete": True,
        "ast": ast,
        "references": references,
        "reference_labels": labels,
        "functions": functions,
        "operators": operators,
        "numbers": numbers,
        "text_literals": text_literals,
        "raw_references": raw_references,
        "literal_renderings": literal_renderings,
        "ast_nodes": sum(1 for _ in walk_ast(ast)),
    })
    return profile


def profile_has_label(profile: dict, *patterns: str) -> bool:
    text = " / ".join(profile.get("reference_labels", [])).lower()
    return any(re.search(pattern, text) for pattern in patterns)


def profile_has_all(profile: dict, *groups: tuple[str, ...]) -> bool:
    return all(profile_has_label(profile, *group) for group in groups)


def references_same_row(profile: dict) -> bool:
    cells = profile.get("cells") or []
    if not cells:
        return False
    sheet, coord = cells[0].split("!", 1)
    p = split_coord(coord)
    if not p:
        return False
    row = p[1]
    for ref in profile.get("references", []):
        rsheet, rcoord = ref.split("!", 1)
        rp = split_coord(rcoord)
        if rsheet == sheet and rp and rp[1] == row:
            return True
    return False


def ast_ref_cell(node, profile: dict) -> str | None:
    if node.kind != "ref":
        return None
    cells = profile.get("cells") or []
    sheet = cells[0].split("!", 1)[0] if cells else ""
    refs = refs_in("=" + node.name, sheet)
    return refs[0] if len(refs) == 1 else None


def exact_prior_period_growth(profile: dict) -> bool:
    """Exactly previous cell × (1 + one other reference), in either order."""
    ast = profile.get("ast")
    cells = profile.get("cells") or []
    if ast is None or not cells or ast.kind != "infix" or ast.name != "*" or len(ast.args) != 2:
        return False
    sheet, coord = cells[0].split("!", 1)
    p = split_coord(coord)
    if not p:
        return False
    current_col, current_row = col_to_num(p[0]), p[1]

    def is_previous(node) -> bool:
        ref = ast_ref_cell(node, profile)
        if not ref:
            return False
        rsheet, rcoord = ref.split("!", 1)
        rp = split_coord(rcoord)
        return bool(
            rsheet == sheet and rp
            and rp[1] == current_row
            and col_to_num(rp[0]) == current_col - 1
        )

    def is_one_plus_rate(node) -> bool:
        if node.kind != "infix" or node.name not in ("+", "-") or len(node.args) != 2:
            return False
        left, right = node.args
        const = left if left.kind == "const" else right if right.kind == "const" else None
        other = right if const is left else left
        try:
            one = const is not None and abs(float(const.shape) - 1.0) < 1e-12
        except (TypeError, ValueError):
            one = False
        return one and other.kind == "ref"

    left, right = ast.args
    return (is_previous(left) and is_one_plus_rate(right)) or (
        is_previous(right) and is_one_plus_rate(left)
    )


def root_is_function(profile: dict, name: str) -> bool:
    ast = profile.get("ast")
    return bool(ast is not None and ast.kind == "func" and ast.name.upper() == name.upper())


def catalog_signature(entry_id: str, profile: dict) -> str | None:
    """The catalogued variant matched by a complete profile, else None.

    These signatures are deliberately conservative. A positive match suppresses
    a custom hint, so each one requires both the formula shape and semantically
    appropriate labelled ingredients.
    """
    funcs = set(profile.get("functions", []))
    ops = set(profile.get("operators", []))
    formula = profile.get("formula", "").upper()

    if entry_id == "method_depreciation":
        if not profile_has_label(profile, r"\blife\b", r"useful"):
            return None
        if "AVERAGE" in funcs and profile_has_label(profile, r"opening", r"bop") and profile_has_label(profile, r"closing", r"eop"):
            return "average_balance"
        if any(abs(n - 0.5) < 1e-12 for n in profile.get("numbers", [])) and profile_has_label(profile, r"capex", r"capital"):
            return "midyear_capex"
        if profile_has_label(profile, r"capex", r"capital") and "/" in ops:
            return "bop_plus_capex_over_life"
        if profile_has_label(profile, r"asset", r"balance", r"basis") and "/" in ops:
            return "bop_over_life"
        return None

    if entry_id == "method_interest":
        if "IF" in funcs or "IFS" in funcs:
            return None
        if not profile_has_all(
            profile,
            (r"\brate\b", r"sofr", r"euribor", r"libor"),
            (r"balance", r"debt", r"cash", r"loan", r"principal", r"draw"),
        ):
            return None
        if "AVERAGE" in funcs:
            return "average_balance"
        if any(abs(n - 0.5) < 1e-12 for n in profile.get("numbers", [])):
            return "midyear_flow"
        if profile_has_label(profile, r"draw", r"repay") and ("+" in ops or "-" in ops):
            return "full_draw"
        if "*" in ops:
            return "opening_balance"
        return None

    if entry_id == "method_tax":
        if "IF" in funcs or "IFS" in funcs:
            return None
        if not profile_has_label(profile, r"\brate\b", r"tax %"):
            return None
        if profile_has_label(profile, r"\bnol\b", r"loss") and "MAX" in funcs:
            return "taxable_income_after_losses"
        if profile_has_label(profile, r"taxable income"):
            return "taxable_income"
        if profile_has_label(profile, r"\bebt\b", r"pre.?tax", r"profit before tax"):
            return "pretax_profit"
        return None

    if entry_id == "method_revenue":
        if "IF" in funcs or "IFS" in funcs:
            return None
        if exact_prior_period_growth(profile) and profile_has_label(profile, r"growth"):
            return "prior_period_growth"
        if profile_has_all(profile, (r"price", r"value"), (r"volume", r"units?")) and "*" in ops:
            return "price_times_volume"
        if root_is_function(profile, "SUM") and profile_has_label(profile, r"revenue", r"sales", r"turnover"):
            return "segment_sum"
        if profile_has_all(profile, (r"capacity",), (r"utili[sz]ation",), (r"price",)) and "*" in ops:
            return "capacity_utilisation"
        return None

    if entry_id == "method_operating_expense":
        if "IF" in funcs or "IFS" in funcs:
            return None
        if exact_prior_period_growth(profile) and profile_has_label(profile, r"growth", r"inflation"):
            return "prior_period_growth"
        if profile_has_all(profile, (r"revenue", r"sales"), (r"%", r"margin", r"rate")) and "*" in ops:
            return "percent_of_revenue"
        if profile_has_all(profile, (r"fixed",), (r"variable",)) and "+" in ops:
            return "fixed_plus_variable"
        if root_is_function(profile, "SUM"):
            return "component_sum"
        return None

    if entry_id == "method_working_capital":
        if "AVERAGE" in funcs and profile_has_label(profile, r"days?", r"dso", r"dio", r"dpo"):
            return "average_driver_days"
        if profile_has_label(profile, r"days?", r"dso", r"dio", r"dpo") and "/" in ops:
            return "days_of_driver"
        if profile_has_label(profile, r"%", r"rate") and "*" in ops:
            return "percent_of_driver"
        if profile_has_all(profile, (r"opening", r"bop"), (r"closing", r"eop")) and "-" in ops:
            return "balance_delta"
        return None

    if entry_id == "method_capex":
        if exact_prior_period_growth(profile) and profile_has_label(profile, r"growth"):
            return "prior_period_growth"
        if profile_has_all(profile, (r"revenue", r"sales"), (r"%", r"rate")) and "*" in ops:
            return "percent_of_revenue"
        if profile_has_all(profile, (r"maintenance",), (r"growth",)) and "+" in ops:
            return "maintenance_plus_growth"
        return None

    if entry_id == "method_debt_movement":
        if "MAX" in funcs and profile_has_label(profile, r"funding", r"shortfall"):
            return "required_funding"
        if profile_has_all(profile, (r"opening", r"bop", r"principal"), (r"amorti[sz]ation rate",)) and "*" in ops:
            return "fixed_amortisation"
        if profile_has_label(profile, r"maturity") and profile_has_label(profile, r"opening", r"principal"):
            return "maturity_repayment"
        if "MIN" in funcs and profile_has_all(profile, (r"cash",), (r"sweep",)):
            return "cash_sweep"
        return None

    if entry_id == "method_discounting":
        if "NPV" in funcs or "XNPV" in funcs:
            return "npv_plus_t0" if re.search(r"\)\s*[+\-]", formula) else "periodic_discount"
        if profile_has_all(profile, (r"growth",), (r"discount", r"wacc")) and "/" in ops:
            return "perpetuity_growth"
        if profile_has_label(profile, r"multiple") and "*" in ops:
            return "exit_multiple"
        if "^" in ops and profile_has_label(profile, r"discount", r"wacc"):
            return "midyear_discount" if any(abs(n - 0.5) < 1e-12 for n in profile.get("numbers", [])) else "periodic_discount"
        return None

    if entry_id == "method_returns":
        if "XIRR" in funcs:
            return "xirr_on_dated_series"
        if "IRR" in funcs:
            return "irr_on_series"
        if "/" in ops and profile_has_all(profile, (r"proceeds", r"return"), (r"invest", r"equity")):
            return "multiple_of_money"
        return None

    return None


def ast_is_ref_like(node) -> bool:
    if node.kind == "ref":
        return True
    if node.kind == "prefix" and node.name in ("+", "-") and len(node.args) == 1:
        return ast_is_ref_like(node.args[0])
    if node.kind == "infix" and node.name in ("*", "/") and len(node.args) == 2:
        left, right = node.args
        pairs = [(left, right)]
        if node.name == "*":
            pairs.append((right, left))
        for ref_node, const_node in pairs:
            if not (
                ast_is_ref_like(ref_node)
                and const_node.kind == "const"
                and const_node.name == "number"
            ):
                continue
            try:
                if abs(float(const_node.shape)) in BORING_METHOD_LITERALS:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def structural_reason(profile: dict) -> str | None:
    """Structural/definitional plumbing is not custom finance logic."""
    ast = profile.get("ast")
    if ast is None:
        return None
    if ast_is_ref_like(ast):
        return "link_sign_or_unit_scale"
    funcs = set(profile.get("functions", []))
    ops = set(profile.get("operators", []))
    if funcs & {"INDEX", "MATCH", "VLOOKUP", "HLOOKUP", "XLOOKUP", "INDIRECT", "OFFSET"}:
        return "lookup_or_transcription"
    if profile.get("text_literals") and funcs & {"IF", "IFS", "OR", "AND", "ROUND"}:
        return "diagnostic_or_warning_logic"
    if funcs <= {"SUM"} and ops <= {"+", "-"}:
        return "component_aggregation"
    if not funcs and ops and ops <= {"+", "-"}:
        return "composition_or_rollforward"
    label = (profile.get("label") or "").lower()
    if not (funcs & {"IF", "IFS", "IFERROR", "CHOOSE", "SWITCH", "LOOKUP", "XLOOKUP"}) and any(
        token in label for token in ("/revenue", "% of ", " margin", " ratio")
    ):
        return "ratio_or_assumption_row"
    return None


def nonboring_numbers(profile: dict) -> list[float]:
    return [
        number for number in profile.get("numbers", [])
        if number not in BORING_METHOD_LITERALS
        and -number not in BORING_METHOD_LITERALS
    ]


def confident_custom_reason(entry_id: str, profile: dict) -> str | None:
    """Positive evidence that a no-match is genuinely out of catalogue.

    Merely failing a signature is uncertainty, not custom logic. Claiming the
    band requires a branch, a load-bearing literal, or a role-specific alternate
    driver/predicate that the catalogue explicitly excludes.
    """
    funcs = set(profile.get("functions", []))
    numbers = nonboring_numbers(profile)
    if funcs & {"IF", "IFS", "IFERROR", "CHOOSE", "SWITCH", "LOOKUP", "XLOOKUP"}:
        return "out_of_catalogue_branch"
    if numbers:
        return "embedded_threshold_or_parameter"

    if entry_id == "method_depreciation":
        if profile_has_all(
            profile,
            (r"revenue", r"sales"),
            (r"depreciat", r"capex", r"amorti[sz]"),
        ) and not profile_has_label(profile, r"\blife\b", r"useful"):
            return "alternate_revenue_driver"

    if entry_id == "method_interest":
        if profile_has_label(profile, r"revenue", r"sales") and not profile_has_label(
            profile, r"balance", r"debt", r"cash", r"loan", r"principal"
        ):
            return "alternate_revenue_driver"

    if entry_id == "method_tax":
        if "MAX" in funcs or "MIN" in funcs:
            return "uncatalogued_tax_clamp"

    if entry_id == "method_revenue":
        growth_rows = [
            label for label in profile.get("reference_labels", [])
            if re.search(r"growth|inflation|uplift", label, re.I)
        ]
        if len(growth_rows) > 1:
            return "multiple_growth_drivers"

    if entry_id == "method_operating_expense":
        if "SUM" in funcs and ("*" in set(profile.get("operators", [])) or "/" in set(profile.get("operators", []))):
            return "cumulative_base_escalation"
        if funcs & {"MAX", "MIN"}:
            return "uncatalogued_floor_or_cap"
        growth_rows = [
            label for label in profile.get("reference_labels", [])
            if re.search(r"growth|inflation|uplift|escalat", label, re.I)
        ]
        if len(growth_rows) > 1:
            return "multiple_growth_drivers"

    if entry_id == "method_working_capital":
        own = (profile.get("label") or "").lower()
        refs = " / ".join(profile.get("reference_labels", [])).lower()
        if "payable" in own and "revenue" in refs:
            return "nonstandard_payables_driver"
        if references_same_row(profile):
            return "recursive_working_capital_rule"

    if entry_id == "method_capex":
        if len(profile.get("reference_labels", [])) > 2:
            return "item_driver_build"

    if entry_id == "method_debt_movement":
        if profile.get("operators") and len(profile.get("references", [])) > 2:
            return "uncatalogued_debt_waterfall"

    if entry_id == "method_discounting":
        if "AVERAGE" in funcs or profile_has_label(profile, r"ebitda", r"multiple"):
            return "uncatalogued_valuation_basis"

    if entry_id == "method_returns":
        if "^" in set(profile.get("operators", [])):
            return "uncatalogued_annualisation"

    return None


def reference_lock_qualifier(raw: str) -> tuple[str, str]:
    """Describe Excel absolute-reference locks without exposing formula syntax."""
    match = REF_RE.search(raw)
    if not match:
        return "", ""
    coordinates = [match.group("a")]
    if match.group("b"):
        coordinates.append(match.group("b"))
    locks = []
    for coordinate in coordinates:
        parsed = re.fullmatch(r"(\$?)[A-Z]{1,3}(\$?)[0-9]{1,7}", coordinate)
        locks.append((bool(parsed and parsed.group(1)), bool(parsed and parsed.group(2))))
    if len(locks) == 1:
        fixed_column, fixed_row = locks[0]
        if fixed_column and fixed_row:
            return "fixed ", ""
        if fixed_column:
            return "", " with its column fixed when copied"
        if fixed_row:
            return "", " with its row fixed when copied"
        return "", ""
    if locks and all(fixed_column and fixed_row for fixed_column, fixed_row in locks):
        return "fixed ", ""
    details = []
    for coordinate, (fixed_column, fixed_row) in zip(coordinates, locks):
        if not fixed_column and not fixed_row:
            continue
        clean = coordinate.replace("$", "")
        if fixed_column and fixed_row:
            scope = "row and column"
        elif fixed_column:
            scope = "column"
        else:
            scope = "row"
        details.append(f"{clean}'s {scope}")
    return "", (" with " + " and ".join(details) + " fixed when copied") if details else ""


def describe_ref(gold: Book, raw: str, sheet: str, own_label: str) -> str:
    refs = refs_in("=" + raw, sheet)
    lock_prefix, lock_suffix = reference_lock_qualifier(raw)
    if len(refs) == 1:
        value = gold.value.get(refs[0])
        if (
            refs[0] not in gold.formula
            and isinstance(value, str)
            and value.strip()
        ):
            return f"{lock_prefix}cell {pretty(refs[0])}{lock_suffix} containing {q(value.strip())}"
    ref_labels = [gold.row_label(ref) for ref in refs]
    labels = []
    for label in ref_labels:
        if label and label not in labels:
            labels.append(label)
    if refs:
        if len(refs) == 1 and ":" not in raw:
            location = lock_prefix + "cell " + pretty(refs[0]) + lock_suffix
        else:
            first_sheet, first_coord = pretty(refs[0]).rsplit("!", 1)
            _, last_coord = pretty(refs[-1]).rsplit("!", 1)
            location = (
                f"{lock_prefix}range {first_sheet}!{first_coord}:{last_coord}"
                f"{lock_suffix}"
            )
        if labels and all(ref_labels):
            named = [q(label) for label in labels]
            label_text = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]
            noun = "row" if len(named) == 1 else "rows"
            return f"{location} on the {noun} labelled {label_text}"
        return location
    return f'the named range {q(raw)}'


def describe_ast(node, gold: Book, sheet: str, own_label: str) -> str:
    """Render a parsed Excel AST as deterministic English, never formula text."""
    kind = node.kind
    if kind == "ref":
        return describe_ref(gold, node.name, sheet, own_label)
    if kind == "const":
        if node.name == "text":
            if node.shape == "":
                return "leave the cell blank"
            return q(str(node.shape))
        if node.name == "logical":
            return "true" if node.shape else "false"
        return str(node.value)
    if kind == "missing":
        return "a blank value"
    args = [describe_ast(arg, gold, sheet, own_label) for arg in node.args]
    if kind == "prefix":
        return f"take the negative of ({args[0]})" if node.name == "-" else args[0]
    if kind == "postfix":
        return f"{args[0]} percent" if node.name == "%" else args[0]
    if kind == "infix" and len(args) == 2:
        words = {
            "+": "add ({a}) and ({b})",
            "-": "subtract ({b}) from ({a})",
            "*": "multiply ({a}) by ({b})",
            "/": "divide ({a}) by ({b})",
            "^": "raise ({a}) to the power ({b})",
            "=": "({a}) equals ({b})",
            "<>": "({a}) does not equal ({b})",
            ">": "({a}) is greater than ({b})",
            "<": "({a}) is less than ({b})",
            ">=": "({a}) is at least ({b})",
            "<=": "({a}) is at most ({b})",
            "&": "join ({a}) with ({b})",
        }
        return words.get(node.name, "combine ({a}) with ({b})").format(a=args[0], b=args[1])
    if kind == "func":
        name = node.name.upper()
        if name == "IF" and len(args) >= 3:
            return f"when ({args[0]}) is true, use ({args[1]}); otherwise use ({args[2]})"
        if name == "IF" and len(args) == 2:
            return f"when ({args[0]}) is true, use ({args[1]}); otherwise use FALSE"
        if name == "IFERROR" and len(args) >= 2:
            return f"use ({args[0]}), or use ({args[1]}) if that calculation errors"
        if name == "MAX":
            return "take the greater of " + " and ".join(f"({arg})" for arg in args)
        if name == "MIN":
            return "take the lesser of " + " and ".join(f"({arg})" for arg in args)
        if name == "AVERAGE":
            return "take the mean of " + " and ".join(f"({arg})" for arg in args)
        if name == "SUM":
            return "add " + " and ".join(f"({arg})" for arg in args)
        if name == "CHOOSE" and len(args) >= 2:
            options = "; ".join(
                f"option {index}: ({value})" for index, value in enumerate(args[1:], 1)
            )
            return f"use ({args[0]}) as the option number, then choose {options}"
        if name == "MROUND" and len(args) >= 2:
            return f"round ({args[0]}) to the nearest multiple of ({args[1]})"
        if name == "SUMPRODUCT":
            return "sum the products of corresponding values in " + " and ".join(
                f"({arg})" for arg in args
            )
        if name == "SUMIFS" and len(args) >= 3:
            pairs = [
                f"({args[index]}) matches ({args[index + 1]})"
                for index in range(1, len(args) - 1, 2)
            ]
            return f"total the values in ({args[0]}) where " + " and ".join(pairs)
        if name == "SUMIF" and len(args) >= 2:
            summed = args[2] if len(args) >= 3 else args[0]
            return f"total the values in ({summed}) where ({args[0]}) matches ({args[1]})"
        if name == "AND":
            return " and ".join(args)
        if name == "OR":
            return " or ".join(args)
        if name in ("NPV", "XNPV", "IRR", "XIRR"):
            return f"the {name.lower()} of " + ", ".join(args)
        return f"{name.lower()} applied to " + ", ".join(args)
    if kind in ("union", "array"):
        return ", ".join(args)
    return " then ".join(args)


def custom_sentence_fields(profile: dict, gold: Book) -> dict:
    cells = profile.get("cells") or []
    sheet = cells[0].split("!", 1)[0] if cells else ""
    ast = profile.get("ast")
    steps = describe_ast(ast, gold, sheet, profile.get("label", "")) if ast is not None else ""
    reference_renderings = [
        describe_ref(gold, raw, sheet, profile.get("label", ""))
        for raw in profile.get("raw_references", [])
    ]
    coverage_complete = all(rendered in steps for rendered in reference_renderings)
    coverage_complete = coverage_complete and all(
        rendered in steps for rendered in profile.get("literal_renderings", [])
    )
    band = profile.get("band") or compact_cells([pretty(c) for c in cells])
    representative = pretty(profile.get("representative_cell")) if profile.get("representative_cell") else band
    return {
        "label": q(profile.get("label", "")),
        "band": band,
        "representative": representative,
        "calculation_kind": (
            "calculation" if len(cells) == 1 else "copied-column calculation"
        ),
        "steps": steps,
        "_coverage_complete": coverage_complete,
    }


def expand_method_band(gold: Book, delivered: Book, band: dict) -> dict:
    """Include the full contiguous blank copied-formula run on the target row."""
    cells = list(band.get("cell_keys") or [])
    if not cells:
        return band
    first = split_coord(cells[0].split("!", 1)[1])
    if not first:
        return band
    sheet, row = band.get("sheet"), int(band.get("row") or first[1])
    pattern = band.get("pattern") or r1c1ish(
        gold.formula.get(cells[0], ""), row, col_to_num(first[0])
    )
    cols = [col_to_num(split_coord(c.split("!", 1)[1])[0]) for c in cells]
    lo, hi = min(cols), max(cols)

    def matches(col: int) -> bool:
        candidate = key(sheet, f"{num_to_col(col)}{row}")
        formula = gold.formula.get(candidate, "")
        return bool(
            formula
            and not delivered.has(candidate)
            and r1c1ish(formula, row, col) == pattern
        )

    while lo > 1 and matches(lo - 1):
        lo -= 1
    while matches(hi + 1):
        hi += 1
    if lo == min(cols) and hi == max(cols):
        return band
    return make_band(
        gold,
        sheet,
        row,
        pattern,
        [(col, key(sheet, f"{num_to_col(col)}{row}")) for col in range(lo, hi + 1)],
    )


def detect_custom_methods(
    gold: Book,
    delivered: Book,
    bands: list[dict],
    targets: set[str],
    resolutions_path: Path | None = None,
):
    """Run before convention detectors; only confident no-matches claim a band."""
    records, assessments, claimed = [], [], set()
    prepared = prepare_method_items(gold, bands)
    cases = collect_ambiguous_role_cases(prepared)
    if cases and resolutions_path is not None:
        payload = require_role_resolutions(cases, resolutions_path)
        for item in prepared:
            apply_role_resolution(item, payload)
    for item in prepared:
        band = expand_method_band(gold, delivered, item["band"])
        label = item["label"]
        if item.get("blank_label"):
            assessments.append({
                "band": band.get("band"),
                "cells": band.get("cells", []),
                "cell_keys": band.get("cell_keys", []),
                "label": "",
                "roles": [],
                "status": "unclassified",
                "reason": "blank_target_label",
            })
            continue
        roles = item["roles"]
        role_context = item["role_context"]
        base = {
            "band": band.get("band"),
            "cells": band.get("cells", []),
            "cell_keys": band.get("cell_keys", []),
            "label": label,
            "roles": roles,
            "role_context": role_context,
        }
        if not roles:
            assessments.append({
                **base, "status": "unclassified", "reason": "no_method_role"
            })
            continue
        # Registry order within Section 2. A resolutions file, when it chooses,
        # only pins the first candidate; a miss still falls through.
        ordered = [r for r in METHOD_ROLE_PATTERNS if r in roles]
        last_assessment = None
        claimed_here = False
        for entry_id in ordered:
            rec, assessment = try_custom_role(gold, band, label, base, entry_id)
            last_assessment = assessment
            if rec is not None:
                records.append(rec)
                claimed.update(rec.get("cell_keys", []))
                assessments.append(assessment)
                claimed_here = True
                break
        if not claimed_here and last_assessment is not None:
            if len(ordered) > 1:
                last_assessment = {
                    **base, "status": "unclassified", "reason": "ambiguous_role",
                    "tried": ordered,
                    "last": last_assessment,
                }
            assessments.append(last_assessment)
    return records, assessments, claimed


def try_custom_role(gold: Book, band: dict, label: str, base: dict, entry_id: str):
    """One method-entry attempt. A None record means fall through to the next."""
    profile = formula_profile(gold, band)
    if not profile.get("complete"):
        return None, {
            **base, "entry": entry_id, "status": "unclassified",
            "reason": profile.get("error", "profile_incomplete"),
        }
    variant = catalog_signature(entry_id, profile)
    if variant:
        return None, {
            **base, "entry": entry_id, "status": "standard",
            "variant": variant,
        }
    plumbing = structural_reason(profile)
    if plumbing:
        return None, {
            **base, "entry": entry_id, "status": "structural",
            "reason": plumbing,
        }
    custom_reason = confident_custom_reason(entry_id, profile)
    if not custom_reason:
        return None, {
            **base, "entry": entry_id, "status": "unclassified",
            "reason": "no_catalogue_match_but_no_positive_custom_signal",
        }
    fields = custom_sentence_fields(profile, gold)
    coverage_complete = bool(fields.pop("_coverage_complete", False))
    rec = record(
        entry_id,
        "out_of_catalogue",
        band.get("cell_keys", []),
        profile["formula"],
        registry().get(entry_id, {}).get("alternatives", []),
        f"Out-of-catalogue {entry_id} method on row {label!r}.",
        fields=fields,
    )
    rec.update({
        "source": "custom_method_detector",
        "custom_reason": custom_reason,
        "coverage_complete": coverage_complete,
        "method_profile": {
            k: v for k, v in profile.items()
            if k not in ("ast",)
        },
    })
    return rec, {
        **base, "entry": entry_id, "status": "out_of_catalogue",
        "reason": custom_reason,
    }


def calibrate_legacy_custom(task_dir: Path, assessments: list[dict], selected: set[str]) -> dict:
    """Compare deterministic outcomes with old custom_logic band labels.

    The old prose is never imported. It is a labelled coverage set only, and is
    deliberately not treated as a correctness oracle because 0523 proved it can
    be wrong.
    """
    path = task_dir / "tests" / "formula_hints.json"
    if not path.exists():
        return {"bands": 0, "outcomes": {}}
    hints = json.loads(path.read_text(encoding="utf-8")).get("hints", [])
    old = [
        band
        for hint in hints
        if "custom_logic" in hint.get("classes", [])
        for band in hint.get("bands", [])
    ]
    rows = []
    for band in old:
        cells = cells_from_band_ref(band)
        overlaps = [
            a for a in assessments
            if cells & set(a.get("cell_keys", []))
        ]
        statuses = sorted({a.get("status", "unknown") for a in overlaps})
        if not (cells & selected):
            outcome = "not_selected"
        elif "out_of_catalogue" in statuses:
            outcome = "out_of_catalogue"
        elif "standard" in statuses:
            outcome = "standard"
        elif "structural" in statuses:
            outcome = "structural"
        elif "unclassified" in statuses:
            outcome = "unclassified"
        else:
            outcome = "missed"
        rows.append({"band": band, "outcome": outcome, "statuses": statuses})
    return {
        "bands": len(old),
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "details": rows,
    }


# --------------------------------------------------------------------------- detectors


HEADER_RE = re.compile(r"(statement|schedule|summary|assumptions|inputs)\s*$|:\s*$", re.I)


def selected_keys(selection: dict) -> set[str]:
    return {k for band in selection.get("bands", []) for k in band.get("cell_keys", [])}


def _fmt(cells, n=4):
    cells = list(cells)
    shown = ", ".join(pretty(c) for c in cells[:n])
    return shown + (f" +{len(cells) - n} more" if len(cells) > n else "")


def detect_discount_period(gold: Book, scope: set[str]) -> list[dict]:
    out, seen_rows = [], set()
    for k in sorted(scope):
        label = gold.row_label(k).lower()
        if "discount period" not in label and "discount factor period" not in label:
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen_rows:
            continue
        seen_rows.add((sheet, p[1]))
        row = [c for c in gold.row_cells(sheet, p[1]) if c in gold.formula or c in gold.value]
        nums = [gold.value.get(c) for c in row if isinstance(gold.value.get(c), (int, float))]
        formulas = [gold.formula[c] for c in row if c in gold.formula]
        value = None
        if any(isinstance(v, float) and abs(v - round(v)) > 0.4 for v in nums):
            value = "mid_year"
        elif nums and all(isinstance(v, (int, float)) and abs(v - round(v)) < 1e-9 for v in nums):
            value = "year_end"
        if value is None and any("/2" in f for f in formulas):
            value = "mid_year"
        if value:
            out.append(record(
                "discount_period", value, [c for c in row if c in gold.formula],
                formulas[0] if formulas else f"cached values {nums[:4]}",
                ["mid_year", "year_end", "day_weighted"],
                f"Row labelled {gold.row_label(k)!r}.",
                fields={"label": q(gold.row_label(k))},
            ))
    return out


def detect_inert_line(gold: Book, scope: set[str]) -> list[dict]:
    out, seen_rows = [], set()
    for k in sorted(scope):
        label = gold.row_label(k)
        if not label or len(label) < 4 or HEADER_RE.search(label) or label.isupper():
            continue
        if not re.search(
            r"waterfall|guard|flag|minimum cash|less:\s*minimum|closing balance|"
            r"cash closing|reserve|trap",
            label, re.I,
        ):
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen_rows:
            continue
        seen_rows.add((sheet, p[1]))
        row = [c for c in gold.row_cells(sheet, p[1]) if c in gold.formula]
        vals = [gold.value.get(c) for c in row]
        nums = [v for v in vals if isinstance(v, (int, float))]
        if not row or len(nums) < 2:
            continue
        if not all(abs(v) < 1e-9 for v in nums):
            continue
        out.append(record(
            "inert_line", "always_zero", row, gold.formula[row[0]],
            ["always_zero", "charged_once", "charged_every_period"],
            f"Row labelled {label!r} evaluates to zero in all periods.",
            fields={"label": q(label)},
        ))
    return out


def detect_terminal_value(gold: Book, scope: set[str]) -> list[dict]:
    out, seen_rows = [], set()
    for k in sorted(scope):
        label = gold.row_label(k).lower()
        if "terminal value" not in label and "exit value" not in label:
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen_rows:
            continue
        seen_rows.add((sheet, p[1]))
        row = [c for c in gold.row_cells(sheet, p[1]) if c in gold.formula]
        row_label = gold.row_label(k)
        if not row:
            out.append(record(
                "terminal_value", "absent", [], "no formula anywhere on the row",
                ["absent", "perpetuity_growth", "exit_multiple", "other"],
                f"Row labelled {row_label!r} is never populated.",
                row_ref=f"{quote_sheet(sheet)}!row {p[1]}",
                fields={"label": q(row_label)},
            ))
            continue
        formula = gold.formula[row[0]]
        if re.search(r"\(1\s*\+\s*[^)]+\)\s*/\s*\(", formula):
            value = "perpetuity_growth"
        elif re.search(r"[Mm]ultiple|Comps", formula):
            value = "exit_multiple"
        else:
            value = "other"
        out.append(record(
            "terminal_value", value, row, formula,
            ["absent", "perpetuity_growth", "exit_multiple", "other"],
            f"Row labelled {row_label!r}, populated in {len(row)} column(s).",
            fields={"label": q(row_label)},
        ))
    return out


def _row_once(gold: Book, k: str, seen_rows: set) -> tuple | None:
    """Resolve a scope cell to its sheet, row number and formula cells, once."""
    sheet, coord = k.split("!", 1)
    p = split_coord(coord)
    if not p or (sheet, p[1]) in seen_rows:
        return None
    seen_rows.add((sheet, p[1]))
    row = [c for c in gold.row_cells(sheet, p[1]) if c in gold.formula]
    return (sheet, p[1], col_to_num(p[0]), row) if row else None


def _ingredient_labels(gold: Book, formula: str, sheet: str, row: int, col: int) -> list[str]:
    return [
        gold.row_label(key(rs, "%s%d" % (num_to_col(rc), rr))).lower()
        for rs, rc, rr in _ingredients(formula, sheet, row, col)
    ]


def detect_distribution_policy(gold: Book, scope: set[str]) -> list[dict]:
    """Which rule sizes a dividend or distribution row.

    A residual and a payout ratio are both ordinary practice and the delivered
    file keeps neither formula, so the choice is invisible. Worse, a surviving
    payout-ratio row on another sheet actively points at the wrong one.
    """
    values = [
        "residual_cash_floored", "residual_cash_unfloored", "payout_ratio",
        "capped_at_retained_earnings", "first_period_only",
    ]
    out, seen_rows = [], set()
    for k in sorted(scope):
        label = gold.row_label(k)
        lowered = label.lower()
        if not any(t in lowered for t in DISTRIBUTION_LABELS):
            continue
        # "Available cash for dividends" contains the word but is the pool,
        # not the payment. 0638 rows 117-118 are that trap; row 119 is the
        # distribution.
        if re.search(r"available (cash|for)|cash available|retained earnings", lowered):
            continue
        resolved = _row_once(gold, k, seen_rows)
        if not resolved:
            continue
        sheet, rownum, col, row = resolved
        # Not the leftmost formula. 0638 row 119 holds `=BC` in D (a named
        # unit) and `=SUM(H119:AD119)` in F; the period cells `=H118` are
        # the residual. Classify on the strongest payment shape present.
        def dist_rank(cell: str) -> int:
            f = gold.formula[cell]
            u = f.upper()
            c = split_coord(cell.split("!", 1)[1])
            labs = _ingredient_labels(
                gold, f, sheet, rownum, col_to_num(c[0]) if c else col
            )
            cash = any(
                (
                    "cash available" in l
                    or "available cash" in l
                    or "cash left" in l
                    or "available for distribution" in l
                    or "available for dividend" in l
                )
                for l in labs
            )
            if cash and "MAX(" in u and re.search(r",\s*0\s*\)", u):
                return 5
            if "MIN(" in u and any("retained" in l for l in labs):
                return 4
            if any(("payout" in l or "dividend per" in l) for l in labs) and "*" in f:
                return 3
            if cash:
                return 2
            if re.match(r"^=\s*SUM\(", f.strip(), re.I) or not refs_in(f, sheet):
                return -1
            return 0

        cell = max(row, key=dist_rank)
        if dist_rank(cell) < 0:
            continue
        formula = gold.formula[cell]
        upper = formula.upper()
        labels = _ingredient_labels(
            gold, formula, sheet, rownum,
            col_to_num(split_coord(cell.split("!", 1)[1])[0]),
        )
        # Every value needs a positive signal. There is no default: a dividend
        # row whose shape says nothing is left uncovered, because asserting a
        # rule off the absence of a token is how this entry would become the
        # next `projection_rule` over-disclosure.
        cash_available = any(
            (
                "cash available" in l
                or "available cash" in l
                or "cash left" in l
                or "available for distribution" in l
                or "available for dividend" in l
            )
            for l in labels
        )
        # The floor is tested before the cap. 0677's `=MAX(MIN( H167:H168),0)` is
        # both at once - the lesser of cash available and retained earnings, held
        # at zero - and the two values are written as siblings, so one has to
        # win. The floored residual wins because it is the reading the row's own
        # report attributes and the one an agent gets wrong; a co-occurring cap
        # is recorded as a known limit on the entry rather than silently chosen.
        if cash_available and "MAX(" in upper and re.search(r",\s*0\s*\)", upper):
            value = "residual_cash_floored"
        elif "MIN(" in upper and any("retained" in l for l in labels):
            value = "capped_at_retained_earnings"
        elif cash_available:
            value = "residual_cash_unfloored"
        elif any(("payout" in l or "dividend per" in l) for l in labels) and "*" in formula:
            value = "payout_ratio"
        elif (
            len(row) == 1
            and refs_in(formula, sheet)
            and not re.match(r"^=\s*SUM\(", formula.strip(), re.I)
        ):
            value = "first_period_only"
        else:
            continue
        # Attach the payment cells, not a surviving unit stamp. 0638 D119 is
        # `=BC` and remains as "EURm" in the delivered file; including it made
        # Ship when think the distribution row had survived.
        pay_cells = [c for c in row if dist_rank(c) >= 2] or [
            c for c in row if dist_rank(c) >= 0
        ]
        out.append(record(
            "distribution_policy", value, pay_cells, formula, values,
            f"Row labelled {label!r} sizes its distribution by {value}.",
            fields={"label": q(label)},
        ))
    return out


def detect_liquidation_preference(gold: Book, scope: set[str]) -> list[dict]:
    """How exit proceeds divide between preferred and common holders.

    Ordered ahead of `stake_scaling`, which ships on `always` and would describe
    one of these rows as an ownership-share multiplication.
    """
    values = [
        "participating", "non_participating", "pro_rata_no_preference",
        "capped_participation",
    ]
    out, seen_rows = [], set()
    for k in sorted(scope):
        label = gold.row_label(k)
        lowered = label.lower()
        if not any(t in lowered for t in EXIT_SPLIT_LABELS):
            continue
        # Exit or cap-table block only. Elsewhere these words are commentary.
        sheet_l = k.split("!", 1)[0].lower()
        if "cap table" not in sheet_l and "exit" not in lowered and "waterfall" not in lowered:
            continue
        resolved = _row_once(gold, k, seen_rows)
        if not resolved:
            continue
        _sheet, _rownum, _col, row = resolved
        # Not the leftmost formula on the row. A waterfall row commonly holds a
        # plain pro-rata column beside the column that carries the preference -
        # 0668 row 43 is `=C42/$C$24` in C and the solved preference in D - so
        # classify on the most specific shape present, not on whichever comes
        # first.
        def rank(cell: str) -> int:
            f = gold.formula[cell]
            u = f.upper()
            if "MIN(" in u:
                return 3
            if "MAX(" in u or re.search(r"\bIF\s*\(", u):
                return 2
            if solves_for_divisor(f):
                return 1
            return 0
        cell = max(row, key=rank)
        rnk = rank(cell)
        # A product or a SUM labelled "Exit Equity Value" is not a split
        # convention. 0644's Flags row 420 is that shape. 0668 ships because
        # D43/D49 carry the solved preference (rank 1).
        if rnk == 0 and "cap table" not in k.split("!", 1)[0].lower():
            continue
        if rnk == 0 and "/" not in gold.formula[cell]:
            continue
        formula = gold.formula[cell]
        value = {
            3: "capped_participation",
            2: "non_participating",
            1: "participating",
            0: "pro_rata_no_preference",
        }[rnk]
        out.append(record(
            "liquidation_preference", value, row, formula, values,
            f"Row labelled {label!r} splits exit proceeds as {value}.",
            fields={"label": q(label)},
        ))
    return out


def solves_for_divisor(formula: str) -> bool:
    """A preference solved for carries its own pro-rata divisor in the numerator.

    0668's `=(C42-$C$21+$C$24*$C$21)/$C$24` is the observed shape: the share
    count divides the whole expression and also multiplies inside it, which is
    what solving for a participating preference leaves behind.
    """
    m = re.search(r"/\s*(\$?[A-Za-z]{1,3}\$?\d+)\s*\)?\s*$", formula.strip())
    if not m:
        return False
    ref = m.group(1).replace("$", "")
    head = formula[: m.start()].replace("$", "")
    return bool(re.search(r"\b%s\b" % re.escape(ref), head))


def detect_npv_timing(gold: Book, scope: set[str]) -> list[dict]:
    out = []
    for k in sorted(scope):
        formula = gold.formula.get(k)
        if not formula or "NPV(" not in formula.upper():
            continue
        tail = formula.upper().split("NPV(", 1)[1]
        extra = bool(re.search(r"\)\s*[+\-]\s*[A-Z$]", tail))
        out.append(record(
            "npv_timing",
            "t0_added_separately" if extra else "excel_default_one_period_out",
            [k],
            formula,
            ["excel_default_one_period_out", "t0_added_separately"],
            "Excel NPV discounts its first argument by one full period.",
            fields={"label": q(gold.row_label(k))},
        ))
    return out


def q(text: str) -> str:
    return '"%s"' % str(text).replace('"', "'")


def ingredient_phrase(gold: Book, evidence: str, cells: list[str], own_label: str = "") -> str:
    """Name the rows a formula reads, by their visible labels.

    Returns empty when the result would not be a clean sentence, which is the
    signal for the record not to ship at all. Three cases fail: no labelled
    ingredient to name, more than two of them, or one that merely repeats the
    target's own label on the same sheet. An off-sheet row with the same
    visible name is a different cell and is named (and the tab is included).
    In each failing case the sentence would be mush, and a mushy disclosure
    is worse than silence.
    """
    if not cells:
        return ""
    sheet, coord = cells[0].split("!", 1)
    p = split_coord(coord)
    if not p:
        return ""
    col, row = col_to_num(p[0]), p[1]
    labels = []
    for rsheet, rcol, rrow in _ingredients(evidence, sheet, row, col):
        lab = gold.row_label(key(rsheet, "%s%d" % (num_to_col(rcol), rrow)))
        if not lab:
            return ""
        # Same-string on the same sheet is the target naming itself. Same-string
        # on another sheet is a different row (0042: Summary I9 "Asset
        # Management Fee" is the rate; Operations 146 is the calculated line).
        if lab == own_label and rsheet == sheet:
            return ""
        if rsheet != sheet:
            piece = "the row labelled %s on the %s tab" % (q(lab), rsheet)
        else:
            piece = "the row labelled %s" % q(lab)
        if piece not in labels:
            labels.append(piece)
    if not labels or len(labels) > 2:
        return ""
    return labels[0] if len(labels) == 1 else " and ".join(labels)


def is_step_increment(body: str, prev: str) -> bool:
    """Prior cell of this row, plus or minus exactly one further term.

    `=+H59+$F$17` is the observed case. A SUM over members is not this shape.
    """
    text = re.sub(r"^\+", "", body.strip())
    prev_re = r"\$?%s" % re.escape(prev)
    other = r"(?:'[^']+'!)?\$?[A-Z]{1,3}\$?\d+"
    return bool(re.fullmatch(
        r"%s\s*[+\-]\s*%s" % (prev_re, other), text, re.I
    ))


def detect_projection_rule(gold: Book, delivered: Book, selected: set[str]) -> list[dict]:
    rows = defaultdict(list)
    for k in selected:
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if p:
            rows[(sheet, p[1])].append((col_to_num(p[0]), k))
    out = []
    for (_sheet, _row), entries in sorted(rows.items()):
        if len(entries) < 3:
            continue
        entries.sort()
        classified = []
        for cnum, k in entries:
            formula = gold.formula.get(k, "")
            body = formula[1:] if formula.startswith("=") else formula
            prev = "%s%d" % (num_to_col(cnum - 1), _row)
            if re.fullmatch(r"\$?%s" % re.escape(prev), body.strip(), re.I):
                kind = "hold_level"
            elif re.search(r"\*\s*\(\s*1\s*[+\-]", body):
                kind = "hold_growth"
            elif is_step_increment(body, prev):
                kind = "step_increment"
            elif re.match(r"^AVERAGE\(", body.strip(), re.I):
                kind = "average_window"
            elif re.search(r"[A-Z]{1,3}\$?\d+\s*\*\s*", body):
                kind = "ratio_to_driver"
            else:
                kind = "other"
            # Two cells can share a rule kind and still be different formulas.
            # Splitting on kind alone fuses them into one band, and no single
            # sentence is true of both.
            classified.append((cnum, k, kind, r1c1ish(formula, _row, cnum)))
        runs: list[list] = []
        run: list[tuple] = []
        for item in classified:
            if run and (item[0] != run[-1][0] + 1
                        or item[2] != run[-1][2]
                        or item[3] != run[-1][3]):
                runs.append(run)
                run = []
            run.append(item)
        if run:
            runs.append(run)
        # A 1-cell seed plus one long run (0644 Info!H59 is implied share,
        # I59:M59 adds the increment) is not two bullets claiming the
        # horizon. Decline only when two shippable runs would compete.
        shippable = [r for r in runs if len(r) >= 3 and r[0][2] != "other"]
        for r in runs:
            append_projection_run(
                out, gold, r,
                covers_row=len(shippable) == 1 and r in shippable,
            )
    return out


def append_projection_run(out: list[dict], gold: Book, run: list[tuple], covers_row: bool = True):
    if len(run) < 3:
        return
    kind = run[0][2]
    if kind == "other":
        return
    label = gold.row_label(run[0][1])
    if not label:
        return
    cells = [item[1] for item in run]
    evidence = gold.formula.get(cells[min(1, len(cells) - 1)], "")
    rec = record(
        "projection_rule", kind, cells, evidence,
        ["hold_level", "hold_growth", "step_increment", "average_window",
         "ratio_to_driver"],
        f"Forecast row labelled {label!r}; applies only to this contiguous {kind} run.",
        # hold_level names no ingredient, and its sentence has no slot for one.
        # Passing an empty field would make the renderer discard the record while
        # the disposition still claimed it shipped.
        fields=({"label": q(label)} if kind == "hold_level" else
                {"label": q(label),
                 "ingredient": ingredient_phrase(gold, evidence, cells, own_label=label)}),
    )
    rec["covers_row"] = covers_row
    out.append(rec)


def detect_stake_scaling(gold: Book, scope: set[str]) -> list[dict]:
    pct_cells = set()
    for k, v in gold.value.items():
        label = gold.row_label(k)
        if label and re.search(r"equity investment|ownership|stake|% acquired", label, re.I):
            if isinstance(v, (int, float)):
                pct_cells.add(k)
    byrow = defaultdict(list)
    for k in sorted(scope):
        formula = gold.formula.get(k)
        if not formula:
            continue
        hits = [r for r in refs_in(formula, k.split("!", 1)[0]) if r in pct_cells]
        if not hits:
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if p:
            byrow[(sheet, p[1])].append((col_to_num(p[0]), k, formula, hits))
    out = []
    for (_sheet, _row), entries in sorted(byrow.items()):
        entries.sort()
        _, first, formula, hits = entries[0]
        out.append(record(
            "stake_scaling", "applied", [k for _, k, _, _ in entries],
            formula, ["applied", "not_applied"],
            f"Row labelled {gold.row_label(first)!r} multiplies by {pretty(hits[0])}, the ownership share.",
            fields={
                "label": q(gold.row_label(first)),
                "ingredient": "the row labelled %s" % q(gold.row_label(hits[0]))
                              if gold.row_label(hits[0]) else pretty(hits[0]),
            },
        ))
    return out


def _row_key(ref: str) -> tuple[str, int] | None:
    sheet, coord = ref.split("!", 1)
    p = split_coord(coord)
    return (sheet, p[1]) if p else None


def is_whole_value_read(formula: str, gold: Book, sheet: str, row: int) -> str | None:
    """Single named source row, optionally sign-flipped or scaled by one factor."""
    refs = refs_in(formula, sheet)
    rows = []
    for ref in refs:
        rk = _row_key(ref)
        if rk and rk not in rows:
            rows.append(rk)
    if not rows:
        return None
    # Same-row references are a projection, not a source read.
    foreign = [(s, r) for s, r in rows if not (s == sheet and r == row)]
    if not foreign or len(foreign) > 2:
        return None
    source_sheet, source_row = foreign[0]
    sample = next(ref for ref in refs if _row_key(ref) == (source_sheet, source_row))
    if not gold.row_label(sample):
        return None
    if len(foreign) == 2:
        other = next(ref for ref in refs if _row_key(ref) == foreign[1])
        if not gold.row_label(other):
            return None
        if not re.search(r"\*", formula):
            return None
    return sample


def detect_source_selection(gold: Book, scope: set[str]) -> list[dict]:
    out, seen_rows = [], set()
    for k in sorted(scope):
        formula = gold.formula.get(k)
        if not formula:
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen_rows:
            continue
        label = gold.row_label(k)
        if not label:
            continue
        source = is_whole_value_read(formula, gold, sheet, p[1])
        if not source:
            continue
        source_sheet = source.split("!", 1)[0]
        lowered = label.lower()
        purchase = any(t in lowered for t in (
            "initial investment", "purchase price", "entry", "consideration", "cv",
        ))
        rate_like = bool(re.search(
            r"\brate\b|hold start|blended|wacc|source|cost of (debt|equity|capital)",
            lowered,
        ))
        bare = bool(re.fullmatch(r"=\s*[+\-]?[A-Za-z0-9_ '$#!]+$", formula.strip()))
        if source_sheet != sheet:
            if not (purchase or rate_like):
                continue
        else:
            if gold.row_label(source) == label or not bare:
                continue
        seen_rows.add((sheet, p[1]))
        source_label = gold.row_label(source)
        out.append(record(
            "source_selection", "source", [k], formula, ["source"],
            f"Row labelled {label!r} reads from {pretty(source)}.",
            fields={
                "label": q(label),
                "ingredient": "the %s sheet, on the row labelled %s"
                              % (source.split("!", 1)[0], q(source_label))
                              if source_label else "the %s sheet" % source.split("!", 1)[0],
            },
        ))
    return out


def row_has_data(gold: Book, sheet: str, rownum: int) -> bool:
    for c in range(1, 61):
        k = key(sheet, "%s%d" % (num_to_col(c), rownum))
        if k in gold.formula or isinstance(gold.value.get(k), (int, float)):
            return True
    return False


def rows_inside_sums(gold: Book) -> dict:
    """Rows covered by some SUM range, keyed by sheet.

    An empty labelled row is worth disclosing only when a total spans it, since
    then its emptiness changes that total. Anywhere else it is a block header,
    and telling the agent to leave a header empty is noise.
    """
    out: dict = defaultdict(set)
    for k, formula in gold.formula.items():
        sheet = k.split("!", 1)[0]
        for m in re.finditer(r"SUM\(\s*(\$?[A-Z]{1,3}\$?\d+\s*:\s*\$?[A-Z]{1,3}\$?\d+)\s*\)",
                             formula, re.I):
            for cell in refs_in("=" + m.group(1), sheet):
                csheet, coord = cell.split("!", 1)
                p = split_coord(coord)
                if p:
                    out[csheet].add(p[1])
    return out


def detect_row_populated(gold: Book, delivered: Book, scope: set[str], targets: list[str]) -> list[dict]:
    out = []
    summed_rows = rows_inside_sums(gold)
    sheets = {t.split("!", 1)[0] for t in targets}
    scope_rows = defaultdict(set)
    for k in sorted(scope):
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if p:
            scope_rows[sheet].add(p[1])
    for sheet in sorted(sheets):
        rows = defaultdict(list)
        for k in list(gold.value) + list(gold.formula):
            s, coord = k.split("!", 1)
            if s != sheet:
                continue
            p = split_coord(coord)
            if p:
                rows[p[1]].append((col_to_num(p[0]), k))
        for rownum, entries in sorted(rows.items()):
            entries.sort()
            label, label_col, data = "", None, []
            for cnum, k in entries:
                v = gold.value.get(k)
                is_text = isinstance(v, str) and k not in gold.formula
                if is_text and not label:
                    label, label_col = v.strip(), cnum
                elif label_col is not None and cnum > label_col:
                    if k in gold.formula or isinstance(v, (int, float)):
                        data.append(k)
            if data or not label or len(label) < 4 or HEADER_RE.search(label) or label.isupper():
                continue
            # The row must look empty in the file the agent actually receives. Its
            # label survives the emptying pass by design, so only cells to the
            # right of the label count: a visible annotation or figure there means
            # this is not an unused row, and saying it is contradicts what the
            # agent can see.
            if any(delivered.has(k) for cnum, k in entries if cnum > (label_col or 0)):
                continue
            # An unused row only matters when a total actually spans it, which is
            # most of what separates a real empty member from a block header.
            if rownum not in summed_rows.get(sheet, ()):
                continue
            # A header also falls inside its block's total, so require data on
            # both sides. A member sits between populated rows; a header has its
            # block below it and nothing above.
            if not (row_has_data(gold, sheet, rownum - 1)
                    and row_has_data(gold, sheet, rownum + 1)):
                continue
            above = [r for r in (rownum - 1, rownum - 2) if r in scope_rows[sheet]]
            below = [r for r in (rownum + 1, rownum + 2) if r in scope_rows[sheet]]
            if above and below:
                out.append(record(
                    "row_populated", "unused", [], "no formula and no value anywhere on row",
                    ["unused", "populated", "populated_but_unread"],
                    f"Row labelled {label!r} is empty in the original but sits inside the block that feeds a graded answer.",
                    row_ref=f"{quote_sheet(sheet)}!row {rownum}",
                    fields={"label": q(label)},
                ))
    out.extend(detect_populated_but_unread(gold, delivered))
    return out


ASSUMPTION_LABEL_RE = re.compile(
    r"payout|ratio|assumption|yield|per annum|\bp\.a\.?\b|days\b|growth rate",
    re.I,
)


def detect_populated_but_unread(gold: Book, delivered: Book) -> list[dict]:
    """A surviving assumption row that nothing in the golden reads."""
    truncated: set[str] = set()
    referenced: set[str] = set()
    for k, formula in gold.formula.items():
        sheet = k.split("!", 1)[0]
        referenced.update(refs_in(formula, sheet, truncated=truncated))
    referenced_rows = set()
    for ref in referenced:
        rk = _row_key(ref)
        if rk:
            referenced_rows.add(rk)
    out, seen = [], set()
    for k in list(gold.value) + list(gold.formula):
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen:
            continue
        seen.add((sheet, p[1]))
        if sheet in truncated:
            continue
        if (sheet, p[1]) in referenced_rows:
            continue
        label = gold.row_label(k)
        if not label or len(label) < 4 or HEADER_RE.search(label) or label.isupper():
            continue
        if not ASSUMPTION_LABEL_RE.search(label):
            continue
        row_cells = gold.row_cells(sheet, p[1])
        surviving = [
            c for c in row_cells
            if c in delivered.formula
            or isinstance(delivered.value.get(c), (int, float))
        ]
        if not surviving:
            continue
        populated = any(
            c in gold.formula or isinstance(gold.value.get(c), (int, float))
            for c in row_cells
        )
        if not populated:
            continue
        out.append(record(
            "row_populated", "populated_but_unread",
            surviving,
            "populated; no inbound reference",
            ["unused", "populated", "populated_but_unread"],
            f"Row labelled {label!r} is populated but unread.",
            fields={"label": q(label)},
        ))
    return out


def detect_aggregate_scope(gold: Book, delivered: Book, targets: list[str]) -> list[dict]:
    """Name every member of equivalent vertical totals across target columns."""
    grouped = defaultdict(list)
    for k in targets:
        formula = gold.formula.get(k)
        if not formula:
            continue
        # A plain sum over a single span, nothing else. The old greedy pattern
        # also matched `=SUM(a-b/c*d)` and `=SUM(...)/-SUM(...)`, and described a
        # subtraction or a ratio as if it were an addition.
        m = re.match(r"^=\s*SUM\(\s*(\$?[A-Z]{1,3}\$?\d+\s*:\s*\$?[A-Z]{1,3}\$?\d+)\s*\)\s*$",
                     formula.strip(), re.I)
        if not m:
            continue
        sheet = k.split("!", 1)[0]
        spanned = refs_in("=" + m.group(1).strip(), sheet)
        # Vertical only: a same-row horizontal SUM names one row and renders as
        # "the total labelled X is the sum of the row labelled X".
        member_rows = {split_coord(c.split("!", 1)[1])[1] for c in spanned
                       if split_coord(c.split("!", 1)[1])}
        if len(member_rows) < 2:
            continue
        named = [gold.row_label(c) for c in spanned]
        if not all(named):
            continue
        if not all(row_has_data(gold, *c.split("!", 1)[0:1], split_coord(c.split("!", 1)[1])[1])
                   for c in spanned):
            continue
        labels = list(dict.fromkeys(named))
        members = [c for c in spanned if not delivered.has(c)]
        if not members or not labels:
            continue
        first = split_coord(spanned[0].split("!", 1)[1])
        last = split_coord(spanned[-1].split("!", 1)[1])
        target = split_coord(k.split("!", 1)[1])
        if not first or not last or not target:
            continue
        group_key = (sheet, first[1], last[1], tuple(labels), gold.row_label(k))
        grouped[group_key].append((col_to_num(target[0]), k, formula))

    out = []
    for (_sheet, first_row, last_row, labels, total_label), entries in sorted(grouped.items()):
        entries.sort()
        target_cells = [item[1] for item in entries]
        if not target_cells or not total_label:
            continue
        rec = record(
            "aggregate_scope", "member set", target_cells, entries[0][2], ["member set"],
            f"Total labelled {total_label!r} aggregates the complete named member set.",
            fields={
                "label": q(total_label),
                "members": ("the row labelled " + q(labels[0])) if len(labels) == 1
                           else "the rows labelled " + ", ".join(q(label) for label in labels),
            },
        )
        rec["accepted_target_reference"] = True
        rec["aggregate_member_rows"] = {
            "first": first_row,
            "last": last_row,
            "labels": list(labels),
        }
        out.append(rec)
    return out


# --------------------------------------------------------------------------- registry
#
# REGISTRY.md is the authority. Nothing may ship that it does not name, so the
# entry ids, alternatives and agent-facing sentences are read from it rather
# than duplicated here. Only the `Ship when` predicates live in Python, because
# they are conditions over a workbook rather than prose; `check_registry_drift`
# asserts the two stay in step.

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "REGISTRY.md"
ENTRY_ID_RE = re.compile(r"^-\s+\*\*Id\.\*\*\s+`([a-z_]+)`", re.M)
FIELD_RE = re.compile(r"^-\s+\*\*([A-Z][A-Za-z ]*)\.\*\*\s*(.*)$")
SENTENCE_RE = re.compile(r"-\s+(?:`([^`]+)`|([A-Za-z][A-Za-z ]*?))\s+-\s+\"(.+?)\"", re.S)
_REGISTRY_CACHE: dict | None = None


def registry() -> dict:
    """Parse REGISTRY.md into {entry_id: {alternatives, ship_when, sentences}}."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    if not REGISTRY_PATH.exists():
        raise SystemExit(f"registry not found: {REGISTRY_PATH}")
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    entries: dict = {}
    for block in text.split("\n## ")[1:]:
        ids = ENTRY_ID_RE.findall(block)
        if not ids:
            continue
        entry_id = ids[0]
        fields: dict = {}
        current = None
        for line in block.splitlines():
            m = FIELD_RE.match(line)
            if m:
                current = m.group(1).strip().lower()
                fields[current] = m.group(2).strip()
            elif current and line.startswith("  "):
                fields[current] += "\n" + line.strip()
        alts = [
            " ".join(a.split()).strip(" `")
            for a in fields.get("alternatives", "").split("|")
            if a.strip()
        ]
        sentences = {
            (m.group(1) or m.group(2) or "").strip(): " ".join(m.group(3).split())
            for m in SENTENCE_RE.finditer(fields.get("sentence", ""))
        }
        entries[entry_id] = {
            "alternatives": alts,
            "ship_when": fields.get("ship when", ""),
            "sentences": sentences,
            "question": fields.get("question", ""),
        }
    _REGISTRY_CACHE = entries
    return entries


def registry_always_ships(entry_id: str) -> bool:
    return registry().get(entry_id, {}).get("ship_when", "").lstrip().startswith("`always`")


def check_registry_drift(detector_entries: set[str]) -> list[str]:
    """Prove detector -> registry and registry -> detector coverage."""
    known = set(registry())
    faults = [f"detector emits `{e}` with no registry entry" for e in sorted(detector_entries - known)]
    expected = CONVENTION_ENTRY_IDS | METHOD_ENTRY_IDS
    faults.extend(
        f"registry entry `{entry_id}` has no detector mapping"
        for entry_id in sorted(known - expected)
    )
    faults.extend(
        f"detector mapping `{entry_id}` has no registry entry"
        for entry_id in sorted(expected - known)
    )
    for entry_id in sorted(detector_entries & known):
        if not registry_always_ships(entry_id) and entry_id not in SHIP_WHEN:
            faults.append(f"entry `{entry_id}` has a conditional Ship when but no predicate")
    for entry_id in sorted(METHOD_ENTRY_IDS):
        if entry_id not in SHIP_WHEN:
            faults.append(f"method entry `{entry_id}` has no shared Ship when predicate")
        entry = registry().get(entry_id, {})
        if "out_of_catalogue" not in entry.get("alternatives", []):
            faults.append(f"method entry `{entry_id}` has no out_of_catalogue alternative")
        if "out_of_catalogue" not in entry.get("sentences", {}):
            faults.append(f"method entry `{entry_id}` has no out_of_catalogue sentence")
    return faults


# ----------------------------------------------------------------- ship-when
#
# A predicate returns True to allow disclosure for this band. Entries whose
# registry `Ship when` is `always` need no predicate.


def _ingredients(evidence: str, sheet: str, row: int, col: int) -> list[tuple[str, int, int]]:
    """Referenced cells other than the immediately-prior column on the same row."""
    out = []
    for ref in refs_in(evidence, sheet):
        rsheet, coord = ref.split("!", 1)
        p = split_coord(coord)
        if not p:
            continue
        rcol, rrow = col_to_num(p[0]), p[1]
        if rsheet == sheet and rrow == row and abs(rcol - col) <= 1:
            continue
        out.append((rsheet, rcol, rrow))
    return out


def _available(ctx, rsheet: str, rcol: int, rrow: int, sheet: str, row: int) -> bool:
    """Visible in the delivered file, or structurally adjacent on the same sheet."""
    if ctx["delivered"].has(key(rsheet, "%s%d" % (num_to_col(rcol), rrow))):
        return True
    return rsheet == sheet and abs(rrow - row) <= 15


def ship_projection_rule(rec: dict, ctx: dict) -> bool:
    """Ship only when the rule's ingredient is out of the agent's reach.

    A driver, growth rate or averaged window that is visible or sitting a few
    rows away is ordinary spreadsheet reasoning. Disclosing it restates what the
    workbook already shows.
    """
    cells = rec.get("cell_keys") or []
    if not cells:
        return False
    if rec.get("value") != "hold_level":
        rec["declined_reason"] = (
            "only a literal hold-level convention is safe for agent-facing disclosure"
        )
        return False
    sheet, coord = cells[0].split("!", 1)
    p = split_coord(coord)
    if not p:
        return False
    col, row = col_to_num(p[0]), p[1]
    evidence = rec.get("evidence") or ""
    # A branch is not a projection rule. The shape classifier fires on the
    # multiplication inside an IF and mislabels genuinely custom logic.
    if re.search(r"\b(IF|IFS|CHOOSE|LOOKUP|XLOOKUP)\s*\(", evidence, re.I):
        return False
    if not evidence:
        return False
    if rec.get("value") == "average_window":
        # The sentence says the row IS an average. Anything wrapped around the
        # AVERAGE - a multiplier, an offset - makes that false, and flattening
        # the operand and the multiplier into one list reads as though both were
        # being averaged.
        if not re.fullmatch(r"=\s*AVERAGE\([^()]*\)\s*", evidence.strip(), re.I):
            return False
    # The sentences say "in each period" and "across the forecast". That is only
    # true when the run covers every cell the agent has to build on this row; a
    # row that switches rule partway gets two bullets, each claiming the whole
    # horizon, and at most one of them can be right.
    if rec.get("covers_row") is False:
        return False
    # A record whose ingredient could not be named renders nothing. Deciding that
    # here keeps the disposition honest instead of marking it disclosed and
    # dropping it silently at render time.
    if rec.get("value") != "hold_level" and not (rec.get("fields") or {}).get("ingredient"):
        return False
    if rec.get("value") == "hold_level":
        # Holding flat needs no named ingredient, only a level to hold. Ship when
        # no numeric value earlier on the row is visible, so nothing tells the
        # agent what level to carry. The row's label is visible by design and is
        # not a level, so text cells do not count.
        return not any(
            isinstance(ctx["delivered"].value.get(key(sheet, "%s%d" % (num_to_col(c), row))),
                       (int, float))
            for c in range(1, col)
        )
    ingredients = _ingredients(evidence, sheet, row, col)
    if not ingredients:
        return False
    if rec.get("value") == "step_increment" and increment_label_is_rate_like(
        ctx, ingredients
    ):
        # Visibility of the increment is not visibility of add-vs-compound.
        return True
    return not all(_available(ctx, rs, rc, rr, sheet, row) for rs, rc, rr in ingredients)


def increment_label_is_rate_like(ctx: dict, ingredients: list) -> bool:
    gold = ctx["gold"]
    for rs, rc, rr in ingredients:
        label = gold.row_label(key(rs, "%s%d" % (num_to_col(rc), rr))).lower()
        if re.search(r"growth|p\.a\.|per annum|%", label):
            return True
    return False


def ship_source_selection(rec: dict, ctx: dict) -> bool:
    """Ship the wiring only when the source still has to be built.

    A source cell that survives in the delivered file is one read away from a
    value, so naming it hands that value over.
    """
    rec["declined_reason"] = (
        "source relationship remains reviewer-only until sign and period "
        "semantics are represented explicitly"
    )
    return False


def ship_not_a_target(rec: dict, ctx: dict) -> bool:
    """Ship only when the band is not itself graded."""
    return not any(c in ctx["targets"] for c in rec.get("cell_keys") or [])


def ship_aggregate_scope(rec: dict, ctx: dict) -> bool:
    """Keep exact aggregate evidence reviewer-only when the total is graded."""
    if any(c in ctx["targets"] for c in rec.get("cell_keys") or []):
        rec["declined_reason"] = (
            "complete aggregate scope would disclose a graded target's calculation"
        )
        return False
    return bool(rec.get("aggregate_member_rows", {}).get("labels"))


def _band_survives(rec: dict, ctx: dict) -> bool:
    return any(ctx["delivered"].has(c) for c in rec.get("cell_keys") or [])


def ship_distribution_policy(rec: dict, ctx: dict) -> bool:
    """Ship only while the rule is still hidden.

    A payout rate that survives on a labelled row and is applied to one visible
    driver is ordinary reasoning, not a convention the agent cannot see.
    """
    if _band_survives(rec, ctx):
        rec["declined_reason"] = "distribution row survives in the delivered file"
        return False
    if rec.get("value") == "payout_ratio":
        ings = list(_ingredients(rec.get("evidence") or "", *_origin(rec)))
        if len(ings) == 1 and ctx["delivered"].has(
            key(ings[0][0], "%s%d" % (num_to_col(ings[0][1]), ings[0][2]))
        ):
            rec["declined_reason"] = "payout rate survives and applies to one visible driver"
            return False
    return True


def ship_liquidation_preference(rec: dict, ctx: dict) -> bool:
    """Ship only while the waterfall is blank and no preference multiple survives.

    Deliberately not gated on the band being graded. This is a Section 1
    convention: it names which of four defensible splits the author chose and
    states no construction over inputs the agent still holds.
    """
    if _band_survives(rec, ctx):
        rec["declined_reason"] = "waterfall row survives in the delivered file"
        return False
    for rs, rc, rr in _ingredients(rec.get("evidence") or "", *_origin(rec)):
        cell = key(rs, "%s%d" % (num_to_col(rc), rr))
        if "preference" in ctx["gold"].row_label(cell).lower() and ctx["delivered"].has(cell):
            rec["declined_reason"] = "a surviving labelled row states the preference multiple"
            return False
    return True


def ship_custom_method(rec: dict, ctx: dict) -> bool:
    """Shared gate for every out-of-catalogue method record.

    Thresholds, long ingredient lists, mixed source bands, constants, and full
    operator sequences are intentionally not decline reasons. They are rendered
    deterministically and then audited. Only direct answer disclosure or a
    sentence the parser could not build stops the record.
    """
    if any(c in ctx["targets"] for c in rec.get("cell_keys") or []):
        rec["declined_reason"] = "method band is itself graded"
        return False
    referenced = set((rec.get("method_profile") or {}).get("references", []))
    if referenced & ctx["targets"]:
        rec["declined_reason"] = "method sentence would name another graded target"
        return False
    fields = rec.get("fields") or {}
    if not fields.get("label") or not fields.get("steps"):
        rec["declined_reason"] = "method sentence fields are incomplete"
        return False
    if not rec.get("coverage_complete"):
        rec["declined_reason"] = "method sentence does not cover every reference and literal"
        return False
    return True


def _origin(rec: dict):
    cells = rec.get("cell_keys") or []
    if not cells:
        return "", 0, 0
    sheet, coord = cells[0].split("!", 1)
    p = split_coord(coord)
    return (sheet, p[1], col_to_num(p[0])) if p else (sheet, 0, 0)


SHIP_WHEN = {
    "projection_rule": ship_projection_rule,
    "source_selection": ship_source_selection,
    "npv_timing": ship_not_a_target,
    "aggregate_scope": ship_aggregate_scope,
    "distribution_policy": ship_distribution_policy,
    "liquidation_preference": ship_liquidation_preference,
    **{entry_id: ship_custom_method for entry_id in METHOD_ENTRY_IDS},
}


def apply_ship_when(records: list[dict], ctx: dict) -> list[dict]:
    """Set each record's disposition from its registry entry's Ship when."""
    for rec in records:
        entry_id = rec.get("entry")
        if not entry_id:
            rec["disposition"] = "unclassified"
            continue
        if not registry().get(entry_id, {}).get("sentences"):
            rec["disposition"] = "unclassified"
            rec["declined_reason"] = "entry has no sentence for any value"
            continue
        if rec.get("value") not in registry()[entry_id]["sentences"]:
            # A value the registry does not word, such as terminal_value `other`.
            rec["disposition"] = "unclassified"
            rec["declined_reason"] = f"no sentence for value {rec.get('value')!r}"
            continue
        predicate = SHIP_WHEN.get(entry_id)
        if predicate is not None and not predicate(rec, ctx):
            rec["disposition"] = "suppressed"
            rec.setdefault("declined_reason", "Ship when declined for this band")
            continue
        rec["disposition"] = "disclosed"
    return records


ENTRY_PRECEDENCE = (
    list(METHOD_ROLE_PATTERNS)
    + [
        "discount_period", "inert_line", "terminal_value", "row_populated",
        "npv_timing", "aggregate_scope", "projection_rule", "distribution_policy",
        "liquidation_preference", "stake_scaling", "source_selection",
    ]
)


def _entry_rank(rec: dict) -> int:
    entry = rec.get("entry") or rec.get("family") or ""
    try:
        return ENTRY_PRECEDENCE.index(entry)
    except ValueError:
        return len(ENTRY_PRECEDENCE)


def arbitrate_overlapping(records: list[dict]) -> list[dict]:
    """First candidate that ships wins the shared cells; the rest fall through.

    A withheld or declined record does not consume the band. Only a disclosed
    record claims its cells.
    """
    winners = [r for r in records if r.get("disposition") == "disclosed"]
    winners.sort(key=_entry_rank)
    claimed: set[str] = set()
    for rec in winners:
        cells = set(rec.get("cell_keys") or [])
        if cells & claimed:
            rec["disposition"] = "suppressed"
            rec["declined_reason"] = "earlier candidate already shipped"
            continue
        claimed |= cells
    return records


def resolve_unused_conflicts(records: list[dict]) -> list[dict]:
    """Never say a row is unused and also name it as part of a total.

    Both statements can be true of the golden - an empty row really can sit
    inside a summed range - but together they tell the agent to build and not
    build the same row. The membership sentence is the more useful of the two,
    so the unused claim yields.
    """
    named = set()
    for rec in records:
        if rec.get("disposition") == "disclosed" and rec.get("entry") == "aggregate_scope":
            # The members field is a rendered phrase, so strip its lead-in before
            # comparing. Splitting it raw leaves the first member glued to "the
            # rows labelled " and it never matches.
            phrase = re.sub(r"^the rows? labelled ", "",
                            str(rec.get("fields", {}).get("members", "")))
            named.update(m.strip() for m in phrase.split(", "))
    for rec in records:
        if rec.get("entry") != "row_populated" or rec.get("disposition") != "disclosed":
            continue
        if str(rec.get("fields", {}).get("label", "")) in named:
            rec["disposition"] = "suppressed"
            rec["declined_reason"] = "row is named as a member of a disclosed total"
    return records


def record(family, value, cells, evidence, alternatives, note, row_ref=None, fields=None):
    return {
        "band": compact_cells([pretty(c) for c in cells]) if cells else row_ref,
        "cells": [pretty(c) for c in cells] if cells else ([row_ref] if row_ref else []),
        "cell_keys": cells,
        "label": note,
        "role": family,
        "family": family,
        "entry": family if family in registry() else None,
        "value": value,
        "alternatives": alternatives,
        # Set by apply_ship_when once the registry has been consulted.
        "disposition": "pending",
        "source": "convention_detector",
        "evidence": evidence,
        "note": note,
        "fields": fields or {},
        "leak_flag": False,
    }


def detect_defects(gold: Book, targets: list[str], scope: set[str]) -> list[dict]:
    out, seen_rows = [], set()
    for k in sorted(scope):
        formula = gold.formula.get(k)
        label = gold.row_label(k)
        if not (formula and label and re.search(r"\(\s*1\s*-\s*t\w*\s*\)", label, re.I)):
            continue
        m = re.search(r"\*\s*\(\s*1\s*\+\s*([^)]+)\)", formula)
        if not m:
            continue
        sheet, coord = k.split("!", 1)
        p = split_coord(coord)
        if not p or (sheet, p[1]) in seen_rows:
            continue
        rates = [gold.value.get(r) for r in refs_in("=" + m.group(1), sheet)]
        rates = [v for v in rates if isinstance(v, (int, float))]
        if rates and all(v < 0 for v in rates):
            continue
        seen_rows.add((sheet, p[1]))
        out.append({"kind": "label_sign_mismatch", "cell": pretty(k), "detail": f"Label {label!r} but formula multiplies by (1 + rate): {formula}"})
    for k in targets:
        formula = gold.formula.get(k)
        val = gold.value.get(k)
        if formula and isinstance(val, (int, float)) and abs(val) < 1e-12:
            empty = [r for r in refs_in(formula, k.split("!", 1)[0]) if not gold.has(r)]
            if empty:
                out.append({"kind": "empty_operand_zero_target", "cell": pretty(k), "detail": f"Target is zero because {_fmt(empty, 2)} is empty: {formula}"})
    return out


def overlap_claimed(record_cells: list[str], claimed: set[str]) -> bool:
    return any(c in claimed for c in record_cells)


def import_legacy_method_records(task_dir: Path, delivered: Book, claimed_keys: set[str]) -> list[dict]:
    """Legacy hints are evidence, not shippable disclosure.

    Earlier versions imported non-redundant old hints as `method` records. The
    layer-3 verifier caught that this can re-ship known-bad prose (0523's
    "averaged forecast EBITDA" hint). Fresh method records must come from the
    model/context pass with reviewer evidence, not from stale instructions.
    """
    return []


def coalesce_same_band(records: list[dict]) -> list[dict]:
    """Enforce the single-record-per-band contract.

    A cell can legitimately encode two safe conventions, for example a purchase
    price can both read from another sheet and apply ownership scaling. The writer
    still needs one bullet for the band, so combine same-band convention records.
    """
    grouped = defaultdict(list)
    for rec in records:
        grouped[rec.get("band") or ""].append(rec)
    out = []
    for band, items in grouped.items():
        if len(items) == 1 or not band:
            out.extend(items)
            continue
        # Keep non-convention or review-only records separate if a collision ever
        # appears there; those need human attention rather than automatic merging.
        mergeable = [
            item for item in items
            if item.get("disposition") == "convention" and not item.get("review_only")
        ]
        others = [item for item in items if item not in mergeable]
        if len(mergeable) <= 1:
            out.extend(items)
            continue
        base = dict(mergeable[0])
        families = [item["family"] for item in mergeable]
        values = [f"{item['family']}={item['value']}" for item in mergeable]
        notes = [item.get("note", "") for item in mergeable if item.get("note")]
        alternatives = []
        evidence = []
        for item in mergeable:
            alternatives.extend(item.get("alternatives", []))
            if item.get("evidence"):
                evidence.append(item["evidence"])
        base.update({
            "role": "multiple_conventions",
            "family": "multiple_conventions",
            "value": "; ".join(values),
            "alternatives": list(dict.fromkeys(alternatives)),
            "note": " ".join(notes),
            "evidence": " | ".join(evidence),
            "merged_families": families,
        })
        out.append(base)
        out.extend(others)
    return out


def detect_records(args) -> dict:
    task_dir = Path(args.task_dir).resolve()
    selection = read_stage(task_dir, "bands", Path(args.runs_root))
    gold = Book(Path(selection["golden"]))
    delivered = Book(Path(selection["delivered"]))
    scope = selected_keys(selection)
    targets = selection["target_keys"]
    target_set = set(targets)

    # Custom methods get first claim. Standard, ambiguous and uncertain
    # assessments remain reviewer-visible but do not claim the band, so the
    # convention detectors still get their normal chance.
    resolutions_path = getattr(args, "role_resolutions", None)
    if resolutions_path:
        resolutions_path = Path(resolutions_path)
    else:
        resolutions_path = default_role_resolutions_path(task_dir, Path(args.runs_root))
    method_records, method_assessments, custom_claimed = detect_custom_methods(
        gold, delivered, selection.get("bands", []), target_set,
        resolutions_path=resolutions_path,
    )
    custom_calibration = calibrate_legacy_custom(task_dir, method_assessments, scope)
    convention_scope = scope - custom_claimed
    convention_records = []
    for fn in (
        detect_discount_period,
        detect_inert_line,
        detect_terminal_value,
        detect_npv_timing,
        detect_distribution_policy,
        # Ahead of stake_scaling: order is precedence, and stake_scaling ships
        # on `always`, so it cannot be beaten by suppression.
        detect_liquidation_preference,
        detect_stake_scaling,
        detect_source_selection,
    ):
        convention_records.extend(fn(gold, convention_scope))
    convention_records.extend(detect_projection_rule(gold, delivered, convention_scope))
    convention_records.extend(detect_row_populated(
        gold, delivered, convention_scope, targets
    ))
    convention_records.extend(detect_aggregate_scope(gold, delivered, targets))
    convention_records = [
        rec for rec in convention_records
        if not (set(rec.get("cell_keys", [])) & custom_claimed)
    ]
    records = method_records + convention_records

    unique = []
    seen = set()
    claimed_keys = set()
    for rec in records:
        sig = (rec["family"], tuple(rec["cells"]))
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(rec)
        claimed_keys.update(rec.get("cell_keys", []))

    defects = detect_defects(gold, targets, scope)

    for rec in unique:
        rec["leak_flag"] = any(c in target_set for c in rec.get("cell_keys", []))

    drift = check_registry_drift({r["family"] for r in unique})
    if drift:
        raise SystemExit("registry drift:\n  " + "\n  ".join(drift))

    ctx = {"gold": gold, "delivered": delivered, "targets": target_set}
    unique = apply_ship_when(unique, ctx)
    unique = arbitrate_overlapping(unique)
    unique = resolve_unused_conflicts(unique)
    # Section 2 only. A method on a graded target reconstructs the answer
    # from kept inputs; the registry draws that line at "is the band graded"
    # and does not extend it to Section 1. liquidation_preference on 0668
    # D43/D49 is the standing case: the cells are graded and the convention
    # still ships.
    for rec in unique:
        if (
            rec.get("leak_flag")
            and rec["disposition"] == "disclosed"
            and rec.get("entry") in METHOD_ENTRY_IDS
        ):
            rec["disposition"] = "suppressed"
            rec["declined_reason"] = "method band is itself graded"

    claimed = {c for rec in unique for c in rec.get("cell_keys", [])}
    by_disposition = Counter(r["disposition"] for r in unique)
    payload = {
        "schema_version": "2.0",
        "task": task_dir.name,
        "records": sorted(unique, key=lambda r: (r["disposition"], r["family"], r["band"] or "")),
        "method_assessments": method_assessments,
        "custom_calibration": custom_calibration,
        "defects": defects,
        "summary": {
            "records": len(unique),
            "by_disposition": dict(by_disposition),
            "disclosed": by_disposition.get("disclosed", 0),
            "suppressed": by_disposition.get("suppressed", 0),
            "unclassified": by_disposition.get("unclassified", 0),
            "uncited_records": sum(1 for r in unique if not r.get("entry")),
            "selected_cells": len(scope),
            "claimed_selected_cells": len(scope & claimed),
            "unexplained_cells": len(scope - claimed),
            "leak_flags": sum(1 for r in unique if r.get("leak_flag")),
            "method_assessments": dict(Counter(
                a.get("status", "unknown") for a in method_assessments
            )),
            "custom_claimed_cells": len(custom_claimed),
            "legacy_custom_calibration": custom_calibration.get("outcomes", {}),
        },
    }
    return payload


def cmd_roles(args):
    """Collect unique Filter 1 role collisions. No formulas or values."""
    task_dir = Path(args.task_dir).resolve()
    selection = read_stage(task_dir, "bands", Path(args.runs_root))
    gold = Book(Path(selection["golden"]))
    prepared = prepare_method_items(gold, selection.get("bands", []))
    cases = collect_ambiguous_role_cases(prepared)
    payload = {
        "schema_version": "1.0",
        "task": task_dir.name,
        "cases": cases,
        "case_count": len(cases),
        "band_count": sum(c["band_count"] for c in cases),
    }
    out = Path(args.out) if args.out else run_dir(task_dir, Path(args.runs_root)) / "ambiguous_roles.json"
    write_json(out, payload)
    print(
        f"{task_dir.name}: {payload['case_count']} ambiguous-role case(s) "
        f"covering {payload['band_count']} band(s) -> {out}"
    )


def cmd_detect(args):
    payload = detect_records(args)
    out = Path(args.out) if args.out else run_dir(Path(args.task_dir), Path(args.runs_root)) / "records.json"
    write_json(out, payload)
    s = payload["summary"]
    print(f"{payload['task']}: {s['records']} records "
          f"({s['disclosed']} disclosed, {s['suppressed']} suppressed, "
          f"{s['unclassified']} unclassified), {s['unexplained_cells']} cells no entry explains")


# --------------------------------------------------------------------------- probe/context/write/verify


def cmd_probe(args):
    task_dir = Path(args.task_dir).resolve()
    selection = read_stage(task_dir, "bands", Path(args.runs_root))
    started = time.time()
    payload = {
        "schema_version": "1.0",
        "task": task_dir.name,
        "status": "heuristic",
        "reason": "Per-alternative evaluator perturbation is not run in this pre-run implementation unless ast_out/seg_out artifacts are present.",
        "bands": [
            {
                "band": b["band"],
                "cells": b["cells"],
                "can_move_graded_answer": True,
                "measurement": "selected_by_closure",
                "divergence": {},
            }
            for b in selection.get("bands", [])
        ],
        "elapsed_s": round(time.time() - started, 3),
    }
    out = Path(args.out) if args.out else run_dir(task_dir, Path(args.runs_root)) / "probe.json"
    write_json(out, payload)
    print(f"{task_dir.name}: probe wrote {len(payload['bands'])} heuristic band measurements")


def role_hints(text: str) -> list[str]:
    patterns = {
        "depreciation_amortization": (r"\bdepreciat", r"\bamorti[sz]", r"\bd&a\b"),
        "interest_expense_income": (r"\binterest", r"\bfinance cost"),
        "tax": (r"\btax", r"\bcurrent tax", r"\bcash tax"),
        "revenue": (r"\brevenue", r"\bsales\b", r"\bturnover"),
        "operating_expense": (r"\bopex\b", r"\boperating expense", r"\bsg&a\b", r"\brent\b"),
        "working_capital": (r"\bworking capital\b", r"\breceivable", r"\binventor", r"\bpayable"),
        "capital_expenditure": (r"\bcapex\b", r"\bcapital expenditure"),
        "discounting_valuation": (r"\bdiscount", r"\bpresent value", r"\bterminal value", r"\bnpv\b"),
        "returns": (r"\birr\b", r"\bxirr\b", r"\bmoic\b"),
        "cash_flow_metric": (r"\bcash flow\b", r"\bfcf\b"),
        "profit_metric": (r"\bebitda\b", r"\bebit\b", r"\bnet profit\b"),
    }
    lowered = text.lower()
    return [role for role, pats in patterns.items() if any(re.search(p, lowered) for p in pats)]


def cmd_context(args):
    task_dir = Path(args.task_dir).resolve()
    selection = read_stage(task_dir, "bands", Path(args.runs_root))
    records_path = run_dir(task_dir, Path(args.runs_root)) / "records.json"
    claimed = set()
    if records_path.exists():
        records = json.loads(records_path.read_text(encoding="utf-8")).get("records", [])
        claimed = {c for r in records for c in r.get("cell_keys", [])}
    context = []
    for band in selection.get("bands", []):
        if any(c in claimed for c in band.get("cell_keys", [])):
            continue
        text = " ".join([band.get("label", "")] + band.get("formula_samples", []))
        context.append({
            "band": band["band"],
            "label": band.get("label", ""),
            "cells": band.get("cells", []),
            "formula_samples": band.get("formula_samples", []),
            "role_hints": role_hints(text),
            "instruction": "A model may propose role and plausible alternatives here; it must not decide disposition.",
        })
    payload = {"schema_version": "1.0", "task": task_dir.name, "residue": context}
    out = Path(args.out) if args.out else run_dir(task_dir, Path(args.runs_root)) / "context.json"
    write_json(out, payload)
    print(f"{task_dir.name}: {len(context)} residue bands for model context")


def compact_cells(cells: list[str]) -> str:
    if not cells:
        return ""
    if len(cells) == 1 or "!row " in cells[0] or " members" in cells[0]:
        return "`%s`" % cells[0]
    parsed = []
    for c in cells:
        sheet, coord = c.rsplit("!", 1)
        p = split_coord(coord)
        if not p:
            return ", ".join(f"`{x}`" for x in cells[:4])
        parsed.append((sheet, p[1], col_to_num(p[0]), coord))
    if len({p[0] for p in parsed}) == 1 and len({p[1] for p in parsed}) == 1:
        parsed.sort(key=lambda x: x[2])
        return f"`{parsed[0][0]}!{parsed[0][3]}:{parsed[-1][3]}`"
    shown = ", ".join(f"`{c}`" for c in cells[:4])
    return shown + (f" and {len(cells) - 4} more" if len(cells) > 4 else "")


def agent_records(records: list[dict]) -> list[dict]:
    """Only cited, disclosed records reach the agent."""
    out = []
    for rec in records:
        if not rec.get("entry"):
            continue
        if rec.get("leak_flag") and rec.get("entry") in METHOD_ENTRY_IDS:
            continue
        if rec.get("disposition") != "disclosed":
            continue
        if not render_sentence(rec):
            continue
        out.append({k: v for k, v in rec.items()
                    if k not in ("evidence", "cell_keys", "divergence", "declined_reason")})
    return out


def render_sentence(rec: dict) -> str:
    """Fill the registry entry's sentence for this record's value.

    Wording lives in REGISTRY.md so the phrasing sits beside the definition that
    licenses it. A record whose placeholders cannot be filled renders nothing
    rather than emitting a half-written sentence.
    """
    entry = registry().get(rec.get("entry") or "", {})
    template = entry.get("sentences", {}).get(rec.get("value") or "")
    if not template:
        return ""
    fields = rec.get("fields") or {}
    # An empty field means the detector could not name the thing cleanly. Render
    # nothing rather than a sentence with a hole in it.
    if any(not str(v).strip() for v in fields.values()):
        return ""
    try:
        text = template.format(**fields)
    except (KeyError, IndexError):
        return ""
    return text if "{" not in text else ""


def render_section(records: list[dict]) -> str:
    """One bullet per band, joining the sentences of every record on that band."""
    if not records:
        return ""
    lines = [
        SECTION_START,
        "",
        "These are choices the original model made that the delivered file no longer shows.",
        "They describe how the model works, not the figures you are asked to report.",
        "",
    ]
    by_band: dict = {}
    for rec in records:
        by_band.setdefault(rec.get("band") or "", []).append(rec)
    seen_text: set = set()
    for band in sorted(by_band):
        sentences = [s for s in (render_sentence(r) for r in by_band[band]) if s]
        if not sentences:
            continue
        body = " ".join(dict.fromkeys(sentences))
        cells = compact_cells(by_band[band][0].get("cells", []))
        # A colliding sentence is re-anchored, never dropped. 0618 and 0632 both
        # lost a disclosed record this way: same label, byte-identical body.
        if body in seen_text:
            sheet = ""
            first = (by_band[band][0].get("cells") or [""])[0]
            if isinstance(first, str) and "!" in first:
                sheet = first.split("!", 1)[0].strip("`'\"")
            candidates = []
            if sheet:
                candidates.append("On the %s sheet, %s%s" % (
                    sheet, body[0].lower(), body[1:],
                ))
            cells_plain = cells.strip("`")
            if cells_plain:
                candidates.append("At %s, %s%s" % (
                    cells_plain, body[0].lower(), body[1:],
                ))
            for cand in candidates:
                if cand not in seen_text:
                    body = cand
                    break
        seen_text.add(body)
        lines.append(f"- {cells}: {body}")
    return "\n".join(lines) + "\n"


def numeric_targets(task_dir: Path) -> list[float]:
    targets, _ = load_key(task_dir)
    return [
        float(v)
        for v in targets.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]


def internal_tokens() -> list[str]:
    """Entry ids and value tokens, none of which may appear in agent-facing text."""
    tokens = set(registry())
    for entry in registry().values():
        tokens.update(a for a in entry.get("alternatives", []) if "_" in a)
    return sorted(tokens)


def audit_text(section: str, task_dir: Path) -> list[str]:
    faults = []
    formula_lines = [line for line in section.splitlines() if FORMULA_RE.search(line)]
    if formula_lines:
        faults.append(f"{len(formula_lines)} formula-shaped line(s)")
    leaked = [t for t in internal_tokens() if re.search(r"\b%s\b" % re.escape(t), section)]
    if leaked:
        faults.append(f"internal taxonomy token(s) in agent text: {leaked[:5]}")
    targets = numeric_targets(task_dir)
    for raw in NUMBER_RE.findall(section):
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        for target in targets:
            # Zero/one are control literals in branches and collide with zero
            # checks or boolean targets by accident. Larger integers receive the
            # normal answer-collision audit.
            if abs(float(target)) <= 1e-12 or (
                float(target).is_integer() and abs(target) <= 1
            ):
                continue
            if abs(val - target) <= 1e-12 * max(1.0, abs(target)):
                faults.append(f"numeric literal {raw} matches target {target}")
    return faults


def clone_task(source: Path, out: Path):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(source, out, ignore=shutil.ignore_patterns(
        "*-traces", "__pycache__", "*.pyc", "gap_attrib.py", "verify_channels.py"
    ))


def write_disclosure(args) -> dict:
    source_task = Path(args.task_dir).resolve()
    records_payload = read_stage(source_task, "records", Path(args.runs_root))
    safe = agent_records(records_payload.get("records", []))
    section = render_section(safe)
    faults = audit_text(section, source_task)
    if faults and not args.force:
        raise SystemExit("refusing to write disclosure: " + "; ".join(faults[:5]))
    dst = Path(args.out).resolve() if args.out else source_task
    if dst != source_task:
        clone_task(source_task, dst)
    instruction_path = dst / "instruction.md"
    text = instruction_path.read_text(encoding="utf-8")
    text = strip_agent_sections(text)
    if OUTPUT_HEADING not in text:
        raise SystemExit(f"{instruction_path} has no Output heading")
    text = text.replace(OUTPUT_HEADING, "\n" + section + "\n## Output\n", 1)
    if not args.dry_run:
        instruction_path.write_text(text, encoding="utf-8")
        reviewer = {
            **records_payload,
            "agent_records": safe,
            "audit": {"faults": faults, "written_records": len(safe)},
        }
        write_json(dst / "tests" / "disclosure.json", reviewer)
    return {"task": source_task.name, "out": str(dst), "written_records": len(safe), "faults": faults}


def strip_agent_sections(text: str) -> str:
    """Remove disclosure sections written by either old tool or this one."""
    for heading in STALE_AGENT_SECTIONS:
        while heading in text:
            head, rest = text.split(heading, 1)
            parts = rest.split("\n## ", 1)
            text = head + ("\n## " + parts[1] if len(parts) > 1 else "")
    return text


def cmd_write(args):
    result = write_disclosure(args)
    print(f"{result['task']}: wrote {result['written_records']} disclosure record(s) -> {result['out']}")


def verify_task(task_dir: Path) -> dict:
    disclosure_path = task_dir / "tests" / "disclosure.json"
    faults = []
    if not disclosure_path.exists():
        return {"task": task_dir.name, "passed": False, "faults": ["missing tests/disclosure.json"]}
    payload = json.loads(disclosure_path.read_text(encoding="utf-8"))
    delivered = Book(find_environment(task_dir))
    shipped = payload.get("agent_records", [])
    visible_cells = sorted({
        cell
        for record in shipped
        for cell in record.get("cell_keys", [])
    })
    nonblank = [pretty(c) for c in visible_cells if delivered.has(c)]
    if nonblank:
        faults.append(
            f"{len(nonblank)} agent-visible disclosure cells are non-blank "
            "in delivered workbook"
        )
    # One record per (band, entry). Two entries may legitimately describe one band;
    # the writer joins them into a single bullet.
    seen = Counter((r.get("band"), r.get("entry")) for r in payload.get("records", []))
    dupes = [b for b, count in seen.items() if b[0] and count > 1]
    if dupes:
        faults.append(f"duplicate record band/entry pairs: {dupes[:5]}")
    uncited = [r.get("band") for r in shipped if not r.get("entry")]
    if uncited:
        faults.append(f"{len(uncited)} shipped record(s) cite no registry entry")
    unknown = sorted({r.get("entry") for r in shipped if r.get("entry") not in registry()})
    if unknown:
        faults.append(f"shipped record(s) cite unknown registry entries: {unknown[:5]}")
    wrong_disposition = [r.get("band") for r in shipped if r.get("disposition") != "disclosed"]
    if wrong_disposition:
        faults.append(f"{len(wrong_disposition)} shipped record(s) are not disclosed")
    section = ""
    text = (task_dir / "instruction.md").read_text(encoding="utf-8")
    for heading in STALE_AGENT_SECTIONS[:-1]:
        if heading in text:
            faults.append(f"stale agent-facing section remains: {heading}")
    if SECTION_START in text:
        section = SECTION_START + text.split(SECTION_START, 1)[1].split("\n## Output", 1)[0]
    custom = [
        r for r in payload.get("records", [])
        if r.get("source") == "custom_method_detector"
        and r.get("disposition") == "disclosed"
        and not r.get("leak_flag")
    ]
    convention_cells = {
        c for r in payload.get("records", [])
        if r.get("source") == "convention_detector"
        for c in r.get("cell_keys", [])
    }
    overlap = sorted({
        c for r in custom for c in r.get("cell_keys", []) if c in convention_cells
    })
    if overlap:
        faults.append(f"custom and convention records overlap on {len(overlap)} cell(s)")
    missing_render = []
    for rec in custom:
        sentence = render_sentence(rec)
        cells_text = compact_cells(rec.get("cells", []))
        if not sentence or sentence not in section or cells_text not in section:
            missing_render.append(rec.get("band"))
        if not rec.get("coverage_complete"):
            faults.append(f"custom record lacks AST coverage proof: {rec.get('band')}")
    if missing_render:
        faults.append(f"{len(missing_render)} custom record(s) did not render: {missing_render[:5]}")

    if custom:
        try:
            gold = Book(find_golden(task_dir))
            structural = []
            for rec in custom:
                profile = formula_profile(gold, {
                    "band": rec.get("band"),
                    "cell_keys": rec.get("cell_keys", []),
                    "label": (rec.get("method_profile") or {}).get("label", ""),
                    "pattern": (rec.get("method_profile") or {}).get("pattern", ""),
                })
                if profile.get("complete") and structural_reason(profile):
                    structural.append(rec.get("band"))
            if structural:
                faults.append(
                    f"{len(structural)} custom record(s) are hard structural: {structural[:5]}"
                )
        except Exception as exc:
            faults.append(f"custom structural verification failed: {exc}")
    faults.extend(audit_text(section, task_dir))
    return {"task": task_dir.name, "passed": not faults, "faults": faults}


def cmd_verify(args):
    result = verify_task(Path(args.task_dir).resolve())
    out = Path(args.out) if args.out else run_dir(Path(args.task_dir), Path(args.runs_root)) / "verify.json"
    write_json(out, result)
    print(f"{result['task']}: {'PASS' if result['passed'] else 'FAIL'}")
    for fault in result["faults"][:10]:
        print(f"  - {fault}")
    if result["faults"] and not args.no_fail:
        raise SystemExit(2)


# --------------------------------------------------------------------------- bench/migrate


def count_hint_cells(task_dir: Path):
    path = task_dir / "tests" / "formula_hints.json"
    if not path.exists():
        return 0, 0, 0, 0
    spec = json.loads(path.read_text(encoding="utf-8"))
    delivered = Book(find_environment(task_dir))
    hints = spec.get("hints", [])
    all_cells = []
    full_redundant = partial = 0
    for hint in hints:
        cells = sorted({c for band in hint.get("bands", []) for c in cells_from_band_ref(band)})
        filled = [c for c in cells if delivered.has(c)]
        all_cells.extend(cells)
        if cells and len(filled) == len(cells):
            full_redundant += 1
        elif filled:
            partial += 1
    filled_total = sum(1 for c in all_cells if delivered.has(c))
    return len(hints), len(set(all_cells)), filled_total, full_redundant + partial


def convention_counts(task_dir: Path):
    path = task_dir / "tests" / "conventions.json"
    if not path.exists():
        return 0, Counter()
    data = json.loads(path.read_text(encoding="utf-8"))
    return len(data.get("conventions", [])), Counter(r.get("family") for r in data.get("conventions", []))


def bench_tasks(tasks_root: Path, runs_root: Path = DEFAULT_RUNS_ROOT) -> dict:
    tasks = sorted(tasks_root.glob("*-outputs"))
    total = Counter()
    families = Counter()
    task_rows = []
    for task in tasks:
        hints, hint_cells, filled, redundant_hints = count_hint_cells(task)
        conventions, fams = convention_counts(task)
        families.update(fams)
        disclosure_path = task / "tests" / "disclosure.json"
        disclosed = 0
        if disclosure_path.exists():
            disclosed = len(json.loads(disclosure_path.read_text(encoding="utf-8")).get("agent_records", []))
        instruction = (task / "instruction.md").read_text(encoding="utf-8") if (task / "instruction.md").exists() else ""
        old_hint_section = int("## Custom formula hints" in instruction)
        old_manifest_section = int("## Modelling conventions in this workbook" in instruction)
        unified_section = int(SECTION_START in instruction)
        total.update({
            "tasks": 1,
            "hints": hints,
            "hint_cells": hint_cells,
            "filled_hint_cells": filled,
            "redundant_or_partial_hints": redundant_hints,
            "convention_records": conventions,
            "disclosure_records": disclosed,
            "agent_custom_hint_sections": old_hint_section,
            "agent_old_manifest_sections": old_manifest_section,
            "agent_unified_sections": unified_section,
        })
        task_rows.append({
            "task": task.name,
            "hints": hints,
            "hint_cells": hint_cells,
            "filled_hint_cells": filled,
            "redundant_or_partial_hints": redundant_hints,
            "convention_records": conventions,
            "disclosure_records": disclosed,
            "agent_custom_hint_section": bool(old_hint_section),
            "agent_old_manifest_section": bool(old_manifest_section),
            "agent_unified_section": bool(unified_section),
        })
    return {
        "schema_version": "1.0",
        "tasks_root": str(tasks_root),
        "totals": dict(total),
        "convention_families": dict(families),
        "tasks": task_rows,
    }


def cmd_bench(args):
    payload = bench_tasks(Path(args.tasks_root).resolve(), Path(args.runs_root))
    out = Path(args.out) if args.out else Path(args.runs_root) / "bench.json"
    write_json(out, payload)
    t = payload["totals"]
    print(
        f"{t.get('tasks', 0)} tasks; {t.get('hints', 0)} old hints; "
        f"{t.get('filled_hint_cells', 0)} filled hint cells; "
        f"{t.get('convention_records', 0)} old convention records"
    )


def ensure_pipeline(task: Path, args):
    class A:
        pass
    a = A()
    a.task_dir = str(task)
    a.golden = None
    a.ast_dir = args.ast_dir
    a.seg_root = args.seg_root
    a.segmentation_mode = args.segmentation_mode
    a.expected_generation_id = getattr(args, "expected_generation_id", None)
    a.runs_root = args.runs_root
    a.out = None
    a.role_resolutions = getattr(args, "role_resolutions", None)
    cmd_select(a)
    cmd_probe(a)
    cmd_roles(a)
    cmd_detect(a)
    cmd_context(a)


def cmd_migrate(args):
    tasks_root = Path(args.tasks_root).resolve()
    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in sorted(tasks_root.glob("*-outputs")):
        ensure_pipeline(task, args)
        class W:
            pass
        w = W()
        w.task_dir = str(task)
        w.runs_root = args.runs_root
        w.out = str(out_root / task.name)
        w.force = args.force
        w.dry_run = False
        result = write_disclosure(w)
        verify = verify_task(Path(result["out"]))
        rows.append({**result, "verify_passed": verify["passed"], "verify_faults": verify["faults"]})
        print(f"{task.name}: migrated, verify={'PASS' if verify['passed'] else 'FAIL'}")
    payload = {"schema_version": "1.0", "tasks_root": str(tasks_root), "out": str(out_root), "tasks": rows}
    write_json(Path(args.runs_root) / "migration-summary.json", payload)


# --------------------------------------------------------------------------- cli


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bench")
    b.add_argument("--tasks-root", default=str(DEFAULT_TASKS_ROOT))
    b.add_argument("--out", default=None)

    for name in ("select", "probe", "roles", "detect", "context", "write", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--task-dir", required=True)
        p.add_argument("--out", default=None)
        if name == "select":
            p.add_argument("--golden", default=None)
            p.add_argument("--ast-dir", default=None)
            p.add_argument("--seg-root", default="seg_out")
            p.add_argument(
                "--segmentation-mode",
                choices=("strict", "shadow", "legacy"),
                default="strict",
            )
            p.add_argument("--expected-generation-id", default=None)
        if name == "detect":
            p.add_argument("--role-resolutions", default=None)
        if name == "write":
            p.add_argument("--force", action="store_true")
            p.add_argument("--dry-run", action="store_true")
        if name == "verify":
            p.add_argument("--no-fail", action="store_true")

    m = sub.add_parser("migrate")
    m.add_argument("--tasks-root", default=str(DEFAULT_TASKS_ROOT))
    m.add_argument("--out", required=True)
    m.add_argument("--ast-dir", default="ast_out")
    m.add_argument("--seg-root", default="seg_out")
    m.add_argument(
        "--segmentation-mode",
        choices=("strict", "shadow", "legacy"),
        default="strict",
    )
    m.add_argument("--expected-generation-id", default=None)
    m.add_argument("--force", action="store_true")
    m.add_argument("--role-resolutions", default=None)

    args = parser.parse_args(argv)
    {
        "bench": cmd_bench,
        "select": cmd_select,
        "probe": cmd_probe,
        "roles": cmd_roles,
        "detect": cmd_detect,
        "context": cmd_context,
        "write": cmd_write,
        "verify": cmd_verify,
        "migrate": cmd_migrate,
    }[args.command](args)


if __name__ == "__main__":
    main()

