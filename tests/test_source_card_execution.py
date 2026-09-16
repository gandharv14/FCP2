"""Real v8 local-env renewal guard interception; no actual subprocess or lease."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json
import time
import pytest
from test_source_card_worker import case, instruction_setup
from scripts import harbor_instruction_campaign as C
from scripts.fleet import source_card_execution as X, undispatched_campaign as A

REPO=Path(__file__).resolve().parents[1]
ROOT=REPO/'diagnostics/vm-fleet-20260912/control/source-to-authoring-001/code-005/0200/code/3ea7e135b1b4dc2acaa88b5bff985ccf49f98a3aa3c46f3c886ce06b22ba1c11'


def original_driver():
    path=ROOT/'scripts/fleet/advance_authoring_v8.py'
    spec=importlib.util.spec_from_file_location('source_card_test_original_v8',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_real_v8_renew_local_environment_still_emits_cold_bound_child(tmp_path, monkeypatch):
    original=original_driver(); seen=[]
    base=SimpleNamespace(**C.base.__dict__)
    control=tmp_path/'control.lock';control.touch()
    base._controls=lambda _:str(control)
    def guard(command,**options):seen.append((command,options));return 0
    base._run_in_process_group=guard
    campaign=SimpleNamespace(**{**C.__dict__,'base':base})
    evidence=[X.binding(X.__file__),X.binding(A.__file__)]
    plan=tmp_path/'plan.json';plan.write_text('{}\n');bound=X.binding(plan)
    reader=SimpleNamespace(dependencies=lambda:evidence)
    selected=X.dispatcher(original,{'campaign':campaign},A,plan_binding=bound,entry=evidence[0],reader=reader)
    selected.declared_namespace['renewal_context']=lambda *_:(None,None,None,None,None)
    active=SimpleNamespace(active=lambda *_:(None,{'leases':{'task_agent':{'expires_at':time.time_ns()+7200*10**9}}},None))
    env=(base,campaign,None,active,None,{},Path(base.ROOT),None)
    outcome=selected.renew({'fixture':'job'},env,{'fixture':'checkpoint'},tmp_path/'renewal',())
    assert len(seen)==1
    command,options=seen[0]
    assert command[1:5]==['-I','-B',evidence[0]['path'],'owning-child']
    assert command[command.index('--kind')+1]=='renewal'
    assert options['timeout_seconds']==900 and options['memory_limit_bytes']==8*1024**3
    side=json.loads((tmp_path/'renewal/declared-runtime-adapter.json').read_bytes())
    assert side['reader_plan']==bound and side['reader_evidence']==evidence
    assert side['selected_command']==command and side['original_command'][3]=='renew-child'
    assert original.renew.__code__ is not selected.renew.__code__
    for name in ('run','renewal_child','setup','selected','successful'):
        assert getattr(original,name).__code__ is getattr(selected,name).__code__
    assert json.loads(Path(outcome['path']).read_bytes())['status']=='RECONCILIATION_REQUIRED'
    assert not (tmp_path/'renewal/result.json').exists()


def test_child_probe_is_rejected_before_any_owned_step(monkeypatch):
    monkeypatch.setattr(X,'load',lambda *a,**k:(None,)*7)
    with pytest.raises(ValueError,match='diagnostic'):
        X.owning_child({},'instruction',{},probe=True)


def test_plain_permit_lease_uses_actual_typed_takeover_not_transition_decode():
    from dataclasses import asdict
    from scripts.batch.harbor_workbook_bootstrap import Lease
    lease=Lease('fixture-owner','fixture-token',18,'a'*64)
    bound={'path':'fixture-takeover','sha256':'b'*64}
    checked=[]
    def validate(row):
        assert row==bound;checked.append(row);return {},{},lease
    reader=SimpleNamespace(plan={'takeover':bound},takeover=SimpleNamespace(validate_completion=validate))
    raw={'lease':asdict(lease)}
    with pytest.raises((ValueError,TypeError,KeyError)):
        C.base.decode(raw['lease'])
    typed=X.takeover_lease(reader,raw)
    assert typed is lease and C.base.decode(C.base.encode(typed))==lease and checked==[bound]
    with pytest.raises(ValueError,match='takeover'):
        X.takeover_lease(reader,{'lease':{**raw['lease'],'revision':17}})


@pytest.mark.parametrize('phase',['dialogue-writer','dialogue-review'])
def test_actual_child_sidecar_reaches_original_worker_operation_boundary(case,monkeypatch,tmp_path,phase):
    from dataclasses import asdict
    import sys
    from scripts.batch import harbor_instruction_worker as W
    from scripts.fleet import source_card_worker,source_card_readiness
    from test_source_card_worker import prepared
    worker,selection,lease,old,authority,files,reader,verified=prepared(case,monkeypatch)
    reader.authority=authority
    (worker.context.tracker_root/'0200.json').write_bytes((worker.context.tracker_root/'0245.json').read_bytes())
    active=W.active
    def current(*args):
        f,state,bound=active(*args)
        return f,{**state,'leases':{'task_agent':{'expires_at':time.time_ns()+7200*10**9}}},bound
    monkeypatch.setattr(W,'active',current)
    reached=[]
    class BeforeOwningOperation(BaseException):pass
    def operation(w,s,l,chosen,directory,**caps):
        assert l==lease
        import os
        assert os.fstat(caps.pop('lock_fd'))
        if chosen==phase:
            reached.append((chosen,caps));raise BeforeOwningOperation()
        return s,l,None
    monkeypatch.setattr(W,'_operation',operation)
    selectedW=source_card_worker.install(W,reader,W.producer)
    checkpoint=authority
    if phase=='dialogue-review':
        for _ in range(2):
            done=selectedW.step(worker,selection,lease,checkpoint=checkpoint)
            checkpoint=done.evidence
    selectedC=source_card_readiness.campaign(C,reader,selectedW)
    selectedC.declared_namespace['dependency']=lambda recipe,directory:C.base.keep(directory/'dependency.json',{'fixture_only':True})
    controls=C.base.Controls(tmp_path/'cold-child.lock')
    recipe=C.base.admission.DependencyRecipe(X.binding(Path(sys.executable).resolve()),(),(),False,4,2)
    descriptor=C.base.keep(tmp_path/'cold-descriptor.json',dict(schema_version=C.SCHEMA,
        worker=C.worker_encode(worker),controls=C.base.encode(controls),dependencies=C.base.encode(recipe),
        origin=[authority],code=X.binding(C.__file__),initial=dict(selection=C.codec.encode(selection),lease=C.base.encode(lease))))
    previous=C.base.keep(tmp_path/'cold-checkpoint.json',C._result('RETRY_READY' if phase=='dialogue-writer' else 'STEP_COMPLETE',
        descriptor,selection,lease,checkpoint=checkpoint,next_step=phase))
    directory=tmp_path/'cold-guard';directory.mkdir()
    intent=C.base.keep(directory/'intent.json',dict(schema_version=C.SCHEMA,action='step',
        arguments=dict(descriptor=descriptor,checkpoint=previous),controls=C.base.encode(controls),
        models=False,commands=False,containers=False,code=X.binding(C.__file__),base_code=X.binding(C.base.__file__),
        executable=X.binding(Path(sys.executable).resolve()),memory_limit_bytes=8*1024**3,timeout_seconds=3300))
    plan=C.base.keep(tmp_path/'execution-fixture.json',{'fixture_only':True})
    entry=X.binding(X.__file__);evidence=reader.dependencies()
    command=[sys.executable,'-B',C.__file__,'_child',intent['path'],intent['sha256']]
    cold=[sys.executable,'-I','-B',entry['path'],'owning-child','--plan',plan['path'],'--plan-sha256',plan['sha256'],
        '--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    C.base.keep(directory/'declared-runtime-adapter.json',dict(schema_version=A.SCHEMA,adapter=A.binding(A.__file__),
        producer_code=X.binding(C.__file__),base_code=X.binding(C.base.__file__),kind='instruction',reader_plan=plan,
        child_wrapper=entry,reader_evidence=evidence,intent=intent,original_command=command,selected_command=cold,
        timeout_seconds=3300,memory_limit_bytes=8*1024**3,original_snapshot_unchanged=True,
        semantic_budget_reset=False,automatic_retry=False))
    # The cold-loader/full-source proof is separate. Here only custody and
    # installation fixtures are substituted; child, descriptor/prior/step and
    # declared command validation execute their real implementations.
    monkeypatch.setattr(X,'load',lambda *a,**kw:({'entry':entry},None,reader,{'campaign':selectedC},None,(C.base,),A))
    monkeypatch.setattr(X,'live',lambda *a:None)
    monkeypatch.setattr(sys,'argv',cold[3:])
    with pytest.raises(BeforeOwningOperation):X.owning_child(plan,'instruction',intent)
    assert reached==[(phase,{'models':False,'commands':False,'containers':False,'credential':None})]
    assert not (directory/'result.json').exists()
    assert all(Path(path).read_bytes()==raw for path,raw in files.items())


@pytest.mark.parametrize('fault',[None,'memory','returncode','command','intent','origin','phase','result'])
def test_actual_authorization_child_emits_typed_prior_and_guard_tamper_rejects(case,monkeypatch,tmp_path,fault):
    import sys
    from dataclasses import replace
    from scripts.batch import harbor_instruction_worker as W,harbor_model_executor as E,harbor_tracker_lifecycle as L,harbor_lease_takeover as T
    from scripts.fleet import source_card_authorize as authority_module,source_card_readiness
    from test_source_card_worker import prepared
    worker,s,lease,old,authority,files,reader,verified=prepared(case,monkeypatch)
    reader.authority=authority
    tracker=worker.context.tracker_root/'0200.json';tracker.write_bytes((worker.context.tracker_root/'0245.json').read_bytes())
    takeover=C.base.keep(tmp_path/'unexecuted-takeover.json',{'fixture_only':True})
    controls=C.base.Controls(tmp_path/'author-control.lock')
    recipe=C.base.admission.DependencyRecipe(X.binding(Path(sys.executable).resolve()),(),(),False,4,2)
    old_descriptor=C.base.keep(tmp_path/'old-descriptor.json',dict(schema_version=C.SCHEMA,
        worker=C.worker_encode(replace(worker,dialogue_pre_review_repair=None)),controls=C.base.encode(controls),
        dependencies=C.base.encode(recipe),origin=[],code=X.binding(C.__file__),initial=dict(selection=C.codec.encode(s),lease=C.base.encode(old))))
    reader.plan={**reader.plan,'old_descriptor':old_descriptor,'blocked':C.base.keep(tmp_path/'old-blocked.json',{'fixture_only':True}),'takeover':takeover}
    reader.takeover=SimpleNamespace(validate_completion=lambda bound:({}, {}, lease) if bound==takeover else pytest.fail('foreign takeover'))
    selectedC=source_card_readiness.campaign(C,reader,W)
    directory=tmp_path/'authorization';directory.mkdir()
    entry=X.binding(X.__file__);p={'entry':entry,'authorization_directory':str(directory)}
    bound=C.base.keep(tmp_path/'execution.json',{'fixture_only':True})
    intent=C.base.keep(directory/'intent.json',X.authorization_intent(p,bound))
    driver=SimpleNamespace(retain=C.base.keep)
    env=(C.base,selectedC,SimpleNamespace(run=None,execute=None))
    monkeypatch.setattr(X,'load',lambda *a,**k:(p,None,reader,{'campaign':selectedC},driver,env,None))
    monkeypatch.setattr(X,'live',lambda *a:None)
    monkeypatch.setattr(authority_module,'authorize',lambda *a,**k:authority)
    for module,name in ((E,'execute'),(E,'_open'),(L,'lease_operation'),(T,'replace_expired')):
        monkeypatch.setattr(module,name,getattr(module,name))
    argv=[entry['path'],'authorize-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],
        '--intent',intent['path'],'--intent-sha256',intent['sha256']]
    monkeypatch.setattr(sys,'argv',argv)
    X.authorize_child(bound,intent)
    result=X.binding(directory/'result.json');value=X.checked(result)
    assert C.base.decode(value['lease'])==lease and value['checkpoint']==authority and verified
    (directory/'step.log').write_text('fixture-only child\n')
    resource=dict(intent=intent,returncode=0,failure=None,metrics={},log=X.binding(directory/'step.log'),
        command=[sys.executable,'-I','-B',*argv],seconds=900,memory_bytes=8*1024**3)
    if fault=='memory':resource['memory_bytes']*=2
    elif fault=='returncode':resource['returncode']=1
    elif fault=='command':resource['command'].remove('-I')
    elif fault=='intent':resource['intent']=bound
    elif fault=='origin':
        d=X.checked(value['descriptor']);d['origin']=d['origin'][:-1]
        Path(value['descriptor']['path']).write_bytes(C.base.executor.canonical(d))
        value['descriptor']=X.binding(value['descriptor']['path'])
    elif fault=='phase':value['next_step']='dialogue-review'
    elif fault=='result':value['checkpoint']=takeover
    if fault in {'origin','phase','result'}:
        Path(result['path']).write_bytes(C.base.executor.canonical(value));result=X.binding(result['path'])
    res=C.base.keep(directory/'resource.json',resource)
    outcome=C.base.keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION',resource=res,result=result,automatic_retry=False))
    if fault is None:
        assert X.authorization_outcome(outcome,p=p,plan_binding=bound,reader=reader,env=env)[0]==result
    else:
        with pytest.raises((ValueError,KeyError,TypeError)):
            X.authorization_outcome(outcome,p=p,plan_binding=bound,reader=reader,env=env)
