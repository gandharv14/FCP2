from dataclasses import asdict,replace
from pathlib import Path
import importlib.util,importlib.machinery,json,sys
import pytest
from scripts import harbor_pretransport_recovery as R
from scripts.batch import harbor_instruction_worker as W,harbor_instruction_producer as P
from scripts.batch import harbor_dialogue_retry as D,harbor_dialogue_semantic_retry as S
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record
from test_harbor_instruction_producer import setup
from test_dialogue_retry_worker import failed as structural_failed
from test_dialogue_slot_producer import values

@pytest.fixture
def ready(setup,monkeypatch):
    from scripts.batch import harbor_dialogue_authoring as author
    _,_,w,s,lease,prior,t,campaign,respond,calls=structural_failed.__wrapped__(setup,author,(W,D),monkeypatch)
    w,s,lease,permit=D.authorize(w,s,lease,checkpoint=prior)
    pending=W.step(w,s,lease,checkpoint=permit)
    writer=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json');respond(writer,values(t))
    done=W.step(w,s,lease,checkpoint=pending.evidence)
    pending=W.step(w,done.selection,done.lease,checkpoint=done.evidence)
    para=W.request_saved(W.step_directory(w,'dialogue-paraphraser')/'request.json');respond(para,values(t))
    done=W.step(w,pending.selection,pending.lease,checkpoint=pending.evidence)
    pending=W.step(w,done.selection,done.lease,checkpoint=done.evidence)
    review=W.request_saved(W.step_directory(w,'dialogue-review')/'request.json');claims=P.read(record(t.run/'dialogue/claims.json'))
    result=dict(agent_model='gpt-5.6-sol-high',round=1,accuracy=dict(verdict='pass',claims=[dict(record_id=c['record_id'],must_say='entailed',findings=[]) for c in claims['claims']],extras=[],cell_refs_in_senior_turns=[]),naturalness=dict(verdict='fail',findings=['Repeated prose']),passed=False)
    respond(review,result);failed=W.step(w,pending.selection,pending.lease,checkpoint=pending.evidence)
    w,s,lease,permit=S.authorize(w,failed.selection,failed.lease,blocked=failed.evidence)
    # Reproduce the historical actual executor failure with its retained code on
    # synthetic calls only. Production recovery never executes historical code.
    archive=Path(R.__file__).with_name('harbor_instruction_code_v1')/'executor-before.py.txt'
    loader=importlib.machinery.SourceFileLoader('original_protocol_fixture',str(archive));spec=importlib.util.spec_from_loader(loader.name,loader);old=importlib.util.module_from_spec(spec);sys.modules[loader.name]=old;loader.exec_module(old)
    def oldexecute(p,c,**kw):
        legacy=old.PreparedCall(p.request,p.policy,old.SemanticScope(**asdict(p.scope)),p.ordinal,p.limit,p.attempt_id,p.budget_evidence,p.response_mode)
        return old.execute(legacy,c,**kw)
    monkeypatch.setattr(P,'execute',oldexecute)
    with pytest.raises(old.ExecutorError,match='prior semantic scope/limit differs'):
        W.step(w,s,lease,checkpoint=permit,models=True,credential='fixture-credential')
    monkeypatch.setattr(P,'execute',E.execute)
    exception=record(W.step_directory(w,'dialogue-writer')/'exception.json')
    code=W.code(w);review=R.P.put(w.run/'root-code-review.json',dict(schema_version=R.CODE_SCHEMA,root_actor='harbor-orchestrator',worker_run=str(w.run),before=code,after=code,changes=[]))
    monkeypatch.setattr(W.runtime,'validate_runtime',lambda *a,**kw:None)
    s=W.refreshed(s)
    return w,s,lease,exception,review,t,campaign,respond,calls

def test_recovery_keeps_exception_request_identity_and_consumed_round(ready):
    w,s,l,exc,review,t,c,respond,calls=ready
    req=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json');request=asdict(req);spent=len(calls)
    original=E.checked_bytes(exc);w,s,l,permit=R.authorize(w,s,l,exception=exc,review=review)
    assert E.checked_bytes(exc)==original and len(calls)==spent
    assert asdict(W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json'))==request
    pending=W.step(w,s,l,checkpoint=permit);assert pending.status=='MODEL_REQUIRED'
    assert W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json').prepared.attempt_id==req.prepared.attempt_id
    respond(req,values(t));done=W.step(w,s,l,checkpoint=pending.evidence)
    assert done.status=='STEP_COMPLETE' and done.next_step=='dialogue-paraphraser' and len(calls)==spent+1
    before=P.read(record(t.tracker['path']));assert before['lane_state'][P.DIALOGUE]['attempt']==1
    with pytest.raises(ValueError):R.authorize(w,s,l,exception=exc,review=review)

@pytest.mark.parametrize('point',['prepared','after_temp_fsync','before_replace','after_replace','committed'])
def test_interruption_never_spends_call_or_repeats_CAS(ready,point):
    w,s,l,exc,review,t,c,respond,calls=ready;spent=len(calls)
    def stop(at):
        if at==point:raise RuntimeError('synthetic interruption')
    with pytest.raises(RuntimeError):R.authorize(w,s,l,exception=exc,review=review,_fault=stop)
    p=record(w.run/'dialogue-pretransport-recovery-v1/permit.json')
    if point in {'after_replace','committed'}:
        w,s,l,done=R.recover(w,p,l);assert W.step(w,s,l,checkpoint=done).status=='MODEL_REQUIRED'
    else:
        with pytest.raises(ValueError):R.recover(w,p,l)
    assert len(calls)==spent

@pytest.mark.parametrize('fault',['exception','request','executor_attempt','identity','later_step','lease','review','prefix'])
def test_unknown_changed_or_potentially_submitted_effect_rejects(ready,fault,monkeypatch):
    w,s,l,exc,review,t,c,respond,calls=ready;spent=len(calls);req=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json')
    if fault=='exception':Path(exc['path']).write_text('{}')
    elif fault=='request':Path(req.prepared.request['path']).write_text('{}')
    elif fault=='executor_attempt':E.location(req.prepared,c).mkdir()
    elif fault=='identity':(c.root/'attempt-identities'/(req.prepared.attempt_id+'.json')).write_text('{}')
    elif fault=='later_step':W.step_directory(w,'dialogue-paraphraser').mkdir()
    elif fault=='lease':l=replace(l,token='wrong')
    elif fault=='review':Path(review['path']).write_text('{}')
    else:monkeypatch.setattr(D,'_prefix',lambda *a:(_ for _ in ()).throw(ValueError('current gate failed')))
    with pytest.raises((ValueError,KeyError)):R.authorize(w,s,l,exception=exc,review=review)
    assert len(calls)==spent

def test_root_worker_codec_retains_explicit_pretransport_receipt(ready):
    from scripts import harbor_instruction_campaign as C
    w,s,l,exc,review,*_=ready;w,s,l,p=R.authorize(w,s,l,exception=exc,review=review)
    assert C.worker_decode(C.worker_encode(w))==w
    payload=C.worker_encode(w);payload['pretransport_recovery']={'passed':True}
    with pytest.raises(ValueError):C.worker_decode(payload)

def test_retained_writer_code_profile_keeps_payload_uuid_and_context_bytes(ready):
    from scripts import harbor_instruction_code_applicability as A
    w,s,l,exc,review,t,c,respond,calls=ready
    request=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json');task=W.task(w,s,record(t.tracker['path']))
    context=P.read(request.context);context['code']=A.profile(P.code_evidence(task))
    historical=P.put(w.run/'synthetic-historical-context.json',context)
    request=replace(request,context=historical);before=E.checked_bytes(historical);payload=E.checked_bytes(request.prepared.request);uuid=request.prepared.attempt_id
    P.validate_request(task,request,'dialogue-writer')
    assert E.checked_bytes(historical)==before and E.checked_bytes(request.prepared.request)==payload and request.prepared.attempt_id==uuid
    changed=dict(context);changed['code']=context['code'][:-1]
    altered=P.put(w.run/'altered-code-context.json',changed)
    with pytest.raises(ValueError):P.validate_request(task,replace(request,context=altered),'dialogue-writer')

def test_renewal_preserves_recovery_budget_and_owner(ready):
    from scripts.batch import harbor_tracker_lifecycle as L
    from scripts.batch.harbor_workbook_bootstrap import Lease
    w,s,l,exc,review,t,c,respond,calls=ready
    authority=L.RootAuthority('harbor-orchestrator',10**12,lambda *a:True)
    result=L.lease_operation(t.tracker['path'],operation='renew',authority=authority,lease_kind='task_agent',owner=l.owner,token=l.token,scope='workbook:0245',expected_revision=l.revision,expected_sha256=l.sha256,duration_ns=10**12)
    current=Lease(l.owner,l.token,result['revision'],result['sha256'])
    w,s,new,p=R.authorize(w,s,current,exception=exc,review=review)
    assert new.owner==l.owner and new.token==l.token
    assert P.read(P.read(p)['permit'])['consumed_rounds']==1

@pytest.mark.parametrize('fault',['request','archive','budget','review','code'])
def test_completed_recovery_revalidation_rejects_tampering(ready,fault,monkeypatch):
    w,s,l,exc,review,t,c,respond,calls=ready;w,s,l,p=R.authorize(w,s,l,exception=exc,review=review)
    complete=P.read(p);permit=P.read(complete['permit'])
    if fault=='request':Path(permit['request']['path']).write_text('{}')
    elif fault=='archive':
        archive=Path(permit['copies'][0]['retained']['path']);archive.chmod(0o600);archive.write_text('{}')
    elif fault=='budget':
        permit['consumed_rounds']=0;Path(complete['permit']['path']).write_text(json.dumps(permit))
    elif fault=='review':Path(review['path']).write_text('{}')
    else:monkeypatch.setattr(W,'code',lambda _: {'unexpected':'code'})
    with pytest.raises(ValueError):R.validate(w,P.read(record(t.tracker['path'])))

@pytest.mark.parametrize('fault',['caller_pass','added_financial_code','missing_change','wrong_after','old_bytes'])
def test_root_code_review_is_exact_and_never_financial_override(ready,fault):
    w,s,l,exc,review,t,c,respond,calls=ready
    value=P.read(review)
    if fault=='caller_pass':value['passed']=True
    elif fault=='wrong_after':value['after']={}
    else:
        current=value['after'];key=next(iter(current));old=dict(current)
        if fault=='added_financial_code':
            path=w.root/'xl_ast_graph.py';key=str(path);old.pop(key,None);current[key]=record(path)['sha256'];value['changes']=[dict(path=key,before=None,after=record(path))]
        elif fault=='missing_change':old[key]='0'*64
        else:
            old[key]='0'*64;value['changes']=[dict(path=key,before=record(key),after=record(key))]
        value['before']=old
    changed=P.put(w.run/('review-'+fault+'.json'),value)
    with pytest.raises(ValueError):R.code_review(changed,w,value['before'] if fault not in {'caller_pass','wrong_after'} else P.read(review)['before'])

def test_same_semantic_scope_cannot_dispatch_during_recovery_CAS(ready):
    w,s,l,exc,review,t,c,respond,calls=ready;req=W.request_saved(W.step_directory(w,'dialogue-writer')/'request.json')
    with E.scope_lock(E.location(req.prepared,c).parent,create=False):
        with pytest.raises(E.ExecutorError,match='already executing'):R.authorize(w,s,l,exception=exc,review=review)
    assert not(w.run/'dialogue-pretransport-recovery-v1').exists()
