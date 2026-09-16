import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import dialogue_revision_adoption as M


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)


def test_namespace_keeps_original_globals_isolated():
    original=SimpleNamespace(offset=3);exec('def f(x): return x + offset',original.__dict__)
    selected=M.namespace(original);M.assign(selected,offset=9)
    assert original.f(2)==5 and selected.f(2)==11
    assert selected.f.__code__ is original.f.__code__


@pytest.mark.parametrize('fault',[None,'checkpoint','claims','source','candidate','claims_copy','claims_copy_changed'])
def test_retained_review_keeps_failed_intent_without_fabricating_checkpoint(tmp_path,fault):
    source=put(tmp_path/'source.json',{'immutable':True});claims=put(tmp_path/'claims.json',{'claims':[]})
    draft=put(tmp_path/'draft.json',{'original':'draft'});candidate=put(tmp_path/'candidate.json',{'derived':'draft'})
    intent=put(tmp_path/'failed/intent.json',{'step':'dialogue-review'})
    rejected=put(tmp_path/'rejected.json',dict(claims=claims,draft=draft,descriptor={},original_intent=intent,failed_resource={}))
    copied=put(tmp_path/'copy/claims.json',{'claims':[]}) if fault in ('claims_copy','claims_copy_changed') else claims
    revision=put(tmp_path/'revision.json',dict(claims=copied,source=source,corrected_draft=candidate))
    selected=put(tmp_path/'selected.json',dict(rejection=rejected,revision=revision,source=source,workbook='0061'))
    q=dict(schema_version='fleet-rejected-reader-review/v1',reader_selection=selected,proof=revision,candidate=candidate,source=source,workbook='0061')
    if fault=='checkpoint':put(tmp_path/'failed/complete.json',{'fabricated':True})
    elif fault in ('source','candidate'):q[fault]={}
    elif fault=='claims':
        q['proof']=put(tmp_path/'different-revision.json',dict(claims={},source=source,corrected_draft=candidate))
    if fault=='claims_copy_changed':Path(copied['path']).write_text('{"claims":["changed"]}')
    if fault and fault!='claims_copy':
        with pytest.raises(ValueError):M.retained_review(q)
    else:
        value=M.retained_review(q)
        assert value['original_intent']==intent and 'blocked'not in value
        assert M.predecessor(value)==(intent,[intent,{}])
        assert not(tmp_path/'failed/complete.json').exists()


def test_historical_guard_requires_explicit_bounded_version():
    assert M.guard_seconds({})==180
    assert M.guard_seconds({'retained_context':{'guard_seconds':1800}})==1800
    for value in (180,1801,3600):
        with pytest.raises(ValueError):M.guard_seconds({'retained_context':{'guard_seconds':value}})


def test_zero_model_controls_keep_lock_and_block_dispatch_then_restore(tmp_path):
    from dataclasses import dataclass,replace
    @dataclass(frozen=True)
    class Controls:
        slot_lock: Path
        env_file: Path | None
    lock=tmp_path/'slot.lock';lock.write_text('')
    selected=Controls(lock,tmp_path/'missing-secret');seen=[]
    def original(value):
        assert value.slot_lock.is_file()
        assert value.env_file is None or value.env_file.is_file()
        seen.append(value);return value.slot_lock
    execute=lambda:None;executor=SimpleNamespace(execute=execute,_open=execute)
    base=SimpleNamespace(Controls=Controls,_controls=original,executor=executor)
    with M.zero_model_controls(base,selected,dict(models=False,publish=False)):
        assert base._controls(selected)==lock and seen==[replace(selected,env_file=None)]
        with pytest.raises(ValueError):base._controls(replace(selected,slot_lock=tmp_path/'other'))
        for fn in (executor.execute,executor._open):
            with pytest.raises(ValueError,match='forbid model dispatch'):fn()
    assert base._controls is original and executor.execute is execute and executor._open is execute
    with pytest.raises(ValueError):
        with M.zero_model_controls(base,selected,dict(models=True,publish=False)):pass


def test_old_instruction_runner_keeps_native_and_external_guardians(tmp_path):
    import os
    fd=os.open(tmp_path/'fleet.lock',os.O_CREAT|os.O_RDWR,0o600)
    native=os.open(tmp_path/'workbook.lock',os.O_CREAT|os.O_RDWR,0o600)
    observed=[];guard=lambda command,**options:observed.append((command,options))
    base=SimpleNamespace(_run_in_process_group=guard)
    scope=dict(base=base)
    exec('def run(action,arguments,controls,directory,*,models=False,commands=False,containers=False):\n return base._run_in_process_group([action],guardian_lock_fds=(controls,),timeout_seconds=3300)\n',scope)
    instruction=SimpleNamespace(run=scope['run'])
    try:
        M.instruction_step(instruction,'step',{},native,tmp_path,models=False,commands=True,containers=True,guardian_lock_fds=(fd,))
        assert observed==[(['step'],dict(guardian_lock_fds=(native,fd),timeout_seconds=3300))]
        assert scope['base']is base and base._run_in_process_group is guard
        with pytest.raises(ValueError):M.instruction_step(instruction,'step',{},fd,tmp_path,models=True,guardian_lock_fds=(fd,))
    finally:os.close(fd);os.close(native)


def test_current_instruction_runner_forwards_guardians_unchanged():
    seen=[]
    def run(*args,guardian_lock_fds=(),**kwargs):seen.append((args,guardian_lock_fds,kwargs))
    M.instruction_step(SimpleNamespace(run=run),'step',guardian_lock_fds=(11,),models=False)
    assert seen==[(('step',),(11,),dict(models=False))]


@pytest.fixture
def diagnosed(tmp_path):
    root=tmp_path/'run';root.mkdir();initial=put(root/'initial.json',{'original':True})
    folder=root/'steps/final-runtime';q={'tracker':{'path':'retained','sha256':'exact'},'source':{'path':'source','sha256':'bound'}}
    review=put(tmp_path/'review.json',q);fixture=tmp_path/'fixture';output=fixture/'output'
    plan=put(fixture/'plan.json',{'output':str(output),'review_plan':review})
    selection={'content':{'stage':str(fixture/'0078-outputs'),'bindings':{'original_raw_source':q['source']}}}
    complete=put(output/'adoption/complete.json',{'selection':selection,'lease':{'test':True}})
    result=put(output/'adoption/result.json',{'status':'STEP_COMPLETE','next_step':'final-runtime','publication_performed':False,'checkpoint':complete,'selection':selection})
    intent=put(folder/'intent.json',{'step':'final-runtime','previous':complete,'selection':selection,'lease':{'test':True},'initial':initial,'code':{'original':'same'}})
    error=put(folder/'exception.json',{'type':'BeforeRuntimeWrite','message_sha256':M.hashlib.sha256(b'').hexdigest(),'automatic_replay':False})
    probe=put(tmp_path/'probe.json',{'fixture':True})
    proof={'schema_version':'fleet-derivative-diagnostic-preparation/v1','review_plan':review,'probe':probe,'fixture_plan':plan,'fixture_result':result,'intent':intent,'exception':error,'tracker':q['tracker'],'stage_sha256':'same'}
    def decode(value):
        return SimpleNamespace(content=SimpleNamespace(stage=Path(value['content']['stage']),bindings=value['content']['bindings']))
    W=SimpleNamespace(step_directory=lambda worker,phase:folder,code=lambda worker:{'original':'same'},codec=SimpleNamespace(decode=decode))
    return tmp_path,q,W,SimpleNamespace(run=root),proof


def check(case):
    tmp,q,W,w,p=case;return M.retained_preparation(put(tmp/'proof.json',p),q,W,w)


def test_exact_diagnostic_preparation_is_read_only(diagnosed):
    tmp,*_=diagnosed;before={str(p):p.read_bytes()for p in tmp.rglob('*')if p.is_file()}
    assert check(diagnosed)['schema_version']=='fleet-derivative-diagnostic-preparation/v1'
    assert all(Path(p).read_bytes()==v for p,v in before.items())


@pytest.mark.parametrize('fault',['extra_file','changed_exception','changed_tracker','foreign_fixture','changed_previous','changed_worker','actual_runtime'])
def test_diagnostic_reuse_rejects_unknown_effects(diagnosed,fault):
    tmp,q,W,w,p=diagnosed;directory=Path(p['intent']['path']).parent
    if fault in('extra_file','actual_runtime'):
        put(directory/('runtime/execution.json'if fault=='actual_runtime'else'unrecognized.json'),{})
    elif fault=='changed_tracker':p['tracker']={'path':'elsewhere','sha256':'foreign'}
    elif fault=='changed_exception':p['exception']=put(Path(p['exception']['path']),{'type':'TimeoutError','message_sha256':M.hashlib.sha256(b'').hexdigest(),'automatic_replay':False})
    else:
        intent=M.read(p['intent'])
        if fault=='changed_previous':intent['previous']=p['fixture_result']
        elif fault=='changed_worker':intent['code']={'new':'different'}
        else:
            result=M.read(p['fixture_result']);complete=M.read(result['checkpoint']);selected=dict(intent['selection']);selected={'content':dict(selected['content'],stage=str(tmp/'actual-task'))}
            complete['selection']=selected;result['selection']=selected;result['checkpoint']=put(Path(result['checkpoint']['path']),complete);p['fixture_result']=put(Path(p['fixture_result']['path']),result);intent['selection']=selected;intent['previous']=result['checkpoint']
        p['intent']=put(Path(p['intent']['path']),intent)
    with pytest.raises(ValueError):check(diagnosed)


def test_owning_namespace_requires_parent_intent_and_selected_sidecar(tmp_path):
    root=tmp_path.resolve()/'run';ops=root/'dispatcher-v2'/'remaining';folder=ops/'000';folder.mkdir(parents=True)
    plan=put(tmp_path/'plan.json',{'source':'fixture'})
    intent=put(folder/'intent.json',dict(action='step',models=False,commands=True,containers=True))
    put(folder/'declared-runtime-adapter.json',dict(reader_plan=plan))
    def fresh(config,value):
        if Path(value).exists():raise ValueError('exists')
        return Path(value)
    D=SimpleNamespace(operation_namespace=fresh);config=dict(run_root=str(root))
    with pytest.raises(ValueError):M.operation_namespace(D,config,str(ops))
    assert M.operation_namespace(D,config,str(ops),owning_intent=intent,plan=plan)==ops
    with pytest.raises(ValueError):M.operation_namespace(D,config,str(ops),owning_intent=intent,plan=dict(plan,sha256='f'*64))
    foreign=put(ops/'003/intent.json',dict(action='step',models=False,commands=True,containers=True))
    with pytest.raises(ValueError):M.operation_namespace(D,config,str(ops),owning_intent=foreign,plan=plan)
    bad=put(folder/'intent.json',dict(action='step',models=True,commands=True,containers=True))
    with pytest.raises(ValueError):M.operation_namespace(D,config,str(ops),owning_intent=bad,plan=plan)


@pytest.mark.parametrize('raises', [False, True])
def test_historical_findings_use_original_policy_and_restore_current(tmp_path, raises):
    path=tmp_path/'validator.py'
    path.write_text('def review_warnings(review, claims):\n return ["original-rejection"]\n')
    current=lambda *args:['current-style-warning']
    validator=SimpleNamespace(__file__=str(path),review_warnings=current)
    try:
        with M.historical_warnings(validator,M.binding(path)):
            assert validator.review_warnings({}, {})==['original-rejection']
            if raises:raise RuntimeError('retained evidence differs')
    except RuntimeError:
        assert raises
    assert validator.review_warnings is current


def test_historical_warning_policy_rejects_changed_bytes_and_foreign_validator(tmp_path):
    path=tmp_path/'validator.py';path.write_text('def review_warnings(*args):\n return []\n')
    bound=M.binding(path);validator=SimpleNamespace(__file__=str(path),review_warnings=lambda *args:[])
    path.write_text('def review_warnings(*args):\n return ["changed"]\n')
    with pytest.raises(ValueError):
        with M.historical_warnings(validator,bound):pass
    validator.__file__=str(tmp_path/'foreign.py')
    with pytest.raises(ValueError):
        with M.historical_warnings(validator,M.binding(path)):pass


def test_run_refreshes_lease_without_reopening_retained_module_stack(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import time
    controls=tmp_path/'controls';controls.touch();tracker=tmp_path/'trackers/fixture.json';tracker.parent.mkdir()
    tracker.write_text(json.dumps({'leases':{'task_agent':{'expires_at':time.time_ns()+7200*10**9}}}))
    output=tmp_path/'output';operations=tmp_path/'operations';worker_run=tmp_path/'worker';worker_run.mkdir()
    worker=SimpleNamespace(run=worker_run,context=SimpleNamespace(tracker_root=tracker.parent))
    policy={'workbook':'fixture','output':str(output),'operations':str(operations),'renewal_module':{},'helper':{'path':'unused'}}
    lease=object();renewed=SimpleNamespace(sha256=M.binding(tracker)["sha256"]);current=[lease];calls=[]
    B=SimpleNamespace(_controls=lambda _:str(controls),_run_in_process_group=lambda *a,**kw:0)
    W=SimpleNamespace(active=lambda *args:(None,json.loads(tracker.read_text()),M.binding(tracker)))
    D=SimpleNamespace(operation_namespace=lambda *args:None,fleet_slot=lambda _:nullcontext([]))
    def validate(_):return {'evidence':[]},[]
    def refresh():
        assert current[0]is renewed
        calls.append('authenticated-refresh');return renewed
    validate.refresh_lease=refresh
    validate.refresh_runner=lambda:calls.append("fresh-runner-inputs")
    env=(B,None,None,W,None,None,tmp_path,None)
    def context(_):
        assert not calls,'a second warm context load contaminates retained module imports'
        calls.append('original-context')
        return policy,{}, {},[],env,D,worker,None,lease,validate,None
    def renewal(**kw):
        current[0]=renewed;kw['directory'].mkdir();return renewed,{}
    L=SimpleNamespace(DURATION_NS=7200*10**9,SCHEMA='fleet-validated-continuation-lease/v1',renew=renewal)
    monkeypatch.setattr(M,'context',context);monkeypatch.setattr(M,'module',lambda *args:L)
    monkeypatch.setattr(M,'no_models',lambda:None);monkeypatch.setattr(M,'live',lambda _:None)
    class GuardReached(Exception):pass
    monkeypatch.setattr(M,'authorization',lambda *args:(_ for _ in()).throw(GuardReached()))
    with pytest.raises(GuardReached):M.run({'path':'selected','sha256':'exact'})
    assert calls==['original-context','authenticated-refresh','fresh-runner-inputs']
    assert (output/'adoption/resource.json').is_file()


@pytest.fixture
def renewed_only(tmp_path):
    home=tmp_path/'home';home.mkdir();run=tmp_path/'worker';run.mkdir()
    for name in ('lease-before.json','lease-after.json','lease/intent.json','lease/complete.json'):put(home/name,{})
    old=dict(helper={'path':'old','sha256':'old'},owner='old-owner',fence=8,operations=str(tmp_path/'old-operations'),output=str(home),workbook='fixture',source={'path':'source','sha256':'same'})
    plan=put(tmp_path/'old-plan.json',old)
    job=put(tmp_path/'job.json',dict(driver=old['helper'],inputs=[plan],result=str(home/'result.json'),run=str(run),workbook='fixture',source=old['source']))
    intent=put(run/'intent.json',dict(job=job,owner=old['owner']))
    log=run/'worker.log';log.write_text('retained traceback\nValueError: cached foreign implementation\n')
    outcome=put(run/'outcome.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=intent,log=M.binding(log)))
    process=put(tmp_path/'process.json',dict(job=job,pid=999999999))
    p=dict(old,output=str(tmp_path/'new-output'),helper={'path':'new','sha256':'new'},owner='new-owner',fence=9,operations=str(tmp_path/'new-operations'),renewed_preparation=dict(plan=plan,job=job,outcome=outcome,process=process))
    return p,home,run


def test_renewal_only_recovery_retains_exact_completed_lease(renewed_only):
    p,home,_=renewed_only;before={str(f):f.read_bytes()for f in home.rglob('*')if f.is_file()}
    assert M.renewed_preparation(p)==p['renewed_preparation']['plan']
    assert before=={str(f):f.read_bytes()for f in home.rglob('*')if f.is_file()}


@pytest.mark.parametrize('fault',['source','fence','extra','dispatcher','adoption','failure','live_process'])
def test_renewal_only_recovery_rejects_unknown_effects(renewed_only,fault):
    p,home,run=renewed_only
    if fault=='source':p['source']={'path':'source','sha256':'changed'}
    elif fault=='fence':p['fence']=8
    elif fault=='extra':put(home/'unknown.json',{})
    elif fault=='dispatcher':Path(M.read(p['renewed_preparation']['plan'])['operations']).mkdir()
    elif fault=='adoption':put(home/'adoption/certificate.json',{})
    elif fault=='failure':
        log=run/'worker.log';log.write_text('TimeoutError: uncertain\n');value=M.read(p['renewed_preparation']['outcome']);value['log']=M.binding(log);p['renewed_preparation']['outcome']=put(run/'outcome.json',value)
    else:
        value=M.read(p['renewed_preparation']['process']);value['pid']=1;p['renewed_preparation']['process']=put(Path(p['renewed_preparation']['process']['path']),value)
    with pytest.raises(ValueError):M.renewed_preparation(p)


@pytest.fixture
def failed_readiness(tmp_path):
    task=tmp_path/'enqueue';task.mkdir()
    failed=dict(publication_ready=False,blockers=['prose_mechanics'],checks=dict(prose_mechanics=dict(checked=False,error_type='AdmissionError')))
    c=dict(failed_readiness=put(task/'readiness.json',failed));put(task/'intent.json',{})
    error=dict(type='AdmissionError',message_sha256=M.hashlib.sha256(b'full current readiness did not qualify').hexdigest(),automatic_replay=False);put(task/'exception.json',error)
    tracker=tmp_path/'trackers/book.json';put(tracker,{})
    W=SimpleNamespace(active=lambda *a:(None,{},M.binding(tracker)),complete_readiness=lambda *a,**k:dict(publication_ready=True,blockers=[]))
    return c,task,error,None,None,SimpleNamespace(revision=18),W,tmp_path


def test_stopped_readiness_requires_fresh_full_pass(failed_readiness):
    assert M.stopped_readiness(*failed_readiness)is None


@pytest.mark.parametrize('fault',['remaining_blocker','wrong_old_gate','prepared_transaction','extra_file','uncertain_error'])
def test_stopped_readiness_preserves_failures_and_rejects_unknown_effects(failed_readiness,fault):
    c,task,error,worker,selection,lease,W,root=failed_readiness
    if fault=='remaining_blocker':W.complete_readiness=lambda *a,**k:dict(publication_ready=False,blockers=['numerical_verification'])
    elif fault=='wrong_old_gate':
        value=M.read(c['failed_readiness']);value['blockers']=['numerical_verification'];c['failed_readiness']=put(task/'readiness.json',value)
    elif fault=='prepared_transaction':(root/'trackers/book-notes-transactions/000019-prepared').mkdir(parents=True)
    elif fault=='extra_file':put(task/'submitted.json',{})
    else:error['type']='TimeoutError'
    with pytest.raises(ValueError):M.stopped_readiness(*failed_readiness)

@pytest.mark.parametrize('fault',[None,'no_scope','cached_class','current_gate','original_gate','prepared'])
def test_facade_failure_requires_actual_selected_transition_and_full_gates(failed_readiness,fault):
    c,task,error,worker,selection,lease,W,root=failed_readiness
    c['failed_readiness']=put(task/'readiness.json',dict(publication_ready=True,blockers=[]))
    error.update(message_sha256=M.hashlib.sha256(b'complete current readiness required').hexdigest())
    put(task/'exception.json',error)
    transition=lambda:None;seen=[]
    W.HarborLaneLifecycle=SimpleNamespace(transition=transition)
    ready=SimpleNamespace(ReadyTransitions=SimpleNamespace(transition=transition),_proof=lambda *a:seen.append(a))
    if fault=='cached_class':W.HarborLaneLifecycle=SimpleNamespace(transition=lambda:None)
    elif fault=='current_gate':W.complete_readiness=lambda *a,**k:dict(publication_ready=False,blockers=['numerical_verification'])
    elif fault=='original_gate':c['failed_readiness']=put(task/'readiness.json',dict(publication_ready=False,blockers=['numerical_verification']))
    elif fault=='prepared':(root/'trackers/book-notes-transactions/000019-prepared').mkdir(parents=True)
    if fault:
        with pytest.raises(ValueError):M.stopped_readiness(*failed_readiness,ready=None if fault=='no_scope'else ready)
        assert seen==[]
    else:
        M.stopped_readiness(*failed_readiness,ready=ready)
        assert seen==[({},selection)]


def test_context_restores_selected_runtime_after_failure(monkeypatch):
    from contextlib import contextmanager
    events=[]
    @contextmanager
    def selected():
        events.append('enter')
        try:yield
        finally:events.append('restore')
    def broken(*args,_stack,**kwargs):
        _stack.enter_context(selected());raise ValueError('full owning gate rejected')
    monkeypatch.setattr(M,'_context',broken)
    with pytest.raises(ValueError,match='full owning gate'):M.context({})
    assert events==['enter','restore']


def test_runner_refresh_uses_original_function_and_new_exact_lease_evidence(monkeypatch):
    original=lambda:None;producer=M.namespace(SimpleNamespace(run=original));rows=[{'path':'code','sha256':'old'}];observed=[]
    def select(current,**kw):
        assert current.run is original
        observed.append(list(kw['reader_evidence']));return lambda:tuple(kw['reader_evidence'])
    monkeypatch.setattr(M,'dependencies',lambda _:list(rows))
    refresh=M.runner_refresher(SimpleNamespace(select_run=select),producer,base=None,bound={},p={'helper':{}},producer_code={})
    refresh();first=producer.run;rows.append({'path':'completed-lease','sha256':'new'})
    refresh();assert len(first())==1 and len(producer.run())==2 and observed==[rows[:1],rows]
    previous=producer.run
    monkeypatch.setattr(M,'dependencies',lambda _:(_ for _ in()).throw(ValueError('changed authenticated archive')))
    with pytest.raises(ValueError):refresh()
    assert producer.run is previous


def test_namespace_composes_existing_declared_worker_without_stale_globals():
    original=SimpleNamespace(offset=3);exec('def f(x): return x + offset',original.__dict__)
    previous=M.namespace(original);M.assign(previous,offset=7)
    selected=M.namespace(previous);M.assign(selected,offset=11)
    assert original.f(2)==5 and previous.f(2)==9 and selected.f(2)==13
    assert selected.f.__globals__ is selected.declared_namespace
    assert selected.f.__code__ is original.f.__code__

@pytest.mark.parametrize('fault',[None,'foreign_file','directory'])
def test_retained_worker_step_composes_only_exact_original_aliases(tmp_path,fault):
    file=tmp_path/'worker.py';file.write_text('def _step(worker):\n return step_directory(worker), history_validator(worker)\n')
    directory=lambda w:'historical';validator=lambda w:'original authenticated history'
    scope={'__file__':str(file),'step_directory':directory,'history_validator':validator};exec(compile(file.read_text(),str(file),'exec'),scope)
    original=SimpleNamespace(__file__=str(file),_step=scope['_step'],step_directory=directory)
    selected=M.namespace(original);M.assign(selected,step_directory=lambda w:'adopted checkpoint')
    if fault=='foreign_file':original.__file__=str(tmp_path/'foreign.py');Path(original.__file__).write_text('different')
    elif fault=='directory':scope['step_directory']=lambda w:'foreign'
    if fault:
        with pytest.raises(ValueError):M.retained_worker_scope(original,selected)
    else:
        M.retained_worker_scope(original,selected)
        assert selected._step(None)==('adopted checkpoint','original authenticated history')
        assert original._step(None)==('historical','original authenticated history')
        assert selected._step.__code__ is original._step.__code__
        assert selected._step.__globals__['history_validator']is validator


def test_selected_source_warning_preserves_original_mechanics_and_root(tmp_path,monkeypatch):
    import importlib.util
    original=tmp_path/'old.py'
    original.write_text('from pathlib import Path\ndef check_draft(text,claims,task):\n return {"original":True}\ndef review_warnings(*a,**k):return []\ndef review_faults(*a,**k):return []\ndef review_passed(*a,**k):return False\ndef apply(*a,**k):return None\n')
    spec=importlib.util.spec_from_file_location('old_warning_test',original);V=importlib.util.module_from_spec(spec);spec.loader.exec_module(V)
    selected=tmp_path/'new.py';selected.write_text('def review_warnings(*a,**k):return ["selected"]\ndef review_faults(*a,**k):return []\ndef review_passed(*a,**k):return True\ndef apply(text,claims,task,source_workbook):return check_draft(text,claims,task,source_workbook=source_workbook)\ndef unrelated():raise RuntimeError("must not select")\n')
    reader=tmp_path/'reader.py';reader.write_text('SCHEMA="harbor-dialogue-source-revision-warning/v1"\n')
    import scripts.batch as package
    monkeypatch.setattr(package,'harbor_dialogue_source_revision_warnings',None,raising=False)
    monkeypatch.setitem(M.sys.modules,'scripts.batch.harbor_dialogue_source_revision_warnings',None)
    source=tmp_path/'source';source.write_text('original source')
    mechanics=V.check_draft;proof=M.selected_source_warning(V,M.binding(selected),M.binding(reader))
    assert V.check_draft is mechanics and V.__file__==str(original)and not hasattr(V,'unrelated')
    assert V.apply('',{},tmp_path,M.binding(source))=={'original':True}
    assert proof['symbols']==['review_warnings','review_faults','review_passed','apply']
    assert M.selected_source_warning(V,M.binding(selected),M.binding(reader))==proof
    with pytest.raises(ValueError):V.apply('',{},tmp_path,{'path':str(source),'sha256':'f'*64})
    selected.write_text(selected.read_text()+'\n# a changed dependency\n')
    with pytest.raises(ValueError,match='scope changed'):
        M.selected_source_warning(V,M.binding(selected),M.binding(reader))


@pytest.mark.parametrize('fault',[None,'extra_symbol','foreign_lifecycle'])
def test_retained_lifecycle_rebinds_cached_facade_and_restores(tmp_path,monkeypatch,fault):
    import importlib.util,sys,types
    from scripts.fleet import retained_runtime as RT
    root=tmp_path/'old';file=root/'scripts/batch/harbor_tracker_lifecycle.py';file.parent.mkdir(parents=True)
    file.write_text('def validate_lifecycle(event):\n if event != "old":raise ValueError("old event only")\n return 0\n')
    selected=tmp_path/'new.py';selected.write_text('def validate_lifecycle(event):\n if event not in ("old","authorized"):raise ValueError("invalid event")\n return int(event == "authorized")\n')
    name='scripts.batch.harbor_tracker_lifecycle';spec=importlib.util.spec_from_file_location(name,file);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    monkeypatch.setitem(sys.modules,name,old)
    consumer=types.ModuleType('test_cached_lifecycle_consumer');consumer.lifecycle=old
    exec('def check(event):return lifecycle.validate_lifecycle(event)',consumer.__dict__)
    monkeypatch.setitem(sys.modules,consumer.__name__,consumer)
    helper=M.binding(Path(M.__file__).resolve());runtime=M.binding(Path(RT.__file__).resolve())
    dependency=dict(module_name=name,original=M.binding(file),selected=M.binding(selected))
    profile=dict(schema_version=RT.SCHEMA,runtime=runtime,entrypoint=helper,read_call=dict(function='context',args=[],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],dependencies=[dependency],symbols=[dict(dependency,names=['validate_lifecycle'])])
    if fault=='extra_symbol':profile['symbols'][0]['names'].append('lease_operation')
    if fault=='foreign_lifecycle':profile['dependencies'][0]=dict(dependency,selected=M.binding(file))
    p=dict(helper=helper,retained_context=dict(takeover_profile=put(tmp_path/'takeover.json',dict(shared=dict(lifecycle=M.binding(selected)))),lifecycle_runtime=dict(helper=runtime,profile=put(tmp_path/'profile.json',profile))))
    if fault:
        with pytest.raises(ValueError):
            with M.retained_lifecycle(p,root):pass
    else:
        with M.retained_lifecycle(p,root):
            assert consumer.check('authorized')==1 and consumer.check('old')==0
            with pytest.raises(ValueError):consumer.check('forged')
    assert consumer.lifecycle is old
    with pytest.raises(ValueError):consumer.check('authorized')


def test_operations_create_missing_parent_but_reject_reuse_and_symlinks(tmp_path):
 path=tmp_path/'dispatcher-v2/new';assert M.create_operations(path)==path and path.is_dir()
 with pytest.raises(ValueError):M.create_operations(path)
 link=tmp_path/'link';link.symlink_to(tmp_path/'dispatcher-v2')
 with pytest.raises(ValueError):M.create_operations(link/'other')


@pytest.mark.parametrize('fault',[None,'owner','result','running','other_failure','owning_work','plan'])
def test_pre_tail_stop_requires_exact_worker_and_no_owning_guard(tmp_path,fault):
 old=dict(owner='old',helper=put(tmp_path/'helper.json',{}),source={'sha256':'source'},output=str(tmp_path/'output'),operations=str(tmp_path/'absent-parent/operations'))
 plan=put(tmp_path/'plan.json',old);job=dict(driver=old['helper'],source=old['source'],result=str(Path(old['output'])/'result.json'),inputs=[plan])
 if fault=='plan':job['inputs']=[{}]
 intent=put(tmp_path/'intent.json',dict(owner='foreign'if fault=='owner'else old['owner'],job=put(tmp_path/'job.json',job)))
 log=tmp_path/'worker.log';log.write_text('Traceback\n'+("RuntimeError: unknown"if fault=='other_failure'else "FileNotFoundError: [Errno 2] No such file or directory: "+repr(old['operations']))+'\n')
 outcome=put(tmp_path/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=None if fault=='running'else 1,failure_type=None,result={}if fault=='result'else None,automatic_retry=False,intent=intent,log=M.binding(log)))
 if fault=='owning_work':Path(old['operations']).mkdir(parents=True)
 c=dict(pre_tail_directory_failure=True,failed_outcome=outcome,plan=plan)
 if fault:
  with pytest.raises(ValueError):M.stopped_before_tail(c,old)
 else:M.stopped_before_tail(c,old)

@pytest.mark.parametrize('fault',[None,'foreign_path','changed_bytes','reviewed_path','not_retained'])
def test_retained_semantic_claims_preserves_original_copy(tmp_path,fault):
    current=put(tmp_path/'original/claims.json',{'claims':['exact original']})
    reviewed=put(tmp_path/'repair/claims.json',{'claims':['exact original']})
    args=dict(claims=current,reviewed_claims=reviewed);q=dict(claims=reviewed)
    selection=SimpleNamespace(content=SimpleNamespace(dialogue_claims=current))
    if fault=='foreign_path':args['claims']=put(tmp_path/'foreign/claims.json',{'claims':['exact original']})
    elif fault=='changed_bytes':Path(current['path']).write_text('{}')
    elif fault=='reviewed_path':args['reviewed_claims']=current
    if fault:
        with pytest.raises(ValueError):M.semantic_claims(args,q,selection,retained=fault!='not_retained')
    else:assert M.semantic_claims(args,q,selection,retained=True)
