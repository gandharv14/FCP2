"""Load an ``ast_out/<wb>/`` graph into memory and index it.

The on-disk graph has two layers: spreadsheet cells (``formula``/``input``/``label``)
and the parsed formula trees hanging off them (``op``/``const``/``range`` nodes,
each pointing at its ``owner`` cell). Everything downstream needs both layers, so
this module keeps them together and exposes the indexes the later stages want.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

csv.field_size_limit(1 << 30)

CELL_KINDS = ("formula", "input", "label")
AST_KINDS = ("op", "const", "range")

A1_RE = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")


def col_letter(col: int) -> str:
    out = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


def col_number(letters: str) -> int:
    num = 0
    for ch in letters.upper():
        num = num * 26 + ord(ch) - 64
    return num


def a1(sheet: str, row: int, col: int) -> str:
    return f"{sheet}!{col_letter(col)}{row}"


def split_ref(node_id: str):
    """``Sheet!B12`` -> ``('Sheet', 12, 2)``; ``None`` when not a plain cell ref."""
    sheet, sep, coord = node_id.rpartition("!")
    if not sep:
        return None
    m = A1_RE.match(coord)
    if not m:
        return None
    return sheet, int(m.group(2)), col_number(m.group(1))


def range_members(sheet: str, span: str) -> list[str]:
    """Cell ids covered by ``A1:B5``; empty when the span cannot be parsed."""
    start, sep, end = span.partition(":")
    if not sep:
        return []
    m0, m1 = A1_RE.match(start), A1_RE.match(end)
    if not (m0 and m1):
        return []
    c0, r0 = col_number(m0.group(1)), int(m0.group(2))
    c1, r1 = col_number(m1.group(1)), int(m1.group(2))
    return [
        a1(sheet, row, col)
        for row in range(min(r0, r1), max(r0, r1) + 1)
        for col in range(min(c0, c1), max(c0, c1) + 1)
    ]


def as_number(text: str):
    """Parse a cached cell value as a float, or ``None`` if it is not numeric."""
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class Node:
    id: str
    kind: str
    sheet: str
    coordinate: str
    row: int | None
    col: int | None
    owner: str
    op: str
    op_kind: str
    arity: str
    expr: str
    label: str
    formula: str
    value: str
    in_cycle: bool

    @property
    def is_cell(self) -> bool:
        return self.kind in CELL_KINDS

    @property
    def number(self):
        return as_number(self.value)


@dataclass
class Edge:
    source: str
    target: str
    role: str
    arg_index: int
    op: str
    cell: str
    ref: str
    via_range: str
    cross_sheet: bool


@dataclass
class Graph:
    wb: str
    nodes: dict[str, Node]
    edges: list[Edge]
    in_edges: dict[str, list[Edge]] = field(default_factory=dict)
    out_edges: dict[str, list[Edge]] = field(default_factory=dict)

    def owner_of(self, node_id: str) -> str | None:
        """Resolve any node to the spreadsheet cell that owns it."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        if node.kind in AST_KINDS:
            node = self.nodes.get(node.owner)
            if node is None:
                return None
        return node.id if node.is_cell else None

    def cells(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.is_cell]

    def root_of(self, cell_id: str) -> str | None:
        """The single AST node (or referenced cell) that produces a formula's value."""
        incoming = self.in_edges.get(cell_id, ())
        return incoming[0].source if len(incoming) == 1 else None


def _int(text: str):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def load(ast_dir: Path, wb: str) -> Graph:
    nodes: dict[str, Node] = {}
    with open(ast_dir / "nodes.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            nodes[row["id"]] = Node(
                id=row["id"],
                kind=row["kind"],
                sheet=row["sheet"],
                coordinate=row["coordinate"],
                row=_int(row["row"]),
                col=_int(row["col"]),
                owner=row["owner"],
                op=row["op"],
                op_kind=row["op_kind"],
                arity=row["arity"],
                expr=row["expr"],
                label=row["label"],
                formula=row["formula"],
                value=row["value"],
                in_cycle=row["in_cycle"] == "True",
            )

    edges: list[Edge] = []
    with open(ast_dir / "edges.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            edges.append(
                Edge(
                    source=row["source"],
                    target=row["target"],
                    role=row["role"],
                    arg_index=_int(row["arg_index"]) or 0,
                    op=row["op"],
                    cell=row["cell"],
                    ref=row["ref"],
                    via_range=row["via_range"],
                    cross_sheet=row["cross_sheet"] == "True",
                )
            )

    graph = Graph(wb=wb, nodes=nodes, edges=edges)
    for edge in edges:
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    return graph
