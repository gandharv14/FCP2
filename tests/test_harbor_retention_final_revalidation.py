"""Late cooperative-writer changes remain quarantined, including on resume."""
from pathlib import Path
import pytest
from scripts import harbor_retention as R


@pytest.fixture
def selected(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    scratch, kept, protected, journal = [root / name for name in ('scratch', 'kept', 'protected', 'journal')]
    for path in (scratch, kept, protected, journal):
        path.mkdir()
    source, copy = scratch/'a', kept/'a'
    source.write_bytes(b'original disposable bytes')
    copy.write_bytes(source.read_bytes())
    plan = R.plan_cleanup([dict(path=str(source), retained_path=str(copy))],
                          disposable_roots=[str(scratch)], protected_roots=[str(protected)],
                          retained_roots=[str(kept)])
    # Filesystem protocol only; these tests do not substitute for public custody.
    monkeypatch.setattr(R, '_authorize', lambda *args: None)
    staged = scratch / ('.harbor-retention-' + R.plan_sha256(plan)) / '0'
    return plan, journal, source, copy, staged


def execute(selected):
    return R.execute_cleanup(selected[0], authorization=object(), journal_dir=selected[1])


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('phase', ['retained_hash', 'journal_write'])
def test_late_candidate_changes_are_preserved(selected, monkeypatch, phase, resume):
    plan, journal, source, copy, staged = selected
    if resume:
        real_unlink = R.os.unlink
        def stop(name, **kwargs):
            if name == '0':
                raise InterruptedError('simulated process interruption')
            return real_unlink(name, **kwargs)
        monkeypatch.setattr(R.os, 'unlink', stop)
        with pytest.raises(InterruptedError):
            execute(selected)
        monkeypatch.setattr(R.os, 'unlink', real_unlink)
    changed = False
    retained_reads = 0
    real_record, real_atomic = R._record, R._atomic_json
    def mutate():
        nonlocal changed
        if staged.exists() and not changed:
            staged.write_bytes(b'late modified bytes must survive')
            changed = True
    def record(path):
        nonlocal retained_reads
        value = real_record(path)
        if Path(path) == copy:
            retained_reads += 1
            if phase == 'retained_hash' and retained_reads == 2:
                mutate()
        return value
    def atomic(fd, name, value):
        real_atomic(fd, name, value)
        if phase == 'journal_write' and value.get('files') == ['staged']:
            mutate()
    monkeypatch.setattr(R, '_record', record)
    monkeypatch.setattr(R, '_atomic_json', atomic)
    with pytest.raises(R.RetentionError, match='preserved'):
        execute(selected)
    assert changed and staged.read_bytes() == b'late modified bytes must survive'
    assert copy.read_bytes() == b'original disposable bytes'
    assert not source.exists()


def test_retained_copy_modified_during_staged_journal_write_blocks(selected, monkeypatch):
    plan, journal, source, copy, staged = selected
    real_atomic = R._atomic_json
    def atomic(fd, name, value):
        real_atomic(fd, name, value)
        if value.get('files') == ['staged']:
            copy.write_bytes(b'changed retained bytes')
    monkeypatch.setattr(R, '_atomic_json', atomic)
    with pytest.raises(R.RetentionError, match='retained copy changed before unlink'):
        execute(selected)
    assert staged.read_bytes() == b'original disposable bytes'
    assert not source.exists()
