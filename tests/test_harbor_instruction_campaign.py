from dataclasses import replace,asdict
from pathlib import Path
import json,time,fcntl,os
import pytest
from scripts import harbor_instruction_campaign as C
from scripts.batch.harbor_admission import read,record
from test_instruction_worker import case,instruction_setup
from test_harbor_environment_worker import worker,selected,setup

@pytest.fixture
def inprocess(monkeypatch):
    from types import SimpleNamespace
    # Model installation capacity for all in-process fixture entry points,
    # including imported retry/handoff scenarios. Low-disk tests override it.
    monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=8*1024**3))
    calls=[]
    def guarded(command,**kwargs):
        intent=read(record(Path(command[-2])))
        prior=read(intent['arguments']['checkpoint']) if intent['action']=='step' else {}
        assert kwargs['timeout_seconds']==(1350 if prior.get('next_step')=='final-runtime' else 3300) and kwargs['memory_limit_bytes']==8*1024**3
        assert len(kwargs['guardian_lock_fds'])==1 and os.fstat(kwargs['guardian_lock_fds'][0])
        calls.append(command);C.child(dict(path=command[-2],sha256=command[-1]));return 0
    monkeypatch.setattr(C.base,'_run_in_process_group',guarded);return calls


def bound(path,value):return C.base.keep(path,value)

@pytest.fixture
def instruction(case,tmp_path,monkeypatch):
    w,s,lease,t,respond,calls=case
    active=C.worker.active
    def controlled(*a):
        facade,before,tracker=active(*a)
        before['leases']['task_agent']={'expires_at':time.time_ns()+3600*10**9}
        return facade,before,tracker
    monkeypatch.setattr(C.worker,'active',controlled)
    controls=C.base.Controls(tmp_path/'shared-workbook.lock')
    recipe=C.base.admission.DependencyRecipe(record(Path(C.base.sys.executable).resolve()),(),(),False,4,2)
    # Only external installation probes are fixture boundaries. Worker checkpoint
    # sequencing, immutable evidence and guard capability forwarding are real.
    monkeypatch.setattr(C,'dependency',lambda recipe,directory:bound(directory/'dependency.json',{'fixture':True}))
    descriptor=bound(tmp_path/'descriptor.json',dict(schema_version=C.SCHEMA,worker=C.worker_encode(w),controls=C.base.encode(controls),dependencies=C.base.encode(recipe),origin=[],code=record(C.__file__),initial=dict(selection=C.codec.encode(s),lease=C.base.encode(lease))))
    initial=bound(tmp_path/'ready.json',C._result('INSTRUCTION_READY',descriptor,s,lease,next_step=C.worker.STEPS[0]))
    return w,s,lease,controls,descriptor,initial,tmp_path


def execute(fixture,index,checkpoint=None,**caps):
    w,s,l,controls,d,initial,home=fixture
    outcome=C.run('step',dict(descriptor=d,checkpoint=checkpoint or initial),controls,home/('attempt-%02d'%index),**caps)
    return read(outcome),read(read(outcome)['result']) if read(outcome).get('result') else None


def test_canonical_steps_and_pending_capabilities_use_exact_worker_evidence(instruction,inprocess,monkeypatch):
    options=[];states=iter(['COMMANDS_REQUIRED',None,None])
    def operation(w,s,l,phase,directory,**kw):options.append((phase,kw));return s,l,next(states)
    monkeypatch.setattr(C.worker,'_operation',operation)
    a,result=execute(instruction,0);assert result['status']=='COMMANDS_REQUIRED'
    b,result=execute(instruction,1,a['result'],commands=True);assert result['status']=='STEP_COMPLETE'
    c,result=execute(instruction,2,b['result']);assert result['status']=='STEP_COMPLETE'
    assert [x[0] for x in options]==['disclosure-build','disclosure-build','disclosure-review']
    assert options[0][1]['commands'] is False and options[1][1]['commands'] is True
    assert all(not x[1]['models'] and not x[1]['containers'] for x in options)
    assert result['publication_performed'] is False and len(inprocess)==3


def test_all_fixed_steps_stop_at_enqueue_and_never_publish(instruction,inprocess,monkeypatch):
    seen=[]
    def op(w,s,l,phase,directory,**kw):seen.append(phase);return s,l,None
    monkeypatch.setattr(C.worker,'_operation',op);previous=None
    for i in range(len(C.worker.STEPS)):
        outcome,result=execute(instruction,i,previous);previous=outcome['result']
    assert result['status']=='PUBLICATION_READY' and seen==list(C.worker.STEPS)
    assert not result['publication_performed']
    with pytest.raises(ValueError,match='root reconciliation'):C._prior(previous,instruction[4])


@pytest.mark.parametrize('status',['NAT_REPAIR_REQUIRED','RECONCILIATION_REQUIRED','MODEL_RECONCILIATION_REQUIRED','PUBLICATION_READY','LEASE_RENEWAL_REQUIRED'])
def test_failure_and_completed_boundary_never_implicitly_retry(instruction,status):
    w,s,l,c,d,initial,home=instruction
    binding=bound(home/'failure.json',{**read(initial),'status':status})
    with pytest.raises(ValueError,match='root reconciliation'):C._prior(binding,d)


def test_short_lease_returns_before_dependency_or_work_and_preserves_checkpoint(instruction,inprocess,monkeypatch):
    prior=C.worker.active
    def expiring(*a):
        facade,before,tracker=prior(*a);before['leases']['task_agent']['expires_at']=time.time_ns()+3329*10**9;return facade,before,tracker
    monkeypatch.setattr(C.worker,'active',expiring)
    monkeypatch.setattr(C,'dependency',lambda *a:pytest.fail('no probe/work after short lease'))
    monkeypatch.setattr(C.worker,'step',lambda *a,**k:pytest.fail('no instruction work consumed'))
    outcome,result=execute(instruction,0)
    assert result['status']=='LEASE_RENEWAL_REQUIRED' and result['lease']==read(instruction[5])['lease']
    assert result['checkpoint'] is None and not instruction[0].run.exists()


def test_low_disk_stops_before_instruction_guard_or_attempt(instruction,inprocess,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(C.shutil,'disk_usage',lambda _:SimpleNamespace(free=8*1024**3-1))
    with pytest.raises(ValueError,match='8-GiB free-space reserve required'):
        execute(instruction,0)
    assert inprocess==[]
    assert not (instruction[-1]/'attempt-00').exists()


@pytest.mark.parametrize('change',['descriptor','checkpoint','lease','code','selection'])
def test_exact_provenance_rejects_before_second_operation(instruction,inprocess,monkeypatch,change):
    monkeypatch.setattr(C.worker,'_operation',lambda w,s,l,*a,**k:(s,l,None))
    outcome,result=execute(instruction,0);w,s,l,c,d,initial,home=instruction;checkpoint=outcome['result']
    value=read(checkpoint)
    if change=='descriptor':value['descriptor']={**d,'sha256':'0'*64}
    if change=='checkpoint':value['checkpoint']={**value['checkpoint'],'sha256':'0'*64}
    if change=='lease':value['lease']=C.base.encode(replace(l,revision=l.revision+1))
    if change=='selection':value['selection']=C.codec.encode(replace(s,content=replace(s.content,stage_sha256='0'*64)))
    if change=='code':
        raw=read(d);raw['code']['sha256']='0'*64;d=bound(home/'changed-descriptor.json',raw);value['descriptor']=d
    checkpoint=bound(home/'changed-result.json',value)
    if change=='code':
        with pytest.raises(ValueError,match='code changed'):C.run('step',dict(descriptor=d,checkpoint=checkpoint),c,home/'bad')
    else:
        result=C.run('step',dict(descriptor=d,checkpoint=checkpoint),c,home/'bad')
        assert read(result)['status']=='RECONCILIATION_REQUIRED'


def test_missing_model_permission_fails_before_guard(instruction,inprocess):
    with pytest.raises(ValueError,match='credential file'):execute(instruction,0,models=True)
    assert not inprocess


def test_shared_slot_and_unknown_guard_never_replay(instruction,inprocess,monkeypatch):
    w,s,l,c,d,initial,home=instruction
    with c.slot_lock.open('a+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):execute(instruction,0)
    assert not (home/'attempt-00').exists()
    def interrupted(*a,**k):raise C.base.ConversionTimeout('interrupted')
    monkeypatch.setattr(C.base,'_run_in_process_group',interrupted)
    outcome,_=execute(instruction,0)
    assert outcome['status']=='RECONCILIATION_REQUIRED' and outcome['terminal_verdict'] is None
    with pytest.raises(ValueError,match='attempt exists'):execute(instruction,0)


def test_real_environment_handoff_starts_with_same_owner_lease_and_owning_gate(worker,inprocess,monkeypatch,tmp_path):
    from test_harbor_environment_worker import test_real_worker_through_phase2_uses_independent_review_and_generic_profile as build_fixture
    build_fixture(worker,monkeypatch)
    s,oldlease=worker;tracker=record(s.bootstrap.policy.tracker_root/'0001.json');before=read(tracker)
    lease=replace(oldlease,revision=before['revision'],sha256=tracker['sha256'])
    selection=C.base.worker._read_checkpoint(s,'environment');controls=C.base.Controls(tmp_path/'actual.lock')
    descriptor=bound(tmp_path/'environment-descriptor.json',C.base._descriptor(s,lease,controls,()))
    result=bound(tmp_path/'environment-result.json',dict(status='ENVIRONMENT_READY',descriptor=descriptor,selection=C.codec.encode(selection),lease=C.base.encode(lease),evidence=[]))
    outcome=C.run('start',dict(environment_descriptor=descriptor,checkpoint=result,worker_run=str(tmp_path/'instructions')),controls,tmp_path/'start')
    value=read(read(outcome)['result']);assert value['status']=='INSTRUCTION_READY',value
    renewed=C.base.decode(value['lease']);assert renewed.owner==oldlease.owner and renewed.token==oldlease.token
    assert C.selected(value['descriptor'])[0].context.tracker_root==s.bootstrap.policy.tracker_root
    assert not (tmp_path/'instructions').exists() and value['checkpoint'] is None

from test_nat_worker_retry import failed,modules
from test_harbor_instruction_producer import setup as setup_nat

@pytest.fixture
def retry_case(tmp_path,monkeypatch):
    # Compose existing hermetic strict-CAS/executor/frozen-writer fixture. The
    # fixture uses a synthetic response, never actual model or network calls.
    from test_harbor_instruction_producer import setup as make_setup
    from test_nat_worker_retry import failed as make_failed,modules as make_modules
    return make_failed.__wrapped__(make_setup.__wrapped__(tmp_path,monkeypatch),make_modules.__wrapped__(),monkeypatch)


@pytest.mark.parametrize('changed',[False,True])
def test_explicit_existing_nat_retry_keeps_spent_round_and_exact_scope(retry_case,tmp_path,inprocess,monkeypatch,changed):
    w,r,old,s,l,blocked,t,respond,calls,*_=retry_case
    new,selection,lease,completion=r.authorize(old,s,l,blocked=blocked)
    controls=C.base.Controls(tmp_path/'actual.lock')
    recipe=C.base.admission.DependencyRecipe(record(Path(C.base.sys.executable).resolve()),(),(),False,4,2)
    descriptor=bound(tmp_path/'instruction-descriptor.json',dict(schema_version=C.SCHEMA,worker=C.worker_encode(old),controls=C.base.encode(controls),dependencies=C.base.encode(recipe),origin=[],code=record(C.__file__),initial=dict(selection=C.codec.encode(s),lease=C.base.encode(l))))
    rejected=bound(tmp_path/'rejected-result.json',C._result('NAT_REPAIR_REQUIRED',descriptor,s,l,checkpoint=blocked,evidence=[blocked],next_step='nat-writer'))
    if changed:completion={**completion,'sha256':'0'*64}
    arguments=dict(descriptor=descriptor,checkpoint=rejected,retry_completion=completion)
    if changed:
        with pytest.raises(ValueError):C.run('import-retry',arguments,controls,tmp_path/'import')
        assert not (tmp_path/'import').exists();return
    outcome=C.run('import-retry',arguments,controls,tmp_path/'import');result=read(read(outcome)['result'])
    assert result['status']=='RETRY_READY' and C.base.decode(result['lease'])==lease and len(calls)==1
    monkeypatch.setattr(C,'dependency',lambda recipe,directory:bound(directory/'dependency.json',{'fixture':True}))
    # Model sufficient root lease capacity, as the main instruction fixture does.
    # The separate short-lease test checks fail-closed behavior before work.
    active=C.worker.active
    def covered(*args):
        facade,before,tracker=active(*args)
        before['leases']['task_agent']['expires_at']=time.time_ns()+3600*10**9
        return facade,before,tracker
    monkeypatch.setattr(C.worker,'active',covered)
    outcome=C.run('step',dict(descriptor=result['descriptor'],checkpoint=read(outcome)['result']),controls,tmp_path/'round2')
    result=read(read(outcome)['result']);assert result['status']=='MODEL_REQUIRED' and len(calls)==1
    request=w.request_saved(old.run/'steps/nat-writer/round-02/request.json')
    first=w.request_saved(old.run/'steps/nat-writer/request.json')
    assert request.prepared.ordinal==request.prepared.limit==2 and request.prepared.scope==first.prepared.scope


@pytest.mark.parametrize('models,commands,containers',[(True,False,False),(False,True,False),(False,False,True),(True,True,True)])
def test_capabilities_are_forwarded_exactly_and_journaled(instruction,inprocess,monkeypatch,models,commands,containers):
    w,s,l,controls,d,initial,home=instruction
    credential=home/'credential.env';credential.write_text('synthetic fixture, never a credential')
    controls=replace(controls,env_file=credential)
    document=read(d);document['controls']=C.base.encode(controls);d=bound(home/'cap-descriptor.json',document)
    initial=bound(home/'cap-ready.json',{**read(initial),'descriptor':d})
    seen=[]
    def operation(w,s,l,phase,directory,**kw):seen.append(kw);return s,l,None
    monkeypatch.setattr(C.worker,'_operation',operation)
    import xl_task_build
    monkeypatch.setattr(xl_task_build,'read_env_key',lambda _: 'synthetic-only')
    outcome=C.run('step',dict(descriptor=d,checkpoint=initial),controls,home/'cap-attempt',models=models,commands=commands,containers=containers)
    assert read(read(outcome)['result'])['status']=='STEP_COMPLETE'
    intent=read(record(home/'cap-attempt/intent.json'))
    for name,enabled in [('models',models),('commands',commands),('containers',containers)]:
        assert seen[0][name] is enabled and intent[name] is enabled
    assert seen[0]['credential']==('synthetic-only' if models else None)


@pytest.mark.parametrize('slots',[1,2,0,3,True])
def test_shared_campaign_capacity_never_exceeds_two(instruction,slots):
    w,s,l,c,d,initial,home=instruction
    document=read(d);document['worker']=C.worker_encode(replace(w,campaign=replace(w.campaign,model_slots=slots)))
    changed=bound(home/'capacity.json',document)
    if type(slots) is int and slots in (1,2):assert C.selected(changed)[0].campaign.model_slots==slots
    else:
        with pytest.raises(ValueError,match='capacity'):C.selected(changed)


def test_initial_selection_cannot_be_substituted(instruction):
    w,s,l,c,d,initial,home=instruction
    value=read(initial);value['selection']=C.codec.encode(replace(s,content=replace(s.content,stage_sha256='0'*64)))
    with pytest.raises(ValueError,match='initial selection'):C._prior(bound(home/'other-initial.json',value),d)


def test_cli_pairs_explicit_hashes_and_capabilities_without_dispatch(monkeypatch,tmp_path,capsys):
    seen=[]
    def captured(*args,**kwargs):seen.append((args,kwargs));return {'fixture':'no execution'}
    monkeypatch.setattr(C,'run',captured)
    argv=['step','--descriptor','/selected/descriptor.json','--descriptor-sha256','a'*64,'--checkpoint','/selected/result.json','--checkpoint-sha256','b'*64,'--journal',str(tmp_path/'step'),'--slot-lock',str(tmp_path/'slot.lock'),'--env-file',str(tmp_path/'env'),'--dispatch-models','--dispatch-commands','--dispatch-containers']
    assert C.main(argv)==0
    args,options=seen[0]
    assert args[0]=='step' and args[1]['checkpoint']==dict(path='/selected/result.json',sha256='b'*64)
    assert options==dict(models=True,commands=True,containers=True)
    seen.clear();argv.remove('--descriptor-sha256');argv.remove('a'*64)
    with pytest.raises(ValueError,match='paired'):C.main(argv)
    assert not seen


def test_final_runtime_can_finish_before_renewal_with_two_thousand_seconds(instruction,inprocess,monkeypatch):
    seen=[]
    def op(w,s,l,phase,directory,**kw):seen.append(phase);return s,l,None
    monkeypatch.setattr(C.worker,'_operation',op)
    previous=None
    for i,phase in enumerate(C.worker.STEPS):
        if phase=='final-runtime':break
        outcome,result=execute(instruction,i,previous);previous=outcome['result']
    active=C.worker.active
    def short(*args):
        facade,before,tracker=active(*args)
        before['leases']['task_agent']['expires_at']=time.time_ns()+2000*10**9
        return facade,before,tracker
    monkeypatch.setattr(C.worker,'active',short)
    outcome,result=execute(instruction,i,previous,containers=True)
    assert result['status']=='STEP_COMPLETE' and result['next_step']=='dialogue-commit'
    assert seen[-1]=='final-runtime'
    resource=read(outcome['resource']);intent=read(resource['intent'])
    assert intent['timeout_seconds']==1350 and intent['memory_limit_bytes']==8*1024**3


@pytest.mark.parametrize('remaining',[1379,1380])
def test_short_final_runtime_lease_does_not_start_work(instruction,inprocess,monkeypatch,remaining):
    def op(w,s,l,phase,directory,**kw):return s,l,None
    monkeypatch.setattr(C.worker,'_operation',op);previous=None
    for i,phase in enumerate(C.worker.STEPS):
        if phase=='final-runtime':break
        outcome,result=execute(instruction,i,previous);previous=outcome['result']
    active=C.worker.active
    def short(*args):
        facade,before,tracker=active(*args)
        before['leases']['task_agent']['expires_at']=time.time_ns()+remaining*10**9
        return facade,before,tracker
    monkeypatch.setattr(C.worker,'active',short)
    monkeypatch.setattr(C.worker,'step',lambda *a,**k:pytest.fail('no runtime work below headroom'))
    outcome,result=execute(instruction,i,previous,containers=True)
    assert result['status']=='LEASE_RENEWAL_REQUIRED'
    assert not C.worker.step_directory(instruction[0],'final-runtime').exists()
