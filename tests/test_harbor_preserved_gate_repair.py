import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_harbor_ready_transitions import fixture,kw
from scripts.batch import harbor_failure_snapshot as F,harbor_lane_notes as N,harbor_preserved_gate_repair as K
from scripts.batch.harbor_admission import record,read,require
from scripts.batch.harbor_disclosure_round_adoption import function_sha

@pytest.mark.parametrize('fault',[None,'different_lane','reset_attempt','lease','history'])
def test_original_begin_repair_comparison_preserves_failure(tmp_path,monkeypatch,fault):
    _,path,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True)
    facade=F.FailureSnapshots(N.RootContext(tmp_path));snapshot=facade.snapshot_failure('0245',**kw(start));tx=read(snapshot['journal']);before=tx['after'];actual=facade.begin_repair('0245',**kw(snapshot))
    after=tmp_path/'after-repair.json';after.write_bytes(path.read_bytes());value=read(record(after))
    if fault=='different_lane':value['lane_state']['harbor-source-warden']['state']='pending'
    if fault=='reset_attempt':value['lane_state']['harbor-normalization-engineer']['attempt']=0
    if fault=='lease':value['leases']['task_agent']['owner']='foreign'
    if fault=='history':value['handoffs']=value['handoffs'][:-1]
    if fault:after.write_bytes(N.lifecycle.canonical(value))
    raw=path.read_bytes()
    if fault:
        with pytest.raises(Exception):K._repair_after(F,N,before,record(after),tx['notes'],facade.context)
    else:
        assert K._repair_after(F,N,before,record(after),tx['notes'],facade.context)==actual['tracker']
        assert actual['tracker']['promotion_failure']==snapshot['tracker']['promotion_failure']
    assert path.read_bytes()==raw

active_tracker=None

def actual_active(worker,selection,lease):
    before=active_tracker
    require(lease=='live','original live lease required')
    require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')
    require(selection=='actual-source','original source check required')
    return None,before,{}

@pytest.mark.parametrize('fault',[None,'failure','history','worker','lease','source'])
def test_scoped_failure_predicate_keeps_original_live_checks(tmp_path,monkeypatch,fault):
    global active_tracker
    base=dict(promotion_failure={'diagnostic_signature':'a'*64},bindings={'original_raw_source':{}},handoffs=[{'original':'snapshot'}],failure_history=[{'original':'failure'}])
    active_tracker=copy.deepcopy(base);worker=SimpleNamespace(run=tmp_path/'run',root=tmp_path/'root')
    value=dict(worker_run=str(worker.run),worker_root=str(worker.root),source={});W=SimpleNamespace(__file__=__file__,active=actual_active,record=record)
    profile=dict(worker=record(__file__),active=function_sha(actual_active));monkeypatch.setattr(K,'validate',lambda *a:(value,base))
    if fault=='failure':active_tracker['promotion_failure']={'diagnostic_signature':'b'*64}
    if fault=='history':active_tracker['handoffs']=[]
    if fault=='worker':worker=SimpleNamespace(run=tmp_path/'foreign',root=tmp_path/'root')
    lease='expired'if fault=='lease'else'live';source='foreign'if fault=='source'else'actual-source'
    with K.active_scope(W,receipt={},profile=profile):
        if fault:
            with pytest.raises(Exception):W.active(worker,source,lease)
        else:assert W.active(worker,source,lease)[1]==base
    assert W.active is actual_active


@pytest.mark.parametrize('completed_takeover',[False,True])
def test_original_worker_checkpoint_traversal_accepts_only_named_repair(tmp_path,monkeypatch,completed_takeover):
    """Execute the real worker function through its first operation boundary."""
    import types
    from dataclasses import asdict
    from scripts.batch import harbor_instruction_worker as original
    from scripts.batch.harbor_workbook_bootstrap import Lease
    class Worker(SimpleNamespace):
        def __getattr__(self,key):return None
    w=Worker(run=tmp_path/'worker',root=tmp_path/'code');w.run.mkdir()
    selection=SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':{}}));lease=Lease('owner','token',7,'a'*64)
    def put(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
    initial=put(w.run/'initial.json',dict(selection={'selection':1},code={}))
    previous_intent=put(tmp_path/'prior-intent.json',dict(initial=initial,previous=None))
    previous=put(tmp_path/'old-steps/disclosure-review/complete.json',dict(selection={'selection':1},lease=asdict(lease),intent=previous_intent))
    repairdir=tmp_path/'repair';receipt=put(repairdir/'permit.json',{'explicit':'receipt'})
    checkpoint=put(repairdir/'result.json',{})
    base=dict(promotion_failure={'diagnostic_signature':'a'*64},bindings={'original_raw_source':{}},handoffs=[],failure_history=[])
    value=dict(worker_run=str(w.run),worker_root=str(w.root),source={},phase='disclosure-commit',prior=previous,selection={'selection':1},lease=asdict(lease))
    monkeypatch.setattr(K,'validate',lambda *args:(value,base))
    codec=SimpleNamespace(encode=lambda s:{'selection':1},decode=lambda s:selection)
    def directory(worker,phase):return tmp_path/'old-steps'/phase
    def active(worker,selection,lease):
        before=base
        require(before['promotion_failure'] is None, 'existing failure requires root-owned snapshot/repair before continuing')
        return None,before,{}
    monkeypatch.setitem(active.__globals__,'base',base)
    seen=[]
    def operation(*args,**kwargs):seen.append(args[3]);raise StopIteration('actual operation boundary')
    scope=dict(original._step.__globals__);scope.update(active=active,code=lambda w:{},STEPS=['disclosure-review','disclosure-commit','nat-writer'],read=read,record=record,codec=codec,step_directory=directory,_operation=operation,keep=put)
    step=types.FunctionType(original._step.__code__,scope,original._step.__name__,original._step.__defaults__,original._step.__closure__)
    W=SimpleNamespace(__file__=original.__file__,active=active,_step=step,step_directory=directory,_operation=operation,record=record,read=read)
    def prior(row,descriptor):raise AssertionError('not called')
    I=SimpleNamespace(__file__=__file__,_prior=prior)
    profile=dict(worker=record(W.__file__),instruction=record(I.__file__),active=function_sha(active),step=function_sha(step),directory=function_sha(directory),prior=function_sha(prior))
    with K.instruction_scope(W,I,receipt=receipt,profile=profile):
        from contextlib import nullcontext
        from scripts.fleet import completed_checkpoint_takeover as bridge
        current=Lease('new-owner','new-token',8,'b'*64)if completed_takeover else lease
        observed=[]
        def allowed(worker,chosen,live,checkpoint,predecessor):
            assert worker is w and chosen is selection and live==current and checkpoint==receipt
            assert predecessor==value and predecessor['lease']==asdict(lease)
            observed.append(True);return True
        with bridge.step_scope(W,allowed,wrapper=record(K.__file__))if completed_takeover else nullcontext():
            with pytest.raises(StopIteration,match='actual operation boundary'):W._step(w,selection,current,phase='disclosure-commit',checkpoint=receipt)
        assert observed==([True]if completed_takeover else[])
        assert seen==['disclosure-commit']
        new=read(record(repairdir/'steps/disclosure-commit/intent.json'));assert new['previous']==receipt
        assert new['lease']==asdict(current)
    assert W._step is step and W.active is active and I._prior is prior

@pytest.fixture
def source_context_case(tmp_path):
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
    descriptor=put('descriptor.json',{});checkpoint=put('runtime-complete.json',{})
    selected={'source':'unchanged'};phase='dialogue-commit'
    prior=put('prior.json',dict(status='STEP_COMPLETE',next_step=phase,descriptor=descriptor,checkpoint=checkpoint,selection=selected))
    task=put('task-intent.json',dict(step=phase,previous=checkpoint,selection=selected))
    blocked=put('blocked.json',dict(status='OWNING_GATE_FAILED',step=phase,intent=task,selection=selected))
    result=put('result.json',dict(status='OWNING_GATE_FAILED',next_step=phase,descriptor=descriptor,checkpoint=blocked,selection=selected))
    code=put('instruction.json',{})
    intent=put('guard-intent.json',dict(action='step',models=False,code=code,arguments=dict(descriptor=descriptor,checkpoint=prior)))
    resource=put('resource.json',dict(returncode=0,failure=None,intent=intent))
    outcome=put('outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result))
    p=dict(source_context=dict(kind='dialogue',prefix={}),failed_step=blocked,failed_outcome=outcome,owning=dict(instruction=code),original_descriptor=descriptor)
    return p,dict(gate='source'),put

@pytest.mark.parametrize('fault',[None,'foreign_phase','foreign_descriptor','foreign_predecessor','unsettled'])
def test_source_failure_retains_exact_original_intended_phase(source_context_case,fault):
    p,result,put=source_context_case
    if fault=='foreign_phase':p['source_context']['kind']='naturalization'
    elif fault=='foreign_descriptor':p['original_descriptor']=put('foreign.json',{})
    elif fault=='foreign_predecessor':
        out=read(p['failed_outcome']);resource=read(out['resource']);intent=read(resource['intent']);prior=read(intent['arguments']['checkpoint']);prior['next_step']='enqueue';put('prior.json',prior)
    elif fault=='unsettled':
        out=read(p['failed_outcome']);out['status']='RECONCILIATION_REQUIRED';p['failed_outcome']=put('outcome.json',out)
    if fault:
        with pytest.raises(Exception):K.intended_gate(N,p,result)
    else:assert K.intended_gate(N,p,result)=='dialogue'

@pytest.mark.parametrize('contradiction',[False,True])
def test_source_context_requires_complete_original_prefix_without_mutation(tmp_path,source_context_case,contradiction):
    from scripts.batch import harbor_completed_lanes as G
    p,result,_=source_context_case
    root=tmp_path/'root';code=root/'scripts/batch/harbor_instruction_repair.py';code.parent.mkdir(parents=True)
    code.write_text('''from scripts.batch.harbor_completed_lanes import STEPS
def prefix(before,selection,context,*,stop):
 if not before['source_valid']:raise ValueError('actual source contradiction')
 return [dict(kind=kind,result=dict(passed=True,lane=lane,bindings={},phase='phase1_complete'if kind=='normalization_phase1'else'phase2_complete'if kind=='normalization_phase2'else None))for kind,lane in STEPS[:[x[0]for x in STEPS].index(stop)]]
''')
    p['source_context']['prefix']=record(code)
    before=dict(source_valid=not contradiction,lane_state={lane:dict(state='failed',phase=None)for _,lane in G.STEPS},bindings={},promotion_failure={'original':'preserved'},failure_history=[{'original':'preserved'}])
    saved=copy.deepcopy(before);calls=[]
    def check(view,packet,context):
        assert view['lane_state']['harbor-source-warden']['state']=='passed'
        assert view['promotion_failure']==before['promotion_failure']
        calls.append(packet.kind);return {'passed':True}
    W=SimpleNamespace(record=record,check_completed_gate=check,CompletionEvidence=G.CompletionEvidence)
    worker=SimpleNamespace(root=root,context=SimpleNamespace());selection=SimpleNamespace()
    if contradiction:
        with pytest.raises(ValueError,match='source contradiction'):K.corrected_gate(W,N,p,result,before,selection,worker)
        assert calls==[]
    else:assert K.corrected_gate(W,N,p,result,before,selection,worker)=={'passed':True}and calls==['dialogue']
    assert before==saved


def test_function_witness_does_not_require_newer_task_adoption_module(monkeypatch):
    import sys
    from scripts.batch.harbor_disclosure_round_adoption import function_sha as original
    from scripts.batch import harbor_preserved_gate_repair as K
    expected=original(K.intended_gate)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_round_adoption',None)
    assert K.function_sha(K.intended_gate)==expected
