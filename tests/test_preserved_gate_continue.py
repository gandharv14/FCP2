import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import preserved_gate_continue as P


@pytest.mark.parametrize('fault',[None,'guard','status','checkpoint','descriptor','sidecar','owner','source','plan','parent'])
def test_completed_original_gate_failure_is_not_an_unstarted_child(tmp_path,fault):
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,sort_keys=True));return P.binding(path)
    source=put('source.json',{'raw':1});helper=put('old.py',{});old=dict(workbook='0040',source=source,owner='old',helper=helper,output=str(tmp_path/'old'))
    previous=put('old-plan.json',old);p=dict(previous=previous,source=source)
    descriptor=put('descriptor.json',{});selection={'typed':'actual'}
    blocked=put('blocked.json',dict(status='OWNING_GATE_FAILED',step='disclosure-commit',selection=selection))
    prior=put('prior.json',dict(status='STEP_COMPLETE',next_step='disclosure-commit'))
    intent=put('i-intent.json',dict(action='step',arguments=dict(checkpoint=prior,descriptor=descriptor)))
    result=put('result.json',dict(status='OWNING_GATE_FAILED',checkpoint=blocked,descriptor=descriptor,selection=selection))
    guard=put('guard.json',dict(returncode=0,failure=None,intent=intent))
    outcome=put('outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=guard))
    side=put('side.json',dict(schema_version='original-adoption',plan=previous,helper=helper,intent=intent,kind='instruction',automatic_retry=False))
    job=put('job.json',dict(driver=helper,source=source,command=['python','--plan',previous['path'],'--sha256',previous['sha256']]))
    outer_intent=put('outer-intent.json',dict(owner='old',job=job))
    outer=put('outer.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,intent=outer_intent))
    put('old/intent.json',dict(schema_version='original-adoption',plan=previous,automatic_retry=False))
    profile=dict(failed_outcome=outcome,failed_step=blocked,failed_sidecar=side,original_descriptor=descriptor,worker_outcome=outer)
    # Rebind the changed enclosing evidence honestly, so a rejected case tests
    # the semantic predicate rather than only the generic file hash check.
    if fault=='guard':
        profile['failed_outcome']=put('outcome2.json',dict(P.read(outcome),resource=put('guard2.json',dict(P.read(guard),returncode=1))))
    if fault=='status':profile['failed_outcome']=put('outcome2.json',dict(P.read(outcome),status='RECONCILIATION_REQUIRED'))
    if fault in ('checkpoint','descriptor'):
        changed=dict(P.read(result));changed[fault]=put('foreign.json',{})
        profile['failed_outcome']=put('outcome2.json',dict(P.read(outcome),result=put('result2.json',changed)))
    if fault=='sidecar':profile['failed_sidecar']=put('side2.json',dict(P.read(side),plan=put('foreign-plan.json',{})))
    if fault in ('owner','source','plan'):
        oi=dict(P.read(outer_intent))
        if fault=='owner':oi['owner']='foreign'
        else:
            j=dict(P.read(job))
            if fault=='source':j['source']=put('foreign-source.json',{})
            else:j['command']=['python','--plan','other','--sha256',previous['sha256']]
            oi['job']=put('job2.json',j)
        profile['worker_outcome']=put('outer2.json',dict(P.read(outer),intent=put('outer-intent2.json',oi)))
    if fault=='parent':put('old/intent.json',dict(schema_version='original-adoption',plan=previous,automatic_retry=True))
    S=SimpleNamespace(SCHEMA='original-adoption')
    if fault:
        with pytest.raises(ValueError):P.completed_parent(p,old,S,profile)
    else:assert P.completed_parent(p,old,S,profile)==prior


def test_bootstrap_is_importable_in_isolated_child(tmp_path):
    import subprocess,sys
    run=subprocess.run([sys.executable,'-I','-B',str(Path(P.__file__)),'--help'],capture_output=True,text=True)
    assert run.returncode==0,run.stderr


def test_declared_relocated_prior_is_selected_without_rewriting_original_profile(tmp_path):
    path=tmp_path/'relocated.py';path.write_text('def _prior(row, descriptor):\n    return row, descriptor\n')
    relocated=P.module('preserved_test_relocated',P.binding(path))
    witness=dict(module=P.binding(path),prior=P.function_sha(relocated._prior),decode='d'*64,history='e'*64)
    original=dict(owning=dict(worker='worker',instruction='instruction',active='active',step='step',directory='directory',prior='f'*64))
    selected=P.instruction_profile(dict(checkpoint_reader=witness),original,relocated)
    assert selected==dict(original['owning'],prior=witness['prior'])
    assert original['owning']['prior']=='f'*64
    assert P.instruction_profile({},original,relocated)==original['owning']
    with pytest.raises(ValueError):P.instruction_profile(dict(checkpoint_reader=dict(witness,prior='0'*64)),original,relocated)
    path.write_text('def _prior(row, descriptor):\n    return descriptor, row\n')
    with pytest.raises(ValueError):P.instruction_profile(dict(checkpoint_reader=witness),original,relocated)


@pytest.mark.parametrize('fault',[None,'first-child','task','steps','owner','result','log','tracker','process','parent'])
def test_completed_preparation_is_reused_only_before_exact_unstarted_child(tmp_path,fault):
    def put(name,value,text=False):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value if text else json.dumps(value,sort_keys=True));return P.binding(path)
    home=tmp_path/'old';source=put('source',{});helper=put('helper',{})
    tracker=put('tracker',{})
    profile=put('profile',dict(tracker=tracker))
    old=dict(helper=helper,owner='old-owner',fence=7,output=str(home),source=source,profile=profile)
    old_plan=put('old-plan',old)
    job=put('job',dict(driver=helper,source=source,command=['python','--plan',old_plan['path'],'--sha256',old_plan['sha256']]))
    wi=put('wi',dict(owner=old['owner'],job=job))
    log=put('log','  in run\n    K.instruction_scope\nValueError: original preserved-repair instruction witnesses changed\n',True)
    outer=dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,intent=wi,result=None,log=log)
    process=put('process',dict(job=job,command=['python','--owner',old['owner']]))
    permit=put('old/repair/permit.json',dict(after_repair=tracker))
    parent=put('old/intent.json',dict(schema_version=P.SCHEMA,plan=old_plan,automatic_retry=False))
    if fault=='first-child':(home/'first-step').mkdir()
    if fault=='task':put('old/authoring-job.json',{})
    if fault=='steps':(home/'repair/steps').mkdir()
    if fault=='owner':outer['intent']=put('wrong-wi',dict(owner='foreign',job=job))
    if fault=='result':outer['result']=put('unexpected-result',{})
    if fault=='log':outer['log']=put('wrong-log','TimeoutError',True)
    if fault=='parent':put('old/intent.json',dict(schema_version=P.SCHEMA,plan=old_plan,automatic_retry=True))
    outer_row=put('outer',outer)
    obs=dict(schema_version='fleet-stopped-preserved-repair/v1',process=process,worker_outcome=outer_row,processes=[],tracker=tracker,observed_at_ns=123,unstarted_first_step=True)
    if fault=='tracker':obs['tracker']=put('foreign-tracker',{})
    if fault=='process':obs['processes']=[123]
    cert=put('certificate',dict(schema_version='fleet-preserved-gate-settled-preparation/v1',plan=old_plan,worker_outcome=outer_row,process=process,observation=put('observation',obs)))
    p=dict(old,helper=put('new-helper',{}),owner='new-owner',fence=8,output=str(tmp_path/'new'),settled_repair=cert)
    if fault:
        with pytest.raises(ValueError):P.settled(p)
    else:
        assert P.settled(p)==home
        assert P.binding(home/'repair/permit.json')==permit
        assert not Path(p['output']).exists()


def test_prefix_factory_clones_original_evidence_then_restores_semantic_wrapper(tmp_path):
    helper=tmp_path/'helper.py';helper.write_text('def archived_prefix_reader(C):\n    original=C.evidence_records\n    return lambda value: original(value)\n')
    S=P.module('original_prefix_test',P.binding(helper));old=dict(helper=P.binding(helper))
    original=lambda value:('authenticated original',value)
    wrapper=lambda value:('semantic gate wrapper',value)
    C=SimpleNamespace(evidence_records=wrapper);saved=S.archived_prefix_reader
    with P.archived_prefix_factory(S,old,C,original):
        reader=S.archived_prefix_reader(C)
        assert C.evidence_records is wrapper
        assert reader('historical prefix')==('authenticated original','historical prefix')
        assert C.evidence_records('selection')==('semantic gate wrapper','selection')
    assert S.archived_prefix_reader is saved and C.evidence_records is wrapper
    helper.write_text('changed')
    with pytest.raises(ValueError):
        with P.archived_prefix_factory(S,old,C,original):pass


@pytest.mark.parametrize('fault',[None,'kind','runtime','selection','sidecar','model','outer'])
def test_dialogue_parent_requires_actual_completed_runtime_and_declared_adapter(tmp_path,fault):
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return P.binding(path)
    helper=tmp_path/'parent.py';helper.write_text("SCHEMA='fleet-dialogue-source-revision-adoption/v1'\ndef context(row, **kwargs):\n    return row\ndef dependencies(p):\n    return []\n")
    runner=tmp_path/'runner.py';runner.write_text("SCHEMA='fleet-declared-owning-child-adapter/v1'\n")
    S=P.module('test_dialogue_parent',P.binding(helper));source=put('source',{})
    old=dict(schema_version=S.SCHEMA,helper=P.binding(helper),runner=P.binding(runner),retained_context={},owner='owner4')
    previous=put('previous',old);p=dict(previous=previous,source=source,parent_context=dict(kind='dialogue_source_revision',claims_reader=put('claims',{}),context=P.function_sha(S.context),semantic_review='b'*64))
    descriptor=put('descriptor',{});selection={'binding':'original'}
    blocked=put('blocked',dict(status='OWNING_GATE_FAILED',step='dialogue-commit',selection=selection))
    prior=put('prior',dict(status='STEP_COMPLETE',next_step='dialogue-commit',selection=selection))
    intent=put('op/intent.json',dict(action='step',models=False,commands=True,containers=True,arguments=dict(checkpoint=prior,descriptor=descriptor),code=put('producer',{}),base_code=put('base',{}),timeout_seconds=3300,memory_limit_bytes=8589934592))
    iv=P.read(intent)
    result=put('result',dict(status='OWNING_GATE_FAILED',checkpoint=blocked,descriptor=descriptor,selection=selection))
    guard=put('guard',dict(returncode=0,failure=None,intent=intent))
    outcome=put('outcome',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
    side=dict(schema_version='fleet-declared-owning-child-adapter/v1',adapter=old['runner'],reader_plan=previous,child_wrapper=old['helper'],reader_evidence=[],intent=intent,kind='instruction',producer_code=iv['code'],base_code=iv['base_code'],original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False,timeout_seconds=3300,memory_limit_bytes=8589934592,original_command=['python','-B',iv['code']['path'],'_child',intent['path'],intent['sha256']])
    side['selected_command']=['python','-I','-B',old['helper']['path'],'owning-child','--plan',previous['path'],'--plan-sha256',previous['sha256'],'--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']]
    job=put('job',dict(driver=old['helper'],source=source,command=['python','--plan',previous['path'],'--sha256',previous['sha256']]))
    oi=put('wi',dict(owner='owner4',job=job));outer=put('outer',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,automatic_retry=False,intent=oi))
    profile=dict(failed_outcome=outcome,failed_step=blocked,original_descriptor=descriptor,worker_outcome=outer)
    if fault=='kind':p['parent_context']['kind']='other'
    if fault=='runtime':profile['failed_outcome']=put('wrong-outcome',dict(P.read(outcome),resource=put('wrong-guard',dict(P.read(guard),returncode=1))))
    if fault=='selection':profile['failed_step']=put('wrong-blocked',dict(P.read(blocked),selection={'foreign':True}))
    if fault=='sidecar':side['reader_plan']=put('foreign-plan',{})
    if fault=='model':
        changed=put('model-intent',dict(iv,models=True));profile['failed_outcome']=put('wrong-outcome',dict(P.read(outcome),resource=put('wrong-guard',dict(P.read(guard),intent=changed))))
    if fault=='outer':profile['worker_outcome']=put('wrong-outer',dict(P.read(outer),returncode=0))
    profile['failed_sidecar']=put('op/declared-runtime-adapter.json',side)
    if fault:
        with pytest.raises(ValueError):P.dialogue_parent(p,old,S,profile)
    else:assert P.dialogue_parent(p,old,S,profile)==(prior,intent)


def test_dialogue_repair_aliases_follow_original_private_functions_and_restore(tmp_path,monkeypatch):
    from contextlib import contextmanager
    old=tmp_path/'old.json';old.write_text('{}')
    p=dict(previous=P.binding(old),parent_context={})
    names=('active','step_directory','_step','_operation','check_completed_gate')
    W=SimpleNamespace(**{k:object()for k in names});W.declared_namespace={k:getattr(W,k)for k in names}
    I=SimpleNamespace(_prior=object());I.declared_namespace={'_prior':I._prior}
    original=dict(W.declared_namespace);prior=I._prior
    @contextmanager
    def selected_scope(w,i,**kw):
        for name in names:setattr(w,name,object())
        i._prior=object()
        try:yield
        finally:
            for name,value in original.items():setattr(w,name,value)
            i._prior=prior
    monkeypatch.setattr(P,'instruction_profile',lambda *args:{})
    with P.repair_scope(p,None,SimpleNamespace(instruction_scope=selected_scope),{},(None,I,None,W),{}):
        assert all(W.declared_namespace[k]is getattr(W,k)and getattr(W,k)is not original[k]for k in names)
        assert I.declared_namespace['_prior']is I._prior and I._prior is not prior
    assert W.declared_namespace==original and I.declared_namespace=={'_prior':prior}


def test_dialogue_admission_restores_original_before_repair_scope(monkeypatch):
    from scripts.batch import harbor_preserved_gate_repair as K
    from tests import test_harbor_preserved_gate_repair as fixture
    before={'promotion_failure':{'signature':'retained'},'failure_history':['original']}
    monkeypatch.setattr(fixture,'active_tracker',before)
    worker=object(); W=SimpleNamespace(active=fixture.actual_active)
    assert P.check_dialogue_activity(K,W,worker,'actual-source','live',before)[1]==before
    assert W.active is fixture.actual_active
    # prepare() must be able to install its original checked predicate again.
    with K.initial_active_scope(W,before,worker):
        assert W.active(worker,'actual-source','live')[1]==before
        with pytest.raises(Exception):W.active(worker,'foreign','live')
        with pytest.raises(Exception):W.active(worker,'actual-source','expired')
    assert W.active is fixture.actual_active
    with pytest.raises(Exception):P.check_dialogue_activity(K,W,worker,'actual-source','live',None)


@pytest.mark.parametrize('fault',[None,'tracker','source','effects','log','active','uncertain','fence'])
def test_unstarted_preparation_preserves_exact_stopped_authority(tmp_path,fault):
    def put(name,value,text=False):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value if text else json.dumps(value));return P.binding(path)
    home=tmp_path/'old';source=put('source',{});tracker=put('tracker',{})
    profile=put('profile',dict(tracker=tracker,custody_after=tracker))
    old=dict(helper=put('old-helper',{}),owner='owner5',fence=5,output=str(home),source=source,profile=profile,parent_context={},workbook='0061')
    plan=put('plan',old);job=put('job',dict(driver=old['helper'],source=source,inputs=[plan],result=str(home/'result.json'),run=str(tmp_path/'worker')))
    wi=put('wi',dict(owner=old['owner'],job=job))
    log=put('log','K.prepare\nwith initial_active_scope(W,before,worker)\nValueError: original owning predicate changed\n',True)
    out=put('worker/outcome.json',dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=wi,log=log))
    process=put('process',dict(job=job,pid=999999999,command=['python','--owner',old['owner']]))
    put('old/intent.json',dict(schema_version=P.SCHEMA,plan=plan,automatic_retry=False))
    put('old/repair/intent.json',dict(schema_version='harbor-preserved-owning-gate-repair/v1',profile=profile,tracker=tracker,automatic_retry=False))
    hosts=[put('host'+str(i),dict(status='OBSERVED',matches=[]))for i in range(8)]
    central=put('central',dict(workbooks=[dict(state='hold',owner=old['owner'],fence=5)],operations=[dict(state='confirmed')]))
    census=put('census',dict(workbook='0061',read_only=True,local=put('local',dict(matches=[])),hosts=hosts,central=central))
    cert=put('cert',dict(schema_version='fleet-preserved-gate-unstarted-preparation/v1',plan=plan,worker_outcome=out,process=process,census=census))
    p=dict(old,helper=put('new-helper',{}),owner='owner6',fence=6,output=str(tmp_path/'new'),unstarted_preparation=cert)
    if fault=='source':p['source']=put('other',{})
    if fault=='tracker':put('tracker',{'mutation':True})
    if fault=='effects':put('old/repair/permit.json',{})
    if fault=='log':put('log','different failure',True)
    if fault=='active':put('host0',dict(status='OBSERVED',matches=[123]))
    if fault=='uncertain':put('central',dict(workbooks=[dict(state='hold',owner='owner5',fence=5)],operations=[dict(state='active')]))
    if fault=='fence':p['fence']=7
    if fault:
        with pytest.raises(ValueError):P.unstarted_preparation(p)
    else:assert P.unstarted_preparation(p)==home


@pytest.mark.parametrize('modern,seconds,fault',[(False,3300,None),(True,1350,None),(False,3300,'timeout'),(False,3300,'memory'),(True,1350,'base')])
def test_original_legacy_and_phase_specific_guards_keep_exact_limits(tmp_path,modern,seconds,fault):
    def put(name,v):
        p=tmp_path/name;p.write_text(json.dumps(v));return P.binding(p)
    instruction=put('instruction',{});base=put('base',{});I=SimpleNamespace(__file__=instruction['path']);C=SimpleNamespace(__file__=base['path'],SECONDS=3300)
    if modern:I.guard_seconds=lambda action,args:1350
    value=dict(code=instruction,base_code=base,action='step',arguments={'checkpoint':{}},memory_limit_bytes=8589934592,timeout_seconds=seconds)
    if fault=='timeout':value['timeout_seconds']=6600
    if fault=='memory':value['memory_limit_bytes']=17179869184
    if fault=='base':value['base_code']=put('foreign',{})
    row=put('intent',value)
    if fault:
        with pytest.raises(ValueError):P.instruction_guard_seconds(I,C,row)
    else:assert P.instruction_guard_seconds(I,C,row)==seconds


@pytest.mark.parametrize('fault',[None,'message','launched','log','models','descriptor'])
def test_legacy_guard_error_cannot_authorize_a_started_child(tmp_path,fault):
    def put(name,value,text=False):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value if text else json.dumps(value));return P.binding(p)
    descriptor=put('repair/descriptor.json',{});checkpoint=put('repair/result.json',{})
    intent=put('first-step/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint),models=fault=='models',commands=False,containers=False,timeout_seconds=3300,memory_limit_bytes=8589934592))
    if fault=='descriptor':intent=put('first-step/intent.json',dict(P.read(intent),arguments=dict(descriptor=put('foreign',{}),checkpoint=checkpoint)))
    error="'types.SimpleNamespace' object has no attribute 'guard_seconds'"
    guard=put('first-step/resource.json',dict(intent=intent,metrics={},returncode=0 if fault=='launched'else None,failure=dict(type='AttributeError',message_sha256=P.hashlib.sha256((error if fault!='message'else'other').encode()).hexdigest()),log=put('first-step/step.log','child ran'if fault=='log'else'',True)))
    out=put('first-step/outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,automatic_retry=False,resource=guard))
    if fault:
        with pytest.raises(ValueError):P.stopped_legacy_guard(tmp_path,dict(failed_outcome=out))
    else:P.stopped_legacy_guard(tmp_path,dict(failed_outcome=out))


def source_activity_original(worker,selection,lease):
    require(worker.custody,'lease rejected')
    require(worker.environment,'environment lane must pass before instructions')
    require(worker.stage,'stage changed')
    return 'original checks passed'

@pytest.mark.parametrize('fault',[None,'source','custody','stage','failed_gate','omitted_gate','permit'])
def test_source_activity_reexecutes_original_prefix_without_mutating_tracker(tmp_path,monkeypatch,fault):
    from scripts.batch import harbor_completed_lanes as completed
    global require
    require=P.require
    source={'path':'/original','sha256':'a'*64};failure={'lane':'source'}
    before=dict(revision=46,promotion_failure=failure,bindings={'original_raw_source':source})
    tracker=tmp_path/'tracker.json';tracker.write_text(json.dumps(before));raw=tracker.read_bytes()
    prefix=tmp_path/'scripts/batch/harbor_instruction_repair.py';prefix.parent.mkdir(parents=True);prefix.write_text('# original')
    profile=dict(tracker=P.binding(tracker),source_context={'prefix':P.binding(prefix)})
    value=dict(failure_result={'gate':'source'},profile=profile,worker_root=str(tmp_path),worker_run=str(tmp_path/'run'),source=source)
    worker=SimpleNamespace(root=tmp_path,run=tmp_path/'run',context=object(),custody=fault!='custody',environment=False,stage=fault!='stage')
    selection=SimpleNamespace(content=SimpleNamespace(bindings={'original_raw_source':dict(source,sha256='b'*64)if fault=='source'else source}))
    lease=SimpleNamespace(sha256=P.binding(tracker)['sha256'],revision=46);calls=[]
    expected=completed.STEPS[:[x[0]for x in completed.STEPS].index('dialogue')]
    def prefix_check(*args,**kwargs):
        calls.append('original prefix')
        rows=[dict(kind=kind,result=dict(passed=fault!='failed_gate',lane=lane))for kind,lane in expected]
        return rows[:-1]if fault=='omitted_gate'else rows
    def validate(*args):
        if fault=='permit'and calls:raise ValueError('changed permit')
        calls.append('permit');return value,before
    K=SimpleNamespace(validate=validate,intended_gate=lambda *a:'dialogue',_module=lambda *a:SimpleNamespace(prefix=prefix_check))
    W=SimpleNamespace(active=source_activity_original,read=lambda row:profile if row is profile else P.read(row),record=P.binding,check_completed_gate=object())
    with P.source_repair_activity(W,K,profile,{'permit':1}):
        if fault:
            with pytest.raises(ValueError):W.active(worker,selection,lease)
        else:
            assert W.active(worker,selection,lease)=='original checks passed'
            assert W.active(worker,selection,lease)=='original checks passed'
            assert calls.count('original prefix')==2
    assert W.active is source_activity_original and tracker.read_bytes()==raw
