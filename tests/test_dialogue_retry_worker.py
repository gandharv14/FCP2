from dataclasses import asdict,replace
from pathlib import Path
import json,fcntl
import pytest
from scripts.batch import harbor_instruction_producer as p,harbor_tracker_lifecycle as life
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from test_harbor_instruction_producer import setup,dialogue_fixture
from test_dialogue_slot_producer import authoring,values,legacy_failed

@pytest.fixture
def worker_modules():
    from scripts.batch import harbor_instruction_worker,harbor_dialogue_retry
    return harbor_instruction_worker,harbor_dialogue_retry

@pytest.fixture
def failed(setup,authoring,worker_modules,monkeypatch):
    w,r=worker_modules;task,campaign,respond,calls=dialogue_fixture(setup)
    path=Path(task.tracker['path']);before=p.read(task.tracker)
    # Preserve the actual XLSX source supplied by dialogue_fixture.
    for key in ('source_generation','segmentation_generation','curation'):before['bindings'][key]=before['raw_source']
    path.write_text(json.dumps(before))
    authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    changed=life.lease_operation(path,operation='acquire',authority=authority,lease_kind='task_agent',owner='fixture',scope='workbook:0245',expected_revision=before['revision'],expected_sha256=record(path)['sha256'],duration_ns=10**12)
    tracker=p.read(record(path));lease=Lease('fixture',tracker['leases']['task_agent']['token'],changed['revision'],changed['sha256']);task=replace(task,tracker=record(path))
    # Fixture dispatcher retains actual executor; select current tracker after CAS.
    old_dispatch=p.dispatch
    monkeypatch.setattr(p,'dispatch',lambda _task,*a,**kw:old_dispatch(replace(task,tracker=record(path)),*a,**kw))
    worker=w.Worker(RootContext(path.parent),task.run.parent,campaign)
    norm=task.run/'normalization';norm.mkdir();(norm/'normalized.json').write_text('{}')
    selected=ReadinessSelection(Selection('0245',task.stage,w.static.publication._tree_hash(task.stage)[0],tracker['bindings'],norm,task.run/'nat',None,None,model_policy=task.policy),None,None,None,None)
    old,filled=legacy_failed(task,campaign,respond,authoring)
    result=p.consume_dialogue_writer(task,old,campaign);assert not result['passed']
    initial=w.keep(worker.run/'initial.json',{'selection':w.codec.encode(selected),'lease':asdict(lease),'code':{'old-code':'0'*64}})
    prior=None
    for step in w.STEPS[:w.STEPS.index('dialogue-writer')]:
        intent=w.keep(w.step_directory(worker,step)/'intent.json',{'initial':initial,'previous':prior,'step':step})
        prior=w.keep(w.step_directory(worker,step)/'complete.json',{'status':'completed','step':step,'selection':w.codec.encode(selected),'lease':asdict(lease),'intent':intent})
    directory=worker.run/'steps/dialogue-writer';w.request_saved(directory/'request.json',old)
    w.keep(directory/'intent.json',{'step':'dialogue-writer','selection':w.codec.encode(selected),'lease':asdict(lease),'previous':prior,'initial':initial})
    w.keep(directory/'exception.json',{'type':'AdmissionError','message_sha256':r.schema.sha(r.ERROR.encode()),'automatic_replay':False})
    # Source/audit/runtime are explicit fixture stubs; real gate callbacks never
    # exist in the production API. CAS, frozen call, scope and mechanics are real.
    monkeypatch.setattr(r,'_prefix',lambda *a:[record(task.stage/'instruction.md')])
    return w,r,worker,selected,lease,prior,task,campaign,respond,calls

def test_root_retry_cas_resumes_same_round_json_and_preserves_failure(failed):
    w,r,worker,s,lease,prior,task,campaign,respond,calls=failed
    error=record(worker.run/'steps/dialogue-writer/exception.json')
    worker,s,lease,checkpoint=r.authorize(worker,s,lease,checkpoint=prior)
    assert p.read(record(task.tracker['path']))['lane_state'][p.DIALOGUE]['attempt']==1
    pending=w.step(worker,s,lease,checkpoint=checkpoint);assert pending.status=='MODEL_REQUIRED'
    req=w.request_saved(worker.run/'steps/dialogue-writer/structural-correction/request.json')
    respond(req,values(task));done=w.step(worker,s,lease,checkpoint=pending.evidence)
    assert done.status=='STEP_COMPLETE' and done.next_step=='dialogue-paraphraser'
    assert record(error['path'])==error and len(calls)==2
    with pytest.raises(ValueError):r.authorize(worker,s,lease,checkpoint=prior)

@pytest.mark.parametrize('at',['prepared','after_temp_fsync','before_replace','after_replace','committed'])
def test_structural_transaction_interrupts_without_call_or_reset(failed,at):
    w,r,worker,s,lease,prior,task,campaign,respond,calls=failed
    def fail(stage):
        if stage==at:raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):r.authorize(worker,s,lease,checkpoint=prior,_fault=fail)
    permit=record(worker.run/'dialogue-structural-retry-01/permit.json')
    if at in ('after_replace','committed'):
        worker,s,lease,checkpoint=r.recover(worker,permit,lease)
        assert w.step(worker,s,lease,checkpoint=checkpoint).status=='MODEL_REQUIRED'
    else:
        with pytest.raises(ValueError):r.recover(worker,permit,lease)
    assert len(calls)==1

@pytest.mark.parametrize('fault',['unknown_exception','changed_filled','changed_template','wrong_lease','completed','prefix'])
def test_unknown_or_changed_structural_failure_rejects(failed,fault,monkeypatch):
    w,r,worker,s,lease,prior,task,campaign,respond,calls=failed
    if fault=='unknown_exception':(worker.run/'steps/dialogue-writer/exception.json').write_text('{}')
    elif fault=='changed_filled':(task.run/'dialogue/round-01/writer.filled.md').write_text('changed')
    elif fault=='changed_template':(task.run/'dialogue/draft_template.md').write_text('changed')
    elif fault=='wrong_lease':lease=replace(lease,token='wrong')
    elif fault=='completed':(worker.run/'steps/dialogue-writer/complete.json').write_text('{}')
    else:monkeypatch.setattr(r,'_prefix',lambda *a:(_ for _ in ()).throw(ValueError('current gate failed')))
    with pytest.raises((ValueError,KeyError)):r.authorize(worker,s,lease,checkpoint=prior)
    assert len(calls)==1

def test_shared_worker_lock_prevents_parallel_retry(failed):
    _,r,worker,s,lease,prior,*_=failed
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):r.authorize(worker,s,lease,checkpoint=prior)

def test_exact_root_lease_renewal_preserves_old_dialogue_intent(failed):
    w,r,worker,s,lease,prior,t,campaign,respond,calls=failed
    intent=record(worker.run/'steps/dialogue-writer/intent.json')
    authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    renewed=life.lease_operation(t.tracker['path'],operation='renew',authority=authority,lease_kind='task_agent',owner=lease.owner,token=lease.token,scope='workbook:0245',expected_revision=lease.revision,expected_sha256=lease.sha256,duration_ns=10**12)
    current=Lease(lease.owner,lease.token,renewed['revision'],renewed['sha256'])
    worker,s,current,checkpoint=r.authorize(worker,s,current,checkpoint=prior)
    assert w.step(worker,s,current,checkpoint=checkpoint).status=='MODEL_REQUIRED'
    assert record(intent['path'])==intent and p.read(intent)['lease']==asdict(lease) and len(calls)==1

@pytest.mark.parametrize('change',['archive','code'])
def test_changed_archive_or_current_code_rejects_continuation(failed,monkeypatch,change):
    w,r,worker,s,lease,prior,t,campaign,respond,calls=failed
    worker,s,lease,checkpoint=r.authorize(worker,s,lease,checkpoint=prior)
    permit=p.read(p.read(checkpoint)['permit'])
    if change=='archive':
        target=Path(permit['copies'][0]['retained']['path']);target.chmod(0o600);target.write_text('changed')
    else:monkeypatch.setattr(w,'code',lambda _: {'changed':'f'*64})
    with pytest.raises(ValueError):w.step(worker,s,lease,checkpoint=checkpoint)
    assert len(calls)==1
