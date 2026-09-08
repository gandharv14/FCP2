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
import gc
import hashlib
import json
import os
import random
import sys
import time
import tracemalloc
from pathlib import Path

from xl_seg import adjudicate, bands, condense, diagnostics, emit, evaluate, frontier, lineage
from xl_seg import model, partition, project, publication, restriction_cone


MAX_PROOF_CLOSURE_PASSES = 12
TRACE_MEMORY_ENV = "FCP_TRACE_MEMORY"


def _trace_memory_enabled() -> bool:
    """Whether to measure Python peak memory during verification.

    Off by default: the profiler costs more than the number is worth on a
    production run. Benchmarks and rollout comparisons set the variable.
    """

    return os.environ.get(TRACE_MEMORY_ENV, "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


TIMING_ENV = "XL_SEGMENT_TIMING"


def _timing_enabled() -> bool:
    """Stage timers and RSS prints on stderr; never changes emitted bytes."""
    return os.environ.get(TIMING_ENV, "").strip().lower() not in {
        "", "0", "false", "no", "off",
    }


def _rss_gib() -> float:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 2**30 if sys.platform == "darwin" else peak / 2**20
    except Exception:
        return 0.0


class _StageClock:
    """``mark(name)`` prints seconds since the previous mark and peak RSS."""

    def __init__(self, wb: str, enabled: bool):
        self.wb = wb
        self.enabled = enabled
        self.started = self.last = time.perf_counter()

    def mark(self, name: str, extra: str = "") -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        print(
            f"  [timing] {self.wb} {name:<22} {now - self.last:8.1f}s "
            f"total={now - self.started:8.1f}s peak_rss={_rss_gib():6.1f} GiB {extra}",
            file=sys.stderr,
            flush=True,
        )
        self.last = now


def _wait_for_curation(curation_path: Path, wb: str, seconds: int) -> None:
    """Pause so an operator can edit curation.toml without a reload.

    The graph build is the expensive part of a segmentation run; before this
    flag the only way to change the output cone was to kill the process and
    start over. The wait ends when ``<curation.toml>.ready`` appears (the
    sentinel is removed), on SIGUSR1, or after ``seconds`` (then the run
    continues with the curation as written). The bound must stay under the
    worker's stall detector, which kills a process whose stream and CPU are
    both flat for 1800 s.
    """
    sentinel = curation_path.with_name(curation_path.name + ".ready")
    if sentinel.exists():
        sentinel.unlink()
    resumed = {"signal": False}
    try:
        import signal

        def _resume(signum, frame):
            resumed["signal"] = True

        previous = signal.signal(signal.SIGUSR1, _resume)
    except (ImportError, AttributeError, ValueError):
        previous = None
    print(
        f"  {wb}: curation written to {curation_path}; waiting up to {seconds}s "
        f"for edits. Touch {sentinel} (or send SIGUSR1 to pid {os.getpid()}) "
        "to continue.",
        flush=True,
    )
    deadline = time.monotonic() + max(0, int(seconds))
    while time.monotonic() < deadline:
        if sentinel.exists() or resumed["signal"]:
            break
        time.sleep(1.0)
    else:
        print(f"  {wb}: curation wait expired; continuing with the file as written", flush=True)
    if sentinel.exists():
        sentinel.unlink()
    if previous is not None:
        try:
            import signal

            signal.signal(signal.SIGUSR1, previous)
        except (ValueError, OSError):
            pass


def _eligible_proof_input(cg, cell):
    """Only typed primitive cells may supply values to a strict proof."""
    info = cg.info.get(cell)
    return info is not None and info.node.kind in ("input", "label")


class _ProofRadjView:
    """Active value graph, with static fallback for legacy evaluator results.

    Runtime edges win wherever the evaluator recorded a target; every other
    workbook cell falls back to the static cell graph. This used to be
    materialized as a dict of copied sets over all of ``cg.info`` on every
    proof pass (tens of GB on the largest workbook, held twice at the rebind);
    the view answers the same ``get``/``items`` lookups on demand.
    """

    __slots__ = ("_cg", "_runtime")

    def __init__(self, cg, result):
        self._cg = cg
        self._runtime = getattr(result, "runtime_radj", None) or {}

    def get(self, target, default=None):
        sources = self._runtime.get(target)
        if sources is not None:
            return sources
        if target in self._cg.info:
            return self._cg.radj.get(target, ())
        return default

    def __getitem__(self, target):
        sources = self.get(target)
        if sources is None:
            raise KeyError(target)
        return set(sources)

    def __contains__(self, target) -> bool:
        return target in self._runtime or target in self._cg.info

    def __iter__(self):
        info = self._cg.info
        yield from info
        for target in self._runtime:
            if target not in info:
                yield target

    def keys(self):
        return iter(self)

    def items(self):
        for target in self:
            yield target, self.get(target)

    def band_sources(self, target, of_cell, range_bands):
        """Bands the sources of ``target`` belong to, without expanding ranges.

        Equivalent to ``{of_cell[s] for s in self.get(target)} - {None}``;
        static range references use the range-to-bands map the band builder
        already computed. Lineage needs only band-level edges to build its
        component graph, and the full cell expansion is the expensive part.
        """
        sources = self._runtime.get(target)
        if sources is not None:
            return {of_cell[s] for s in sources if s in of_cell}
        cg = self._cg
        if target not in cg.info:
            return set()
        members = cg.range_members
        out: set = set()
        for ref in cg.sources_of.get(target, ()):
            if ref in members:
                covered = range_bands.get(ref)
                if covered is None:
                    covered = {of_cell[m] for m in members[ref] if m in of_cell}
                    range_bands[ref] = covered
                out |= covered
            elif ref != target and ref in of_cell:
                out.add(of_cell[ref])
        return out


def _runtime_proof_radj(cg, result):
    return _ProofRadjView(cg, result)


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


PRUNE_ROUNDS_CAP = 2
PRUNE_EXIT_CODE = 3
PRUNE_TIME_BUDGET_ENV = "XL_SEGMENT_TIME_BUDGET_SECONDS"
# Absolute alternative (unix seconds): what the recovery worker sets, because
# the attempt clock started before this process did (AST build first).
PRUNE_DEADLINE_ENV = "XL_SEGMENT_DEADLINE_EPOCH"
# A re-proof is only started when the last one, with headroom, fits the budget.
PRUNE_REPROOF_HEADROOM = 1.2
LAST_VERIFICATION_NAME = "last-verification.json"
PRUNING_LOG_NAME = "pruning-log.json"


def _env_float(name: str):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _verification_passes(verify) -> bool:
    return (
        verify.get("status") == "pass"
        and verify.get("disposition") == "pass"
        and verify.get("blocking_reasons") == []
    )


def _output_cone_masks(runtime_radj, bands_cells):
    """cell -> bitmask of the output bands whose proof cone contains it.

    One backward walk per output over the stabilized proof graph. The walk is
    bounded by the cone itself: ``runtime_radj`` only has entries for cells the
    proof reached.
    """
    mask: dict = {}
    for index, (_band, cells) in enumerate(bands_cells):
        bit = 1 << index
        stack = list(cells)
        while stack:
            cell = stack.pop()
            current = mask.get(cell, 0)
            if current & bit:
                continue
            mask[cell] = current | bit
            stack.extend(runtime_radj.get(cell, ()))
    return mask


def _attribute_to_outputs(attribution, certificate_error, runtime_radj, bands_cells, blocking_reasons):
    """Trace every blocking reason to the output bands it sits in.

    Returns ``(implicated, cone_wide, unattributed)``: ``implicated`` maps an
    output band to the sorted reason codes against it; ``cone_wide`` lists
    codes that belong to no cell; ``unattributed`` lists cells a blocking
    reason named that lie in no selected cone (which also stops pruning).
    """
    bands = [band for band, _cells in bands_cells]
    cell_band = {cell: band for band, cells in bands_cells for cell in cells}
    mask = _output_cone_masks(runtime_radj, bands_cells)
    implicated: dict = {}
    unattributed: list = []
    covered: set = set()

    def blame(cell, code):
        bits = mask.get(cell, 0)
        if not bits:
            unattributed.append({"cell": cell, "reason": code})
            return
        covered.add(code)
        for index, band in enumerate(bands):
            if bits & (1 << index):
                implicated.setdefault(band, set()).add(code)

    for record in attribution.get("output_failures", ()):
        code = f"output_{record['verdict']}"
        band = cell_band.get(record["cell"])
        if band is None:
            unattributed.append({"cell": record["cell"], "reason": code})
            continue
        covered.add(code)
        implicated.setdefault(band, set()).add(code)
    for cell in attribution.get("leaked_cells", ()):
        blame(cell, "seeded_inside_output_cone")
    for cell in attribution.get("oracle_leak_cells", ()):
        blame(cell, "proof_expected_cache_read")
    for cell in attribution.get("unknown_op_cells", {}):
        blame(cell, "unknown_operation")
    for cycle in attribution.get("blocking_cycles", ()):
        for cell in cycle.get("cells", ()):
            for code in cycle.get("reasons", ()):
                blame(cell, code)
    cone_wide = list(attribution.get("cone_wide", ()))
    if certificate_error is not None:
        locations = list(getattr(certificate_error, "locations", ()) or ())
        generic = str(certificate_error).startswith(
            "strict segmentation verification is not fully passing"
        )
        if locations:
            for cell in locations:
                blame(cell, "restriction_certificate")
        elif not generic:
            cone_wide.append("restriction_certificate")
    # Safety net: a blocking code no cell explained is treated as cone-wide.
    for code in blocking_reasons or ():
        if code not in covered and code not in cone_wide:
            cone_wide.append(code)
    return (
        {band: sorted(codes) for band, codes in sorted(implicated.items())},
        cone_wide,
        unattributed,
    )


class _OutputPruner:
    """Drops only the outputs the verifier implicates, one bounded round at a time.

    After a failed verification the pruned ``curation.toml`` and the round log
    are written to disk before anything else, so a kill or a spent time budget
    loses no work: the next run of the same command reads the pruned cone. A
    re-proof is only started when the previous one, with headroom, fits the
    remaining budget; otherwise the process exits with ``PRUNE_EXIT_CODE`` and
    asks to be rerun. Reasons the verifier cannot pin to a cell stop pruning.
    """

    def __init__(self, wb, args, curation_path, out_dir, candidates):
        self.wb = wb
        self.curation_path = Path(curation_path)
        self.out_dir = Path(out_dir)
        self.enabled = bool(getattr(args, "prune_failing_outputs", False))
        requested = int(getattr(args, "prune_rounds", 1) or 0)
        self.rounds_left = max(0, min(requested, PRUNE_ROUNDS_CAP))
        self.round = 0
        self.started = time.monotonic()
        self.budget = _env_float(PRUNE_TIME_BUDGET_ENV)
        self.deadline = _env_float(PRUNE_DEADLINE_ENV)
        self.candidate_info = {
            cand.band: {
                "label": cand.label,
                "score": cand.score,
                "depth": cand.features.get("depth"),
            }
            for cand in candidates
        }
        self.rounds: list = []

    def _remaining_budget(self):
        remaining = []
        if self.budget is not None:
            remaining.append(self.budget - (time.monotonic() - self.started))
        if self.deadline is not None:
            remaining.append(self.deadline - time.time())
        return min(remaining) if remaining else None

    def _write_log(self, final_status):
        payload = {
            "schema_version": "output-pruning-log/v1",
            "workbook": self.wb,
            "enabled": self.enabled,
            "time_budget_seconds": self.budget,
            "deadline_epoch": self.deadline,
            "rounds": self.rounds,
            "final_status": final_status,
        }
        (self.out_dir / PRUNING_LOG_NAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _mark_prior_log_verified(self):
        """A rerun after exit 3 closes the log the earlier process left open."""
        path = self.out_dir / PRUNING_LOG_NAME
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        status = str(payload.get("final_status", ""))
        if status.startswith("pruned_pending_rerun"):
            payload["final_status"] = "verified"
            payload["verified_on_rerun"] = True
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )

    def _write_last_verification(self, verify, certificate_error, by_output,
                                 cone_wide, unattributed, passed,
                                 verification_runtime_s):
        payload = {
            "schema_version": "segmentation-last-verification/v1",
            "workbook": self.wb,
            "round": self.round,
            "passed": passed,
            "status": verify.get("status"),
            "disposition": verify.get("disposition"),
            "blocking_reasons": verify.get("blocking_reasons"),
            "reason_counts": (verify.get("attribution") or {}).get("reason_counts"),
            "outputs": verify.get("outputs"),
            "verification_runtime_s": round(verification_runtime_s, 3),
            "certificate_error": (
                None if certificate_error is None else str(certificate_error)
            ),
            "certificate_locations": list(
                getattr(certificate_error, "locations", ()) or ()
            ),
            "by_output": by_output,
            "cone_wide": cone_wide,
            "unattributed_cells": unattributed[: diagnostics.DEFAULT_SAMPLE_LIMIT],
            "attribution": verify.get("attribution"),
        }
        (self.out_dir / LAST_VERIFICATION_NAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _print_table(self, by_output, cone_wide, unattributed, certificate_error):
        print(f"  {self.wb}: verification failed; blame by output band:")
        for row in by_output:
            reasons = ",".join(row["reasons"]) if row["reasons"] else "-"
            print(
                f"    {'PRUNE' if row['implicated'] else 'keep '} "
                f"{row['band']:<28} score={row['score']:<6} "
                f"cells={row['cells']:<4} {reasons}"
            )
        if cone_wide:
            print(f"    cone-wide (cannot attribute, pruning stops): {', '.join(cone_wide)}")
        if unattributed:
            print(f"    {len(unattributed)} blocking cells lie in no selected cone; pruning stops")
        if certificate_error is not None:
            print(f"    certificate: {certificate_error}")

    def decide(self, *, verify, proof, result, certificate_error,
               selected_output_bands, curation_entries, bg, cd,
               verification_runtime_s):
        passed = _verification_passes(verify) and certificate_error is None
        ordered_bands = [
            entry["band"] for entry in curation_entries
            if entry.get("include") and entry.get("band") in selected_output_bands
        ]
        bands_cells = [(band, list(bg.bands[band].cells)) for band in ordered_bands]
        runtime_radj = (proof or {}).get("runtime_radj") or {}
        attribution = verify.get("attribution") or {}
        if passed:
            implicated, cone_wide, unattributed = {}, [], []
        else:
            implicated, cone_wide, unattributed = _attribute_to_outputs(
                attribution,
                certificate_error,
                runtime_radj,
                bands_cells,
                verify.get("blocking_reasons"),
            )
        by_output = []
        for band, cells in bands_cells:
            info = self.candidate_info.get(band, {})
            by_output.append({
                "band": band,
                "label": info.get("label", ""),
                "score": info.get("score"),
                "depth": info.get("depth"),
                "cells": len(cells),
                "implicated": band in implicated,
                "reasons": implicated.get(band, []),
            })
        self._write_last_verification(
            verify, certificate_error, by_output, cone_wide, unattributed,
            passed, verification_runtime_s,
        )
        if passed:
            if self.rounds:
                self._write_log("verified")
            else:
                self._mark_prior_log_verified()
            return "done"
        self._print_table(by_output, cone_wide, unattributed, certificate_error)
        if not self.enabled:
            return "done"
        retained = [band for band in ordered_bands if band not in implicated]
        stop = None
        if cone_wide:
            stop = "cone_wide"
        elif unattributed:
            stop = "unattributed_cells"
        elif not implicated:
            stop = "nothing_to_prune"
        elif not retained:
            stop = "every_output_implicated"
        if stop is not None:
            print(f"  {self.wb}: pruning stopped ({stop}); the cone is unchanged")
            self.rounds.append({
                "round": self.round,
                "verification_runtime_s": round(verification_runtime_s, 3),
                "pruned": [],
                "retained": ordered_bands,
                "stopped": stop,
                "cone_wide": cone_wide,
            })
            self._write_log(f"stopped:{stop}")
            return "done"
        # Persist the decision before anything long-running.
        emit.apply_pruning(self.curation_path, implicated)
        self.rounds.append({
            "round": self.round,
            "verification_runtime_s": round(verification_runtime_s, 3),
            "pruned": [
                {
                    "band": band,
                    "label": self.candidate_info.get(band, {}).get("label", ""),
                    "score": self.candidate_info.get(band, {}).get("score"),
                    "reasons": codes,
                }
                for band, codes in implicated.items()
            ],
            "retained": retained,
            "blocking_reasons": verify.get("blocking_reasons"),
        })
        print(
            f"  {self.wb}: pruned {len(implicated)} of {len(ordered_bands)} outputs; "
            f"{len(retained)} retained in {self.curation_path}"
        )
        remaining = self._remaining_budget()
        needed = verification_runtime_s * PRUNE_REPROOF_HEADROOM
        if self.rounds_left <= 0:
            self._write_log("pruned_pending_rerun:rounds_exhausted")
            print(f"  {self.wb}: cone pruned; rerun the same command (round cap reached)")
            raise SystemExit(PRUNE_EXIT_CODE)
        if remaining is not None and needed > remaining:
            self._write_log("pruned_pending_rerun:time_budget")
            print(
                f"  {self.wb}: cone pruned; rerun the same command "
                f"(re-proof needs ~{needed:.0f}s, {max(0.0, remaining):.0f}s left)"
            )
            raise SystemExit(PRUNE_EXIT_CODE)
        self.rounds_left -= 1
        self.round += 1
        self._write_log("reproving")
        return "rerun"


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
    # Graph-derived caches (argument specs, range shapes, const literals,
    # subtree purity, sort keys) depend only on ``graph``/``cg``. One index is
    # shared by every evaluator this closure builds; value state stays per
    # evaluator, so the final strict evaluator below is still fresh.
    shared = {}
    if evaluator_factory is evaluate.Evaluator:
        shared["index"] = evaluate.GraphIndex()

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
            **shared,
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
        **shared,
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
            target: sorted(
                source for source in final_radj.get(target, ())
                if source in final_cone
            )
            for target in sorted(final_cone)
            if target in final_radj
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

    clock = _StageClock(wb, _timing_enabled())
    graph = model.load(ast_dir, wb)
    clock.mark("load", f"nodes={len(graph.nodes):,} edges={len(graph.edges):,}")
    cg = project.build(graph)
    if clock.enabled:
        pairs = sum(
            len(cg.range_members.get(ref, ()))
            for refs in cg.sources_of.values()
            for ref in refs
        )
        clock.mark(
            "project",
            f"consumers={len(cg.sources_of):,} ranges={len(cg.range_members):,} "
            f"member_pairs_if_expanded={pairs:,}",
        )
    bg = bands.build(cg)
    clock.mark("bands", f"bands={len(bg.bands):,}")
    embedded = bands.attach_literal_sources(bg, cg, frontier.is_notable_literal)
    cd = condense.build(bg)
    clock.mark("condense")

    candidates = frontier.score_outputs(bg, cd)
    clock.mark("score", f"candidates={len(candidates):,}")
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

    if getattr(args, "curation_wait", False):
        _wait_for_curation(
            curation_path, wb, int(getattr(args, "curation_wait_seconds", 1500))
        )
        # The operator's edit is the new reference for the tamper check below;
        # an unexplained change after this point is still an error.
        original_curation = curation_path.read_bytes()
        chosen = included_bands()
    clock.mark("curation")

    # tracemalloc records every allocation, which roughly doubles the cost of
    # allocation-heavy code -- and it wraps the heaviest path in the pipeline.
    # It is a diagnostic, so it is opt-in; peak_memory_bytes stays in the
    # payload as None when it is off so consumers can tell "not measured" from
    # "measured zero".
    trace_memory = _trace_memory_enabled()
    already_tracing = tracemalloc.is_tracing()
    memory_before = 0
    if trace_memory:
        if not already_tracing:
            tracemalloc.start()
        memory_before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()

    restricted_route = (
        source_generation is not None
        and source_generation["health"].get("route")
        in {"restricted_pass", "restricted_recalc_pass"}
    )
    pruner = _OutputPruner(wb, args, curation_path, out_dir, candidates)
    calculation = None
    verification_runtime_s = 0.0
    while True:
        chosen = included_bands()
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

        input_cells = {
            c for comp in inputs for b in cd.comp_members[comp] for c in bg.bands[b].cells
        }
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
        result = None
        proof_inputs = set(input_cells)
        proof_radj = None
        proof = None
        evaluator_benchmark = {}
        cone_certificate = None
        certificate_error = None
        if args.no_verify:
            break
        verification_started = time.perf_counter()
        if calculation is None:
            calculation = evaluate.workbook_calculation_metadata(source)
            clock.mark("calc_metadata")
        result, proof_inputs, proof_radj, proof = stabilize_runtime_proof(
            graph,
            cg,
            input_cells,
            output_cells,
            calculation=calculation,
        )
        evaluator_benchmark = dict(getattr(result, "benchmark", {}) or {})
        values = result.values
        if clock.enabled:
            closure = (proof or {}).get("closure") or {}
            clock.mark(
                "proof",
                f"passes={len(closure.get('history') or [])} "
                f"effective_inputs={len((proof or {}).get('effective_inputs') or [])}",
            )
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
        clock.mark("verify")
        verification_runtime_s = time.perf_counter() - verification_started

        # The certificate is attempted inside the loop so that, on a restricted
        # route, it is only ever issued for the cone that finally passes, and
        # so its cell-level failures feed the same attribution as the verifier's.
        if restricted_route:
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
                certificate_error = exc

        decision = pruner.decide(
            verify=verify,
            proof=proof,
            result=result,
            certificate_error=certificate_error,
            selected_output_bands=selected_output_bands,
            curation_entries=curation_entries,
            bg=bg,
            cd=cd,
            verification_runtime_s=verification_runtime_s,
        )
        if decision == "done":
            break
        # ``decision == "rerun"``: the pruned curation is already on disk and
        # is the new reference for the tamper check below.
        original_curation = curation_path.read_bytes()
        result = proof_radj = None
        values = {}
        gc.collect()

    if certificate_error is not None:
        raise SystemExit(
            f"{wb}: restriction cone certification failed: {certificate_error}"
        )
    peak_memory_bytes = None
    if trace_memory:
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
    clock.mark("lineage", f"traces={len(traces)}")

    if (
        original_curation is not None
        and not args.recurate
        and curation_path.read_bytes() != original_curation
    ):
        raise SystemExit(
            f"{wb}: existing curation.toml changed outside explicit recuration"
        )

    if source_generation is not None:
        if restricted_route and proof is None:
            raise SystemExit(
                f"{wb}: restricted segmentation requires strict verification"
            )
        publication.bind_source_generation(
            verify,
            source_generation["manifest"],
            certificate=cone_certificate,
        )
    # ``build_certificate`` above is the last reader of the in-memory graph.
    # On a restricted route ``publish_generation`` re-parses the AST from disk
    # (``validate_certificate`` is deliberately a fresh load, fail-closed), so
    # dropping the first copy here keeps one graph in memory, not two. The
    # evaluator result is dropped with it: ``endpoint_equation_checker`` is a
    # bound method whose evaluator still holds ``graph``, ``cg`` and its own
    # caches. ``values``, ``bg``, ``cd``, ``part`` and ``traces`` carry only
    # strings and numbers and stay for lineage and the report.
    graph = cg = result = None
    gc.collect()
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
    clock.mark("publish", f"generation={generation_manifest['generation_id'][:12]}")

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


# Blocking reasons that describe the proof as a whole rather than any cell.
# Pruning cannot attribute them to an output cone, so it stops when one fires.
CONE_WIDE_REASONS = (
    "runtime_dependency_closure_not_stabilized",
    "proof_benchmark_limits_exceeded",
    "ast_integrity_error",
    "missing_required_proof_evidence",
)


def _cycle_blocking_reasons(diagnostic, *, calculation, iteration_delta):
    """The ``active_cycle_*`` codes one output-relevant cycle contributes.

    Mirrors the per-code sums in ``_verify`` so the attribution and the tally
    can never disagree about whether a cycle blocks.
    """
    codes = []
    if getattr(calculation, "iterate", None) is None:
        codes.append("active_cycle_iteration_status_unknown")
    if getattr(calculation, "iterate_count_origin", "unknown") not in ("explicit", "default"):
        codes.append("active_cycle_iteration_count_unknown")
    if getattr(calculation, "iterate_delta_origin", "unknown") not in ("explicit", "default"):
        codes.append("active_cycle_iteration_delta_unknown")
    if diagnostic.get("iteration_enabled") is not True:
        codes.append("active_cycle_iteration_disabled")
    if diagnostic.get("converged") is not True:
        codes.append("active_cycle_non_converged")
    if diagnostic.get("budget_exhausted"):
        codes.append("active_cycle_iteration_budget_exhausted")
    residual = diagnostic.get("residual")
    if residual is None:
        codes.append("active_cycle_residual_unavailable")
    elif iteration_delta is None or residual > iteration_delta:
        codes.append("active_cycle_residual_above_delta")
    if diagnostic.get("errors"):
        codes.append("active_cycle_errors")
    if diagnostic.get("unresolved"):
        codes.append("active_cycle_unresolved")
    if diagnostic.get("topology_stable") is not True:
        codes.append("active_cycle_topology_not_stabilized")
    if diagnostic.get("targets_stable") is not True:
        codes.append("active_cycle_targets_not_stabilized")
    if diagnostic.get("uniqueness") == "demonstrated_non_unique":
        codes.append("active_cycle_demonstrated_non_unique")
    if diagnostic.get("certified") is not True:
        codes.append("active_cycle_uncertified")
    return codes


def _attribution_evidence(
    *,
    out_bad,
    leaked,
    oracle_leaks,
    unknown_op_cells,
    cone,
    relevant_cycle_ids,
    diagnostics_by_id,
    complete_cycle_groups,
    missing_cycle_groups,
    endpoint_check_failures,
    calculation,
    iteration_delta,
    reason_counts,
    missing_evidence,
    missing_ast_capabilities,
):
    """Uncapped cell-level evidence for every blocking reason.

    Only reasons that fail the generation appear here; the middle sample is
    report-only and is deliberately absent. ``cone_wide`` lists the codes that
    cannot be traced to a cell and therefore stop output pruning.
    """
    blocking_cycles = []
    for cycle_id in sorted(relevant_cycle_ids):
        diagnostic = diagnostics_by_id.get(cycle_id)
        if diagnostic is None:
            continue
        codes = _cycle_blocking_reasons(
            diagnostic, calculation=calculation, iteration_delta=iteration_delta
        )
        if cycle_id in endpoint_check_failures:
            codes.append("active_cycle_endpoint_equation_unverified")
        if codes:
            blocking_cycles.append({
                "cycle_id": cycle_id,
                "reasons": codes,
                "cells": sorted(set(complete_cycle_groups.get(cycle_id, ())) & cone),
            })
    for members in missing_cycle_groups:
        blocking_cycles.append({
            "cycle_id": None,
            "reasons": ["active_cycle_diagnostics_missing"],
            "cells": sorted(set(members) & cone),
        })
    unknown_in_cone = {
        cell: list(ops)
        for cell, ops in sorted(unknown_op_cells.items())
        if cell in cone
    }
    cone_wide = [
        code for code in CONE_WIDE_REASONS
        if reason_counts.get(code, 0) > 0
    ]
    if missing_evidence or missing_ast_capabilities:
        if "missing_required_proof_evidence" not in cone_wide:
            cone_wide.append("missing_required_proof_evidence")
    if reason_counts.get("proof_expected_cache_read", 0) > 0 and not oracle_leaks:
        cone_wide.append("proof_expected_cache_read")
    if reason_counts.get("unknown_operation", 0) > 0 and not unknown_in_cone:
        cone_wide.append("unknown_operation")
    return {
        "output_failures": [
            {
                "cell": record["cell"],
                "verdict": record["verdict"],
                "unresolved_reason": record.get("unresolved_reason"),
            }
            for record in out_bad
        ],
        "leaked_cells": list(leaked),
        "oracle_leak_cells": list(oracle_leaks),
        "unknown_op_cells": unknown_in_cone,
        "blocking_cycles": blocking_cycles,
        "cone_wide": cone_wide,
        "reason_counts": {
            code: int(count) for code, count in sorted(reason_counts.items()) if count
        },
    }


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
    missing_cycle_groups = []
    for members in proof_cycle_groups:
        ids = {
            result.cycle_membership.get(cell)
            for cell in members
            if result.cycle_membership.get(cell) is not None
        }
        if len(ids) != 1:
            missing_cycle_diagnostics += 1
            missing_cycle_groups.append(members)
            continue
        cycle_id = next(iter(ids))
        if (
            cycle_id not in diagnostics_by_id
            or set(complete_cycle_groups.get(cycle_id, ())) != set(members)
        ):
            missing_cycle_diagnostics += 1
            missing_cycle_groups.append(members)
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
    attribution = _attribution_evidence(
        out_bad=out_bad,
        leaked=leaked,
        oracle_leaks=oracle_leaks,
        unknown_op_cells=getattr(result, "unknown_op_cells", None) or {},
        cone=cone,
        relevant_cycle_ids=relevant_cycle_ids,
        diagnostics_by_id=diagnostics_by_id,
        complete_cycle_groups=complete_cycle_groups,
        missing_cycle_groups=missing_cycle_groups,
        endpoint_check_failures=endpoint_check_failures,
        calculation=calculation,
        iteration_delta=iteration_delta,
        reason_counts=reason_counts,
        missing_evidence=missing_evidence,
        missing_ast_capabilities=missing_ast_capabilities,
    )
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
        # Uncapped cell-level evidence behind every blocking reason, so a
        # failure can be traced to the output cones it sits in. Not part of the
        # published generation; ``segments.json`` drops it (see emit).
        "attribution": attribution,
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
    parser.add_argument(
        "--curation-wait", action="store_true",
        help="after writing curation.toml, pause for edits (touch "
             "curation.toml.ready or send SIGUSR1 to continue) instead of "
             "requiring a rerun",
    )
    parser.add_argument(
        "--curation-wait-seconds", type=int, default=1500,
        help="upper bound for --curation-wait; keep under the worker's "
             "1800 s stall detector (default 1500)",
    )
    parser.add_argument(
        "--prune-failing-outputs", action="store_true",
        help="when strict verification fails, flip include=false only for the "
             "outputs the verifier implicates (marked '# pruned: <codes>' in "
             "curation.toml), then re-verify the rest; reasons that belong to "
             "no cell stop pruning and the run fails as before",
    )
    parser.add_argument(
        "--prune-rounds", type=int, default=1,
        help=f"re-proofs allowed after pruning (default 1, cap {PRUNE_ROUNDS_CAP}); "
             f"when exhausted, or when the re-proof would not fit "
             f"${PRUNE_TIME_BUDGET_ENV}, exit {PRUNE_EXIT_CODE} with the pruned "
             "curation on disk and rerun the same command",
    )
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
