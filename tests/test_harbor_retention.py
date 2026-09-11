"""Synthetic-only cleanup; public execution stays fail-closed without custody."""
from pathlib import Path
import hashlib
import json
import os
import pytest
from scripts import harbor_retention as R


@pytest.fixture
def layout(tmp_path):
    scratch, kept, protected, journal = [tmp_path / x for x in ('scratch', 'kept', 'protected', 'journal')]
    for path in (scratch, kept, protected, journal):
        path.mkdir()
    (scratch/'a').write_bytes(b'large disposable graph')
    (kept/'a').write_bytes((scratch/'a').read_bytes())
    return scratch, kept, protected, journal


def plan(layout):
    scratch, kept, protected, journal = layout
    return R.plan_cleanup([dict(path=str(scratch/'a'), retained_path=str(kept/'a'))],
                          disposable_roots=[str(scratch)], protected_roots=[str(protected)],
                          retained_roots=[str(kept)])


def run_private(plan, journal, monkeypatch):
    # Exercise filesystem protocol, not public authority acceptance. Public API's
    # closed real-package custody verification is tested in its own suite.
    monkeypatch.setattr(R, '_authorize', lambda authorization, selected: None)
    return R.execute_cleanup(plan, authorization=object(), journal_dir=journal)


def test_plan_is_read_only(layout):
    p = plan(layout)
    assert (layout[0]/'a').exists()
    assert list(layout[3].iterdir()) == []
    assert p['logical_bytes'] == len(b'large disposable graph')
    assert p['physical_space_estimate'] is False
    assert p['files'][0]['source']['sha256'] == hashlib.sha256(b'large disposable graph').hexdigest()


@pytest.mark.parametrize('mutation', ['different', 'symlink', 'directory', 'missing'])
def test_invalid_copy_rejected(layout, mutation):
    copy = layout[1]/'a'
    copy.unlink()
    if mutation == 'different': copy.write_bytes(b'wrong')
    if mutation == 'symlink': copy.symlink_to(layout[0]/'a')
    if mutation == 'directory': copy.mkdir()
    with pytest.raises((ValueError, OSError)):
        plan(layout)
    assert (layout[0]/'a').exists()


@pytest.mark.parametrize('which', ['source', 'parent'])
def test_symlink_source_or_ancestor_rejected(layout, which):
    scratch, kept, protected, journal = layout
    if which == 'source':
        (scratch/'a').unlink()
        (scratch/'a').symlink_to(kept/'a')
    else:
        scratch.rename(scratch.with_name('actual'))
        scratch.symlink_to(scratch.with_name('actual'), target_is_directory=True)
    with pytest.raises(OSError): plan(layout)


@pytest.mark.parametrize('bad', ['relative', 'dotdot', 'lock', 'metadata', 'foreign'])
def test_unsafe_candidate_rejected(layout, bad):
    scratch, kept, protected, journal = layout
    value = {'relative':'scratch/a', 'dotdot':str(scratch/'..'/'scratch'/'a'),
             'lock':str(scratch/'x.lock'), 'metadata':str(scratch/'.harbor-retention-x'),
             'foreign':str(protected/'a')}[bad]
    with pytest.raises(R.RetentionError):
        R.plan_cleanup([dict(path=value,retained_path=str(kept/'a'))],
                       disposable_roots=[str(scratch)],protected_roots=[str(protected)],retained_roots=[str(kept)])


@pytest.mark.parametrize('root', ['protected', 'retained', 'ancestor', 'child', 'duplicate'])
def test_overlapping_roots_rejected(layout, root):
    scratch, kept, protected, journal = layout
    roots = {'protected':[protected], 'retained':[kept], 'ancestor':[scratch.parent],
             'child':[scratch, scratch/'child'], 'duplicate':[scratch,scratch]}[root]
    with pytest.raises(R.RetentionError):
        R.plan_cleanup([dict(path=str(scratch/'a'),retained_path=str(kept/'a'))],
                       disposable_roots=list(map(str,roots)),protected_roots=[str(protected)],retained_roots=[str(kept)])


def test_duplicate_candidates_rejected(layout):
    scratch, kept, protected, journal = layout
    row = dict(path=str(scratch/'a'),retained_path=str(kept/'a'))
    with pytest.raises(R.RetentionError,match='duplicate'):
        R.plan_cleanup([row,row],disposable_roots=[str(scratch)],protected_roots=[str(protected)],retained_roots=[str(kept)])


def test_public_boolean_authority_rejected(layout):
    # Package may be under implementation; missing closed API must also fail.
    with pytest.raises((R.RetentionError, ImportError, AttributeError, ValueError)):
        R.execute_cleanup(plan(layout), authorization=True, journal_dir=layout[3])
    assert (layout[0]/'a').exists()


def test_delete_only_explicit_duplicate_and_idempotent_resume(layout, monkeypatch):
    scratch, kept, protected, journal = layout
    (scratch/'leave').write_bytes(b'untouched')
    p = plan(layout)
    result = run_private(p,journal,monkeypatch)
    assert result['files'] == ['deleted']
    assert not (scratch/'a').exists()
    assert (scratch/'leave').read_bytes() == b'untouched'
    assert (kept/'a').read_bytes() == b'large disposable graph'
    assert type(result['observed_available_bytes_delta']) is int
    assert 'APFS' in result['physical_space_attribution']
    assert run_private(p,journal,monkeypatch)['files'] == ['deleted']


@pytest.mark.parametrize('changed', ['source','copy'])
def test_changes_after_plan_are_never_deleted(layout, monkeypatch, changed):
    p=plan(layout)
    path=layout[0 if changed == 'source' else 1]/'a'
    path.write_bytes(b'changed after plan')
    with pytest.raises(R.RetentionError,match='changed'):
        run_private(p,layout[3],monkeypatch)
    assert (layout[0]/'a').exists()


def test_replaced_source_same_bytes_different_inode_is_rejected(layout, monkeypatch):
    p=plan(layout)
    target=layout[0]/'a'
    target.rename(layout[0]/'old')
    target.write_bytes(b'large disposable graph')
    with pytest.raises(R.RetentionError,match='changed'):
        run_private(p,layout[3],monkeypatch)
    assert target.exists()


@pytest.mark.parametrize('phase', ['intent', 'rename', 'staged', 'unlink'])
def test_interrupted_each_boundary_resumes(layout, monkeypatch, phase):
    p=plan(layout)
    atomic, rename, unlink = R._atomic_json, R.os.rename, R.os.unlink
    crashed=False
    def save(fd,name,value):
        nonlocal crashed
        atomic(fd,name,value)
        if not crashed and value.get('files') == [phase]:
            crashed=True
            raise InterruptedError(phase)
    def move(src,dst,**kw):
        nonlocal crashed
        rename(src,dst,**kw)
        if not crashed and phase == 'rename' and src == 'a':
            crashed=True
            raise InterruptedError(phase)
    def remove(path,**kw):
        nonlocal crashed
        unlink(path,**kw)
        if not crashed and phase == 'unlink' and path == '0':
            crashed=True
            raise InterruptedError(phase)
    monkeypatch.setattr(R,'_atomic_json',save)
    monkeypatch.setattr(R.os,'rename',move)
    monkeypatch.setattr(R.os,'unlink',remove)
    with pytest.raises(InterruptedError):run_private(p,layout[3],monkeypatch)
    assert crashed
    result=run_private(p,layout[3],monkeypatch)
    assert result['files'] == ['deleted']
    assert not (layout[0]/'a').exists()
    assert (layout[1]/'a').exists()


def test_rename_race_preserves_replacement_in_quarantine(layout, monkeypatch):
    p=plan(layout)
    rename=R.os.rename
    def raced(src,dst,**kw):
        if src == 'a':
            # Simulate replacement between final source check and rename.
            (layout[0]/'a').write_bytes(b'other content must survive')
        return rename(src,dst,**kw)
    monkeypatch.setattr(R.os,'rename',raced)
    with pytest.raises(R.RetentionError,match='preserved'):
        run_private(p,layout[3],monkeypatch)
    staged=list(layout[0].glob('.harbor-retention-*/0'))
    assert len(staged)==1 and staged[0].read_bytes() == b'other content must survive'
    assert (layout[1]/'a').exists()


def test_quarantine_symlink_rejected(layout,monkeypatch):
    p=plan(layout)
    (layout[0]/('.harbor-retention-'+R.plan_sha256(p))).symlink_to(layout[1],target_is_directory=True)
    with pytest.raises(OSError):run_private(p,layout[3],monkeypatch)
    assert (layout[0]/'a').exists()


def test_retained_copy_change_during_resume_blocks(layout,monkeypatch):
    p=plan(layout)
    unlink=R.os.unlink
    def stop(path,**kw):
        if path == '0':raise InterruptedError()
        return unlink(path,**kw)
    monkeypatch.setattr(R.os,'unlink',stop)
    with pytest.raises(InterruptedError):run_private(p,layout[3],monkeypatch)
    (layout[1]/'a').write_bytes(b'bad retained data')
    with pytest.raises(R.RetentionError,match='retained'):
        run_private(p,layout[3],monkeypatch)
    assert list(layout[0].glob('.harbor-retention-*/0'))


def test_journal_outside_scratch_required(layout,monkeypatch):
    p=plan(layout)
    with pytest.raises(R.RetentionError,match='overlaps'):
        run_private(p,layout[0],monkeypatch)
    assert (layout[0]/'a').exists()


def test_lock_is_exclusive(layout,monkeypatch):
    import fcntl
    p=plan(layout)
    fd=os.open(layout[3]/'cleanup.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):run_private(p,layout[3],monkeypatch)
    finally:os.close(fd)
    assert (layout[0]/'a').exists()


def test_corrupt_plan_totals_rejected(layout,monkeypatch):
    p=plan(layout);p['logical_bytes']+=1
    with pytest.raises(R.RetentionError,match='totals'):run_private(p,layout[3],monkeypatch)
    assert (layout[0]/'a').exists()


def test_partial_multi_file_cleanup_resumes_remaining_only(layout, monkeypatch):
    scratch, kept, protected, journal = layout
    (scratch/'b').write_bytes(b'second artifact')
    (kept/'b').write_bytes(b'second artifact')
    p=R.plan_cleanup([dict(path=str(scratch/n), retained_path=str(kept/n)) for n in ('a','b')],
                     disposable_roots=[str(scratch)],protected_roots=[str(protected)],retained_roots=[str(kept)])
    unlink=R.os.unlink
    calls=[]
    interrupted=False
    def remove(path,**kw):
        nonlocal interrupted
        if path == '1' and not interrupted:
            interrupted=True
            raise InterruptedError()
        calls.append(path)
        return unlink(path,**kw)
    monkeypatch.setattr(R.os,'unlink',remove)
    with pytest.raises(InterruptedError):run_private(p,journal,monkeypatch)
    assert not (scratch/'a').exists()
    assert run_private(p,journal,monkeypatch)['files'] == ['deleted','deleted']
    assert calls == ['0','1']
    assert (kept/'a').exists() and (kept/'b').exists()


def test_authority_revocation_before_file_prevents_deletion(layout,monkeypatch):
    p=plan(layout)
    calls=0
    def authorize(auth, selected):
        nonlocal calls
        calls+=1
        if calls == 2:raise R.RetentionError('publication changed')
    monkeypatch.setattr(R,'_authorize',authorize)
    with pytest.raises(R.RetentionError,match='publication changed'):
        R.execute_cleanup(p,authorization=object(),journal_dir=layout[3])
    assert (layout[0]/'a').exists()


def test_completed_journal_rejects_recreated_path(layout,monkeypatch):
    p=plan(layout)
    run_private(p,layout[3],monkeypatch)
    (layout[0]/'a').write_bytes(b'new user file')
    with pytest.raises(R.RetentionError,match='recreated'):
        run_private(p,layout[3],monkeypatch)
    assert (layout[0]/'a').read_bytes()==b'new user file'


def test_stale_interrupted_atomic_temporary_does_not_block_resume(layout,monkeypatch):
    p=plan(layout)
    (layout[3]/(R.plan_sha256(p)+'.deadbeef.new')).write_bytes(b'partial interrupted journal')
    assert run_private(p,layout[3],monkeypatch)['files']==['deleted']
