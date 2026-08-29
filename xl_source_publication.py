"""Fail-closed publication for source workbooks and production AST artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path


MANIFEST_SCHEMA_VERSION = "source-generation/v2"
LEGACY_MANIFEST_SCHEMA_VERSION = "source-generation/v1"
POINTER_SCHEMA_VERSION = "source-current/v1"
PROVENANCE_SCHEMA_VERSION = "ast-provenance/v2"
GENERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
AST_FILES = ("nodes.csv", "edges.csv")
AST_SIDECARS = frozenset({"ast-provenance.json"})


class SourcePublicationError(ValueError):
    """A source generation is incomplete, mixed, unsafe, or tampered."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_record(path: Path, logical_path: str) -> dict:
    return {
        "algorithm": "sha256",
        "path": logical_path,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _json_value(value: dict | str | Path, description: str) -> dict:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourcePublicationError(f"cannot read {description}: {exc}") from exc
    if not isinstance(result, dict):
        raise SourcePublicationError(f"{description} must be a JSON object")
    return result


def _default_builder_paths() -> tuple[Path, ...]:
    return (Path(__file__).resolve().with_name("xl_ast_graph.py"),)


def _validate_ast_directory(ast_dir: Path, *, require_two_csv: bool = True) -> None:
    if ast_dir.is_symlink() or not ast_dir.is_dir():
        raise SourcePublicationError("AST directory is missing or is a symlink")
    actual = set()
    for child in ast_dir.iterdir():
        if child.is_symlink():
            raise SourcePublicationError(f"AST artifact is a symlink: {child.name}")
        if child.is_file():
            actual.add(child.name)
        elif child.is_dir():
            actual.add(child.name + "/")
    missing = set(AST_FILES) - actual
    if missing:
        raise SourcePublicationError(f"AST artifacts are missing: {sorted(missing)}")
    unexpected = actual - set(AST_FILES) - AST_SIDECARS
    if require_two_csv and unexpected:
        raise SourcePublicationError(
            "production AST has files outside its two-CSV contract: "
            f"{sorted(unexpected)}"
        )


def create_ast_provenance(
    source_path: str | Path,
    ast_dir: str | Path,
    *,
    builder_code_paths: tuple[str | Path, ...] | list[str | Path] | None = None,
    builder_args: object = None,
    require_two_csv: bool = True,
) -> dict:
    """Bind source bytes, builder implementation/arguments, and both AST CSVs."""
    source = Path(source_path)
    ast = Path(ast_dir)
    if source.is_symlink() or not source.is_file():
        raise SourcePublicationError("source workbook is missing or is a symlink")
    _validate_ast_directory(ast, require_two_csv=require_two_csv)
    code_paths = tuple(
        Path(path)
        for path in (
            builder_code_paths
            if builder_code_paths is not None
            else _default_builder_paths()
        )
    )
    if not code_paths:
        raise SourcePublicationError("builder code paths are required")
    code = []
    for path in code_paths:
        if path.is_symlink() or not path.is_file():
            raise SourcePublicationError(
                f"builder code is missing or is a symlink: {path}"
            )
        code.append({
            "name": path.name,
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        })
    code.sort(key=lambda item: (item["name"], item["sha256"]))
    args = [] if builder_args is None else builder_args
    args_bytes = _canonical_bytes(args)
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source": {
            "sha256": _sha256(source),
            "size_bytes": source.stat().st_size,
        },
        "builder": {
            "code": code,
            "code_sha256": _bytes_hash(_canonical_bytes(code)),
            "args": args,
            "args_sha256": _bytes_hash(args_bytes),
        },
        "ast": {
            name: _file_record(ast / name, f"ast/{name}")
            for name in AST_FILES
        },
    }


def validate_ast_provenance(
    source_path: str | Path,
    ast_dir: str | Path,
    provenance: dict | str | Path,
    *,
    builder_code_paths: tuple[str | Path, ...] | list[str | Path] | None = None,
    builder_args: object = None,
    require_two_csv: bool = True,
) -> dict:
    record = _json_value(provenance, "AST provenance")
    failures = []
    if record.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        failures.append("schema_version")
    source = Path(source_path)
    ast = Path(ast_dir)
    try:
        _validate_ast_directory(ast, require_two_csv=require_two_csv)
    except SourcePublicationError:
        failures.append("ast_contract")
    source_record = record.get("source") or {}
    if (
        source.is_symlink()
        or not source.is_file()
        or source_record.get("sha256") != _sha256(source)
        or source_record.get("size_bytes") != source.stat().st_size
    ):
        failures.append("source")
    ast_record = record.get("ast") or {}
    for name in AST_FILES:
        path = ast / name
        expected = ast_record.get(name) or {}
        if (
            not path.is_file()
            or path.is_symlink()
            or expected.get("sha256") != _sha256(path)
            or expected.get("size_bytes") != path.stat().st_size
        ):
            failures.append(name)
    builder = record.get("builder") or {}
    args = builder.get("args")
    if builder.get("args_sha256") != _bytes_hash(_canonical_bytes(args)):
        failures.append("builder_args")
    if builder_args is not None and args != builder_args:
        failures.append("expected_builder_args")
    code = builder.get("code")
    if (
        not isinstance(code, list)
        or builder.get("code_sha256") != _bytes_hash(_canonical_bytes(code))
    ):
        failures.append("builder_code")
    recorded_code_paths = []
    if isinstance(code, list):
        for item in code:
            path_text = item.get("path") if isinstance(item, dict) else None
            if not isinstance(path_text, str) or not path_text:
                failures.append("builder_code_path")
                continue
            recorded_code_paths.append(Path(path_text))
    live_code_paths = (
        tuple(Path(path) for path in builder_code_paths)
        if builder_code_paths is not None
        else tuple(recorded_code_paths)
    )
    if live_code_paths:
        live = create_ast_provenance(
            source,
            ast,
            builder_code_paths=live_code_paths,
            builder_args=args,
            require_two_csv=require_two_csv,
        )
        if live["builder"] != builder:
            failures.append("live_builder_code")
    if failures:
        raise SourcePublicationError(
            "AST provenance validation failed: " + ", ".join(sorted(set(failures)))
        )
    return record


def validate_bound_ast_if_required(
    source_path: str | Path,
    ast_dir: str | Path,
    *,
    require: bool = False,
) -> dict | None:
    """Validate provenance whenever paths belong to a source generation."""
    source = Path(source_path)
    ast = Path(ast_dir)
    generation_root = source.parent.parent
    generation_bound = (
        generation_root == ast.parent.parent
        and (generation_root / "generation-manifest.json").is_file()
    )
    candidates = (
        generation_root / "ast-provenance.json",
        ast / "ast-provenance.json",
    )
    provenance = next((path for path in candidates if path.is_file()), None)
    if provenance is None:
        if generation_bound or require:
            raise SourcePublicationError(
                "source generation is missing required AST provenance"
            )
        return None
    return validate_ast_provenance(source, ast, provenance)


def _binding_hash(value: dict) -> str:
    return _bytes_hash(_canonical_bytes(value))


def _bound_source_hash(value: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    source = value.get("source")
    if isinstance(source, dict) and isinstance(source.get("sha256"), str):
        return source["sha256"]
    output = value.get("output")
    if isinstance(output, dict) and isinstance(output.get("sha256"), str):
        return output["sha256"]
    return None


def _make_manifest(
    stage: Path,
    provenance: dict,
    layout: dict,
    health: dict,
    result: dict,
    inventory: dict | None,
) -> dict:
    artifacts = {}
    for path in sorted(stage.rglob("*")):
        if path.is_symlink():
            raise SourcePublicationError(
                f"generation artifact is a symlink: {path.relative_to(stage)}"
            )
        if path.is_file() and path.name != "generation-manifest.json":
            relative = path.relative_to(stage).as_posix()
            artifacts[relative] = _file_record(path, relative)
    restricted = health.get("route") == "restricted_pass"
    policy_decision = {
        "health_report_sha256": health.get("report_sha256"),
        "policy_version": health.get("policy_version"),
        "route": health.get("route"),
    }
    if restricted:
        policy_decision.update({
            "inventory_sha256": inventory.get("inventory_sha256"),
            "restriction_evidence_sha256": result.get("result_sha256"),
            "restriction_events_sha256": health.get(
                "restriction_events_sha256"
            ),
            "restriction_profile": health.get("restriction_profile"),
            "restriction_profile_sha256": _binding_hash(
                health.get("restriction_profile")
            ),
        })
    identity = {
        "schema_version": "source-generation-identity/v2",
        "source_sha256": artifacts[layout["source_workbook"]]["sha256"],
        "policy_version": health.get("policy_version"),
        "policy_decision": policy_decision,
        "engine": {
            "name": result.get("engine"),
            "version": result.get("engine_version"),
            "mode": result.get("mode", "recalculated"),
        },
        "builder": provenance["builder"],
        "nodes_sha256": artifacts[f'{layout["ast_directory"]}/nodes.csv'][
            "sha256"
        ],
        "edges_sha256": artifacts[f'{layout["ast_directory"]}/edges.csv'][
            "sha256"
        ],
    }
    bindings = {
        "request_sha256": artifacts["request.json"]["sha256"],
        "result_sha256": artifacts["result.json"]["sha256"],
        "health_sha256": artifacts["health.json"]["sha256"],
        "health_report_sha256": health.get("report_sha256"),
        "ast_provenance_sha256": artifacts["ast-provenance.json"]["sha256"],
        "source_sha256": artifacts[layout["source_workbook"]]["sha256"],
        "nodes_sha256": artifacts[
            f'{layout["ast_directory"]}/nodes.csv'
        ]["sha256"],
        "edges_sha256": artifacts[
            f'{layout["ast_directory"]}/edges.csv'
        ]["sha256"],
    }
    if restricted:
        bindings.update({
            "inventory_artifact_sha256": artifacts["inventory.json"]["sha256"],
            "inventory_sha256": inventory["inventory_sha256"],
            "restriction_evidence_sha256": result["result_sha256"],
            "restriction_events_sha256": health["restriction_events_sha256"],
            "restriction_profile_sha256": _binding_hash(
                health["restriction_profile"]
            ),
        })
    core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifacts": artifacts,
        "layout": layout,
        "identity": identity,
        "bindings": bindings,
        "ast_provenance": provenance,
    }
    return {**core, "generation_id": _binding_hash(identity)}


def publish_source_generation(
    source_path: str | Path,
    ast_dir: str | Path,
    publication_root: str | Path,
    *,
    request: dict | str | Path,
    result: dict | str | Path,
    health: dict | str | Path,
    ast_provenance: dict | str | Path | None = None,
    builder_code_paths: tuple[str | Path, ...] | list[str | Path] | None = None,
    builder_args: object = None,
    activate: bool = False,
    trusted_runner_public_key: str | Path | None = None,
    original_source_path: str | Path | None = None,
    inventory: dict | str | Path | None = None,
    fault=None,
) -> tuple[Path, dict]:
    """Publish immutable bytes; activation is a separate proof-gated operation."""
    source = Path(source_path)
    ast = Path(ast_dir)
    root = Path(publication_root)
    if activate:
        raise SourcePublicationError(
            "publication cannot activate; use activate with strict segmentation proof"
        )
    if source.suffix.casefold() != ".xlsx" or not source.stem:
        raise SourcePublicationError("source generation requires a named .xlsx file")
    workbook_id = source.stem
    layout = {
        "workbook_id": workbook_id,
        "source_root": "source",
        "source_workbook": f"source/{source.name}",
        "ast_root": "ast",
        "ast_directory": f"ast/{workbook_id}",
    }
    root.mkdir(parents=True, exist_ok=True)
    request_value = _json_value(request, "recalculation request")
    result_value = _json_value(result, "recalculation result")
    health_value = _json_value(health, "source-health report")
    inventory_value = (
        None if inventory is None else _json_value(inventory, "source inventory")
    )
    provenance = (
        create_ast_provenance(
            source,
            ast,
            builder_code_paths=builder_code_paths,
            builder_args=builder_args,
        )
        if ast_provenance is None and not (ast / "ast-provenance.json").is_file()
        else validate_ast_provenance(
            source,
            ast,
            (
                ast / "ast-provenance.json"
                if ast_provenance is None
                else ast_provenance
            ),
            builder_code_paths=builder_code_paths,
            builder_args=builder_args,
        )
    )
    source_hash = _sha256(source)
    health_hash = _bound_source_hash(health_value, ("source_sha256",))
    result_hash = _bound_source_hash(
        result_value, ("output_sha256", "destination_sha256")
    )
    if health_hash != source_hash:
        raise SourcePublicationError("health report is not bound to source workbook")
    if result_hash is not None and result_hash != source_hash:
        raise SourcePublicationError("result is not bound to source workbook")
    route = health_value.get("route")
    if route not in {"pass", "restricted_pass", "recalc_candidate"}:
        raise SourcePublicationError(
            "source health does not permit generation publication"
        )
    for value, signature, description in (
        (request_value, "request_sha256", "request"),
        (result_value, "result_sha256", "result"),
    ):
        if signature not in value:
            raise SourcePublicationError(f"{description} self-hash is missing")
        unsigned = dict(value)
        claimed = unsigned.pop(signature)
        if claimed != _binding_hash(unsigned):
            raise SourcePublicationError(
                f"{description} self-hash validation failed"
            )
    if (
        request_value.get("request_id") != result_value.get("request_id")
        or result_value.get("request_sha256")
        != request_value.get("request_sha256")
        or result_value.get("source_sha256")
        != request_value.get("source_sha256")
    ):
        raise SourcePublicationError(
            "recalculation request/result binding validation failed"
        )
    from xl_source_health import inspect_workbook, validate_report

    try:
        validate_report(health_value, source)
        if health_value != inspect_workbook(source):
            raise ValueError(
                "source-health report does not match fresh source observation"
            )
    except ValueError as exc:
        raise SourcePublicationError(
            f"health report validation failed: {exc}"
        ) from exc
    if health_value.get("schema_version") != "xlsx-source-health/v2":
        raise SourcePublicationError(
            "new source generations require v2 source health"
        )
    identity_pair = (
        request_value.get("schema_version") == "source-identity-request/v2"
        and result_value.get("schema_version") == "source-identity-result/v2"
        and result_value.get("mode") == "identity"
    )
    engine_evidence = result_value.get("engine_evidence") or {}
    controls = engine_evidence.get("controls") or {}
    runner_receipt = engine_evidence.get("runner_receipt") or {}
    engine_constraints = request_value.get("engine_constraints") or {}
    permitted_versions = engine_constraints.get("permitted_versions")
    allowed_engines = engine_constraints.get("allowed_engines")
    recalc_pair = (
        request_value.get("schema_version") == "source-recalc-request/v2"
        and result_value.get("schema_version") == "source-recalc-result/v2"
        and result_value.get("authoritative") is True
        and result_value.get("policy_version") == health_value.get("policy_version")
        and isinstance(result_value.get("engine"), str)
        and isinstance(result_value.get("engine_version"), str)
        and isinstance(permitted_versions, list)
        and len(permitted_versions) >= 1
        and "*" not in permitted_versions
        and result_value.get("engine_version") in permitted_versions
        and isinstance(allowed_engines, list)
        and result_value.get("engine") in allowed_engines
        and engine_constraints.get("required_engine")
        == result_value.get("engine")
        and (result_value.get("semantic_diff") or {}).get(
            "supported_equivalent_semantics"
        )
        is True
        and all(
            controls.get(key) is True
            for key in (
                "dedicated_session",
                "network_disabled",
                "macros_disabled",
                "add_ins_disabled",
                "link_updates_disabled",
                "prompts_suppressed",
            )
        )
        and isinstance(request_value.get("created_at_ns"), int)
        and isinstance(request_value.get("expires_at_ns"), int)
        and isinstance(result_value.get("completed_at_ns"), int)
        and request_value["created_at_ns"]
        <= result_value["completed_at_ns"]
        <= request_value["expires_at_ns"]
        and time.time_ns() <= request_value["expires_at_ns"]
    )
    if recalc_pair:
        if trusted_runner_public_key is None or original_source_path is None:
            recalc_pair = False
        else:
            try:
                from xl_source_recalc import (
                    semantic_workbook_diff,
                    verify_signed_runner_receipt,
                )
                from xl_source_health import _open_regular_nofollow

                trusted_key_hash = _sha256(Path(trusted_runner_public_key))
                original_source = Path(original_source_path)
                with tempfile.TemporaryDirectory(
                    prefix=".original-source-snapshot-",
                    dir=str(root),
                ) as snapshot_root:
                    original_snapshot = Path(snapshot_root) / "original.xlsx"
                    with _open_regular_nofollow(original_source) as source_handle:
                        with original_snapshot.open("xb") as snapshot_handle:
                            shutil.copyfileobj(source_handle, snapshot_handle)
                            snapshot_handle.flush()
                            os.fsync(snapshot_handle.fileno())
                    if (
                        _sha256(original_snapshot)
                        != request_value.get("source_sha256")
                    ):
                        raise ValueError(
                            "request-bound original source is unavailable"
                        )
                    observed_diff = semantic_workbook_diff(
                        original_snapshot,
                        source,
                    )
                if (
                    observed_diff.get("supported_equivalent_semantics") is not True
                    or observed_diff != result_value.get("semantic_diff")
                ):
                    raise ValueError(
                        "semantic diff does not match independent comparison"
                    )
                if (
                    request_value.get("engine_constraints", {}).get(
                        "trusted_runner_public_key_sha256"
                    )
                    != trusted_key_hash
                ):
                    raise ValueError("request trust anchor does not match")
                signed_payload = verify_signed_runner_receipt(
                    runner_receipt,
                    trusted_runner_public_key,
                    request_sha256=request_value["request_sha256"],
                    source_sha256=request_value["source_sha256"],
                    output_sha256=source_hash,
                )
                if (
                    signed_payload.get("engine") != result_value.get("engine")
                    or signed_payload.get("engine_version")
                    != result_value.get("engine_version")
                    or signed_payload.get("completed_at_ns")
                    != result_value.get("completed_at_ns")
                ):
                    raise ValueError("signed runner claims do not match result")
            except (OSError, ValueError) as exc:
                raise SourcePublicationError(
                    f"trusted runner receipt validation failed: {exc}"
                ) from exc
    if route == "recalc_candidate" and not recalc_pair:
        raise SourcePublicationError(
            "recalculation candidate requires authoritative recalculation evidence"
        )
    if route == "pass" and not (identity_pair or recalc_pair):
        raise SourcePublicationError("source generation has invalid evidence schemas")
    restricted_pair = (
        request_value.get("schema_version") == "source-restriction-request/v2"
        and result_value.get("schema_version") == "source-restriction-result/v2"
        and request_value.get("mode") == "restricted_policy"
        and result_value.get("mode") == "restricted"
    )
    if route == "restricted_pass":
        if not restricted_pair or inventory_value is None:
            raise SourcePublicationError(
                "restricted pass requires dedicated restriction evidence and inventory"
            )
        try:
            from xl_source_inventory import validate_inventory_manifest
            from xl_source_recalc import create_restriction_documents

            validate_inventory_manifest(inventory_value)
            expected_request, expected_result = create_restriction_documents(
                source, health_value, inventory_value
            )
            if (
                request_value != expected_request
                or result_value != expected_result
            ):
                raise ValueError(
                    "restriction evidence does not match independent observation"
                )
        except ValueError as exc:
            raise SourcePublicationError(
                f"restriction evidence validation failed: {exc}"
            ) from exc
    elif restricted_pair or inventory_value is not None:
        raise SourcePublicationError(
            "restriction evidence cannot be reinterpreted for another route"
        )

    stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=str(root)))
    try:
        (stage / "source").mkdir()
        shutil.copyfile(source, stage / layout["source_workbook"])
        generation_ast = stage / layout["ast_directory"]
        generation_ast.mkdir(parents=True)
        for name in AST_FILES:
            shutil.copyfile(ast / name, generation_ast / name)
        documents = {
            "request.json": request_value,
            "result.json": result_value,
            "health.json": health_value,
            "ast-provenance.json": provenance,
        }
        if route == "restricted_pass":
            documents["inventory.json"] = inventory_value
        for name, value in documents.items():
            (stage / name).write_bytes(_canonical_bytes(value))
        manifest = _make_manifest(
            stage,
            provenance,
            layout,
            health_value,
            result_value,
            inventory_value,
        )
        (stage / "generation-manifest.json").write_bytes(_canonical_bytes(manifest))
        validate_source_generation(stage)
        if fault:
            fault("staged")

        generations = root / "generations"
        generations.mkdir(exist_ok=True)
        destination = generations / manifest["generation_id"]
        if destination.exists():
            existing = validate_source_generation(destination)
            if existing.get("identity") != manifest.get("identity"):
                raise SourcePublicationError(
                    "immutable generation ID collision with different content"
                )
            shutil.rmtree(stage)
            manifest = existing
        else:
            os.replace(stage, destination)
            _fsync_directory(generations)
        if fault:
            fault("generation_published")
        request_id = str(request_value.get("request_id", "unknown"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", request_id):
            raise SourcePublicationError("request ID is unsafe for receipt storage")
        receipt_root = root / "receipts" / manifest["generation_id"] / request_id
        receipt_root.mkdir(parents=True, exist_ok=True)
        _atomic_write(receipt_root / "request.json", _canonical_bytes(request_value))
        _atomic_write(receipt_root / "result.json", _canonical_bytes(result_value))
        for path in destination.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return destination, manifest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourcePublicationError(f"cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourcePublicationError(f"{description} must be an object")
    return value


def validate_source_generation(
    generation_dir: str | Path,
    *,
    expected_generation_id: str | None = None,
) -> dict:
    directory = Path(generation_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise SourcePublicationError("source generation is missing or is a symlink")
    manifest = _read_json(
        directory / "generation-manifest.json", "source generation manifest"
    )
    failures = []
    manifest_schema = manifest.get("schema_version")
    if manifest_schema not in {
        LEGACY_MANIFEST_SCHEMA_VERSION,
        MANIFEST_SCHEMA_VERSION,
    }:
        failures.append("schema_version")
    generation_id = manifest.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        failures.append("generation_id")
    if expected_generation_id is not None and generation_id != expected_generation_id:
        failures.append("expected_generation_id")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        failures.append("artifacts")
        artifacts = {}
    expected_files = set(artifacts) | {"generation-manifest.json"}
    actual_files = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink() or not (
            path.is_file() or stat.S_ISDIR(path.lstat().st_mode)
        ):
            failures.append(f"unsafe:{relative}")
        if path.is_file():
            actual_files.add(relative)
    if actual_files != expected_files:
        failures.append("artifact_set")
    for relative, record in artifacts.items():
        path = directory / relative
        if (
            not isinstance(record, dict)
            or not path.is_file()
            or record.get("path") != relative
            or record.get("sha256") != _sha256(path)
            or record.get("size_bytes") != path.stat().st_size
        ):
            failures.append(f"artifact:{relative}")
    core = {
        "schema_version": manifest.get("schema_version"),
        "artifacts": manifest.get("artifacts"),
        "layout": manifest.get("layout"),
        "identity": manifest.get("identity"),
        "bindings": manifest.get("bindings"),
        "ast_provenance": manifest.get("ast_provenance"),
    }
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or generation_id != _binding_hash(identity):
        failures.append("generation_binding")
    bindings = manifest.get("bindings") or {}
    layout = manifest.get("layout") or {}
    source_relative = layout.get("source_workbook", "")
    ast_relative = layout.get("ast_directory", "")
    if (
        not isinstance(source_relative, str)
        or not source_relative.startswith("source/")
        or not isinstance(ast_relative, str)
        or not ast_relative.startswith("ast/")
        or layout.get("workbook_id") != Path(source_relative).stem
    ):
        failures.append("layout")
    expected_bindings = {
        "request_sha256": artifacts.get("request.json", {}).get("sha256"),
        "result_sha256": artifacts.get("result.json", {}).get("sha256"),
        "health_sha256": artifacts.get("health.json", {}).get("sha256"),
        "ast_provenance_sha256": artifacts.get("ast-provenance.json", {}).get(
            "sha256"
        ),
        "source_sha256": artifacts.get(source_relative, {}).get("sha256"),
        "nodes_sha256": artifacts.get(f"{ast_relative}/nodes.csv", {}).get(
            "sha256"
        ),
        "edges_sha256": artifacts.get(f"{ast_relative}/edges.csv", {}).get(
            "sha256"
        ),
    }
    if manifest_schema == MANIFEST_SCHEMA_VERSION:
        try:
            health_document = _read_json(
                directory / "health.json", "source health"
            )
            result_document = _read_json(
                directory / "result.json", "source evidence result"
            )
        except SourcePublicationError:
            health_document = {}
            result_document = {}
            failures.append("policy_documents")
        expected_bindings["health_report_sha256"] = health_document.get(
            "report_sha256"
        )
        if health_document.get("route") == "restricted_pass":
            try:
                inventory_document = _read_json(
                    directory / "inventory.json", "source inventory"
                )
            except SourcePublicationError:
                inventory_document = {}
                failures.append("inventory")
            expected_bindings.update({
                "inventory_artifact_sha256": artifacts.get(
                    "inventory.json", {}
                ).get("sha256"),
                "inventory_sha256": inventory_document.get("inventory_sha256"),
                "restriction_evidence_sha256": result_document.get(
                    "result_sha256"
                ),
                "restriction_events_sha256": health_document.get(
                    "restriction_events_sha256"
                ),
                "restriction_profile_sha256": _binding_hash(
                    health_document.get("restriction_profile")
                ),
            })
    if bindings != expected_bindings:
        failures.append("bindings")
    if manifest_schema == MANIFEST_SCHEMA_VERSION and not failures:
        policy_decision = {
            "health_report_sha256": health_document.get("report_sha256"),
            "policy_version": health_document.get("policy_version"),
            "route": health_document.get("route"),
        }
        if health_document.get("route") == "restricted_pass":
            policy_decision.update({
                "inventory_sha256": inventory_document.get("inventory_sha256"),
                "restriction_evidence_sha256": result_document.get(
                    "result_sha256"
                ),
                "restriction_events_sha256": health_document.get(
                    "restriction_events_sha256"
                ),
                "restriction_profile": health_document.get(
                    "restriction_profile"
                ),
                "restriction_profile_sha256": _binding_hash(
                    health_document.get("restriction_profile")
                ),
            })
        expected_identity = {
            "schema_version": "source-generation-identity/v2",
            "source_sha256": artifacts[source_relative]["sha256"],
            "policy_version": health_document.get("policy_version"),
            "policy_decision": policy_decision,
            "engine": {
                "name": result_document.get("engine"),
                "version": result_document.get("engine_version"),
                "mode": result_document.get("mode", "recalculated"),
            },
            "builder": manifest["ast_provenance"]["builder"],
            "nodes_sha256": artifacts[f"{ast_relative}/nodes.csv"]["sha256"],
            "edges_sha256": artifacts[f"{ast_relative}/edges.csv"]["sha256"],
        }
        if identity != expected_identity:
            failures.append("identity")
        try:
            from xl_source_health import inspect_workbook, validate_report

            source_path = directory / source_relative
            validate_report(health_document, source_path)
            if health_document != inspect_workbook(source_path):
                raise ValueError("source health does not match fresh observation")
            request_document = _read_json(
                directory / "request.json", "source evidence request"
            )
            route = health_document.get("route")
            evidence_schemas = (
                request_document.get("schema_version"),
                result_document.get("schema_version"),
            )
            accepted = {
                "pass": {
                    ("source-identity-request/v2", "source-identity-result/v2"),
                    ("source-recalc-request/v2", "source-recalc-result/v2"),
                },
                "recalc_candidate": {
                    ("source-recalc-request/v2", "source-recalc-result/v2")
                },
                "restricted_pass": {
                    (
                        "source-restriction-request/v2",
                        "source-restriction-result/v2",
                    )
                },
            }
            if evidence_schemas not in accepted.get(route, set()):
                raise ValueError("evidence schemas do not match route")
            if (
                request_document.get("policy_version")
                != health_document.get("policy_version")
                or result_document.get("policy_version")
                not in {None, health_document.get("policy_version")}
            ):
                raise ValueError("evidence policy does not match health policy")
            if route == "restricted_pass":
                from xl_source_inventory import validate_inventory_manifest
                from xl_source_recalc import create_restriction_documents

                validate_inventory_manifest(inventory_document)
                expected_request, expected_result = create_restriction_documents(
                    source_path, health_document, inventory_document
                )
                if (
                    request_document != expected_request
                    or result_document != expected_result
                ):
                    raise ValueError("restriction evidence changed")
        except (OSError, ValueError, SourcePublicationError):
            failures.append("policy_validation")
    if not failures:
        provenance = _read_json(
            directory / "ast-provenance.json", "AST provenance"
        )
        if provenance != manifest.get("ast_provenance"):
            failures.append("ast_provenance_manifest")
        else:
            try:
                validate_ast_provenance(
                    directory / source_relative,
                    directory / ast_relative,
                    provenance,
                )
            except SourcePublicationError:
                failures.append("ast_provenance")
    if failures:
        raise SourcePublicationError(
            "source generation validation failed: "
            + ", ".join(sorted(set(failures)))
        )
    return manifest


def _pointer_for_generation(root: Path, generation_dir: Path, manifest: dict) -> dict:
    return {
        "schema_version": POINTER_SCHEMA_VERSION,
        "generation_id": manifest["generation_id"],
        "generation_path": f"generations/{manifest['generation_id']}",
        "manifest_sha256": _sha256(
            generation_dir / "generation-manifest.json"
        ),
        "source_sha256": manifest["bindings"]["source_sha256"],
    }


def activate_source_generation(
    publication_root: str | Path,
    generation_id: str,
    *,
    segmentation_dir: str | Path | None = None,
    expected_segmentation_generation_id: str | None = None,
    fault=None,
) -> dict:
    """Select a generation only after its provenance and strict proof pass."""
    root = Path(publication_root)
    generation_dir = root / "generations" / generation_id
    manifest = validate_source_generation(
        generation_dir, expected_generation_id=generation_id
    )
    route = (
        (manifest.get("identity") or {})
        .get("policy_decision", {})
        .get("route")
    )
    if (
        manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
        and route == "restricted_pass"
    ):
        raise SourcePublicationError(
            "restricted v2 source generations stay inactive; promote the exact "
            "source/segmentation/task tuple through xl_release_publication"
        )
    layout = manifest["layout"]
    source_path = generation_dir / layout["source_workbook"]
    ast_dir = generation_dir / layout["ast_directory"]
    validate_ast_provenance(
        source_path,
        ast_dir,
        generation_dir / "ast-provenance.json",
    )
    if segmentation_dir is None:
        raise SourcePublicationError(
            "strict segmentation proof is required before source activation"
        )
    from xl_seg.publication import resolve_current_generation

    resolve_current_generation(
        Path(segmentation_dir),
        source_path=source_path,
        ast_dir=ast_dir,
        require_pass=True,
        validate_live_evidence=True,
        expected_generation_id=expected_segmentation_generation_id,
    )
    pointer = _pointer_for_generation(root, generation_dir, manifest)
    if fault:
        fault("before_pointer_switch")
    _atomic_write(root / "current.json", _canonical_bytes(pointer))
    if fault:
        fault("pointer_switched")
    return pointer


def resolve_current_source_generation(
    publication_root: str | Path,
    *,
    expected_generation_id: str | None = None,
) -> tuple[Path, dict]:
    root = Path(publication_root)
    pointer = _read_json(root / "current.json", "source current pointer")
    if pointer.get("schema_version") != POINTER_SCHEMA_VERSION:
        raise SourcePublicationError("unsupported source current pointer schema")
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise SourcePublicationError("source current pointer has invalid generation ID")
    if expected_generation_id is not None and generation_id != expected_generation_id:
        raise SourcePublicationError("source current pointer changed")
    if pointer.get("generation_path") != f"generations/{generation_id}":
        raise SourcePublicationError("unsafe source generation path")
    generations = root / "generations"
    if generations.is_symlink():
        raise SourcePublicationError("generations directory is a symlink")
    directory = generations / generation_id
    if directory.resolve().parent != generations.resolve():
        raise SourcePublicationError("source generation escapes publication root")
    manifest_path = directory / "generation-manifest.json"
    if (
        not manifest_path.is_file()
        or pointer.get("manifest_sha256") != _sha256(manifest_path)
    ):
        raise SourcePublicationError("source pointer manifest hash mismatch")
    manifest = validate_source_generation(
        directory, expected_generation_id=generation_id
    )
    if pointer.get("source_sha256") != manifest["bindings"]["source_sha256"]:
        raise SourcePublicationError("source pointer workbook hash mismatch")
    return directory, manifest


def resolve_source_generation_by_id(
    publication_root: str | Path,
    generation_id: str,
) -> tuple[Path, dict]:
    """Resolve and validate an immutable generation without consulting current."""
    if not isinstance(generation_id, str) or not GENERATION_ID_RE.fullmatch(
        generation_id
    ):
        raise SourcePublicationError("source generation ID is invalid")
    root = Path(publication_root)
    generations = root / "generations"
    if generations.is_symlink():
        raise SourcePublicationError("generations directory is a symlink")
    directory = generations / generation_id
    if directory.resolve().parent != generations.resolve():
        raise SourcePublicationError("source generation escapes publication root")
    manifest = validate_source_generation(
        directory,
        expected_generation_id=generation_id,
    )
    return directory, manifest


def resolve_effective_source(
    publication_root: str | Path,
    *,
    expected_generation_id: str | None = None,
) -> dict:
    directory, manifest = resolve_current_source_generation(
        publication_root,
        expected_generation_id=expected_generation_id,
    )
    layout = manifest["layout"]
    source_path = directory / layout["source_workbook"]
    ast_dir = directory / layout["ast_directory"]
    validate_ast_provenance(
        source_path,
        ast_dir,
        directory / "ast-provenance.json",
    )
    return {
        "generation_id": manifest["generation_id"],
        "generation_dir": str(directory),
        "workbook_id": layout["workbook_id"],
        "source_path": str(source_path),
        "source_root": str(directory / layout["source_root"]),
        "source_sha256": manifest["bindings"]["source_sha256"],
        "ast_dir": str(ast_dir),
        "ast_root": str(directory / layout["ast_root"]),
        "nodes_sha256": manifest["bindings"]["nodes_sha256"],
        "edges_sha256": manifest["bindings"]["edges_sha256"],
    }


# Short aliases for callers that already use xl_seg.publication naming.
publish_generation = publish_source_generation
validate_generation_directory = validate_source_generation
resolve_current_generation = resolve_current_source_generation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("generation_dir")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("publication_root")
    resolve.add_argument("--expected-generation-id")
    activate = commands.add_parser("activate")
    activate.add_argument("publication_root")
    activate.add_argument("generation_id")
    activate.add_argument("--segmentation-dir", required=True)
    activate.add_argument("--expected-segmentation-generation-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_source_generation(args.generation_dir)
        elif args.command == "resolve":
            result = resolve_effective_source(
                args.publication_root,
                expected_generation_id=args.expected_generation_id,
            )
        else:
            result = activate_source_generation(
                args.publication_root,
                args.generation_id,
                segmentation_dir=args.segmentation_dir,
                expected_segmentation_generation_id=(
                    args.expected_segmentation_generation_id
                ),
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ValueError as exc:
        print(f"source publication FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

