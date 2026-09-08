"""Derivation traces: how each output is built, all the way back to inputs.

Two resolutions are produced for every output. The band trace is the readable one
-- a few dozen line items in dependency order, which is how a modeller would walk
someone through the calculation. The cell trace is the exact one: every individual
cell, its formula, its recomputed value, and the cells it consumed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .condense import strongly_connected
from .frontier import primary_band
from .partition import INPUT, MIDDLE, OUTPUT


@dataclass
class Step:
    order: int
    node: str
    bucket: str
    sheet: str
    label: str
    formula: str
    depth: int
    inputs: list
    values: list = field(default_factory=list)


@dataclass
class Trace:
    output: str
    label: str
    sheet: str
    band_steps: list
    cell_traces: dict
    stats: dict


def _restricted_topo(cone: set, adj: dict, radj: dict) -> list:
    indeg = {n: sum(1 for p in radj.get(n, ()) if p in cone) for n in cone}
    queue = deque(sorted(n for n, d in indeg.items() if d == 0))
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for succ in sorted(adj.get(node, ())):
            if succ in cone:
                indeg[succ] -= 1
                if indeg[succ] == 0:
                    queue.append(succ)
    # Anything left sits in a cycle the caller already contracted; append it.
    # One set, built once: rebuilding ``set(order)`` inside the generator made
    # this O(N^2) on the cone size (hours on a 100k-cell cone).
    ordered = set(order)
    order.extend(sorted(n for n in cone if n not in ordered))
    return order


def band_trace(
    bg,
    cd,
    part,
    comp: str,
    *,
    comp_adj=None,
    comp_radj=None,
    proof_input_comps=None,
) -> list:
    """Every component the output depends on, in dependency order."""
    comp_adj = cd.comp_adj if comp_adj is None else comp_adj
    comp_radj = cd.comp_radj if comp_radj is None else comp_radj
    proof_input_comps = set() if proof_input_comps is None else proof_input_comps
    cone = {comp}
    stack = [comp]
    while stack:
        node = stack.pop()
        for pred in comp_radj.get(node, ()):
            if pred not in cone:
                cone.add(pred)
                stack.append(pred)

    steps = []
    for i, node in enumerate(_restricted_topo(cone, comp_adj, comp_radj)):
        band_id = primary_band(cd, bg, node)
        band = bg.bands[band_id]
        members = cd.comp_members[node]
        steps.append(
            Step(
                order=i,
                node=band_id,
                bucket=INPUT if node in proof_input_comps else part.bucket.get(node, MIDDLE),
                sheet=band.sheet,
                label=band.label,
                formula=band.pattern or (band.kind if band.kind != "formula" else ""),
                depth=cd.depth.get(node, 0),
                inputs=sorted(
                    primary_band(cd, bg, p) for p in comp_radj.get(node, ()) if p in cone
                ),
                values=[f"{len(members)} band(s)"] if len(members) > 1 else [],
            )
        )
    return steps


def cell_trace(
    cg,
    values,
    cell_id: str,
    input_cells: set,
    limit: int,
    *,
    adj=None,
    radj=None,
) -> dict:
    """Exact cell-by-cell derivation of one output value."""
    radj = cg.radj if radj is None else radj
    cone = {cell_id}
    stack = [cell_id]
    while stack:
        node = stack.pop()
        if node in input_cells and node != cell_id:
            continue
        for pred in radj.get(node, ()):
            if pred not in cone:
                cone.add(pred)
                stack.append(pred)

    # Forward edges inside the cone, derived from the same radj the cone was
    # traversed with; identical to filtering a full forward graph to the cone,
    # without ever building one. ``adj`` is accepted for callers that still
    # pass it and used only as a fallback when no radj is available.
    successors: dict = {}
    if radj is not None:
        for target in cone:
            for source in radj.get(target, ()):
                if source in cone:
                    successors.setdefault(source, set()).add(target)
    elif adj is not None:
        for node in cone:
            successors[node] = {s for s in adj.get(node, ()) if s in cone}
    # Canonicalize both Tarjan roots and successors.  The proof graphs store
    # adjacency in sets, whose iteration order changes with PYTHONHASHSEED.
    restricted_adj = {
        node: tuple(sorted(successors.get(node, ())))
        for node in sorted(cone)
    }
    groups = strongly_connected(sorted(cone), restricted_adj)
    comp_of, members = {}, {}
    for group in groups:
        members[group[0]] = group
        for cell in group:
            comp_of[cell] = group[0]
    cadj, cradj = {}, {}
    for node in cone:
        for succ in successors.get(node, ()):
            a, b = comp_of[node], comp_of[succ]
            if a != b:
                cadj.setdefault(a, set()).add(b)
                cradj.setdefault(b, set()).add(a)

    ordered = [
        cell
        for comp in _restricted_topo(set(members), cadj, cradj)
        for cell in sorted(members[comp])
    ]
    truncated = max(0, len(ordered) - limit)
    if truncated:
        # Keep the inputs and the tail nearest the output; the middle is the bulk.
        head = [c for c in ordered if c in input_cells][: limit // 2]
        tail = [c for c in ordered if c not in head][-(limit - len(head)):]
        ordered = head + tail

    steps = []
    for i, cid in enumerate(ordered):
        info = cg.info.get(cid)
        if info is None:
            continue
        steps.append(
            {
                "order": i,
                "cell": cid,
                "role": INPUT if cid in input_cells else (OUTPUT if cid == cell_id else MIDDLE),
                "label": info.node.label,
                "formula": info.node.formula or "",
                "kind": info.node.kind,
                "value": _plain(values.get(cid)),
                "cached": info.node.value,
                "precedents": sorted(p for p in radj.get(cid, ()) if p in cone),
            }
        )
    return {
        "cell": cell_id,
        "steps": steps,
        "total_ancestors": len(cone) - 1,
        "truncated": truncated,
    }


def _plain(value):
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _proof_graphs(bg, cd, proof_radj):
    """Component-level graph of the proof, plus the cell radj for traces.

    The cell-level forward graph is no longer materialized: ``cell_trace``
    derives the forward edges it needs inside each cone from ``radj``. The
    component graph is built band by band; a proof radj that can name source
    bands directly (``band_sources``) avoids expanding range references to
    cells, which on the largest workbook is hundreds of millions of pairs.
    """
    of_cell, comp_of = bg.of_cell, cd.comp_of
    comp_adj: dict = {}
    comp_radj: dict = {}

    def link(source_band, target_comp):
        source_comp = comp_of.get(source_band)
        if source_comp is None or source_comp == target_comp:
            return
        comp_adj.setdefault(source_comp, set()).add(target_comp)
        comp_radj.setdefault(target_comp, set()).add(source_comp)

    band_sources = getattr(proof_radj, "band_sources", None)
    if band_sources is not None:
        range_bands = getattr(bg, "range_bands", None)
        if range_bands is None:
            range_bands = {}
        for target in proof_radj:
            target_comp = comp_of.get(of_cell.get(target))
            if target_comp is None:
                continue
            for source_band in band_sources(target, of_cell, range_bands):
                link(source_band, target_comp)
    else:
        for target, sources in proof_radj.items():
            target_comp = comp_of.get(of_cell.get(target))
            if target_comp is None:
                continue
            for source in sources:
                source_band = of_cell.get(source)
                if source_band is not None:
                    link(source_band, target_comp)
    return None, proof_radj, comp_adj, comp_radj


def build(
    bg,
    cd,
    part,
    cg,
    values,
    outputs,
    input_cells,
    cell_limit: int,
    *,
    proof_radj=None,
) -> list:
    if proof_radj is None:
        # Forward edges are derived per trace from radj; never touch cg.adj
        # (materializing it costs the full cell-level expansion).
        cell_adj, cell_radj = None, cg.radj
        comp_adj, comp_radj = cd.comp_adj, cd.comp_radj
    else:
        cell_adj, cell_radj, comp_adj, comp_radj = _proof_graphs(
            bg, cd, proof_radj
        )
    proof_input_comps = {
        comp
        for cell in input_cells
        if (band := bg.of_cell.get(cell)) is not None
        if (comp := cd.comp_of.get(band)) is not None
    }
    traces = []
    for comp in outputs:
        band_id = primary_band(cd, bg, comp)
        band = bg.bands[band_id]
        steps = band_trace(
            bg,
            cd,
            part,
            comp,
            comp_adj=comp_adj,
            comp_radj=comp_radj,
            proof_input_comps=proof_input_comps,
        )
        cells = {}
        for cid in band.cells:
            cells[cid] = cell_trace(
                cg,
                values,
                cid,
                input_cells,
                cell_limit,
                adj=cell_adj,
                radj=cell_radj,
            )
        traces.append(
            Trace(
                output=band_id,
                label=band.label,
                sheet=band.sheet,
                band_steps=steps,
                cell_traces=cells,
                stats={
                    "band_steps": len(steps),
                    "inputs_used": sum(1 for s in steps if s.bucket == INPUT),
                    "middle_steps": sum(1 for s in steps if s.bucket == MIDDLE),
                    "max_depth": max((s.depth for s in steps), default=0),
                    "output_cells": len(cells),
                },
            )
        )
    traces.sort(key=lambda t: -t.stats["band_steps"])
    return traces
