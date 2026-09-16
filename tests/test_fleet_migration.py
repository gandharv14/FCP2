import io
import json
from pathlib import Path
import tarfile
import pytest
from scripts.fleet import migration as m


def bundle(tmp_path):
    tmp_path = tmp_path.resolve()
    source = tmp_path / "origin" / "checkpoint.json"
    source.parent.mkdir(); source.write_text('{"path":"/Users/original/a","attempt":2,"status":"uncertain"}\n')
    archive = tmp_path / "bundle.tar.gz"
    manifest = m.export_files([source], archive, tmp_path / "manifest.json")
    return source, archive, manifest


def test_original_paths_hashes_and_spent_attempt_bytes_remain(tmp_path):
    source, archive, manifest = bundle(tmp_path)
    dest = tmp_path.resolve() / "offline-linux-namespace"; dest.mkdir()
    receipt = m.import_files(archive, manifest, tmp_path.resolve() / "receipt.json", [source.parent], namespace_root=dest)
    value = m.checked(receipt); copied = Path(value["files"][0]["path"])
    assert copied.read_bytes() == source.read_bytes()
    assert value["files"][0]["original_path"] == str(source)
    assert not value["historical_bytes_rewritten"] and not value["attempts_reset"]
    assert not value["qualification_claimed"] and not value["absolute_paths_preserved"]


def test_later_remote_snapshot_preserves_existing_local_checkpoint(tmp_path):
    source, archive, manifest = bundle(tmp_path)
    remote_bytes = source.read_bytes()
    retained_local_bytes = b'{"attempt":1,"status":"rejected"}\n'
    source.write_bytes(retained_local_bytes)
    destination = tmp_path.resolve() / "offline-return"
    receipt = m.import_files(archive, manifest, tmp_path.resolve() / "snapshot-receipt.json",
                             [source.parent], namespace_root=destination)
    value = m.checked(receipt)
    assert source.read_bytes() == retained_local_bytes
    assert Path(value["files"][0]["path"]).read_bytes() == remote_bytes
    assert value["files"][0]["original_path"] == str(source)
    assert not value["absolute_paths_preserved"] and not value["qualification_claimed"]


def test_corrupt_archive_rejected_before_any_import(tmp_path):
    source, archive, manifest = bundle(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    dest = tmp_path.resolve() / "dest"; dest.mkdir()
    with pytest.raises(ValueError, match="integrity"):
        m.import_files(archive, manifest, tmp_path.resolve() / "receipt", [source.parent], namespace_root=dest)
    assert not list(dest.iterdir())


def test_existing_evidence_never_overwritten(tmp_path):
    source, archive, manifest = bundle(tmp_path)
    dest = tmp_path.resolve() / "dest"; dest.mkdir()
    target = dest / str(source).lstrip("/"); target.parent.mkdir(parents=True); target.write_bytes(b"retained review rejection")
    with pytest.raises(ValueError, match="existing evidence"):
        m.import_files(archive, manifest, tmp_path.resolve() / "receipt", [source.parent], namespace_root=dest)
    assert target.read_bytes() == b"retained review rejection"


def test_late_symlink_member_rejected_before_any_write(tmp_path):
    source, archive, manifest = bundle(tmp_path)
    extra = tmp_path.resolve() / "late"
    rows = m.checked(manifest)
    rows["files"].append({"path": str(extra), "member": str(extra).lstrip("/"), "sha256": m.digest(source), "size": source.stat().st_size, "mode": 0o600})
    archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname=str(source).lstrip("/"))
        info = tarfile.TarInfo(str(extra).lstrip("/")); info.type = tarfile.SYMTYPE; info.linkname = str(source); tar.addfile(info)
    rows["archive_sha256"] = m.digest(archive)
    new = m.retain(tmp_path.resolve() / "malicious-manifest.json", rows)
    dest = tmp_path.resolve() / "dest"; dest.mkdir()
    with pytest.raises(ValueError, match="type or size"):
        m.import_files(archive, new, tmp_path.resolve() / "receipt", [tmp_path.resolve()], namespace_root=dest)
    assert not list(dest.iterdir())


def test_export_credentials_and_symlinks_rejected(tmp_path):
    tmp_path = tmp_path.resolve(); key = tmp_path / ".env"; key.write_text("secret")
    with pytest.raises(ValueError, match="credential"):
        m.export_files([key], tmp_path / "archive", tmp_path / "manifest")
    alias = tmp_path / "alias"; alias.symlink_to(key)
    with pytest.raises(ValueError, match="symlink"):
        m.export_files([alias], tmp_path / "archive", tmp_path / "manifest")
