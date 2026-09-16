"""Verified offline custody for evidence from a different operating system.

Remote absolute names are provenance, not paths to resolve on this machine.
Only the explicitly separate local destination is checked against local symlinks.
This module cannot install an executable continuation at historical paths.
"""
from pathlib import Path,PurePosixPath
import hashlib,os,platform,tarfile,tempfile,time
from scripts.fleet.migration import SCHEMA,safe,checked,digest,binding,retain

def remote_path(value):
    text=str(value)
    path=PurePosixPath(text)
    if not path.is_absolute() or path==PurePosixPath('/') or '..' in path.parts or '\x00' in text or str(path)!=text:
        raise ValueError('canonical absolute remote namespace required')
    return path

def import_snapshot(archive, manifest_binding, receipt, allowed_roots, *, namespace_root):
    """Import remote evidence into an explicit local, non-executable snapshot."""
    archive = safe(archive); namespace_root = safe(namespace_root)
    if namespace_root == Path("/"):
        raise ValueError("offline snapshot requires a separate destination namespace")
    value = checked(manifest_binding)
    if value.get("schema_version") != SCHEMA or value.get("path_policy") != "preserve_absolute_paths":
        raise ValueError("unsupported custody manifest")
    if digest(archive) != value["archive_sha256"]:
        raise ValueError("archive integrity failure")
    roots = tuple(remote_path(p) for p in allowed_roots)
    rows = value["files"]
    if not rows or len({row["path"] for row in rows}) != len(rows):
        raise ValueError("empty or duplicate custody files")
    expected = {}
    for row in rows:
        path = remote_path(row["path"])
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
        "attempts_reset": False, "execution_authorized": False, "remote_paths_interpreted_lexically": True, "created_at_ns": time.time_ns()})

