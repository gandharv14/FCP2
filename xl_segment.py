#!/usr/bin/env python3
"""Segment a parsed Excel model into inputs, middle, outputs and scaffolding.

Reads the graph ``xl_ast_graph.py`` wrote to ``ast_out/<wb>/`` and answers three
questions about a financial model: what has to be supplied, what has to be worked
out, and what the model concludes -- plus, for every conclusion, the full chain of
calculation that produced it.

The segmentation is verified rather than asserted. Only the input frontier is
seeded with cached values; everything else is recomputed from its parsed formula
and compared against the workbook. If the outputs come back right, the input set
is provably sufficient.

    python3 xl_segment.py 0248 0262 0449 0450
    python3 xl_segment.py 0248 --llm          # LLM adjudicates the output shortlist
    python3 xl_segment.py 0248 --recurate     # discard hand edits, re-score
"""

from __future__ import annotations

import argparse
import collections
import random
import sys
import time
from pathlib import Path

from xl_seg import adjudicate, bands, condense, emit, evaluate, frontier, lineage
from xl_seg import model, partition, project


def segment(wb: str, args) -> dict:
    started = time.time()
    ast_dir = Path(args.ast_dir) / wb
    if not (ast_dir / "nodes.csv").exists():
        raise SystemExit(f"no graph at {ast_dir}")
    out_dir = Path(args.out) / wb
    out_dir.mkdir(parents=True, exist_ok=True)

    graph = model.load(ast_dir, wb)
    cg = project.build(graph)
    bg = bands.build(cg)
    embedded = bands.attach_literal_sources(bg, cg, frontier.is_notable_literal)
    cd = condense.build(bg)

    candidates = frontier.score_outputs(bg, cd)
    curation_path = out_dir / "curation.toml"
    wrote_fresh = args.recurate or not curation_path.exists()
    if wrote_fresh:
        emit.write_curation(out_dir, wb, candidates, args.threshold, args.top)
        if args.llm:
            key = adjudicate.read_key(Path(args.env_file))
            if not key:
                raise SystemExit(
                    f"--llm needs anthropic_api_key or lbx_api_key in {args.env_file}")
            proxy = adjudicate.via_proxy(Path(args.env_file))
            decisions = adjudicate.adjudicate(
                wb, candidates[: args.top], key, args.model, proxy=proxy)
            flipped = adjudicate.apply_to_curation(curation_path, decisions)
            print(f"  adjudicator reviewed {len(decisions)} candidates, changed {flipped}")
    emit.write_candidates(out_dir, candidates, bg)

    def included_bands():
        return {e["band"] for e in emit.read_curation(curation_path) if e.get("include")}

    # Escalation ladder. Only a curation this run wrote is escalated; a
    # pre-existing (possibly hand-edited) file with zero includes still aborts,
    # because overriding a human decision is not this code's call to make.
    chosen = included_bands()
    rung = "llm" if args.llm else "heuristic"
    if not chosen and wrote_fresh:
        if not args.llm:
            key = adjudicate.read_key(Path(args.env_file))
            if key:
                print(f"  {wb}: 0 auto-includes; escalating to LLM adjudicator")
                try:
                    decisions = adjudicate.adjudicate(
                        wb, candidates[: args.top], key, args.model,
                        proxy=adjudicate.via_proxy(Path(args.env_file)))
                    adjudicate.apply_to_curation(curation_path, decisions)
                    rung = "llm"
                except (ValueError, OSError) as exc:
                    print(f"  {wb}: WARNING adjudicator escalation failed ({exc})")
            else:
                print(f"  {wb}: 0 auto-includes and no API key in {args.env_file}")
            # Re-read rather than trust the changed count: an adjudicator that
            # runs cleanly but includes nothing must still fall through.
            chosen = included_bands()
        if not chosen:
            picks = frontier.fallback_outputs(candidates[: args.top])
            if picks:
                emit.apply_fallback(curation_path, [c.band for c in picks])
                rung = "fallback"
                print(f"  {wb}: fallback auto-included top {len(picks)}: "
                      + ", ".join(f"{c.band} ({c.label[:32]!r})" for c in picks))
            chosen = included_bands()
    tally = {"heuristic": 0, "llm": 0, "fallback": 0}
    tally[rung] = len(chosen)
    print(f"  {wb}: outputs {tally['heuristic']} heuristic / "
          f"{tally['llm']} llm / {tally['fallback']} fallback")

    outputs = {cd.comp_of[b] for b in chosen if b in cd.comp_of}
    if not outputs:
        raise SystemExit(f"{wb}: no outputs selected in {curation_path}")

    inputs = frontier.input_frontier(cd, outputs)
    part = partition.build(cd, inputs, outputs)

    input_cells = {c for comp in inputs for b in cd.comp_members[comp] for c in bg.bands[b].cells}
    output_cells = {c for comp in outputs for b in cd.comp_members[comp] for c in bg.bands[b].cells}

    verify = {"skipped": True}
    values: dict = {}
    if not args.no_verify:
        source = next(
            (candidate for candidate in
             (Path(args.source) / f"{wb}{suffix}" for suffix in (".xlsx", ".xlsm"))
             if candidate.exists()),
            None,
        )
        oracle = evaluate.workbook_oracle(source) if source else None
        ev = evaluate.Evaluator(graph, cg, oracle)
        result = ev.run(input_cells)
        values = result.values
        verify = _verify(cg, result, input_cells, output_cells, part, cd, bg, args)

    literals = [
        {**rec, "bucket": part.bucket.get(cd.comp_of.get(rec["band"]), partition.SCAFFOLD)}
        for rec in embedded
    ]
    promoted = sum(1 for rec in literals if rec["bucket"] == partition.INPUT)
    if literals:
        print(f"  {wb}: {len(literals)} hardcoded constants promoted to source bands, "
              f"{promoted} inside the output cone (-> inputs)")
    traces = lineage.build(bg, cd, part, cg, values, sorted(outputs), input_cells, args.lineage_max)

    emit.write_bands(out_dir, bg, cd, part)
    payload = emit.write_segments(out_dir, wb, bg, cd, part, inputs, outputs, literals, verify)
    emit.write_lineage(out_dir, wb, traces, values)

    payload["elapsed_s"] = round(time.time() - started, 2)
    _report(wb, payload, part, verify, traces, out_dir)
    return payload


def _divergence_roots(cg, result, bad_cells):
    """Map each failing cell to its nearest divergence root.

    A root is an ancestor that itself diverges from the workbook's cached
    value while every one of its own sources agrees -- the first place the
    recomputation went wrong, rather than the downstream symptom the grader
    happened to sample. Walks the static edges plus the evaluator's
    runtime-resolved edges so dynamic reads are traced too.
    """
    runtime_radj = result.runtime_radj or {}
    state = {}

    def divergent(cid):
        cached = state.get(cid)
        if cached is not None:
            return cached
        info = cg.info.get(cid)
        if info is None or info.node.kind != "formula" or info.is_literal:
            state[cid] = False
            return False
        verdict, _ = evaluate.compare(info.node.value, result.values.get(cid))
        state[cid] = verdict in ("mismatch", "unresolved")
        return state[cid]

    source_cache = {}

    def sources(cid):
        cached = source_cache.get(cid)
        if cached is None:
            cached = set(cg.radj.get(cid, ()))
            cached.update(runtime_radj.get(cid, ()))
            source_cache[cid] = cached
        return cached

    roots = {}
    for bad in bad_cells:
        seen = {bad}
        frontier = [bad]
        root = None
        budget = 100000
        while frontier and root is None and budget > 0:
            nxt = []
            for cid in frontier:
                budget -= 1
                bad_parents = [p for p in sources(cid) if divergent(p)]
                if not bad_parents:
                    root = cid
                    break
                for parent in bad_parents:
                    if parent not in seen:
                        seen.add(parent)
                        nxt.append(parent)
            frontier = nxt
        if root is None:
            # Every divergent ancestor has a divergent parent: a cycle. Report
            # the deterministic representative so repeated runs agree.
            root = min(cid for cid in seen if divergent(cid))
        roots[bad] = root
    return roots


def _verify(cg, result, input_cells, output_cells, part, cd, bg, args):
    """Recompute from the frontier and grade the result."""
    middle_cells = [
        c for comp in cd.comp_members
        if part.bucket.get(comp) == partition.MIDDLE
        for b in cd.comp_members[comp] for c in bg.bands[b].cells
        if cg.info[c].node.kind == "formula" and not cg.info[c].is_literal
    ]
    rng = random.Random(0)
    sample = middle_cells if len(middle_cells) <= args.sample else rng.sample(middle_cells, args.sample)

    def grade(cells):
        tally = {"match": 0, "mismatch": 0, "unresolved": 0, "unverifiable": 0}
        worst = []
        for cid in cells:
            info = cg.info.get(cid)
            if info is None or info.node.kind != "formula" or info.is_literal:
                continue
            verdict, diff = evaluate.compare(info.node.value, result.values.get(cid))
            tally[verdict] += 1
            if verdict in ("mismatch", "unresolved"):
                worst.append({
                    "cell": cid, "verdict": verdict, "label": info.node.label,
                    "formula": info.node.formula, "workbook": info.node.value,
                    "recomputed": str(result.values.get(cid))[:40],
                })
        return tally, worst

    out_tally, out_bad = grade(sorted(output_cells))
    mid_tally, mid_bad = grade(sample)
    roots = _divergence_roots(cg, result, [r["cell"] for r in out_bad + mid_bad])
    for record in out_bad + mid_bad:
        root = roots.get(record["cell"])
        if root and root != record["cell"]:
            info = cg.info.get(root)
            record["root"] = {
                "cell": root,
                "formula": info.node.formula if info else None,
                "workbook": info.node.value if info else None,
                "recomputed": str(result.values.get(root))[:40],
            }
    root_counts = collections.Counter(roots.values())
    divergence_roots = []
    for root, affected in root_counts.most_common(10):
        info = cg.info.get(root)
        divergence_roots.append({
            "cell": root,
            "affected_failures": affected,
            "formula": info.node.formula if info else None,
            "workbook": info.node.value if info else None,
            "recomputed": str(result.values.get(root))[:40],
        })
    out_bad, mid_bad = out_bad[:20], mid_bad[:20]
    # Some cells have to be seeded because nothing computes them: typed values,
    # labels, and formulas whose only references are empty. They should all sit
    # outside the output cone, so check rather than assume -- a leak would mean an
    # output was handed a cached answer instead of recomputing it. The cone is
    # walked over the static edges plus the evaluator's runtime-resolved edges,
    # because dynamic references (OFFSET) read cells the static graph never saw.
    extra = {
        cid for cid, info in cg.info.items()
        if cid not in input_cells and (info.node.kind in ("input", "label") or info.is_literal)
    }
    runtime_radj = result.runtime_radj or {}
    cone, stack = set(output_cells), list(output_cells)
    while stack:
        node = stack.pop()
        preds = set(cg.radj.get(node, ()))
        preds.update(runtime_radj.get(node, ()))
        for pred in preds:
            if pred not in cone:
                cone.add(pred)
                stack.append(pred)
    leaked = sorted(extra & cone)

    # Cells the evaluator read straight from the workbook cache because the
    # graph never recorded them (parse failures, adoption gaps). One of these
    # feeding the output cone means an output was partly handed its answer.
    oracle_reads = result.oracle_reads or {}
    oracle_leaks = sorted(
        cid for cid, consumers in oracle_reads.items()
        if any(consumer is None or consumer in cone for consumer in consumers)
    )

    return {
        "skipped": False,
        "input_cells_seeded": len(input_cells),
        "uncomputable_cells_outside_frontier": len(extra),
        "seeded_inside_output_cone": leaked[:20],
        "seeded_inside_output_cone_count": len(leaked),
        "oracle_fallback_cells": len(oracle_reads),
        "oracle_fallback_inside_output_cone": oracle_leaks[:20],
        "oracle_fallback_inside_output_cone_count": len(oracle_leaks),
        "outputs": out_tally,
        "middle_sample": mid_tally,
        "middle_sample_size": len(sample),
        "iterative_blocks": result.iterated,
        "unresolved_total": len(result.unresolved),
        "unknown_functions": result.coverage["unknown_ops"],
        "divergence_roots": divergence_roots,
        "failures": out_bad + mid_bad,
        "passed": (out_tally["mismatch"] == 0 and out_tally["unresolved"] == 0
                   and out_tally["unverifiable"] == 0
                   and not leaked and not oracle_leaks),
    }


def _report(wb, payload, part, verify, traces, out_dir):
    counts = payload["counts"]
    print(f"\n=== {wb} ===")
    print(f"  {counts['cells']} cells -> {counts['bands']} bands "
          f"({counts['mirror_bands']} presentation) -> {counts['components']} components")
    print(f"  input {counts['input']} | middle {counts['middle']} | output {counts['output']} | "
          f"scaffolding {counts['scaffolding']} "
          f"(unused_input {counts['unused_input']}, dead {counts['dead']}, "
          f"detached {counts['detached']})")
    if part.unfed:
        print(f"  WARNING: {len(part.unfed)} components reach an output without a frontier input")
    if not verify.get("skipped"):
        out, mid = verify["outputs"], verify["middle_sample"]
        status = "PASS" if verify["passed"] else "FAIL"
        print(f"  verification {status}: outputs {out['match']} match, "
              f"{out['mismatch']} mismatch, {out['unresolved']} unresolved, "
              f"{out['unverifiable']} unverifiable; "
              f"middle sample {mid['match']}/{verify['middle_sample_size']} match")
        if verify["seeded_inside_output_cone_count"]:
            print(f"  WARNING: {verify['seeded_inside_output_cone_count']} seeded cells "
                  f"sit inside the output cone; the frontier is incomplete")
        if verify["oracle_fallback_inside_output_cone_count"]:
            print(f"  WARNING: {verify['oracle_fallback_inside_output_cone_count']} cells "
                  f"missing from the graph fed the output cone from the workbook cache")
        if out["unverifiable"]:
            print(f"  WARNING: {out['unverifiable']} output cells have no cached "
                  f"value to verify against")
        for block in verify["iterative_blocks"]:
            print(f"  circular block of {block['size']} cells: "
                  f"{block['iterations']} iterations, converged={block['converged']}")
        for bad in verify["failures"][:3]:
            print(f"    {bad['verdict']}: {bad['cell']} {bad['formula'][:44]!r} "
                  f"workbook={bad['workbook'][:14]} got={bad['recomputed'][:14]}")
        for root in verify.get("divergence_roots", [])[:5]:
            formula = (root.get("formula") or "")[:44]
            workbook = str(root.get("workbook"))[:14]
            print(f"    root: {root['cell']} affects {root['affected_failures']} "
                  f"failure(s) {formula!r} workbook={workbook} "
                  f"got={root['recomputed'][:24]}")
    print(f"  lineage for {len(traces)} outputs -> {out_dir}/lineage/")
    for trace in traces[:5]:
        print(f"    {trace.label[:44] or trace.output:46s} "
              f"{trace.stats['band_steps']:4d} steps, {trace.stats['inputs_used']:3d} inputs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbooks", nargs="+", help="workbook ids under --ast-dir")
    parser.add_argument("--ast-dir", default="ast_out")
    parser.add_argument("--source", default="4-10 100", help="folder holding <wb>.xlsx")
    parser.add_argument("-o", "--out", default="seg_out")
    parser.add_argument("--threshold", type=float, default=6.0,
                        help="auto-include outputs scoring at least this")
    parser.add_argument("--top", type=int, default=40, help="candidates written for curation")
    parser.add_argument("--sample", type=int, default=400,
                        help="middle cells spot-checked during verification")
    parser.add_argument("--lineage-max", type=int, default=4000,
                        help="max cell-level steps kept per output value")
    parser.add_argument("--recurate", action="store_true",
                        help="overwrite curation.toml, discarding hand edits")
    parser.add_argument("--llm", action="store_true", help="let the adjudicator pick outputs")
    parser.add_argument("--model", default=adjudicate.DEFAULT_MODEL)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)

    failures = 0
    for wb in args.workbooks:
        payload = segment(wb, args)
        if not payload["verification"].get("skipped") and not payload["verification"]["passed"]:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
