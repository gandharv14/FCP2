import dataclasses
import fcntl
import json
import os
import subprocess
from pathlib import Path
import pytest
import xl_variable_source_audit as audit
from scripts import harbor_campaign as C
from scripts.batch.harbor_admission import read,record
from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import selected
from test_harbor_environment_worker import worker,responses
from scripts.batch.harbor_transition_codec import encode as content_encode


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);return C.keep(path,value)


@pytest.fixture
def intake(selected,tmp_path):
    b,_,home=selected;b=dataclasses.replace(b,model_policy=C.bootstrap.Policy(audit.DEFAULT_PROJECT_ID))
    taxonomy=write(home/'taxonomy.json',{})
    return C.Intake(b,C.executor.Campaign(home/'calls',home/'models',1),taxonomy,home/'environment','harbor-orchestrator:test'),C.Controls(home/'actual-workbook-slot.lock'),home


@pytest.fixture
def inprocess(monkeypatch):
    seen=[]
    def guarded(command,**kwargs):
        assert kwargs['memory_limit_bytes']==8*1024**3 and kwargs['timeout_seconds']==3300
        assert len(kwargs['guardian_lock_fds'])==1
        fd=kwargs['guardian_lock_fds'][0];assert os.fstat(fd)
        seen.append((command,kwargs))
        C.child(dict(path=command[-2],sha256=command[-1]));kwargs['metrics']['peak_rss_bytes']=1234;return 0
    monkeypatch.setattr(C,'_run_in_process_group',guarded);return seen


def start(intake):
    selection,controls,home=intake;root=write(home/'intake.json',C.encode(selection))
    outcome=C.run('admit',{'intake':root},controls,home/'admit-step')
    return selection,controls,home,read(read(outcome)['result'])


def test_guarded_admission_then_one_owned_source_step(intake,inprocess):
    selection,controls,home,result=start(intake)
    assert result['status']=='ADMITTED' and result['publication_ready'] is False
    descriptor=result['descriptor'];s,lease,actual=C.selected(descriptor)
    assert actual==controls and s.normalization_budget.ordinal==1 and s.normalization_budget.limit==2
    prior=record(home/'admit-step/result.json')
    before=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    outcome=C.run('step',dict(descriptor=descriptor,checkpoint=prior,captures=None),controls,home/'source-step')
    value=read(read(outcome)['result'])
    assert value['status']=='STEP_COMPLETE' and value['step']=='source'
    after=read(record(s.bootstrap.policy.tracker_root/'0001.json'))
    assert after['revision']==before['revision']+1 and after['lane_state']['harbor-source-warden']['state']=='passed'
    assert after['lane_state']['harbor-research-auditor']['attempt']==0
    assert len(inprocess)==2 and read(outcome)['status']=='COMPLETED_OPERATION'


@pytest.mark.parametrize('change',['missing_sha','forged_type','extra_field','duplicate_key','controls'])
def test_exact_descriptor_inputs_reject(intake,inprocess,change):
    selection,controls,home=intake
    if change=='controls':
        _,controls,home,result=start(intake)
        with pytest.raises(ValueError,match='controls changed'):
            C.run('step',dict(descriptor=result['descriptor'],checkpoint=record(home/'admit-step/result.json'),captures=None),C.Controls(home/'other.lock'),home/'bad')
        return
    encoded=C.encode(selection)
    if change=='forged_type':encoded['type']='arbitrary.module.Call'
    if change=='extra_field':encoded['value']['unexpected']=C.encode(True)
    if change=='duplicate_key':encoded={'type':'dict','value':[[C.encode('x'),C.encode(1)],[C.encode('x'),C.encode(2)]]}
    if change=='missing_sha':
        root=write(home/'intake.json',encoded)
        with pytest.raises(ValueError):C.run('admit',{'intake':{'path':root['path']}},controls,home/'bad')
    else:
        with pytest.raises(ValueError):C.decode(encoded)


def test_unknown_child_and_existing_attempt_never_replay(intake,monkeypatch):
    selection,controls,home=intake;binding=write(home/'intake.json',C.encode(selection));calls=[]
    def killed(*a,**k):calls.append(1);k['metrics']['limit_reason']='wall_time';raise C.ConversionTimeout('fixture child killed')
    monkeypatch.setattr(C,'_run_in_process_group',killed)
    result=C.run('admit',dict(intake=binding),controls,home/'attempt');outcome=read(result)
    assert outcome['status']=='RECONCILIATION_REQUIRED' and outcome['terminal_verdict'] is None and not outcome['source_invalidity_claimed']
    assert read(outcome['resource'])['metrics']['limit_reason']=='wall_time'
    with pytest.raises(ValueError,match='attempt exists'):C.run('admit',dict(intake=binding),controls,home/'attempt')
    assert len(calls)==1


def test_shared_slot_prevents_overlapping_admission(intake,inprocess):
    selection,controls,home=intake;binding=write(home/'intake.json',C.encode(selection))
    with controls.slot_lock.open('a+b') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):C.run('admit',dict(intake=binding),controls,home/'attempt')
    assert not (home/'attempt').exists() and not inprocess


@pytest.mark.parametrize('status',['UNRESOLVED','OWNING_GATE_FAILED','NAT_REPAIR_REQUIRED','ENVIRONMENT_READY'])
def test_no_implicit_retry_or_work_after_boundary(intake,inprocess,status):
    _,controls,home,result=start(intake);prior={**result,'status':status};bound=write(home/'other-result.json',prior)
    with pytest.raises(ValueError,match='root reconciliation'):
        C._prior(bound,result['descriptor'])


def test_legacy_pending_import_revalidates_exact_existing_worker(worker,tmp_path,inprocess):
    s,lease=worker
    result=C.worker.advance(s,lease)
    assert result['status']=='PENDING_MODEL'
    legacy=write(tmp_path/'legacy.json',C.bootstrap.serial({'worker':dataclasses.asdict(s),'lease':dataclasses.asdict(lease)}))
    checkpoint=write(tmp_path/'legacy-result.json',{**result,'lease':dataclasses.asdict(result['lease']),'selection':content_encode(result['selection'])})
    controls=C.Controls(tmp_path/'actual.lock')
    before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    outcome=C.run('import',dict(legacy=legacy,checkpoint=checkpoint),controls,tmp_path/'import-step')
    imported=read(read(outcome)['result']);actual,_,_=C.selected(imported['descriptor'])
    assert actual==s and imported['status']=='IMPORTED'
    assert (s.bootstrap.policy.tracker_root/'0001.json').read_bytes()==before
    assert not s.campaign.root.exists()


def test_canonical_codec_roundtrip(intake):
    value=intake[0];assert C.decode(C.encode(value))==value
    assert C.encode(C.decode(json.loads(json.dumps(C.encode(value)))))==C.encode(value)


@pytest.mark.parametrize('free',[8*1024**3-1,8*1024**3])
def test_campaign_dependency_enforces_exact_disk_reserve(worker,tmp_path,monkeypatch,free):
    from types import SimpleNamespace
    s,_=worker
    (tmp_path/'reserve').mkdir()
    monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=free))
    if free<C.MEMORY:
        with pytest.raises(ValueError,match='8-GiB free-space reserve required'):
            C._dependency(s,tmp_path/'reserve')
    else:
        proof=C._dependency(s,tmp_path/'reserve')
        C.admission.validate_dependency(proof,s.bootstrap.policy.dependencies,C.ROOT)


def test_dispatch_requires_selected_env_file_before_guard(intake,inprocess):
    _,controls,home,result=start(intake);count=len(inprocess)
    with pytest.raises(ValueError,match='environment file'):
        C.run('step',dict(descriptor=result['descriptor'],checkpoint=record(home/'admit-step/result.json'),captures=None),controls,home/'bad',dispatch=True)
    assert len(inprocess)==count and not (home/'bad').exists()


def test_lease_renewal_preserves_token_and_has_actual_cas_evidence(worker,tmp_path,monkeypatch):
    s,lease=worker;directory=tmp_path/'renewal';directory.mkdir()
    actual=C.time.time_ns();monkeypatch.setattr(C.time,'time_ns',lambda:actual+100*10**9)
    result=C._lease(s,lease,directory)
    assert result.token==lease.token and result.owner==lease.owner and result.revision==lease.revision+1
    saved=read(record(directory/'lease-renewal.json'))
    assert C.decode(saved['previous'])==lease and C.decode(saved['lease'])==result
    with pytest.raises(ValueError):C._lease(s,lease,directory)


@pytest.mark.parametrize('what',['checkpoint','descriptor_code','budget'])
def test_stale_checkpoint_code_or_budget_never_replays(intake,inprocess,what):
    _,controls,home,result=start(intake);descriptor=result['descriptor'];checkpoint=record(home/'admit-step/result.json')
    if what=='checkpoint':checkpoint={**checkpoint,'sha256':'0'*64}
    elif what=='descriptor_code':
        value=read(descriptor);value['code']['sha256']='0'*64;descriptor=write(home/'bad-descriptor.json',value)
    else:
        s,lease,c=C.selected(descriptor);s=dataclasses.replace(s,normalization_budget=dataclasses.replace(s.normalization_budget,ordinal=2))
        descriptor=write(home/'changed-descriptor.json',C._descriptor(s,lease,c,()))
        prior={**read(checkpoint),'descriptor':descriptor};checkpoint=write(home/'changed-result.json',prior)
    count=len(inprocess)
    if what=='budget':
        outcome=C.run('step',dict(descriptor=descriptor,checkpoint=checkpoint,captures=None),controls,home/'bad')
        assert read(outcome)['status']=='RECONCILIATION_REQUIRED'
    else:
        with pytest.raises(ValueError):C.run('step',dict(descriptor=descriptor,checkpoint=checkpoint,captures=None),controls,home/'bad')
        assert len(inprocess)==count


def test_guard_records_interrupted_real_harmless_child(intake,monkeypatch):
    selection,controls,home=intake;root=write(home/'intake.json',C.encode(selection))
    original=C._run_in_process_group
    def short_fixture(command,**kw):
        assert kw['timeout_seconds']==3300 and kw['memory_limit_bytes']==8*1024**3
        # Exercise real process-group termination with a harmless sleeping child;
        # no workbook or model code is launched. Resource guard itself is owned
        # and independently regression-covered by test_process_parent_death.py.
        return original([C.sys.executable,'-c','import time;time.sleep(20)'],**{**kw,'timeout_seconds':.15,'grace_seconds':.1,'memory_limit_bytes':None})
    monkeypatch.setattr(C,'_run_in_process_group',short_fixture)
    outcome=C.run('admit',dict(intake=root),controls,home/'interrupted')
    value=read(outcome);assert value['status']=='RECONCILIATION_REQUIRED'
    assert read(value['resource'])['metrics']['limit_reason']=='wall_time'
    assert not selection.bootstrap.run.exists()


def test_imported_pending_worker_dispatches_one_exact_owned_research_call(worker,tmp_path,inprocess,monkeypatch):
    import xl_task_build
    s,lease=worker;seen=responses(monkeypatch)
    pending=C.worker.advance(s,lease);assert pending['status']=='PENDING_MODEL'
    legacy=write(tmp_path/'legacy.json',C.bootstrap.serial({'worker':dataclasses.asdict(s),'lease':dataclasses.asdict(lease)}))
    prior=write(tmp_path/'legacy-result.json',{**pending,'lease':dataclasses.asdict(pending['lease']),'selection':content_encode(pending['selection'])})
    env=tmp_path/'selected.env';env.write_text('test fixture credential is injected, not real\n')
    monkeypatch.setattr(xl_task_build,'read_env_key',lambda path:'synthetic-test-token')
    controls=C.Controls(tmp_path/'actual.lock',env)
    imported=read(read(C.run('import',dict(legacy=legacy,checkpoint=prior),controls,tmp_path/'imported'))['result'])
    checkpoint=record(tmp_path/'imported/result.json')
    result=read(read(C.run('step',dict(descriptor=imported['descriptor'],checkpoint=checkpoint,captures=None),controls,tmp_path/'research',dispatch=True))['result'])
    assert result['status']=='STEP_COMPLETE' and result['step']=='research' and len(seen)==1
    assert C.decode(result['lease']).revision==lease.revision+2
    renewal=read(record(tmp_path/'research/lease-renewal.json'))
    assert C.decode(renewal['previous'])==lease and C.decode(renewal['lease']).revision==lease.revision+1
    assert C.content_codec.decode(result['selection']).content.bindings['curation']==read(s.prepared)['validated']['bindings']['curation']
    # Re-selecting the old pending root checkpoint cannot replay the model: its
    # exact current tracker CAS is now stale.
    outcome=C.run('step',dict(descriptor=imported['descriptor'],checkpoint=checkpoint,captures=None),controls,tmp_path/'stale',dispatch=True)
    assert read(outcome)['status']=='RECONCILIATION_REQUIRED' and len(seen)==1


def test_root_selected_inherited_audit_record_is_preserved_not_reset(intake,inprocess):
    selection,controls,home=intake
    # Admission stores the explicit original evidence; the auditor later owns
    # semantic validation of this contract before any dispatch.
    inherited=write(home/'selected-prior-budget.json',{'schema_version':'audit-inherited-budget/v1','workbook':'0001','attempts':[]})
    selection=dataclasses.replace(selection,inherited_audit_budget=inherited)
    _,_,_,result=start((selection,controls,home))
    s,_,_=C.selected(result['descriptor']);assert s.inherited_audit_budget==inherited
    Path(inherited['path']).write_text('{}')
    with pytest.raises(ValueError):C.executor.checked_bytes(s.inherited_audit_budget)


def test_legacy_completed_source_step_import_uses_actual_lane(intake,inprocess):
    _,controls,home,result=start(intake)
    out=C.run('step',dict(descriptor=result['descriptor'],checkpoint=record(home/'admit-step/result.json'),captures=None),controls,home/'source-step')
    completed=read(read(out)['result']);s,_,_=C.selected(result['descriptor']);lease=C.decode(completed['lease'])
    legacy=write(home/'legacy.json',C.bootstrap.serial({'worker':dataclasses.asdict(s),'lease':dataclasses.asdict(lease)}))
    prior=write(home/'legacy-result.json',{**completed,'lease':dataclasses.asdict(lease)})
    before=(s.bootstrap.policy.tracker_root/'0001.json').read_bytes()
    result=read(read(C.run('import',dict(legacy=legacy,checkpoint=prior),controls,home/'imported'))['result'])
    assert result['status']=='IMPORTED' and (s.bootstrap.policy.tracker_root/'0001.json').read_bytes()==before


def test_cli_requires_explicit_checkpoint_hashes(tmp_path):
    with pytest.raises(ValueError,match='paired'):
        C.main(['step','--descriptor',str(tmp_path/'descriptor.json'),'--journal',str(tmp_path/'journal'),'--slot-lock',str(tmp_path/'actual.lock')])


from test_harbor_profile_producer import environment as profile_environment


@pytest.mark.parametrize('tampered',[False,True])
def test_nonempty_capture_binding_reaches_owning_profile_preparation(worker,profile_environment,inprocess,monkeypatch,tmp_path,tampered):
    s,lease=worker;fixture,_=profile_environment;input_binding,_,campaign=fixture()
    inputs=read(input_binding);capture_binding=inputs['captures'];assert read(capture_binding)['sources']
    s=dataclasses.replace(s,campaign=campaign)
    controls=C.Controls(tmp_path/'profile-workbook.lock')
    descriptor=write(tmp_path/'profile-descriptor.json',C._descriptor(s,lease,controls,(s.prepared,)))
    prior=write(tmp_path/'profile-prior.json',dict(status='ADMITTED',descriptor=descriptor,lease=C.encode(lease),selection=None,evidence=[s.prepared]))
    content=C.worker._content(s,dict(normalized_phase1=inputs['phase1'],variable_source_inventory=inputs['inventory'],original_raw_source=inputs['original_source']),())
    observed=[]
    def profile_boundary(actual,active_lease,*,dispatch,credential,captures):
        observed.append(captures)
        # Exercise the actual downstream worker/producer API, not a success
        # callback: prepare one nonempty public-source job, with dispatch off.
        result=C.worker._profiles(actual,content,captures,dispatch=False,credential=None)
        assert result['status']=='PENDING_PRODUCER'
        return C.worker._status(actual,'PENDING_MODEL',active_lease,step='source_profile',selection=C.worker._selection(content),evidence=[record(actual.run/'profiles/plan.json')])
    monkeypatch.setattr(C.worker,'advance',profile_boundary)
    if tampered:
        Path(capture_binding['path']).write_bytes(b'{}')
        with pytest.raises(ValueError):
            C.run('step',dict(descriptor=descriptor,checkpoint=prior,captures=capture_binding),controls,tmp_path/'profile-step')
        assert not observed and not (s.run/'profile-input.json').exists()
    else:
        outcome=C.run('step',dict(descriptor=descriptor,checkpoint=prior,captures=capture_binding),controls,tmp_path/'profile-step')
        result=read(read(outcome)['result']);assert result['status']=='PENDING_MODEL'
        assert observed==[capture_binding]
        saved=read(record(s.run/'profile-input.json'));assert saved['captures']==capture_binding
        owning=C.worker.profiles.input_context(record(s.run/'profile-input.json'))
        assert len(owning[-1])==1 and len(read(record(s.run/'profiles/plan.json'))['jobs'])==1
        assert not campaign.root.exists()
