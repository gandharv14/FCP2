"""Load an ``ast_out/<wb>/`` graph into memory and index it.

The on-disk graph has two layers: spreadsheet cells (``formula``/``input``/``label``)
and the parsed formula trees hanging off them (``op``/``const``/``range`` nodes,
each pointing at its ``owner`` cell). Everything downstream needs both layers, so
this module keeps them together and exposes the indexes the later stages want.
"""

from __future__ import annotations

import csv
import gc
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

csv.field_size_limit(1 << 30)

# One graph holds tens of millions of small strings (13.2M nodes and 22.7M
# edges on the largest fleet workbook), and most of them repeat: every edge
# names two node ids that already exist as ``Node.id``, every AST node names
# its owner cell, and ``kind``/``op``/``role`` come from a handful of tokens.
# Interning makes those repeats share one object each. It changes identity
# only, never equality, so nothing downstream (JSON, TOML, hashes) can differ.
_intern = sys.intern

CELL_KINDS = ("formula", "input", "label")
AST_KINDS = ("op", "const", "range")
REQUIRED_AST_NODE_FIELDS = frozenset({
    "ast_schema_version",
    "parse_status",
    "parse_error",
    "value_type",
    "range_truncated",
    "array_anchor",
    "array_ref",
    "array_row",
    "array_col",
})

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
    # Interned so a cell id minted from a range span is the same object as the
    # node id loaded from nodes.csv; range expansion otherwise mints a fresh
    # copy of every member id for every range node that covers it.
    return _intern(f"{sheet}!{col_letter(col)}{row}")


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


@dataclass(slots=True)
class Node:
    # Only fields something downstream reads are stored. ``in_cycle``,
    # ``array_formula``, ``array_anchor``, ``value_type``, ``parse_error`` and
    # ``ast_schema_version`` are still parsed from nodes.csv (the ``load``
    # capability and integrity checks use them) but not kept per node: with
    # ``slots=True`` every field is 8 bytes on each of 13M nodes.
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
    array_ref: str = ""
    array_row: int | None = None
    array_col: int | None = None
    parse_status: str = ""
    range_truncated: bool = False

    @property
    def is_cell(self) -> bool:
        return self.kind in CELL_KINDS

    @property
    def number(self):
        return as_number(self.value)


@dataclass(slots=True)
class Edge:
    # ``op``, ``cell``, ``ref`` and ``cross_sheet`` exist in edges.csv but have
    # no reader after load; they are not stored (22.7M edges on the largest
    # fleet workbook).
    source: str
    target: str
    role: str
    arg_index: int
    via_range: str


@dataclass
class Graph:
    wb: str
    nodes: dict[str, Node]
    edges: list[Edge]
    in_edges: dict[str, list[Edge]] = field(default_factory=dict)
    # Kept for callers that build a Graph by hand; ``load`` leaves it empty
    # because nothing in the pipeline reads it and filling it costs one list
    # slot per edge (about 2 GB on the largest fleet workbook).
    out_edges: dict[str, list[Edge]] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    integrity_errors: list[dict] = field(default_factory=list)
    ast_schema_version: str = ""

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
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _text(row: dict, key: str, default: str = "") -> str:
    value = row.get(key)
    return default if value is None else value


_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def _bool(row: dict, key: str, default: bool = False) -> bool:
    value = row.get(key)
    if value in (None, ""):
        return default
    return str(value).strip().lower() in _TRUE_TOKENS


def _bool_text(text: str, default: bool = False) -> bool:
    """``_bool`` for a raw CSV field: same truth table, no dict lookup."""
    if not text:
        return default
    if text in _TRUE_TOKENS:
        return True
    return text.strip().lower() in _TRUE_TOKENS


class _Columns:
    """Positional access to a csv.reader row with DictReader semantics.

    Missing columns and short rows read as "" (DictReader's ``restval`` is
    None, and every caller here mapped None to "").
    """

    __slots__ = ("index", "width")

    def __init__(self, header: list[str]):
        self.index = {name: position for position, name in enumerate(header)}
        self.width = len(header)

    def at(self, name: str):
        return self.index.get(name)


def load(ast_dir: Path, wb: str) -> Graph:
    # The loader creates tens of millions of small objects and none of them are
    # cyclic garbage; running the cyclic collector while the heap grows costs
    # roughly a third of the load time for nothing. Disable it for the load
    # and freeze the survivors so later collections skip them.
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        return _load(ast_dir, wb)
    finally:
        gc.freeze()
        if gc_was_enabled:
            gc.enable()


def _load(ast_dir: Path, wb: str) -> Graph:
    intern = _intern
    nodes: dict[str, Node] = {}
    node_fields: set[str] = set()
    schema_versions: set[str] = set()
    # ``parse_error`` is only needed for the formula_parse_error integrity
    # record, which is rare, so it is kept off the Node and looked up here.
    parse_errors: dict[str, str] = {}
    with open(ast_dir / "nodes.csv", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        node_fields = set(header)
        cols = _Columns(header)
        c_id = cols.at("id"); c_kind = cols.at("kind"); c_sheet = cols.at("sheet")
        c_coord = cols.at("coordinate"); c_row = cols.at("row"); c_col = cols.at("col")
        c_owner = cols.at("owner"); c_op = cols.at("op"); c_op_kind = cols.at("op_kind")
        c_arity = cols.at("arity"); c_expr = cols.at("expr"); c_label = cols.at("label")
        c_formula = cols.at("formula"); c_value = cols.at("value")
        c_array_ref = cols.at("array_ref"); c_array_row = cols.at("array_row")
        c_array_col = cols.at("array_col"); c_schema = cols.at("ast_schema_version")
        c_parse_status = cols.at("parse_status"); c_parse_error = cols.at("parse_error")
        c_truncated = cols.at("range_truncated")
        required = (c_id, c_kind, c_sheet, c_coord, c_row, c_col, c_owner, c_op,
                    c_op_kind, c_arity, c_expr, c_label, c_formula, c_value)
        if any(position is None for position in required):
            missing = [name for name, position in zip(
                ("id", "kind", "sheet", "coordinate", "row", "col", "owner", "op",
                 "op_kind", "arity", "expr", "label", "formula", "value"), required)
                if position is None]
            raise KeyError(missing[0])
        width = cols.width
        for row in reader:
            if len(row) < width:
                row = row + [""] * (width - len(row))
            node_id = intern(row[c_id])
            kind = intern(row[c_kind])
            parse_status = intern(row[c_parse_status]) if c_parse_status is not None else ""
            if c_schema is not None:
                schema = row[c_schema]
                if schema:
                    schema_versions.add(schema)
            if kind == "formula" and parse_status == "error" and c_parse_error is not None:
                parse_errors[node_id] = row[c_parse_error]
            # ``expr``/``label``/``formula`` are mostly unique per node, so
            # they stay as read.
            nodes[node_id] = Node(
                id=node_id,
                kind=kind,
                sheet=intern(row[c_sheet]),
                coordinate=intern(row[c_coord]),
                row=_int(row[c_row]),
                col=_int(row[c_col]),
                owner=intern(row[c_owner]),
                op=intern(row[c_op]),
                op_kind=intern(row[c_op_kind]),
                arity=intern(row[c_arity]),
                expr=row[c_expr],
                label=row[c_label],
                formula=row[c_formula],
                value=intern(row[c_value]),
                array_ref=intern(row[c_array_ref]) if c_array_ref is not None else "",
                array_row=_int(row[c_array_row]) if c_array_row is not None else None,
                array_col=_int(row[c_array_col]) if c_array_col is not None else None,
                parse_status=parse_status,
                range_truncated=(
                    _bool_text(row[c_truncated]) if c_truncated is not None else False
                ),
            )

    edges: list[Edge] = []
    edge_fields: set[str] = set()
    integrity_errors: list[dict] = []
    in_edges: dict[str, list[Edge]] = {}
    with open(ast_dir / "edges.csv", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        edge_fields = set(header)
        cols = _Columns(header)
        c_source = cols.at("source"); c_target = cols.at("target"); c_role = cols.at("role")
        c_arg = cols.at("arg_index"); c_via = cols.at("via_range")
        for name, position in (("source", c_source), ("target", c_target),
                               ("role", c_role), ("arg_index", c_arg),
                               ("via_range", c_via)):
            if position is None:
                raise KeyError(name)
        width = cols.width
        edge_index = 0
        for row in reader:
            if len(row) < width:
                row = row + [""] * (width - len(row))
            source = intern(row[c_source])
            target = intern(row[c_target])
            edge = Edge(
                source=source,
                target=target,
                role=intern(row[c_role]),
                arg_index=_int(row[c_arg]) or 0,
                via_range=intern(row[c_via]),
            )
            edges.append(edge)
            # Endpoint check and in_edges are built here, in edge order, which
            # is the order the separate passes used to produce.
            source_ok = source in nodes
            target_ok = target in nodes
            if source_ok and target_ok:
                bucket = in_edges.get(target)
                if bucket is None:
                    in_edges[target] = [edge]
                else:
                    bucket.append(edge)
            else:
                missing = [
                    endpoint for endpoint, ok in ((source, source_ok), (target, target_ok))
                    if not ok
                ]
                integrity_errors.append({
                    "code": "missing_edge_endpoint",
                    "edge_index": edge_index,
                    "missing": missing[:2],
                })
            edge_index += 1

    capabilities = {
        "required_node_fields": REQUIRED_AST_NODE_FIELDS <= node_fields,
        "schema_version_v2": schema_versions == {"xl-ast/v2"},
        "edge_endpoints": {"source", "target"} <= edge_fields,
        "formula_parse_status": "parse_status" in node_fields,
        "array_member_coordinates": {
            "array_anchor", "array_ref", "array_row", "array_col"
        } <= node_fields,
        "raw_value_type": "value_type" in node_fields,
    }
    # The owner check needs every node loaded, so it stays a separate pass; it
    # and the root-count pass keep their original order because the first
    # records are emitted into segments.json.
    for node in nodes.values():
        if node.kind in AST_KINDS and (
            not node.owner
            or node.owner not in nodes
            or nodes[node.owner].kind != "formula"
        ):
            integrity_errors.append({
                "code": "invalid_ast_owner",
                "node": node.id,
                "owner": node.owner,
            })
        if node.kind == "formula" and node.parse_status == "error":
            integrity_errors.append({
                "code": "formula_parse_error",
                "node": node.id,
                "detail": parse_errors.get(node.id, "")[:240],
            })

    graph = Graph(
        wb=wb,
        nodes=nodes,
        edges=edges,
        in_edges=in_edges,
        capabilities=capabilities,
        integrity_errors=integrity_errors,
        ast_schema_version=(
            next(iter(schema_versions)) if len(schema_versions) == 1 else ""
        ),
    )
    for node in nodes.values():
        if node.kind != "formula" or node.parse_status != "ok":
            continue
        roots = in_edges.get(node.id, ())
        if len(roots) != 1:
            graph.integrity_errors.append({
                "code": "invalid_formula_root_count",
                "node": node.id,
                "count": len(roots),
            })
    return graph
