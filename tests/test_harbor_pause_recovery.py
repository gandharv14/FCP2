from dataclasses import asdict,replace
from pathlib import Path
import copy,fcntl
import pytest
from scripts.batch import harbor_pause_recovery as R,harbor_tracker_lifecycle as L,harbor_tracker as S,harbor_lease_takeover as T
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_workbook_bootstrap import Lease
from test_root_lease_takeover import setup,save
from test_harbor_tracker_lifecycle import authority,kw

@pytest.fixture
def paused(setup):
    s,d,b,old=setup
    new,completed=T.replace_expired(s,d,clock=lambda:120)
    checkpoint=save(d/'checkpoint-new.json',{'lease':asdict(new),'status':'NAT_REPAIR_REQUIRED'})
    before=read(record(s.tracker['path'])); prior=record(s.tracker['path'])
    request=save(d/'continued-user-request.json',{'request':'Continue the already authorized rebuild after the coordinator pause.'})
    auth=save(d/'pause-authorization.json',dict(schema_version=R.AUTH,root_actor='harbor-orchestrator',operation=R.OPERATION,campaign_root=str(s.campaign_root),workbook=s.workbook,prior_tracker=prior,prior_lease=before['leases']['task_agent'],continuation_request=request,reason='coordinator_pause',additional_recoveries=1,semantic_budget_reset=False))
    selected=R.Selection(s.campaign_root,s.workbook,prior,checkpoint,s.guard,auth,'resumed-root','instruction',100)
    return selected,s.campaign_root/'work'/s.workbook/'pause-recoveries/one',before,new,completed


def test_explicit_pause_recovery_preserves_history_and_revokes_old_token(paused):
    s,d,b,old,prior=paused
    new,completion=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    before,after,checked=R.validate_completion(completion)
    assert before==b and checked==new and new.token!=old.token and new.owner=='resumed-root'
    assert L.validate_lifecycle(after)==1
    assert after['handoffs'][:-1]==b['handoffs']
    for field in b:
        if field not in {'handoffs','leases','revision','updated_at'}:assert after[field]==b[field]
    assert len([e for e in after['handoffs'] if e.get('operation')=='replace_expired'])==1
    assert len([e for e in after['handoffs'] if e.get('operation')==R.OPERATION])==1
    with pytest.raises(ValueError):S.cas_mutate(s.tracker['path'],expected_revision=new.revision,expected_sha256=new.sha256,owner=old.owner,token=old.token,scope='workbook:'+s.workbook,mutate=lambda _:None,allowed_fields=set(),now_ns=221)
    R.continuation_chain(record(d/'before.json'),asdict(old),after,new,completion=completion,tracker_path=s.tracker['path'])
    assert T.validate_completion(prior)[2]==old


def test_default_second_replacement_and_expired_renew_remain_rejected(paused):
    s,d,b,old,_=paused
    current=dict(revision=old.revision,sha256=old.sha256)
    for op in ['renew','replace_expired']:
        args=dict(token=old.token) if op=='renew' else dict(prior_lease=b['leases']['task_agent'],stopped_owner_proof=s.guard,prior_owner_stopped_validator=lambda *x:True)
        with pytest.raises(ValueError):L.lease_operation(s.tracker['path'],operation=op,duration_ns=100,**args,**kw(current,owner=old.owner,clock=lambda:220))
    assert read(s.tracker)==b and not d.exists()


@pytest.mark.parametrize('fault',['denied','foreign_auth','old_token','changed_request','missing_guard','active','bad_count','reset','wrong_owner','stale_tracker','no_auth','bad_duration'])
def test_recovery_requires_exact_root_authority_and_stopped_context(paused,fault):
    s,d,b,old,_=paused;a=authority();now=220
    if fault=='denied':a=authority(False)
    elif fault=='active':now=219
    elif fault=='bad_duration':s=replace(s,duration_ns=True)
    elif fault=='no_auth':s=replace(s,authorization={'path':str(d/'missing'),'sha256':'0'*64})
    elif fault=='missing_guard':Path(s.guard['path']).unlink()
    elif fault in {'foreign_auth','bad_count','reset','old_token'}:
        v=read(s.authorization)
        if fault=='foreign_auth':v['workbook']='0334'
        elif fault=='bad_count':v['additional_recoveries']=2
        elif fault=='reset':v['semantic_budget_reset']=True
        else:v['prior_lease']['token']='different'
        s=replace(s,authorization=save(Path(s.authorization['path']).with_name('altered.json'),v))
    elif fault=='changed_request':Path(read(s.authorization)['continuation_request']['path']).write_text('changed')
    elif fault=='wrong_owner':v=read(s.checkpoint);v['lease']['owner']='other';s=replace(s,checkpoint=save(Path(s.checkpoint['path']).with_name('wrong.json'),v))
    elif fault=='stale_tracker':s=replace(s,tracker={**s.tracker,'sha256':'0'*64})
    with pytest.raises((ValueError,FileNotFoundError)):R.recover_pause(s,d,authority=a,clock=lambda:now)
    assert not d.exists()


@pytest.mark.parametrize('point',['prepared','after_temp_fsync','after_publish','committed'])
def test_interrupted_commit_is_retained_never_repeated(paused,point):
    s,d,b,old,_=paused
    def fail(p):
        if p==point:raise KeyboardInterrupt(p)
    with pytest.raises(KeyboardInterrupt):R.recover_pause(s,d,authority=authority(),clock=lambda:220,_fault=fail)
    event=record(d/'event.json')
    if point in {'after_publish','committed'}:
        new,complete=R.reconcile(event)
        assert R.reconcile(event)==(new,complete)
        assert R.validate_completion(complete)[2]==new
    else:
        with pytest.raises(ValueError):R.reconcile(event)
        assert read(s.tracker)==b and not (d/'after.json').exists()
    with pytest.raises(ValueError):R.recover_pause(s,d,authority=authority(),clock=lambda:220)


@pytest.mark.parametrize('kind',['workbook','worker'])
def test_held_worker_or_workbook_lock_prevents_recovery(paused,kind):
    s,d,b,old,_=paused;p=s.campaign_root/('actual-workbook-slot.lock' if kind=='workbook' else 'work/0245/instruction-worker/worker.lock')
    with p.open('a+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    assert not d.exists() and read(s.tracker)==b


def test_additional_pause_budget_cannot_be_reset_by_new_authorization(paused):
    s,d,b,old,_=paused;new,completed=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    after=read(record(s.tracker['path']));auth=read(s.authorization);auth.update(prior_tracker=record(s.tracker['path']),prior_lease=after['leases']['task_agent'])
    s=replace(s,tracker=record(s.tracker['path']),checkpoint=save(d/'new-checkpoint.json',{'lease':asdict(new)}),authorization=save(d/'new-auth.json',auth))
    with pytest.raises(ValueError,match='budget'):R.recover_pause(s,d.with_name('two'),authority=authority(),clock=lambda:320)
    assert read(s.tracker)==after


@pytest.mark.parametrize('field',['attempt','prior_history','token','at','authorization'])
def test_rehashed_completion_cannot_change_budgets_or_history(paused,field):
    s,d,b,old,_=paused;new,completed=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    value=read(completed);after=read(value['after'])
    if field=='attempt':after['scheduler']['attempts']+=1
    elif field=='prior_history':after['handoffs'][1]['at']+=1
    elif field=='token':value['event']['after']['token']=old.token;after['handoffs'][-1]=value['event'];after['leases']['task_agent']['token']=old.token
    elif field=='at':value['event']['at']=219;after['handoffs'][-1]=value['event']
    else:value['event']['authorization']={**s.authorization,'sha256':'0'*64};after['handoffs'][-1]=value['event']
    value['after']=save(d/'forged-after.json',after);value['lease']['sha256']=value['after']['sha256']
    with pytest.raises(ValueError):R.validate_completion(save(d/'forged-complete.json',value))


def test_root_authority_receives_exact_authorization_and_prior_history(paused):
    s,d,b,old,_=paused;seen=[]
    def allow(op,path,prior,request):
        seen.append(request)
        return op==R.OPERATION and path==s.tracker['path'] and prior==b and request==dict(selection=R._value(s),prior_lease=b['leases']['task_agent'],authorization=s.authorization)
    R.recover_pause(s,d,authority=L.RootAuthority('harbor-orchestrator',100,allow),clock=lambda:220)
    assert len(seen)==1


def test_coordinator_encoded_checkpoint_and_completed_guard(paused):
    from scripts import harbor_campaign as C
    s,d,b,old,_=paused
    checkpoint=save(d.parent/'encoded.json',{'lease':C.encode(old)})
    home=s.campaign_root/'work'/s.workbook
    code=home/'current-driver.py';code.write_text('# guarded fixed coordinator\n')
    executable=home/'python';executable.write_text('guarded runtime identity\n')
    intent=save(home/'coordinator/intent.json',dict(schema_version='harbor-coordinator-step/v1',code=record(code),executable=record(executable),timeout_seconds=900,memory_limit_bytes=8*1024**3))
    log=home/'coordinator/step.log';log.write_text('child exited 1\n')
    resource=save(home/'coordinator/resource.json',dict(intent=intent,log=record(log),metrics={'peak_rss_bytes':32},returncode=1,failure=None))
    outcome=save(home/'coordinator/outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,resource=resource,source_invalidity_claimed=False,terminal_verdict=None))
    s=replace(s,checkpoint=checkpoint,guard=outcome)
    new,c=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    assert R.validate_completion(c)[2]==new


def test_parallel_activation_requires_exact_workbook_lock_and_rejects_old_global(paused,monkeypatch):
    from scripts import harbor_parallel_policy as policy
    # Fixture uses0245 while production closed roster is0035/0042/0076/0184.
    # Build a real synthetic policy with that fixed fixture roster; owning policy
    # validation, activation, inode observation and locks still execute normally.
    monkeypatch.setattr(policy,'WORKBOOKS',('0035','0042','0245','0184'))
    s,d,b,old,_=paused;c=s.campaign_root
    for name in ['parallel-policy','parallel-workbook-locks','model-slots','model-journal']:(c/name).mkdir()
    (c/'model-slots/capacity').write_bytes(b'1\n')
    contract=policy.put(c/'parallel-policy/selected.json',policy.proposal(c));activation=policy.activate(contract)
    with pytest.raises(ValueError,match='activated'):R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    s=replace(s,parallel_contract=contract,parallel_activation=activation)
    lane=policy.validate(contract,activation,s.workbook)
    with lane.open('a+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    # A different workbook lane does not block this workbook's stopped proof.
    with (c/'parallel-workbook-locks/0042.lock').open('a+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        new,complete=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    event=read(complete)['event'];stop=read(event['stopped_owner_proof'])
    assert stop['locks'][0]['path']==str(lane)
    assert R.validate_completion(complete)[2]==new


def test_reconciliation_does_not_accept_later_tracker_mutation(paused):
    s,d,b,old,_=paused;new,c=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    renewed=L.lease_operation(s.tracker['path'],operation='renew',duration_ns=100,token=new.token,**kw(dict(revision=new.revision,sha256=new.sha256),owner=new.owner,clock=lambda:230))
    with pytest.raises(ValueError,match='advanced'):R.reconcile(record(d/'event.json'))
    current=Lease(new.owner,new.token,renewed['revision'],renewed['sha256'])
    R.continuation_chain(record(d/'before.json'),asdict(old),renewed['tracker'],current,completion=c,tracker_path=s.tracker['path'])


@pytest.mark.parametrize('change',['guard','tracker','request'])
def test_last_commit_rechecks_all_selected_bytes(paused,change):
    s,d,b,old,_=paused
    def mutate(point):
        if point=='after_temp_fsync':
            if change=='tracker':Path(s.tracker['path']).write_bytes(L.canonical({**b,'revision':b['revision']+1}))
            elif change=='guard':Path(s.guard['path']).write_text('changed')
            else:Path(read(s.authorization)['continuation_request']['path']).write_text('changed')
    with pytest.raises(ValueError):R.recover_pause(s,d,authority=authority(),clock=lambda:220,_fault=mutate)
    assert not (d/'complete.json').exists()
    assert not any(e.get('schema_version')==L.PAUSE_EVENT_SCHEMA for e in read(record(s.tracker['path']))['handoffs'])
