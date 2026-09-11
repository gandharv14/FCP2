"""Root publisher custody hook mechanics; no production/model/container work."""
from dataclasses import replace
import fcntl
from pathlib import Path
from types import SimpleNamespace
import os
import pytest
from scripts import harbor_publish as P
from scripts import harbor_finalize as F


@pytest.fixture
def setup(tmp_path,monkeypatch):
    trackers=tmp_path/'trackers';trackers.mkdir()
    original=tmp_path/'original.xlsx';original.write_bytes(b'original')
    tracker=trackers/'0001.json'
    P._keep(tracker,{'execution_state':'PROMOTED','bindings':{'original_raw_source':P.record(original)}})
    content=SimpleNamespace(workbook='0001',stage=tmp_path/'scratch-stage',
                            normalization_run=tmp_path/'normalization',naturalization_root=tmp_path/'naturalization',
                            mcp_build_a=None,mcp_build_b=None,trusted_public_key=str(tmp_path/'trusted.pem'))
    request=P.Request(SimpleNamespace(content=content, observation=SimpleNamespace(
        execution={'path':str(tmp_path/'runtime'/'execution.json'),'sha256':'b'*64})),None,{},trackers,tmp_path/'publication',tmp_path/'final',
                      tmp_path/'queue.lock',tmp_path/'slot.lock')
    journal=tmp_path/'journal';journal.mkdir()
    policy=P.VerificationPolicy(tmp_path/'private','sha256:'+'a'*64)
    state=dict(calls=[],status='PROMOTED',finalizer_error=None,publication_error=None)
    def publish(request,journal,*,resume):
        state['calls'].append(('publish',resume))
        if state['publication_error']:raise state['publication_error']
        with P.locks(request):
            state['outcome']=P._keep(journal/'outcome.json',{'status':state['status']})
            return state['outcome']
    def finalize(request):
        assert type(request) is F.FinalizeRequest
        state['calls'].append(('finalize',request))
        # A second open can acquire both locks only if publication released them.
        for path in (request.queue_lock,request.slot_lock):
            fd=os.open(path,os.O_RDWR)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(fd)
        if state['finalizer_error']:raise state['finalizer_error']
        return {'deleted_files':2,'verification_receipt':{'synthetic_boundary':True}}
    monkeypatch.setattr(P,'_run_publication',publish)
    monkeypatch.setattr(F,'finalize',finalize)
    return request,journal,policy,state,tracker


def test_successful_publication_finalizes_after_releasing_queue_and_slot(setup):
    request,journal,policy,state,tracker=setup
    before=tracker.read_bytes()
    result=P.read(P.run(request,journal,verification=policy))
    assert result['status']=='PROMOTED' and result['cleanup']['state']=='COMPLETE'
    assert tracker.read_bytes()==before
    assert [call[0] for call in state['calls']]==['publish','finalize']
    selected=state['calls'][1][1]
    assert selected.source==dict(workbook_id='0001',**P.read(P.record(tracker))['bindings']['original_raw_source'])
    assert selected.bundle==str(policy.bundle) and selected.verifier_image==policy.verifier_image
    assert selected.queue_lock==str(request.queue_lock) and selected.slot_lock==str(request.slot_lock)
    assert selected.disposable_roots==tuple(map(str,sorted([
        request.selection.content.stage,request.selection.content.normalization_run,request.selection.content.naturalization_root,
        Path(request.selection.observation.execution['path']).parent])))


def test_cleanup_failure_keeps_promoted_verdict_and_evidence(setup):
    request,journal,policy,state,tracker=setup
    state['finalizer_error']=ValueError('isolated evidence not accepted')
    before=tracker.read_bytes()
    result=P.read(P.run(request,journal,verification=policy))
    assert result['status']=='PROMOTED' and result['cleanup']['state']=='PENDING'
    assert result['cleanup']['error_type']=='ValueError'
    assert result['publication']==state['outcome']
    assert tracker.read_bytes()==before


def test_failed_publication_does_not_finalize(setup):
    request,journal,policy,state,tracker=setup
    state['status']='RECONCILIATION_REQUIRED'
    result=P.run(request,journal,verification=policy)
    assert result==state['outcome']
    assert [call[0] for call in state['calls']]==['publish']
    assert not (journal/'finalization.json').exists()


def test_publication_exception_does_not_finalize(setup):
    request,journal,policy,state,tracker=setup
    state['publication_error']=ValueError('publication rejected')
    with pytest.raises(ValueError,match='publication rejected'):P.run(request,journal,verification=policy)
    assert [call[0] for call in state['calls']]==['publish']


def test_default_native_publication_does_not_introduce_custody_work(setup):
    request,journal,policy,state,tracker=setup
    result=P.run(request,journal)
    assert result==state['outcome'] and [call[0] for call in state['calls']]==['publish']


@pytest.mark.parametrize('value',[True,{'bundle':'/tmp/x','verifier_image':'sha256:'+'a'*64},'enabled'])
def test_verification_requires_exact_typed_policy(setup,value):
    request,journal,policy,state,tracker=setup
    with pytest.raises(ValueError,match='typed verification policy'):P.run(request,journal,verification=value)
    assert state['calls']==[]


@pytest.mark.parametrize('image',['latest','repository:tag','sha256:abc','sha256:'+'A'*64])
def test_invalid_image_rejected_before_publication(setup,image):
    request,journal,policy,state,tracker=setup
    with pytest.raises(ValueError):P.run(request,journal,verification=replace(policy,verifier_image=image))
    assert state['calls']==[]


@pytest.mark.parametrize('where',['inside','equal','ancestor','relative'])
def test_private_bundle_must_be_disjoint_from_final_task_before_publication(setup,where):
    request,journal,policy,state,tracker=setup
    bundle={'inside':request.output/'private','equal':request.output,'ancestor':request.output.parent,
            'relative':Path('private')}[where]
    with pytest.raises(ValueError):P.run(request,journal,verification=replace(policy,bundle=bundle))
    assert state['calls']==[]


@pytest.mark.parametrize('change',['missing_tracker','malformed_tracker','missing_binding'])
def test_postpublication_preparation_failure_is_cleanup_pending(setup,change):
    request,journal,policy,state,tracker=setup
    if change=='missing_tracker':tracker.unlink()
    elif change=='malformed_tracker':tracker.write_bytes(b'not json')
    else:tracker.write_text('{}')
    result=P.read(P.run(request,journal,verification=policy))
    assert result['status']=='PROMOTED' and result['cleanup']['state']=='PENDING'
    assert [call[0] for call in state['calls']]==['publish']


def test_nested_scratch_roots_are_selected_once(setup):
    request,journal,policy,state,tracker=setup
    content=request.selection.content
    content.mcp_build_a=content.normalization_run/'build-a'
    content.mcp_build_b=content.normalization_run/'build-b'
    P.run(request,journal,verification=policy)
    roots=state['calls'][1][1].disposable_roots
    assert str(content.normalization_run) in roots
    assert str(content.mcp_build_a) not in roots and str(content.mcp_build_b) not in roots
