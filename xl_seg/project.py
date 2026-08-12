"""Stage 1-2: collapse the AST layer onto cells, then type every cell.

Collapsing rewrites each ``op``/``const``/``range`` node onto its owning cell, so
``K8 -> L8#1:+ -> L8`` becomes ``K8 -> L8``. Typing marks the cells that carry a
real number apart from the units, titles and date axes that are structurally
indistinguishable but semantically inert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Graph, Node, as_number, range_members

# Cells whose cached value is one of these are unit stamps, not model values.
UNIT_TOKENS = {
    "aed", "usd", "eur", "gbp", "x", "%", "na", "n/a", "-", "bc",
    "000$", "$mm", "$bn", "$000s", "mm", "bn", "k", "m",
}

YEAR_STEP_RE = re.compile(r"^=\s*\$?[A-Za-z]{1,3}\$?\d+\s*\+\s*1\s*$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")
PURE_REF_RE = re.compile(r"^=\s*(?:'[^']*'|[A-Za-z0-9_ ]+)?!?\$?[A-Za-z]{1,3}\$?\d{1,7}\s*$")
AXIS_WORDS = ("year", "date", "period", "month", "quarter")

# ``value``-bearing cells feed the frontier; everything else is scaffolding.
NUMERIC = "numeric"
TEXT = "text"
UNIT = "unit"
AXIS = "axis"
BLANK = "blank"


@dataclass
class CellInfo:
    node: Node
    vtype: str
    is_mirror: bool
    is_literal: bool
    empty_ref: bool = False
    literals: tuple = ()

    @property
    def value_bearing(self) -> bool:
        return self.vtype == NUMERIC


@dataclass
class CellGraph:
    graph: Graph
    info: dict[str, CellInfo]
    adj: dict[str, set] = field(default_factory=dict)
    radj: dict[str, set] = field(default_factory=dict)

    def successors(self, cid: str) -> set:
        return self.adj.get(cid, set())

    def predecessors(self, cid: str) -> set:
        return self.radj.get(cid, set())


def _looks_like_axis(node: Node, number) -> bool:
    label = (node.label or "").lower()
    if any(word in label.split(" / ")[0] for word in AXIS_WORDS):
        return True
    if number is not None and float(number).is_integer():
        if 1900 <= number <= 2200 and YEAR_STEP_RE.match(node.formula or ""):
            return True
    return False


def _classify(node: Node) -> str:
    if node.kind == "label":
        return TEXT
    raw = (node.value or "").strip()
    if raw == "":
        return BLANK
    number = as_number(raw)
    if number is None:
        # Dates arrive as ISO strings; they are the period axis, not model values.
        if ISO_DATE_RE.match(raw):
            return AXIS
        return UNIT if raw.lower() in UNIT_TOKENS else TEXT
    if _looks_like_axis(node, number):
        return AXIS
    return NUMERIC


def build(graph: Graph) -> CellGraph:
    consts = literal_index(graph)
    info: dict[str, CellInfo] = {}
    for node in graph.cells():
        incoming = graph.in_edges.get(node.id, ())
        is_mirror = (
            node.kind == "formula"
            and len(incoming) == 1
            and incoming[0].role == "identity"
        )
        # `=5` is a hardcode wearing a formula's clothes, and has to be supplied.
        is_literal = (
            node.kind == "formula"
            and bool(incoming)
            and graph.nodes[incoming[0].source].kind == "const"
        )
        # `=O604` where O604 is empty gets no edge, because there is no node to
        # draw it from. Excel evaluates it to 0, so it is derived, not an input.
        empty_ref = (
            node.kind == "formula"
            and not incoming
            and bool(PURE_REF_RE.match(node.formula or ""))
        )
        literals = tuple(c.value for c in consts.get(node.id, ()))
        info[node.id] = CellInfo(
            node=node,
            vtype=_classify(node),
            is_mirror=is_mirror,
            is_literal=is_literal,
            empty_ref=empty_ref,
            literals=literals,
        )

    # A ``range`` node stands in for a whole span and carries no edges from its
    # members, so those dependencies have to be reinstated by hand.
    expanded: dict[str, list] = {}
    for node in graph.nodes.values():
        if node.kind == "range":
            expanded[node.id] = [
                cid for cid in range_members(node.sheet, node.coordinate)
                if cid in info
            ]

    adj: dict[str, set] = {}
    radj: dict[str, set] = {}
    for edge in graph.edges:
        sources = expanded.get(edge.source)
        if sources is None:
            resolved = graph.owner_of(edge.source)
            sources = [resolved] if resolved else []
        dst = graph.owner_of(edge.target)
        if dst is None:
            continue
        for src in sources:
            if src == dst:
                continue
            adj.setdefault(src, set()).add(dst)
            radj.setdefault(dst, set()).add(src)

    return CellGraph(graph=graph, info=info, adj=adj, radj=radj)


def literal_index(graph: Graph) -> dict[str, list]:
    """Map each formula cell to the literal constants baked into its formula."""
    out: dict[str, list] = {}
    for node in graph.nodes.values():
        if node.kind == "const" and node.owner:
            out.setdefault(node.owner, []).append(node)
    return out
