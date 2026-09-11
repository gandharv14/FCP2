from dataclasses import asdict,replace
from pathlib import Path
import copy,time
import pytest
from scripts import harbor_package_recovery as R,harbor_campaign as C
from scripts.batch import harbor_pause_recovery as pause,harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import record,read
from scripts.batch.harbor_workbook_bootstrap import Lease
from test_harbor_package_recovery import scenario,worker,completed,direct,setup,selected,mock_review
from test_root_lease_takeover import save
from test_harbor_tracker_lifecycle import authority

@pytest.fixture
def continued(scenario,monkeypatch):
    case=scenario;s=case.s;c=s.bootstrap.policy.tracker_root.parent;wb=s.bootstrap.workbook
    path=s.bootstrap.policy.tracker_root/(wb+'.json');before=read(record(path));old=before['leases']['task_agent'];at=old['expires_at']+1
    proof=save(case.home/'ordinary-stopped.json',{'lease':old,'guard_reaped':True})
    result=L.lease_operation(path,authority=authority(),operation='replace_expired',lease_kind='task_agent',owner=old['owner'],scope=old['scope'],duration_ns=1000,expected_revision=before['revision'],expected_sha256=record(path)['sha256'],prior_lease=old,stopped_owner_proof=proof,prior_owner_stopped_validator=lambda observed,prior,chosen:observed==before and prior==old and read(chosen)=={'lease':old,'guard_reaped':True},clock=lambda:at)
    new=result['tracker']['leases']['task_agent'];phase2lease=Lease(new['owner'],new['token'],result['revision'],result['sha256'])
    home=c/'work'/wb;(home/'environment-worker').mkdir(parents=True);(c/'actual-workbook-slot.lock').touch()
    # A root-selected complete prefix snapshot under the replacement owner; the
    # exact same content/owning continuation is retained, no semantic gate reset.
    value=read(case.checkpoint);value['lease']=C.encode(phase2lease)
    checkpoint=save(home/'completed-phase2.json',value)
    guard=read(case.outcome);resource=read(guard['resource']);intent=read(resource['intent'])
    intent['arguments']['checkpoint']=checkpoint
    gi=save(home/'guard/intent.json',intent);log=home/'guard/step.log';log.write_bytes(Path(resource['log']['path']).read_bytes())
    gr=save(home/'guard/resource.json',{**resource,'intent':gi,'log':record(log)})
    outcome=save(home/'guard/outcome.json',{**guard,'resource':gr})
    case.checkpoint=checkpoint;case.outcome=outcome
    before=read(record(path));request=save(home/'continued-rebuild.json',{'request':'Resume the authorized rebuild after coordinator pause.'})
    auth=save(home/'authorization.json',dict(schema_version=pause.AUTH,root_actor='harbor-orchestrator',operation=pause.OPERATION,campaign_root=str(c),workbook=wb,prior_tracker=record(path),prior_lease=before['leases']['task_agent'],continuation_request=request,reason='coordinator_pause',additional_recoveries=1,semantic_budget_reset=False))
    selected_pause=pause.Selection(c,wb,record(path),checkpoint,outcome,auth,phase2lease.owner,'environment')
    at=new['expires_at']+1
    rootauthority=L.RootAuthority('harbor-orchestrator',pause.MAX_DURATION_NS,lambda op,p,b,q:op==pause.OPERATION and p==str(path) and b==before and q==dict(selection=pause._value(selected_pause),prior_lease=before['leases']['task_agent'],authorization=auth))
    lease,receipt=pause.recover_pause(selected_pause,home/'pause-recoveries/one',authority=rootauthority,clock=lambda:at)
    monkeypatch.setattr(time,'time_ns',lambda:at+100)
    case.lease=lease;case.pause=receipt
    return case


def prepare(c):return R.prepare(c.descriptor,c.checkpoint,c.outcome,c.lease,c.home/'continued-package',pause_completion=c.pause)


def test_current_prefix_and_actual_package_after_exact_pause_bridge(continued,monkeypatch):
    c=continued;before=read(record(c.s.bootstrap.policy.tracker_root/(c.s.bootstrap.workbook+'.json')))
    historical=Path(c.checkpoint['path']).read_bytes();p=prepare(c)
    assert read(p)['schema_version']==R.PAUSE_SCHEMA and read(p)['pause_completion']==c.pause
    result=R.execute(p,dispatch_commands=True)
    assert result['status']=='ENVIRONMENT_READY'
    after=read(record(c.s.bootstrap.policy.tracker_root/(c.s.bootstrap.workbook+'.json')))
    assert after['revision']==before['revision']+1 and after['handoffs']==before['handoffs']
    assert Path(c.checkpoint['path']).read_bytes()==historical
    assert all(after['lane_state'][k]['attempt']==v['attempt'] for k,v in before['lane_state'].items())


@pytest.mark.parametrize('fault',['omitted','foreign','stale_checkpoint','old_token'])
def test_explicit_bridge_never_rewrites_or_synthesizes_old_lease(continued,fault):
    c=continued
    if fault=='omitted':
        with pytest.raises(ValueError):R.prepare(c.descriptor,c.checkpoint,c.outcome,c.lease,c.home/'wrong')
        return
    if fault=='foreign':c.pause={**c.pause,'sha256':'0'*64}
    elif fault=='old_token':c.lease=C.decode(read(c.checkpoint)['lease'])
    else:
        value=read(c.checkpoint);old=C.decode(value['lease']);value['lease']=C.encode(replace(old,sha256='0'*64));c.checkpoint=save(c.home/'stale.json',value)
    with pytest.raises(ValueError):prepare(c)
    assert not (c.home/'continued-package').exists()


def test_complete_package_and_cas_interruption_reconciles_with_pause_evidence(continued,monkeypatch):
    c=continued;p=prepare(c)
    def stop(at):
        if at=='after_gate':raise RuntimeError('lost result')
    with pytest.raises(RuntimeError):R.execute(p,dispatch_commands=True,fault=stop)
    monkeypatch.setattr(R.W,'_gate',lambda *a,**k:pytest.fail('no second CAS'))
    result=R.reconcile(p);assert result['status']=='ENVIRONMENT_READY'


def test_root_parallel_controls_match_exact_model_roots_and_guard_fd(scenario,monkeypatch):
    from scripts import harbor_parallel_policy as policy
    from scripts.batch import harbor_model_executor as E
    import fcntl,os
    case=scenario;c=case.s.bootstrap.policy.tracker_root.parent;wb=case.s.bootstrap.workbook
    monkeypatch.setattr(policy,'WORKBOOKS',(wb,'0042','0076','0184'))
    for name in ('parallel-policy','parallel-workbook-locks','model-journal','model-slots'):(c/name).mkdir()
    (c/'actual-workbook-slot.lock').touch();(c/'model-slots/capacity').write_bytes(b'1\n')
    contract=policy.put(c/'parallel-policy/selected.json',policy.proposal(c));activation=policy.activate(contract);parallel=dict(contract=contract,activation=activation)
    value=read(case.descriptor);new_worker=replace(case.s,campaign=E.Campaign(c/'model-journal',c/'model-slots',1));value['worker']=C.encode(new_worker)
    descriptor=save(case.home/'parallel-descriptor.json',value)
    controls=R.parallel_controls(descriptor,parallel)
    assert controls.slot_lock==c/'parallel-workbook-locks'/(wb+'.lock')
    with pytest.raises(ValueError,match='model roots'):R.parallel_controls(case.descriptor,parallel)
    with pytest.raises(ValueError):R.parallel_controls(descriptor,{'contract':contract,'activation':{**activation,'sha256':'0'*64}})
    seen=[]
    def guard(argv,**kw):
        seen.append(kw)
        assert kw['timeout_seconds']==900 and kw['memory_limit_bytes']==8*1024**3
        held=kw['guardian_lock_fds'];assert len(held)==1
        assert os.fstat(held[0]).st_ino==controls.slot_lock.stat().st_ino
        with controls.slot_lock.open('a+b') as other:
            with pytest.raises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
        raise RuntimeError('synthetic interrupted child, no completion fabricated')
    monkeypatch.setattr(C,'_run_in_process_group',guard)
    lease=save(case.home/'current-lease.json',C.encode(case.lease))
    args=dict(descriptor=descriptor,checkpoint=case.checkpoint,outcome=case.outcome,lease=lease)
    outcome=R.run('prepare',args,controls,case.home/'parallel-guard',parallel=parallel)
    assert read(outcome)['status']=='RECONCILIATION_REQUIRED' and len(seen)==1
    assert read(read(outcome)['resource'])['failure'] is not None
    with pytest.raises(ValueError,match='explicitly selected'):R.run('prepare',args,case.controls,case.home/'global-guard')
