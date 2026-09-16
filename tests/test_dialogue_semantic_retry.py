from dataclasses import asdict,replace
from pathlib import Path
import json,fcntl
import pytest
from scripts.batch import harbor_instruction_producer as p,harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_worker as w,harbor_dialogue_semantic_retry as retry
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from test_harbor_instruction_producer import setup,dialogue_fixture
from test_dialogue_slot_producer import values

@pytest.fixture
def failed(setup,monkeypatch):
    task,campaign,respond,calls=dialogue_fixture(setup)
    path=Path(task.tracker['path']);before=p.read(task.tracker)
    # Keep the original source/disclosure used by completed first-call discovery.
    # Replacing it after initialization is correctly rejected by the new proof.
    for key in ('source_generation','segmentation_generation','curation'):
        before['bindings'][key]=before['raw_source']
    path.write_text(json.dumps(before));authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    changed=life.lease_operation(path,operation='acquire',authority=authority,lease_kind='task_agent',owner='fixture',scope='workbook:0245',expected_revision=before['revision'],expected_sha256=record(path)['sha256'],duration_ns=10**12)
    tracker=p.read(record(path));lease=Lease('fixture',tracker['leases']['task_agent']['token'],changed['revision'],changed['sha256']);task=replace(task,tracker=record(path))
    old_dispatch=p.dispatch
    monkeypatch.setattr(p,'dispatch',lambda _task,*a,**kw:old_dispatch(replace(task,tracker=record(path)),*a,**kw))
    worker=w.Worker(RootContext(path.parent),task.run.parent,campaign)
    norm=task.run/'normalization';norm.mkdir();(norm/'normalized.json').write_text('{}')
    selected=ReadinessSelection(Selection('0245',task.stage,w.static.publication._tree_hash(task.stage)[0],tracker['bindings'],norm,task.run/'nat',None,None,model_policy=task.policy),None,None,None,None)
    writer=p.prepare_dialogue_writer(task);respond(writer,values(task));assert p.consume_dialogue_writer(task,writer,campaign)['passed']
    para=p.prepare_dialogue_paraphraser(task);respond(para,values(task));assert p.consume_dialogue_paraphraser(task,para,writer,campaign)['passed']
    review=p.prepare_dialogue_review(task);claims=p.read(record(task.run/'dialogue/claims.json'))
    value={'agent_model':'gpt-5.6-sol-high','round':1,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in claims['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'fail','findings':['The wording leaves the calculation ambiguous.']},'passed':False}
    respond(review,value);assert not p.consume_dialogue_review(task,review,writer,para,campaign)['passed']
    initial=w.keep(worker.run/'initial.json',{'selection':w.codec.encode(selected),'lease':asdict(lease),'code':{'old-code':'0'*64}})
    prior=None
    for step in w.STEPS[:w.STEPS.index('dialogue-review')]:
        intent=w.keep(w.step_directory(worker,step)/'intent.json',{'initial':initial,'previous':prior,'step':step})
        prior=w.keep(w.step_directory(worker,step)/'complete.json',{'status':'completed','step':step,'selection':w.codec.encode(selected),'lease':asdict(lease),'intent':intent})
    directory=worker.run/'steps/dialogue-review';w.request_saved(directory/'request.json',review)
    intent=w.keep(directory/'intent.json',{'step':'dialogue-review','selection':w.codec.encode(selected),'lease':asdict(lease),'previous':prior,'initial':initial})
    blocked=w.keep(directory/'blocked.json',{'step':'dialogue-review','status':'DIALOGUE_REPAIR_REQUIRED','selection':w.codec.encode(selected),'lease':asdict(lease),'intent':intent})
    # Only external source/runtime fixture prefix is stubbed. Actual model
    # journals, prior author/reviewer validation, lease/CAS and worker are real.
    monkeypatch.setattr(retry.structural,'_prefix',lambda *a:[record(task.stage/'instruction.md')])
    return worker,selected,lease,blocked,task,campaign,respond,calls

def test_remaining_round_preserves_calls_and_prepares_new_writer(failed):
    worker,s,lease,blocked,t,campaign,respond,calls=failed
    old=record(t.run/'dialogue/round-01/review.json')
    worker,s,lease,permit=retry.authorize(worker,s,lease,blocked=blocked)
    result=w.step(worker,s,lease,checkpoint=permit)
    assert result.status=='MODEL_REQUIRED' and len(calls)==3
    request=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json')
    assert request.prepared.ordinal==2 and request.prepared.limit==2
    assert p.read(request.prepared.request)['messages'][1]['content'].find('previous_review')>=0
    respond(request,values(t));done=w.step(worker,s,lease,checkpoint=result.evidence)
    assert done.next_step=='dialogue-paraphraser' and len(calls)==4 and record(old['path'])==old
    with pytest.raises(ValueError):retry.authorize(worker,s,lease,blocked=blocked)

@pytest.mark.parametrize('at',['prepared','after_temp_fsync','before_replace','after_replace','committed'])
def test_interruptions_preserve_consumption_and_explicit_recovery(failed,at):
    worker,s,lease,blocked,t,_,_,calls=failed
    def interrupt(stage):
        if stage==at:raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):retry.authorize(worker,s,lease,blocked=blocked,_fault=interrupt)
    permit=record(worker.run/'dialogue-semantic-retry-02/permit.json')
    if at in ('after_replace','committed'):
        worker,s,new,complete=retry.recover(worker,permit,lease)
        assert w.step(worker,s,new,checkpoint=complete).status=='MODEL_REQUIRED'
    else:
        with pytest.raises(ValueError):retry.recover(worker,permit,lease)
    assert len(calls)==3

@pytest.mark.parametrize('fault',['changed_draft','changed_review','second_round','unknown_status','wrong_lease','prefix','code','archive'])
def test_changed_or_unqualified_context_rejected(failed,fault,monkeypatch):
    worker,s,lease,blocked,t,_,_,calls=failed
    if fault in ('code','archive'):
        worker,s,lease,complete=retry.authorize(worker,s,lease,blocked=blocked)
        if fault=='code':monkeypatch.setattr(w,'code',lambda _: {'changed':'f'*64})
        else:
            b=p.read(p.read(complete)['permit'])['copies'][0]['retained'];Path(b['path']).chmod(0o600);Path(b['path']).write_text('tampered')
        with pytest.raises(ValueError):w.step(worker,s,lease,checkpoint=complete)
        return
    if fault=='changed_draft':(t.run/'dialogue/round-01/draft.md').write_text('changed')
    elif fault=='changed_review':(t.run/'dialogue/round-01/review.json').write_text('{}')
    elif fault=='second_round':(t.run/'dialogue/round-02').mkdir()
    elif fault=='unknown_status':Path(blocked['path']).write_text('{}')
    elif fault=='wrong_lease':lease=replace(lease,token='wrong')
    else:monkeypatch.setattr(retry.structural,'_prefix',lambda *a:(_ for _ in ()).throw(ValueError('current runtime failed')))
    with pytest.raises((ValueError,KeyError)):retry.authorize(worker,s,lease,blocked=blocked)
    assert len(calls)==3

def test_same_identity_renewal_is_not_budget_reset(failed):
    worker,s,lease,blocked,t,_,_,calls=failed
    authority=life.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    r=life.lease_operation(t.tracker['path'],operation='renew',authority=authority,lease_kind='task_agent',owner=lease.owner,token=lease.token,scope='workbook:0245',expected_revision=lease.revision,expected_sha256=lease.sha256,duration_ns=10**12)
    current=Lease(lease.owner,lease.token,r['revision'],r['sha256'])
    worker,s,current,permit=retry.authorize(worker,s,current,blocked=blocked)
    assert w.step(worker,s,current,checkpoint=permit).status=='MODEL_REQUIRED' and len(calls)==3

def test_exact_worker_codec_and_no_second_structural_scope(failed):
    from scripts import harbor_instruction_campaign as campaign
    worker,s,lease,blocked,*_=failed
    worker,s,lease,permit=retry.authorize(worker,s,lease,blocked=blocked)
    assert campaign.worker_decode(campaign.worker_encode(worker))==worker
    changed=campaign.worker_encode(worker);changed['dialogue_semantic_retry']={'anything':'not evidence'}
    with pytest.raises(ValueError):campaign.worker_decode(changed)
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):retry.authorize(worker,s,lease,blocked=blocked)

def test_complete_second_review_has_source_proof_and_no_third_round(failed):
    worker,s,lease,blocked,t,campaign,respond,calls=failed
    worker,s,lease,permit=retry.authorize(worker,s,lease,blocked=blocked)
    next=w.step(worker,s,lease,checkpoint=permit)
    req=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json');respond(req,values(t))
    result=w.step(worker,s,lease,checkpoint=next.evidence)
    next=w.step(worker,result.selection,result.lease,checkpoint=result.evidence)
    para=w.request_saved(w.step_directory(worker,'dialogue-paraphraser')/'request.json');respond(para,values(t))
    result=w.step(worker,next.selection,next.lease,checkpoint=next.evidence)
    next=w.step(worker,result.selection,result.lease,checkpoint=result.evidence)
    review=w.request_saved(w.step_directory(worker,'dialogue-review')/'request.json');body=json.loads(p.read(review.prepared.request)['messages'][1]['content'])
    assert body['round']==2 and body['operand_evidence']['target_values_disclosed'] is False
    assert 'operand_evidence' not in p.read(req.prepared.request)['messages'][1]['content']
    response=p.read(record(t.run/'dialogue/round-01/review.json'));response['round']=2
    respond(review,response)
    result=w.step(worker,next.selection,next.lease,checkpoint=next.evidence)
    assert result.status=='DIALOGUE_REPAIR_REQUIRED' and len(calls)==6
    with pytest.raises(ValueError):retry.authorize(worker,result.selection,result.lease,blocked=result.evidence)
    with pytest.raises(ValueError):p.prepare_dialogue_writer(replace(t,tracker=record(t.tracker['path'])),3)

@pytest.mark.parametrize('fault',[None,'source','key','missing_inputs'])
def test_second_review_current_readiness_rederives_operand_projection(failed,fault):
    from scripts.batch.harbor_semantic_readiness import dialogue_review
    worker,s,lease,blocked,t,campaign,respond,calls=failed
    writer=p.prepare_dialogue_writer(t,2);respond(writer,values(t));p.consume_dialogue_writer(t,writer,campaign)
    para=p.prepare_dialogue_paraphraser(t,2);respond(para,values(t));p.consume_dialogue_paraphraser(t,para,writer,campaign)
    review=p.prepare_dialogue_review(t,2);response=p.read(record(t.run/'dialogue/round-01/review.json'))
    response.update(round=2,passed=True);response['naturalness']={'verdict':'pass','findings':[]}
    respond(review,response);assert p.consume_dialogue_review(t,review,writer,para,campaign)['passed']
    v=p.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue');notes=t.run/'reviewed-notes.md'
    notes.write_text(v.strip_claim_comments((t.run/'dialogue/round-02/draft.md').read_text()))
    claims=record(t.run/'dialogue/claims.json');before=p.read(t.tracker)
    source=before['bindings']['source_file'];key=record(t.stage/'tests/answer_key.json')
    args=dict(claims=claims,notes=record(notes),reviewed_claims=claims,policy=t.policy,writer_calls=(p.observed(writer,campaign),p.observed(para,campaign)),workbook='0245',current_source_sha256=source['sha256'],original_sha256=before['bindings']['original_raw_source']['sha256'],source=source,answer_key=key,task_dir=t.stage,root=worker.root)
    if fault=='source':args['source']=before['raw_source']
    elif fault=='key':Path(key['path']).write_text('{}');args['answer_key']=record(key['path'])
    elif fault=='missing_inputs':args['source']=None
    if fault is None:assert dialogue_review(p.observed(review,campaign),**args)['round']==2
    else:
        with pytest.raises(ValueError):dialogue_review(p.observed(review,campaign),**args)

def test_existing_structural_correction_chain_survives_semantic_round2(setup,monkeypatch):
    from test_dialogue_retry_worker import failed as previous_failure
    from scripts.batch import harbor_dialogue_retry as old_retry,harbor_dialogue_authoring as author
    prior_fixture=previous_failure.__wrapped__(setup,author,(w,old_retry),monkeypatch)
    _,_,worker,s,lease,previous,t,campaign,respond,calls=prior_fixture
    worker,s,lease,permit=old_retry.authorize(worker,s,lease,checkpoint=previous)
    pending=w.step(worker,s,lease,checkpoint=permit)
    writer=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json');respond(writer,values(t))
    complete=w.step(worker,s,lease,checkpoint=pending.evidence)
    pending=w.step(worker,complete.selection,complete.lease,checkpoint=complete.evidence)
    para=w.request_saved(w.step_directory(worker,'dialogue-paraphraser')/'request.json');respond(para,values(t))
    complete=w.step(worker,pending.selection,pending.lease,checkpoint=pending.evidence)
    pending=w.step(worker,complete.selection,complete.lease,checkpoint=complete.evidence)
    request=w.request_saved(w.step_directory(worker,'dialogue-review')/'request.json');claims=p.read(record(t.run/'dialogue/claims.json'))
    response={'agent_model':'gpt-5.6-sol-high','round':1,'accuracy':{'verdict':'pass','claims':[{'record_id':c['record_id'],'must_say':'entailed','findings':[]} for c in claims['claims']],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'fail','findings':['Repeated source framing']},'passed':False}
    respond(request,response);blocked=w.step(worker,pending.selection,pending.lease,checkpoint=pending.evidence)
    assert blocked.status=='DIALOGUE_REPAIR_REQUIRED' and len(calls)==4
    old=worker.dialogue_retry
    worker,s,lease,permit=retry.authorize(worker,blocked.selection,blocked.lease,blocked=blocked.evidence)
    assert worker.dialogue_retry==old
    next=w.step(worker,s,lease,checkpoint=permit)
    second=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json')
    assert next.status=='MODEL_REQUIRED' and second.prepared.ordinal==2
    assert second.prepared.scope.budget_id=='dialogue-writer' and len(calls)==4
    with pytest.raises(ValueError):w.step(replace(worker,dialogue_retry=None),s,lease,checkpoint=permit)

def test_actual_boundary_and_nat_permits_retain_two_writer_calls(setup,monkeypatch,tmp_path):
    from test_nat_boundary_resume import ready as boundary_ready
    from scripts.batch import harbor_nat_retry as nat,harbor_nat_boundary_resume as boundary
    _,worker,s,lease,blocked,t,calls=boundary_ready.__wrapped__(setup,(w,nat),monkeypatch,tmp_path)
    worker,s,lease,complete=boundary.authorize(worker,s,lease,blocked=blocked)
    before=p.read(record(t.tracker['path']))
    records=retry._history(worker,before)
    assert worker.retry in records and worker.boundary_recovery in records and len(calls)==2
    assert before['lane_state'][p.NAT]['attempt']==2
    from scripts import harbor_instruction_campaign as campaign
    synthetic=replace(worker,dialogue_semantic_retry={'path':str(tmp_path/'root-selected-future-permit.json'),'sha256':'a'*64})
    assert campaign.worker_decode(campaign.worker_encode(synthetic))==synthetic
    assert w.step_directory(synthetic,'dialogue-writer').name=='semantic-round-02'
    assert w.step_directory(synthetic,'nat-writer').name=='round-02'
    # A hypothetical later semantic binding cannot remove earlier root evidence.
    permit=p.read(p.read(worker.boundary_recovery)['permit'])
    row=permit['copies'][0]['retained'];Path(row['path']).chmod(0o600);Path(row['path']).write_text('changed')
    with pytest.raises(ValueError):retry._history(worker,before)


def test_semantic_continuation_cannot_detach_or_replay_code_repair(failed):
    from scripts.batch import harbor_instruction_repair as repair
    worker,s,lease,blocked,t,_,_,calls=failed
    original=s.content.bindings['original_raw_source']
    historical=p.put(worker.run/'historic/code.json',{'retained':'historical code bytes'})
    snapshot=p.put(worker.run/'historic/snapshot.json',dict(root=str(worker.run/'historic'),
        files=[dict(relative='code.json',sha256=historical['sha256'])]))
    initial=record(worker.run/'initial.json')
    predecessor=record(worker.run/'steps/nat-commit/complete.json')
    tracker=p.put(worker.run/'historic/tracker.json',p.read(record(t.tracker['path'])))
    receipt=p.put(worker.run/'historic/repair.json',dict(schema_version=repair.SCHEMA,
        phase='dialogue-init',worker_run=str(worker.run),initial=initial,original=original,
        current_code={historical['path']:historical['sha256']},failure=blocked,
        predecessor=predecessor,tracker=tracker,old_snapshot=snapshot,
        retained_prefix=[dict(original=initial,retained=initial)],selection=w.codec.encode(s)))
    worker=replace(worker,code_repair=receipt)
    worker,s,lease,checkpoint=retry.authorize(worker,s,lease,blocked=blocked)
    before=p.read(record(t.tracker['path']))
    assert retry.validate(worker,before)['prior_continuations']['code_repair']==receipt
    assert len(calls)==3
    detached=replace(worker,code_repair=None)
    with pytest.raises(ValueError,match='history detached'):
        retry.validate(detached,before)
    substituted=p.put(worker.run/'historic/substitute.json',p.read(receipt))
    with pytest.raises(ValueError,match='history detached'):
        retry.validate(replace(worker,code_repair=substituted),before)
    with pytest.raises(ValueError,match='already selected'):
        retry.authorize(worker,s,lease,blocked=blocked)
    Path(historical['path']).write_text('changed historical bytes')
    with pytest.raises(ValueError,match='code bytes changed'):
        retry.validate(worker,before)
    assert len(calls)==3

@pytest.mark.parametrize('fault',[None,'handoff','foreign_adoption'])
def test_actual_semantic_cas_preserves_aggregate_predecessor_history(failed,monkeypatch,fault):
    from types import SimpleNamespace
    from scripts.fleet import instruction_model_continue as H
    worker,s,lease,blocked,t,_,_,_=failed
    adoption=w.keep(worker.run/'adoption.json',{'blocked':blocked})
    worker=replace(worker,disclosure_repair=adoption)
    # The fixture already supplies original completed Calls/steps; this test
    # isolates the new historical cycle while running the actual semantic CAS.
    monkeypatch.setattr(retry,'_history',lambda *a:[])
    worker,s,lease,permit=retry.authorize(worker,s,lease,blocked=blocked)
    before=p.read(record(worker.context.tracker_root/'0245.json'))
    receipt=dict(implementation='impl',history_reader='history',round_plan='round',round_result='result',review_plan='review',aggregate='aggregate',initial='initial',prior_continuations={},historical_records=[])
    seen=[]
    A=SimpleNamespace(__file__='impl',record=lambda _: 'impl',selected=lambda _:True,E=SimpleNamespace(checked_bytes=lambda x:None))
    A.read=lambda row:receipt if row==adoption else p.read(row)
    def validate(w,b):
        assert w.dialogue_semantic_retry is None
        seen.append(w);return receipt
    A.validate=validate
    def original_history(w,b):return H.adoption_history(A,None,w,b)[1]
    monkeypatch.setattr(retry,'_history',original_history)
    if fault=='handoff':before['handoffs']=[]
    elif fault=='foreign_adoption':worker=replace(worker,disclosure_repair={'path':'foreign','sha256':'b'*64})
    if fault:
        with pytest.raises(ValueError):H.adoption_history(A,None,worker,before)
    else:
        assert H.adoption_history(A,None,worker,before)[0]==receipt and len(seen)==2
        assert worker.dialogue_semantic_retry is not None
    assert retry._history is original_history
