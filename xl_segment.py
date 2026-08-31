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
import hashlib
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

from xl_seg import adjudicate, bands, condense, diagnostics, emit, evaluate, frontier, lineage
from xl_seg import model, partition, project, publication, restriction_cone


MAX_PROOF_CLOSURE_PASSES = 12


def _eligible_proof_input(cg, cell):
    """Only typed primitive cells may supply values to a strict proof."""
    info = cg.info.get(cell)
    return info is not None and info.node.kind in ("input", "label")


def _runtime_proof_radj(cg, result):
    """Active value graph, with static fallback for legacy evaluator results."""
    runtime = getattr(result, "runtime_radj", None) or {}
    out = {}
    for target in cg.info:
        info = cg.info[target]
        if info.node.kind == "formula" and target in runtime:
            out[target] = set(runtime[target])
        else:
            out[target] = set(cg.radj.get(target, ()))
    for target, sources in runtime.items():
        out[target] = set(sources)
    return out


def _proof_cone(outputs, radj):
    cone, stack = set(outputs), list(outputs)
    while stack:
        target = stack.pop()
        for source in radj.get(target, ()):
            if source not in cone:
                cone.add(source)
                stack.append(source)
    return cone


def _proof_edge_signature(radj, cone):
    return tuple(sorted(
        (source, target)
        for target in cone
        for source in radj.get(target, ())
        if source in cone
    ))


def _resolved_target_signature(result, cone, graph=None):
    operation_targets = getattr(result, "resolved_operation_targets", None)
    if operation_targets is not None:
        return tuple(
            (operation, tuple(sorted(targets)))
            for operation, targets in sorted(operation_targets.items())
            if graph is None
            or (
                graph.nodes.get(operation) is not None
                and graph.nodes[operation].owner in cone
            )
        )
    targets = getattr(result, "resolved_targets", None) or {}
    return tuple(
        (owner, tuple(sorted(sources)))
        for owner, sources in sorted(targets.items())
        if owner in cone
    )


def _active_cycle_groups(cone, radj):
    """Find exact active SCCs in the stabilized output proof graph."""
    adjacency = {cell: set() for cell in cone}
    for target in cone:
        for source in radj.get(target, ()):
            if source in cone:
                adjacency.setdefault(source, set()).add(target)
    groups = evaluate.strongly_connected(
        sorted(cone),
        {
            source: tuple(sorted(targets))
            for source, targets in adjacency.items()
        },
    )
    cycles = []
    for group in groups:
        members = tuple(sorted(group))
        if len(members) > 1 or any(
            cell in adjacency.get(cell, ()) for cell in members
        ):
            cycles.append(members)
    return sorted(cycles)


def _curated_output_identity(entries, chosen, cd, bg):
    """Keep explicit curated bands distinct from their topology components."""
    selected_bands = {band for band in chosen if band in cd.comp_of}
    components = {cd.comp_of[band] for band in selected_bands}
    cells_by_band = {
        band: list(bg.bands[band].cells) for band in selected_bands
    }
    ordered_cells = [
        cell
        for entry in entries
        if entry.get("include")
        for cell in cells_by_band.get(entry.get("band"), ())
    ]
    return components, selected_bands, set(ordered_cells), ordered_cells


def _pinned_source_generation(wb: str, args):
    generation_id = getattr(args, "source_generation_id", None)
    generation_path = getattr(args, "source_generation_path", None)
    if not generation_id and not generation_path:
        return None
    try:
        from xl_source_publication import (
            resolve_source_generation_by_id,
            validate_source_generation,
        )

        if generation_path:
            directory = Path(generation_path)
            manifest = validate_source_generation(
                directory,
                expected_generation_id=generation_id,
            )
        else:
            root = Path(
                getattr(args, "source_generation_root", "source_out")
            ) / wb
            directory, manifest = resolve_source_generation_by_id(
                root, generation_id
            )
    except ValueError as exc:
        raise SystemExit(f"source generation gate failed: {exc}") from exc
    layout = manifest.get("layout") or {}
    if layout.get("workbook_id") != wb:
        raise SystemExit(
            f"source generation workbook ID does not match requested {wb}"
        )
    source = directory / str(layout.get("source_workbook", ""))
    ast_dir = directory / str(layout.get("ast_directory", ""))
    return {
        "directory": directory,
        "manifest": manifest,
        "source": source,
        "ast_dir": ast_dir,
        "health": json.loads(
            (directory / "health.json").read_text(encoding="utf-8")
        ),
        "result": json.loads(
            (directory / "result.json").read_text(encoding="utf-8")
        ),
    }


def stabilize_runtime_proof(
    graph,
    cg,
    declared_inputs,
    output_cells,
    *,
    calculation=None,
    max_passes=MAX_PROOF_CLOSURE_PASSES,
    evaluator_factory=evaluate.Evaluator,
):
    """Discover active edges and primitive seeds until both stop changing."""
    closure_started = time.perf_counter()
    declared_inputs = set(declared_inputs)
    static_cone = _proof_cone(output_cells, cg.radj)
    proof_inputs = {
        cell for cell in static_cone if _eligible_proof_input(cg, cell)
    }
    initial_proof_inputs = set(proof_inputs)
    proof_scope = set(static_cone)
    previous_edges = None
    previous_seeds = None
    previous_targets = None
    history = []
    discovery_benchmarks = []
    stabilized = False

    for pass_index in range(1, max_passes + 1):
        pass_started = time.perf_counter()
        discovery = evaluator_factory(
            graph,
            cg,
            strict_proof=True,
            calculation=calculation,
            proof_outputs=output_cells,
            run_probes=False,
            proof_scope=proof_scope,
        ).run(proof_inputs)
        discovery_benchmarks.append({
            "pass": pass_index,
            "seconds": time.perf_counter() - pass_started,
            "evaluator": dict(getattr(discovery, "benchmark", {}) or {}),
        })
        radj = _runtime_proof_radj(cg, discovery)
        cone = _proof_cone(output_cells, radj)
        next_inputs = {
            cell for cell in cone if _eligible_proof_input(cg, cell)
        }
        edge_signature = _proof_edge_signature(radj, cone)
        seed_signature = tuple(sorted(next_inputs))
        target_signature = _resolved_target_signature(discovery, cone, graph)
        history.append({
            "pass": pass_index,
            "cone_cells": len(cone),
            "value_edges": len(edge_signature),
            "proof_inputs": len(next_inputs),
            "resolved_targets": sum(len(item[1]) for item in target_signature),
            "resolved_targets_sha256": hashlib.sha256(
                json.dumps(
                    target_signature,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "added_inputs": sorted(next_inputs - proof_inputs),
            "removed_inputs": sorted(proof_inputs - next_inputs),
            "active_graph_passes": discovery.coverage.get("active_graph_passes", 0),
            "workbook_iterations": discovery.coverage.get("workbook_iterations", 0),
        })
        if (
            edge_signature == previous_edges
            and seed_signature == previous_seeds
            and target_signature == previous_targets
        ):
            proof_inputs = next_inputs
            stabilized = True
            break
        previous_edges = edge_signature
        previous_seeds = seed_signature
        previous_targets = target_signature
        proof_inputs = next_inputs
        proof_scope = set(cone)

    # The result used for grading is intentionally produced by a new strict
    # evaluator after discovery. It has no expected-value oracle or saved-cache
    # handle, so discovery state cannot bleed into the proof.
    final_started = time.perf_counter()
    result = evaluator_factory(
        graph,
        cg,
        strict_proof=True,
        calculation=calculation,
        proof_outputs=output_cells,
        proof_scope=proof_scope,
    ).run(proof_inputs)
    final_seconds = time.perf_counter() - final_started
    final_radj = _runtime_proof_radj(cg, result)
    final_cone = _proof_cone(output_cells, final_radj)
    final_inputs = {
        cell for cell in final_cone if _eligible_proof_input(cg, cell)
    }
    final_edges = _proof_edge_signature(final_radj, final_cone)
    final_targets = _resolved_target_signature(result, final_cone, graph)
    if (
        final_inputs != proof_inputs
        or final_edges != previous_edges
        or final_targets != previous_targets
    ):
        stabilized = False

    address_radj = {
        target: sorted(sources)
        for target, sources in sorted(
            (getattr(result, "runtime_address_radj", None) or {}).items()
        )
        if target in final_cone
    }
    proof = {
        "schema_version": 1,
        "declared_static_inputs": sorted(declared_inputs),
        "effective_inputs": sorted(proof_inputs),
        "runtime_added_inputs": sorted(proof_inputs - initial_proof_inputs),
        "runtime_radj": {
            target: sorted(source for source in sources if source in final_cone)
            for target, sources in sorted(final_radj.items())
            if target in final_cone
        },
        "runtime_address_radj": address_radj,
        "resolved_targets": {
            target: list(sources)
            for target, sources in sorted(
                (getattr(result, "resolved_targets", None) or {}).items()
            )
            if target in final_cone
        },
        "resolved_operation_targets": {
            operation: list(targets)
            for operation, targets in sorted(
                (
                    getattr(result, "resolved_operation_targets", None)
                    or {}
                ).items()
            )
            if graph.nodes.get(operation) is not None
            and graph.nodes[operation].owner in final_cone
        },
        "attempted_reads": {
            source: sorted(
                consumer for consumer in consumers
                if consumer is not None and consumer in final_cone
            )
            for source, consumers in sorted(
                (getattr(result, "read_attempts", None) or {}).items()
            )
            if any(
                consumer is not None and consumer in final_cone
                for consumer in consumers
            )
        },
        "closure": {
            "stabilized": stabilized,
            "targets_stable": stabilized and final_targets == previous_targets,
            "passes": len(history),
            "max_passes": max_passes,
            "diagnostic": (
                None if stabilized else "runtime_dependency_closure_not_stabilized"
            ),
            "history": history,
        },
        "benchmark": {
            "evaluator_runs": len(history) + 1,
            "max_evaluator_runs": max_passes + 1,
            "scoped_cells": len(final_cone),
            "workbook_cells": len(cg.info),
            "discovery_probes_enabled": False,
            "within_hard_limits": (
                len(history) <= max_passes
                and all(
                    item["active_graph_passes"] <= evaluate.MAX_ACTIVE_PASSES
                    for item in history
                )
            ),
        },
    }
    result.benchmark = dict(getattr(result, "benchmark", {}) or {})
    result.benchmark["proof_closure"] = {
        "seconds": time.perf_counter() - closure_started,
        "discovery_runs": discovery_benchmarks,
        "final_run_seconds": final_seconds,
        "evaluator_runs": len(discovery_benchmarks) + 1,
    }
    return result, proof_inputs, final_radj, proof


def segment(wb: str, args) -> dict:
    started = time.time()
    source_generation = _pinned_source_generation(wb, args)
    if source_generation is None:
        ast_dir = Path(args.ast_dir) / wb
        source = Path(args.source) / f"{wb}.xlsx"
    else:
        ast_dir = source_generation["ast_dir"]
        source = source_generation["source"]
    if not (ast_dir / "nodes.csv").exists():
        raise SystemExit(f"no graph at {ast_dir}")
    try:
        from xl_source_publication import validate_bound_ast_if_required

        validate_bound_ast_if_required(
            source,
            ast_dir,
            require=not getattr(args, "allow_legacy_ast", True),
        )
    except ValueError as exc:
        raise SystemExit(f"source/AST provenance gate failed: {exc}") from exc
    out_dir = Path(args.out) / wb
    out_dir.mkdir(parents=True, exist_ok=True)
    curation_path = out_dir / "curation.toml"
    original_curation = (
        curation_path.read_bytes() if curation_path.is_file() else None
    )

    graph = model.load(ast_dir, wb)
    cg = project.build(graph)
    bg = bands.build(cg)
    embedded = bands.attach_literal_sources(bg, cg, frontier.is_notable_literal)
    cd = condense.build(bg)

    candidates = frontier.score_outputs(bg, cd)
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

    curation_entries = emit.read_curation(curation_path)
    (
        outputs,
        selected_output_bands,
        output_cells,
        ordered_curated_cells,
    ) = _curated_output_identity(curation_entries, chosen, cd, bg)
    if not outputs:
        raise SystemExit(f"{wb}: no outputs selected in {curation_path}")

    inputs = frontier.input_frontier(cd, outputs)
    part = partition.build(cd, inputs, outputs)

    input_cells = {c for comp in inputs for b in cd.comp_members[comp] for c in bg.bands[b].cells}
    if set(ordered_curated_cells) != output_cells:
        raise SystemExit(
            f"{wb}: curated output identity does not match selected output cells"
        )

    verify = _not_run_verification(
        source,
        ast_dir,
        curation_path,
        ordered_curated_cells,
        "verification_disabled",
    )
    values: dict = {}
    proof_inputs = set(input_cells)
    proof_radj = None
    proof = None
    evaluator_benchmark = {}
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    memory_before, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    verification_started = time.perf_counter()
    if not args.no_verify:
        calculation = evaluate.workbook_calculation_metadata(source)
        result, proof_inputs, proof_radj, proof = stabilize_runtime_proof(
            graph,
            cg,
            input_cells,
            output_cells,
            calculation=calculation,
        )
        evaluator_benchmark = dict(getattr(result, "benchmark", {}) or {})
        values = result.values
        # The expected cache is not opened until bounded discovery and the
        # final fresh strict proof have both completed.
        expected_cache = (
            evaluate.workbook_expected_cache(source) if source.exists() else None
        )
        verify = _verify(
            cg, result, proof_inputs, output_cells, part, cd, bg, args,
            expected_cache=expected_cache,
            calculation=calculation,
            source_path=source,
            ast_dir=ast_dir,
            curation_path=curation_path,
            proof=proof,
            curated_output_cells=ordered_curated_cells,
        )
        if expected_cache is not None and hasattr(expected_cache, "close"):
            expected_cache.close()
    verification_runtime_s = time.perf_counter() - verification_started
    _, memory_peak = tracemalloc.get_traced_memory()
    peak_memory_bytes = max(0, int(memory_peak - memory_before))
    if not already_tracing:
        tracemalloc.stop()

    literals = [
        {**rec, "bucket": part.bucket.get(cd.comp_of.get(rec["band"]), partition.SCAFFOLD)}
        for rec in embedded
    ]
    promoted = sum(1 for rec in literals if rec["bucket"] == partition.INPUT)
    if literals:
        print(f"  {wb}: {len(literals)} hardcoded constants promoted to source bands, "
              f"{promoted} inside the output cone (-> inputs)")
    traces = lineage.build(
        bg,
        cd,
        part,
        cg,
        values,
        sorted(outputs),
        proof_inputs,
        args.lineage_max,
        proof_radj=proof_radj,
    )

    if (
        original_curation is not None
        and not args.recurate
        and curation_path.read_bytes() != original_curation
    ):
        raise SystemExit(
            f"{wb}: existing curation.toml changed outside explicit recuration"
        )

    cone_certificate = None
    if source_generation is not None:
        source_route = source_generation["health"].get("route")
        if source_route in {"restricted_pass", "restricted_recalc_pass"}:
            if proof is None:
                raise SystemExit(
                    f"{wb}: restricted segmentation requires strict verification"
                )
            try:
                cone_certificate = restriction_cone.build_certificate(
                    source_generation_dir=source_generation["directory"],
                    graph=graph,
                    proof=proof,
                    verification=verify,
                    ordered_outputs=ordered_curated_cells,
                    segmentation_fingerprints=verify.get("fingerprints") or {},
                )
            except restriction_cone.RestrictionConeError as exc:
                raise SystemExit(
                    f"{wb}: restriction cone certification failed: {exc}"
                ) from exc
        publication.bind_source_generation(
            verify,
            source_generation["manifest"],
            certificate=cone_certificate,
        )
    publication.attach_generation_contract(verify)
    stage_dir = publication.make_staging_directory(Path(args.out), wb)
    emit.write_candidates(stage_dir, candidates, bg)
    emit.write_bands(stage_dir, bg, cd, part)
    payload = emit.write_segments(
        stage_dir,
        wb,
        bg,
        cd,
        part,
        inputs,
        outputs,
        literals,
        verify,
        proof=proof,
        selected_output_bands=selected_output_bands,
    )
    emit.write_lineage(stage_dir, wb, traces, values, proof=proof)
    if cone_certificate is not None:
        (stage_dir / "restriction-cone-certificate.json").write_bytes(
            restriction_cone.certificate_bytes(cone_certificate)
        )
    generation_dir, generation_manifest = publication.publish_generation(
        stage_dir,
        out_dir,
        verify,
        ordered_curated_cells,
        source_path=source,
        ast_dir=ast_dir,
        source_generation_dir=(
            source_generation["directory"]
            if source_generation is not None
            else None
        ),
    )

    payload["elapsed_s"] = round(time.time() - started, 2)
    payload["benchmark"] = {
        "verifier_runtime_s": verification_runtime_s,
        "peak_memory_bytes": peak_memory_bytes,
        "evaluator": evaluator_benchmark,
    }
    payload["generation"] = {
        "generation_id": generation_manifest["generation_id"],
        "directory": str(generation_dir),
        "manifest": str(generation_dir / "generation-manifest.json"),
    }
    _report(wb, payload, part, verify, traces, generation_dir)
    return payload


def _not_run_verification(
    source_path,
    ast_dir,
    curation_path,
    ordered_output_cells,
    reason,
):
    fingerprints, missing = publication.evidence_fingerprints(
        source_path,
        ast_dir,
        curation_path,
        ordered_output_cells,
    )
    classification = diagnostics.classify_disposition({
        "executed": False,
        "reason_counts": {reason: 1},
        "missing_evidence": missing,
    })
    return {
        "schema_version": diagnostics.SCHEMA_VERSION,
        "status": "not_run",
        "disposition": classification["disposition"],
        "primary_ownership": classification["primary_ownership"],
        "forensic_primary_ownership":
            classification["forensic_primary_ownership"],
        "operational_ownership": classification["operational_ownership"],
        "cache_policy": classification["cache_policy"],
        "cache_policy_by_reason": classification["cache_policy_by_reason"],
        "blocking_reasons": classification["reason_codes"],
        "blocking_reason_details": classification["reason_details"],
        "skipped": True,
        "passed": False,
        "counts": {
            "outputs": {"eligible": None, "checked": 0},
            "middle": {"eligible": None, "checked": 0},
            "failures": {"complete": 0, "sampled": 0},
            "cycles": {"complete": 0, "sampled": 0},
            "runtime": {
                "read_cells": 0,
                "dependency_edges": 0,
                "missing_read_cells": 0,
            },
            "cache_reads": {"proof": 0, "post_proof_comparison": 0},
        },
        "samples": {},
        "provenance": {
            "proof": {
                "strict": None,
                "expected_cache_available": False,
                "expected_cache_reads": 0,
            },
            "seeds": {},
            "runtime": {},
            "cycles": {},
            "comparison_cache": {
                "opened_after_proof": False,
                "reads": 0,
            },
        },
        "fingerprints": fingerprints,
        "generation_id": diagnostics.generation_id(fingerprints),
        "missing_evidence": missing,
    }


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


def _verify(
    cg,
    result,
    input_cells,
    output_cells,
    part,
    cd,
    bg,
    args,
    *,
    expected_cache=None,
    calculation=None,
    source_path=None,
    ast_dir=None,
    curation_path=None,
    proof=None,
    curated_output_cells=None,
):
    """Recompute from the frontier and grade the result."""
    middle_cells = sorted([
        c for comp in cd.comp_members
        if part.bucket.get(comp) == partition.MIDDLE
        for b in cd.comp_members[comp] for c in bg.bands[b].cells
        if cg.info[c].node.kind == "formula" and not cg.info[c].is_literal
    ])
    # The output proof is intentionally scoped. A middle cell outside that
    # scope has not been evaluated, so ``result.values.get`` returning None
    # must not be reported as an unresolved formula. Keep the deterministic
    # sample over executed cells and expose the remainder explicitly as
    # ``not_run`` diagnostics.
    evaluated_cells = getattr(result, "evaluated_cells", None)
    executed_middle_cells = [
        cell for cell in middle_cells
        if (
            cell in evaluated_cells
            if evaluated_cells is not None
            else cell in result.values
        )
    ]
    middle_not_run = sorted(set(middle_cells) - set(executed_middle_cells))
    rng = random.Random(0)
    sample = (
        executed_middle_cells
        if len(executed_middle_cells) <= args.sample
        else sorted(rng.sample(executed_middle_cells, args.sample))
    )

    runtime_radj = result.runtime_radj or {}
    stabilized_radj = (proof or {}).get("runtime_radj")
    effective_radj = {}
    for target in cg.info:
        if stabilized_radj is not None:
            effective_radj[target] = set(stabilized_radj.get(target, ()))
        else:
            effective_radj[target] = set(cg.radj.get(target, ()))
            effective_radj[target].update(runtime_radj.get(target, ()))
    if stabilized_radj is not None:
        for target, sources in stabilized_radj.items():
            effective_radj[target] = set(sources)

    cone, stack = set(output_cells), list(output_cells)
    while stack:
        node = stack.pop()
        for pred in effective_radj.get(node, ()):
            if pred not in cone:
                cone.add(pred)
                stack.append(pred)
    proof_cycle_groups = _active_cycle_groups(cone, effective_radj)

    complete_cycle_groups = dict(getattr(result, "cycle_groups", {}) or {})
    if not complete_cycle_groups:
        for cell, cycle_id in (getattr(result, "cycle_membership", {}) or {}).items():
            complete_cycle_groups.setdefault(cycle_id, []).append(cell)
        complete_cycle_groups = {
            cycle_id: tuple(sorted(members))
            for cycle_id, members in complete_cycle_groups.items()
        }
    diagnostics_by_id = {
        cycle_id: dict(result.iterated[cycle_id - 1])
        for cycle_id in complete_cycle_groups
        if isinstance(cycle_id, int) and 0 < cycle_id <= len(result.iterated)
    }
    relevant_cycle_ids = set()
    missing_cycle_diagnostics = 0
    for members in proof_cycle_groups:
        ids = {
            result.cycle_membership.get(cell)
            for cell in members
            if result.cycle_membership.get(cell) is not None
        }
        if len(ids) != 1:
            missing_cycle_diagnostics += 1
            continue
        cycle_id = next(iter(ids))
        if (
            cycle_id not in diagnostics_by_id
            or set(complete_cycle_groups.get(cycle_id, ())) != set(members)
        ):
            missing_cycle_diagnostics += 1
            continue
        relevant_cycle_ids.add(cycle_id)
    # If iteration itself changed the active equations, the final graph may no
    # longer contain the SCC that was output-relevant when the budget ran. Keep
    # that unstable cycle fail-closed when its members remain in the proof cone;
    # a detached cycle still cannot enter through this path.
    for cycle_id, diagnostic in diagnostics_by_id.items():
        members = set(complete_cycle_groups.get(cycle_id, ()))
        if (
            diagnostic.get("output_relevant") is True
            and members & cone
            and (
                diagnostic.get("topology_stable") is not True
                or diagnostic.get("targets_stable") is not True
            )
        ):
            relevant_cycle_ids.add(cycle_id)

    cycle_diagnostics = []
    for index, raw_diagnostic in enumerate(result.iterated, 1):
        diagnostic = dict(raw_diagnostic)
        diagnostic["output_relevant"] = index in relevant_cycle_ids
        cycle_diagnostics.append(diagnostic)

    cycle_endpoint_checks = {}
    endpoint_check_failures = set()

    def cycle_safe_for_equivalence(cycle_id):
        diagnostic = diagnostics_by_id.get(cycle_id)
        if diagnostic is None:
            return False
        delta = result.coverage.get("iteration_delta")
        residual = diagnostic.get("residual")
        return (
            cycle_id in relevant_cycle_ids
            and getattr(calculation, "iterate", None) is True
            and diagnostic.get("iteration_enabled") is True
            and diagnostic.get("converged") is True
            and not diagnostic.get("budget_exhausted")
            and residual is not None
            and delta is not None
            and residual <= delta
            and not diagnostic.get("errors")
            and not diagnostic.get("unresolved")
            and diagnostic.get("topology_stable") is True
            and diagnostic.get("targets_stable") is True
            and diagnostic.get("uniqueness") != "demonstrated_non_unique"
            and diagnostic.get("certified") is True
        )

    def cycle_equivalent(cycle_id):
        if cycle_id in cycle_endpoint_checks:
            cached = cycle_endpoint_checks[cycle_id]
            return cached.get("equivalent", False) if isinstance(cached, dict) else bool(cached)
        checker = getattr(result, "endpoint_equation_checker", None)
        members = tuple(complete_cycle_groups.get(cycle_id, ()))
        if not cycle_safe_for_equivalence(cycle_id) or not callable(checker):
            endpoint_check_failures.add(cycle_id)
            cycle_endpoint_checks[cycle_id] = False
            return False
        expected_state = {}
        actual_state = {}
        for member in members:
            parsed = model.split_ref(member)
            if expected_cache is None or parsed is None:
                endpoint_check_failures.add(cycle_id)
                cycle_endpoint_checks[cycle_id] = False
                return False
            expected_state[member] = evaluate.literal(expected_cache(*parsed))
            actual_state[member] = result.values.get(member)
        expected_check = checker(members, expected_state)
        actual_check = checker(members, actual_state)
        delta = result.coverage.get("iteration_delta")
        equivalent = (
            expected_check.get("safe") is True
            and actual_check.get("safe") is True
            and expected_check.get("residual") is not None
            and actual_check.get("residual") is not None
            and expected_check["residual"] <= delta
            and actual_check["residual"] <= delta
            and expected_check.get("equations") == actual_check.get("equations")
        )
        cycle_endpoint_checks[cycle_id] = {
            "equivalent": equivalent,
            "expected": expected_check,
            "recomputed": actual_check,
        }
        if not equivalent:
            endpoint_check_failures.add(cycle_id)
        return equivalent

    def grade(cells):
        tally = {"match": 0, "mismatch": 0, "unresolved": 0, "unverifiable": 0}
        failures = []
        eligible = 0
        for cid in sorted(cells):
            info = cg.info.get(cid)
            if info is None or info.node.kind != "formula" or info.is_literal:
                continue
            eligible += 1
            expected = info.node.value
            parsed = model.split_ref(cid)
            if expected_cache is not None and parsed is not None:
                expected = expected_cache(*parsed)
            actual = result.values.get(cid)
            verdict, diff = evaluate.compare(expected, actual)
            cycle_id = result.cycle_membership.get(cid)
            if (
                verdict == "mismatch"
                and cycle_id in relevant_cycle_ids
                and cycle_equivalent(cycle_id)
            ):
                verdict = "match"
            tally[verdict] += 1
            if verdict in ("mismatch", "unresolved", "unverifiable"):
                wanted = evaluate.literal(expected)
                scale = (
                    max(abs(float(wanted)), 1.0)
                    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool)
                    else None
                )
                failures.append({
                    "cell": cid, "verdict": verdict, "label": info.node.label,
                    "formula": info.node.formula,
                    "workbook": "" if expected is None else str(expected),
                    "recomputed": str(actual)[:40],
                    "absolute_error": diff,
                    "relative_error": (diff / scale if diff is not None and scale else None),
                    "unresolved_reason": (
                        actual.reason if isinstance(actual, evaluate.Unresolved) else None
                    ),
                    "cycle_id": cycle_id,
                })
        return tally, failures, eligible

    out_tally, out_bad, output_eligible = grade(output_cells)
    mid_tally, mid_bad, middle_checked = grade(sample)
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
    seeded_cells = set(getattr(result, "seeded_cells", ()) or input_cells)
    leaked = sorted((seeded_cells - set(input_cells)) & cone)

    # Cells the evaluator read straight from the workbook cache because the
    # graph never recorded them (parse failures, adoption gaps). One of these
    # feeding the output cone means an output was partly handed its answer.
    oracle_reads = result.oracle_reads or {}
    oracle_accesses = getattr(result, "oracle_accesses", None) or {}
    oracle_leaks = sorted(
        cid for cid, consumers in oracle_reads.items()
        if any(consumer is None or consumer in cone for consumer in consumers)
    )

    relevant_cycle_diagnostics = [
        diagnostics_by_id[cycle_id]
        for cycle_id in sorted(relevant_cycle_ids)
        if cycle_id in diagnostics_by_id
    ]
    iteration_delta = result.coverage.get("iteration_delta")
    cycle_reason_counts = {
        "active_cycle_diagnostics_missing": missing_cycle_diagnostics,
        "active_cycle_iteration_status_unknown": sum(
            getattr(calculation, "iterate", None) is None
            for _ in relevant_cycle_diagnostics
        ),
        "active_cycle_iteration_count_unknown": sum(
            getattr(calculation, "iterate_count_origin", "unknown")
            not in ("explicit", "default")
            for _ in relevant_cycle_diagnostics
        ),
        "active_cycle_iteration_delta_unknown": sum(
            getattr(calculation, "iterate_delta_origin", "unknown")
            not in ("explicit", "default")
            for _ in relevant_cycle_diagnostics
        ),
        "active_cycle_iteration_disabled": sum(
            diagnostic.get("iteration_enabled") is not True
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_non_converged": sum(
            diagnostic.get("converged") is not True
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_iteration_budget_exhausted": sum(
            bool(diagnostic.get("budget_exhausted"))
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_residual_unavailable": sum(
            diagnostic.get("residual") is None
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_residual_above_delta": sum(
            diagnostic.get("residual") is not None
            and (
                iteration_delta is None
                or diagnostic.get("residual") > iteration_delta
            )
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_errors": sum(
            bool(diagnostic.get("errors"))
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_unresolved": sum(
            bool(diagnostic.get("unresolved"))
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_topology_not_stabilized": sum(
            diagnostic.get("topology_stable") is not True
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_targets_not_stabilized": sum(
            diagnostic.get("targets_stable") is not True
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_demonstrated_non_unique": sum(
            diagnostic.get("uniqueness") == "demonstrated_non_unique"
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_uncertified": sum(
            diagnostic.get("certified") is not True
            for diagnostic in relevant_cycle_diagnostics
        ),
        "active_cycle_endpoint_equation_unverified": len(endpoint_check_failures),
    }
    graph_capabilities = getattr(cg.graph, "capabilities", {}) or {}
    missing_ast_capabilities = sorted(
        name for name, available in graph_capabilities.items() if not available
    )
    ast_integrity_errors = list(
        getattr(cg.graph, "integrity_errors", ()) or ()
    )
    cycle_blocked = any(cycle_reason_counts.values())
    closure_stabilized = (proof or {}).get("closure", {}).get("stabilized", True)
    proof_within_limits = (proof or {}).get("benchmark", {}).get(
        "within_hard_limits", True
    )
    legacy_passed = (
        out_tally["mismatch"] == 0
        and out_tally["unresolved"] == 0
        and out_tally["unverifiable"] == 0
        and not leaked
        and not oracle_leaks
        and closure_stabilized
        and not cycle_blocked
        and not missing_ast_capabilities
        and not ast_integrity_errors
        and proof_within_limits
        and not result.coverage.get("unknown_ops")
    )

    source_path = Path(source_path) if source_path is not None else Path("")
    ast_dir = Path(ast_dir) if ast_dir is not None else Path("")
    curation_path = Path(curation_path) if curation_path is not None else Path("")
    ordered_output_cells = (
        list(curated_output_cells)
        if curated_output_cells is not None
        else sorted(output_cells)
    )
    fingerprints, missing_evidence = publication.evidence_fingerprints(
        source_path,
        ast_dir,
        curation_path,
        ordered_output_cells,
    )
    if calculation is None or not getattr(calculation, "available", False):
        missing_evidence.append({
            "kind": "calculation_metadata",
            "path": str(source_path),
            "reason": getattr(calculation, "reason", "not_provided"),
        })
    if expected_cache is None:
        missing_evidence.append({
            "kind": "expected_value_cache",
            "path": str(source_path),
            "reason": "unavailable",
        })
    if missing_ast_capabilities:
        missing_evidence.append({
            "kind": "ast_required_capabilities",
            "path": str(ast_dir / "nodes.csv"),
            "reason": "missing_capabilities",
            "capabilities": missing_ast_capabilities,
        })

    reason_counts = {
        "output_mismatch": out_tally["mismatch"],
        "output_unresolved": out_tally["unresolved"],
        "output_unverifiable": out_tally["unverifiable"],
        "seeded_inside_output_cone": len(leaked),
        "proof_expected_cache_read": sum(
            len(consumers) for consumers in oracle_accesses.values()
        ),
        "runtime_dependency_closure_not_stabilized": int(
            not closure_stabilized
        ),
        "proof_benchmark_limits_exceeded": int(not proof_within_limits),
        "ast_integrity_error": len(ast_integrity_errors),
        "unknown_operation": sum(
            int(count)
            for count in (result.coverage.get("unknown_ops") or {}).values()
        ),
        **cycle_reason_counts,
    }
    classification = diagnostics.classify_disposition({
        "executed": True,
        "proof_clean": not any(reason_counts.values()) and not missing_evidence,
        "reason_counts": reason_counts,
        "missing_evidence": missing_evidence,
    })
    blocking_reasons = classification["reason_codes"]
    blocking_details = classification["reason_details"]
    status = "pass" if classification["disposition"] == "pass" else "fail"

    all_failures = sorted(
        out_bad + mid_bad,
        key=lambda record: (record["cell"], record["verdict"]),
    )
    cycle_sample = diagnostics.bounded_sample(
        cycle_diagnostics,
        key=diagnostics.canonical_json,
    )
    runtime_edges = sum(len(sources) for sources in (result.runtime_radj or {}).values())
    runtime_reads = result.read_attempts or {}
    missing_reads = result.missing_reads or {}
    runtime_address_radj = getattr(result, "runtime_address_radj", None) or {}
    resolved_targets = getattr(result, "resolved_targets", None) or {}
    postproof_reads = (
        sum(expected_cache.reads.values())
        if expected_cache is not None and hasattr(expected_cache, "reads")
        else 0
    )

    return {
        "schema_version": diagnostics.SCHEMA_VERSION,
        "status": status,
        "disposition": classification["disposition"],
        "primary_ownership": classification["primary_ownership"],
        "forensic_primary_ownership":
            classification["forensic_primary_ownership"],
        "operational_ownership": classification["operational_ownership"],
        "cache_policy": classification["cache_policy"],
        "cache_policy_by_reason": classification["cache_policy_by_reason"],
        "blocking_reasons": blocking_reasons,
        "blocking_reason_details": blocking_details,
        "skipped": False,
        "input_cells_seeded": len(seeded_cells),
        "declared_static_input_cells": len(
            (proof or {}).get("declared_static_inputs", input_cells)
        ),
        "effective_proof_input_cells": len(input_cells),
        "uncomputable_cells_outside_frontier": len(extra),
        "seeded_inside_output_cone": leaked[:20],
        "seeded_inside_output_cone_count": len(leaked),
        "oracle_fallback_cells": len(oracle_reads),
        "oracle_fallback_inside_output_cone": oracle_leaks[:20],
        "oracle_fallback_inside_output_cone_count": len(oracle_leaks),
        "outputs": out_tally,
        "middle_sample": mid_tally,
        "middle_sample_size": len(sample),
        "iterative_blocks": cycle_diagnostics,
        "unresolved_total": len(result.unresolved),
        "unknown_functions": result.coverage["unknown_ops"],
        "failures": all_failures[: diagnostics.DEFAULT_SAMPLE_LIMIT],
        "divergence_roots": divergence_roots,
        # Kept for existing callers until the planned consumer migration.
        "passed": legacy_passed,
        "counts": {
            "outputs": {
                "eligible": output_eligible,
                "checked": sum(out_tally.values()),
                **out_tally,
            },
            "middle": {
                "eligible": len(middle_cells),
                "executed_eligible": len(executed_middle_cells),
                "not_run": len(middle_not_run),
                "checked": middle_checked,
                **mid_tally,
            },
            "failures": {
                "complete": len(all_failures),
                "sampled": min(len(all_failures), diagnostics.DEFAULT_SAMPLE_LIMIT),
            },
            "cycles": {
                "complete": len(cycle_diagnostics),
                "sampled": len(cycle_sample),
                "output_relevant": len(relevant_cycle_ids),
            },
            "runtime": {
                "read_cells": len(runtime_reads),
                "dependency_edges": runtime_edges,
                "address_edges": sum(
                    len(sources) for sources in runtime_address_radj.values()
                ),
                "resolved_targets": sum(
                    len(sources) for sources in resolved_targets.values()
                ),
                "missing_read_cells": len(missing_reads),
            },
            "cache_reads": {
                "proof": sum(len(consumers) for consumers in oracle_accesses.values()),
                "post_proof_comparison": postproof_reads,
            },
        },
        "samples": {
            "failure_records": all_failures[: diagnostics.DEFAULT_SAMPLE_LIMIT],
            "middle_cells": sample,
            "middle_not_run_cells": middle_not_run[
                : diagnostics.DEFAULT_SAMPLE_LIMIT
            ],
            "cycles": cycle_sample,
            "runtime_read_cells": sorted(runtime_reads)[: diagnostics.DEFAULT_SAMPLE_LIMIT],
            "missing_read_cells": sorted(missing_reads)[: diagnostics.DEFAULT_SAMPLE_LIMIT],
            "resolved_target_cells": sorted({
                source for sources in resolved_targets.values() for source in sources
            })[: diagnostics.DEFAULT_SAMPLE_LIMIT],
        },
        "provenance": {
            "proof": {
                "strict": result.strict_proof,
                "expected_cache_available": False,
                "expected_cache_reads": sum(
                    len(consumers) for consumers in oracle_accesses.values()
                ),
                "declared_static_inputs": (
                    (proof or {}).get("declared_static_inputs", sorted(input_cells))
                ),
                "effective_inputs": sorted(input_cells),
            },
            "seeds": result.seed_provenance,
            "runtime": {
                "active_graph_passes": result.coverage.get("active_graph_passes"),
                "runtime_dependency_edges": runtime_edges,
                "runtime_address_edges": sum(
                    len(sources) for sources in runtime_address_radj.values()
                ),
                "resolved_targets": sum(
                    len(sources) for sources in resolved_targets.values()
                ),
                "attempted_read_cells": len(runtime_reads),
                "missing_read_cells": len(missing_reads),
                "closure": (proof or {}).get("closure"),
            },
            "cycles": {
                "iteration_enabled": result.coverage.get("iteration_enabled"),
                "iteration_setting": result.coverage.get("iteration_setting"),
                "iteration_limit": result.coverage.get("iteration_limit"),
                "iteration_delta": result.coverage.get("iteration_delta"),
                "workbook_iterations": result.coverage.get("workbook_iterations"),
                "active_cycles": result.coverage.get("active_cycles"),
                "output_relevant_cycles": len(relevant_cycle_ids),
                "proof_cycle_groups": [list(group) for group in proof_cycle_groups],
                "active_topology_stable": result.coverage.get(
                    "active_topology_stable"
                ),
                "runtime_targets_stable": result.coverage.get(
                    "runtime_targets_stable"
                ),
                "calculation_metadata": result.coverage.get(
                    "calculation_metadata"
                ),
                "endpoint_equation_checks": {
                    str(cycle_id): check
                    for cycle_id, check in sorted(cycle_endpoint_checks.items())
                },
            },
            "comparison_cache": {
                "opened_after_proof": expected_cache is not None,
                "reads": postproof_reads,
            },
        },
        "fingerprints": fingerprints,
        "generation_id": diagnostics.generation_id(fingerprints),
        "ordered_output_cells": ordered_output_cells,
        "missing_evidence": missing_evidence,
        "ast_integrity": {
            "schema_version": getattr(cg.graph, "ast_schema_version", ""),
            "capabilities": graph_capabilities,
            "error_count": len(ast_integrity_errors),
            "errors": ast_integrity_errors[: diagnostics.DEFAULT_SAMPLE_LIMIT],
        },
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
        status = "PASS" if (
            verify.get("status") == "pass"
            and verify.get("disposition") == "pass"
            and verify.get("blocking_reasons") == []
        ) else "FAIL"
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
    parser.add_argument(
        "--source-generation-id",
        help="pinned immutable source generation ID (never reads current.json)",
    )
    parser.add_argument(
        "--source-generation-path",
        help="exact immutable source generation directory",
    )
    parser.add_argument(
        "--source-generation-root",
        default="source_out",
        help="root containing <workbook>/generations/<source-generation-id>",
    )
    parser.add_argument(
        "--allow-legacy-ast",
        action="store_true",
        help="offline compatibility only: allow AST CSVs without provenance",
    )
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
    if (
        args.source_generation_path or args.source_generation_id
    ) and len(args.workbooks) != 1:
        parser.error("pinned source generation options require exactly one workbook")

    failures = 0
    for wb in args.workbooks:
        payload = segment(wb, args)
        verification = payload["verification"]
        if (
            not verification.get("skipped")
            and (
                verification.get("status") != "pass"
                or verification.get("disposition") != "pass"
                or verification.get("blocking_reasons") != []
            )
        ):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
