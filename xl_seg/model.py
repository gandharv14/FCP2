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


@dataclass(slots=True)
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
    array_formula: bool = False
    array_anchor: str = ""
    array_ref: str = ""
    array_row: int | None = None
    array_col: int | None = None
    ast_schema_version: str = ""
    parse_status: str = ""
    parse_error: str = ""
    value_type: str = ""
    range_truncated: bool = False
    # A1 span of the multi-cell array formula this cell belongs to, empty for
    # ordinary cells. Lets the evaluator hand each member its own positional
    # element instead of the anchor's first value.
    array_span: str = ""

    @property
    def is_cell(self) -> bool:
        return self.kind in CELL_KINDS

    @property
    def number(self):
        return as_number(self.value)


@dataclass(slots=True)
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
    capabilities: dict[str, bool] = field(default_factory=dict)
    integrity_errors: list[dict] = field(default_factory=list)
    ast_schema_version: str = ""
    _store: object = field(default=None, repr=False, compare=False)

    def close(self):
        if self._store is not None:
            self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def iter_nodes(self, *kinds):
        if self._store is not None:
            return self._store.iter_nodes(kinds)
        return (node for node in self.nodes.values() if not kinds or node.kind in kinds)

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
        return list(self.iter_nodes(*CELL_KINDS))

    def root_of(self, cell_id: str) -> str | None:
        """The single AST node (or referenced cell) that produces a formula's value."""
        incoming = self.in_edges.get(cell_id, ())
        return incoming[0].source if len(incoming) == 1 else None


def _int(text: str):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _text(row: dict, key: str, default: str = "") -> str:
    value = row.get(key)
    return default if value is None else value


def _bool(row: dict, key: str, default: bool = False) -> bool:
    value = row.get(key)
    if value in (None, ""):
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")



def _parse_node(row):
    return Node(
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
        in_cycle=_bool(row, "in_cycle"),
        array_formula=_bool(row, "array_formula"),
        array_anchor=_text(row, "array_anchor"),
        array_ref=_text(row, "array_ref"),
        array_row=_int(row.get("array_row")),
        array_col=_int(row.get("array_col")),
        ast_schema_version=_text(row, "ast_schema_version"),
        parse_status=_text(row, "parse_status"),
        parse_error=_text(row, "parse_error"),
        value_type=_text(row, "value_type"),
        range_truncated=_bool(row, "range_truncated"),
        array_span=_text(row, "array_span") or _text(row, "array_ref"),
    )


def _parse_edge(row):
    return Edge(
        source=row["source"],
        target=row["target"],
        role=row["role"],
        arg_index=_int(row["arg_index"]) or 0,
        op=row["op"],
        cell=row["cell"],
        ref=row["ref"],
        via_range=row["via_range"],
        cross_sheet=_bool(row, "cross_sheet"),
    )


def _capabilities(node_fields, edge_fields, schema_versions):
    return {
        "required_node_fields": REQUIRED_AST_NODE_FIELDS <= node_fields,
        "schema_version_v2": schema_versions == {"xl-ast/v2"},
        "edge_endpoints": {"source", "target"} <= edge_fields,
        "formula_parse_status": "parse_status" in node_fields,
        "array_member_coordinates": {
            "array_anchor", "array_ref", "array_row", "array_col"
        } <= node_fields,
        "raw_value_type": "value_type" in node_fields,
    }


DISK_THRESHOLD_BYTES = 128 * 1024 * 1024


def load(ast_dir: Path, wb: str, *, storage="auto", storage_dir=None) -> Graph:
    """Load exact CSV semantics; large graphs use a disposable disk index.

    Disk graph records are read-only. Explicit ``memory`` keeps the historical
    mutable API for small exploratory graphs. Storage mode changes no target,
    dependency, integrity check, or numeric interpretation.
    """
    ast_dir = Path(ast_dir)
    if storage not in {"auto", "memory", "disk"}:
        raise ValueError("unknown graph storage mode")
    if storage == "disk" or (storage == "auto" and sum(
        (ast_dir / name).stat().st_size for name in ("nodes.csv", "edges.csv")
    ) >= DISK_THRESHOLD_BYTES):
        from .disk_model import load as disk_load
        return disk_load(ast_dir, wb, storage_dir)
    return _load_memory(ast_dir, wb)


def _load_memory(ast_dir: Path, wb: str) -> Graph:
    nodes: dict[str, Node] = {}
    integrity_errors: list[dict] = []
    node_fields: set[str] = set()
    with open(ast_dir / "nodes.csv", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        node_fields = set(reader.fieldnames or ())
        for node_index, row in enumerate(reader):
            if row["id"] in nodes:
                # A graph identity has exactly one definition. Even identical
                # repeats are malformed evidence; never silently let a later
                # CSV row replace a formula, primitive, or ownership record.
                integrity_errors.append({
                    "code": "duplicate_node_id",
                    "node": row["id"],
                    "node_index": node_index,
                })
                continue
            nodes[row["id"]] = _parse_node(row)

    edges: list[Edge] = []
    edge_fields: set[str] = set()
    with open(ast_dir / "edges.csv", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        edge_fields = set(reader.fieldnames or ())
        for row in reader:
            edges.append(_parse_edge(row))

    schema_versions = {
        node.ast_schema_version for node in nodes.values()
        if node.ast_schema_version
    }
    capabilities = _capabilities(node_fields, edge_fields, schema_versions)
    for edge_index, edge in enumerate(edges):
        missing = [
            endpoint for endpoint in (edge.source, edge.target)
            if endpoint not in nodes
        ]
        if missing:
            integrity_errors.append({
                "code": "missing_edge_endpoint",
                "edge_index": edge_index,
                "missing": missing[:2],
            })
    for node in nodes.values():
        # Range nodes are shared dependency sources when expansion would be
        # excessive, so they intentionally have no single formula owner. A
        # top-level array/CSE range may still carry an owner and must validate.
        requires_owner = node.kind in {"op", "const"}
        validates_owner = requires_owner or bool(node.owner)
        if validates_owner and node.kind in AST_KINDS and (
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
                "detail": node.parse_error[:240],
            })

    graph = Graph(
        wb=wb,
        nodes=nodes,
        edges=edges,
        capabilities=capabilities,
        integrity_errors=integrity_errors,
        ast_schema_version=(
            next(iter(schema_versions)) if len(schema_versions) == 1 else ""
        ),
    )
    for edge in edges:
        if edge.source not in nodes or edge.target not in nodes:
            continue
        graph.in_edges.setdefault(edge.target, []).append(edge)
        graph.out_edges.setdefault(edge.source, []).append(edge)
    for node in nodes.values():
        if node.kind != "formula" or node.parse_status != "ok":
            continue
        roots = graph.in_edges.get(node.id, ())
        if len(roots) != 1:
            graph.integrity_errors.append({
                "code": "invalid_formula_root_count",
                "node": node.id,
                "count": len(roots),
            })
    return graph
