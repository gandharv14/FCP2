from pathlib import Path
from types import SimpleNamespace
import json,sys
import pytest
from scripts.fleet import package_recovery_continue as M
from scripts import harbor_package_recovery as R


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)

@pytest.fixture
def guarded(tmp_path):
    integration=put(tmp_path/'integration.json',{});prepared=put(tmp_path/'prepared.json',{});args={'prepared':prepared};directory=tmp_path/'execute';directory.mkdir()
    code=[M.binding(Path(M.__file__).resolve())];r=SimpleNamespace(SCHEMA=R.SCHEMA,SECONDS=900,MEMORY=8*1024**3,guard_seconds=lambda i:900,code=lambda i:code)
    intent=put(directory/'intent.json',dict(schema_version=r.SCHEMA,action='execute',arguments=args,integration=integration,dispatch_commands=True,code=code,executable=M.binding(Path(sys.executable).resolve()),timeout_seconds=r.SECONDS,memory_limit_bytes=r.MEMORY))
    (directory/'step.log').write_text('original successful child\n');resource=put(directory/'resource.json',dict(intent=intent,returncode=0,metrics={},log=M.binding(directory/'step.log'),failure=None));result=put(directory/'result.json',{'status':'ENVIRONMENT_READY'})
    outcome=put(directory/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False,source_invalidity_claimed=False,publication_performed=False))
    return r,integration,args,directory,outcome


def test_only_actual_original_guard_result_is_accepted(guarded):
    r,i,a,d,o=guarded
    assert M.successful(r,o,'execute',a,i)==(M.binding(d/'result.json'),{'status':'ENVIRONMENT_READY'})

@pytest.mark.parametrize('field,value',[('dispatch_commands',False),('timeout_seconds',1800),('memory_limit_bytes',16*1024**3),('action','prepare'),('integration',{'path':'/other','sha256':'0'*64})])
def test_rehashed_guard_contract_change_rejects(guarded,field,value):
    r,i,a,d,o=guarded;v=M.read(M.binding(d/'intent.json'));v[field]=value;intent=put(d/'intent.json',v)
    resource=M.read(M.binding(d/'resource.json'));resource['intent']=intent;rb=put(d/'resource.json',resource)
    outcome=M.read(o);outcome['resource']=rb;o=put(d/'outcome.json',outcome)
    with pytest.raises(ValueError,match='action changed'):M.successful(r,o,'execute',a,i)


def test_failed_or_external_result_never_becomes_environment_ready(guarded,tmp_path):
    r,i,a,d,o=guarded;value=M.read(o);value['result']=put(tmp_path/'external.json',{'status':'ENVIRONMENT_READY'});o=put(d/'outcome.json',value)
    with pytest.raises(ValueError,match='did not complete'):M.successful(r,o,'execute',a,i)


def test_original_gate_state_preserves_all_unrelated_history():
    import copy
    before={'bindings':{'source':'original'},'lane_state':{'environment':{'confidence_history':[]},'research':{'state':'passed','current_confidence':'high','attempt':3}},'revision':8,'updated_at':100,'confidence_history':[],'leases':{'task_agent':{'owner':'same','token':'same','heartbeat_at':100}},'failure_history':['original'],'repairs':[]}
    proof={'lane':'environment','gate':'environment','bindings':{'packaged':'exact'},'evidence':[]}
    actual=copy.deepcopy(before);row=actual['lane_state']['environment'];row.update(state='passed',current_confidence='high',current_gate='environment',evidence_paths=[]);row['confidence_history'].append(dict(level='high',reason='current_independent_gate_pass',revision=9));actual['bindings'].update(proof['bindings']);actual.update(last_gate='environment',current_gate=None,execution_state='running',revision=9,updated_at=101,current_confidence=dict(level='high',reason_codes=['current_independent_gate_pass'],contributors=['environment','research'],computed_at_revision=9));actual['confidence_history'].append(copy.deepcopy(actual['current_confidence']));actual['leases']['task_agent']['heartbeat_at']=101
    R.validate_gate_state(before,actual,proof)
    for mutate in (lambda x:x['failure_history'].clear(),lambda x:x['lane_state']['research'].update(attempt=0),lambda x:x['leases']['task_agent'].update(token='foreign')):
        changed=copy.deepcopy(actual);mutate(changed)
        with pytest.raises(ValueError):R.validate_gate_state(before,changed,proof)


@pytest.mark.parametrize('fault',[None,'new_result','changed_log','foreign_checkpoint','foreign_sidecar','foreign_process'])
def test_interrupted_start_requires_closed_original_command_and_no_effect(tmp_path,fault):
    home=tmp_path/'old';d=home/'instruction-start';d.mkdir(parents=True);code=M.binding(Path(M.__file__).resolve())
    previous=put(tmp_path/'previous.json',{});cp=put(tmp_path/'environment.json',{});descriptor=put(tmp_path/'descriptor.json',{})
    old=dict(home=str(home),helper=code,descriptor=descriptor,owner='old',fence=3,workbook='0001',source={'sha256':'raw'});p=dict(previous=previous)
    I=SimpleNamespace(__file__=M.__file__);base=SimpleNamespace(__file__=M.__file__);env=[base,I,None,None,None,dict(run_root=str(tmp_path/'run'))]
    intent=put(d/'intent.json',dict(action='start',arguments=dict(environment_descriptor=descriptor,checkpoint={}if fault=='foreign_checkpoint'else cp,worker_run=str(tmp_path/'run/instruction'))))
    (d/'step.log').write_text('changed'if fault=='changed_log'else '');put(d/'dependency.json',{})
    O=SimpleNamespace(evidence=lambda p:[previous]);A=SimpleNamespace(__file__=M.__file__,SCHEMA='adapter',contract=lambda *a:3300)
    command=[sys.executable,'-I','-B',code['path'],'owning-child','--plan',previous['path'],'--plan-sha256',previous['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    put(d/'declared-runtime-adapter.json',dict(schema_version=A.SCHEMA,adapter=code,producer_code=code,base_code=code,kind='instruction',reader_plan={}if fault=='foreign_sidecar'else previous,child_wrapper=code,reader_evidence=[previous],intent=intent,original_command=['original'],selected_command=command,timeout_seconds=3300,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False))
    job=put(tmp_path/'job.json',dict(driver=code,result=str(home/'result.json'),workbook='0001',source=old['source'],version='v',command=[sys.executable,'-I','-B',code['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]));wi=put(tmp_path/'worker-intent.json',dict(owner='old',job=job,claim_id='id',automatic_retry=False,created_at_ns=1))
    (tmp_path/'worker.log').write_text('');claim=put(tmp_path/'claim.json',dict(owner='old',claim_id='id',fence=3,workbook='0001',source_sha256='raw',version='v'))
    outer=put(tmp_path/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',automatic_retry=False,publication_performed=False,log=M.binding(tmp_path/'worker.log'),metrics={},failure_type='TimeoutError',result=None,returncode=None,intent=wi))
    proc=put(tmp_path/'process.json',dict(pid=99999999,start_ticks='0',job={}if fault=='foreign_process'else job,automatic_retry=False))
    if fault=='new_result':put(d/'result.json',{})
    tree={x.name:M.binding(x)['sha256']for x in d.iterdir()};p['stopped']=put(tmp_path/'stop.json',dict(schema_version='fleet-unstarted-package-instruction/v1',directory=str(d),tree=tree,worker_outcome=outer,process=proc,claim=claim))
    if fault:
        with pytest.raises(ValueError):M.interrupted_start(p,old,O,None,env,A,cp)
    else:M.interrupted_start(p,old,O,None,env,A,cp)


def test_completed_history_ignores_only_mutable_task_stage(tmp_path):
    source=tmp_path/'0001.xlsx';source.write_bytes(b'authentic source')
    old={'home':str(tmp_path/'old'),'integration':put(tmp_path/'integration.json',{})}
    prepared=Path(old['home'])/'prepare/recovery';prepared.mkdir(parents=True)
    put(prepared/'prepared.json',{'immutable':'original prepared'})
    put(prepared/'result.json',{'status':'ENVIRONMENT_READY'})
    stage=tmp_path/'task-stage';stage.mkdir();(stage/'instruction.md').write_text('original packaged instruction')
    p={'previous':put(tmp_path/'old-plan.json',old),'source':M.binding(source)}
    cp=M.binding(prepared/'result.json');r=SimpleNamespace(code=lambda i:[M.binding(Path(M.__file__).resolve())])
    original=M.package_history(p,old,r,cp)
    (stage/'instruction.md').write_text('actual later authored instruction')
    assert M.package_history(p,old,r,cp)==original
    (prepared/'result.json').write_text('{"status":"forged"}')
    assert M.package_history(p,old,r,cp)!=original
    source.write_bytes(b'changed original workbook')
    with pytest.raises(ValueError,match='source bytes changed'):M.package_history(p,old,r,cp)


def test_immutable_receipt_tree_rejects_missing_added_and_linked_history(tmp_path):
    d=tmp_path/'receipts';d.mkdir();(d/'original.json').write_text('{}');original=M.immutable_tree(d)
    (d/'extra.json').write_text('{}');assert M.immutable_tree(d)!=original
    (d/'extra.json').unlink();(d/'original.json').unlink();assert M.immutable_tree(d)!=original
    (d/'alias').symlink_to(tmp_path)
    with pytest.raises(ValueError,match='link'):M.immutable_tree(d)


@pytest.mark.parametrize('fault',[None,'start-selection','start-handoff','predecessor','unsealed'])
def test_historical_start_requires_owning_selection_and_contiguous_tail(tmp_path,monkeypatch,fault):
    old={'home':str(tmp_path/'old'),'integration':put(tmp_path/'integration.json',{}),'descriptor':put(tmp_path/'environment-descriptor.json',{})}
    source=tmp_path/'source.xlsx';source.write_bytes(b'original');previous=put(tmp_path/'previous.json',old)
    p={'home':str(tmp_path/'next'),'operations':str(tmp_path/'ops'),'previous':previous,'source':M.binding(source),'helper':M.binding(Path(M.__file__).resolve())}
    home=Path(p['home']);home.mkdir();recovery=Path(old['home'])/'prepare/recovery';recovery.mkdir(parents=True)
    prepared=put(recovery/'prepared.json',{});value=dict(status='ENVIRONMENT_READY',descriptor=old['descriptor'],selection={'exact':'packaged'},lease={'old':1})
    cp=put(Path(old['home'])/'execute/result.json',value);put(recovery/'result.json',value);put(Path(old['home'])/'execute/outcome.json',{})
    called=[];R=SimpleNamespace(code=lambda i:['selected'],validate=lambda x,active:called.append(('validate',x,active)),source_runtime=lambda i:SimpleNamespace(completed_commands=lambda *a:called.append(('physical',))))
    receipt=put(home/'package-history.json',M.package_history(p,old,R,cp));handoff=put(home/'environment-handoff.json',dict(lease={'new':3},evidence=[receipt]))
    desc=put(home/'instruction-start/descriptor.json',dict(initial=dict(selection=value['selection'],lease={'new':3}),origin=[handoff]))
    start=dict(status='INSTRUCTION_READY',selection={}if fault=='start-selection'else value['selection'],lease={'new':3},descriptor=desc)
    result=put(home/'instruction-start/result.json',start);outcome=put(home/'instruction-start/outcome.json',{})
    put(home/'instruction-start/intent.json',dict(action='start',arguments=dict(environment_descriptor=old['descriptor'],checkpoint={}if fault=='start-handoff'else handoff,worker_run=str(tmp_path/'run/instruction'))))
    directory=Path(p['operations'])/'000';intent=put(directory/'intent.json',dict(action='step',arguments=dict(checkpoint={}if fault=='predecessor'else result,descriptor=desc)))
    if fault!='unsealed':tail=put(directory/'outcome.json',{})
    O=SimpleNamespace(successful=lambda *a:(cp,value));D=SimpleNamespace(successful=lambda x,e:(result,start)if x==outcome else (x,{}))
    monkeypatch.setattr(M,'historical_adapter',lambda *a:called.append(('adapter',a[-1])))
    env=(None,None,None,None,None,{'run_root':str(tmp_path/'run')})
    if fault:
        with pytest.raises(ValueError):M.historical_environment(p,old,O,D,env,None,R,previous,None)
    else:
        assert M.historical_environment(p,old,O,D,env,None,R,previous,None)==(cp,value)
        assert ('validate',prepared,False)in called and ('physical',)in called
        assert len([x for x in called if x[0]=='adapter'])==2
