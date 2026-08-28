"""Versioned, deterministic diagnostics for segmentation verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCHEMA_VERSION = "segmentation-verification/v2"
REPLAY_SCHEMA_VERSION = "segmentation-shadow-replay/v2"
DEFAULT_SAMPLE_LIMIT = 20

DISPOSITIONS = frozenset({
    "pass",
    "code_fix_required",
    "quarantine",
    "recalc_required",
    "recurate_required",
    "insufficient_evidence",
    "not_run",
})
CACHE_POLICIES = frozenset({
    "retain_as_oracle",
    "refresh_after_authoritative_recalc",
    "preserve_ignore_as_authority",
    "preserve_pending_diagnostics",
    "not_applicable",
})
PRIMARY_OWNERS = frozenset({
    "evaluator",
    "AST/parser",
    "frontier",
    "source/cache",
    "selection",
    "insufficient",
})
OPERATIONAL_OWNERS = PRIMARY_OWNERS | {None}

_SOURCE_QUARANTINE_REASONS = (
    "formula_free_output_model",
    "source_structurally_broken",
    "active_cycle_demonstrated_non_unique",
)
_SOURCE_RECALC_REASONS = (
    "source_cache_proven_stale",
    "source_recalculation_required",
    "active_cycle_diagnostics_missing",
    "active_cycle_iteration_status_unknown",
    "active_cycle_iteration_count_unknown",
    "active_cycle_iteration_delta_unknown",
    "active_cycle_iteration_disabled",
    "active_cycle_non_converged",
    "active_cycle_iteration_budget_exhausted",
    "active_cycle_residual_unavailable",
    "active_cycle_residual_above_delta",
    "active_cycle_topology_not_stabilized",
    "active_cycle_targets_not_stabilized",
)
_FRONTIER_REASONS = frozenset({
    "seeded_inside_output_cone",
    "proof_expected_cache_read",
    "runtime_dependency_closure_not_stabilized",
    "proof_benchmark_limits_exceeded",
})
_REASON_ORDER = {
    code: index
    for index, code in enumerate((
        "formula_free_output_model",
        "source_structurally_broken",
        "active_cycle_demonstrated_non_unique",
        "active_cycle_uncertified",
        "source_cache_proven_stale",
        "source_recalculation_required",
        "active_cycle_diagnostics_missing",
        "active_cycle_iteration_status_unknown",
        "active_cycle_iteration_count_unknown",
        "active_cycle_iteration_delta_unknown",
        "active_cycle_iteration_disabled",
        "active_cycle_non_converged",
        "active_cycle_iteration_budget_exhausted",
        "active_cycle_residual_unavailable",
        "active_cycle_residual_above_delta",
        "active_cycle_topology_not_stabilized",
        "active_cycle_targets_not_stabilized",
        "explicit_recuration_required",
        "missing_required_proof_evidence",
        "missing_evidence",
        "output_mismatch",
        "output_unresolved",
        "output_unverifiable",
        "seeded_inside_output_cone",
        "proof_expected_cache_read",
        "runtime_dependency_closure_not_stabilized",
        "proof_benchmark_limits_exceeded",
        "active_cycle_errors",
        "active_cycle_unresolved",
        "active_cycle_endpoint_equation_unverified",
        "verification_disabled",
    ))
}


def _positive_reason_counts(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {
            str(code): int(count)
            for code, count in raw.items()
            if isinstance(count, (int, float)) and count > 0
        }
    return {str(code): 1 for code in raw}


def _ordered_reason_codes(reason_counts) -> list:
    return sorted(
        reason_counts,
        key=lambda code: (_REASON_ORDER.get(code, len(_REASON_ORDER)), code),
    )


def _explicit_recuration(evidence) -> bool:
    """Require an affirmative, scoped record; never infer recuration."""
    if not isinstance(evidence, dict):
        return False
    scope = evidence.get("scope")
    supporting_evidence = evidence.get("evidence")
    return (
        evidence.get("decision") == "recurate"
        and bool(scope)
        and bool(supporting_evidence)
        and evidence.get("prerequisites_satisfied") is True
    )


def _cache_policy_for_reason(code) -> str:
    if code == "formula_free_output_model":
        return "not_applicable"
    if code in {
        "source_structurally_broken",
        "active_cycle_demonstrated_non_unique",
    }:
        return "preserve_ignore_as_authority"
    if code in _SOURCE_RECALC_REASONS:
        return "refresh_after_authoritative_recalc"
    if code in {
        "missing_required_proof_evidence",
        "missing_evidence",
        "verification_disabled",
    }:
        return "preserve_pending_diagnostics"
    return "retain_as_oracle"


def _forensic_owner(
    *,
    evidence,
    reason_set,
    operational_owner,
    executed,
) -> str | None:
    """Attribute root cause independently from the operational disposition."""
    explicit = evidence.get("forensic_primary_ownership")
    if explicit in PRIMARY_OWNERS:
        return explicit
    if not executed:
        return None
    if reason_set.intersection({
        "missing_required_proof_evidence",
        "missing_evidence",
    }):
        return "insufficient"
    if any(code.startswith(("ast_", "parser_")) for code in reason_set):
        return "AST/parser"
    if reason_set.intersection(_FRONTIER_REASONS):
        return "frontier"
    if any(code.startswith("output_") for code in reason_set) or reason_set.intersection({
        "active_cycle_errors",
        "active_cycle_unresolved",
        "active_cycle_endpoint_equation_unverified",
    }):
        return "evaluator"
    return operational_owner


def classify_disposition(evidence) -> dict:
    """Classify verification evidence without workbook-specific control flow.

    ``source`` may affirm ``formula_free``, ``structurally_broken``,
    ``demonstrated_non_unique``, ``proven_stale_cache``, or ``recalc_required``.
    ``recuration`` must contain an explicit decision, scope, supporting evidence,
    and a true ``prerequisites_satisfied`` flag. Merely observing a suspicious
    selection can therefore never trigger recuration.
    """
    evidence = dict(evidence or {})
    source = evidence.get("source") or {}
    reasons = _positive_reason_counts(evidence.get("reason_counts"))

    if source.get("formula_free") or evidence.get("formula_count") == 0:
        reasons["formula_free_output_model"] = max(
            reasons.get("formula_free_output_model", 0), 1
        )
    if source.get("structurally_broken"):
        reasons["source_structurally_broken"] = max(
            reasons.get("source_structurally_broken", 0), 1
        )
    if source.get("demonstrated_non_unique"):
        reasons["active_cycle_demonstrated_non_unique"] = max(
            reasons.get("active_cycle_demonstrated_non_unique", 0), 1
        )
    if source.get("proven_stale_cache"):
        reasons["source_cache_proven_stale"] = max(
            reasons.get("source_cache_proven_stale", 0), 1
        )
    if source.get("recalc_required"):
        reasons["source_recalculation_required"] = max(
            reasons.get("source_recalculation_required", 0), 1
        )

    missing = evidence.get("missing_evidence")
    if missing:
        reasons["missing_required_proof_evidence"] = (
            len(missing) if isinstance(missing, (list, tuple, set, dict)) else 1
        )

    recuration = _explicit_recuration(evidence.get("recuration"))
    if recuration:
        reasons["explicit_recuration_required"] = 1

    executed = evidence.get("executed", True)
    ordered = _ordered_reason_codes(reasons)
    reason_set = set(ordered)
    quarantine_reasons = reason_set.intersection(_SOURCE_QUARANTINE_REASONS)
    recalc_reasons = reason_set.intersection(_SOURCE_RECALC_REASONS)
    missing_reasons = reason_set.intersection({
        "missing_required_proof_evidence",
        "missing_evidence",
    })

    if not executed:
        disposition = "not_run"
        operational_owner = None
    elif quarantine_reasons:
        disposition = "quarantine"
        operational_owner = "source/cache"
    elif recalc_reasons:
        disposition = "recalc_required"
        operational_owner = "source/cache"
    elif missing_reasons:
        disposition = "insufficient_evidence"
        operational_owner = "insufficient"
    elif recuration:
        disposition = "recurate_required"
        operational_owner = "selection"
    elif ordered:
        disposition = "code_fix_required"
        if any(
            code.startswith(("ast_", "parser_"))
            for code in ordered
        ):
            operational_owner = "AST/parser"
        elif reason_set.intersection(_FRONTIER_REASONS):
            operational_owner = "frontier"
        else:
            operational_owner = "evaluator"
    elif evidence.get("proof_clean") is True:
        disposition = "pass"
        operational_owner = None
    else:
        disposition = "insufficient_evidence"
        operational_owner = "insufficient"
        reasons["missing_required_proof_evidence"] = 1
        ordered = _ordered_reason_codes(reasons)

    if disposition == "quarantine":
        cache_policy = (
            "not_applicable"
            if "formula_free_output_model" in reason_set
            else "preserve_ignore_as_authority"
        )
    elif disposition == "recalc_required":
        cache_policy = "refresh_after_authoritative_recalc"
    elif disposition in {"insufficient_evidence", "not_run"}:
        cache_policy = "preserve_pending_diagnostics"
    else:
        cache_policy = "retain_as_oracle"

    forensic_owner = _forensic_owner(
        evidence=evidence,
        reason_set=set(ordered),
        operational_owner=operational_owner,
        executed=executed,
    )
    return {
        "disposition": disposition,
        # ``primary_ownership`` remains the compatibility name for forensic
        # root-cause ownership. Operational remediation ownership is separate.
        "primary_ownership": forensic_owner,
        "forensic_primary_ownership": forensic_owner,
        "operational_ownership": operational_owner,
        "reason_codes": ordered,
        "reason_details": [
            {"code": code, "count": reasons[code]}
            for code in ordered
        ],
        "cache_policy": cache_policy,
        "cache_policy_by_reason": [
            {"reason_code": code, "cache_policy": _cache_policy_for_reason(code)}
            for code in ordered
        ],
    }


def canonical_json(value) -> str:
    """Serialize diagnostic data identically across runs."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_bytes(content: bytes) -> dict:
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def fingerprint_file(path, *, logical_path=None) -> dict:
    """Fingerprint a file when it exists without inventing missing evidence."""
    path = Path(path)
    record = {
        "path": str(logical_path) if logical_path is not None else str(path),
        "available": path.is_file(),
    }
    if record["available"]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        record.update({
            "algorithm": "sha256",
            "sha256": digest.hexdigest(),
            "size_bytes": size,
        })
    return record


def fingerprint_values(values) -> dict:
    """Fingerprint an ordered logical value set using canonical JSON."""
    ordered = list(values)
    content = canonical_json(ordered).encode("utf-8")
    return {
        "available": True,
        "encoding": "canonical-json",
        "count": len(ordered),
        **fingerprint_bytes(content),
    }


def generation_id(fingerprints: dict) -> str:
    """Content-derived generation identity; never time- or host-dependent."""
    bound = {
        name: record.get("sha256")
        for name, record in sorted(fingerprints.items())
        if record.get("available") and record.get("sha256")
    }
    if not bound:
        return ""
    return hashlib.sha256(canonical_json(bound).encode("utf-8")).hexdigest()


def evidence_fingerprints(
    *,
    source_path,
    ast_dir,
    curation_path,
    output_cells,
    verifier_paths=(),
) -> tuple[dict, list]:
    """Return fingerprints plus explicit records for every missing artifact."""
    ast_dir = Path(ast_dir)
    files = {
        "source_workbook": source_path,
        "ast_nodes": ast_dir / "nodes.csv",
        "ast_edges": ast_dir / "edges.csv",
        "curation": curation_path,
    }
    logical_paths = {
        "source_workbook": Path(source_path).name,
        "ast_nodes": str(Path(ast_dir).name + "/nodes.csv"),
        "ast_edges": str(Path(ast_dir).name + "/edges.csv"),
        "curation": str(Path(curation_path).parent.name + "/curation.toml"),
    }
    fingerprints = {
        name: fingerprint_file(path, logical_path=logical_paths[name])
        for name, path in files.items()
    }
    # Ordering is part of curated output identity. Callers that only have an
    # unordered legacy set must sort it before crossing this contract.
    fingerprints["selected_output_cells"] = fingerprint_values(output_cells)

    verifier_records = [
        fingerprint_file(path, logical_path=Path(path).name)
        for path in verifier_paths
    ]
    available_verifiers = [
        record for record in verifier_records if record.get("available")
    ]
    if available_verifiers:
        fingerprints["verifier_code"] = fingerprint_values(
            [
                {
                    "path": record["path"],
                    "sha256": record["sha256"],
                    "size_bytes": record["size_bytes"],
                }
                for record in available_verifiers
            ]
        )
    else:
        fingerprints["verifier_code"] = {
            "available": False,
            "paths": [record["path"] for record in verifier_records],
        }

    missing = [
        {
            "kind": name,
            "path": record["path"],
            "reason": "file_not_found",
        }
        for name, record in fingerprints.items()
        if name in files and not record.get("available")
    ]
    if not fingerprints["verifier_code"].get("available"):
        missing.append({
            "kind": "verifier_code",
            "paths": fingerprints["verifier_code"]["paths"],
            "reason": "file_not_found",
        })
    return fingerprints, missing


def bounded_sample(values, limit=DEFAULT_SAMPLE_LIMIT, *, key=None) -> list:
    """Sort before truncating so samples are stable and never order-accidental."""
    ordered = sorted(values, key=key)
    return ordered[: max(int(limit), 0)]
