"""Coordinator mechanics with a synthetic fixed package authority boundary.

These tests never represent model/runtime observations as production evidence.
Owning package tests separately exercise actual isolated-verifier authorization.
"""
from dataclasses import dataclass,replace
import hashlib
import json
import os
from pathlib import Path
from types import ModuleType
import sys
import pytest
from scripts import harbor_finalize as F
from scripts import harbor_retention as R


def sha(data):return hashlib.sha256(data).hexdigest()


@pytest.fixture
def setup(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    original=source/'0001.xlsx';original.write_bytes(b'original')
    tracker=tmp_path/'tracker.json';tracker.write_bytes(b'promoted tracker')
    pointer=tmp_path/'release-pointer.json';pointer.write_bytes(b'current release')
    release=tmp_path/'release.json';release.write_bytes(b'fixed release')
    key=tmp_path/'trusted-key.pem';key.write_bytes(b'key')
    task=tmp_path/'final-task';task.mkdir();(task/'instruction.md').write_text('task')
    code=tmp_path/'code';code.mkdir();(code/'pipeline.py').write_bytes(b'code')
    scratch=tmp_path/'0001-scratch';scratch.mkdir();(scratch/'graph').write_bytes(b'graph data')
    queue=tmp_path/'publication.lock';queue.touch()
    slot=tmp_path/'slot.lock';slot.touch()
    request=F.FinalizeRequest(dict(path=str(original),sha256=sha(b'original'),workbook_id='0001'),
                             str(tracker),str(key),str(tmp_path/'private-bundle'),'sha256:'+'a'*64,(str(scratch),),str(queue),str(slot))
    state=dict(calls=[],manifest=None,fail_verify=False)
    package=ModuleType('scripts.harbor_verification_package')
    @dataclass(frozen=True)
    class CleanupAuthorization:
        digest:str
    package.CleanupAuthorization=CleanupAuthorization
    def build(source,tracker_path,*,trusted_public_key,destination):
        state['calls'].append('build')
        bundle=Path(destination);bundle.mkdir();(bundle/'objects').mkdir()
        payload=(scratch/'graph').read_bytes();digest=sha(payload)
        (bundle/'objects'/digest).write_bytes(payload)
        manifest=dict(schema_version='harbor-verification-package/v1',source=source,
                      tracker=F._binding(str(tracker)),release_pointer=F._binding(str(pointer)),
                      release_manifest=F._binding(str(release)),release_id='r'*64,final_task_root=str(task),
                      trusted_public_key=trusted_public_key,files=[dict(path=str(scratch/'graph'),sha256=digest,size=len(payload))],
                      directories=[str(scratch)],scratch_roots=[str(scratch)],
                      objects=[dict(sha256=digest,size=len(payload))],protected_roots=[str(source_dir),str(code)],
                      protected_files=[str(original),str(key),str(tracker),str(pointer),str(release)])
        state['manifest']=manifest
        (bundle/'manifest.json').write_bytes(R.canonical(manifest))
        return sha(R.canonical(manifest))
    source_dir=source
    def inspect(bundle,*,expected_manifest_sha256):
        state['calls'].append('inspect')
        manifest=state['manifest']
        assert sha(R.canonical(manifest))==expected_manifest_sha256
        return manifest
    def verify(bundle,*,expected_manifest_sha256,verifier_image):
        state['calls'].append('verify')
        if state['fail_verify']:raise R.RetentionError('isolated verification rejected')
        return {'synthetic_boundary':True}
    def authorize(bundle,*,expected_manifest_sha256,verifier_image,plan):
        state['calls'].append('authorize')
        verify(bundle,expected_manifest_sha256=expected_manifest_sha256,verifier_image=verifier_image)
        return CleanupAuthorization(R.plan_sha256(plan))
    def validate(auth,plan):
        state['calls'].append('validate')
        assert type(auth) is CleanupAuthorization and auth.digest==R.plan_sha256(plan)
        assert (task/'instruction.md').read_text()=='task'
        return {'synthetic_boundary':True}
    package.build_package=build;package.inspect_package=inspect;package.verify_package=verify
    def recover(*args, **kwargs):
        state['calls'].append('recover')
        raise R.RetentionError('unassociated package has no fresh native proof in this fixture')
    package.recover_package=recover
    package.authorize_cleanup=authorize;package.validate_cleanup_authorization=validate
    monkeypatch.setitem(sys.modules,'scripts.harbor_verification_package',package)
    import scripts
    monkeypatch.setattr(scripts,'harbor_verification_package',package,raising=False)
    return request,state,scratch,package


def test_package_then_verify_then_cleanup_and_resume(setup):
    request,state,scratch,package=setup
    (scratch/'extra-copy').write_bytes(b'graph data')
    (scratch/'unknown').write_bytes(b'keep unrepresented data')
    (scratch/'worker.lock').write_bytes(b'graph data')
    result=F.finalize(request)
    assert result['deleted_files']==1
    assert not (scratch/'graph').exists() and (scratch/'extra-copy').exists()
    assert (scratch/'unknown').exists() and (scratch/'worker.lock').exists()
    assert state['calls'][:4]==['build','inspect','authorize','verify']
    assert 'validate' in state['calls'][4:]
    assert {row['reason'] for row in result['preserved']}=={'protected','not captured by owning verification proof'}
    assert result['production_started'] is False
    assert result['verification_receipt']=={'synthetic_boundary':True}
    saved=json.loads((Path(request.bundle).with_name(Path(request.bundle).name+'.cleanup')/'result.json').read_bytes())
    assert saved['verification_receipt']==result['verification_receipt']
    assert F.finalize(request)['deleted_files']==1
    assert state['calls'].count('build')==1


def test_verification_failure_never_deletes_and_can_resume(setup):
    request,state,scratch,package=setup
    state['fail_verify']=True
    with pytest.raises(R.RetentionError,match='verification rejected'):F.finalize(request)
    assert (scratch/'graph').exists()
    state['fail_verify']=False
    assert F.finalize(request)['deleted_files']==1
    assert state['calls'].count('build')==1


def test_association_is_private_and_binds_exact_current_release(setup):
    request,state,scratch,package=setup
    F.finalize(request)
    bundle=Path(request.bundle);association=json.loads(bundle.with_name(bundle.name+'.association.json').read_bytes())
    assert association['manifest_sha256']==sha(R.canonical(state['manifest']))
    assert association['request']['source']==request.source
    assert not Path(state['manifest']['final_task_root']) in bundle.parents
    Path(state['manifest']['release_pointer']['path']).write_bytes(b'new release')
    with pytest.raises(R.RetentionError,match='publication identity changed'):F.finalize(request)


def test_existing_unknown_bundle_not_adopted(setup):
    request,state,scratch,package=setup
    Path(request.bundle).mkdir()
    with pytest.raises(R.RetentionError,match='unassociated'):F.finalize(request)
    assert state['calls']==['recover'] and (scratch/'graph').exists()


@pytest.mark.parametrize('field', ['verifier_image','disposable_roots','source'])
def test_request_changed_after_association_rejected(setup,field):
    request,state,scratch,package=setup
    state['fail_verify']=True
    with pytest.raises(R.RetentionError):F.finalize(request)
    changed={'verifier_image':'sha256:'+'b'*64,'disposable_roots':(str(scratch/'other'),),
             'source':{**request.source,'workbook_id':'0002'}}[field]
    with pytest.raises(R.RetentionError,match='association differs'):
        F.finalize(replace(request,**{field:changed}))
    assert (scratch/'graph').exists()


@pytest.mark.parametrize('kind',['unselected','cross_workbook','protected_file','protected_root','final_task'])
def test_unsafe_scratch_root_rejected_before_cleanup(setup,kind):
    request,state,scratch,package=setup
    build=package.build_package
    def altered(*args,**kw):
        build(*args,**kw)
        m=state['manifest']
        if kind in ('unselected','cross_workbook'):m['scratch_roots']=[]
        elif kind=='protected_file':m['protected_files'].append(str(scratch/'graph'))
        elif kind=='protected_root':m['protected_roots'].append(str(scratch))
        elif kind=='final_task':m['final_task_root']=str(scratch)
        return sha(R.canonical(m))
    package.build_package=altered
    with pytest.raises(R.RetentionError):F.finalize(request)
    assert (scratch/'graph').exists()
    assert 'authorize' not in state['calls']


def test_symlink_in_scratch_rejected_before_deletion(setup):
    request,state,scratch,package=setup
    (scratch/'link').symlink_to(scratch/'graph')
    with pytest.raises(R.RetentionError,match='symlink'):F.finalize(request)
    assert (scratch/'graph').exists()


def test_no_eligible_files_still_requires_independent_verification(setup):
    request,state,scratch,package=setup
    build=package.build_package
    def altered(*args,**kw):
        result=build(*args,**kw)
        (scratch/'graph').write_bytes(b'new unpublished data')
        return result
    package.build_package=altered
    result=F.finalize(request)
    assert result['deleted_files']==0 and result['cleanup'] is None
    assert state['calls']==['build','inspect','verify']
    assert (scratch/'graph').read_bytes()==b'new unpublished data'


def test_partial_deletion_resume_uses_frozen_plan(setup,monkeypatch):
    request,state,scratch,package=setup
    (scratch/'another').write_bytes(b'graph data')
    build=package.build_package
    def capture_both(*args,**kw):
        build(*args,**kw)
        state['manifest']['files'].append({**state['manifest']['files'][0],'path':str(scratch/'another')})
        return sha(R.canonical(state['manifest']))
    package.build_package=capture_both
    unlink=R.os.unlink;failed=False
    def interrupted(name,**kw):
        nonlocal failed
        if name=='1' and not failed:
            failed=True;raise InterruptedError()
        return unlink(name,**kw)
    monkeypatch.setattr(R.os,'unlink',interrupted)
    with pytest.raises(InterruptedError):F.finalize(request)
    assert F.finalize(request)['deleted_files']==2
    assert state['calls'].count('build')==1


@pytest.mark.parametrize('image',['latest','image:tag','sha256:abc','sha256:'+'A'*64])
def test_only_digest_pinned_verifier_image_accepted(setup,image):
    request,state,scratch,package=setup
    with pytest.raises(R.RetentionError,match='immutable verifier'):
        F.finalize(replace(request,verifier_image=image))
    assert state['calls']==[]


def test_disposable_roots_must_be_immutable_tuple(setup):
    request,state,scratch,package=setup
    with pytest.raises(R.RetentionError,match='immutable scratch'):
        F.finalize(replace(request,disposable_roots=list(request.disposable_roots)))
    assert state['calls']==[]


@pytest.mark.parametrize('which',['queue_lock','slot_lock'])
def test_coordination_lock_is_held_through_verification(setup,which):
    import fcntl
    request,state,scratch,package=setup
    verify=package.verify_package
    # authorize_cleanup's synthetic verifier closes over its function; instead
    # inspect the held lock at the authorization boundary before it verifies.
    authorize=package.authorize_cleanup
    def observed(*args,**kw):
        fd=os.open(getattr(request,which),os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
        return authorize(*args,**kw)
    package.authorize_cleanup=observed
    assert F.finalize(request)['deleted_files']==1


@pytest.mark.parametrize('which',['queue_lock','slot_lock'])
def test_busy_existing_lock_blocks_before_build(setup,which):
    import fcntl
    request,state,scratch,package=setup
    fd=os.open(getattr(request,which),os.O_RDWR)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):F.finalize(request)
    finally:os.close(fd)
    assert state['calls']==[] and (scratch/'graph').exists()


def test_coordination_locks_must_be_distinct(setup):
    request,state,scratch,package=setup
    with pytest.raises(R.RetentionError,match='distinct'):
        F.finalize(replace(request,slot_lock=request.queue_lock))
    assert state['calls']==[]


def test_coordination_lock_cannot_be_under_disposable_root(setup):
    request,state,scratch,package=setup
    lock=scratch/'worker.lock';lock.touch()
    with pytest.raises(R.RetentionError,match='coordination lock'):
        F.finalize(replace(request,slot_lock=str(lock)))
    assert state['calls']==[]


def test_coordination_lock_symlink_rejected(setup):
    request,state,scratch,package=setup
    link=Path(request.slot_lock);link.unlink();link.symlink_to(request.queue_lock)
    with pytest.raises(OSError):F.finalize(request)
    assert state['calls']==[]
