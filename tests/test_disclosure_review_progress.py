from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass,asdict
import json,hashlib
import pytest
from scripts.fleet import disclosure_review_progress as P,disclosure_reserved_round as S
from scripts.batch.harbor_admission import record,read


def keep(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)

@pytest.fixture
def case(tmp_path,monkeypatch):
    h=tmp_path/'old';wd=tmp_path/'worker';code=keep(tmp_path/'runner.py',{});source=keep(tmp_path/'source',{});tracker=keep(tmp_path/'tracker',{})
    authority=dict(implementation=keep(tmp_path/'rounds',{}),allowance=keep(tmp_path/'allowance',{}),build={},prior_rejections=[],round=2,limit=2)
    inputs=keep(tmp_path/'inputs',{'exact':'source projection'});plan=keep(tmp_path/'reserved-plan',dict(read(inputs),review_round=keep(tmp_path/'authority',authority)))
    old=dict(helper=code,owner='old',fence=4,output=str(h),workbook='0322',source=source,modules={'rounds':authority['implementation']},allowance=authority['allowance'],build={},prior_rejections=[],round=2,limit=2,inputs=inputs,max_calls=2,reserved_round=keep(tmp_path/'oldproof',{}),round_reader={})
    oldplan=keep(tmp_path/'oldplan',old);job=keep(tmp_path/'job',dict(driver=code,source=source,workbook='0322',run=str(wd),result=str(h/'result.json'),command=['python','--plan',oldplan['path'],'--sha256',oldplan['sha256']]))
    wi=keep(wd/'intent.json',dict(job=job,owner='old'));log=wd/'worker.log';log.write_text('U.custody(p,w.root);transport_checked(p,E)\nValueError: unreconciled model effect or quarantined custody\n')
    outer=keep(wd/'outcome.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=wi,log=record(log)))
    process=keep(tmp_path/'process',dict(job=job,automatic_retry=False,pid=999999,command=['python','--owner','old','--job',job['path'],'--job-sha256',job['sha256']]))
    proof=keep(tmp_path/'proof',dict(schema_version=P.SCHEMA,plan=oldplan,worker_outcome=outer,process=process,tracker=tracker,completed_count=1))
    p={k:v for k,v in old.items()if k not in ('reserved_round','round_reader')};p.update(owner='new',fence=5,output=str(tmp_path/'new'),completed_prefix=proof,progress_reader=record(P.__file__))
    keep(h/'intent.json',dict(plan=oldplan,automatic_retry=False));keep(h/'continued.json',old['reserved_round']);keep(h/'reserved.json',dict(plan=plan,automatic_retry=False));keep(h/'validation-metrics.json',{})
    @dataclass
    class Scope:index:int
    @dataclass
    class Call:receipt:str
    call=Call('actual completed call');scope=Scope(0);request=keep(tmp_path/'request',{'source':'unchanged'});ctx=keep(tmp_path/'context',dict(plan=plan,index=0));prepared=SimpleNamespace(scope=scope,request=request)
    saved=SimpleNamespace(prepared=prepared,context=ctx);outcome=keep(tmp_path/'executor-outcome',dict(status='completed'));state=SimpleNamespace(status='completed',call=call,outcome=outcome)
    d=h/'batches/0000';keep(d/'request.json',{});keep(d/'admission.json',{});keep(d/'transport-complete.json',dict(index=0,plan=plan,outcome=outcome,call=asdict(call),semantic_approval=False,automatic_retry=False));keep(d/'complete.json',dict(index=0,plan=plan,call=asdict(call),cached_input_recheck_seconds=1))
    events=[];Q=SimpleNamespace(validate=lambda row,body:(events.append('original authority')or authority));W=SimpleNamespace(active=lambda *a:(None,None,tracker),request_saved=lambda path:saved)
    canonical=lambda x:json.dumps(x).encode();E=SimpleNamespace(inspect=lambda *a:state,sha=lambda x:hashlib.sha256(x).hexdigest(),canonical=canonical,checked_bytes=lambda row:Path(row['path']).read_bytes())
    def review(*a,**kw):events.append('original independent review');return {}
    R=SimpleNamespace(E=E,scope=lambda v,i:Scope(i),payload=lambda *a:{'source':'unchanged'},validate_batch=review)
    w=SimpleNamespace(campaign=SimpleNamespace(root=tmp_path/'campaign'),root=tmp_path/'code');s=SimpleNamespace(content=SimpleNamespace(model_policy='original policy'))
    monkeypatch.setattr(S,'stopped_process',lambda *a:events.append('native process absence'))
    return proof,p,w,s,None,Q,W,R,({},{}),state,events,h,plan


def test_completed_call_is_read_verified_and_retained_never_replayed(case):
    proof,p,w,s,l,Q,W,R,validated,state,events,h,plan=case
    actual,calls=P.completed_calls(proof,p,w,s,l,Q,W,R,validated)
    assert actual==plan and calls==[state.call]
    assert events==['native process absence','original authority','original independent review']
    assert not Path(p['output']).exists()

@pytest.mark.parametrize('fault',['uncertain','later_executor','later_request','changed_response','source_projection','failed_review','budget','current_tracker'])
def test_unknown_later_effects_or_failed_quality_prevent_continuation(case,fault):
    proof,p,w,s,l,Q,W,R,validated,state,events,h,plan=case
    if fault=='uncertain':state.status='incomplete'
    elif fault=='later_executor':(w.campaign.root/R.E.sha(R.E.canonical({'index':1}))).mkdir(parents=True)
    elif fault=='later_request':keep(h/'batches/0001/request.json',{})
    elif fault=='changed_response':state.call=type(state.call)('foreign')
    elif fault=='source_projection':R.payload=lambda *a:{'source':'different'}
    elif fault=='failed_review':R.validate_batch=lambda *a,**k:(_ for _ in()).throw(ValueError('numeric contradiction'))
    elif fault=='budget':p['limit']=3
    elif fault=='current_tracker':W.active=lambda *a:(None,None,{})
    with pytest.raises(ValueError):P.completed_calls(proof,p,w,s,l,Q,W,R,validated)
