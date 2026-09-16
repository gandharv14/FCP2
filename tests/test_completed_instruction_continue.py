import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.fleet import completed_instruction_continue as S


def test_standalone_cold_entry_imports_without_repository_on_sys_path():
    result=subprocess.run([sys.executable,'-I','-B',S.__file__,'--help'],capture_output=True,text=True)
    assert result.returncode==0 and '{run,child}'in result.stdout


@pytest.mark.parametrize('fault',[None,'missing_prefix','foreign_sidecar','different_guard','wrong_worker'])
def test_actual_prefix_contract_precedes_any_new_handoff(tmp_path,fault):
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return S.binding(path)
    home=tmp_path/'old';ops=tmp_path/'operations'
    old=dict(output=str(home),helper={'path':'old-helper','sha256':'old'},owner='old-owner',fence=4)
    prior=keep(tmp_path/'old-plan.json',old);source=dict(schema_version='fleet-disclosure-command-continuation/v1')
    first=keep(tmp_path/'prior-result.json',{'original':True})
    descriptor=keep(tmp_path/'descriptor.json',{})
    I=SimpleNamespace(__file__=__file__,guard_seconds=lambda *a:1350)
    env=(SimpleNamespace(encode=lambda x:x),I,None,None,None,None,None,'controls')
    intent=keep(ops/'000/intent.json',dict(action='step',code=S.binding(__file__),controls='controls',timeout_seconds=1350,
        arguments=dict(checkpoint=first,descriptor=descriptor)))
    guard=keep(ops/'000/resource.json',dict(intent=intent))
    value=dict(status='STEP_COMPLETE',next_step='enqueue',descriptor=descriptor)
    cp=keep(ops/'000/result.json',value);outcome=keep(ops/'000/outcome.json',dict(result=cp,resource=guard))
    side=dict(schema_version=source['schema_version'],plan=prior,helper=old['helper'],kind='instruction',intent=intent,
        original_command=['original'],seconds=1350,memory_bytes=8589934592)
    if fault=='foreign_sidecar':side['helper']={'path':'foreign','sha256':'foreign'}
    keep(ops/'000/disclosure-bootstrap.json',side)
    next_intent=keep(ops/'001/intent.json',dict(arguments=dict(checkpoint=cp,descriptor=descriptor)))
    next_guard=keep(ops/'001/resource.json',dict(intent=next_intent))
    failure=keep(ops/'001/outcome.json',dict(resource=next_guard))
    keep(home/'authoring-job.json',dict(operations=str(ops)))
    worker_job=keep(tmp_path/'worker-job.json',dict(driver=old['helper'],result=str(home/'result.json')))
    wi=keep(tmp_path/'worker-intent.json',dict(owner=old['owner'],job=worker_job))
    outer=keep(tmp_path/'worker-outcome.json',dict(returncode=1,failure_type=None,result=None,intent=wi))
    bp=keep(tmp_path/'bridge.json',dict(outcome=outcome,result=cp,descriptor=descriptor,guard=next_guard))
    p=dict(previous=prior,bridge_profile=bp,failure=failure,worker_outcome=outer)
    D=SimpleNamespace(successful=lambda row,env:(S.read(row)['result'],S.read(S.read(row)['result'])))
    U=SimpleNamespace(retained_command=lambda *a:('instruction',intent))
    if fault=='missing_prefix':first=keep(tmp_path/'foreign-result.json',{})
    if fault=='different_guard':p['failure']=outcome
    if fault=='wrong_worker':old=dict(old,owner='foreign')
    if fault:
        with pytest.raises(ValueError):S.retained_prefix(p,old,source,U,D,env,first)
    else:S.retained_prefix(p,old,source,U,D,env,first)


def test_prepared_review_runs_original_first_step_once_without_tail_or_reprepare(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from scripts.fleet import completed_instruction_continue as M
    import json
    p=dict(output=str(tmp_path/'new'),workbook='0001',bridge_profile={'real':'profile'});events=[];calls=[]
    handoff=tmp_path/'handoff.json';handoff.write_text(json.dumps(dict(descriptor={'real':'descriptor'},result={'real':'handoff'})))
    done=M.binding(handoff);w=SimpleNamespace(root=tmp_path)
    U=SimpleNamespace(custody=lambda *a:events.append('custody'),keep=lambda path,value: calls.append(value)or value)
    B=SimpleNamespace(handoff=lambda *a:events.append('handoff'))
    S=SimpleNamespace(install_commands=lambda *a:events.append('guards'))
    @contextmanager
    def slot(wb):
        assert wb=='0001';yield (4,5)
    def run(action,args,controls,directory,**options):
        assert action=='step'and args==dict(descriptor={'real':'descriptor'},checkpoint={'real':'handoff'})
        assert directory==tmp_path/'new/first-step'and options==dict(models=True,commands=True,containers=True,guardian_lock_fds=(4,5))
        events.append('original-step');return {'real':'outcome'}
    I=SimpleNamespace(run=run)
    D=SimpleNamespace(fleet_slot=slot,successful=lambda *a:({'real':'result'},dict(status='STEP_COMPLETE')),run=lambda *a:pytest.fail('not a new tail allowance'))
    env=[None,I,None,None,None,None,None,'controls'];iteration=iter((None,done))
    @contextmanager
    def context(row):yield p,p,S,U,D,env,w,None,None,None,None,{},B,None,None,next(iteration)
    monkeypatch.setattr(M,'context',context)
    result=M.run_prepared({'actual':'plan'})
    assert events==['custody','handoff','custody','guards','original-step']
    assert result['status']=='STEP_COMPLETE'and result['outcome']=={'real':'outcome'}and result['prepared_request_preserved']is True

@pytest.mark.parametrize('fault',[None,'selected','extra','entrypoint'])
def test_current_lifecycle_is_one_exact_takeover_dependency(tmp_path,monkeypatch,fault):
    from contextlib import contextmanager
    old=tmp_path/'root/scripts/batch/harbor_tracker_lifecycle.py';old.parent.mkdir(parents=True);old.write_text('old')
    new=tmp_path/'new.py';new.write_text('selected')
    helper=tmp_path/'runtime.py';helper.write_text('runtime')
    p=dict(history={'path':'history','sha256':'h'},previous={'path':'previous','sha256':'p'})
    data=dict(schema_version='runtime/v1',runtime=S.binding(helper),entrypoint=p['history'],read_call=dict(function='context',args=[p['previous']],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],symbols=[],dependencies=[dict(module_name='scripts.batch.harbor_tracker_lifecycle',original=S.binding(old),selected=S.binding(new))])
    data['symbols']=[dict(data['dependencies'][0],names=['validate_lifecycle'])]
    if fault=='selected':data['dependencies'][0]['selected']=S.binding(old)
    if fault=='extra':data['symbols']=[{}]
    if fault=='entrypoint':data['entrypoint']={}
    profile=tmp_path/'profile.json';profile.write_text(json.dumps(data));p['lifecycle_runtime']=dict(helper=S.binding(helper),profile=S.binding(profile));events=[]
    @contextmanager
    def installed(row):
        events.append('installed')
        try:yield
        finally:events.append('restored')
    monkeypatch.setattr(S,'module',lambda *a:SimpleNamespace(SCHEMA='runtime/v1',installed=installed))
    worker=SimpleNamespace(root=tmp_path/'root');T=SimpleNamespace(life=SimpleNamespace(__file__=new))
    if fault:
        with pytest.raises(ValueError,match='only the actual takeover'): 
            with S.prepared_lifecycle_runtime(p,worker,T):pytest.fail('foreign runtime entered')
        assert events==[]
    else:
        with pytest.raises(RuntimeError):
            with S.prepared_lifecycle_runtime(p,worker,T):raise RuntimeError('original consumer failed')
        assert events==['installed','restored']


@pytest.mark.parametrize('fault',[None,'skipped','failed','sidecar','unsealed','numbering'])
def test_completed_review_tail_uses_exact_contiguous_guarded_predecessors(tmp_path,fault):
    def put(path,v):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return S.binding(path)
    row=put(tmp_path/'plan.json',{});helper=S.binding(__file__);p=dict(output=str(tmp_path/'tail'),helper=helper)
    first=put(tmp_path/'first.json',{});ops=tmp_path/'run/dispatcher-v2/tail';d=ops/('001'if fault=='numbering'else'000')
    intent=put(d/'intent.json',dict(action='step',arguments=dict(checkpoint={}if fault=='skipped'else first)))
    result=put(d/'result.json',{'next_step':'nat-init'});guard=put(d/'resource.json',dict(intent=intent,returncode=1 if fault=='failed'else 0,failure=None))
    if fault!='unsealed':put(d/'outcome.json',dict(resource=guard,result=result))
    put(d/'disclosure-bootstrap.json',dict(schema_version='fleet-disclosure-command-continuation/v1',plan={}if fault=='sidecar'else row,
        helper=helper,kind='instruction',intent=intent,original_command=['real'],seconds=3300,memory_bytes=8589934592))
    U=SimpleNamespace(retained_command=lambda *a:('instruction',intent),retained_seconds=lambda *a:3300)
    D=SimpleNamespace(successful=lambda r,e:(S.read(r)['result'],S.read(S.read(r)['result'])))
    env=[None]*8;env[5]=dict(run_root=str(tmp_path/'run'))
    if fault:
        with pytest.raises(ValueError):S.completed_review_prefix(row,p,U,D,env,first,{})
    else:assert S.completed_review_prefix(row,p,U,D,env,first,{})==result
    if fault=='unsealed':assert S.completed_review_prefix(row,p,U,D,env,first,{},invocation=(intent,{}))==first
