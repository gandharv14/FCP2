"""Stage 4-5: bypass the presentation layer, contract cycles, split islands.

Roughly a quarter of the cells in these workbooks are pure pass-through mirrors
(``=Valuation!B$30``). They invert the topology exactly where it matters: the real
output stops being a sink and the dashboard copy that computes nothing becomes one.

So mirrors are lifted out of the working graph and their predecessors wired
straight to their successors. They are not thrown away though -- a value the
modeller chose to surface on a dashboard is, by their own judgement, an output, so
each mirror credits ``mirror_fanin`` to the real cell it reflects.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .bands import BandGraph

PRESENTATION_HINTS = ("dashboard", "output", "summary", "return", "case comparison")


@dataclass
class Condensed:
    """Band graph with mirrors bypassed and strongly connected blocks contracted."""

    adj: dict[str, set]
    radj: dict[str, set]
    mirrors: set
    mirror_fanin: dict[str, int]
    presented_on: dict[str, set]
    comp_of: dict[str, str]
    comp_members: dict[str, list]
    comp_adj: dict[str, set] = field(default_factory=dict)
    comp_radj: dict[str, set] = field(default_factory=dict)
    depth: dict[str, int] = field(default_factory=dict)
    island_of: dict[str, int] = field(default_factory=dict)
    island_sizes: dict[int, int] = field(default_factory=dict)


def _walk(start, step, mirrors, real_nodes):
    """Follow mirror hops from ``start`` until reaching non-mirror bands."""
    seen, out, queue = set(), set(), deque(step.get(start, ()))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        if node in mirrors:
            queue.extend(step.get(node, ()))
        elif node in real_nodes:
            out.add(node)
    return out


def bypass_mirrors(bg: BandGraph):
    mirrors = {bid for bid, b in bg.bands.items() if b.is_mirror}
    real = {bid for bid in bg.bands if bid not in mirrors}

    adj: dict[str, set] = {}
    radj: dict[str, set] = {}
    for bid in real:
        for succ in _walk(bid, bg.adj, mirrors, real):
            adj.setdefault(bid, set()).add(succ)
            radj.setdefault(succ, set()).add(bid)

    fanin: dict[str, int] = defaultdict(int)
    presented: dict[str, set] = defaultdict(set)
    for mid in mirrors:
        sheet = bg.bands[mid].sheet
        for origin in _walk(mid, bg.radj, mirrors, real):
            fanin[origin] += 1
            if any(hint in sheet.lower() for hint in PRESENTATION_HINTS):
                presented[origin].add(sheet)
    return adj, radj, mirrors, dict(fanin), {k: v for k, v in presented.items()}


def strongly_connected(nodes, adj) -> list[list]:
    """Iterative Tarjan; the 0450 cash-sweep loop is 186 bands deep."""
    index, low, on_stack, stack, order = {}, {}, set(), [], []
    result: list[list] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(adj.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(adj.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                result.append(group)
    order.clear()
    return result


def build(bg: BandGraph) -> Condensed:
    adj, radj, mirrors, fanin, presented = bypass_mirrors(bg)
    real = [bid for bid in bg.bands if bid not in mirrors]

    comp_of: dict[str, str] = {}
    comp_members: dict[str, list] = {}
    for group in strongly_connected(real, adj):
        cid = group[0] if len(group) == 1 else f"<scc:{min(group)}+{len(group) - 1}>"
        comp_members[cid] = group
        for member in group:
            comp_of[member] = cid

    comp_adj: dict[str, set] = {}
    comp_radj: dict[str, set] = {}
    for src, targets in adj.items():
        csrc = comp_of.get(src)
        if csrc is None:
            continue
        for dst in targets:
            cdst = comp_of.get(dst)
            if cdst is None or cdst == csrc:
                continue
            comp_adj.setdefault(csrc, set()).add(cdst)
            comp_radj.setdefault(cdst, set()).add(csrc)

    depth = _longest_path(comp_members, comp_adj, comp_radj)
    island_of, island_sizes = _islands(comp_members, comp_adj, comp_radj)

    return Condensed(
        adj=adj,
        radj=radj,
        mirrors=mirrors,
        mirror_fanin=fanin,
        presented_on=presented,
        comp_of=comp_of,
        comp_members=comp_members,
        comp_adj=comp_adj,
        comp_radj=comp_radj,
        depth=depth,
        island_of=island_of,
        island_sizes=island_sizes,
    )


def topo_order(comp_members, comp_adj, comp_radj) -> list:
    indeg = {c: len(comp_radj.get(c, ())) for c in comp_members}
    queue = deque(c for c, d in indeg.items() if d == 0)
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for succ in comp_adj.get(node, ()):
            indeg[succ] -= 1
            if indeg[succ] == 0:
                queue.append(succ)
    return order


def _longest_path(comp_members, comp_adj, comp_radj) -> dict[str, int]:
    depth = {c: 0 for c in comp_members}
    for node in topo_order(comp_members, comp_adj, comp_radj):
        for succ in comp_adj.get(node, ()):
            depth[succ] = max(depth[succ], depth[node] + 1)
    return depth


def _islands(comp_members, comp_adj, comp_radj):
    undirected: dict[str, set] = defaultdict(set)
    for src, targets in comp_adj.items():
        for dst in targets:
            undirected[src].add(dst)
            undirected[dst].add(src)

    island_of: dict[str, int] = {}
    sizes: dict[int, int] = {}
    groups = []
    seen = set()
    for comp in comp_members:
        if comp in seen:
            continue
        stack, group = [comp], []
        seen.add(comp)
        while stack:
            node = stack.pop()
            group.append(node)
            for nb in undirected.get(node, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        groups.append(group)

    groups.sort(key=lambda g: sum(len(comp_members[c]) for c in g), reverse=True)
    for idx, group in enumerate(groups):
        sizes[idx] = sum(len(comp_members[c]) for c in group)
        for comp in group:
            island_of[comp] = idx
    return island_of, sizes
