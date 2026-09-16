import copy,json,time
from pathlib import Path
from dataclasses import replace
import pytest
from scripts.batch import harbor_queued_publication_lease as Q,harbor_continuation_allowance as A
from scripts import harbor_publish as P
from test_harbor_publish import setup

def selected(tmp_path,monkeypatch):
    import test_harbor_ready_transitions as fixture_module
    from test_harbor_tracker_lifecycle import kw
    from scripts.batch import harbor_tracker_lifecycle as life
    def long_acquire(path,state):return life.lease_operation(path,operation='acquire',duration_ns=1000*10**9,**kw(state,authority=life.RootAuthority('harbor-orchestrator',1000*10**9,lambda *a:True)))
    monkeypatch.setattr(fixture_module,'acquire',long_acquire)
    request,bound,path,state,journal,calls=setup(tmp_path,monkeypatch)
    before=P.read(P.record(path));now=time.time_ns();lease=before['leases']['task_agent']
    original_life=P.lifecycle.lease_operation
    monkeypatch.setattr(P.lifecycle,'lease_operation',lambda *a,**k:original_life(*a,**{**k,'clock':lambda:40}))
    # Fixture clock is already current and its real queued lease remains live.
    directory=tmp_path/'allowance';directory.mkdir();auth=tmp_path/'auth';auth.write_text('Fixture: same-owner queued lease extension only.')
    limits=dict(takeovers=6,lease_duration_ns=Q.DURATION_NS,stage_seconds=7200,memory_bytes=16*1024**3,review_attempts=4,transport_retries=0)
    allowance=A.issue(directory,expected_previous=None,selected_scope=A.scope('0245',before['bindings']['original_raw_source']['sha256']),limits=limits,authorization=A.record(auth),reason='fixture',expires_at_ns=now+86400*10**9,approve=lambda _:True,clock=lambda:now)
    proof=P._keep(tmp_path/'proof.json',dict(status='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED',tracker=P.record(path),models_dispatched=0,publication_mutations=0))
    profile=dict(schema_version=Q.SCHEMA,workbook='0245',source_sha256=before['bindings']['original_raw_source']['sha256'],tracker=P.record(path),ready_proof=proof,allowance=allowance,home=str(tmp_path/'renewal'))
    return request,path,before,profile,calls

def test_real_lifecycle_renewal_and_original_checkpoint_preserved(tmp_path,monkeypatch):
    r,path,before,p,calls=selected(tmp_path,monkeypatch);original=Path(r.checkpoint['path']).read_bytes()
    complete=Q.renew(P,r,p);new=Q.projected(P,r,complete)
    assert Path(r.checkpoint['path']).read_bytes()==original and new.checkpoint!=r.checkpoint
    assert P._checkpoint(new)['lease']['token']==r.lease.token
    after=P.read(P.record(path));assert after['revision']==before['revision']+1
    assert after['lane_state']==before['lane_state'] and after['repairs']==before['repairs'] and not calls
    with pytest.raises(ValueError):Q.renew(P,r,p)

@pytest.mark.parametrize('kind',['task','owner','source','duration','selection'])
def test_projection_rejects_rehashed_foreign_delta(tmp_path,monkeypatch,kind):
    r,path,before,p,calls=selected(tmp_path,monkeypatch);c=Q.renew(P,r,p);v=P.read(c)
    if kind=='selection':
        x=P.read(v['projected_checkpoint']);x['selection']={};fp=Path(v['projected_checkpoint']['path']);fp.write_text(json.dumps(x));v['projected_checkpoint']=P.record(fp)
    else:
        x=P.read(v['after'])
        if kind=='task':x['lane_state'][next(iter(x['lane_state']))]['attempt']+=1
        if kind=='owner':x['leases']['task_agent']['owner']='foreign'
        if kind=='source':x['bindings']['original_raw_source']['sha256']='0'*64
        if kind=='duration':x['leases']['task_agent']['expires_at']+=1
        fp=Path(v['after']['path']);fp.write_text(json.dumps(x));v['after']=P.record(fp)
    fp=Path(c['path']);fp.write_text(json.dumps(v))
    with pytest.raises((ValueError,P.lifecycle.Conflict)):Q.projected(P,r,P.record(fp))
    assert not calls

@pytest.mark.parametrize('field,value',[('status','SYNTHETIC'),('models_dispatched',1),('publication_mutations',1)])
def test_projection_independently_rechecks_actual_ready_proof(tmp_path,monkeypatch,field,value):
    r,path,before,p,calls=selected(tmp_path,monkeypatch);c=Q.renew(P,r,p);v=P.read(c);intent=P.read(v['intent']);proof=P.read(intent['profile']['ready_proof']);proof[field]=value
    fp=Path(intent['profile']['ready_proof']['path']);fp.write_text(json.dumps(proof));intent['profile']['ready_proof']=P.record(fp)
    fp=Path(v['intent']['path']);fp.write_text(json.dumps(intent));v['intent']=P.record(fp)
    fp=Path(c['path']);fp.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='proof'):Q.projected(P,r,P.record(fp))
    assert not calls
