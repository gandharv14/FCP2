"""Root controller gates only; no SSH, real model calls or task execution."""
import json
from pathlib import Path
import pytest
from scripts.fleet import fleet_control as f
from scripts.fleet import publish as p


def smoke(tmp_path):
    out=tmp_path/'output';out.mkdir();(out/'generation-manifest.json').write_text('{"fixture":true}')
    verification=p.retain(tmp_path/'verification.json',{'schema_version':'independent-published-task-copy-check/v1',
        'workbook':'0270','passed':True,'byte_identical_to_immutable_generation':True,
        'current_qualification_verified':True,'model_calls':0,'runtime_operations':0,
        'task_manifest':p.binding(out/'generation-manifest.json')})
    collection=p.retain(tmp_path/'collection.json',{'schema_version':'harbor-fleet-collection/v1','status':'COLLECTED',
        'workbook':'0270','local_byte_integrity_verified':True,'independent_remote_verification':verification,
        'output':str(out),'files':p.files(out)})
    return {'smoke_verification':verification,'smoke_collection':collection},{'workbooks':[{'workbook':'0270','state':'complete'}],'operations':[]}


def test_real_receipt_contract_and_local_output_integrity_gate(tmp_path):
    plan,central=smoke(tmp_path);f.smoke_gate(plan,central)
    (tmp_path/'output/generation-manifest.json').write_text('changed')
    with pytest.raises(ValueError,match='output changed'):f.smoke_gate(plan,central)


@pytest.mark.parametrize('change',['incomplete','uncertain','active'])
def test_smoke_and_global_operation_must_be_terminal(tmp_path,change):
    plan,central=smoke(tmp_path)
    if change=='incomplete':central['workbooks'][0]['state']='hold'
    else:central['operations']=[{'kind':'model','state':'hold' if change=='uncertain' else 'active'}]
    with pytest.raises(ValueError):f.smoke_gate(plan,central)


def test_fresh_gate_refuses_consumed_claim_or_local_attempt(tmp_path):
    job={'workbook':'0085','source':{'sha256':'a'*64},'run':str(tmp_path/'worker')}
    assignment={'host':{'jobs':[job],'recipes':[{'home':str(tmp_path/'attempt'),'reconstruction':{'prior_preparation':{'path':str(tmp_path/'prior/prepared/0085.json')}}}]}}
    central={'workbooks':[{'workbook':'0085','state':'hold','fence':0,'owner':None,'source_sha256':'a'*64}]}
    f.fresh_gate(assignment,central)
    central['workbooks'][0]['fence']=1
    with pytest.raises(ValueError,match='untouched'):f.fresh_gate(assignment,central)
    central['workbooks'][0]['fence']=0;(tmp_path/'attempt').mkdir()
    with pytest.raises(ValueError,match='retained attempt'):f.fresh_gate(assignment,central)


def test_dry_run_never_reconciles_or_starts(tmp_path,monkeypatch):
    plan,central=smoke(tmp_path);assignments={h:{'jobs':[],'recipes':[]} for h in f.HOSTS}
    monkeypatch.setattr(f,'load_plan',lambda selected:(plan,assignments,{}))
    calls=[]
    class Client:
        def call(self,action,**args):calls.append(action);assert action=='status';return central
    monkeypatch.setattr(f,'client_for',lambda p:Client())
    monkeypatch.setattr(f,'probe',lambda host,*args,**kwargs:{'host':host,'pool':None})
    monkeypatch.setattr(f,'stage_and_operate',lambda *a,**k:pytest.fail('dry run must not launch'))
    result=f.operate({},'start',tmp_path/'journal')
    assert result['preflight_only'] and calls==['status'] and not (tmp_path/'journal').exists()


def test_uncertain_launch_retained_and_same_journal_never_replayed(tmp_path,monkeypatch):
    plan,central=smoke(tmp_path);assignments={h:{'jobs':[],'recipes':[]} for h in f.HOSTS}
    monkeypatch.setattr(f,'load_plan',lambda selected:(plan,assignments,{}))
    class Client:
        def call(self,action,**args):assert action=='status';return central
    monkeypatch.setattr(f,'client_for',lambda p:Client())
    monkeypatch.setattr(f,'probe',lambda host,*args,**kwargs:{'host':host,'pool':None})
    calls=[]
    def launch(host,*args,**kwargs):calls.append(host);raise TimeoutError('lost response after process launch')
    monkeypatch.setattr(f,'stage_and_operate',launch)
    result=f.operate({},'start',tmp_path/'journal',execute=True)
    value=p.checked(result)
    assert len(calls)==8 and all(not h['response_received'] for h in value['hosts'])
    assert len(list((tmp_path/'journal').glob('*-uncertain.json')))==8
    with pytest.raises(ValueError,match='journal exists'):f.operate({},'start',tmp_path/'journal',execute=True)
    assert len(calls)==8


def test_stop_does_not_require_coordinator_or_mutate_claims(tmp_path,monkeypatch):
    assignments={h:{'jobs':[],'recipes':[]} for h in f.HOSTS}
    monkeypatch.setattr(f,'load_plan',lambda selected:({},assignments,{}))
    monkeypatch.setattr(f,'client_for',lambda p:pytest.fail('drain should not contact coordinator'))
    monkeypatch.setattr(f,'probe',lambda host,*args,**kwargs:{'host':host,'pool':None})
    monkeypatch.setattr(f,'stage_and_operate',lambda host,a,p,action,**kwargs:{'host':host,'action':action})
    result=p.checked(f.operate({},'stop',tmp_path/'stop',execute=True))
    assert result['coordinator_stopped'] is False and result['tunnels_stopped'] is False


def test_resume_requires_exact_stopped_process_and_no_replay_evidence(tmp_path):
    prior=p.retain(tmp_path/'prior.json',{'fixture':'original uncertain launch intent'})
    evidence=p.retain(tmp_path/'stopped.json',{'fixture':'retained process/claim/dispatch check'})
    value={'schema_version':'harbor-fleet-resume-reconciliation/v1','prior_intent':prior,
        'hosts':[{'host':h,'processes_stopped':True,'retained_dispatches_checked':True,
                  'no_uncertain_claims':True,'action':'resume','evidence':evidence} for h in f.HOSTS]}
    row=p.retain(tmp_path/'reconciliation.json',value)
    assert set(f.resume_gate(row,prior,f.HOSTS).values())=={'resume'}
    value['hosts'][0]['no_uncertain_claims']=False
    rejected=p.retain(tmp_path/'rejected.json',value)
    with pytest.raises(ValueError,match='evidence required'):f.resume_gate(rejected,prior,f.HOSTS)
