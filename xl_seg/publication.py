"""Atomic, immutable publication and strict reading of segmentation generations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

from . import diagnostics, restriction_cone


MANIFEST_SCHEMA_VERSION = "segmentation-generation/v2"
LEGACY_MANIFEST_SCHEMA_VERSION = "segmentation-generation/v1"
POINTER_SCHEMA_VERSION = "segmentation-current/v1"
INPUTS_POINTER_SCHEMA_VERSION = "segmentation-inputs/v1"
SUPPORTED_VERIFICATION_SCHEMAS = frozenset({diagnostics.SCHEMA_VERSION})
GENERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_GENERATED_ARTIFACTS = frozenset({
    "bands.csv",
    "segments.json",
    "lineage.json",
    "output_candidates.csv",
})
LEGACY_ARTIFACTS = (
    "bands.csv",
    "segments.json",
    "lineage.json",
    "output_candidates.csv",
    "lineage",
)


class GenerationValidationError(ValueError):
    """The selected generation is absent, mixed, incomplete, or tampered."""


def _canonical_bytes(value) -> bytes:
    return (json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def inputs_sidecar_path(inputs_path) -> Path:
    path = Path(inputs_path)
    return path.with_name(f"{path.stem}.segmentation.json")


def write_inputs_sidecar(
    inputs_path,
    generation_dir,
    manifest: dict,
    *,
    pipeline_bindings: dict | None = None,
) -> Path:
    """Bind one generated inputs workbook to one immutable segmentation."""
    inputs_path = Path(inputs_path)
    generation_dir = Path(generation_dir)
    generation_id = manifest.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise GenerationValidationError("cannot bind inputs to an invalid generation")
    sidecar = {
        "schema_version": INPUTS_POINTER_SCHEMA_VERSION,
        "generation_id": generation_id,
        "inputs_file": inputs_path.name,
        "inputs_sha256": _sha256(inputs_path),
        "generation_manifest_sha256":
            _sha256(generation_dir / "generation-manifest.json"),
    }
    if pipeline_bindings is not None:
        if (
            not isinstance(pipeline_bindings, dict)
            or pipeline_bindings.get("segmentation_generation_id")
            != generation_id
        ):
            raise GenerationValidationError(
                "inputs pipeline bindings do not match segmentation generation"
            )
        sidecar["pipeline_bindings"] = pipeline_bindings
    path = inputs_sidecar_path(inputs_path)
    _atomic_write(path, _canonical_bytes(sidecar))
    return path


def validate_inputs_sidecar(
    inputs_path,
    *,
    expected_generation_id,
    generation_dir=None,
    expected_pipeline_bindings=None,
) -> dict:
    """Reject stale, copied, or cross-generation input artifacts."""
    inputs_path = Path(inputs_path)
    sidecar = _read_json(
        inputs_sidecar_path(inputs_path),
        "inputs segmentation sidecar",
    )
    failures = []
    if sidecar.get("schema_version") != INPUTS_POINTER_SCHEMA_VERSION:
        failures.append("schema_version")
    if sidecar.get("generation_id") != expected_generation_id:
        failures.append("generation_id")
    if sidecar.get("inputs_file") != inputs_path.name:
        failures.append("inputs_file")
    if not inputs_path.is_file() or inputs_path.is_symlink():
        failures.append("inputs_workbook")
    elif sidecar.get("inputs_sha256") != _sha256(inputs_path):
        failures.append("inputs_sha256")
    if generation_dir is not None:
        manifest_path = Path(generation_dir) / "generation-manifest.json"
        if sidecar.get("generation_manifest_sha256") != _sha256(manifest_path):
            failures.append("generation_manifest_sha256")
    if (
        expected_pipeline_bindings is not None
        and sidecar.get("pipeline_bindings") != expected_pipeline_bindings
    ):
        failures.append("pipeline_bindings")
    if failures:
        raise GenerationValidationError(
            "inputs segmentation sidecar validation failed: "
            + ", ".join(failures)
        )
    return sidecar


def make_staging_directory(seg_root, workbook: str) -> Path:
    """Create a same-filesystem sibling stage, never inside the live case."""
    root = Path(seg_root)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{workbook}.seg-stage-", dir=str(root)))


def verifier_paths() -> tuple[Path, ...]:
    """Code files whose exact bytes define the verification implementation."""
    package = Path(__file__).resolve().parent
    return (
        package.parent / "xl_segment.py",
        package / "evaluate.py",
        package / "diagnostics.py",
        package / "proof.py",
        package / "restriction_cone.py",
        Path(__file__).resolve(),
    )


def evidence_fingerprints(source_path, ast_dir, curation_path, curated_cells):
    return diagnostics.evidence_fingerprints(
        source_path=source_path,
        ast_dir=ast_dir,
        curation_path=curation_path,
        output_cells=curated_cells,
        verifier_paths=verifier_paths(),
    )


def attach_generation_contract(verification: dict) -> None:
    generation_id = verification.get("generation_id")
    if generation_id:
        verification["generation_manifest"] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generation_id": generation_id,
            "path": "generation-manifest.json",
        }


def _value_fingerprint(value: object, logical_path: str) -> dict:
    content = _canonical_bytes(value)
    return {
        "path": logical_path,
        "available": True,
        "algorithm": "sha256",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def bind_source_generation(
    verification: dict,
    source_manifest: dict,
    *,
    certificate: dict | None = None,
) -> None:
    """Add exact source-policy bindings before deriving segmentation identity."""
    if source_manifest.get("schema_version") != "source-generation/v2":
        raise GenerationValidationError(
            "new segmentation source bindings require source-generation/v2"
        )
    fingerprints = verification.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise GenerationValidationError("verification has no evidence fingerprints")
    identity = source_manifest.get("identity") or {}
    policy = identity.get("policy_decision") or {}
    bindings = source_manifest.get("bindings") or {}
    source_binding = {
        "generation_id": source_manifest.get("generation_id"),
        "source_sha256": bindings.get("source_sha256"),
        "health_report_sha256": bindings.get("health_report_sha256"),
        "policy_version": identity.get("policy_version"),
        "route": policy.get("route"),
    }
    if (
        not GENERATION_ID_RE.fullmatch(str(source_binding["generation_id"]))
        or not GENERATION_ID_RE.fullmatch(str(source_binding["source_sha256"]))
        or not GENERATION_ID_RE.fullmatch(
            str(source_binding["health_report_sha256"])
        )
        or source_binding["route"] not in {
            "pass", "recalc_candidate", "restricted_pass"
        }
        or not isinstance(source_binding["policy_version"], str)
    ):
        raise GenerationValidationError("source generation bindings are incomplete")
    fingerprints["source_generation"] = _value_fingerprint(
        source_binding, "source-generation"
    )
    verification["source_generation"] = source_binding
    source_policy_bindings = {"source_generation": source_binding}
    restricted = policy.get("route") == "restricted_pass"
    if restricted:
        if not isinstance(certificate, dict):
            raise GenerationValidationError(
                "restricted segmentation requires a cone certificate"
            )
        required_hashes = (
            bindings.get("restriction_evidence_sha256"),
            bindings.get("restriction_events_sha256"),
            bindings.get("restriction_profile_sha256"),
            certificate.get("certificate_sha256"),
        )
        if (
            any(
                not isinstance(value, str)
                or not GENERATION_ID_RE.fullmatch(value)
                for value in required_hashes
            )
            or not isinstance(policy.get("restriction_profile"), dict)
        ):
            raise GenerationValidationError(
                "restricted source policy bindings are incomplete"
            )
        restriction_binding = {
            "restriction_evidence_sha256": bindings.get(
                "restriction_evidence_sha256"
            ),
            "restriction_events_sha256": bindings.get(
                "restriction_events_sha256"
            ),
        }
        profile_binding = {
            "restriction_profile": policy.get("restriction_profile"),
            "restriction_profile_sha256": bindings.get(
                "restriction_profile_sha256"
            ),
        }
        fingerprints["source_restriction_evidence"] = _value_fingerprint(
            restriction_binding, "source-restriction-evidence"
        )
        fingerprints["source_restriction_profile"] = _value_fingerprint(
            profile_binding, "source-restriction-profile"
        )
        fingerprints["restriction_cone_certificate"] = _value_fingerprint(
            certificate, "restriction-cone-certificate.json"
        )
        verification["restriction_cone"] = {
            "schema_version": restriction_cone.SCHEMA_VERSION,
            "certificate_sha256": certificate.get("certificate_sha256"),
            "artifact_sha256": fingerprints[
                "restriction_cone_certificate"
            ]["sha256"],
        }
        source_policy_bindings.update({
            "restriction_evidence_sha256": bindings.get(
                "restriction_evidence_sha256"
            ),
            "restriction_events_sha256": bindings.get(
                "restriction_events_sha256"
            ),
            "restriction_profile_sha256": bindings.get(
                "restriction_profile_sha256"
            ),
            "restriction_profile": policy.get("restriction_profile"),
            "cone_certificate_sha256": certificate.get("certificate_sha256"),
            "cone_certificate_artifact_sha256": fingerprints[
                "restriction_cone_certificate"
            ]["sha256"],
        })
    elif certificate is not None:
        raise GenerationValidationError(
            "ordinary segmentation cannot carry a restriction certificate"
        )
    verification["source_policy_bindings"] = source_policy_bindings
    verification["generation_id"] = diagnostics.generation_id(fingerprints)


def _manifest_bindings(fingerprints: dict) -> dict:
    return {
        key: fingerprints[key]
        for key in sorted(restriction_cone.SEGMENTATION_BINDING_KEYS)
        if key in fingerprints
    }


def _artifact_records(stage_dir: Path) -> dict:
    records = {}
    for path in sorted(stage_dir.rglob("*")):
        relative = path.relative_to(stage_dir).as_posix()
        if path.is_symlink():
            raise GenerationValidationError(
                f"generation contains a symlink: {relative}"
            )
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise GenerationValidationError(
                f"generation contains a non-regular file: {relative}"
            )
        if path.name == "generation-manifest.json":
            continue
        records[relative] = diagnostics.fingerprint_file(
            path,
            logical_path=relative,
        )
    return records


def build_manifest(
    stage_dir,
    verification: dict,
    curated_cells,
) -> dict:
    stage_dir = Path(stage_dir)
    generation_id = verification.get("generation_id", "")
    if not generation_id:
        raise GenerationValidationError("verification has no generation_id")
    fingerprints = verification.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise GenerationValidationError("verification has no evidence fingerprints")
    computed_generation_id = diagnostics.generation_id(fingerprints)
    if not GENERATION_ID_RE.fullmatch(str(generation_id)):
        raise GenerationValidationError(
            "generation_id must be a lowercase 64-character SHA-256"
        )
    if generation_id != computed_generation_id:
        raise GenerationValidationError(
            "verification generation_id disagrees with evidence fingerprints"
        )
    artifacts = _artifact_records(stage_dir)
    missing = sorted(REQUIRED_GENERATED_ARTIFACTS - set(artifacts))
    if missing:
        raise GenerationValidationError(
            f"generation stage is missing required artifacts: {missing}"
        )
    bindings = _manifest_bindings(fingerprints)
    restricted = "source_restriction_evidence" in bindings
    if restricted and "restriction-cone-certificate.json" not in artifacts:
        raise GenerationValidationError(
            "restricted generation stage is missing its cone certificate"
        )
    if restricted and (
        artifacts["restriction-cone-certificate.json"].get("sha256")
        != bindings.get("restriction_cone_certificate", {}).get("sha256")
    ):
        raise GenerationValidationError(
            "staged cone certificate disagrees with verification fingerprints"
        )
    curation_record = artifacts.get("curation.toml")
    evidence_curation = fingerprints.get("curation")
    if (
        not isinstance(curation_record, dict)
        or not isinstance(evidence_curation, dict)
        or curation_record.get("sha256") != evidence_curation.get("sha256")
        or curation_record.get("size_bytes") != evidence_curation.get("size_bytes")
    ):
        raise GenerationValidationError(
            "staged curation disagrees with verification fingerprints"
        )
    ordered = list(curated_cells)
    selected = diagnostics.fingerprint_values(ordered)
    expected_selected = fingerprints.get("selected_output_cells")
    if not isinstance(expected_selected, dict) or (
        selected.get("sha256") != expected_selected.get("sha256")
        or selected.get("count") != expected_selected.get("count")
    ):
        raise GenerationValidationError(
            "ordered curated cells disagree with verification fingerprints"
        )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generation_id": generation_id,
        "identity": {
            "schema_version": "segmentation-generation-identity/v2",
            "generation_id": generation_id,
            "evidence_sha256": diagnostics.generation_id(fingerprints),
            "bindings": bindings,
            "source_policy_bindings": verification.get(
                "source_policy_bindings"
            ),
        },
        "bindings": bindings,
        "source_policy_bindings": verification.get("source_policy_bindings"),
        "verification_schema_version": verification.get("schema_version"),
        "evidence": fingerprints,
        "curated_output_cells": {
            "ordered": ordered,
            "fingerprint": selected,
        },
        "artifacts": artifacts,
    }


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GenerationValidationError(
            f"cannot read {description} at {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise GenerationValidationError(f"{description} must be a JSON object")
    return value


def _expected_generation_files(manifest: dict) -> set[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise GenerationValidationError("generation manifest has no artifacts map")
    return set(artifacts) | {"generation-manifest.json"}


def _curated_cells_from_artifacts(generation_dir: Path) -> list[str]:
    curation_path = generation_dir / "curation.toml"
    segments_path = generation_dir / "segments.json"
    try:
        from .emit import read_curation

        entries = read_curation(curation_path)
        segments = _read_json(segments_path, "segments artifact")
    except (OSError, UnicodeError, ValueError) as exc:
        raise GenerationValidationError(
            f"cannot reconstruct curated output identity: {exc}"
        ) from exc
    cells_by_band = {
        record.get("band"): record.get("cells")
        for record in segments.get("outputs", [])
        if isinstance(record, dict)
    }
    ordered = []
    for entry in entries:
        if not entry.get("include"):
            continue
        cells = cells_by_band.get(entry.get("band"))
        if not isinstance(cells, list):
            raise GenerationValidationError(
                f"curated band {entry.get('band')!r} is absent from segments outputs"
            )
        ordered.extend(str(cell) for cell in cells)
    return ordered


def validate_generation_directory(
    generation_dir,
    *,
    expected_generation_id=None,
    expected_manifest_sha256=None,
    require_pass=False,
    source_generation_dir=None,
) -> dict:
    """Validate every bound byte and the verification/manifest agreement."""
    generation_dir = Path(generation_dir)
    manifest_path = generation_dir / "generation-manifest.json"
    if generation_dir.is_symlink():
        raise GenerationValidationError(
            f"generation directory must not be a symlink: {generation_dir}"
        )
    if not generation_dir.is_dir():
        raise GenerationValidationError(
            f"generation directory is missing: {generation_dir}"
        )
    manifest = _read_json(manifest_path, "generation manifest")
    manifest_schema = manifest.get("schema_version")
    if manifest_schema not in {
        LEGACY_MANIFEST_SCHEMA_VERSION,
        MANIFEST_SCHEMA_VERSION,
    }:
        raise GenerationValidationError(
            f"unsupported generation manifest schema: {manifest_schema!r}"
        )
    generation_id = manifest.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise GenerationValidationError(
            "generation manifest has no valid lowercase SHA-256 generation_id"
        )
    computed_generation_id = diagnostics.generation_id(manifest.get("evidence") or {})
    if computed_generation_id != generation_id:
        raise GenerationValidationError(
            "generation manifest ID disagrees with evidence fingerprints"
        )
    if manifest_schema == MANIFEST_SCHEMA_VERSION:
        bindings = _manifest_bindings(manifest.get("evidence") or {})
        source_policy_bindings = manifest.get("source_policy_bindings")
        expected_identity = {
            "schema_version": "segmentation-generation-identity/v2",
            "generation_id": generation_id,
            "evidence_sha256": computed_generation_id,
            "bindings": bindings,
            "source_policy_bindings": source_policy_bindings,
        }
        if manifest.get("bindings") != bindings:
            raise GenerationValidationError(
                "generation manifest source bindings disagree with evidence"
            )
        if manifest.get("identity") != expected_identity:
            raise GenerationValidationError(
                "generation manifest v2 identity is invalid"
            )
        if source_policy_bindings is not None:
            if not isinstance(source_policy_bindings, dict):
                raise GenerationValidationError(
                    "source policy bindings must be an object"
                )
            source_binding = source_policy_bindings.get("source_generation")
            if (
                not isinstance(source_binding, dict)
                or bindings.get("source_generation")
                != _value_fingerprint(source_binding, "source-generation")
            ):
                raise GenerationValidationError(
                    "source generation binding fingerprint is invalid"
                )
            restricted_binding = "source_restriction_evidence" in bindings
            route = source_binding.get("route")
            if route not in {"pass", "recalc_candidate", "restricted_pass"}:
                raise GenerationValidationError(
                    "source generation binding has an unsupported route"
                )
            if (route == "restricted_pass") != restricted_binding:
                raise GenerationValidationError(
                    "restricted source and cone bindings are inconsistent"
                )
            if restricted_binding:
                restriction_value = {
                    "restriction_evidence_sha256": source_policy_bindings.get(
                        "restriction_evidence_sha256"
                    ),
                    "restriction_events_sha256": source_policy_bindings.get(
                        "restriction_events_sha256"
                    ),
                }
                profile_value = {
                    "restriction_profile": source_policy_bindings.get(
                        "restriction_profile"
                    ),
                    "restriction_profile_sha256": source_policy_bindings.get(
                        "restriction_profile_sha256"
                    ),
                }
                if bindings.get("source_restriction_evidence") != (
                    _value_fingerprint(
                        restriction_value, "source-restriction-evidence"
                    )
                ):
                    raise GenerationValidationError(
                        "restriction evidence binding fingerprint is invalid"
                    )
                if bindings.get("source_restriction_profile") != (
                    _value_fingerprint(
                        profile_value, "source-restriction-profile"
                    )
                ):
                    raise GenerationValidationError(
                        "restriction profile binding fingerprint is invalid"
                    )
    if expected_generation_id is not None and generation_id != expected_generation_id:
        raise GenerationValidationError("pointer and manifest generation_id disagree")
    if (
        expected_manifest_sha256 is not None
        and _sha256(manifest_path) != expected_manifest_sha256
    ):
        raise GenerationValidationError("generation manifest hash disagrees with pointer")

    expected_files = _expected_generation_files(manifest)
    actual_files = set()
    for path in generation_dir.rglob("*"):
        relative = path.relative_to(generation_dir).as_posix()
        if path.is_symlink():
            raise GenerationValidationError(
                f"generation contains a symlink: {relative}"
            )
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise GenerationValidationError(
                f"generation contains a non-regular file: {relative}"
            )
        actual_files.add(relative)
    if actual_files != expected_files:
        raise GenerationValidationError(
            "generation file set is incomplete or unbound: "
            f"missing={sorted(expected_files - actual_files)}, "
            f"unexpected={sorted(actual_files - expected_files)}"
        )
    missing_required = sorted(REQUIRED_GENERATED_ARTIFACTS - actual_files)
    if missing_required:
        raise GenerationValidationError(
            f"generation is missing required artifacts: {missing_required}"
        )
    for relative, record in sorted(manifest["artifacts"].items()):
        path = generation_dir / relative
        if not isinstance(record, dict) or not record.get("available"):
            raise GenerationValidationError(f"invalid artifact record for {relative}")
        if (
            record.get("algorithm") != "sha256"
            or _sha256(path) != record.get("sha256")
            or path.stat().st_size != record.get("size_bytes")
        ):
            raise GenerationValidationError(f"tampered artifact: {relative}")

    segments = _read_json(generation_dir / "segments.json", "segments artifact")
    verification = segments.get("verification")
    if not isinstance(verification, dict):
        raise GenerationValidationError("segments artifact has no verification object")
    if verification.get("schema_version") not in SUPPORTED_VERIFICATION_SCHEMAS:
        raise GenerationValidationError(
            "unsupported segmentation verification schema: "
            f"{verification.get('schema_version')!r}"
        )
    if verification.get("generation_id") != generation_id:
        raise GenerationValidationError(
            "verification and manifest generation_id disagree"
        )
    contract = verification.get("generation_manifest")
    expected_contract_schema = (
        MANIFEST_SCHEMA_VERSION
        if manifest_schema == MANIFEST_SCHEMA_VERSION
        else LEGACY_MANIFEST_SCHEMA_VERSION
    )
    if not isinstance(contract, dict) or (
        contract.get("schema_version") != expected_contract_schema
        or contract.get("generation_id") != generation_id
        or contract.get("path") != "generation-manifest.json"
    ):
        raise GenerationValidationError(
            "verification does not bind the generation manifest"
        )
    if verification.get("fingerprints") != manifest.get("evidence"):
        raise GenerationValidationError(
            "verification and generation evidence fingerprints disagree"
        )
    if (
        manifest_schema == MANIFEST_SCHEMA_VERSION
        and verification.get("source_policy_bindings")
        != manifest.get("source_policy_bindings")
    ):
        raise GenerationValidationError(
            "verification and manifest source policy bindings disagree"
        )
    restricted = (
        manifest_schema == MANIFEST_SCHEMA_VERSION
        and "source_restriction_evidence" in manifest.get("bindings", {})
    )
    certificate_path = generation_dir / "restriction-cone-certificate.json"
    if restricted and "restriction-cone-certificate.json" not in actual_files:
        raise GenerationValidationError(
            "restricted segmentation has no cone certificate artifact"
        )
    if restricted:
        certificate = _read_json(
            certificate_path, "restriction cone certificate"
        )
        source_policy = manifest.get("source_policy_bindings") or {}
        certificate_source = certificate.get("source_generation") or {}
        if (
            source_policy.get("cone_certificate_sha256")
            != certificate.get("certificate_sha256")
            or source_policy.get("cone_certificate_artifact_sha256")
            != manifest["artifacts"]["restriction-cone-certificate.json"].get(
                "sha256"
            )
            or source_policy.get("source_generation", {}).get("generation_id")
            != certificate_source.get("generation_id")
            or source_policy.get("restriction_evidence_sha256")
            != certificate_source.get("restriction_evidence_sha256")
            or source_policy.get("restriction_events_sha256")
            != certificate_source.get("restriction_events_sha256")
            or source_policy.get("restriction_profile_sha256")
            != certificate_source.get("restriction_profile_sha256")
        ):
            raise GenerationValidationError(
                "cone certificate hashes disagree with generation bindings"
            )
    if (
        manifest_schema == MANIFEST_SCHEMA_VERSION
        and not restricted
        and "restriction-cone-certificate.json" in actual_files
    ):
        raise GenerationValidationError(
            "ordinary segmentation unexpectedly carries a cone certificate"
        )

    ordered_record = manifest.get("curated_output_cells")
    if not isinstance(ordered_record, dict):
        raise GenerationValidationError("manifest has no ordered curated cells")
    ordered = ordered_record.get("ordered")
    if not isinstance(ordered, list):
        raise GenerationValidationError("ordered curated cells must be a list")
    if ordered_record.get("fingerprint") != diagnostics.fingerprint_values(ordered):
        raise GenerationValidationError("ordered curated cell fingerprint is invalid")
    if ordered != _curated_cells_from_artifacts(generation_dir):
        raise GenerationValidationError(
            "curation, segments, and ordered curated cells disagree"
        )

    if require_pass:
        closure = (
            verification.get("provenance", {})
            .get("runtime", {})
            .get("closure", {})
        )
        proof_reads = (
            verification.get("counts", {})
            .get("cache_reads", {})
            .get("proof")
        )
        proof_contract = segments.get("proof")
        proof_closure = (
            proof_contract.get("closure", {})
            if isinstance(proof_contract, dict)
            else {}
        )
        strict_proof = (
            verification.get("provenance", {})
            .get("proof", {})
            .get("strict")
        )
        failures = []
        if verification.get("status") != "pass":
            failures.append("status")
        if verification.get("disposition") != "pass":
            failures.append("disposition")
        if verification.get("blocking_reasons") != []:
            failures.append("blocking_reasons")
        if verification.get("skipped") is not False:
            failures.append("skipped")
        if verification.get("passed") is not True:
            failures.append("passed")
        if strict_proof is not True:
            failures.append("strict_proof")
        if closure.get("stabilized") is not True:
            failures.append("runtime_closure")
        if proof_closure != closure or proof_closure.get("stabilized") is not True:
            failures.append("proof_object")
        if proof_reads != 0:
            failures.append("proof_cache_reads")
        if failures:
            raise GenerationValidationError(
                "strict production verification gate failed: "
                + ", ".join(failures)
            )
        if restricted:
            if source_generation_dir is None:
                raise GenerationValidationError(
                    "restricted pass validation requires its source generation"
                )
            try:
                restriction_cone.validate_certificate(
                    certificate_path,
                    source_generation_dir=source_generation_dir,
                    segmentation_artifact=generation_dir / "segments.json",
                )
            except restriction_cone.RestrictionConeError as exc:
                raise GenerationValidationError(
                    f"restriction cone validation failed: {exc}"
                ) from exc
    return manifest


def _verify_live_evidence(
    manifest: dict,
    *,
    source_path,
    ast_dir,
    curation_path,
) -> None:
    try:
        from xl_source_publication import validate_bound_ast_if_required

        validate_bound_ast_if_required(source_path, ast_dir)
    except ValueError as exc:
        raise GenerationValidationError(
            f"effective source/AST provenance failed: {exc}"
        ) from exc
    ordered = manifest["curated_output_cells"]["ordered"]
    current, missing = evidence_fingerprints(
        source_path,
        ast_dir,
        curation_path,
        ordered,
    )
    if missing:
        raise GenerationValidationError(
            f"current evidence is missing: {missing}"
        )
    expected = restriction_cone.base_segmentation_fingerprints(
        manifest.get("evidence") or {}
    )
    if current != expected:
        differing = sorted(
            name
            for name in set(current) | set(expected or {})
            if current.get(name) != (expected or {}).get(name)
        )
        raise GenerationValidationError(
            f"current evidence does not match generation manifest: {differing}"
        )


def resolve_current_generation(
    seg_dir,
    *,
    source_path=None,
    ast_dir=None,
    require_pass=False,
    validate_live_evidence=False,
    expected_generation_id=None,
    source_generation_dir=None,
) -> tuple[Path, dict]:
    """Resolve one immutable generation, then validate it before returning."""
    seg_dir = Path(seg_dir)
    pointer_path = seg_dir / "current.json"
    pointer = _read_json(pointer_path, "current generation pointer")
    if pointer.get("schema_version") != POINTER_SCHEMA_VERSION:
        raise GenerationValidationError(
            f"unsupported current pointer schema: {pointer.get('schema_version')!r}"
        )
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise GenerationValidationError("current pointer has an invalid generation_id")
    if (
        expected_generation_id is not None
        and generation_id != expected_generation_id
    ):
        raise GenerationValidationError(
            "current pointer changed from the expected generation_id"
        )
    relative = pointer.get("generation_path")
    if relative != f"generations/{generation_id}":
        raise GenerationValidationError("unsafe or inconsistent generation path")
    generations_dir = seg_dir / "generations"
    if generations_dir.is_symlink():
        raise GenerationValidationError("generations directory must not be a symlink")
    generation_dir = generations_dir / generation_id
    if generation_dir.resolve().parent != generations_dir.resolve():
        raise GenerationValidationError("generation path escapes generations directory")
    manifest = validate_generation_directory(
        generation_dir,
        expected_generation_id=generation_id,
        expected_manifest_sha256=pointer.get("manifest_sha256"),
        require_pass=require_pass,
        source_generation_dir=source_generation_dir,
    )
    if validate_live_evidence:
        if source_path is None or ast_dir is None:
            raise GenerationValidationError(
                "strict live-evidence validation requires source_path and ast_dir"
            )
        _verify_live_evidence(
            manifest,
            source_path=Path(source_path),
            ast_dir=Path(ast_dir),
            curation_path=seg_dir / "curation.toml",
        )
    return generation_dir, manifest


def resolve_generation_by_id(
    seg_dir,
    generation_id,
    *,
    source_path=None,
    ast_dir=None,
    source_generation_dir=None,
    require_pass=False,
    validate_live_evidence=False,
) -> tuple[Path, dict]:
    """Resolve an inactive immutable candidate without consulting current.json."""
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise GenerationValidationError("segmentation generation_id is invalid")
    seg_dir = Path(seg_dir)
    generations = seg_dir / "generations"
    if generations.is_symlink():
        raise GenerationValidationError("generations directory must not be a symlink")
    generation_dir = generations / generation_id
    if generation_dir.resolve().parent != generations.resolve():
        raise GenerationValidationError("generation path escapes generations directory")
    manifest = validate_generation_directory(
        generation_dir,
        expected_generation_id=generation_id,
        require_pass=require_pass,
        source_generation_dir=source_generation_dir,
    )
    if validate_live_evidence:
        if source_path is None or ast_dir is None:
            raise GenerationValidationError(
                "strict live-evidence validation requires source_path and ast_dir"
            )
        _verify_live_evidence(
            manifest,
            source_path=Path(source_path),
            ast_dir=Path(ast_dir),
            curation_path=seg_dir / "curation.toml",
        )
    return generation_dir, manifest


def resolve_for_consumer(
    seg_dir,
    *,
    mode="strict",
    source_path=None,
    ast_dir=None,
    require_pass=True,
    expected_generation_id=None,
) -> tuple[Path, dict | None]:
    """Resolve strict, shadow, or explicitly legacy consumer behavior."""
    seg_dir = Path(seg_dir)
    if mode == "legacy":
        return seg_dir, None
    if mode == "shadow":
        return resolve_current_generation(
            seg_dir,
            require_pass=False,
            validate_live_evidence=False,
            expected_generation_id=expected_generation_id,
        )
    if mode != "strict":
        raise GenerationValidationError(f"unknown segmentation mode: {mode!r}")
    return resolve_current_generation(
        seg_dir,
        source_path=source_path,
        ast_dir=ast_dir,
        require_pass=require_pass,
        validate_live_evidence=True,
        expected_generation_id=expected_generation_id,
    )


def _replace_symlink(path: Path, target: str) -> None:
    temporary = path.with_name(f".{path.name}.link-{uuid.uuid4().hex}")
    os.symlink(target, temporary)
    try:
        if os.path.lexists(path) and not path.is_symlink():
            raise GenerationValidationError(
                f"refusing to replace existing legacy artifact: {path}"
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _install_legacy_links(seg_dir: Path, generation_id: str) -> bool:
    """Install or switch legacy links without replacing direct legacy artifacts.

    Existing real files/directories are an explicitly offline legacy layout and
    are left untouched.  In a fresh layout, artifact links are installed while
    broken and become visible together when ``current`` is atomically installed.
    """
    current_link = seg_dir / "current"
    direct = [
        seg_dir / name
        for name in LEGACY_ARTIFACTS
        if os.path.lexists(seg_dir / name) and not (seg_dir / name).is_symlink()
    ]
    if (
        direct
        or (
            os.path.lexists(current_link)
            and not current_link.is_symlink()
        )
    ):
        return False
    for name in LEGACY_ARTIFACTS:
        path = seg_dir / name
        if not os.path.lexists(path):
            os.symlink(f"current/{name}", path)
        elif not path.is_symlink() or os.readlink(path) != f"current/{name}":
            raise GenerationValidationError(f"unsafe legacy compatibility path: {path}")
    _replace_symlink(current_link, f"generations/{generation_id}")
    _fsync_directory(seg_dir)
    return True


def publish_generation(
    stage_dir,
    seg_dir,
    verification: dict,
    curated_cells,
    *,
    fault=None,
    source_path=None,
    ast_dir=None,
    source_generation_dir=None,
) -> tuple[Path, dict]:
    """Publish a complete immutable directory, then switch one small pointer."""
    stage_dir = Path(stage_dir)
    seg_dir = Path(seg_dir)
    seg_dir.mkdir(parents=True, exist_ok=True)
    curation = seg_dir / "curation.toml"
    if curation.is_symlink() or not curation.is_file():
        raise GenerationValidationError("root curation.toml is missing")
    shutil.copyfile(curation, stage_dir / "curation.toml")
    manifest = build_manifest(stage_dir, verification, curated_cells)
    manifest_bytes = _canonical_bytes(manifest)
    (stage_dir / "generation-manifest.json").write_bytes(manifest_bytes)
    validate_generation_directory(stage_dir)
    if fault:
        fault("staged")

    generation_id = manifest["generation_id"]
    generations = seg_dir / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    destination = generations / generation_id
    if destination.exists():
        existing = validate_generation_directory(
            destination,
            expected_generation_id=generation_id,
        )
        if _canonical_bytes(existing) != manifest_bytes:
            raise GenerationValidationError(
                "immutable generation_id collision with different content"
            )
        shutil.rmtree(stage_dir)
    else:
        os.replace(stage_dir, destination)
        _fsync_directory(generations)
    if fault:
        fault("generation_published")

    try:
        validate_generation_directory(
            destination,
            require_pass=True,
            source_generation_dir=source_generation_dir,
        )
        passing = True
    except GenerationValidationError:
        passing = False
    if not passing:
        return destination, manifest
    if "source_restriction_evidence" in manifest.get("bindings", {}):
        return destination, manifest
    if source_path is None or ast_dir is None:
        raise GenerationValidationError(
            "passing publication requires source_path and ast_dir live evidence"
        )
    _verify_live_evidence(
        manifest,
        source_path=Path(source_path),
        ast_dir=Path(ast_dir),
        curation_path=curation,
    )

    pointer = {
        "schema_version": POINTER_SCHEMA_VERSION,
        "generation_id": generation_id,
        "generation_path": f"generations/{generation_id}",
        "manifest_sha256": _sha256(destination / "generation-manifest.json"),
    }
    if fault:
        fault("before_pointer_switch")
    _atomic_write(seg_dir / "current.json", _canonical_bytes(pointer))
    if fault:
        fault("pointer_switched")
    _install_legacy_links(seg_dir, generation_id)
    return destination, manifest


def _main_validate(args) -> int:
    try:
        if getattr(args, "generation_id", None):
            generation, manifest = resolve_generation_by_id(
                args.seg_dir,
                args.generation_id,
                source_path=args.source,
                ast_dir=args.ast_dir,
                source_generation_dir=args.source_generation_dir,
                require_pass=args.require_pass,
                validate_live_evidence=args.validate_live_evidence,
            )
        else:
            generation, manifest = resolve_current_generation(
                args.seg_dir,
                source_path=args.source,
                ast_dir=args.ast_dir,
                require_pass=args.require_pass,
                validate_live_evidence=args.validate_live_evidence,
                expected_generation_id=args.expected_generation_id,
            )
    except GenerationValidationError as exc:
        print(f"segmentation generation FAIL: {exc}")
        return 1
    verification = _read_json(
        generation / "segments.json",
        "segments artifact",
    ).get("verification") or {}
    print(json.dumps({
        "status": "pass" if args.require_pass else "valid",
        "verification_status": verification.get("status"),
        "verification_disposition": verification.get("disposition"),
        "generation_id": manifest["generation_id"],
        "generation_dir": str(generation),
        "manifest_sha256": _sha256(generation / "generation-manifest.json"),
    }, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("seg_dir")
    validate.add_argument("--source")
    validate.add_argument("--ast-dir")
    validate.add_argument("--require-pass", action="store_true")
    validate.add_argument("--validate-live-evidence", action="store_true")
    validate.add_argument("--expected-generation-id")
    validate_id = subparsers.add_parser("validate-id")
    validate_id.add_argument("seg_dir")
    validate_id.add_argument("generation_id")
    validate_id.add_argument("--source")
    validate_id.add_argument("--ast-dir")
    validate_id.add_argument("--source-generation-dir")
    validate_id.add_argument("--require-pass", action="store_true")
    validate_id.add_argument("--validate-live-evidence", action="store_true")
    validate_id.set_defaults(expected_generation_id=None)
    args = parser.parse_args(argv)
    if args.validate_live_evidence and (not args.source or not args.ast_dir):
        parser.error("--validate-live-evidence requires --source and --ast-dir")
    return _main_validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
