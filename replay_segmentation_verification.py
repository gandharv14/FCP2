#!/usr/bin/env python3
"""Validate or replay the frozen 53-case segmentation corpus.

Validation is the default and performs no writes.  ``--execute`` copies each
curation file into a separate shadow root and runs segmentation there; source,
AST, and original curation artifacts remain read-only inputs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import shutil
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from xl_seg import diagnostics


DEFAULT_MANIFEST = (
    Path(__file__).parent
    / "verification_manifests"
    / "segmentation_failures_53.v1.json"
)
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PRIMARY_COUNTS = {
    "evaluator": 21,
    "AST/parser": 5,
    "frontier": 8,
    "source/cache": 14,
    "insufficient": 5,
    "selection": 0,
}
SAFE_CACHE_POLICIES = {
    "pass": {"retain_as_oracle"},
    "code_fix_required": {"retain_as_oracle"},
    "quarantine": {
        "preserve_ignore_as_authority",
        "not_applicable",
    },
    "recalc_required": {"refresh_after_authoritative_recalc"},
    "recurate_required": {"retain_as_oracle"},
    "insufficient_evidence": {"preserve_pending_diagnostics"},
    "not_run": {"preserve_pending_diagnostics"},
}
OPERATIONAL_OWNERSHIP_BY_DISPOSITION = {
    "pass": None,
    "code_fix_required": "forensic",
    "quarantine": "source/cache",
    "recalc_required": "source/cache",
    "recurate_required": "selection",
    "insufficient_evidence": "insufficient",
    "not_run": None,
}
SHADOW_RUNS = 3


def load_manifest(path=DEFAULT_MANIFEST) -> dict:
    path = Path(path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    ids = [case.get("id") for case in cases]
    if manifest.get("case_count") != 53 or len(ids) != 53:
        raise ValueError("frozen replay manifest must contain exactly 53 cases")
    if len(set(ids)) != len(ids):
        raise ValueError("frozen replay manifest contains duplicate case IDs")
    if any(not isinstance(case_id, str) or len(case_id) != 4 or not case_id.isdigit()
           for case_id in ids):
        raise ValueError("every replay case ID must be a four-digit string")
    templates = manifest.get("artifact_templates")
    if not isinstance(templates, dict) or not templates:
        raise ValueError("replay manifest has no artifact templates")
    required = {
        "expected_primary_ownership",
        "expected_disposition",
        "expected_cache_policy",
    }
    ownership_contract = manifest.get("ownership_contract")
    if not isinstance(ownership_contract, dict):
        raise ValueError("replay manifest has no ownership contract")
    operational_rules = ownership_contract.get("operational_by_disposition")
    if (
        not isinstance(operational_rules, dict)
        or operational_rules != OPERATIONAL_OWNERSHIP_BY_DISPOSITION
    ):
        raise ValueError(
            "replay manifest operational ownership rules are incompatible"
        )
    if (
        ownership_contract.get("forensic_field")
        != "expected_primary_ownership"
        or ownership_contract.get("operational_field")
        != "expected_operational_ownership"
        or any(
            owner not in diagnostics.OPERATIONAL_OWNERS | {"forensic"}
            for owner in operational_rules.values()
        )
    ):
        raise ValueError("replay manifest ownership contract is invalid")
    for case in cases:
        missing = sorted(required - set(case))
        if missing:
            raise ValueError(
                f"replay case {case['id']} is missing expectations: {missing}"
            )
        if case["expected_primary_ownership"] not in diagnostics.PRIMARY_OWNERS:
            raise ValueError(
                f"replay case {case['id']} has invalid primary ownership"
            )
        if case["expected_disposition"] not in diagnostics.DISPOSITIONS:
            raise ValueError(f"replay case {case['id']} has invalid disposition")
        if case["expected_cache_policy"] not in diagnostics.CACHE_POLICIES:
            raise ValueError(f"replay case {case['id']} has invalid cache policy")
        safe_policies = SAFE_CACHE_POLICIES[case["expected_disposition"]]
        if case["expected_cache_policy"] not in safe_policies:
            raise ValueError(
                f"replay case {case['id']} has unsafe disposition/cache policy"
            )
        operational = _expected_operational_ownership(manifest, case)
        if operational not in diagnostics.OPERATIONAL_OWNERS:
            raise ValueError(
                f"replay case {case['id']} has invalid operational ownership"
            )

    declared_counts = manifest.get("expected_primary_counts")
    if declared_counts != EXPECTED_PRIMARY_COUNTS:
        raise ValueError("replay manifest primary counts do not match frozen totals")
    actual_counts = Counter(
        case["expected_primary_ownership"] for case in cases
    )
    actual_counts.update({
        owner: 0 for owner in EXPECTED_PRIMARY_COUNTS
    })
    if dict(actual_counts) != EXPECTED_PRIMARY_COUNTS:
        raise ValueError("replay case ownership does not match frozen totals")
    return manifest


def _case_artifacts(manifest, case_id, root):
    root = Path(root)
    return {
        name: root / template.format(id=case_id)
        for name, template in sorted(manifest["artifact_templates"].items())
    }


def _expected_operational_ownership(manifest, case):
    rule = manifest["ownership_contract"]["operational_by_disposition"][
        case["expected_disposition"]
    ]
    return case["expected_primary_ownership"] if rule == "forensic" else rule


def _expected_diagnostics(manifest, case):
    return {
        "forensic_primary_ownership": case["expected_primary_ownership"],
        "operational_ownership":
            _expected_operational_ownership(manifest, case),
        "disposition": case["expected_disposition"],
        "cache_policy": case["expected_cache_policy"],
    }


def _actual_diagnostics(manifest, verification):
    forensic = verification.get(
        "forensic_primary_ownership",
        verification.get("primary_ownership"),
    )
    operational = verification.get("operational_ownership")
    if operational is None and verification.get("disposition") in diagnostics.DISPOSITIONS:
        rule = manifest["ownership_contract"]["operational_by_disposition"][
            verification["disposition"]
        ]
        operational = forensic if rule == "forensic" else rule
    return {
        "forensic_primary_ownership": forensic,
        "operational_ownership": operational,
        "disposition": verification.get("disposition"),
        "cache_policy": verification.get("cache_policy"),
    }


def _compare_diagnostics(manifest, case, verification):
    expected = _expected_diagnostics(manifest, case)
    actual = _actual_diagnostics(manifest, verification)
    fields = {
        name: {
            "expected": expected[name],
            "actual": actual[name],
            "matches": expected[name] == actual[name],
        }
        for name in (
            "forensic_primary_ownership",
            "operational_ownership",
            "disposition",
            "cache_policy",
        )
    }
    return {
        "all_match": all(record["matches"] for record in fields.values()),
        "fields": fields,
    }


def _hash_json(value) -> str:
    return hashlib.sha256(
        diagnostics.canonical_json(value).encode("utf-8")
    ).hexdigest()


def _semantic_verification(verification):
    """Stable verifier meaning, excluding host paths and performance fields."""
    return {
        key: verification.get(key)
        for key in (
            "schema_version",
            "status",
            "disposition",
            "primary_ownership",
            "forensic_primary_ownership",
            "operational_ownership",
            "cache_policy",
            "cache_policy_by_reason",
            "blocking_reasons",
            "blocking_reason_details",
            "counts",
            "samples",
            "provenance",
            "generation_id",
        )
    }


def _percentile(values, percentile):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    rank = max(0, math.ceil(percentile * len(values)) - 1)
    return values[rank]


def _benchmark_summary(measurements):
    runtimes = [
        item["verifier_runtime_s"]
        for item in measurements
        if isinstance(item.get("verifier_runtime_s"), (int, float))
    ]
    peaks = [
        item["peak_memory_bytes"]
        for item in measurements
        if isinstance(item.get("peak_memory_bytes"), (int, float))
    ]
    return {
        "samples": len(measurements),
        "verifier_runtime_p95_s": _percentile(runtimes, 0.95),
        "peak_memory_bytes": max(peaks) if peaks else None,
    }


def check_rollout_performance(report, baseline) -> dict:
    """Apply rollout-only budgets; ordinary unit runs never call this implicitly."""
    current = report.get("benchmark") or {}
    reference = baseline.get("benchmark") or baseline
    current_runtime = current.get("verifier_runtime_p95_s")
    baseline_runtime = reference.get("verifier_runtime_p95_s")
    current_peak = current.get("peak_memory_bytes")
    baseline_peak = reference.get("peak_memory_bytes")
    missing = [
        name
        for name, value in {
            "current.verifier_runtime_p95_s": current_runtime,
            "baseline.verifier_runtime_p95_s": baseline_runtime,
            "current.peak_memory_bytes": current_peak,
            "baseline.peak_memory_bytes": baseline_peak,
        }.items()
        if not isinstance(value, (int, float))
    ]
    if missing:
        return {
            "passed": False,
            "missing_metrics": missing,
            "runtime_limit_ratio": 1.25,
            "peak_memory_limit_ratio": 1.20,
        }
    runtime_ratio = current_runtime / baseline_runtime if baseline_runtime else math.inf
    memory_ratio = current_peak / baseline_peak if baseline_peak else math.inf
    return {
        "passed": runtime_ratio <= 1.25 and memory_ratio <= 1.20,
        "runtime_ratio": runtime_ratio,
        "peak_memory_ratio": memory_ratio,
        "runtime_limit_ratio": 1.25,
        "peak_memory_limit_ratio": 1.20,
    }


def _read_execution_verification(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, {
            "state": "invalid",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        return None, {
            "state": "invalid",
            "error_type": "MissingVerification",
            "message": "execution artifact has no verification object",
        }
    return verification, None


def build_replay_report(manifest_path=DEFAULT_MANIFEST, root=DEFAULT_ROOT) -> dict:
    """Produce a deterministic evidence report without mutating any artifact."""
    manifest_path = Path(manifest_path)
    manifest = load_manifest(manifest_path)
    cases = []
    ready = 0
    complete = 0
    execution_artifacts = 0
    execution_matches = 0
    required_inputs = {"source_workbook", "ast_nodes", "ast_edges", "curation"}
    for case in manifest["cases"]:
        case_id = case["id"]
        artifacts = _case_artifacts(manifest, case_id, root)
        records = {
            name: diagnostics.fingerprint_file(path)
            for name, path in artifacts.items()
        }
        missing = sorted(
            name for name, record in records.items()
            if not record["available"]
        )
        missing_inputs = sorted(required_inputs.intersection(missing))
        state = "ready" if not missing_inputs else "missing_artifacts"
        ready += state == "ready"
        complete += not missing
        case_report = {
            "id": case_id,
            "state": state,
            "expected": _expected_diagnostics(manifest, case),
            "missing_artifacts": missing,
            "missing_required_inputs": missing_inputs,
            "artifacts": records,
        }
        if records.get("segments", {}).get("available"):
            execution_artifacts += 1
            verification, error = _read_execution_verification(
                artifacts["segments"]
            )
            if error is not None:
                case_report["execution_diagnostics"] = error
            else:
                comparison = _compare_diagnostics(manifest, case, verification)
                execution_matches += comparison["all_match"]
                case_report["execution_diagnostics"] = {
                    "state": "available",
                    "actual": _actual_diagnostics(manifest, verification),
                    "comparison": comparison,
                }
        cases.append(case_report)
    return {
        "schema_version": diagnostics.REPLAY_SCHEMA_VERSION,
        "manifest": diagnostics.fingerprint_file(manifest_path),
        "case_count": len(cases),
        "counts": {
            "ready": ready,
            "complete_evidence": complete,
            "missing_required_inputs": len(cases) - ready,
            "missing_any_artifact": len(cases) - complete,
            "execution_artifacts": execution_artifacts,
            "execution_diagnostics_match": execution_matches,
        },
        "cases": cases,
    }


def execute_shadow_replay(
    manifest_path=DEFAULT_MANIFEST,
    root=DEFAULT_ROOT,
    shadow_root=None,
) -> dict:
    """Run three isolated generations per ready case beneath ``shadow_root``."""
    if shadow_root is None:
        raise ValueError("shadow_root is required for execution")
    manifest = load_manifest(manifest_path)
    validation = build_replay_report(manifest_path, root)
    manifest_cases = {case["id"]: case for case in manifest["cases"]}
    shadow_root = Path(shadow_root)
    if shadow_root.is_symlink():
        raise ValueError("shadow_root must not be a symlink")
    results = []
    benchmark_measurements = []

    from xl_segment import segment
    from xl_seg.publication import (
        GenerationValidationError,
        validate_generation_directory,
    )

    for case_record in validation["cases"]:
        case_id = case_record["id"]
        if case_record["state"] != "ready":
            results.append({
                "id": case_id,
                "state": "not_run",
                "reason": "missing_artifacts",
                "missing_artifacts": case_record["missing_artifacts"],
            })
            continue
        artifacts = _case_artifacts(manifest, case_id, root)
        source = artifacts["source_workbook"]
        nodes = artifacts["ast_nodes"]
        curation = artifacts["curation"]
        resolved_shadow = shadow_root.resolve()
        protected_dirs = {
            source.parent.resolve(),
            curation.parent.resolve(),
            nodes.parent.resolve(),
        }
        if any(
            resolved_shadow == protected
            or resolved_shadow.is_relative_to(protected)
            or protected.is_relative_to(resolved_shadow)
            for protected in protected_dirs
        ):
            raise ValueError(
                "shadow_root must be disjoint from source, AST, and curation trees"
            )
        if (
            source.name != f"{case_id}.xlsx"
            or nodes.parent.name != case_id
        ):
            results.append({
                "id": case_id,
                "state": "not_run",
                "reason": "unsupported_declarative_layout",
            })
            continue
        preserved_names = (
            "source_workbook",
            "ast_nodes",
            "ast_edges",
            "curation",
        )
        before = {
            name: diagnostics.fingerprint_file(artifacts[name])
            for name in preserved_names
        }
        runs = []
        try:
            for run_index in range(1, SHADOW_RUNS + 1):
                run_root = shadow_root / f"run-{run_index}"
                output_case = run_root / case_id
                if output_case.exists():
                    raise ValueError(
                        f"isolated shadow output already exists: {output_case}"
                    )
                output_case.mkdir(parents=True)
                shutil.copyfile(curation, output_case / "curation.toml")
                args = SimpleNamespace(
                    ast_dir=str(nodes.parent.parent),
                    source=str(source.parent),
                    out=str(run_root),
                    threshold=6.0,
                    top=40,
                    sample=400,
                    lineage_max=4000,
                    recurate=False,
                    llm=False,
                    model="",
                    env_file="__shadow_replay_never_reads_env__",
                    no_verify=False,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    payload = segment(case_id, args)
                generation_dir = Path(
                    (payload.get("generation") or {}).get("directory", "")
                )
                generation_manifest = validate_generation_directory(
                    generation_dir,
                    require_pass=False,
                )
                generation_payload = json.loads(
                    (generation_dir / "segments.json").read_text(encoding="utf-8")
                )
                verification = generation_payload["verification"]
                if (
                    (payload.get("verification") or {}).get("generation_id")
                    != verification.get("generation_id")
                ):
                    raise ValueError(
                        "returned verification disagrees with published generation"
                    )
                comparison = _compare_diagnostics(
                    manifest, manifest_cases[case_id], verification
                )
                proof = generation_payload.get("proof")
                semantic_hash = _hash_json(
                    _semantic_verification(verification)
                )
                proof_hash = _hash_json(proof)
                transitions = [
                    {
                        "field": field,
                        "expected": record["expected"],
                        "actual": record["actual"],
                        "changed": not record["matches"],
                    }
                    for field, record in comparison["fields"].items()
                ]
                benchmark = payload.get("benchmark") or {}
                benchmark_measurements.append(benchmark)
                runs.append({
                    "run": run_index,
                    "state": "completed",
                    "verification_status": verification.get("status"),
                    "primary_ownership": verification.get("primary_ownership"),
                    "forensic_primary_ownership":
                        verification.get("forensic_primary_ownership"),
                    "operational_ownership":
                        verification.get("operational_ownership"),
                    "disposition": verification.get("disposition"),
                    "cache_policy": verification.get("cache_policy"),
                    "blocking_reasons":
                        verification.get("blocking_reasons", []),
                    "generation_id": verification.get("generation_id", ""),
                    "generation_manifest_id":
                        generation_manifest.get("generation_id"),
                    "generation_dir": str(generation_dir),
                    "semantic_classification_sha256": semantic_hash,
                    "proof_sha256": proof_hash,
                    "comparison": comparison,
                    "verdict_transitions": transitions,
                    "benchmark": benchmark,
                })
        except (
            GenerationValidationError,
            OSError,
            ValueError,
            SystemExit,
        ) as exc:
            after = {
                name: diagnostics.fingerprint_file(artifacts[name])
                for name in preserved_names
            }
            mutations = [
                name for name in preserved_names if before[name] != after[name]
            ]
            results.append({
                "id": case_id,
                "state": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "runs": runs,
                "preserved_inputs": not mutations,
                "mutated_artifacts": mutations,
            })
            continue

        after = {
            name: diagnostics.fingerprint_file(artifacts[name])
            for name in preserved_names
        }
        mutations = [
            name for name in preserved_names if before[name] != after[name]
        ]
        semantic_hashes = {
            run["semantic_classification_sha256"] for run in runs
        }
        proof_hashes = {run["proof_sha256"] for run in runs}
        generation_ids = {run["generation_id"] for run in runs}
        manifest_ids = {run["generation_manifest_id"] for run in runs}
        comparisons_match = (
            len(runs) == SHADOW_RUNS
            and all(run["comparison"]["all_match"] for run in runs)
        )
        generation_ids_match = (
            len(generation_ids) == 1
            and len(manifest_ids) == 1
            and all(
                run["generation_id"] == run["generation_manifest_id"]
                for run in runs
            )
        )
        deterministic = (
            len(runs) == SHADOW_RUNS
            and len(semantic_hashes) == 1
            and len(proof_hashes) == 1
            and generation_ids_match
        )
        results.append({
            "id": case_id,
            "state": (
                "completed"
                if deterministic and not mutations and comparisons_match
                else "failed"
            ),
            "expected": _expected_diagnostics(
                manifest, manifest_cases[case_id]
            ),
            "runs": runs,
            "deterministic": deterministic,
            "expected_diagnostics_match": comparisons_match,
            "preserved_inputs": not mutations,
            "mutated_artifacts": mutations,
            "verdict_transitions": [
                {
                    "run": run["run"],
                    **transition,
                }
                for run in runs
                for transition in run["verdict_transitions"]
            ],
        })

    return {
        "schema_version": diagnostics.REPLAY_SCHEMA_VERSION,
        "mode": "execute",
        "validation": validation["counts"],
        "case_count": len(results),
        "shadow_runs_per_ready_case": SHADOW_RUNS,
        "benchmark": _benchmark_summary(benchmark_measurements),
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--shadow-root")
    parser.add_argument(
        "--baseline-report",
        help="optional replay report whose p95 runtime and peak memory set rollout budgets",
    )
    args = parser.parse_args(argv)
    if args.execute:
        report = execute_shadow_replay(
            args.manifest, args.root, args.shadow_root
        )
    else:
        report = build_replay_report(args.manifest, args.root)
    if args.baseline_report:
        baseline = json.loads(
            Path(args.baseline_report).read_text(encoding="utf-8")
        )
        report["rollout_performance_gate"] = check_rollout_performance(
            report,
            baseline,
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.execute and any(
        result.get("state") != "completed"
        for result in report.get("results", [])
    ):
        return 1
    if (
        args.baseline_report
        and report["rollout_performance_gate"].get("passed") is not True
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
