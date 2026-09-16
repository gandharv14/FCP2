import json,sys,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import instruction_semantic_continue as U


def fixture(tmp_path,monkeypatch):
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return U.binding(path)
    code=tmp_path/'code.py';code.write_text('# frozen')
    C=SimpleNamespace(__file__=str(code),MEMORY=8589934592,encode=lambda x:x)
    stage=tmp_path/'owning-writer';worker=object()
    I=SimpleNamespace(__file__=str(code),SCHEMA='instruction/v1',selected=lambda x:(worker,{},None),guard_seconds=lambda *a:3300)
    W=SimpleNamespace(step_directory=lambda *a:stage)
    home=tmp_path/'old';d=home/'first-step';controls={'fixed':True};value={'descriptor':{'id':'retained'}}
    checkpoint=put(home/'permit/result.json',{'status':'RETRY_READY'})
    intent=put(d/'intent.json',dict(action='step',schema_version=I.SCHEMA,code=U.binding(code),base_code=U.binding(code),arguments=dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls=controls,memory_limit_bytes=C.MEMORY,models=True,commands=True,containers=True,timeout_seconds=3300))
    log=d/'step.log';log.write_text('repair.validate_historical_integrity(worker, before)\nscripts.batch.harbor_admission.AdmissionError: foreign historical instruction repair\n')
    guard=put(d/'resource.json',dict(returncode=1,failure=None,intent=intent,log=U.binding(log)))
    parent=put(tmp_path/'parent.json',{'original':True})
    put(d/'semantic-bootstrap.json',dict(schema_version='fleet-retained-semantic-child/v1',plan=parent,helper=U.binding(code),kind='instruction',intent=intent,original_command=[sys.executable,'-B',str(code.resolve()),'_child',intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False))
    failed=put(d/'outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,automatic_retry=False,resource=guard))
    put(d/'dependency.json',dict(schema_version='harbor-dependency-observation/v2'))
    process=put(tmp_path/'process.json',{'pid':2147483000})
    p=dict(failed_guard=failed,unstarted_semantic=dict(process=process),semantic_context=dict(plan=parent));old={'output':str(home),'helper':U.binding(code)}
    return p,old,(C,I,None,W,None,{},tmp_path,controls),checkpoint,value,stage,put


def test_sealed_unstarted_child_is_reconciled(tmp_path,monkeypatch):
    *args,stage,put=fixture(tmp_path,monkeypatch)
    U.sealed_semantic_child(*args)


@pytest.mark.parametrize('mutation',['uncertain','extra_artifact','stage_started','active_process','other_error','wrong_checkpoint'])
def test_sealed_child_rejects_replay_risks(tmp_path,monkeypatch,mutation):
    p,old,env,checkpoint,value,stage,put=fixture(tmp_path,monkeypatch)
    d=Path(old['output'])/'first-step';failed=U.read(p['failed_guard']);r=U.read(failed['resource'])
    if mutation=='stage_started':stage.mkdir()
    elif mutation=='extra_artifact':(d/'response.json').write_text('{}')
    elif mutation=='active_process':
        p['unstarted_semantic']['process']=put(tmp_path/'live.json',{'pid':os.getpid()})
        original=Path.exists
        monkeypatch.setattr(Path,'exists',lambda self:True if self==Path('/proc',str(os.getpid()))else original(self))
    else:
        if mutation=='uncertain':r['returncode']=None
        elif mutation=='other_error':(d/'step.log').write_text('provider timeout');r['log']=U.binding(d/'step.log')
        else:
            intent=U.read(r['intent']);intent['arguments']['checkpoint']={'different':True};r['intent']=put(d/'intent.json',intent)
        failed['resource']=put(d/'resource.json',r);p['failed_guard']=put(d/'outcome.json',failed)
    with pytest.raises(ValueError):U.sealed_semantic_child(p,old,env,checkpoint,value)

@pytest.mark.parametrize('fault',[None,'other_error','stage_started','wrong_kind'])
def test_sealed_aggregate_predecessor_failure_retains_original_permission(tmp_path,monkeypatch,fault):
    p,old,env,checkpoint,value,stage,put=fixture(tmp_path,monkeypatch)
    p['unstarted_semantic']['kind']='aggregate-history';d=Path(old['output'])/'first-step'
    log=d/'step.log';log.write_text('H.adoption_history(A,R,w,before,historical_evidence=G.historical_evidence)\nvalidated=A.validate(selected,before)\nscripts.batch.harbor_admission.AdmissionError: prior owning continuations differ\n')
    if fault=='other_error':log.write_text('different failure')
    elif fault=='wrong_kind':p['unstarted_semantic']['kind']='unlisted'
    elif fault=='stage_started':stage.mkdir()
    failed=U.read(p['failed_guard']);guard=U.read(failed['resource']);guard['log']=U.binding(log)
    failed['resource']=put(d/'resource.json',guard);p['failed_guard']=put(d/'outcome.json',failed)
    if fault:
        with pytest.raises(ValueError):U.sealed_semantic_child(p,old,env,checkpoint,value)
    else:U.sealed_semantic_child(p,old,env,checkpoint,value)

@pytest.mark.parametrize('fault',[None,'schema','module','name','unrelated','guard'])
def test_aggregate_runtime_changes_only_declared_predecessor_function(tmp_path,fault):
    def put(name,v):
        p=tmp_path/name;p.write_text(json.dumps(v));return U.binding(p)
    original=tmp_path/'old.py';original.write_text('CONSTANT=1\ndef adoption_history(*a):\n return 1\n')
    selected=tmp_path/'new.py';selected.write_text(original.read_text().replace('return 1','return 2'))
    q=dict(schema_version='fleet-retained-completed-aggregate/v1',aggregate_history=dict(helper=U.binding(original)))
    symbol=dict(module_name='completed_aggregate_history',names=['adoption_history'],original=U.binding(original),selected=U.binding(selected))
    profile=dict(schema_version='runtime',runtime='bound',patches=[],dependencies=[],symbols=[symbol],guard_modules=[],child_plans=[],journal_roots=[])
    if fault=='schema':q['schema_version']='foreign'
    elif fault=='module':symbol['module_name']='scripts.batch.harbor_instruction_worker'
    elif fault=='name':symbol['names']=['authorize']
    elif fault=='unrelated':selected.write_text(selected.read_text().replace('CONSTANT=1','CONSTANT=2'));symbol['selected']=U.binding(selected)
    elif fault=='guard':profile['guard_modules']=['foreign']
    old=dict(retained_context=dict(plan=put('q.json',q)));p=dict(unstarted_semantic=dict(kind='aggregate-history',runtime='bound'))
    if fault:
        with pytest.raises(ValueError):U.unstarted_history_runtime(p,old,(),SimpleNamespace(SCHEMA='runtime'),profile)
    else:U.unstarted_history_runtime(p,old,(),SimpleNamespace(SCHEMA='runtime'),profile)

@pytest.mark.parametrize('fault',[None,'foreign_worker','foreign_consumer'])
def test_original_installed_worker_consumer_uses_same_validated_aggregate_history(tmp_path,monkeypatch,fault):
    from scripts.batch import harbor_disclosure_round_adoption as A,harbor_instruction_worker as W,harbor_disclosure_review_resume as D
    from scripts import harbor_instruction_campaign as I
    import scripts.batch as package
    def put(name,v):
        p=tmp_path/name;p.write_text(json.dumps(v));return U.binding(p)
    f=tmp_path/'history.py';f.write_text('def adoption_history(*a,**k):\n return ({"validated":True},["archive"])\n')
    h=U.module('test_aggregate_history',U.binding(f));monkeypatch.setitem(sys.modules,'completed_aggregate_history',h)
    adoption=put('adoption.json',dict(adoption=U.binding(A.__file__)));q=put('q.json',dict(context=dict(plan=adoption),aggregate_history=dict(helper=U.binding(f),archive=put('archive.json',{}))))
    old=dict(retained_context=dict(plan=q));profile=dict(symbols=[dict(selected=U.binding(f))]);worker=SimpleNamespace(root=tmp_path,run=tmp_path/'run',context='context',disclosure_repair='receipt')
    monkeypatch.setattr(U,'module',lambda *a:SimpleNamespace(historical_evidence=lambda *a:[]))
    monkeypatch.setattr(A,'selected',lambda w:w.disclosure_repair is not None)
    selected=dict(implementation=A.record(A.__file__),instruction=A.record(I.__file__),worker=A.record(W.__file__),resume=A.record(D.__file__),functions=dict(operation=A.function_sha(W._operation),disclosure_history=A.function_sha(I._disclosure_history),aggregate=A.function_sha(A.review.validate_aggregate)))
    with A.installed(selected):
        proxy=package.harbor_disclosure_review_resume;prior=proxy.validate
        if fault=='foreign_consumer':proxy.validate=lambda *a:None
        if fault=='foreign_consumer':
            with pytest.raises(ValueError):U.unstarted_aggregate_consumer(old,worker,profile)
        else:
            with U.unstarted_aggregate_consumer(old,worker,profile):
                if fault:
                    foreign=SimpleNamespace(**{**vars(worker),'run':tmp_path/'foreign'})
                    with pytest.raises(ValueError):proxy.validate(foreign,{})
                else:assert proxy.validate(worker,{})=={'validated':True}
            assert proxy.validate is prior
