from dataclasses import replace
from pathlib import Path
import pytest
from scripts import harbor_instruction_campaign as C
from scripts.batch import harbor_environment_refresh as F
from scripts.batch.harbor_admission import read,record
from test_harbor_instruction_campaign import inprocess
from test_harbor_environment_worker import worker,selected,setup


def prepared(worker,monkeypatch,tmp_path):
    from test_harbor_environment_worker import test_real_worker_through_phase2_uses_independent_review_and_generic_profile as build
    build(worker,monkeypatch)
    s,old=worker;tracker=record(s.bootstrap.policy.tracker_root/'0001.json');before=read(tracker)
    lease=replace(old,revision=before['revision'],sha256=tracker['sha256'])
    current=C.base.worker._read_checkpoint(s,'environment')
    prior=F.keep(tmp_path/'previous.json',dict(status='ENVIRONMENT_READY',step='environment',selection=C.codec.encode(current),lease=C.base.encode(lease),evidence=[]))
    proof=F.prepare(s,lease,prior,current,'environment',tmp_path/'refresh')
    lease,current,committed=F.commit(s,lease,proof)
    controls=C.base.Controls(tmp_path/'actual.lock')
    descriptor=F.keep(tmp_path/'descriptor.json',{**C.base._descriptor(s,lease,controls,()),'schema_version':'harbor-environment-coordinator-refresh/v1','refresh':proof})
    result=F.keep(tmp_path/'ready.json',dict(status='ENVIRONMENT_READY',descriptor=descriptor,selection=C.codec.encode(current),lease=C.base.encode(lease),evidence=[proof,committed]))
    return s,lease,controls,descriptor,result,proof,committed


def test_refreshed_current_full_environment_handoff_uses_owning_proof(worker,monkeypatch,tmp_path,inprocess):
    s,lease,c,d,r,p,committed=prepared(worker,monkeypatch,tmp_path)
    monkeypatch.setattr(C.base.worker,'_setup',lambda *a:pytest.fail('ordinary stale setup route'))
    monkeypatch.setattr(C.base.worker,'_read_checkpoint',lambda *a:pytest.fail('ordinary stale checkpoint route'))
    outcome=C.run('start',dict(environment_descriptor=d,checkpoint=r,worker_run=str(tmp_path/'instructions')),c,tmp_path/'start')
    value=read(read(outcome)['result']);assert value['status']=='INSTRUCTION_READY',value
    assert p in value['evidence'] and committed in value['evidence']
    assert C.base.decode(value['lease']).token==lease.token
    assert not (tmp_path/'instructions').exists()


@pytest.mark.parametrize('what',['proof','committed','selection','missing_committed','foreign_result'])
def test_refreshed_handoff_rejects_tampered_or_incomplete_evidence(worker,monkeypatch,tmp_path,inprocess,what):
    s,lease,c,d,r,p,committed=prepared(worker,monkeypatch,tmp_path)
    if what=='proof':Path(p['path']).write_text('{}')
    elif what=='committed':Path(committed['path']).write_text('{}')
    elif what=='missing_committed':Path(committed['path']).unlink()
    else:
        value=read(r)
        if what=='selection':
            current=C.codec.decode(value['selection']);value['selection']=C.codec.encode(replace(current,content=replace(current.content,stage_sha256='0'*64)))
        else:value['status']='STEP_COMPLETE'
        r=F.keep(tmp_path/'altered-result.json',value)
    try:
        out=C.run('start',dict(environment_descriptor=d,checkpoint=r,worker_run=str(tmp_path/'instructions')),c,tmp_path/'start')
    except (ValueError,FileNotFoundError,KeyError):pass
    else:assert read(out)['status']=='RECONCILIATION_REQUIRED'
    assert not (tmp_path/'instructions').exists()


def test_refreshed_phase1_continuation_handoff_binds_actual_final_checkpoint(worker,monkeypatch,tmp_path,inprocess):
    from test_environment_checkpoint_refresh import test_current_refresh_continues_through_phase2_without_reentering_legacy_bridge as build
    build(worker,monkeypatch,tmp_path)
    s,old=worker;tracker=record(s.bootstrap.policy.tracker_root/'0001.json');before=read(tracker)
    lease=replace(old,revision=before['revision'],sha256=tracker['sha256'])
    proof=record(tmp_path/'refresh/prepared.json');committed=record(tmp_path/'refresh/committed.json')
    last=record(tmp_path/'refresh/continuation/environment.json');current=C.codec.decode(read(last)['selection'])
    controls=C.base.Controls(tmp_path/'actual.lock')
    d=F.keep(tmp_path/'descriptor.json',{**C.base._descriptor(s,lease,controls,()),'schema_version':'harbor-environment-coordinator-refresh/v1','refresh':proof})
    r=F.keep(tmp_path/'ready.json',dict(status='ENVIRONMENT_READY',descriptor=d,selection=C.codec.encode(current),lease=C.base.encode(lease),evidence=[proof,committed,last]))
    monkeypatch.setattr(C.base.worker,'_setup',lambda *a:pytest.fail('ordinary setup route'))
    monkeypatch.setattr(C.base.worker,'_read_checkpoint',lambda *a:pytest.fail('ordinary checkpoint route'))
    out=C.run('start',dict(environment_descriptor=d,checkpoint=r,worker_run=str(tmp_path/'instructions')),controls,tmp_path/'start')
    v=read(read(out)['result']);assert v['status']=='INSTRUCTION_READY',v
    assert last in v['evidence'] and proof in v['evidence']
