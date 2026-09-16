"""Lossless transfer into the original absolute namespace, never evidence rewriting.

Archives contain only explicitly selected regular files. Import verifies the
entire archive before touching destinations and refuses changed existing files.
The receipt attests custody only: it cannot renew a lease, reset an attempt,
authorize a model call, or qualify historical evidence on a different runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import tarfile
import tempfile
import time

SCHEMA = "harbor-fleet-custody/v1"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("canonical absolute path required")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink custody is not supported")
    return path


def binding(path):
    path = safe(path)
    return {"path": str(path), "sha256": digest(path)}


def checked(row):
    if binding(row["path"]) != row:
        raise ValueError("hash-bound input changed")
    return json.loads(Path(row["path"]).read_bytes())


def retain(path, value):
    path = safe(path)
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".custody-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(name, path)
    finally:
        os.unlink(name)
    return binding(path)


def export_files(paths, archive, manifest):
    """Export an exact file list; callers enumerate and authorize its closure."""
    archive, manifest = safe(archive), safe(manifest)
    if archive.exists() or manifest.exists():
        raise ValueError("fresh archive and manifest required")
    selected = sorted({safe(p) for p in paths})
    if not selected:
        raise ValueError("empty custody transfer")
    rows = []
    for path in selected:
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("only regular files may be transferred")
        # Credentials are provisioned separately, never bundled with evidence.
        if path.name in {".env", "credentials.json", "application_default_credentials.json"} or path.suffix in {".pem", ".key"}:
            raise ValueError("credential-like file cannot enter evidence bundle")
        rows.append({**binding(path), "size": path.stat().st_size,
                     "mode": stat.S_IMODE(path.stat().st_mode) & 0o777,
                     "member": str(path).lstrip("/")})
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for row in rows:
            path = Path(row["path"])
            if binding(path)["sha256"] != row["sha256"]:
                raise ValueError("source changed during transfer")
            info = tar.gettarinfo(str(path), arcname=row["member"])
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with path.open("rb") as source:
                tar.addfile(info, source)
        stream.flush(); os.fsync(stream.fileno())
    value = {"schema_version": SCHEMA, "archive_sha256": digest(archive),
             "source_system": platform.system(), "created_at_ns": time.time_ns(),
             "files": rows, "path_policy": "preserve_absolute_paths",
             "historical_bytes_rewritten": False, "qualification_claimed": False}
    return retain(manifest, value)


def import_files(archive, manifest_binding, receipt, allowed_roots, *, namespace_root=Path("/")):
    """Verify then install absent files; namespace_root is for offline QA only."""
    archive = safe(archive); namespace_root = safe(namespace_root)
    value = checked(manifest_binding)
    if value.get("schema_version") != SCHEMA or value.get("path_policy") != "preserve_absolute_paths":
        raise ValueError("unsupported custody manifest")
    if digest(archive) != value["archive_sha256"]:
        raise ValueError("archive integrity failure")
    roots = tuple(safe(p) for p in allowed_roots)
    rows = value["files"]
    if not rows or len({row["path"] for row in rows}) != len(rows):
        raise ValueError("empty or duplicate custody files")
    expected = {}
    for row in rows:
        path = safe(row["path"])
        if not any(path == root or path.is_relative_to(root) for root in roots):
            raise ValueError("destination outside authorized roots")
        member = str(path).lstrip("/")
        if row["member"] != member or ".." in PurePosixPath(member).parts:
            raise ValueError("archive path differs from retained namespace")
        target = safe(namespace_root / member)
        if target.exists() and (not target.is_file() or digest(target) != row["sha256"]):
            raise ValueError("existing evidence differs; reconciliation required")
        expected[member] = (row, target)
    # Validate every member, including late malicious entries, before any write.
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != set(expected):
            raise ValueError("archive membership differs")
        for member in members:
            row, _ = expected[member.name]
            if not member.isfile() or member.size != row["size"]:
                raise ValueError("archive member type or size differs")
            source = tar.extractfile(member); h = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                h.update(chunk)
            if h.hexdigest() != row["sha256"]:
                raise ValueError("archive file hash differs")
        installed = []
        for member in members:
            row, target = expected[member.name]
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                safe(target)
                fd, name = tempfile.mkstemp(prefix=".custody-", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as out, tar.extractfile(member) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            out.write(chunk)
                        out.flush(); os.fsync(out.fileno())
                    os.chmod(name, row["mode"])
                    os.link(name, target)
                finally:
                    os.unlink(name)
            if digest(target) != row["sha256"]:
                raise ValueError("installed evidence changed")
            installed.append({"original_path": row["path"], **binding(target)})
    return retain(receipt, {"schema_version": "harbor-fleet-custody-receipt/v1",
        "manifest": manifest_binding, "archive_sha256": value["archive_sha256"],
        "destination_system": platform.system(), "namespace_root": str(namespace_root),
        "files": installed, "absolute_paths_preserved": namespace_root == Path("/"),
        "historical_bytes_rewritten": False, "qualification_claimed": False,
        "attempts_reset": False, "created_at_ns": time.time_ns()})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    e = sub.add_parser("export"); e.add_argument("--files-json", type=Path, required=True)
    for q in (e, sub.add_parser("import")):
        q.add_argument("--archive", type=Path, required=True); q.add_argument("--manifest", type=Path, required=True)
        if q is not e:
            q.add_argument("--manifest-sha256", required=True); q.add_argument("--receipt", type=Path, required=True)
            q.add_argument("--allowed-root", type=Path, action="append", required=True)
    a = p.parse_args()
    if a.action == "export": result = export_files(json.loads(a.files_json.read_text()), a.archive, a.manifest)
    else: result = import_files(a.archive, {"path": str(a.manifest), "sha256": a.manifest_sha256}, a.receipt, a.allowed_root)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
