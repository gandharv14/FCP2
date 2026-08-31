#!/usr/bin/env python3
"""Export and import hash-bound Excel recalculation transfer bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping


SCHEMA_VERSION = "excel-recalc-transfer/v1"
ALLOWED_NAMES = frozenset({"manifest.json", "request.json", "source.xlsx", "result.json", "output.xlsx"})
MAX_MEMBER_BYTES = 2 * 1024 * 1024 * 1024


class BundleError(ValueError):
    """A transfer bundle is malformed or no longer hash-bound."""


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise BundleError(f"required JSON file is unavailable: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"JSON file is not an object: {path}")
    return value


def _file_record(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise BundleError(f"bundle input is unavailable: {path}")
    return {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _manifest(kind: str, files: Mapping[str, Path], **bindings: object) -> dict:
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "files": {name: _file_record(path) for name, path in files.items()},
        **bindings,
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    return manifest


def _write_bundle(output: Path, files: Mapping[str, Path], manifest: dict) -> None:
    if output.exists() or output.is_symlink():
        raise BundleError("bundle output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", canonical_json(manifest))
            for name, path in files.items():
                archive.write(path, name)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def export_request(request_path: Path, source: Path, output: Path) -> dict:
    request = _read_json(request_path)
    source_hash = sha256_file(source)
    if (
        request.get("request_sha256") is None
        or request.get("source_sha256") != source_hash
        or request.get("source_size_bytes") != source.stat().st_size
    ):
        raise BundleError("request does not bind the exported source")
    files = {"request.json": request_path, "source.xlsx": source}
    manifest = _manifest(
        "request",
        files,
        request_sha256=request["request_sha256"],
        source_sha256=source_hash,
    )
    _write_bundle(output, files, manifest)
    return manifest


def export_result(
    request_path: Path,
    result_path: Path,
    workbook: Path,
    output: Path,
) -> dict:
    request = _read_json(request_path)
    result = _read_json(result_path)
    workbook_hash = sha256_file(workbook)
    if (
        result.get("request_sha256") != request.get("request_sha256")
        or result.get("source_sha256") != request.get("source_sha256")
        or result.get("output_sha256") != workbook_hash
        or result.get("output_size_bytes") != workbook.stat().st_size
    ):
        raise BundleError("result does not bind the request and output workbook")
    receipt = (
        result.get("engine_evidence", {}).get("runner_receipt", {})
        if isinstance(result.get("engine_evidence"), dict)
        else {}
    )
    payload = receipt.get("signed_payload", {}) if isinstance(receipt, dict) else {}
    if (
        not isinstance(payload, dict)
        or payload.get("request_sha256") != request.get("request_sha256")
        or payload.get("source_sha256") != request.get("source_sha256")
        or payload.get("output_sha256") != workbook_hash
    ):
        raise BundleError("result lacks a hash-bound runner receipt")
    files = {
        "request.json": request_path,
        "result.json": result_path,
        "output.xlsx": workbook,
    }
    manifest = _manifest(
        "result",
        files,
        request_sha256=request["request_sha256"],
        source_sha256=request["source_sha256"],
        output_sha256=workbook_hash,
    )
    _write_bundle(output, files, manifest)
    return manifest


def _validated_members(bundle: Path, expected_kind: str) -> tuple[dict, dict[str, bytes]]:
    if bundle.is_symlink() or not bundle.is_file():
        raise BundleError("transfer bundle is unavailable")
    try:
        with zipfile.ZipFile(bundle) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)) or any(name not in ALLOWED_NAMES for name in names):
                raise BundleError("bundle contains unsafe or duplicate members")
            if "manifest.json" not in names:
                raise BundleError("bundle has no manifest")
            for member in members:
                unix_mode = member.external_attr >> 16
                if unix_mode and not (unix_mode & 0o170000) in (0, 0o100000):
                    raise BundleError("bundle contains a non-regular member")
                if member.file_size > MAX_MEMBER_BYTES:
                    raise BundleError("bundle member exceeds the size limit")
            contents = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleError("transfer bundle is unreadable") from exc
    try:
        manifest = json.loads(contents["manifest.json"])
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError("bundle manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != expected_kind
    ):
        raise BundleError("bundle manifest schema or kind is invalid")
    unsigned = dict(manifest)
    claimed_manifest_hash = unsigned.pop("manifest_sha256", None)
    if claimed_manifest_hash != sha256_bytes(canonical_json(unsigned)):
        raise BundleError("bundle manifest hash does not match")
    file_records = manifest.get("files")
    if not isinstance(file_records, dict) or set(file_records) != set(contents) - {"manifest.json"}:
        raise BundleError("bundle file inventory does not match")
    for name, record in file_records.items():
        value = contents[name]
        if (
            not isinstance(record, dict)
            or record.get("sha256") != sha256_bytes(value)
            or record.get("size_bytes") != len(value)
        ):
            raise BundleError(f"bundle member hash does not match: {name}")
    return manifest, contents


def import_bundle(bundle: Path, destination: Path, expected_kind: str) -> dict:
    manifest, contents = _validated_members(bundle, expected_kind)
    destination.mkdir(parents=True, exist_ok=True)
    expected_files = set(contents) - {"manifest.json"}
    if any((destination / name).exists() for name in expected_files):
        raise BundleError("bundle destination already contains an output file")
    staged: list[tuple[Path, Path]] = []
    try:
        for name in sorted(expected_files):
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{name}.", dir=destination
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(contents[name])
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((temporary, destination / name))
        for temporary, final in staged:
            os.replace(temporary, final)
        return manifest
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_request_command = commands.add_parser("export-request")
    export_request_command.add_argument("--request", type=Path, required=True)
    export_request_command.add_argument("--source", type=Path, required=True)
    export_request_command.add_argument("--output", type=Path, required=True)
    import_request_command = commands.add_parser("import-request")
    import_request_command.add_argument("--bundle", type=Path, required=True)
    import_request_command.add_argument("--destination", type=Path, required=True)
    export_result_command = commands.add_parser("export-result")
    export_result_command.add_argument("--request", type=Path, required=True)
    export_result_command.add_argument("--result", type=Path, required=True)
    export_result_command.add_argument("--workbook", type=Path, required=True)
    export_result_command.add_argument("--output", type=Path, required=True)
    import_result_command = commands.add_parser("import-result")
    import_result_command.add_argument("--bundle", type=Path, required=True)
    import_result_command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "export-request":
        manifest = export_request(args.request, args.source, args.output)
    elif args.command == "import-request":
        manifest = import_bundle(args.bundle, args.destination, "request")
    elif args.command == "export-result":
        manifest = export_result(
            args.request, args.result, args.workbook, args.output
        )
    else:
        manifest = import_bundle(args.bundle, args.destination, "result")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BundleError as error:
        print(f"recalculation transfer: {error}", file=os.sys.stderr)
        raise SystemExit(1)
