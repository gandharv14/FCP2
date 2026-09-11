"""Derivation traces: how each output is built, all the way back to inputs.

Two resolutions are produced for every output. The band trace is the readable one
-- a few dozen line items in dependency order, which is how a modeller would walk
someone through the calculation. The cell trace is the exact one: every individual
cell, its formula, its recomputed value, and the cells it consumed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from functools import partial
import hashlib
import json
from pathlib import Path
import tempfile
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


class CellTraceStore:
    """Private disposable spool; retain one decoded trace at a time."""

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="xl-lineage-")
        self.directory = Path(self._temporary.name)
        self._records = {}
        self._closed = False

    def get(self, key, factory):
        if self._closed:
            raise ValueError("lineage trace store is closed")
        record = self._records.get(key)
        if record is None:
            detail = factory()
            path = self.directory / f"{len(self._records)}.json"
            temporary = path.with_suffix(".tmp")
            try:
                with temporary.open("w", encoding="utf-8") as handle:
                    json.dump(detail, handle)
                content = temporary.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                temporary.replace(path)
                self._records[key] = (path, digest)
            finally:
                temporary.unlink(missing_ok=True)
            return detail
        path, digest = record
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("lineage trace spool changed")
        return json.loads(content)

    def close(self):
        self._closed = True
        self._temporary.cleanup()


class CellTraces(Mapping):
    def __init__(self, cells, store, factory):
        self._cells = tuple(cells)
        self._keys = set(self._cells)
        self._store = store
        self._factory = factory

    def __len__(self):
        return len(self._cells)

    def __iter__(self):
        return iter(self._cells)

    def __getitem__(self, cell):
        if cell not in self._keys:
            raise KeyError(cell)
        return self._store.get(cell, partial(self._factory, cell_id=cell))


class Traces(list):
    def __init__(self, traces, store):
        super().__init__(traces)
        self._store = store

    def close(self):
        self._store.close()


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

    # Canonicalize both Tarjan roots and successors.  The proof graphs store
    # adjacency in sets, whose iteration order changes with PYTHONHASHSEED.
    restricted_adj = {
        node: tuple(sorted(succ for succ in adj.get(node, ()) if succ in cone))
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
        for succ in adj.get(node, ()):
            if succ not in cone:
                continue
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
    # Freeze scalar proof results at the same point as the previous eager build.
    # Graphs are immutable during emission; each cell's exact trace is computed
    # once, then integrity-checked on subsequent Markdown/JSON reads.
    store = CellTraceStore()
    try:
        factory = partial(cell_trace, cg=cg, values=dict(values),
                          input_cells=set(input_cells), limit=cell_limit,
                          adj=cell_adj, radj=cell_radj)
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
            cells = CellTraces(band.cells, store, factory)
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
        return Traces(traces, store)
    except BaseException:
        store.close()
        raise
