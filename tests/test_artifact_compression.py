import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from scripts import compress_artifacts as compression
from scripts.corpus import digest


@pytest.fixture(autouse=True)
def isolated_worker_lock(tmp_path, monkeypatch):
    # Keep real flock behavior, but never contend with an actual maintenance run.
    monkeypatch.setattr(compression.tempfile, 'gettempdir', lambda: str(tmp_path))


def published_artifact(tmp_path):
    # A real published segmentation fixture, with an ordinary bound large file.
    from test_seg_publication import _case, _stage
    from xl_seg import publication
    source, ast, root, verification = _case(tmp_path)
    staging = _stage(tmp_path, 'case', verification)
    (staging / 'lineage' / 'output.md').write_bytes(b'Historical input documentation.\n' * 50000)
    generation, _ = publication.publish_generation(staging, root, verification,
        ['Sheet!B1'], source_path=source, ast_dir=ast)
    return root, generation / 'lineage' / 'output.md'


@pytest.fixture
def artifact(tmp_path):
    return published_artifact(tmp_path)


@pytest.fixture
def compressed_copy(monkeypatch):
    """Hermetic filesystem metadata double; native behavior has a separate test."""
    actual_snapshot = compression.snapshot
    compressed_inodes = set()
    def copied(source, destination):
        shutil.copy2(source, destination)
        compressed_inodes.add(destination.stat().st_ino)
    def snapshot(path):
        value = actual_snapshot(path)
        if value['inode'] in compressed_inodes:
            value['flags'] |= compression.COMPRESSED
            value['allocated_bytes'] = 4096
        return value
    monkeypatch.setattr(compression, 'native_copy', copied)
    monkeypatch.setattr(compression, 'snapshot', snapshot)
    return copied


def test_completed_copy_preserves_bytes_metadata_and_independent_inode(artifact, compressed_copy):
    root, path = artifact
    before = compression.snapshot(path)
    expected = digest(path)
    events = []
    with path.open('rb') as old:
        result = compression.compress_one(path, root, events.append)
        assert old.read() == path.read_bytes()
        assert os.fstat(old.fileno()).st_ino == before['inode']
    assert result['status'] == 'compressed'
    assert digest(path) == expected
    assert path.stat().st_ino != before['inode'] and path.stat().st_nlink == 1
    for key in ('mode', 'uid', 'gid', 'mtime_ns', 'logical_bytes'):
        assert result['after'][key] == before[key]
    assert events[0]['status'] == 'replacement_prepared'
    compression.validate_generation(path.parents[1])
    assert not list(root.glob('.compression-stage-*'))


@pytest.mark.parametrize('damage', ['empty', 'corrupt', 'no_flag'])
def test_ditto_success_is_not_evidence(artifact, monkeypatch, damage):
    root, path = artifact
    before, expected = path.stat(), digest(path)
    def copy(source, destination):
        shutil.copy2(source, destination)
        if damage == 'empty':
            destination.write_bytes(b'')
        elif damage == 'corrupt':
            with destination.open('r+b') as stream:
                stream.write(b'wrong')
    monkeypatch.setattr(compression, 'native_copy', copy)
    if damage == 'no_flag':
        actual = compression.snapshot
        def missing_flag(p):
            value = actual(p)
            if '.compression-stage-' in str(p):
                value['allocated_bytes'] = 4096
            return value
        monkeypatch.setattr(compression, 'snapshot', missing_flag)
    if damage == 'no_flag':
        result = compression.compress_one(path, root, lambda _: pytest.fail('no-op attempted commit'))
        assert result['status'] == 'skipped_native_not_compressed'
        assert result['staged']['flags'] & compression.COMPRESSED == 0
        assert result['staged']['logical_bytes'] == before.st_size
        assert result['sha256'] == expected
    else:
        with pytest.raises(ValueError, match='logical hash or size'):
            compression.compress_one(path, root, lambda _: None)
    assert path.stat().st_ino == before.st_ino and digest(path) == expected
    assert not list(root.glob('.compression-stage-*'))


def test_interrupted_copy_preserves_original(artifact, monkeypatch):
    root, path = artifact
    before, expected = path.stat(), digest(path)
    def copy(source, destination):
        destination.write_bytes(b'partial')
        raise KeyboardInterrupt()
    monkeypatch.setattr(compression, 'native_copy', copy)
    with pytest.raises(KeyboardInterrupt):
        compression.compress_one(path, root, lambda _: None)
    assert path.stat().st_ino == before.st_ino and digest(path) == expected
    assert not list(root.glob('.compression-stage-*'))


@pytest.mark.parametrize('change', ['artifact', 'manifest', 'symlink'])
def test_race_rejected_at_commit_boundary(artifact, compressed_copy, change):
    root, path = artifact
    original_inode = path.stat().st_ino
    manifest = path.parents[1] / 'generation-manifest.json'
    def event(_):
        if change == 'artifact':
            path.write_bytes(b'concurrent write')
        elif change == 'manifest':
            manifest.write_text(manifest.read_text() + ' ')
        else:
            alternate = root / 'outside.md'
            alternate.write_bytes(b'untouched')
            path.unlink()
            path.symlink_to(alternate)
    with pytest.raises(ValueError, match='changed|symlink'):
        compression.compress_one(path, root, event)
    if change == 'symlink':
        assert path.is_symlink() and path.read_bytes() == b'untouched'
    else:
        assert path.stat().st_ino == original_inode


def test_changed_mode_rejected(artifact, compressed_copy, monkeypatch):
    root, path = artifact
    def copy(source, destination):
        compressed_copy(source, destination)
        destination.chmod(0o600 if stat.S_IMODE(source.stat().st_mode) != 0o600 else 0o644)
    monkeypatch.setattr(compression, 'native_copy', copy)
    with pytest.raises(ValueError, match='metadata'):
        compression.compress_one(path, root, lambda _: None)


@pytest.mark.parametrize('damage', ['missing_manifest', 'invalid_schema', 'invalid_identity', 'changed_sibling'])
def test_incomplete_or_invalid_generation_rejected(artifact, damage):
    root, path = artifact
    manifest = path.parents[1] / 'generation-manifest.json'
    if damage == 'missing_manifest':
        manifest.unlink()
    elif damage == 'changed_sibling':
        (manifest.parent / 'bands.csv').write_text('tampered')
    else:
        document = json.loads(manifest.read_text())
        document['schema_version' if damage == 'invalid_schema' else 'identity'] = 'invalid'
        manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        compression.eligible(path, root)


def test_symlink_ancestry_and_hardlinks_rejected(artifact, tmp_path):
    root, path = artifact
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink ancestry'):
        compression.eligible(alias / path.relative_to(root), alias)
    os.link(path, root / 'hardlink')
    with pytest.raises(ValueError, match='one link'):
        compression.eligible(path, root)


def test_small_and_already_compressed_are_skipped(artifact, compressed_copy):
    root, path = artifact
    compression.compress_one(path, root, lambda _: None)
    assert compression.eligible(path, root) is None
    small = path.parent / 'small'
    small.write_bytes(b'small')
    assert compression.eligible(small, root) is None


def test_probe_requires_real_logical_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(compression, 'native_copy', lambda a, b: b.write_bytes(b''))
    with pytest.raises(ValueError, match='logical hash or size'):
        compression.capability_probe(tmp_path)
    assert not list(tmp_path.glob('.compression-probe-*'))


def test_probe_success_and_report_progress(artifact, compressed_copy, tmp_path):
    root, path = artifact
    report = tmp_path / 'run.json'
    result = compression.run([root], report, apply=True)
    assert result['complete']
    events = sorted(Path(str(report) + '.events').glob('*.json'))
    statuses = [json.loads(p.read_text())['status'] for p in events]
    assert statuses == ['started', 'capability', 'replacement_prepared', 'compressed']
    assert 'total_saved_bytes' not in json.loads(report.read_text())
    with pytest.raises(ValueError, match='immutable'):
        compression.run([root], report, apply=True)


def test_failed_run_retains_immutable_progress(artifact, monkeypatch, tmp_path):
    root, path = artifact
    expected = digest(path)
    report = tmp_path / 'failure.json'
    monkeypatch.setattr(compression, 'native_copy', lambda a, b: b.write_bytes(b''))
    with pytest.raises(ValueError):
        compression.run([root], report, apply=True)
    saved = json.loads(report.read_text())
    assert not saved['complete'] and 'error' in saved
    assert digest(path) == expected
    events = list(Path(str(report) + '.events').glob('*.json'))
    assert len(events) >= 3


def test_killed_run_journal_cannot_be_reused(tmp_path):
    report = tmp_path / 'run.json'
    journal = compression.Journal(report)
    journal.event({'status': 'started'})
    with pytest.raises(FileExistsError):
        compression.Journal(report)
    assert not report.exists()


def test_inspect_does_not_invoke_native_copy(artifact, monkeypatch, tmp_path):
    monkeypatch.setattr(compression, 'native_copy', lambda *a: pytest.fail('native call during inspect'))
    root, path = artifact
    expected = path.stat().st_ino
    result = compression.run([root], tmp_path / 'inspect.json')
    assert result['files'][0]['status'] == 'eligible'
    assert path.stat().st_ino == expected


@pytest.mark.skipif(os.environ.get('PIPELINE_TEST_NATIVE_COMPRESSION') != '1',
                    reason='explicit synthetic native APFS capability integration')
def test_native_compression_integration(artifact, tmp_path):
    root, path = artifact
    original = path.read_bytes()
    before = path.stat()
    result = compression.run([root], tmp_path / 'native.json', apply=True)
    record = result['files'][0]
    assert record['status'] == 'compressed'
    assert path.read_bytes() == original and path.stat().st_ino != before.st_ino
    assert path.stat().st_blocks < before.st_blocks
    assert path.stat().st_flags & compression.COMPRESSED
    assert compression.eligible(path, root) is None


def test_incompressible_exact_copy_is_skipped(artifact, monkeypatch):
    root, path = artifact
    inode, expected = path.stat().st_ino, digest(path)
    monkeypatch.setattr(compression, 'native_copy', shutil.copy2)
    result = compression.compress_one(path, root, lambda _: pytest.fail('no replacement intent'))
    assert result['status'] == 'skipped_native_not_compressed'
    assert path.stat().st_ino == inode and digest(path) == expected


def test_native_copy_has_bounded_timeout(monkeypatch, tmp_path):
    seen = {}
    def call(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(compression.subprocess, 'run', call)
    with pytest.raises(subprocess.TimeoutExpired):
        compression.native_copy(tmp_path / 'a', tmp_path / 'b')
    assert seen['timeout'] == 300 and seen['check'] is True


def test_worker_lock_rejects_concurrent_worker(tmp_path):
    with compression.exclusive(tmp_path / 'worker.lock'):
        with pytest.raises(BlockingIOError):
            with compression.exclusive(tmp_path / 'worker.lock'):
                pytest.fail('second worker acquired lock')


def test_interrupt_after_intent_keeps_original_and_durable_event(artifact, compressed_copy, tmp_path):
    root, path = artifact
    expected, inode = digest(path), path.stat().st_ino
    journal = compression.Journal(tmp_path / 'pending.json')
    def event(value):
        journal.event(value)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        compression.compress_one(path, root, event)
    assert digest(path) == expected and path.stat().st_ino == inode
    assert json.loads((journal.directory / '00000000.json').read_text())['status'] == 'replacement_prepared'
    assert not list(root.glob('.compression-stage-*'))


def test_report_cannot_mutate_generation_artifact_set(artifact):
    root, path = artifact
    with pytest.raises(ValueError, match='outside immutable generations'):
        compression.run([root], path.parent / 'compression-report.json')
    assert not (path.parent / 'compression-report.json').exists()


def test_event_publication_never_overwrites_history(tmp_path):
    event = tmp_path / 'event.json'
    compression.write_once(event, {'status': 'first'})
    before = event.read_bytes()
    with pytest.raises(FileExistsError):
        compression.write_once(event, {'status': 'replacement'})
    assert event.read_bytes() == before and event.stat().st_nlink == 1
    assert not list(tmp_path.glob('.compression-report-*'))


def test_historical_validation_failure_is_reported_and_scan_continues(artifact, compressed_copy, monkeypatch, tmp_path):
    root, path = artifact
    # Add a second independently published generation, not a hard link or mutation.
    other = tmp_path / 'other'
    other.mkdir()
    from test_seg_publication import _case, _stage
    from xl_seg import publication
    source, ast, root2, verification = _case(other)
    stage = _stage(other, 'case', verification)
    (stage / 'lineage' / 'output.md').write_bytes(b'Valid repetitive documentation.\n' * 50000)
    generation, _ = publication.publish_generation(stage, root2, verification,
        ['Sheet!B1'], source_path=source, ast_dir=ast)
    original = compression.validate_generation
    def validate(g):
        if g == path.parents[1]:
            raise ValueError('historical AST builder is stale')
        return original(g)
    monkeypatch.setattr(compression, 'validate_generation', validate)
    result = compression.run([root, root2], tmp_path / 'mixed.json', apply=True)
    statuses = {row['path']: row['status'] for row in result['files']}
    assert statuses[str(path)] == 'ineligible'
    assert statuses[str(generation / 'lineage' / 'output.md')] == 'compressed'
    assert result['committed_files'] == 1 and result['complete']


def test_canary_commit_limit(artifact, compressed_copy, tmp_path):
    root, path = artifact
    later = tmp_path / 'zz-later'
    later.mkdir()
    root2, path2 = published_artifact(later)
    untouched_inode = path2.stat().st_ino
    result = compression.run([root, root2], tmp_path / 'canary.json', apply=True, max_files=1)
    assert path2.stat().st_ino == untouched_inode
    assert not compression.snapshot(path2)['flags'] & compression.COMPRESSED
    assert result['committed_files'] == 1 and result['commit_limit_reached']
    assert sum(r['status'] == 'compressed' for r in result['files']) == 1


@pytest.mark.parametrize('limit', [0, -1, True])
def test_invalid_commit_limit_rejected_before_report(tmp_path, limit):
    report = tmp_path / 'unused.json'
    with pytest.raises(ValueError, match='positive integer'):
        compression.run([tmp_path], report, max_files=limit)
    assert not report.exists() and not Path(str(report) + '.events').exists()


def test_changed_evidence_after_selection_aborts_run(artifact, compressed_copy, monkeypatch, tmp_path):
    root, path = artifact
    original = compression.eligible
    calls = 0
    def select(p, r):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('evidence changed after selection')
        return original(p, r)
    monkeypatch.setattr(compression, 'eligible', select)
    with pytest.raises(ValueError, match='after selection'):
        compression.run([root], tmp_path / 'race-run.json', apply=True)
    assert not json.loads((tmp_path / 'race-run.json').read_text())['complete']


@pytest.mark.parametrize('suffix', ['.xlsx', '.XLSX', '.xlsm', '.XLSM', '.xlsb', '.zip', '.bin'])
def test_workbook_and_binary_formats_skipped_without_native_call(tmp_path, monkeypatch, suffix):
    generation = tmp_path / 'generations' / ('a' * 64)
    generation.mkdir(parents=True)
    binary = generation / ('source' + suffix)
    binary.write_bytes(b'repetitive synthetic binary fixture' * 40000)
    manifest = {'generation_id': generation.name, 'artifacts': {binary.name: {
        'sha256': digest(binary), 'size_bytes': binary.stat().st_size}}}
    (generation / 'generation-manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(compression, 'native_copy', lambda *a: pytest.fail('native copy of binary'))
    monkeypatch.setattr(compression, 'validate_generation', lambda *a: pytest.fail('unnecessary binary validation'))
    before = digest(binary), binary.stat().st_ino
    assert compression.eligible(binary, tmp_path) is None
    result = compression.run([tmp_path], tmp_path / 'report.json', apply=True)
    assert result['complete'] and result['committed_files'] == 0
    assert (digest(binary), binary.stat().st_ino) == before


@pytest.mark.parametrize('suffix', ['.JSON', '.CSV', '.MD', '.TXT'])
def test_text_extension_filter_is_case_insensitive(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('proof' + suffix)
    path.write_bytes(b'a' * compression.MIN_BYTES)
    def reached(path, root):
        raise RuntimeError('text reached completion validation')
    monkeypatch.setattr(compression, 'completed_artifact', reached)
    with pytest.raises(RuntimeError, match='text reached'):
        compression.eligible(path, tmp_path)


def test_strict_copy_validation_still_requires_compression_flag(tmp_path):
    source, copied = tmp_path / 'source', tmp_path / 'copied'
    source.write_bytes(b'exact input' * 200000)
    shutil.copy2(source, copied)
    before = compression.snapshot(source)
    expected = digest(source)
    compression.verified_copy(copied, before, expected, require_compressed=False)
    with pytest.raises(ValueError, match='UF_COMPRESSED'):
        compression.verified_copy(copied, before, expected)


def test_compressed_copy_without_allocated_saving_is_skipped(artifact, monkeypatch):
    root, path = artifact
    actual = compression.snapshot
    def fake_flag(p):
        value = actual(p)
        if '.compression-stage-' in str(p):
            value['flags'] |= compression.COMPRESSED
        return value
    monkeypatch.setattr(compression, 'snapshot', fake_flag)
    monkeypatch.setattr(compression, 'native_copy', shutil.copy2)
    inode = path.stat().st_ino
    result = compression.compress_one(path, root, lambda _: pytest.fail('no savings attempted commit'))
    assert result['status'] == 'skipped_no_allocated_saving'
    assert path.stat().st_ino == inode


def test_uncompressed_noop_does_not_hide_changed_original(artifact, monkeypatch):
    root, path = artifact
    def copy(source, destination):
        shutil.copy2(source, destination)
        source.write_bytes(b'concurrent source change')
    monkeypatch.setattr(compression, 'native_copy', copy)
    with pytest.raises(ValueError, match='changed during native copy'):
        compression.compress_one(path, root, lambda _: None)
    assert path.read_bytes() == b'concurrent source change'
