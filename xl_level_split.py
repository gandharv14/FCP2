#!/usr/bin/env python3
"""Split a workbook into cumulative dependency-level snapshots.

Every formula cell sits at a level: level 0 is everything nothing feeds (typed
inputs, hardcodes, headers), level 1 is what can be worked out from level 0 alone,
and so on up the longest path through the dependency DAG built by ``xl_ast_graph``.

This writes one workbook per cutoff -- L0, then L0+L1, then L0+L1+L2 -- each one
keeping all of the original tabs. Cells at or below the cutoff hold their cached
value; cells above it are emptied but keep their formatting, and static text and
numbers are present in every snapshot so the sheets stay readable.

The originals are rewritten at the XML level rather than round-tripped through
openpyxl, so column widths, number formats, conditional formatting, merges, charts
and images all survive untouched. Every formula is replaced by its value, which
makes the output static: ``xl/calcChain.xml`` is dropped along with it.

Requires only openpyxl (for the dependency graph and A1 parsing).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path

try:
    from openpyxl.utils.cell import coordinate_to_tuple
except ImportError:  # pragma: no cover
    sys.exit("openpyxl is required:  python3 -m pip install openpyxl")

try:
    from xl_ast_graph import AstGraph, human_size
except ImportError:  # pragma: no cover
    sys.exit("xl_level_split.py expects xl_ast_graph.py to sit next to it")


# Cells whose formula the parser could not read land here: they only appear in the
# final snapshot, since we cannot say what they depend on.
UNKNOWN_LEVEL = -1

CELL_RE = re.compile(rb"<c(?:\s[^>]*?)?(?:/>|>.*?</c>)", re.S)
FORMULA_RE = re.compile(rb"<f(?:\s[^>]*?)?(?:/>|>.*?</f>)", re.S)
VALUE_RE = re.compile(rb"<v(?:\s[^>]*?)?(?:/>|>(.*?)</v>)", re.S)
SHEET_TAG_RE = re.compile(rb"<sheet\s[^>]*?>")
REL_TAG_RE = re.compile(rb"<Relationship\s[^>]*?>")
CALC_OVERRIDE_RE = re.compile(rb"<Override[^>]*calcChain\.xml[^>]*/>")
CALC_REL_RE = re.compile(rb"<Relationship[^>]*calcChain\.xml[^>]*/>")
NUMERIC_ENTITY_RE = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")

CALC_CHAIN = "xl/calcChain.xml"

# Chart parts keep their own cached copy of every plotted value. In a masked or
# level-cut workbook those caches hold numbers whose grid cells were just
# blanked, so they must go: Excel rebuilds them from the sheet on open. The
# element names are matched under any namespace prefix (c:, c15:, ...).
CHART_NUM_CACHE_RE = re.compile(
    rb"<(?:[A-Za-z0-9]+:)?numCache>.*?</(?:[A-Za-z0-9]+:)?numCache>", re.S)
CHART_STR_CACHE_RE = re.compile(
    rb"<(?:[A-Za-z0-9]+:)?strCache>.*?</(?:[A-Za-z0-9]+:)?strCache>", re.S)
CHART_MULTI_CACHE_RE = re.compile(
    rb"<(?:[A-Za-z0-9]+:)?multiLvlStrCache>.*?</(?:[A-Za-z0-9]+:)?multiLvlStrCache>",
    re.S)
# Modern chartEx parts store the cached points directly as <cx:pt> inside
# numeric/string dimensions instead of a *Cache element.
CHARTEX_PT_RE = re.compile(
    rb"<(?:[A-Za-z0-9]+:)?pt(?:\s[^>]*)?(?:/>|>.*?</(?:[A-Za-z0-9]+:)?pt>)", re.S)


def is_chart_part(name):
    return name.startswith("xl/charts/") and name.endswith(".xml")


def scrub_chart_caches(name, data):
    """Drop cached series values from a chart part; returns (data, hits).

    Classic charts lose their numCache/strCache elements (optional children of
    the series references); chartEx parts lose the cached <pt> entries. The
    cell references that drive the chart survive, so Excel simply re-reads the
    grid -- which is exactly the set of values the mask decided may be seen.
    """
    hits = 0

    def cut(pattern, blob):
        nonlocal hits
        blob, n = pattern.subn(b"", blob)
        hits += n
        return blob

    data = cut(CHART_NUM_CACHE_RE, data)
    data = cut(CHART_STR_CACHE_RE, data)
    data = cut(CHART_MULTI_CACHE_RE, data)
    if "chartex" in name.lower():
        data = cut(CHARTEX_PT_RE, data)
    return data, hits


def attr(tag, name):
    """Value of an XML attribute inside a raw start tag, or None."""
    found = re.search(rb'\s' + re.escape(name) + rb'="([^"]*)"', tag)
    return found.group(1) if found else None


def xml_text(raw):
    """Decode an attribute value, undoing the five XML entities and numeric refs."""
    if raw is None:
        return ""
    text = raw.decode("utf-8")
    text = NUMERIC_ENTITY_RE.sub(
        lambda m: chr(int(m.group(1)[1:], 16) if m.group(1)[0] in "xX"
                      else int(m.group(1))), text)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return text


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------

def cell_levels(graph):
    """Longest path from a source over the cell-only projection of the DAG.

    Operator and constant nodes collapse into the cell that owns them, so a level
    counts calculation steps between cells rather than steps inside one formula.
    """
    owner_of = {}
    real = set()
    for node_id, node in graph.nodes.items():
        if node["kind"] in ("op", "const"):
            owner_of[node_id] = node["owner"] or node_id
        else:
            real.add(node_id)

    succ = defaultdict(set)
    indeg = defaultdict(int)
    for edge in graph.edges:
        source = owner_of.get(edge["source"], edge["source"])
        target = owner_of.get(edge["target"], edge["target"])
        if source == target or source not in real or target not in real:
            continue
        if target not in succ[source]:
            succ[source].add(target)
            indeg[target] += 1

    level = dict.fromkeys(real, 0)
    queue = deque(node_id for node_id in real if not indeg[node_id])
    while queue:
        node_id = queue.popleft()
        for other in succ.get(node_id, ()):
            if level[other] < level[node_id] + 1:
                level[other] = level[node_id] + 1
            indeg[other] -= 1
            if not indeg[other]:
                queue.append(other)

    # cells in a circular reference never reach in-degree zero; a few bounded
    # relaxations drop them below their acyclic precedents instead of into level 0
    cyclic = {node_id for node_id in real if indeg[node_id] > 0}
    for _ in range(4):
        moved = False
        for node_id in real:
            for other in succ.get(node_id, ()):
                if other in cyclic and level[other] < level[node_id] + 1:
                    level[other] = level[node_id] + 1
                    moved = True
        if not moved:
            break
    return level, cyclic


def levels_by_sheet(graph):
    """{sheet: {(row, col): level}} for every real cell, plus the deepest level."""
    level, cyclic = cell_levels(graph)
    placed = defaultdict(dict)
    deepest = 0
    for node_id, node in graph.nodes.items():
        if node["kind"] in ("op", "const", "range", "name", "external"):
            continue
        if node["row"] is None or node["col"] is None:
            continue
        depth = level.get(node_id, 0)
        placed[node["sheet"]][(node["row"], node["col"])] = depth
        deepest = max(deepest, depth)

    unparsed = 0
    for sheet, cells in graph.formulas.items():
        for coord in cells:
            if coord not in placed[sheet]:
                placed[sheet][coord] = UNKNOWN_LEVEL
                unparsed += 1
    return placed, deepest, len(cyclic), unparsed


def cutoff_list(deepest, stride, cap):
    """Which cumulative cutoffs to emit, always including the complete workbook."""
    cutoffs = list(range(0, deepest + 1, max(1, stride)))
    if cutoffs[-1] != deepest:
        cutoffs.append(deepest)
    if cap and len(cutoffs) > cap:
        if cap == 1:
            return [deepest]
        step = (len(cutoffs) - 1) / float(cap - 1)
        cutoffs = sorted({cutoffs[int(round(i * step))] for i in range(cap)})
    return cutoffs


# ---------------------------------------------------------------------------
# xlsx rewriting
# ---------------------------------------------------------------------------

def build_cell(ref, style, kind, inner):
    out = b'<c r="' + ref + b'"'
    if style is not None:
        out += b' s="' + style + b'"'
    if kind:
        out += b' t="' + kind + b'"'
    if inner:
        return out + b">" + inner + b"</c>"
    return out + b"/>"


def rewrite_sheet(xml, levels, cutoff, tally):
    """Freeze formulas at or below `cutoff` to their value and empty the rest."""
    def fix(match):
        cell = match.group(0)
        if FORMULA_RE.search(cell) is None:
            # typed text and numbers stay in every snapshot, untouched; the rest are
            # cells that carry nothing but formatting
            if b"<v" in cell or b"<is" in cell:
                tally["static"] += 1
            return cell

        head = cell[:cell.index(b">")]
        ref = attr(head, b"r")
        if ref is None:
            return cell
        style = attr(head, b"s")
        row, col = coordinate_to_tuple(ref.decode("ascii"))
        level = levels.get((row, col), UNKNOWN_LEVEL)

        if level == UNKNOWN_LEVEL or level > cutoff:
            tally["emptied"] += 1
            return build_cell(ref, style, None, b"")

        value = VALUE_RE.search(cell)
        if value is None:
            tally["uncalculated"] += 1
            return build_cell(ref, style, None, b"")
        tally["frozen"] += 1
        text = value.group(1) or b""  # <v/> is a formula that worked out to ""
        kind = attr(head, b"t")
        if kind == b"str":
            # a formula's string result; without the formula it has to be inline
            return build_cell(ref, style, b"inlineStr",
                              b'<is><t xml:space="preserve">' + text + b"</t></is>")
        if not text.strip():
            return build_cell(ref, style, None, b"")
        return build_cell(ref, style, kind, b"<v>" + text + b"</v>")

    return CELL_RE.sub(fix, xml)


def sheet_parts(zf):
    """[(sheet name, zip part)] in workbook order."""
    targets = {}
    for match in REL_TAG_RE.finditer(zf.read("xl/_rels/workbook.xml.rels")):
        rel_id, target = attr(match.group(0), b"Id"), attr(match.group(0), b"Target")
        if rel_id and target:
            targets[rel_id] = target.decode("utf-8")
    out = []
    for match in SHEET_TAG_RE.finditer(zf.read("xl/workbook.xml")):
        tag = match.group(0)
        rel_id = attr(tag, b"r:id")
        if rel_id is None:
            found = re.search(rb'\s[A-Za-z0-9]+:id="([^"]*)"', tag)
            rel_id = found.group(1) if found else None
        target = targets.get(rel_id)
        if not target:
            continue
        part = target.lstrip("/") if target.startswith("/") else "xl/" + target
        out.append((xml_text(attr(tag, b"name")), part.replace("\\", "/")))
    return out


def write_snapshot(src, out_path, part_levels, cutoff, tally):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            if item.filename == CALC_CHAIN:
                continue
            data = src.read(item.filename)
            if item.filename in part_levels:
                data = rewrite_sheet(data, part_levels[item.filename], cutoff, tally)
            elif is_chart_part(item.filename):
                data, hits = scrub_chart_caches(item.filename, data)
                tally["chart_caches"] += hits
            elif item.filename == "[Content_Types].xml":
                data = CALC_OVERRIDE_RE.sub(b"", data)
            elif item.filename == "xl/_rels/workbook.xml.rels":
                data = CALC_REL_RE.sub(b"", data)
            info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            out.writestr(info, data)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def process(path, args):
    print("%s" % path.name, flush=True)
    if path.suffix.lower() == ".xls":
        print("    skipped: legacy .xls holds no readable formulas for openpyxl.")
        return None

    graph = AstGraph(path, max_range_expand=args.max_range_expand,
                     read_values=False, verbose=not args.quiet)
    try:
        graph.build()
    except Exception as exc:
        print("    failed: %s: %s" % (type(exc).__name__, exc))
        return None

    placed, deepest, cyclic, unparsed = levels_by_sheet(graph)
    print("    %d formulas over %d levels (0-%d)" % (graph.parsed, deepest + 1, deepest))
    if cyclic:
        print("    %d cells sit in a circular reference and were placed past their "
              "acyclic precedents" % cyclic)
    if unparsed:
        print("    %d formulas could not be parsed and appear only in the last snapshot"
              % unparsed)

    out_dir = Path(args.out) / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    cutoffs = cutoff_list(deepest, args.stride, args.max_workbooks)
    width = max(2, len(str(deepest)))

    rows = []
    with zipfile.ZipFile(path) as src:
        names = set(src.namelist())
        part_levels = {}
        for sheet, part in sheet_parts(src):
            if part in names:
                part_levels[part] = placed.get(sheet, {})
            else:
                print("    warning: %r points at a missing part %s" % (sheet, part))
        if not part_levels:
            print("    failed: no worksheets found in the package")
            return None

        for cutoff in cutoffs:
            # a macro-enabled package has to keep its extension or Excel refuses it
            name = "L%s%s" % (str(cutoff).zfill(width), path.suffix.lower())
            tally = defaultdict(int)
            write_snapshot(src, out_dir / name, part_levels, cutoff, tally)
            rows.append({
                "file": name,
                "levels": "0-%d" % cutoff,
                "formula_cells_with_values": tally["frozen"],
                "formula_cells_emptied": tally["emptied"],
                "static_cells_kept": tally["static"],
                "bytes": (out_dir / name).stat().st_size,
            })
            if not args.quiet:
                print("    %-12s levels 0-%-3d %6d values, %6d blank"
                      % (name, cutoff, tally["frozen"], tally["emptied"]), flush=True)
            if tally["uncalculated"]:
                print("      %d cells had no cached value and came out blank"
                      % tally["uncalculated"])

    with open(out_dir / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = sum(row["bytes"] for row in rows)
    print("    -> %s (%d workbooks, %s)"
          % (out_dir, len(rows), human_size(total)))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write one workbook per cumulative dependency level (L0, L0+L1, ...)")
    parser.add_argument("path", help="workbook, or a directory to process in batch")
    parser.add_argument("--glob", default="*.xls[xm]",
                        help="pattern used when path is a directory (default: %(default)s)")
    parser.add_argument("-o", "--out", default="level_out", help="output directory")
    parser.add_argument("--stride", type=int, default=1,
                        help="emit every Nth cutoff instead of all of them "
                             "(default: %(default)s)")
    parser.add_argument("--max-workbooks", type=int, default=0,
                        help="cap the number of snapshots, spreading the cutoffs evenly "
                             "and always keeping the complete one (default: no cap)")
    parser.add_argument("--max-range-expand", type=int, default=100,
                        help="ranges resolving to more populated cells than this collapse "
                             "into a single node (default: %(default)s)")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if root.is_dir():
        targets = sorted(p for p in root.glob(args.glob) if not p.name.startswith("~$"))
    else:
        targets = [root]
    if not targets:
        sys.exit("nothing to do: no workbook matched %s" % args.path)

    print("%d workbook(s) -> %s\n" % (len(targets), Path(args.out).resolve()))
    started = time.time()
    done = 0
    for target in targets:
        if process(target, args):
            done += 1
        print()
    if len(targets) > 1:
        print("%d/%d workbooks in %.1fs" % (done, len(targets), time.time() - started))


if __name__ == "__main__":
    main()
