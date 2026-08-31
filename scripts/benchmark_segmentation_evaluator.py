#!/usr/bin/env python3
"""Interleave reference and optimized evaluator benchmarks for one workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xl_seg import evaluate, model, project


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _seed_initial_values(evaluator: evaluate.Evaluator, cell_graph) -> None:
    for cell, info in cell_graph.info.items():
        if info.node.kind in {"input", "label"}:
            evaluator.values[cell] = evaluate.literal(info.node.value)
        elif info.node.kind == "formula":
            evaluator.values[cell] = 0.0
        else:
            evaluator.values[cell] = evaluate.Unresolved(
                "unauthorized-primitive"
            )


def _worker(arguments: argparse.Namespace) -> dict[str, object]:
    tracemalloc.start()
    graph = model.load(Path(arguments.ast_dir), arguments.workbook)
    cell_graph = project.build(graph)
    evaluator = evaluate.Evaluator(
        graph,
        cell_graph,
        strict_proof=True,
        run_probes=False,
        optimize=arguments.mode == "candidate",
        calculation=evaluate.CalculationMetadata(
            available=True,
            iterate=False,
        ),
        clock=lambda: datetime(2026, 8, 31, 6, 30, 15),
    )
    cells = list(cell_graph.info)
    started = time.perf_counter()
    if arguments.full_run:
        inputs = {
            cell
            for cell, info in cell_graph.info.items()
            if info.node.kind in {"input", "label"}
        }
        result = evaluator.run(inputs)
        elapsed = time.perf_counter() - started
        signature = {
            "values": tuple(
                (cell, repr(value))
                for cell, value in sorted(result.values.items())
            ),
            "unresolved": tuple(sorted(result.unresolved.items())),
            "runtime_edges": tuple(
                (target, tuple(sorted(sources)))
                for target, sources in sorted(result.runtime_radj.items())
            ),
            "address_edges": tuple(
                (target, tuple(sorted(sources)))
                for target, sources in sorted(
                    result.runtime_address_radj.items()
                )
            ),
            "resolved_targets": tuple(sorted(result.resolved_targets.items())),
            "resolved_operation_targets": tuple(
                sorted(result.resolved_operation_targets.items())
            ),
            "attempted_reads": tuple(
                (source, tuple(sorted(str(item) for item in consumers)))
                for source, consumers in sorted(result.read_attempts.items())
            ),
            "active_sccs": tuple(
                (item.get("members"), item.get("reason"))
                for item in result.iterated
            ),
            "coverage": result.coverage,
        }
        telemetry = result.benchmark
    else:
        _seed_initial_values(evaluator, cell_graph)
        active_adj = {}
        active_radj = {}
        for _ in range(arguments.graph_builds):
            evaluator._resolved_targets = {}
            evaluator._resolved_operation_targets = {}
            evaluator.runtime_address_radj = {}
            active_adj, active_radj = evaluator._active_graph(cells)
        elapsed = time.perf_counter() - started
        signature = {
            "edges": evaluator._graph_signature(active_adj),
            "reverse_edges": tuple(
                (target, tuple(sorted(sources)))
                for target, sources in sorted(active_radj.items())
            ),
            "resolved_targets": evaluator._target_signature(),
            "resolved_operation_targets": tuple(
                (operation, tuple(sorted(targets)))
                for operation, targets in sorted(
                    evaluator._resolved_operation_targets.items()
                )
            ),
            "address_edges": tuple(
                (target, tuple(sorted(sources)))
                for target, sources in sorted(
                    evaluator.runtime_address_radj.items()
                )
            ),
            "attempted_reads": tuple(
                (source, tuple(sorted(str(item) for item in consumers)))
                for source, consumers in sorted(evaluator.read_attempts.items())
            ),
        }
        telemetry = evaluator._benchmark
    _, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "mode": arguments.mode,
        "operation": "full_evaluator" if arguments.full_run else "active_graph",
        "seconds": elapsed,
        "process_peak_rss_bytes": _rss_bytes(),
        "tracemalloc_peak_bytes": traced_peak,
        "cells": len(cells),
        "graph_builds": (
            telemetry["active_graph"]["calls"]
            if arguments.full_run
            else arguments.graph_builds
        ),
        "signature_sha256": _canonical_hash(signature),
        "telemetry": telemetry,
    }


def _run_sample(arguments: argparse.Namespace, mode: str) -> dict[str, object]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        arguments.workbook,
        "--ast-dir",
        str(Path(arguments.ast_dir).resolve()),
        "--graph-builds",
        str(arguments.graph_builds),
        "--mode",
        mode,
        "--worker",
    ]
    if arguments.full_run:
        command.append("--full-run")
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _parent(arguments: argparse.Namespace) -> dict[str, object]:
    samples = []
    for round_index in range(1, arguments.repeats + 1):
        modes = (
            ("reference", "candidate")
            if round_index % 2
            else ("candidate", "reference")
        )
        for mode in modes:
            sample = _run_sample(arguments, mode)
            sample["round"] = round_index
            samples.append(sample)
    by_mode = {
        mode: [item for item in samples if item["mode"] == mode]
        for mode in ("reference", "candidate")
    }
    medians = {
        mode: statistics.median(
            float(item["seconds"]) for item in records
        )
        for mode, records in by_mode.items()
    }
    signatures = {
        mode: sorted({item["signature_sha256"] for item in records})
        for mode, records in by_mode.items()
    }
    speedup = medians["reference"] / medians["candidate"]
    equivalent = (
        len(signatures["reference"]) == 1
        and signatures["reference"] == signatures["candidate"]
    )
    return {
        "schema_version": "segmentation-evaluator-benchmark/v1",
        "workbook": arguments.workbook,
        "ast_dir": str(Path(arguments.ast_dir).resolve()),
        "repeats": arguments.repeats,
        "graph_builds_per_sample": arguments.graph_builds,
        "operation": "full_evaluator" if arguments.full_run else "active_graph",
        "minimum_speedup": arguments.minimum_speedup,
        "median_seconds": medians,
        "speedup": speedup,
        "normalized_graph_equivalent": equivalent,
        "passed": equivalent and speedup >= arguments.minimum_speedup,
        "peak_rss_bytes": {
            mode: max(int(item["process_peak_rss_bytes"]) for item in records)
            for mode, records in by_mode.items()
        },
        "tracemalloc_peak_bytes": {
            mode: max(int(item["tracemalloc_peak_bytes"]) for item in records)
            for mode, records in by_mode.items()
        },
        "samples": samples,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook")
    parser.add_argument("--ast-dir", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--graph-builds", type=int, default=4)
    parser.add_argument("--minimum-speedup", type=float, default=1.05)
    parser.add_argument("--full-run", action="store_true")
    parser.add_argument(
        "--mode", choices=("reference", "candidate"), default="candidate"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.repeats < 1 or arguments.graph_builds < 1:
        raise SystemExit("repeats and graph-builds must be positive")
    report = _worker(arguments) if arguments.worker else _parent(arguments)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if arguments.worker or report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
