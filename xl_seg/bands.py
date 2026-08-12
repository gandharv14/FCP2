"""Stage 3: quotient the cell graph into bands.

A band is a maximal run of adjacent cells in one row that share a node kind and,
after R1C1 normalisation, an identical formula. That is the unit a financial
modeller actually thinks in -- one line item, replicated across period columns.

Banding rather than whole-row grouping matters because roughly a third of rows are
mixed: hardcoded historical actuals in the left columns, projection formulas in the
right. The pattern break inside such a row *is* the input frontier, and collapsing
the whole row would erase it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .model import col_letter
from .project import AXIS, BLANK, NUMERIC, CellGraph

STRING_RE = re.compile(r'"[^"]*"')
REF_RE = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d{1,7})")


def r1c1(formula: str, row: int, col: int) -> str:
    """Translation-invariant form of a formula, so period clones collapse together."""
    if not formula:
        return ""
    strings: list[str] = []

    def stash(match):
        strings.append(match.group(0))
        return f"\x00{len(strings) - 1}\x00"

    body = STRING_RE.sub(stash, formula)

    def rewrite(match):
        col_abs, letters, row_abs, digits = match.groups()
        target_col = 0
        for ch in letters.upper():
            target_col = target_col * 26 + ord(ch) - 64
        target_row = int(digits)
        part_r = f"R{target_row}" if row_abs else f"R[{target_row - row}]"
        part_c = f"C{target_col}" if col_abs else f"C[{target_col - col}]"
        return part_r + part_c

    body = REF_RE.sub(rewrite, body)
    return re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], body)


@dataclass
class Band:
    id: str
    sheet: str
    row: int
    col_lo: int
    col_hi: int
    kind: str
    pattern: str
    vtype: str
    label: str
    cells: list
    is_mirror: bool
    is_literal: bool

    @property
    def width(self) -> int:
        return self.col_hi - self.col_lo + 1

    @property
    def value_bearing(self) -> bool:
        return self.vtype in (NUMERIC, AXIS)


@dataclass
class BandGraph:
    bands: dict[str, Band]
    of_cell: dict[str, str]
    adj: dict[str, set] = field(default_factory=dict)
    radj: dict[str, set] = field(default_factory=dict)


def _vgroup(vtype: str) -> str:
    return NUMERIC if vtype in (NUMERIC, BLANK) else vtype


def _label_for(cells, info) -> str:
    counts = Counter(
        info[c].node.label.strip() for c in cells if info[c].node.label.strip()
    )
    return counts.most_common(1)[0][0] if counts else ""


def build(cg: CellGraph) -> BandGraph:
    groups: dict[tuple, list] = defaultdict(list)
    for cid, ci in cg.info.items():
        node = ci.node
        if node.row is None or node.col is None:
            continue
        pattern = r1c1(node.formula, node.row, node.col) if node.kind == "formula" else ""
        groups[(node.sheet, node.row, node.kind, pattern, _vgroup(ci.vtype))].append(cid)

    bands: dict[str, Band] = {}
    of_cell: dict[str, str] = {}
    for (sheet, row, kind, pattern, _), members in groups.items():
        members.sort(key=lambda c: cg.info[c].node.col)
        run: list = []
        for cid in members:
            col = cg.info[cid].node.col
            if run and col != cg.info[run[-1]].node.col + 1:
                _emit(bands, of_cell, cg, sheet, row, kind, pattern, run)
                run = []
            run.append(cid)
        if run:
            _emit(bands, of_cell, cg, sheet, row, kind, pattern, run)

    adj: dict[str, set] = {}
    radj: dict[str, set] = {}
    for src, targets in cg.adj.items():
        bsrc = of_cell.get(src)
        if bsrc is None:
            continue
        for dst in targets:
            bdst = of_cell.get(dst)
            if bdst is None or bdst == bsrc:
                continue
            adj.setdefault(bsrc, set()).add(bdst)
            radj.setdefault(bdst, set()).add(bsrc)

    return BandGraph(bands=bands, of_cell=of_cell, adj=adj, radj=radj)


def attach_literal_sources(bg: BandGraph, cg: CellGraph, is_notable) -> list[dict]:
    """Stage 3.5: promote hardcoded constants inside formulas to source bands.

    ``=1134847*$C$519`` is an assumption wearing a formula's clothes: the flag it
    references makes the cell a non-source, so the constant -- the thing that
    actually has to be supplied -- would otherwise vanish into MIDDLE. Each
    notable literal becomes a synthetic zero-cell band with a single edge into
    its host, so the ordinary frontier rule (source inside the cone) classifies
    it as an input with no special-casing downstream.
    """
    records: list[dict] = []
    for bid, band in sorted(bg.bands.items()):
        if band.kind != "formula" or band.is_mirror or band.is_literal:
            continue
        if not band.value_bearing:
            continue
        cell = band.cells[0]
        seen: set = set()
        for raw in cg.info[cell].literals:
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if num in seen or not is_notable(num):
                continue
            seen.add(num)
            value_text = f"{num:.12g}"
            vid = f"{bid}#lit={value_text}"
            # The nominal span is the host's, so consumers of bands.csv (the
            # input mask) know which grid cells carry the constant.
            bg.bands[vid] = Band(
                id=vid,
                sheet=band.sheet,
                row=band.row,
                col_lo=band.col_lo,
                col_hi=band.col_hi,
                kind="literal",
                pattern="",
                vtype=NUMERIC,
                label=(f"{band.label} [hardcoded {value_text}]" if band.label
                       else f"[hardcoded {value_text}]"),
                cells=[],
                is_mirror=False,
                is_literal=True,
            )
            bg.adj.setdefault(vid, set()).add(bid)
            bg.radj.setdefault(bid, set()).add(vid)
            records.append({
                "band": vid, "host": bid, "sheet": band.sheet,
                "label": band.label, "value": num,
                "formula": cg.info[cell].node.formula,
            })
    return records


def _emit(bands, of_cell, cg, sheet, row, kind, pattern, run):
    lo = cg.info[run[0]].node.col
    hi = cg.info[run[-1]].node.col
    start = f"{col_letter(lo)}{row}"
    bid = f"{sheet}!{start}" if lo == hi else f"{sheet}!{start}:{col_letter(hi)}{row}"
    vtypes = Counter(cg.info[c].vtype for c in run)
    bands[bid] = Band(
        id=bid,
        sheet=sheet,
        row=row,
        col_lo=lo,
        col_hi=hi,
        kind=kind,
        pattern=pattern,
        vtype=vtypes.most_common(1)[0][0],
        label=_label_for(run, cg.info),
        cells=list(run),
        is_mirror=all(cg.info[c].is_mirror for c in run),
        is_literal=all(cg.info[c].is_literal for c in run),
    )
    for cid in run:
        of_cell[cid] = bid
