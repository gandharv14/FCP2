import shutil

import pytest

from scripts import compact_artifacts as compact
from scripts.corpus import digest


def test_compaction_retains_bytes_and_independent_files(tmp_path, monkeypatch):
    first, second = tmp_path / 'a.csv', tmp_path / 'b.csv'
    first.write_bytes(b'proof bytes')
    second.write_bytes(first.read_bytes())
    monkeypatch.setattr(compact.subprocess, 'run',
                        lambda args, **kwargs: shutil.copy2(args[-2], args[-1]))
    record = compact.clone_duplicate(first, second, digest(first))
    assert digest(first) == digest(second) == record['sha256']
    assert first.stat().st_ino != second.stat().st_ino
    second.write_bytes(b'later mutation')
    assert first.read_bytes() == b'proof bytes'


def test_changed_destination_is_never_replaced(tmp_path, monkeypatch):
    first, second = tmp_path / 'a.csv', tmp_path / 'b.csv'
    first.write_bytes(b'proof bytes')
    second.write_bytes(first.read_bytes())
    def changed(args, **kwargs):
        shutil.copy2(args[-2], args[-1])
        second.write_bytes(b'concurrent edit')
    monkeypatch.setattr(compact.subprocess, 'run', changed)
    with pytest.raises(ValueError, match='changed during compaction'):
        compact.clone_duplicate(first, second, digest(first))
    assert second.read_bytes() == b'concurrent edit'


def test_interrupted_clone_preserves_original_artifact(tmp_path, monkeypatch):
    first, second = tmp_path / 'a.csv', tmp_path / 'b.csv'
    first.write_bytes(b'proof bytes')
    second.write_bytes(first.read_bytes())
    before = second.stat().st_ino
    def interrupted(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_bytes(b'partial clone')
        raise OSError('copy interrupted')
    monkeypatch.setattr(compact.subprocess, 'run', interrupted)
    with pytest.raises(OSError, match='copy interrupted'):
        compact.clone_duplicate(first, second, digest(first))
    assert second.read_bytes() == first.read_bytes()
    assert second.stat().st_ino == before
    assert not list(tmp_path.glob('.compact-*'))


def test_nonidentical_or_symlink_evidence_cannot_be_compacted(tmp_path):
    first, second = tmp_path / 'a.csv', tmp_path / 'b.csv'
    first.write_bytes(b'proof bytes')
    second.write_bytes(b'other proof')
    with pytest.raises(ValueError, match='bytes changed'):
        compact.clone_duplicate(first, second, digest(first))
    second.unlink()
    second.symlink_to(first)
    with pytest.raises(ValueError, match='regular file'):
        compact.clone_duplicate(first, second, digest(first))


@pytest.mark.parametrize("filename", ["nodes.csv", "lineage.json", "source.xlsx"])
def test_only_manifest_bound_completed_generations_are_selected(tmp_path, filename):
    import json
    from scripts.compact_artifacts import completed_artifact
    from scripts.corpus import digest
    generation = tmp_path / 'generations' / ('a' * 64)
    generation.mkdir(parents=True)
    csv = generation / filename
    csv.write_text('node,reference\n1,A1\n')
    assert not completed_artifact(csv, tmp_path)
    manifest = generation / 'generation-manifest.json'
    document = {'generation_id': generation.name,
        'artifacts': {filename: {'size_bytes': csv.stat().st_size, 'sha256': digest(csv)}}}
    manifest.write_text(json.dumps(document))
    assert completed_artifact(csv, tmp_path)
    csv.write_text('partial changed writer output')
    assert not completed_artifact(csv, tmp_path)
    manifest.write_text('{interrupted')
    assert not completed_artifact(csv, tmp_path)
