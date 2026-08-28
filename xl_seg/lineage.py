"""Derivation traces: how each output is built, all the way back to inputs.

Two resolutions are produced for every output. The band trace is the readable one
-- a few dozen line items in dependency order, which is how a modeller would walk
someone through the calculation. The cell trace is the exact one: every individual
cell, its formula, its recomputed value, and the cells it consumed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .condense import strongly_connected, topo_order
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
    order.extend(sorted(n for n in cone if n not in set(order)))
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
    adj = cg.adj if adj is None else adj
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

    groups = strongly_connected(cone, {n: {s for s in adj.get(n, ()) if s in cone} for n in cone})
    comp_of, members = {}, {}
    for group in groups:
        members[group[0]] = group
        for cell in group:
            comp_of[cell] = group[0]
    cadj, cradj = {}, {}
    for node in cone:
        for succ in adj.get(node, ()):
            if succ not in cone:
                continue
            a, b = comp_of[node], comp_of[succ]
            if a != b:
                cadj.setdefault(a, set()).add(b)
                cradj.setdefault(b, set()).add(a)

    ordered = [c for comp in topo_order(members, cadj, cradj) for c in sorted(members[comp])]
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
    cell_radj = {
        target: set(sources) for target, sources in proof_radj.items()
    }
    cell_adj = {}
    comp_adj, comp_radj = {}, {}
    for target, sources in cell_radj.items():
        target_band = bg.of_cell.get(target)
        target_comp = cd.comp_of.get(target_band)
        for source in sources:
            cell_adj.setdefault(source, set()).add(target)
            source_band = bg.of_cell.get(source)
            source_comp = cd.comp_of.get(source_band)
            if source_comp is None or target_comp is None or source_comp == target_comp:
                continue
            comp_adj.setdefault(source_comp, set()).add(target_comp)
            comp_radj.setdefault(target_comp, set()).add(source_comp)
    return cell_adj, cell_radj, comp_adj, comp_radj


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
        cell_adj, cell_radj = cg.adj, cg.radj
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
