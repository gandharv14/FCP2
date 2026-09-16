"""Exact retained owning intent resumes once; no new request or erased history."""
from pathlib import Path
from types import SimpleNamespace
import json,time
import pytest
from scripts.fleet import fresh_publication as F
from scripts.fleet.migration import checked,binding


def fixture(tmp_path):
    def put(name,v):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
    auth=put('authorization',{})
    auth['sha256']='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
    def bind(path):return auth if path==auth['path'] else binding(path)
    def require(ok,why):
        if not ok:raise ValueError(why)
    pub=SimpleNamespace(checked=checked,binding=bind,require=require)
    tracker=put('tracker',dict(workbook_id='0162',bindings=dict(original_raw_source=dict(sha256='source')),execution_state='publication_queued',terminal_verdict='PUBLICATION_READY',promotion_failure=None,leases=dict(task_agent=dict(expires_at=time.time_ns()+2000*10**9))))
    old=put('old-plan',dict(workbook='0162',source_sha256='source',journal=str(tmp_path/'old'),publication_wait={'path':'/wait','sha256':'a'*64}))
    intent=put('old/owning-publication/intent.json',dict(queued_tracker=tracker,request={'same':'request'},code={'same':'code'}))
    proof=put('proof',dict(schema_version='fleet-publication-unspawned-stop/v1',status='EXACT_OWNING_INTENT_UNSPAWNED_STOPPED',plan=old,owning_intent=intent,processes=[],owning_child_spawned=False,tracker_mutated=False,output_exists=False,publication_root_exists=False,models_dispatched=0,automatic_retry=False,tracker=tracker,files=[intent]))
    settlement=put('settlement',dict(proof=proof,owning_intent=intent,operation=dict(kind='publication',state='hold',workbook='0162',operation_id='op')))
    receipt=put('receipt',dict(intent=settlement,result=dict(state='abandoned',operation_id='op'),owning_intent_preserved=True,automatic_retry=False))
    policy=put('policy',dict(schema_version='fleet-unspawned-publication-resume-policy/v1',authorization=auth,workbook='0162',source_sha256='source',maximum_resumes=1,minimum_lease_runway_seconds=1800,original_guard_seconds=3300,memory_bytes=8589934592,expiry_behavior='original owning lease checks fail closed; no retry'))
    selection=put('selection',dict(schema_version='fleet-unspawned-publication-resume/v1',prior_plan=old,intent=intent,failure_proof=proof,reconciliation=receipt,policy=policy))
    plan=dict(checked(old),journal=str(tmp_path/'new'),publication_resume=selection)
    producer=SimpleNamespace(encode=lambda r:r,_codes=lambda:{'same':'code'})
    return pub,plan,producer,put


def test_resume_preserves_owning_intent_and_request(tmp_path):
    pub,plan,P,put=fixture(tmp_path)
    selected=checked(plan['publication_resume']);before=Path(selected['intent']['path']).read_bytes()
    journal,kwargs=F.publication_arguments(pub,P,{'same':'request'},plan)
    assert journal==Path(selected['intent']['path']).parent
    assert kwargs==dict(resume=selected['intent'])
    assert Path(selected['intent']['path']).read_bytes()==before
    assert not Path(plan['journal']).exists()


@pytest.mark.parametrize('defect',['request','code','running','spawned','tracker','output','model','unsettled','new-plan','changed-evidence','policy'])
def test_uncertain_or_changed_intent_cannot_resume(tmp_path,defect):
    pub,plan,P,put=fixture(tmp_path);request={'same':'request'};selection=checked(plan['publication_resume'])
    if defect=='request':request={}
    elif defect=='code':P._codes=lambda:{}
    elif defect=='new-plan':plan['source_sha256']='other'
    elif defect=='changed-evidence':Path(selection['intent']['path']).write_text('{}')
    elif defect=='unsettled':
        r=checked(selection['reconciliation']);r['result']['state']='hold';selection['reconciliation']=put('changed-receipt',r)
    elif defect=='policy':
        p=checked(selection['policy']);p['maximum_resumes']=2;selection['policy']=put('changed-policy',p)
    else:
        proof=checked(selection['failure_proof']);key={'running':'processes','spawned':'owning_child_spawned','tracker':'tracker_mutated','output':'output_exists','model':'models_dispatched'}[defect]
        proof[key]=[1] if defect=='running' else True
        selection['failure_proof']=put('changed-proof',proof)
    if defect in ('unsettled','policy','running','spawned','tracker','output','model'):plan['publication_resume']=put('changed-selection',selection)
    with pytest.raises(ValueError):F.publication_arguments(pub,P,request,plan)


def test_fresh_publication_keeps_original_call_signature(tmp_path):
    assert F.publication_arguments(None,None,None,dict(journal=str(tmp_path)))==(tmp_path/'owning-publication',{})


def test_default_wait_delegates_original_policy():
    called=[];pub=SimpleNamespace(publication_wait=SimpleNamespace(enforce_after_slot=lambda x,**kw:called.append(x)),checked=checked,require=None)
    F.enforce_publication_wait(pub,{},dict(policy=None));assert called==[dict(policy=None)]


@pytest.mark.parametrize('remaining,passes',[(1801,True),(1800,False),(0,False)])
def test_explicit_resume_runway_rechecked_after_slot(tmp_path,monkeypatch,remaining,passes):
    pub,plan,P,put=fixture(tmp_path);selection=checked(plan['publication_resume'])
    proof=checked(selection['failure_proof']);tracker=checked(proof['tracker'])
    expires=tracker['leases']['task_agent']['expires_at']
    monkeypatch.setattr(time,'time_ns',lambda:expires-remaining*10**9)
    wait=dict(policy=plan['publication_wait'],value=dict(lease_tracker=proof['tracker']))
    if passes:
        result=F.enforce_publication_wait(pub,plan,wait)
        assert result['remaining_seconds']==remaining
    else:
        with pytest.raises(ValueError,match='insufficient native lease'):F.enforce_publication_wait(pub,plan,wait)

@pytest.mark.parametrize('defect',[None,'entered','running','tracker','output','model','missing-proof'])
def test_stopped_nested_bootstrap_resumes_only_before_owning_execution(tmp_path,defect):
    pub,plan,P,put=fixture(tmp_path);selected=checked(plan['publication_resume'])
    proof=checked(selected['failure_proof']);proof.update(schema_version='fleet-publication-preexecute-stop/v1',status='EXACT_OWNING_INTENT_PREEXECUTE_STOPPED',owning_child_spawned=True,owning_execute_entered=False)
    if defect=='entered':proof['owning_execute_entered']=True
    elif defect=='missing-proof':proof.pop('owning_execute_entered')
    elif defect=='running':proof['processes']=[123]
    elif defect=='tracker':proof['tracker_mutated']=True
    elif defect=='output':proof['output_exists']=True
    elif defect=='model':proof['models_dispatched']=1
    selected['failure_proof']=put('preexecute-proof',proof)
    receipt=checked(selected['reconciliation']);settlement=checked(receipt['intent']);settlement['proof']=selected['failure_proof']
    receipt['intent']=put('preexecute-settlement',settlement);selected['reconciliation']=put('preexecute-receipt',receipt)
    plan['publication_resume']=put('preexecute-selection',selected)
    if defect:
        with pytest.raises(ValueError):F.publication_arguments(pub,P,{'same':'request'},plan)
    else:
        journal,kwargs=F.publication_arguments(pub,P,{'same':'request'},plan)
        assert kwargs=={'resume':selected['intent']} and journal==Path(selected['intent']['path']).parent
