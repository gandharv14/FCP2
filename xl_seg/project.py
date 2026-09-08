"""Stage 1-2: collapse the AST layer onto cells, then type every cell.

Collapsing rewrites each ``op``/``const``/``range`` node onto its owning cell, so
``K8 -> L8#1:+ -> L8`` becomes ``K8 -> L8``. Typing marks the cells that carry a
real number apart from the units, titles and date axes that are structurally
indistinguishable but semantically inert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass(slots=True)
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


class _LazyRadj:
    """Cell-level predecessor sets, expanded from range hyper-edges on demand.

    ``build`` records, per consuming cell, the *references* its formula reads:
    plain cell ids and ``range`` node ids. A range reference stands for every
    populated member cell, so materializing it (the old ``radj``) cost one set
    entry per member per consumer; on the largest fleet workbook that was ~47 GB
    and the bulk of the stage time. Callers only ever ``.get`` one target at a
    time, so the expansion happens here, per lookup, and is never stored for
    the whole graph at once. The returned set has exactly the contents the
    eager version had: members present in ``info``, minus the target itself.
    """

    __slots__ = ("_sources", "_members")

    def __init__(self, sources: dict, members: dict):
        self._sources = sources      # dst cell -> list of cell ids / range ids
        self._members = members      # range id -> list of member cell ids

    def _expand(self, target: str) -> set:
        out: set = set()
        members = self._members
        for ref in self._sources.get(target, ()):
            span = members.get(ref)
            if span is None:
                if ref != target:
                    out.add(ref)
            else:
                out.update(span)
        out.discard(target)
        return out

    def get(self, target: str, default=None):
        if target not in self._sources:
            return default
        return self._expand(target)

    def __getitem__(self, target: str) -> set:
        if target not in self._sources:
            raise KeyError(target)
        return self._expand(target)

    def __contains__(self, target) -> bool:
        return target in self._sources

    def __iter__(self):
        return iter(self._sources)

    def __len__(self) -> int:
        return len(self._sources)

    def keys(self):
        return self._sources.keys()

    def items(self):
        for target in self._sources:
            yield target, self._expand(target)

    def values(self):
        for target in self._sources:
            yield self._expand(target)


class CellGraph:
    """Cell layer of the workbook: classification plus dependency lookups.

    ``radj`` is a ``_LazyRadj`` when built by ``build``; tests hand in plain
    dicts. ``adj`` (forward edges) has no reader on the verified path after
    ``bands.build``, so it is materialized on first access, for the callers
    that still want it (``--no-verify`` lineage). A plain class rather than a
    dataclass so that ``cg.info`` stays a direct slot read in the evaluator's
    hot loops.
    """

    __slots__ = ("graph", "info", "_adj", "radj", "range_members", "sources_of")

    def __init__(
        self,
        graph: Graph,
        info: dict,
        adj: dict | None = None,
        radj=None,
        range_members: dict | None = None,
        sources_of: dict | None = None,
    ):
        self.graph = graph
        self.info = info
        self.range_members = {} if range_members is None else range_members
        self.sources_of = {} if sources_of is None else sources_of
        self.radj = {} if radj is None else radj
        # ``None`` means "derive from sources_of on first access".
        self._adj = adj

    @property
    def adj(self) -> dict:
        if self._adj is None:
            self._adj = _forward_adjacency(self.sources_of, self.range_members)
        return self._adj

    @adj.setter
    def adj(self, value: dict) -> None:
        self._adj = value

    def successors(self, cid: str) -> set:
        return self.adj.get(cid, set())

    def predecessors(self, cid: str) -> set:
        return self.radj.get(cid, set())

    def range_references(self, target: str) -> list:
        """Range node ids a consuming cell reads (band builder fast path)."""
        members = self.range_members
        return [ref for ref in self.sources_of.get(target, ()) if ref in members]

    def cell_references(self, target: str) -> list:
        """Plain cell ids a consuming cell reads, excluding itself."""
        members = self.range_members
        return [
            ref for ref in self.sources_of.get(target, ())
            if ref not in members and ref != target
        ]


def _forward_adjacency(sources_of: dict, range_members: dict) -> dict:
    """Materialize cell-level ``adj`` (the eager structure ``build`` used to make)."""
    adj: dict[str, set] = {}
    for target, refs in sources_of.items():
        for ref in refs:
            span = range_members.get(ref)
            if span is None:
                if ref != target:
                    adj.setdefault(ref, set()).add(target)
            else:
                for member in span:
                    if member != target:
                        adj.setdefault(member, set()).add(target)
    return adj


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
    # members, so those dependencies have to be reinstated by hand. The members
    # are listed once per range node; consumers keep the range id and expand it
    # when a predecessor set is actually asked for (see ``_LazyRadj``).
    expanded: dict[str, list] = {}
    for node in graph.nodes.values():
        if node.kind == "range":
            expanded[node.id] = [
                cid for cid in range_members(node.sheet, node.coordinate)
                if cid in info
            ]

    # Per consuming cell: the distinct references its formula reads, in edge
    # order. A reference is either a cell id (the owner of a non-range source
    # node, when that is a different cell) or a range node id.
    sources_of: dict[str, list] = {}
    seen: dict[str, set] = {}
    nodes = graph.nodes
    for edge in graph.edges:
        dst = graph.owner_of(edge.target)
        if dst is None:
            continue
        source = edge.source
        if source in expanded:
            ref = source
        else:
            ref = graph.owner_of(source)
            if ref is None or ref == dst:
                continue
        bucket = seen.get(dst)
        if bucket is None:
            seen[dst] = {ref}
            sources_of[dst] = [ref]
        elif ref not in bucket:
            bucket.add(ref)
            sources_of[dst].append(ref)
    # The eager ``radj`` only had a key for a consumer once a (source != dst)
    # pair existed. Drop consumers whose expansion would be empty (only empty
    # ranges, or ranges whose sole populated member is the consumer) so ``in``
    # and ``len`` agree with that structure.
    for dst in list(sources_of):
        refs = sources_of[dst]
        if not any(
            (ref not in expanded) or any(m != dst for m in expanded[ref])
            for ref in refs
        ):
            del sources_of[dst]

    return CellGraph(
        graph=graph,
        info=info,
        adj=None,
        radj=_LazyRadj(sources_of, expanded),
        range_members=expanded,
        sources_of=sources_of,
    )


def literal_index(graph: Graph) -> dict[str, list]:
    """Map each formula cell to the literal constants baked into its formula."""
    out: dict[str, list] = {}
    for node in graph.nodes.values():
        if node.kind == "const" and node.owner:
            out.setdefault(node.owner, []).append(node)
    return out
