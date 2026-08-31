"""Atomic v2 publication for source, segmentation, and task generations."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree


RELEASE_SCHEMA_VERSION = "workbook-release/v2"
RELEASE_IDENTITY_SCHEMA_VERSION = "workbook-release-identity/v2"
RELEASE_POINTER_SCHEMA_VERSION = "workbook-current-release/v2"
RELEASE_POLICY_VERSION = "workbook-release-policy/v2"
TASK_SCHEMA_VERSION = "task-generation/v2"
TASK_IDENTITY_SCHEMA_VERSION = "task-generation-identity/v2"
LEGACY_SNAPSHOT_SCHEMA_VERSION = "workbook-legacy-snapshot/v1"
LEGACY_POINTER_SCHEMA_VERSION = "workbook-legacy-snapshot-pointer/v1"
SOURCE_SCHEMA_VERSION = "source-generation/v2"
SEGMENTATION_SCHEMA_VERSION = "segmentation-generation/v2"
SOURCE_POLICY_VERSION = "source-recalc-policy/v3"
SOURCE_HEALTH_SCHEMA_VERSION = "xlsx-source-health/v3"
PREVIOUS_SOURCE_POLICY_VERSION = "source-recalc-policy/v2"
PREVIOUS_SOURCE_HEALTH_SCHEMA_VERSION = "xlsx-source-health/v2"
SEGMENTATION_VERIFICATION_SCHEMA_VERSION = "segmentation-verification/v2"
CONE_SCHEMA_VERSION = "restriction-cone-certificate/v1"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_RESTRICTED_MODES = frozenset({
    "data_tables_outside_output_cone",
})
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TRUE_EXTERNAL_XLSX_PARTS = (
    "xl/externalLinks/",
    "xl/connections.xml",
    "xl/externalConnections/",
    "xl/embeddings/",
    "xl/oleObjects/",
    "xl/vbaProject.bin",
)
EXTERNAL_RELATIONSHIP_TYPES = frozenset({
    "connection",
    "connections",
    "externallink",
    "externallinkpath",
    "externalworkbook",
    "externalworkbookpath",
    "oleobject",
    "package",
})


class ReleasePublicationError(ValueError):
    """A release is stale, incomplete, mixed-version, or tampered."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise ReleasePublicationError(f"immutable file already exists: {path}")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and path.exists():
            raise ReleasePublicationError(f"immutable file already exists: {path}")
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleasePublicationError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleasePublicationError(f"{description} must be a JSON object")
    return value


def _require_hash(value: object, description: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ReleasePublicationError(f"{description} must be a lowercase SHA-256")
    return value


def _manifest_record(directory: Path) -> dict:
    manifest = directory / "generation-manifest.json"
    return {
        "generation_id": directory.name,
        "generation_path": str(directory.resolve()),
        "manifest_path": str(manifest.resolve()),
        "manifest_sha256": _sha256(manifest),
    }


def _artifact_records(root: Path, *, exclude: frozenset[str] = frozenset()) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ReleasePublicationError("artifact root is missing or is a symlink")
    records = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ReleasePublicationError(f"unsafe artifact: {relative}")
        if stat.S_ISREG(mode) and relative not in exclude:
            records[relative] = {
                "algorithm": "sha256",
                "path": relative,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
    return records


def _validate_artifacts(root: Path, records: dict, manifest_name: str) -> None:
    if not isinstance(records, dict):
        raise ReleasePublicationError("artifact manifest is missing")
    actual = _artifact_records(root, exclude=frozenset({manifest_name}))
    if set(actual) != set(records):
        raise ReleasePublicationError("artifact set is incomplete or contains extras")
    if actual != records:
        raise ReleasePublicationError("artifact bytes do not match their manifest")


def _journal(root: Path, operation_id: str, phase: str, **fields) -> None:
    journal = root / "journal" / f"{operation_id}.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "release-publication-journal/v1",
        "operation_id": operation_id,
        "phase": phase,
        "time_ns": time.time_ns(),
        **fields,
    }
    with journal.open("ab") as handle:
        handle.write(_canonical_bytes(record))
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(journal.parent)


@contextlib.contextmanager
def _workbook_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".current-release.lock"
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _reject_external_package_artifacts(task_dir: Path) -> None:
    environment = task_dir / "environment"
    if not environment.is_dir() or environment.is_symlink():
        raise ReleasePublicationError("task environment is missing or unsafe")
    for workbook in environment.rglob("*"):
        if not workbook.is_file() or workbook.suffix.casefold() not in {".xlsx", ".xlsm"}:
            continue
        try:
            with zipfile.ZipFile(workbook) as package:
                names = package.namelist()
                blocked_relationships = []
                for relationship_part in sorted(
                    name for name in names if name.casefold().endswith(".rels")
                ):
                    try:
                        root = ElementTree.fromstring(
                            package.read(relationship_part)
                        )
                    except (KeyError, ElementTree.ParseError) as exc:
                        raise ReleasePublicationError(
                            "staged workbook has an unreadable OOXML relationship "
                            f"part: {relationship_part}"
                        ) from exc
                    for relation in root.iter():
                        if relation.tag.rsplit("}", 1)[-1] != "Relationship":
                            continue
                        relation_type = relation.attrib.get("Type", "")
                        type_name = relation_type.rstrip("/").rsplit("/", 1)[-1]
                        target_mode = relation.attrib.get("TargetMode", "")
                        if (
                            type_name.casefold() in EXTERNAL_RELATIONSHIP_TYPES
                            or target_mode.strip().casefold() == "external"
                        ):
                            blocked_relationships.append(
                                f"{relationship_part}:"
                                f"{relation.attrib.get('Id', '<no-id>')}"
                            )
        except (OSError, zipfile.BadZipFile) as exc:
            raise ReleasePublicationError(
                f"staged workbook is not a valid OOXML package: {workbook.name}"
            ) from exc
        blocked = sorted(
            name
            for name in names
            if any(
                name.casefold() == prefix.casefold()
                or name.casefold().startswith(prefix.casefold())
                for prefix in TRUE_EXTERNAL_XLSX_PARTS
            )
        )
        if blocked:
            raise ReleasePublicationError(
                "true external package artifacts are forbidden in staged workbook: "
                + ", ".join(blocked[:8])
            )
        if blocked_relationships:
            raise ReleasePublicationError(
                "external OOXML relationships are forbidden in staged workbook: "
                + ", ".join(blocked_relationships[:8])
            )
    forbidden = (
        "environment/eval",
        "environment/answer_key.json",
        "environment/normalized.json",
        "environment/masked_inputs.json",
    )
    for relative in forbidden:
        if (task_dir / relative).exists():
            raise ReleasePublicationError(
                f"build/evaluation artifact leaked into task environment: {relative}"
            )


def publish_task_generation(
    task_dir: str | Path,
    publication_root: str | Path,
    workbook_id: str,
    *,
    bindings: dict,
    fault=None,
) -> tuple[Path, dict]:
    """Copy a complete staged task into an immutable, hash-bound generation."""
    if not SAFE_ID_RE.fullmatch(str(workbook_id)):
        raise ReleasePublicationError("workbook ID is unsafe")
    stage_source = Path(task_dir)
    root = Path(publication_root)
    _reject_external_package_artifacts(stage_source)
    if not isinstance(bindings, dict):
        raise ReleasePublicationError("task generation bindings are required")
    for key in ("source_generation_id", "segmentation_generation_id"):
        _require_hash(bindings.get(key), f"task binding {key}")
    records = _artifact_records(stage_source)
    identity = {
        "schema_version": TASK_IDENTITY_SCHEMA_VERSION,
        "workbook_id": workbook_id,
        "bindings": bindings,
        "artifacts": records,
    }
    generation_id = canonical_hash(identity)
    manifest = {
        "schema_version": TASK_SCHEMA_VERSION,
        "generation_id": generation_id,
        "identity": identity,
        "bindings": bindings,
        "artifacts": records,
    }
    generations = root / "task-generations"
    generations.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".task-stage-", dir=str(root)))
    operation_id = f"task-{generation_id}"
    try:
        _journal(root, operation_id, "staging")
        for child in stage_source.iterdir():
            target = temporary / child.name
            if child.is_dir():
                shutil.copytree(child, target, symlinks=False)
            else:
                shutil.copy2(child, target)
        (temporary / "generation-manifest.json").write_bytes(
            _canonical_bytes(manifest)
        )
        validate_task_generation(temporary, expected_generation_id=generation_id)
        if fault:
            fault("task_staged")
        destination = generations / generation_id
        if destination.exists():
            existing = validate_task_generation(
                destination, expected_generation_id=generation_id
            )
            if existing != manifest:
                raise ReleasePublicationError("task generation ID collision")
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, destination)
            _fsync_directory(generations)
        _journal(root, operation_id, "materialized", generation_id=generation_id)
        if fault:
            fault("task_materialized")
        return destination, manifest
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        _journal(root, operation_id, "failed")
        raise


def validate_task_generation(
    generation_dir: str | Path,
    *,
    expected_generation_id: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    directory = Path(generation_dir)
    manifest_path = directory / "generation-manifest.json"
    manifest = _read_json(manifest_path, "task generation manifest")
    if manifest.get("schema_version") != TASK_SCHEMA_VERSION:
        raise ReleasePublicationError("unsupported task generation schema")
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or identity.get("schema_version") != (
        TASK_IDENTITY_SCHEMA_VERSION
    ):
        raise ReleasePublicationError("task generation identity is invalid")
    generation_id = _require_hash(manifest.get("generation_id"), "task generation ID")
    if generation_id != canonical_hash(identity):
        raise ReleasePublicationError("task generation identity hash mismatch")
    if expected_generation_id is not None and generation_id != expected_generation_id:
        raise ReleasePublicationError("task generation ID changed")
    if (
        expected_manifest_sha256 is not None
        and _sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ReleasePublicationError("task manifest hash mismatch")
    if (
        manifest.get("bindings") != identity.get("bindings")
        or manifest.get("artifacts") != identity.get("artifacts")
    ):
        raise ReleasePublicationError("task manifest bindings are inconsistent")
    _validate_artifacts(directory, manifest["artifacts"], "generation-manifest.json")
    _reject_external_package_artifacts(directory)
    return manifest


def resolve_task_generation_by_id(
    publication_root: str | Path, generation_id: str
) -> tuple[Path, dict]:
    _require_hash(generation_id, "task generation ID")
    generations = Path(publication_root) / "task-generations"
    if generations.is_symlink():
        raise ReleasePublicationError("task generations directory is a symlink")
    directory = generations / generation_id
    if directory.resolve().parent != generations.resolve():
        raise ReleasePublicationError("task generation escapes its root")
    return directory, validate_task_generation(
        directory, expected_generation_id=generation_id
    )


def _release_components(
    workbook_id: str,
    source_root: Path,
    source_generation_id: str,
    segmentation_root: Path,
    segmentation_generation_id: str,
    task_root: Path,
    task_generation_id: str,
    *,
    source_policy_version: str = SOURCE_POLICY_VERSION,
    source_health_schema_version: str = SOURCE_HEALTH_SCHEMA_VERSION,
) -> tuple[dict, dict, dict, Path, Path, Path]:
    from xl_source_publication import resolve_source_generation_by_id
    from xl_seg.publication import resolve_generation_by_id

    source_dir, source_manifest = resolve_source_generation_by_id(
        source_root, source_generation_id
    )
    if source_manifest.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ReleasePublicationError("release requires source-generation/v2")
    layout = source_manifest.get("layout") or {}
    if layout.get("workbook_id") != workbook_id:
        raise ReleasePublicationError("source generation workbook ID mismatch")
    source_identity = source_manifest.get("identity") or {}
    if source_identity.get("policy_version") != source_policy_version:
        raise ReleasePublicationError("unsupported or downgraded source policy")
    health = _read_json(source_dir / "health.json", "source health")
    if (
        health.get("schema_version") != source_health_schema_version
        or health.get("policy_version") != source_policy_version
    ):
        raise ReleasePublicationError("unsupported source health policy/schema")
    segmentation_dir, segmentation_manifest = resolve_generation_by_id(
        segmentation_root,
        segmentation_generation_id,
        require_pass=True,
        source_generation_dir=source_dir,
    )
    if segmentation_manifest.get("schema_version") != SEGMENTATION_SCHEMA_VERSION:
        raise ReleasePublicationError("release requires segmentation-generation/v2")
    if (
        segmentation_manifest.get("verification_schema_version")
        != SEGMENTATION_VERIFICATION_SCHEMA_VERSION
    ):
        raise ReleasePublicationError("unsupported segmentation verification schema")
    source_binding = (
        segmentation_manifest.get("source_policy_bindings") or {}
    ).get("source_generation") or {}
    source_bindings = source_manifest.get("bindings") or {}
    if (
        source_binding.get("generation_id") != source_generation_id
        or source_binding.get("source_sha256") != source_bindings.get("source_sha256")
        or source_binding.get("health_report_sha256")
        != source_bindings.get("health_report_sha256")
        or source_binding.get("policy_version") != source_policy_version
    ):
        raise ReleasePublicationError(
            "segmentation does not bind the exact source generation"
        )
    restricted = source_binding.get("route") in {
        "restricted_pass",
        "restricted_recalc_pass",
    }
    policy_decision = source_identity.get("policy_decision") or {}
    recovery_mode = policy_decision.get("recovery_mode")
    recovery_restricted = recovery_mode in RECOVERY_RESTRICTED_MODES
    if recovery_mode is not None and not recovery_restricted:
        raise ReleasePublicationError("unsupported restricted recovery mode")
    if recovery_restricted and (
        source_policy_version == PREVIOUS_SOURCE_POLICY_VERSION
        or not restricted
        or policy_decision.get("route") != source_binding.get("route")
    ):
        raise ReleasePublicationError(
            "restricted recovery mode is not bound to the source route"
        )
    policy_bindings = segmentation_manifest.get("source_policy_bindings") or {}
    if restricted:
        required_source_bindings = (
            "restriction_evidence_sha256",
            "restriction_events_sha256",
            "restriction_profile_sha256",
            "recalc_signals_sha256",
        )
        if not recovery_restricted:
            required_source_bindings += ("inventory_approval_sha256",)
        if source_policy_version != PREVIOUS_SOURCE_POLICY_VERSION and any(
            not isinstance(source_bindings.get(key), str)
            or HASH_RE.fullmatch(source_bindings[key]) is None
            for key in required_source_bindings
        ):
            raise ReleasePublicationError(
                "current restricted source bindings are incomplete"
            )
        if recovery_restricted and (
            source_bindings.get("inventory_approval_sha256") is not None
            or policy_bindings.get("inventory_approval_sha256") is not None
        ):
            raise ReleasePublicationError(
                "restricted recovery mode must omit inventory approval"
            )
        if (
            policy_bindings.get("restriction_evidence_sha256")
            != source_bindings.get("restriction_evidence_sha256")
            or policy_bindings.get("restriction_events_sha256")
            != source_bindings.get("restriction_events_sha256")
            or policy_bindings.get("restriction_profile_sha256")
            != source_bindings.get("restriction_profile_sha256")
            or policy_bindings.get("inventory_approval_sha256")
            != source_bindings.get("inventory_approval_sha256")
            or policy_bindings.get("recalc_signals_sha256")
            != source_bindings.get("recalc_signals_sha256")
        ):
            raise ReleasePublicationError("restriction evidence bindings changed")
        certificate = _read_json(
            segmentation_dir / "restriction-cone-certificate.json",
            "restriction cone certificate",
        )
        certificate_hash = policy_bindings.get("cone_certificate_sha256")
        if (
            certificate.get("schema_version") != CONE_SCHEMA_VERSION
            or not isinstance(certificate_hash, str)
            or HASH_RE.fullmatch(certificate_hash) is None
            or certificate.get("certificate_sha256") != certificate_hash
        ):
            raise ReleasePublicationError(
                "restriction cone certificate is incomplete or changed"
            )
    elif any(
        policy_bindings.get(key) is not None
        for key in (
            "restriction_evidence_sha256",
            "restriction_events_sha256",
            "restriction_profile_sha256",
            "inventory_approval_sha256",
            "recalc_signals_sha256",
            "cone_certificate_sha256",
        )
    ):
        raise ReleasePublicationError("restriction evidence was reinterpreted")
    task_dir, task_manifest = resolve_task_generation_by_id(
        task_root, task_generation_id
    )
    task_bindings = task_manifest.get("bindings") or {}
    expected_task_bindings = {
        "source_generation_id": source_generation_id,
        "source_manifest_sha256": _sha256(
            source_dir / "generation-manifest.json"
        ),
        "source_health_sha256": source_bindings.get("health_sha256"),
        "source_health_report_sha256": source_bindings.get("health_report_sha256"),
        "restriction_evidence_sha256": source_bindings.get(
            "restriction_evidence_sha256"
        ),
        "restriction_events_sha256": source_bindings.get(
            "restriction_events_sha256"
        ),
        "restriction_profile_sha256": source_bindings.get(
            "restriction_profile_sha256"
        ),
        "inventory_approval_sha256": source_bindings.get(
            "inventory_approval_sha256"
        ),
        "recalc_signals_sha256": source_bindings.get(
            "recalc_signals_sha256"
        ),
        "segmentation_generation_id": segmentation_generation_id,
        "segmentation_manifest_sha256": _sha256(
            segmentation_dir / "generation-manifest.json"
        ),
        "cone_certificate_sha256": policy_bindings.get(
            "cone_certificate_sha256"
        ),
    }
    for key, expected in expected_task_bindings.items():
        if task_bindings.get(key) != expected:
            raise ReleasePublicationError(f"task generation binding changed: {key}")
    return (
        source_manifest,
        segmentation_manifest,
        task_manifest,
        source_dir,
        segmentation_dir,
        task_dir,
    )


def build_release_manifest(
    workbook_id: str,
    *,
    source_root: str | Path,
    source_generation_id: str,
    segmentation_root: str | Path,
    segmentation_generation_id: str,
    task_root: str | Path,
    task_generation_id: str,
    prior_release_id: str | None,
    legacy_snapshot_hash: str | None,
    source_policy_version: str = SOURCE_POLICY_VERSION,
    source_health_schema_version: str = SOURCE_HEALTH_SCHEMA_VERSION,
) -> dict:
    """Validate all immutable inputs and derive the canonical release identity."""
    if not SAFE_ID_RE.fullmatch(str(workbook_id)):
        raise ReleasePublicationError("workbook ID is unsafe")
    if prior_release_id is not None:
        _require_hash(prior_release_id, "prior release ID")
    if legacy_snapshot_hash is not None:
        _require_hash(legacy_snapshot_hash, "legacy snapshot hash")
    (
        source_manifest,
        segmentation_manifest,
        task_manifest,
        source_dir,
        segmentation_dir,
        task_dir,
    ) = _release_components(
        workbook_id,
        Path(source_root),
        source_generation_id,
        Path(segmentation_root),
        segmentation_generation_id,
        Path(task_root),
        task_generation_id,
        source_policy_version=source_policy_version,
        source_health_schema_version=source_health_schema_version,
    )
    source_bindings = source_manifest["bindings"]
    policy_bindings = segmentation_manifest.get("source_policy_bindings") or {}
    source_record = _manifest_record(source_dir)
    segmentation_record = _manifest_record(segmentation_dir)
    task_record = {
        **_manifest_record(task_dir),
        "task_generation_sha256": canonical_hash(task_manifest["identity"]),
    }
    bindings = {
        "source_sha256": source_bindings.get("source_sha256"),
        "health_sha256": source_bindings.get("health_sha256"),
        "health_report_sha256": source_bindings.get("health_report_sha256"),
        "restriction_evidence_sha256": source_bindings.get(
            "restriction_evidence_sha256"
        ),
        "restriction_events_sha256": source_bindings.get(
            "restriction_events_sha256"
        ),
        "restriction_profile_sha256": source_bindings.get(
            "restriction_profile_sha256"
        ),
        "inventory_approval_sha256": source_bindings.get(
            "inventory_approval_sha256"
        ),
        "recalc_signals_sha256": source_bindings.get(
            "recalc_signals_sha256"
        ),
        "cone_certificate_sha256": policy_bindings.get(
            "cone_certificate_sha256"
        ),
    }
    versions = {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "release_policy_version": RELEASE_POLICY_VERSION,
        "source_schema_version": SOURCE_SCHEMA_VERSION,
        "source_health_schema_version": source_health_schema_version,
        "source_policy_version": source_policy_version,
        "segmentation_schema_version": SEGMENTATION_SCHEMA_VERSION,
        "segmentation_verification_schema_version":
            SEGMENTATION_VERIFICATION_SCHEMA_VERSION,
        "cone_schema_version": (
            CONE_SCHEMA_VERSION
            if bindings["cone_certificate_sha256"] is not None
            else None
        ),
        "task_schema_version": TASK_SCHEMA_VERSION,
    }
    identity = {
        "schema_version": RELEASE_IDENTITY_SCHEMA_VERSION,
        "workbook_id": workbook_id,
        "source_generation": source_record,
        "segmentation_generation": segmentation_record,
        "task_generation": task_record,
        "bindings": bindings,
        "versions": versions,
        "prior_release_id": prior_release_id,
        "legacy_snapshot_hash": legacy_snapshot_hash,
    }
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": canonical_hash(identity),
        "identity": identity,
        **{key: identity[key] for key in (
            "workbook_id",
            "source_generation",
            "segmentation_generation",
            "task_generation",
            "bindings",
            "versions",
            "prior_release_id",
            "legacy_snapshot_hash",
        )},
    }


def _current_pointer(root: Path, *, absent_ok: bool = False) -> dict | None:
    path = root / "current-release.json"
    if not path.exists():
        if absent_ok:
            return None
        raise ReleasePublicationError("current release pointer is absent")
    if path.is_symlink() or not path.is_file():
        raise ReleasePublicationError("current release pointer is unsafe")
    pointer = _read_json(path, "current release pointer")
    if pointer.get("schema_version") != RELEASE_POINTER_SCHEMA_VERSION:
        raise ReleasePublicationError("unsupported current release pointer schema")
    _require_hash(pointer.get("release_id"), "current release ID")
    if pointer.get("release_path") != f"releases/{pointer['release_id']}":
        raise ReleasePublicationError("current release path is unsafe")
    _require_hash(pointer.get("manifest_sha256"), "release manifest hash")
    return pointer


def publish_release(
    publication_root: str | Path,
    workbook_id: str,
    *,
    source_root: str | Path,
    source_generation_id: str,
    segmentation_root: str | Path,
    segmentation_generation_id: str,
    task_root: str | Path | None = None,
    task_generation_id: str,
    expected_current_release_id: str | None,
    legacy_snapshot_hash: str | None = None,
    compatibility_paths: dict[str, str | Path] | None = None,
    fault=None,
) -> tuple[Path, dict]:
    """Write an immutable release and CAS the sole authoritative pointer."""
    root = Path(publication_root)
    task_root = Path(task_root) if task_root is not None else root
    operation_id = f"release-{time.time_ns()}-{os.getpid()}"
    with _workbook_lock(root):
        current = _current_pointer(root, absent_ok=True)
        if current is not None:
            validate_release(
                root / current["release_path"],
                source_root=source_root,
                segmentation_root=segmentation_root,
                task_root=task_root,
                expected_release_id=current["release_id"],
                expected_manifest_sha256=current["manifest_sha256"],
            )
        observed = None if current is None else current["release_id"]
        if observed != expected_current_release_id:
            raise ReleasePublicationError(
                f"stale expected current release: expected "
                f"{expected_current_release_id!r}, observed {observed!r}"
            )
        first = current is None
        if first and expected_current_release_id is not None:
            raise ReleasePublicationError("first v2 release requires expected absent")
        if first:
            prior_release_id = None
            if legacy_snapshot_hash is None:
                raise ReleasePublicationError(
                    "first v2 release requires a frozen legacy snapshot hash"
                )
            _, snapshot = resolve_legacy_snapshot(root)
            observed_snapshot_hash = snapshot.get("snapshot_hash")
            snapshot_identity = snapshot.get("identity") or {}
            if (
                legacy_snapshot_hash != observed_snapshot_hash
                or snapshot_identity.get("workbook_id") != workbook_id
            ):
                raise ReleasePublicationError(
                    "first v2 release does not bind the validated legacy snapshot"
                )
            if snapshot_identity.get("mode") == "absent":
                expected_source_pointer = str(
                    (Path(source_root) / "current.json").resolve()
                )
                expected_segmentation_pointer = str(
                    (Path(segmentation_root) / "current.json").resolve()
                )
                if (
                    snapshot_identity.get("source_pointer_path")
                    != expected_source_pointer
                    or snapshot_identity.get("segmentation_pointer_path")
                    != expected_segmentation_pointer
                ):
                    raise ReleasePublicationError(
                        "first v2 release does not bind the validated absent "
                        "legacy paths"
                    )
        else:
            prior_release_id = observed
            if legacy_snapshot_hash is not None:
                raise ReleasePublicationError(
                    "legacy snapshot binding is only valid on the first v2 release"
                )
        manifest = build_release_manifest(
            workbook_id,
            source_root=source_root,
            source_generation_id=source_generation_id,
            segmentation_root=segmentation_root,
            segmentation_generation_id=segmentation_generation_id,
            task_root=task_root,
            task_generation_id=task_generation_id,
            prior_release_id=prior_release_id,
            legacy_snapshot_hash=legacy_snapshot_hash,
        )
        release_id = manifest["release_id"]
        releases = root / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        destination = releases / release_id
        _journal(root, operation_id, "staging", release_id=release_id)
        if fault:
            fault("before_immutable_release_write")
        if destination.exists():
            existing = validate_release(
                destination,
                source_root=source_root,
                segmentation_root=segmentation_root,
                task_root=task_root,
            )
            if existing != manifest:
                raise ReleasePublicationError("immutable release ID collision")
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".release-stage-", dir=str(root)))
            try:
                (temporary / "release-manifest.json").write_bytes(
                    _canonical_bytes(manifest)
                )
                os.replace(temporary, destination)
                _fsync_directory(releases)
            except BaseException:
                if temporary.exists():
                    shutil.rmtree(temporary)
                raise
        _journal(root, operation_id, "immutable_release_written", release_id=release_id)
        if fault:
            fault("after_immutable_release_write")
        # Revalidate immediately before CAS so no mutable path can be smuggled in.
        validate_release(
            destination,
            source_root=source_root,
            segmentation_root=segmentation_root,
            task_root=task_root,
        )
        pointer = {
            "schema_version": RELEASE_POINTER_SCHEMA_VERSION,
            "release_id": release_id,
            "release_path": f"releases/{release_id}",
            "manifest_sha256": _sha256(destination / "release-manifest.json"),
        }
        if fault:
            fault("before_pointer_cas")
        _atomic_write(root / "current-release.json", _canonical_bytes(pointer))
        _journal(root, operation_id, "pointer_cas", release_id=release_id)
        if fault:
            fault("after_pointer_cas")
        if fault:
            fault("compatibility_materialization")
        if compatibility_paths:
            _materialize_compatibility(
                manifest, compatibility_paths, operation_id=operation_id, root=root
            )
        _journal(root, operation_id, "complete", release_id=release_id)
        return destination, manifest


def validate_release(
    release_dir: str | Path,
    *,
    source_root: str | Path,
    segmentation_root: str | Path,
    task_root: str | Path,
    expected_release_id: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    directory = Path(release_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ReleasePublicationError("release directory is missing or unsafe")
    manifest_path = directory / "release-manifest.json"
    manifest = _read_json(manifest_path, "release manifest")
    if set(path.name for path in directory.iterdir()) != {"release-manifest.json"}:
        raise ReleasePublicationError("release directory contains unbound artifacts")
    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ReleasePublicationError("unsupported release manifest schema")
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or identity.get("schema_version") != (
        RELEASE_IDENTITY_SCHEMA_VERSION
    ):
        raise ReleasePublicationError("release identity schema is unsupported")
    release_id = _require_hash(manifest.get("release_id"), "release ID")
    if release_id != canonical_hash(identity):
        raise ReleasePublicationError("release identity hash mismatch")
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleasePublicationError("release ID changed")
    if (
        expected_manifest_sha256 is not None
        and _sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ReleasePublicationError("release manifest hash mismatch")
    copied = {
        key: manifest.get(key)
        for key in (
            "workbook_id",
            "source_generation",
            "segmentation_generation",
            "task_generation",
            "bindings",
            "versions",
            "prior_release_id",
            "legacy_snapshot_hash",
        )
    }
    if copied != {key: identity.get(key) for key in copied}:
        raise ReleasePublicationError("release manifest identity fields disagree")
    versions = identity.get("versions")
    def version_bundle(health_schema: str, source_policy: str) -> dict:
        return {
            "release_schema_version": RELEASE_SCHEMA_VERSION,
            "release_policy_version": RELEASE_POLICY_VERSION,
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "source_health_schema_version": health_schema,
            "source_policy_version": source_policy,
            "segmentation_schema_version": SEGMENTATION_SCHEMA_VERSION,
            "segmentation_verification_schema_version":
                SEGMENTATION_VERIFICATION_SCHEMA_VERSION,
            "cone_schema_version": (
                CONE_SCHEMA_VERSION
                if (identity.get("bindings") or {}).get(
                    "cone_certificate_sha256"
                ) is not None
                else None
            ),
            "task_schema_version": TASK_SCHEMA_VERSION,
        }

    known_versions = (
        version_bundle(SOURCE_HEALTH_SCHEMA_VERSION, SOURCE_POLICY_VERSION),
        version_bundle(
            PREVIOUS_SOURCE_HEALTH_SCHEMA_VERSION,
            PREVIOUS_SOURCE_POLICY_VERSION,
        ),
    )
    if versions not in known_versions:
        raise ReleasePublicationError("unknown, mixed, or downgraded release versions")
    rebuilt = build_release_manifest(
        identity["workbook_id"],
        source_root=source_root,
        source_generation_id=identity["source_generation"]["generation_id"],
        segmentation_root=segmentation_root,
        segmentation_generation_id=identity["segmentation_generation"]["generation_id"],
        task_root=task_root,
        task_generation_id=identity["task_generation"]["generation_id"],
        prior_release_id=identity.get("prior_release_id"),
        legacy_snapshot_hash=identity.get("legacy_snapshot_hash"),
        source_policy_version=versions["source_policy_version"],
        source_health_schema_version=versions[
            "source_health_schema_version"
        ],
    )
    if rebuilt != manifest:
        raise ReleasePublicationError("release component bytes or bindings changed")
    return manifest


def resolve_current_release(
    publication_root: str | Path,
    *,
    source_root: str | Path,
    segmentation_root: str | Path,
    task_root: str | Path | None = None,
    expected_release_id: str | None = None,
) -> tuple[Path, dict]:
    root = Path(publication_root)
    pointer = _current_pointer(root)
    if expected_release_id is not None and pointer["release_id"] != expected_release_id:
        raise ReleasePublicationError("current release pointer changed")
    releases = root / "releases"
    if releases.is_symlink():
        raise ReleasePublicationError("releases directory is a symlink")
    directory = releases / pointer["release_id"]
    if directory.resolve().parent != releases.resolve():
        raise ReleasePublicationError("release path escapes its root")
    manifest = validate_release(
        directory,
        source_root=source_root,
        segmentation_root=segmentation_root,
        task_root=(Path(task_root) if task_root is not None else root),
        expected_release_id=pointer["release_id"],
        expected_manifest_sha256=pointer["manifest_sha256"],
    )
    return directory, manifest


def _tree_hash(path: Path) -> tuple[str, dict]:
    records = _artifact_records(path)
    return canonical_hash(records), records


def freeze_legacy_snapshot(
    publication_root: str | Path,
    workbook_id: str,
    *,
    source_root: str | Path,
    segmentation_root: str | Path,
    task_dir: str | Path,
) -> tuple[Path, dict]:
    """Freeze the one legacy tuple allowed before the first v2 CAS."""
    root = Path(publication_root)
    with _workbook_lock(root):
        if (root / "current-release.json").exists():
            raise ReleasePublicationError("cannot freeze legacy state after v2 cutover")
        source_pointer = Path(source_root) / "current.json"
        segmentation_pointer = Path(segmentation_root) / "current.json"
        if source_pointer.is_symlink() or segmentation_pointer.is_symlink():
            raise ReleasePublicationError("legacy pointers must not be symlinks")
        source_bytes = source_pointer.read_bytes()
        segmentation_bytes = segmentation_pointer.read_bytes()
        try:
            source_value = json.loads(source_bytes)
            segmentation_value = json.loads(segmentation_bytes)
        except json.JSONDecodeError as exc:
            raise ReleasePublicationError("legacy pointer JSON is invalid") from exc
        if (
            not isinstance(source_value, dict)
            or source_value.get("schema_version") != "source-current/v1"
            or not isinstance(segmentation_value, dict)
            or segmentation_value.get("schema_version") != "segmentation-current/v1"
        ):
            raise ReleasePublicationError(
                "unknown or mixed legacy pointer versions cannot be frozen"
            )
        task_hash, task_artifacts = _tree_hash(Path(task_dir))
        identity = {
            "schema_version": LEGACY_SNAPSHOT_SCHEMA_VERSION,
            "workbook_id": workbook_id,
            "source_pointer_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_pointer": source_value,
            "segmentation_pointer_sha256":
                hashlib.sha256(segmentation_bytes).hexdigest(),
            "segmentation_pointer": segmentation_value,
            "task_sha256": task_hash,
            "task_path": str(Path(task_dir).resolve()),
            "task_artifacts": task_artifacts,
        }
        snapshot_hash = canonical_hash(identity)
        snapshot = {
            "schema_version": LEGACY_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "identity": identity,
        }
        pointer_path = root / "legacy-snapshot.json"
        if pointer_path.exists():
            pointer = _read_json(pointer_path, "legacy snapshot pointer")
            if (
                pointer.get("schema_version") != LEGACY_POINTER_SCHEMA_VERSION
                or pointer.get("snapshot_hash") != snapshot_hash
            ):
                raise ReleasePublicationError(
                    "legacy snapshot is immutable and live legacy state changed"
                )
            directory = root / pointer.get("snapshot_path", "")
            return directory, validate_legacy_snapshot(directory)
        snapshots = root / "legacy-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        directory = snapshots / snapshot_hash
        temporary = Path(tempfile.mkdtemp(prefix=".legacy-stage-", dir=str(root)))
        try:
            (temporary / "legacy-snapshot.json").write_bytes(
                _canonical_bytes(snapshot)
            )
            (temporary / "source-current.json").write_bytes(source_bytes)
            (temporary / "segmentation-current.json").write_bytes(segmentation_bytes)
            os.replace(temporary, directory)
            _fsync_directory(snapshots)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        pointer = {
            "schema_version": LEGACY_POINTER_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "snapshot_path": f"legacy-snapshots/{snapshot_hash}",
            "manifest_sha256": _sha256(directory / "legacy-snapshot.json"),
        }
        _atomic_write(pointer_path, _canonical_bytes(pointer), exclusive=True)
        return directory, validate_legacy_snapshot(directory)


def freeze_absent_legacy_snapshot(
    publication_root: str | Path,
    workbook_id: str,
    *,
    source_root: str | Path,
    segmentation_root: str | Path,
) -> tuple[Path, dict]:
    """Freeze proof that no pre-cutover source or segmentation pointer exists."""
    root = Path(publication_root)
    with _workbook_lock(root):
        if (root / "current-release.json").exists():
            raise ReleasePublicationError("cannot freeze legacy state after v2 cutover")
        source_pointer = Path(source_root) / "current.json"
        segmentation_pointer = Path(segmentation_root) / "current.json"
        for pointer in (source_pointer, segmentation_pointer):
            if pointer.is_symlink() or pointer.exists():
                raise ReleasePublicationError(
                    "absent legacy snapshot requires both legacy pointers absent"
                )
        identity = {
            "schema_version": LEGACY_SNAPSHOT_SCHEMA_VERSION,
            "mode": "absent",
            "workbook_id": workbook_id,
            "source_pointer_path": str(source_pointer.resolve()),
            "segmentation_pointer_path": str(segmentation_pointer.resolve()),
        }
        snapshot_hash = canonical_hash(identity)
        snapshot = {
            "schema_version": LEGACY_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "identity": identity,
        }
        pointer_path = root / "legacy-snapshot.json"
        if pointer_path.exists():
            pointer = _read_json(pointer_path, "legacy snapshot pointer")
            if (
                pointer.get("schema_version") != LEGACY_POINTER_SCHEMA_VERSION
                or pointer.get("snapshot_hash") != snapshot_hash
            ):
                raise ReleasePublicationError(
                    "legacy snapshot is immutable and live legacy state changed"
                )
            directory = root / pointer.get("snapshot_path", "")
            return directory, validate_legacy_snapshot(directory)
        snapshots = root / "legacy-snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        directory = snapshots / snapshot_hash
        temporary = Path(tempfile.mkdtemp(prefix=".legacy-stage-", dir=str(root)))
        try:
            (temporary / "legacy-snapshot.json").write_bytes(
                _canonical_bytes(snapshot)
            )
            os.replace(temporary, directory)
            _fsync_directory(snapshots)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        pointer = {
            "schema_version": LEGACY_POINTER_SCHEMA_VERSION,
            "snapshot_hash": snapshot_hash,
            "snapshot_path": f"legacy-snapshots/{snapshot_hash}",
            "manifest_sha256": _sha256(directory / "legacy-snapshot.json"),
        }
        _atomic_write(pointer_path, _canonical_bytes(pointer), exclusive=True)
        return directory, validate_legacy_snapshot(directory)


def validate_legacy_snapshot(snapshot_dir: str | Path) -> dict:
    directory = Path(snapshot_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise ReleasePublicationError("legacy snapshot is missing or unsafe")
    snapshot = _read_json(directory / "legacy-snapshot.json", "legacy snapshot")
    if snapshot.get("schema_version") != LEGACY_SNAPSHOT_SCHEMA_VERSION:
        raise ReleasePublicationError("unsupported legacy snapshot schema")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict) or snapshot.get("snapshot_hash") != (
        canonical_hash(identity)
    ):
        raise ReleasePublicationError("legacy snapshot identity hash mismatch")
    if identity.get("mode") == "absent":
        if {path.name for path in directory.iterdir()} != {"legacy-snapshot.json"}:
            raise ReleasePublicationError("absent legacy snapshot file set changed")
        for key in ("source_pointer_path", "segmentation_pointer_path"):
            value = identity.get(key)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ReleasePublicationError(
                    "absent legacy snapshot path is invalid"
                )
            path = Path(value)
            if path.is_symlink() or path.exists():
                raise ReleasePublicationError(
                    "legacy state appeared after its absence was frozen"
                )
        return snapshot
    expected = {
        "legacy-snapshot.json",
        "source-current.json",
        "segmentation-current.json",
    }
    if {path.name for path in directory.iterdir()} != expected:
        raise ReleasePublicationError("legacy snapshot file set changed")
    if hashlib.sha256((directory / "source-current.json").read_bytes()).hexdigest() != (
        identity.get("source_pointer_sha256")
    ):
        raise ReleasePublicationError("frozen legacy source pointer changed")
    if _read_json(
        directory / "source-current.json", "frozen legacy source pointer"
    ) != identity.get("source_pointer"):
        raise ReleasePublicationError("frozen legacy source pointer was reinterpreted")
    if hashlib.sha256(
        (directory / "segmentation-current.json").read_bytes()
    ).hexdigest() != identity.get("segmentation_pointer_sha256"):
        raise ReleasePublicationError("frozen legacy segmentation pointer changed")
    if _read_json(
        directory / "segmentation-current.json",
        "frozen legacy segmentation pointer",
    ) != identity.get("segmentation_pointer"):
        raise ReleasePublicationError(
            "frozen legacy segmentation pointer was reinterpreted"
        )
    task = Path(identity.get("task_path", ""))
    task_hash, task_artifacts = _tree_hash(task)
    if (
        task_hash != identity.get("task_sha256")
        or task_artifacts != identity.get("task_artifacts")
    ):
        raise ReleasePublicationError("legacy task bytes changed")
    return snapshot


def resolve_legacy_snapshot(publication_root: str | Path) -> tuple[Path, dict]:
    root = Path(publication_root)
    if (root / "current-release.json").exists():
        raise ReleasePublicationError("legacy fallback is disabled after v2 cutover")
    pointer_path = root / "legacy-snapshot.json"
    if pointer_path.is_symlink() or not pointer_path.is_file():
        raise ReleasePublicationError("legacy snapshot pointer is missing or unsafe")
    pointer = _read_json(pointer_path, "legacy snapshot pointer")
    if pointer.get("schema_version") != LEGACY_POINTER_SCHEMA_VERSION:
        raise ReleasePublicationError("unsupported legacy snapshot pointer schema")
    snapshot_hash = _require_hash(pointer.get("snapshot_hash"), "legacy snapshot hash")
    if pointer.get("snapshot_path") != f"legacy-snapshots/{snapshot_hash}":
        raise ReleasePublicationError("legacy snapshot path is unsafe")
    directory = root / pointer["snapshot_path"]
    if pointer.get("manifest_sha256") != _sha256(
        directory / "legacy-snapshot.json"
    ):
        raise ReleasePublicationError("legacy snapshot manifest hash mismatch")
    snapshot = validate_legacy_snapshot(directory)
    if snapshot.get("snapshot_hash") != snapshot_hash:
        raise ReleasePublicationError("legacy snapshot pointer hash mismatch")
    return directory, snapshot


def resolve_effective_release(
    publication_root: str | Path,
    *,
    source_root: str | Path,
    segmentation_root: str | Path,
    task_root: str | Path | None = None,
    expected_release_id: str | None = None,
    allow_frozen_legacy: bool = True,
) -> dict:
    """Resolve exactly one complete v2 tuple, or a frozen pre-cutover tuple."""
    root = Path(publication_root)
    if (root / "current-release.json").exists():
        directory, manifest = resolve_current_release(
            root,
            source_root=source_root,
            segmentation_root=segmentation_root,
            task_root=task_root,
            expected_release_id=expected_release_id,
        )
        return {"mode": "v2", "release_dir": str(directory), "manifest": manifest}
    if expected_release_id is not None or not allow_frozen_legacy:
        raise ReleasePublicationError("current v2 release is absent")
    directory, snapshot = resolve_legacy_snapshot(root)
    return {"mode": "frozen-v1", "snapshot_dir": str(directory), "snapshot": snapshot}


def resolve_build_context(
    workbook_id: str,
    *,
    release_root: str | Path | None = None,
    release_id: str | None = None,
    source_root: str | Path,
    segmentation_root: str | Path,
    source_generation_id: str | None = None,
    segmentation_generation_id: str | None = None,
    task_root: str | Path | None = None,
) -> dict:
    """Resolve once for downstream work; never consult source/seg current pointers."""
    from xl_source_publication import resolve_source_generation_by_id
    from xl_seg.publication import resolve_generation_by_id

    if release_root is not None:
        _, release = resolve_current_release(
            release_root,
            source_root=source_root,
            segmentation_root=segmentation_root,
            task_root=(task_root or release_root),
            expected_release_id=release_id,
        )
        source_generation_id = release["source_generation"]["generation_id"]
        segmentation_generation_id = release["segmentation_generation"]["generation_id"]
        release_id = release["release_id"]
        pinned_task_generation_id = release["task_generation"]["generation_id"]
        pinned_task_manifest_sha256 = release["task_generation"]["manifest_sha256"]
    elif source_generation_id is None or segmentation_generation_id is None:
        raise ReleasePublicationError(
            "downstream build requires one pinned release or both inactive generation IDs"
        )
    else:
        pinned_task_generation_id = None
        pinned_task_manifest_sha256 = None
    source_dir, source = resolve_source_generation_by_id(
        source_root, source_generation_id
    )
    layout = source["layout"]
    if layout.get("workbook_id") != workbook_id:
        raise ReleasePublicationError("pinned source workbook ID mismatch")
    source_path = source_dir / layout["source_workbook"]
    ast_dir = source_dir / layout["ast_directory"]
    segmentation_dir, segmentation = resolve_generation_by_id(
        segmentation_root,
        segmentation_generation_id,
        require_pass=True,
        source_generation_dir=source_dir,
    )
    source_binding = (
        segmentation.get("source_policy_bindings") or {}
    ).get("source_generation") or {}
    if source_binding.get("generation_id") != source_generation_id:
        raise ReleasePublicationError("pinned segmentation/source IDs do not bind")
    source_bindings = source["bindings"]
    policy = segmentation.get("source_policy_bindings") or {}
    bindings = {
        "release_id": release_id,
        "source_generation_id": source_generation_id,
        "source_manifest_sha256": _sha256(source_dir / "generation-manifest.json"),
        "source_health_sha256": source_bindings.get("health_sha256"),
        "source_health_report_sha256": source_bindings.get("health_report_sha256"),
        "restriction_evidence_sha256":
            source_bindings.get("restriction_evidence_sha256"),
        "restriction_events_sha256":
            source_bindings.get("restriction_events_sha256"),
        "restriction_profile_sha256":
            source_bindings.get("restriction_profile_sha256"),
        "inventory_approval_sha256":
            source_bindings.get("inventory_approval_sha256"),
        "recalc_signals_sha256":
            source_bindings.get("recalc_signals_sha256"),
        "segmentation_generation_id": segmentation_generation_id,
        "segmentation_manifest_sha256":
            _sha256(segmentation_dir / "generation-manifest.json"),
        "cone_certificate_sha256": policy.get("cone_certificate_sha256"),
        "task_generation_id": pinned_task_generation_id,
        "task_manifest_sha256": pinned_task_manifest_sha256,
        "task_generation_hash": pinned_task_manifest_sha256,
    }
    return {
        "workbook_id": workbook_id,
        "release_id": release_id,
        "source_dir": str(source_dir),
        "source_path": str(source_path),
        "source_root": str(source_dir / layout["source_root"]),
        "source_sha256": source_bindings.get("source_sha256"),
        "ast_dir": str(ast_dir),
        "ast_root": str(source_dir / layout["ast_root"]),
        "segmentation_dir": str(segmentation_dir),
        "bindings": bindings,
        "source_manifest": source,
        "segmentation_manifest": segmentation,
    }


def _materialize_compatibility(
    manifest: dict,
    paths: dict[str, str | Path],
    *,
    operation_id: str,
    root: Path,
) -> None:
    sources = {
        "source": Path(manifest["source_generation"]["generation_path"]),
        "segmentation": Path(manifest["segmentation_generation"]["generation_path"]),
        "task": Path(manifest["task_generation"]["generation_path"]),
    }
    for kind, destination_value in paths.items():
        if kind not in sources:
            raise ReleasePublicationError(f"unknown compatibility target: {kind}")
        destination = Path(destination_value)
        temporary = destination.with_name(
            f".{destination.name}.release-{manifest['release_id']}.tmp"
        )
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(sources[kind], temporary)
        if destination.exists():
            backup = destination.with_name(
                f".{destination.name}.previous-{operation_id}"
            )
            os.replace(destination, backup)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        _journal(root, operation_id, "compatibility_materialized", kind=kind)


def _context_output(context: dict) -> dict:
    return {
        key: value
        for key, value in context.items()
        if key not in {"source_manifest", "segmentation_manifest"}
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-legacy")
    freeze.add_argument("release_root")
    freeze.add_argument("workbook_id")
    freeze.add_argument("--source-root", required=True)
    freeze.add_argument("--segmentation-root", required=True)
    freeze.add_argument("--task-dir", required=True)
    freeze_absent = commands.add_parser("freeze-absent-legacy")
    freeze_absent.add_argument("release_root")
    freeze_absent.add_argument("workbook_id")
    freeze_absent.add_argument("--source-root", required=True)
    freeze_absent.add_argument("--segmentation-root", required=True)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("release_root")
    resolve.add_argument("workbook_id")
    resolve.add_argument("--source-root", required=True)
    resolve.add_argument("--segmentation-root", required=True)
    resolve.add_argument("--task-root")
    resolve.add_argument("--release-id")
    candidate = commands.add_parser("resolve-candidate")
    candidate.add_argument("workbook_id")
    candidate.add_argument("--source-root", required=True)
    candidate.add_argument("--source-generation-id", required=True)
    candidate.add_argument("--segmentation-root", required=True)
    candidate.add_argument("--segmentation-generation-id", required=True)
    task = commands.add_parser("publish-task")
    task.add_argument("task_dir")
    task.add_argument("release_root")
    task.add_argument("workbook_id")
    task.add_argument("--bindings", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("release_root")
    publish.add_argument("workbook_id")
    publish.add_argument("--source-root", required=True)
    publish.add_argument("--source-generation-id", required=True)
    publish.add_argument("--segmentation-root", required=True)
    publish.add_argument("--segmentation-generation-id", required=True)
    publish.add_argument("--task-root")
    publish.add_argument("--task-generation-id", required=True)
    publish.add_argument("--expected-current-release-id")
    publish.add_argument("--expect-absent", action="store_true")
    publish.add_argument("--legacy-snapshot-hash")
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze-legacy":
            directory, value = freeze_legacy_snapshot(
                args.release_root,
                args.workbook_id,
                source_root=args.source_root,
                segmentation_root=args.segmentation_root,
                task_dir=args.task_dir,
            )
            result = {"snapshot_dir": str(directory), **value}
        elif args.command == "freeze-absent-legacy":
            directory, value = freeze_absent_legacy_snapshot(
                args.release_root,
                args.workbook_id,
                source_root=args.source_root,
                segmentation_root=args.segmentation_root,
            )
            result = {"snapshot_dir": str(directory), **value}
        elif args.command == "resolve":
            result = _context_output(resolve_build_context(
                args.workbook_id,
                release_root=args.release_root,
                release_id=args.release_id,
                source_root=args.source_root,
                segmentation_root=args.segmentation_root,
                task_root=args.task_root,
            ))
        elif args.command == "resolve-candidate":
            result = _context_output(resolve_build_context(
                args.workbook_id,
                source_root=args.source_root,
                source_generation_id=args.source_generation_id,
                segmentation_root=args.segmentation_root,
                segmentation_generation_id=args.segmentation_generation_id,
            ))
        elif args.command == "publish-task":
            directory, value = publish_task_generation(
                args.task_dir,
                args.release_root,
                args.workbook_id,
                bindings=_read_json(Path(args.bindings), "task bindings"),
            )
            result = {"task_generation_dir": str(directory), **value}
        else:
            if args.expect_absent and args.expected_current_release_id is not None:
                parser.error("--expect-absent conflicts with --expected-current-release-id")
            if not args.expect_absent and args.expected_current_release_id is None:
                parser.error(
                    "pass --expect-absent or --expected-current-release-id"
                )
            directory, value = publish_release(
                args.release_root,
                args.workbook_id,
                source_root=args.source_root,
                source_generation_id=args.source_generation_id,
                segmentation_root=args.segmentation_root,
                segmentation_generation_id=args.segmentation_generation_id,
                task_root=args.task_root,
                task_generation_id=args.task_generation_id,
                expected_current_release_id=args.expected_current_release_id,
                legacy_snapshot_hash=args.legacy_snapshot_hash,
            )
            result = {"release_dir": str(directory), **value}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"release publication FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
