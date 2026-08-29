"""Bound, fail-closed recalculation requests with semantic workbook checks."""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
import platform
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from xl_source_health import (
    POLICY_VERSION,
    RESTRICTION_PROFILE,
    _open_regular_nofollow,
    atomic_write_report,
    inspect_workbook,
    sha256_file,
    validate_report,
    validate_ooxml_package,
)


REQUEST_SCHEMA_VERSION = "source-recalc-request/v2"
RESULT_SCHEMA_VERSION = "source-recalc-result/v2"
SNAPSHOT_SCHEMA_VERSION = "xlsx-semantic-snapshot/v1"
RESTRICTION_REQUEST_SCHEMA_VERSION = "source-restriction-request/v2"
RESTRICTION_RESULT_SCHEMA_VERSION = "source-restriction-result/v2"
RESTRICTED_SOURCE_COHORT_MANIFEST = (
    Path(__file__).resolve().parent
    / "verification_manifests"
    / "restricted_source_cohort_123.v2.json"
)
RESTRICTED_SOURCE_COHORT_SIZE = 123
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_VOLATILE_RE = re.compile(
    r"(?i)(?<![A-Z0-9_.])"
    r"(CELL|INFO|INDIRECT|NOW|OFFSET|RAND|RANDBETWEEN|TODAY)\s*\("
)


class RecalculationError(ValueError):
    """A recalculation request, engine result, or workbook failed validation."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _trusted_public_key(
    path: str | Path,
    *,
    require_root_owner: bool = True,
) -> Path:
    requested = Path(path)
    try:
        metadata = requested.lstat()
        key = requested.resolve(strict=True)
    except OSError as exc:
        raise RecalculationError("trusted runner public key is unavailable") from exc
    if (
        requested.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o022
        or require_root_owner
        and metadata.st_uid != 0
    ):
        raise RecalculationError("trusted runner public key is not protected")
    if require_root_owner:
        for parent in key.parents:
            parent_metadata = parent.stat()
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != 0
                or parent_metadata.st_mode & 0o022
            ):
                raise RecalculationError(
                    "trusted runner public key has an untrusted parent directory"
                )
    return key


def verify_signed_runner_receipt(
    receipt: dict,
    trusted_public_key: str | Path,
    *,
    request_sha256: str,
    source_sha256: str,
    output_sha256: str,
    require_root_owner: bool = True,
) -> dict:
    """Verify the trusted Excel runner's signature and bound claims."""
    if receipt.get("schema_version") != "excel-runner-receipt/v1":
        raise RecalculationError("sandbox runner receipt schema is invalid")
    payload = receipt.get("signed_payload")
    signature_text = receipt.get("signature_base64")
    if not isinstance(payload, dict) or not isinstance(signature_text, str):
        raise RecalculationError("sandbox runner receipt is unsigned")
    required = {
        "request_sha256": request_sha256,
        "source_sha256": source_sha256,
        "output_sha256": output_sha256,
        "engine": "excel-macos",
        "calculation_complete": True,
        "isolation_enforced": True,
    }
    if any(payload.get(key) != value for key, value in required.items()):
        raise RecalculationError("sandbox runner receipt claims do not match")
    if not isinstance(payload.get("engine_version"), str) or not payload[
        "engine_version"
    ]:
        raise RecalculationError("sandbox runner receipt lacks engine version")
    if not isinstance(payload.get("completed_at_ns"), int):
        raise RecalculationError("sandbox runner receipt lacks completion time")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (ValueError, TypeError) as exc:
        raise RecalculationError("sandbox runner receipt signature is invalid") from exc
    key = _trusted_public_key(
        trusted_public_key,
        require_root_owner=require_root_owner,
    )
    with tempfile.TemporaryDirectory(prefix="excel-receipt-verify-") as temporary:
        payload_path = Path(temporary) / "payload.json"
        signature_path = Path(temporary) / "signature.bin"
        payload_path.write_bytes(_canonical_bytes(payload))
        signature_path.write_bytes(signature)
        verified = subprocess.run(
            [
                "/usr/bin/openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(key),
                "-signature",
                str(signature_path),
                str(payload_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    if verified.returncode != 0:
        raise RecalculationError("sandbox runner receipt signature verification failed")
    return payload


def _object_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _unsigned_hash(value: dict, signature_key: str) -> str:
    unsigned = dict(value)
    unsigned.pop(signature_key, None)
    return _object_hash(unsigned)


def _safe_child(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _request_destination(request: dict, allowed_root: Path) -> Path:
    relative = request.get("destination_relative")
    if not isinstance(relative, str) or not relative:
        raise RecalculationError("request has no destination-relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RecalculationError("request destination is unsafe")
    destination = allowed_root.resolve() / candidate
    if not _safe_child(destination, allowed_root):
        raise RecalculationError("request destination escapes allowed root")
    return destination


def _xml_root(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError) as exc:
        raise RecalculationError(f"unreadable OOXML part {name}: {exc}") from exc


def _normalized_xml(element: ElementTree.Element) -> object:
    """Represent XML independent of prefixes and attribute ordering."""
    return [
        element.tag,
        sorted(element.attrib.items()),
        element.text or "",
        [_normalized_xml(child) for child in list(element)],
    ]


def _resolve_part(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(
        posixpath.join(posixpath.dirname(base_part), target)
    )


def _sheet_parts(
    archive: zipfile.ZipFile,
) -> tuple[ElementTree.Element, list[tuple[str, str, dict]]]:
    workbook = _xml_root(archive, "xl/workbook.xml")
    relationships = _xml_root(archive, "xl/_rels/workbook.xml.rels")
    targets = {
        relation.attrib.get("Id"): relation.attrib.get("Target", "")
        for relation in relationships.findall(f"{{{REL_NS}}}Relationship")
    }
    parts = []
    for sheet in workbook.findall(".//{*}sheet"):
        rel_id = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        target = targets.get(rel_id)
        if not target:
            raise RecalculationError(
                f"worksheet relationship {rel_id!r} is missing"
            )
        parts.append((
            sheet.attrib.get("name", ""),
            _resolve_part("xl/workbook.xml", target),
            dict(sorted(sheet.attrib.items())),
        ))
    return workbook, parts


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml_root(archive, "xl/sharedStrings.xml")
    return [
        "".join(node.text or "" for node in item.findall(".//{*}t"))
        for item in root.findall("{*}si")
    ]


def _input_value(cell: ElementTree.Element, shared: list[str]) -> object | None:
    cell_type = cell.attrib.get("t", "n")
    value = cell.find("{*}v")
    inline = cell.find("{*}is")
    if value is None and inline is None:
        return None
    text = value.text if value is not None else "".join(
        node.text or "" for node in inline.findall(".//{*}t")
    )
    if cell_type == "s":
        try:
            return ["string", shared[int(text or "")]]
        except (ValueError, IndexError):
            raise RecalculationError(
                f"invalid shared-string index in {cell.attrib.get('r', '')}"
            )
    if cell_type in {"inlineStr", "str"}:
        return ["string", text or ""]
    if cell_type == "b":
        return ["boolean", text]
    if cell_type == "e":
        return ["error", text]
    return ["number", text]


def _relationship_snapshot(archive: zipfile.ZipFile) -> list:
    result = []
    for name in sorted(
        item for item in archive.namelist() if item.endswith(".rels")
    ):
        root = _xml_root(archive, name)
        relations = sorted(
            [
                relation.attrib.get("Id"),
                relation.attrib.get("Type"),
                relation.attrib.get("Target"),
                relation.attrib.get("TargetMode"),
            ]
            for relation in root.findall(f"{{{REL_NS}}}Relationship")
        )
        result.append([name, relations])
    return result


def _package_snapshot(
    archive: zipfile.ZipFile,
    worksheet_parts: set[str],
) -> dict:
    """Canonicalize every package part, excluding only formula cached values."""
    result = {}
    for name in sorted(archive.namelist()):
        if name.endswith("/"):
            continue
        raw = archive.read(name)
        is_xml = (
            name.endswith((".xml", ".rels"))
            or name == "[Content_Types].xml"
        )
        if not is_xml:
            result[name] = {
                "kind": "binary",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
            continue
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise RecalculationError(
                f"cannot canonicalize OOXML part {name}: {exc}"
            ) from exc
        if name in worksheet_parts:
            root = copy.deepcopy(root)
            for cell in root.findall(".//{*}c"):
                if cell.find("{*}f") is None:
                    continue
                for child in list(cell):
                    if child.tag.rsplit("}", 1)[-1] == "v":
                        cell.remove(child)
        result[name] = {
            "kind": "xml",
            "content": _normalized_xml(root),
        }
    return result


def semantic_workbook_snapshot(path: str | Path) -> dict:
    """Snapshot workbook meaning separately from formula cached values."""
    workbook_path = Path(path)
    if (
        not workbook_path.is_file()
        or workbook_path.is_symlink()
        or not zipfile.is_zipfile(workbook_path)
    ):
        raise RecalculationError("workbook is missing, linked, or not OOXML")
    try:
        validate_ooxml_package(workbook_path)
    except ValueError as exc:
        raise RecalculationError(str(exc)) from exc
    try:
        with _open_regular_nofollow(workbook_path) as workbook_handle, zipfile.ZipFile(
            workbook_handle
        ) as archive:
            names = set(archive.namelist())
            workbook, sheet_parts = _sheet_parts(archive)
            shared = _shared_strings(archive)
            formulas = {}
            inputs = {}
            caches = {}
            merges = {}
            for sheet_name, part, _ in sheet_parts:
                root = _xml_root(archive, part)
                sheet_formulas = {}
                sheet_inputs = {}
                sheet_caches = {}
                for cell in root.findall(".//{*}c"):
                    coordinate = cell.attrib.get("r", "")
                    formula = cell.find("{*}f")
                    if formula is not None:
                        sheet_formulas[coordinate] = [
                            formula.text or "",
                            sorted(formula.attrib.items()),
                        ]
                        value = cell.find("{*}v")
                        sheet_caches[coordinate] = {
                            "present": value is not None,
                            "value": None if value is None else value.text,
                            "type": cell.attrib.get("t"),
                        }
                    else:
                        value = _input_value(cell, shared)
                        if value is not None:
                            sheet_inputs[coordinate] = value
                formulas[sheet_name] = sheet_formulas
                inputs[sheet_name] = sheet_inputs
                caches[sheet_name] = sheet_caches
                merges[sheet_name] = sorted(
                    merge.attrib.get("ref", "")
                    for merge in root.findall(".//{*}mergeCell")
                )

            defined_names = sorted(
                [
                    sorted(name.attrib.items()),
                    name.text or "",
                ]
                for name in workbook.findall(".//{*}definedName")
            )
            calc = workbook.find("{*}calcPr")
            calc_settings = (
                None
                if calc is None
                else [sorted(calc.attrib.items()), calc.text or ""]
            )
            table_parts = {
                name: _normalized_xml(_xml_root(archive, name))
                for name in sorted(names)
                if name.startswith("xl/tables/") and name.endswith(".xml")
            }
            relationships = _relationship_snapshot(archive)
            package_parts = _package_snapshot(
                archive,
                {part for _, part, _ in sheet_parts},
            )
            relationship_types = [
                relation[1] or ""
                for _, records in relationships
                for relation in records
            ]
            content_types = archive.read("[Content_Types].xml").lower()
            unsupported = {
                "external_links": any(
                    name.startswith("xl/externalLinks/")
                    and name.endswith(".xml")
                    and "/_rels/" not in name
                    for name in names
                ) or any(
                    relation_type.endswith("/externalLink")
                    for relation_type in relationship_types
                ),
                "connections": "xl/connections.xml" in names,
                "macros": any(
                    name.lower().endswith(("vbaproject.bin", "vbadata.xml"))
                    for name in names
                ) or b"macroenabled" in content_types
                or b"vbaproject" in content_types,
                "ole": any(
                    name.startswith("xl/embeddings/") for name in names
                ) or any(
                    relation_type.endswith("/oleObject")
                    for relation_type in relationship_types
                ),
                "data_tables": any(
                    attributes
                    and dict(attributes).get("t") == "dataTable"
                    for sheet in formulas.values()
                    for _, attributes in sheet.values()
                ),
                "volatile_formulas": any(
                    _VOLATILE_RE.search(formula)
                    for sheet in formulas.values()
                    for formula, _ in sheet.values()
                ) or any(_VOLATILE_RE.search(text) for _, text in defined_names),
                "external_formulas": any(
                    re.search(r"\[[^\]]+\]", formula)
                    for sheet in formulas.values()
                    for formula, _ in sheet.values()
                ) or any(
                    re.search(r"\[[^\]]+\]", text)
                    for _, text in defined_names
                ),
            }
            semantics = {
                "formulas": formulas,
                "inputs": inputs,
                "defined_names": defined_names,
                "sheets": [
                    [name, part, attributes]
                    for name, part, attributes in sheet_parts
                ],
                "tables": table_parts,
                "merges": merges,
                "relationships": relationships,
                "calculation_settings": calc_settings,
                "package_parts": package_parts,
            }
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise RecalculationError(f"cannot snapshot workbook: {exc}") from exc
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_sha256": sha256_file(workbook_path),
        "semantics": semantics,
        "semantic_sha256": _object_hash(semantics),
        "formula_caches": caches,
        "formula_cache_sha256": _object_hash(caches),
        "supported_semantics": not any(unsupported.values()),
        "unsupported_features": sorted(
            name for name, present in unsupported.items() if present
        ),
    }


workbook_semantic_snapshot = semantic_workbook_snapshot


def semantic_workbook_diff(
    before: dict | str | Path,
    after: dict | str | Path,
) -> dict:
    """Reject semantic changes and identify changes limited to formula caches."""
    before_snapshot = (
        semantic_workbook_snapshot(before)
        if isinstance(before, (str, Path))
        else before
    )
    after_snapshot = (
        semantic_workbook_snapshot(after)
        if isinstance(after, (str, Path))
        else after
    )
    if (
        before_snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or after_snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
    ):
        raise RecalculationError("unsupported semantic snapshot schema")
    before_semantics = before_snapshot.get("semantics") or {}
    after_semantics = after_snapshot.get("semantics") or {}
    categories = [
        name
        for name in (
            "formulas",
            "inputs",
            "defined_names",
            "sheets",
            "tables",
            "merges",
            "relationships",
            "calculation_settings",
            "package_parts",
        )
        if before_semantics.get(name) != after_semantics.get(name)
    ]
    before_caches = before_snapshot.get("formula_caches") or {}
    after_caches = after_snapshot.get("formula_caches") or {}
    cache_changes = []
    populated_changes = []
    sheets = sorted(set(before_caches) | set(after_caches))
    for sheet in sheets:
        first = before_caches.get(sheet) or {}
        second = after_caches.get(sheet) or {}
        for cell in sorted(set(first) | set(second)):
            old = first.get(cell)
            new = second.get(cell)
            if old == new:
                continue
            address = f"{sheet}!{cell}"
            cache_changes.append(address)
            if (
                isinstance(old, dict)
                and isinstance(new, dict)
                and old.get("present")
                and new.get("present")
                and old.get("value") not in (None, "")
                and new.get("value") not in (None, "")
                and old.get("value") != new.get("value")
            ):
                populated_changes.append(address)
    equivalent = not categories
    supported = (
        equivalent
        and before_snapshot.get("supported_semantics") is True
        and after_snapshot.get("supported_semantics") is True
    )
    return {
        "equivalent_semantics": equivalent,
        "supported_equivalent_semantics": supported,
        "semantic_changes": categories,
        "cache_only": equivalent and bool(cache_changes),
        "formula_cache_changes": len(cache_changes),
        "formula_cache_change_cells": cache_changes,
        "populated_cache_changes": len(populated_changes),
        "populated_cache_change_cells": populated_changes,
        "proven_stale_cache": supported and bool(populated_changes),
    }


workbook_semantic_diff = semantic_workbook_diff


def create_recalc_request(
    source_path: str | Path,
    destination: str | Path,
    *,
    request_id: str | None = None,
    policy: dict | None = None,
    engine_constraints: dict | None = None,
    allowed_root: str | Path | None = None,
    max_source_size_bytes: int | None = None,
    ttl_seconds: int = 24 * 60 * 60,
    trusted_runner_public_key: str | Path | None = None,
) -> dict:
    source = Path(source_path)
    target = Path(destination)
    if source.is_symlink() or not source.is_file():
        raise RecalculationError("source workbook is missing or is a symlink")
    if allowed_root is None:
        raise RecalculationError("an allowed destination root is required")
    destination_root = Path(allowed_root).resolve()
    if not _safe_child(target, destination_root):
        raise RecalculationError("destination is outside the allowed root")
    destination_relative = target.resolve().relative_to(destination_root).as_posix()
    identifier = request_id or str(uuid.uuid4())
    if not 1 <= ttl_seconds <= 7 * 24 * 60 * 60:
        raise RecalculationError("request TTL must be between one second and seven days")
    created_at_ns = time.time_ns()
    constraints = {
        "allowed_engines": ["excel-macos"],
        "required_engine": "excel-macos",
        "permitted_versions": [],
        "require_authoritative": True,
        "require_capability_check": True,
    }
    if engine_constraints:
        constraints.update(engine_constraints)
        if (
            "allowed_engines" in engine_constraints
            and "required_engine" not in engine_constraints
        ):
            allowed = engine_constraints["allowed_engines"]
            constraints["required_engine"] = (
                allowed[0] if isinstance(allowed, list) and len(allowed) == 1 else None
            )
    if trusted_runner_public_key is not None:
        trusted_key = _trusted_public_key(trusted_runner_public_key)
        constraints["trusted_runner_public_key_sha256"] = sha256_file(trusted_key)
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_id": identifier,
        "policy_version": POLICY_VERSION,
        "created_at_ns": created_at_ns,
        "expires_at_ns": created_at_ns + ttl_seconds * 1_000_000_000,
        "source": {
            "path": str(source.resolve()),
            "sha256": sha256_file(source),
            "size_bytes": source.stat().st_size,
        },
        "source_sha256": sha256_file(source),
        "source_size_bytes": source.stat().st_size,
        "destination_relative": destination_relative,
        "max_source_size_bytes": (
            max_source_size_bytes
            if max_source_size_bytes is not None
            else source.stat().st_size
        ),
        "policy": {
            "require_semantic_equivalence": True,
            "allow_cache_only_changes": True,
            **(policy or {}),
        },
        "engine_constraints": constraints,
    }
    request["request_sha256"] = _unsigned_hash(request, "request_sha256")
    return request


def validate_recalc_request(
    request: dict,
    *,
    source_path: str | Path | None = None,
    allowed_root: str | Path | None = None,
    engine_name: str | None = None,
    engine_version: str | None = None,
    seen_request_ids: set[str] | None = None,
) -> dict:
    if not isinstance(request, dict):
        raise RecalculationError("recalculation request must be an object")
    failures = []
    if request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        failures.append("schema_version")
    if request.get("policy_version") != POLICY_VERSION:
        failures.append("policy_version")
    now_ns = time.time_ns()
    created_at_ns = request.get("created_at_ns")
    expires_at_ns = request.get("expires_at_ns")
    if (
        not isinstance(created_at_ns, int)
        or not isinstance(expires_at_ns, int)
        or expires_at_ns <= created_at_ns
        or created_at_ns > now_ns + 60 * 1_000_000_000
        or expires_at_ns < now_ns
    ):
        failures.append("request_expiry")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        failures.append("request_id")
    if seen_request_ids is not None and request_id in seen_request_ids:
        failures.append("request_replay")
    if request.get("request_sha256") != _unsigned_hash(
        request, "request_sha256"
    ):
        failures.append("request_sha256")
    source_record = request.get("source")
    if not isinstance(source_record, dict):
        failures.append("source")
        source_record = {}
    source = Path(source_path or source_record.get("path", ""))
    if (
        source.is_symlink()
        or not source.is_file()
        or source_record.get("sha256") != sha256_file(source)
        or source_record.get("size_bytes") != source.stat().st_size
        or request.get("source_sha256") != source_record.get("sha256")
        or request.get("source_size_bytes") != source_record.get("size_bytes")
    ):
        failures.append("source_binding")
    limit = request.get("max_source_size_bytes")
    if (
        not isinstance(limit, int)
        or limit < 0
        or source.is_file()
        and source.stat().st_size > limit
    ):
        failures.append("size_constraint")
    try:
        destination = _request_destination(
            request,
            Path(allowed_root) if allowed_root is not None else Path.cwd(),
        )
    except RecalculationError:
        destination = Path("")
        failures.append("destination_root")
    if source.is_file() and destination.resolve() == source.resolve():
        failures.append("destination_is_source")
    if destination.exists():
        failures.append("destination_exists")
    policy = request.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("require_semantic_equivalence") is not True
        or policy.get("allow_cache_only_changes") is not True
    ):
        failures.append("policy")
    constraints = request.get("engine_constraints")
    if not isinstance(constraints, dict):
        failures.append("engine_constraints")
    elif engine_name is not None:
        allowed = constraints.get("allowed_engines")
        if allowed and engine_name not in allowed:
            failures.append("engine")
        required = constraints.get("required_engine", constraints.get("engine"))
        if required is not None and engine_name != required:
            failures.append("engine")
        permitted_versions = constraints.get("permitted_versions")
        if (
            not isinstance(permitted_versions, list)
            or not permitted_versions
            or engine_version is None
            or "*" not in permitted_versions
            and engine_version not in permitted_versions
        ):
            failures.append("engine_version")
    if failures:
        raise RecalculationError(
            "recalculation request validation failed: "
            + ", ".join(sorted(set(failures)))
        )
    return request


class MacOSExcelEngine:
    """Microsoft Excel adapter that delegates to a root-owned sandbox runner."""

    name = "excel-macos"
    authoritative = True

    def __init__(
        self,
        *,
        isolation_attestation: str | Path | None = None,
        sandbox_runner: str | Path | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.isolation_attestation = (
            Path(isolation_attestation) if isolation_attestation else None
        )
        self.sandbox_runner = Path(sandbox_runner) if sandbox_runner else None
        self.timeout_seconds = timeout_seconds
        self.version = "unknown"
        self.evidence: dict = {}

    @staticmethod
    def _trusted_root_file(path: Path, *, executable: bool = False) -> bool:
        try:
            metadata = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError:
            return False
        protected_file = (
            stat.S_ISREG(metadata.st_mode)
            and not path.is_symlink()
            and metadata.st_uid == 0
            and metadata.st_mode & 0o022 == 0
            and (not executable or metadata.st_mode & 0o111 != 0)
        )
        protected_parents = all(
            stat.S_ISDIR(parent.stat().st_mode)
            and parent.stat().st_uid == 0
            and parent.stat().st_mode & 0o022 == 0
            for parent in resolved.parents
        )
        return protected_file and protected_parents

    def _attestation(self) -> tuple[bool, str]:
        if self.isolation_attestation is None:
            return False, "isolation_attestation_required"
        if not self._trusted_root_file(self.isolation_attestation):
            return False, "isolation_attestation_not_trusted"
        try:
            payload = json.loads(
                self.isolation_attestation.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False, "isolation_attestation_unreadable"
        required = (
            "dedicated_session",
            "network_disabled",
            "macros_disabled",
            "add_ins_disabled",
            "link_updates_disabled",
            "prompts_suppressed",
        )
        if payload.get("schema_version") != "excel-isolation-attestation/v1":
            return False, "isolation_attestation_schema"
        if any(payload.get(key) is not True for key in required):
            return False, "isolation_controls_unconfirmed"
        if self.sandbox_runner is None:
            return False, "sandbox_runner_required"
        if not self._trusted_root_file(self.sandbox_runner, executable=True):
            return False, "sandbox_runner_not_trusted"
        runner_hash = sha256_file(self.sandbox_runner)
        if payload.get("sandbox_runner_sha256") != runner_hash:
            return False, "sandbox_runner_hash_mismatch"
        public_key_value = payload.get("receipt_public_key")
        if not isinstance(public_key_value, str):
            return False, "receipt_public_key_required"
        try:
            public_key = _trusted_public_key(public_key_value)
        except RecalculationError:
            return False, "receipt_public_key_not_trusted"
        public_key_hash = sha256_file(public_key)
        if payload.get("receipt_public_key_sha256") != public_key_hash:
            return False, "receipt_public_key_hash_mismatch"
        self.receipt_public_key = public_key
        self.evidence = {
            "isolation_attestation_sha256": sha256_file(
                self.isolation_attestation
            ),
            "sandbox_runner_sha256": runner_hash,
            "receipt_public_key_sha256": public_key_hash,
            "controls": {key: payload[key] for key in required},
        }
        return True, "attested"

    def capability(self) -> tuple[bool, str]:
        if platform.system() != "Darwin":
            return False, "platform_not_macos"
        candidates = (
            Path("/Applications/Microsoft Excel.app"),
            Path.home() / "Applications/Microsoft Excel.app",
        )
        if not any(path.exists() for path in candidates):
            return False, "excel_unavailable"
        attested, reason = self._attestation()
        if not attested:
            return False, reason
        self.excel_app = next(path for path in candidates if path.exists())
        version = subprocess.run(
            [
                "mdls",
                "-raw",
                "-name",
                "kMDItemVersion",
                str(self.excel_app),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if version.returncode != 0 or not version.stdout.strip():
            return False, "excel_version_unavailable"
        self.version = version.stdout.strip()
        return True, "available"

    def execute(self, source: Path, destination: Path, request: dict) -> Path:
        available, reason = self.capability()
        if not available:
            raise RecalculationError(f"Excel capability check failed: {reason}")
        request_file = destination.with_name(
            f".{destination.name}.{request['request_id']}.request.json"
        )
        request_file.write_bytes(_canonical_bytes(request))
        request_file.chmod(0o400)
        command = [
            str(self.sandbox_runner),
            "--excel-app",
            str(self.excel_app),
            "--workbook",
            str(destination),
            "--request",
            str(request_file),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            raise RecalculationError("Excel recalculation timed out") from exc
        finally:
            request_file.unlink(missing_ok=True)
        if process.returncode != 0:
            raise RecalculationError(
                "Excel recalculation failed: "
                + (stderr.strip() or "sandbox runner returned nonzero")
            )
        try:
            receipt = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RecalculationError(
                "sandbox runner returned an invalid receipt"
            ) from exc
        public_key_hash = sha256_file(self.receipt_public_key)
        if (
            request.get("engine_constraints", {}).get(
                "trusted_runner_public_key_sha256"
            )
            != public_key_hash
        ):
            raise RecalculationError(
                "request is not bound to the trusted runner public key"
            )
        signed_payload = verify_signed_runner_receipt(
            receipt,
            self.receipt_public_key,
            request_sha256=request["request_sha256"],
            source_sha256=request["source_sha256"],
            output_sha256=sha256_file(destination),
        )
        if signed_payload["engine_version"] != self.version:
            raise RecalculationError(
                "signed runner engine version does not match observed Excel version"
            )
        self.evidence["runner_receipt"] = receipt
        return destination


def _engine_name(engine: object) -> str:
    return str(
        getattr(
            engine,
            "name",
            getattr(engine, "__name__", type(engine).__name__),
        )
    )


def _invoke_engine(
    engine: object,
    source: Path,
    candidate: Path,
    request: dict,
) -> None:
    capability = getattr(engine, "capability", None)
    if callable(capability):
        outcome = capability()
        if isinstance(outcome, tuple):
            available, reason = outcome
        else:
            available, reason = bool(outcome), "unavailable"
        if not available:
            raise RecalculationError(f"engine capability check failed: {reason}")
    execute = getattr(engine, "execute", None)
    if callable(execute):
        execute(source, candidate, request)
    elif callable(engine):
        engine(source, candidate, request)
    else:
        raise RecalculationError("recalculation engine is not callable")


def _execute_recalc_unlocked(
    request: dict,
    engine: object,
    *,
    allowed_root: str | Path,
    source_path: str | Path | None = None,
    journal_dir: str | Path | None = None,
    fault=None,
) -> dict:
    """Execute through a private copy; publish only an equivalent complete file."""
    engine_name = _engine_name(engine)
    engine_version = str(getattr(engine, "version", "unknown"))
    root = Path(allowed_root)
    journal = Path(journal_dir) if journal_dir else root / ".recalc-journal"
    receipt = journal / f"{request.get('request_id', 'invalid')}.json"
    seen = {request.get("request_id")} if receipt.exists() else set()
    validate_recalc_request(
        request,
        source_path=source_path,
        allowed_root=root,
        seen_request_ids=seen,
    )
    if (
        request.get("engine_constraints", {}).get("require_authoritative")
        is True
        and getattr(engine, "authoritative", False) is not True
    ):
        raise RecalculationError("engine is not marked authoritative")
    source = Path(source_path or request["source"]["path"])
    destination = _request_destination(request, root)
    source_hash = sha256_file(source)
    source_health = inspect_workbook(source)
    if source_health["route"] in {"unsupported", "insufficient_evidence"}:
        raise RecalculationError(
            "source health does not permit recalculation: "
            + ", ".join(source_health["reason_codes"])
        )
    before = semantic_workbook_snapshot(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".recalc.xlsx",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    candidate = Path(temporary)
    try:
        shutil.copyfile(source, candidate)
        if fault:
            fault("candidate_copied")
        started_at_ns = time.time_ns()
        _invoke_engine(engine, source, candidate, request)
        engine_version = str(getattr(engine, "version", engine_version))
        validate_recalc_request(
            request,
            source_path=source,
            allowed_root=root,
            engine_name=engine_name,
            engine_version=engine_version,
        )
        if fault:
            fault("engine_completed")
        if sha256_file(source) != source_hash:
            raise RecalculationError("engine modified the original workbook")
        after = semantic_workbook_snapshot(candidate)
        difference = semantic_workbook_diff(before, after)
        if not difference["supported_equivalent_semantics"]:
            raise RecalculationError(
                "recalculation changed or used unsupported workbook semantics: "
                + ", ".join(difference["semantic_changes"])
            )
        after_health = inspect_workbook(candidate)
        if after_health["route"] in {"unsupported", "insufficient_evidence"}:
            raise RecalculationError(
                "recalculated workbook failed source health: "
                + ", ".join(after_health["reason_codes"])
            )
        uncleared_cache_reasons = {
            "formula_cache_incomplete",
            "formula_cache_empty",
        }.intersection(after_health["reason_codes"])
        if uncleared_cache_reasons:
            raise RecalculationError(
                "recalculation did not populate required formula caches: "
                + ", ".join(sorted(uncleared_cache_reasons))
            )
        engine_evidence = getattr(engine, "evidence", {})
        signed_completion = (
            engine_evidence.get("runner_receipt", {})
            .get("signed_payload", {})
            .get("completed_at_ns")
        )
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "engine": engine_name,
            "engine_version": engine_version,
            "authoritative": getattr(engine, "authoritative", False) is True,
            "policy_version": request["policy_version"],
            "engine_evidence": engine_evidence,
            "source_sha256": source_hash,
            "output_sha256": sha256_file(candidate),
            "output_size_bytes": candidate.stat().st_size,
            "destination": str(destination),
            "started_at_ns": started_at_ns,
            "semantic_diff": difference,
            "proven_stale_cache": difference["proven_stale_cache"],
            "calculation_settings_before": before["semantics"][
                "calculation_settings"
            ],
            "calculation_settings_after": after["semantics"][
                "calculation_settings"
            ],
            "source_health_before_sha256": source_health["report_sha256"],
            "source_health_after_sha256": after_health["report_sha256"],
            "completed_at_ns": (
                signed_completion
                if isinstance(signed_completion, int)
                else time.time_ns()
            ),
        }
        result["result_sha256"] = _unsigned_hash(result, "result_sha256")
        if fault:
            fault("before_destination_publish")
        os.replace(candidate, destination)
        if fault:
            fault("destination_published")
        journal.mkdir(parents=True, exist_ok=True)
        atomic_write_report(receipt, result)
        return result
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise


def execute_recalc(
    request: dict,
    engine: object,
    *,
    allowed_root: str | Path,
    source_path: str | Path | None = None,
    journal_dir: str | Path | None = None,
    fault=None,
) -> dict:
    """Serialize a request and reject concurrent writes into its allowed root."""
    root = Path(allowed_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".recalc.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecalculationError(
                "another recalculation owns the destination root"
            ) from exc
        return _execute_recalc_unlocked(
            request,
            engine,
            allowed_root=root,
            source_path=source_path,
            journal_dir=journal_dir,
            fault=fault,
        )
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


execute_recalculation = execute_recalc


def _read_json(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecalculationError(f"cannot read JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RecalculationError("JSON document must be an object")
    return value


def _validated_restricted_inventory_selection(
    inventory: dict,
    *,
    source_sha256: str,
    workbook_id: str,
    expected_cohort_sha256: str | None = None,
) -> tuple[dict, str, str]:
    from xl_source_inventory import validate_inventory_manifest

    validate_inventory_manifest(inventory)
    frozen_inventory = _read_json(RESTRICTED_SOURCE_COHORT_MANIFEST)
    validate_inventory_manifest(frozen_inventory)
    frozen_cohort = frozen_inventory.get("cohort")
    supplied_cohort = inventory.get("cohort")
    if (
        not isinstance(frozen_cohort, dict)
        or frozen_cohort.get("size") != RESTRICTED_SOURCE_COHORT_SIZE
        or not isinstance(frozen_cohort.get("workbook_ids"), list)
        or len(frozen_cohort["workbook_ids"]) != RESTRICTED_SOURCE_COHORT_SIZE
    ):
        raise RecalculationError("frozen restricted cohort contract is invalid")
    cohort_hash = frozen_cohort.get("cohort_sha256")
    if (
        not isinstance(cohort_hash, str)
        or expected_cohort_sha256 is not None
        and expected_cohort_sha256 != cohort_hash
    ):
        raise RecalculationError("expected restricted cohort hash does not match")
    if (
        inventory != frozen_inventory
        or supplied_cohort != frozen_cohort
        or inventory.get("inventory_sha256")
        != frozen_inventory.get("inventory_sha256")
    ):
        raise RecalculationError(
            "restricted evidence requires the exact frozen 123-workbook inventory"
        )
    selected = [
        item
        for item in inventory.get("workbooks", [])
        if isinstance(item, dict) and item.get("workbook_id") == workbook_id
    ]
    if len(selected) != 1 or selected[0].get("sha256") != source_sha256:
        raise RecalculationError(
            "source workbook ID/hash does not match the frozen inventory"
        )
    classification = selected[0].get("classification")
    if classification not in {"native_source", "conversion_equivalent"}:
        raise RecalculationError(
            "restricted evidence rejects conversion_unverified source"
        )
    return selected[0], cohort_hash, frozen_inventory["inventory_sha256"]


def create_restriction_documents(
    source_path: str | Path,
    health: dict,
    inventory: dict,
    *,
    workbook_id: str | None = None,
    expected_cohort_sha256: str | None = None,
) -> tuple[dict, dict]:
    """Create deterministic evidence for one v2 restricted-policy decision."""
    source = Path(source_path)
    try:
        validate_report(health, source)
    except ValueError as exc:
        raise RecalculationError(f"restricted evidence input is invalid: {exc}") from exc
    source_hash = sha256_file(source)
    if health.get("route") != "restricted_pass":
        raise RecalculationError("restricted evidence requires restricted_pass health")
    if health.get("policy_version") != POLICY_VERSION:
        raise RecalculationError("restricted evidence requires the current policy")
    selected_workbook_id = workbook_id or source.stem
    try:
        selected, cohort_hash, inventory_hash = (
            _validated_restricted_inventory_selection(
                inventory,
                source_sha256=source_hash,
                workbook_id=selected_workbook_id,
                expected_cohort_sha256=expected_cohort_sha256,
            )
        )
    except ValueError as exc:
        raise RecalculationError(
            f"restricted evidence input is invalid: {exc}"
        ) from exc
    classification = selected["classification"]
    events = health.get("restriction_events")
    if (
        not isinstance(events, list)
        or not events
        or any(event.get("allowed") is not True for event in events)
        or any(
            event.get("reason") not in RESTRICTION_PROFILE["allowlist"]
            for event in events
        )
    ):
        raise RecalculationError("restricted health contains non-allowlisted events")
    restriction = {
        "classification": classification,
        "cohort_sha256": cohort_hash,
        "cohort_size": RESTRICTED_SOURCE_COHORT_SIZE,
        "health_report_sha256": health["report_sha256"],
        "inventory_sha256": inventory_hash,
        "policy_version": POLICY_VERSION,
        "profile": RESTRICTION_PROFILE,
        "reason_codes": health["reason_codes"],
        "restriction_events": events,
        "restriction_events_sha256": health["restriction_events_sha256"],
        "source_sha256": source_hash,
        "workbook_id": selected_workbook_id,
    }
    request = {
        "schema_version": RESTRICTION_REQUEST_SCHEMA_VERSION,
        "request_id": (
            f"restricted-{source_hash[:16]}-{cohort_hash[:16]}-"
            f"{inventory_hash[:16]}"
        ),
        "policy_version": POLICY_VERSION,
        "source_sha256": source_hash,
        "cohort_sha256": cohort_hash,
        "inventory_sha256": inventory_hash,
        "mode": "restricted_policy",
        "restriction": restriction,
    }
    request["request_sha256"] = _unsigned_hash(request, "request_sha256")
    result = {
        "schema_version": RESTRICTION_RESULT_SCHEMA_VERSION,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "policy_version": POLICY_VERSION,
        "source_sha256": source_hash,
        "output_sha256": source_hash,
        "cohort_sha256": cohort_hash,
        "inventory_sha256": inventory_hash,
        "mode": "restricted",
        "restriction": restriction,
    }
    result["result_sha256"] = _unsigned_hash(result, "result_sha256")
    return request, result


def _identity_documents(source: Path) -> tuple[dict, dict]:
    source_hash = sha256_file(source)
    request = {
        "schema_version": "source-identity-request/v2",
        "request_id": f"identity-{source_hash[:24]}",
        "policy_version": POLICY_VERSION,
        "source_sha256": source_hash,
        "mode": "no_recalculation_required",
    }
    request["request_sha256"] = _unsigned_hash(request, "request_sha256")
    result = {
        "schema_version": "source-identity-result/v2",
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "source_sha256": source_hash,
        "output_sha256": source_hash,
        "mode": "identity",
    }
    result["result_sha256"] = _unsigned_hash(result, "result_sha256")
    return request, result


create_identity_documents = _identity_documents


def prepare_source_generation(
    source_path: str | Path,
    workbook_id: str,
    publication_root: str | Path,
    *,
    health: dict | None = None,
    request: dict | None = None,
    result: dict | None = None,
    trusted_runner_public_key: str | Path | None = None,
    original_source_path: str | Path | None = None,
    inventory: dict | str | Path | None = None,
) -> tuple[Path, dict]:
    """Build a fresh production AST and publish an inactive immutable generation."""
    source = Path(source_path)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", workbook_id):
        raise RecalculationError("workbook ID contains unsafe characters")
    if source.suffix.casefold() != ".xlsx" or not source.is_file():
        raise RecalculationError("prepared source must be an existing .xlsx file")
    health_value = health or inspect_workbook(source)
    if health_value.get("source_sha256") != sha256_file(source):
        raise RecalculationError("source-health report is not bound to prepared source")
    try:
        validate_report(health_value, source)
    except ValueError as exc:
        raise RecalculationError(f"source-health report is invalid: {exc}") from exc
    if (
        health_value.get("schema_version") != "xlsx-source-health/v2"
        or health_value.get("policy_version") != POLICY_VERSION
    ):
        raise RecalculationError(
            "new source generations require current v2 source health"
        )
    if health_value.get("route") not in {
        "pass",
        "restricted_pass",
        "recalc_candidate",
    }:
        raise RecalculationError("source health does not permit AST generation")
    inventory_value = _read_json(inventory) if isinstance(
        inventory, (str, Path)
    ) else inventory
    if (request is None) != (result is None):
        raise RecalculationError("request and result must be supplied together")
    if request is None and health_value.get("route") == "recalc_candidate":
        raise RecalculationError(
            "recalculation candidate cannot use identity evidence"
        )
    if request is None and health_value.get("route") == "pass":
        identity_request, identity_result = _identity_documents(source)
        request = identity_request
        result = identity_result
    elif request is None and health_value.get("route") == "restricted_pass":
        if inventory_value is None:
            raise RecalculationError(
                "restricted pass requires a bound source inventory"
            )
        request, result = create_restriction_documents(
            source,
            health_value,
            inventory_value,
            workbook_id=workbook_id,
        )
    route_schemas = {
        "pass": {
            ("source-identity-request/v2", "source-identity-result/v2"),
            (REQUEST_SCHEMA_VERSION, RESULT_SCHEMA_VERSION),
        },
        "restricted_pass": {
            (
                RESTRICTION_REQUEST_SCHEMA_VERSION,
                RESTRICTION_RESULT_SCHEMA_VERSION,
            )
        },
        "recalc_candidate": {(REQUEST_SCHEMA_VERSION, RESULT_SCHEMA_VERSION)},
    }
    evidence_schemas = (
        request.get("schema_version"),
        result.get("schema_version"),
    )
    if evidence_schemas not in route_schemas[health_value["route"]]:
        raise RecalculationError(
            "source-health route does not match evidence schemas"
        )
    if result.get("output_sha256") != sha256_file(source):
        raise RecalculationError("recalculation result is not bound to prepared source")

    from xl_ast_graph import main as ast_main
    from xl_source_publication import publish_source_generation

    root = Path(publication_root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".source-build-", dir=str(root)))
    try:
        source_root = temporary / "source"
        ast_root = temporary / "ast"
        source_root.mkdir()
        prepared_source = source_root / f"{workbook_id}.xlsx"
        shutil.copyfile(source, prepared_source)
        prepared_health = inspect_workbook(prepared_source)
        if prepared_health["route"] != health_value["route"]:
            raise RecalculationError(
                "source-health route changed while preparing immutable source"
            )
        ast_args = {
            "production": True,
            "workbook_id": workbook_id,
            "max_range_expand": 100,
            "include_constants": True,
            "include_cached_values": True,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            ast_main([
                str(prepared_source),
                "-o",
                str(ast_root),
                "--production",
                "--quiet",
            ])
        ast_dir = ast_root / workbook_id
        destination, manifest = publish_source_generation(
            prepared_source,
            ast_dir,
            root,
            request=request,
            result=result,
            health=prepared_health,
            builder_args=ast_args,
            activate=False,
            trusted_runner_public_key=trusted_runner_public_key,
            original_source_path=original_source_path,
            inventory=inventory_value,
        )
        return destination, manifest
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request_command = commands.add_parser("request")
    request_command.add_argument("source")
    request_command.add_argument("destination")
    request_command.add_argument("--allowed-root", required=True)
    request_command.add_argument("--trusted-runner-public-key", required=True)
    request_command.add_argument("--permitted-engine-version", required=True)
    request_command.add_argument("-o", "--output", required=True)
    validate_command = commands.add_parser("validate")
    validate_command.add_argument("request")
    validate_command.add_argument("--source")
    validate_command.add_argument("--allowed-root", required=True)
    execute_command = commands.add_parser("execute")
    execute_command.add_argument("request")
    execute_command.add_argument("--source")
    execute_command.add_argument("--allowed-root", required=True)
    execute_command.add_argument("--isolation-attestation", required=True)
    execute_command.add_argument("--sandbox-runner", required=True)
    execute_command.add_argument("--timeout-seconds", type=int, default=300)
    execute_command.add_argument("-o", "--output", required=True)
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("source")
    prepare_command.add_argument("--workbook", required=True)
    prepare_command.add_argument("--publication-root", required=True)
    prepare_command.add_argument("--health")
    prepare_command.add_argument("--request")
    prepare_command.add_argument("--result")
    prepare_command.add_argument("--trusted-runner-public-key")
    prepare_command.add_argument("--original-source")
    prepare_command.add_argument("--inventory")
    observe_command = commands.add_parser("diff")
    observe_command.add_argument("before")
    observe_command.add_argument("after")
    args = parser.parse_args(argv)
    try:
        if args.command == "request":
            request = create_recalc_request(
                args.source,
                args.destination,
                allowed_root=args.allowed_root,
                trusted_runner_public_key=args.trusted_runner_public_key,
                engine_constraints={
                    "permitted_versions": [args.permitted_engine_version]
                },
            )
            atomic_write_report(Path(args.output), request)
            print(json.dumps(request, sort_keys=True))
        elif args.command == "validate":
            request = _read_json(args.request)
            validate_recalc_request(
                request,
                source_path=args.source,
                allowed_root=args.allowed_root,
            )
            print(json.dumps({
                "status": "valid",
                "request_id": request["request_id"],
                "request_sha256": request["request_sha256"],
            }, sort_keys=True))
        elif args.command == "execute":
            request = _read_json(args.request)
            result = execute_recalc(
                request,
                MacOSExcelEngine(
                    isolation_attestation=args.isolation_attestation,
                    sandbox_runner=args.sandbox_runner,
                    timeout_seconds=args.timeout_seconds,
                ),
                allowed_root=args.allowed_root,
                source_path=args.source,
            )
            atomic_write_report(Path(args.output), result)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "prepare":
            destination, manifest = prepare_source_generation(
                args.source,
                args.workbook,
                args.publication_root,
                health=_read_json(args.health) if args.health else None,
                request=_read_json(args.request) if args.request else None,
                result=_read_json(args.result) if args.result else None,
                trusted_runner_public_key=args.trusted_runner_public_key,
                original_source_path=args.original_source,
                inventory=args.inventory,
            )
            print(json.dumps({
                "generation_dir": str(destination),
                "generation_id": manifest["generation_id"],
                "source_root": str(destination / manifest["layout"]["source_root"]),
                "source_path": str(
                    destination / manifest["layout"]["source_workbook"]
                ),
                "source_sha256": manifest["bindings"]["source_sha256"],
                "ast_root": str(destination / manifest["layout"]["ast_root"]),
                "ast_dir": str(destination / manifest["layout"]["ast_directory"]),
            }, sort_keys=True))
        else:
            print(json.dumps(
                semantic_workbook_diff(args.before, args.after),
                sort_keys=True,
            ))
        return 0
    except ValueError as exc:
        print(f"source recalc FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
