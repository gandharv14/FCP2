from pathlib import Path
from dataclasses import asdict,replace
import copy,fcntl,json
import pytest
from scripts.batch import harbor_lease_takeover as T
from scripts.batch import harbor_tracker_lifecycle as L,harbor_tracker as S
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_admission import read,record
from test_harbor_tracker_lifecycle import created,acquire,authority,kw


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(L.canonical(value));return record(path)


@pytest.fixture
def setup(tmp_path):
    c=tmp_path/'campaign';(c/'workbooks').mkdir(parents=True)
    path,start=created(c/'workbooks');a=acquire(path,start);before=a['tracker'];old=before['leases']['task_agent']
    lease=Lease(old['owner'],old['token'],a['revision'],a['sha256'])
    home=c/'work/0245';run=home/'instruction-worker';run.mkdir(parents=True)
    (c/'actual-workbook-slot.lock').touch();(run/'worker.lock').touch()
    driver=home/'driver.py';driver.write_text('# root-selected guarded fixture\n')
    intent=save(home/'guard/intent-01.json',{'driver':record(driver)})
    log=home/'guard/step-01.log';log.write_text('guard reaped child\n')
    guard=save(home/'guard/resource-01.json',{'intent':intent,'log':record(log),'metrics':{'peak_rss_bytes':1},'returncode':0})
    result=save(home/'guard/result-01.json',{'status':'NAT_REPAIR_REQUIRED','lease':asdict(lease)})
    selection=T.Selection(c,'0245',record(path),'instruction',guard,result,'new-root',100)
    return selection,home/'lease-takeovers/one',before,lease


def test_exact_takeover_preserves_all_nonlease_history_and_old_owner_loses_authority(setup):
    s,d,b,old=setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    before,after,proved=T.validate_completion(complete)
    assert before==b and new==proved and new.token!=old.token and new.owner=='new-root'
    assert L.validate_lifecycle(after)==1 and after['lane_state']==b['lane_state']
    assert after['bindings']==b['bindings'] and after['repairs']==b['repairs'] and after['scheduler']==b['scheduler']
    assert after['handoffs'][:-1]==b['handoffs']
    with pytest.raises(ValueError):S.cas_mutate(s.tracker['path'],expected_revision=new.revision,expected_sha256=new.sha256,owner=old.owner,token=old.token,scope='workbook:0245',mutate=lambda _:None,allowed_fields=set(),now_ns=121)
    T.continuation_chain(record(d/'before.json'),asdict(old),after,new,takeover=complete,tracker_path=s.tracker['path'])
    with pytest.raises(ValueError):T.continuation_chain(record(d/'before.json'),asdict(old),after,new)


@pytest.mark.parametrize('point',['prepared','after_temp_fsync','after_publish','committed'])
def test_interruption_never_repeats_replacement_or_invents_commit(setup,point):
    s,d,b,old=setup
    def fail(stage):
        if stage==point:raise KeyboardInterrupt(stage)
    with pytest.raises(KeyboardInterrupt):T.replace_expired(s,d,clock=lambda:120,_fault=fail)
    proof=record(d/'stopped-owner.json')
    if point in ('after_publish','committed'):
        new,complete=T.recover(proof);assert T.validate_completion(complete)[2]==new
        assert T.recover(proof)==(new,complete)
    else:
        with pytest.raises(ValueError):T.recover(proof)
        assert read(record(s.tracker['path']))==b
    with pytest.raises(ValueError):T.replace_expired(s,d,clock=lambda:120)


@pytest.mark.parametrize('kind',['workbook','worker'])
def test_active_guard_or_worker_lock_blocks_takeover_without_mutation(setup,kind):
    s,d,b,old=setup;path=s.campaign_root/('actual-workbook-slot.lock' if kind=='workbook' else 'work/0245/instruction-worker/worker.lock')
    with path.open('a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):T.replace_expired(s,d,clock=lambda:120)
    assert not d.exists() and read(s.tracker)==b


@pytest.mark.parametrize('fault',['unexpired','missing_lock','changed_guard','foreign_guard','wrong_checkpoint_owner','wrong_checkpoint_token','missing_checkpoint','wrong_tracker','bad_duration'])
def test_unproved_old_owner_or_guard_rejects(setup,fault):
    s,d,b,old=setup
    if fault=='missing_lock':(s.campaign_root/'work/0245/instruction-worker/worker.lock').unlink()
    elif fault=='changed_guard':Path(s.guard['path']).write_text('{}')
    elif fault=='foreign_guard':s=replace(s,guard=save(s.campaign_root/'other/resource.json',read(s.guard)))
    elif fault.startswith('wrong_checkpoint'):
        v=read(s.checkpoint);v['lease']['owner' if fault.endswith('owner') else 'token']='wrong';s=replace(s,checkpoint=save(Path(s.checkpoint['path']),v))
    elif fault=='missing_checkpoint':Path(s.checkpoint['path']).unlink()
    elif fault=='wrong_tracker':s=replace(s,tracker={**s.tracker,'sha256':'0'*64})
    elif fault=='bad_duration':s=replace(s,duration_ns=True)
    with pytest.raises((ValueError,FileNotFoundError)):T.replace_expired(s,d,clock=lambda:119 if fault=='unexpired' else 120)
    assert not d.exists()


def test_second_replacement_is_forbidden_even_after_first_expiry(setup):
    s,d,b,old=setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    checkpoint=save(d/'new-root-result.json',{'lease':asdict(new)})
    selected=replace(s,tracker=record(s.tracker['path']),checkpoint=checkpoint,owner='third')
    with pytest.raises(ValueError,match='replacement'):T.replace_expired(selected,d.with_name('second'),clock=lambda:220)
    assert L.validate_lifecycle(read(record(s.tracker['path'])))==1


def test_exact_renewals_before_and_after_takeover_are_bound(setup):
    s,d,b,original=setup;old_snapshot=save(d.parent/'original.json',b)
    renew=L.lease_operation(s.tracker['path'],operation='renew',token=original.token,duration_ns=200,**kw({'revision':original.revision,'sha256':original.sha256},clock=lambda:30))
    s=replace(s,tracker=record(s.tracker['path']));new,complete=T.replace_expired(s,d,clock=lambda:230)
    later=L.lease_operation(s.tracker['path'],operation='renew',token=new.token,duration_ns=200,**kw({'revision':new.revision,'sha256':new.sha256},owner=new.owner,clock=lambda:240))
    current=Lease(new.owner,new.token,later['revision'],later['sha256'])
    T.continuation_chain(old_snapshot,asdict(original),later['tracker'],current,takeover=complete,tracker_path=s.tracker['path'])
    altered=copy.deepcopy(later['tracker']);altered['lane_state'][next(iter(altered['lane_state']))]['attempt']=0.5
    with pytest.raises(ValueError):T.continuation_chain(old_snapshot,asdict(original),altered,current,takeover=complete,tracker_path=s.tracker['path'])


def test_environment_before_instruction_uses_same_root_guard_no_fictitious_worker_lock(setup):
    s,d,b,old=setup;run=s.campaign_root/'work/0245/environment-worker';run.mkdir()
    oldrun=s.campaign_root/'work/0245/instruction-worker';(oldrun/'worker.lock').unlink();oldrun.rmdir()
    s=replace(s,worker_kind='environment')
    new,complete=T.replace_expired(s,d,clock=lambda:120)
    stop=read(read(complete)['stopped_owner_proof']);assert len(stop['locks'])==1
    assert new.token!=old.token and not (run/'worker.lock').exists()


@pytest.mark.parametrize('fault',['drop_evidence','change_old_lease','change_stop_time','change_after','change_count','change_code'])
def test_rehashed_forged_completion_does_not_establish_takeover(setup,fault):
    s,d,b,old=setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    v=read(complete);stop=read(v['stopped_owner_proof']);after=read(v['after'])
    if fault=='drop_evidence':stop['evidence']=[]
    elif fault=='change_old_lease':stop['prior_lease']['token']='different'
    elif fault=='change_stop_time':stop['observed_at']=0
    elif fault=='change_after':after['scheduler']['attempts']+=1
    elif fault=='change_count':after['handoffs'].append(after['handoffs'][-1])
    else:stop['implementation']['sha256']='0'*64
    proof=save(d/'forged-stop.json',stop);after['handoffs'][-1]['stopped_owner_proof']=proof
    a=save(d/'forged-after.json',after);v.update(stopped_owner_proof=proof,after=a,lease={**v['lease'],'sha256':a['sha256']})
    with pytest.raises(ValueError):T.validate_completion(save(d/'forged-complete.json',v))


def test_takeover_from_same_bytes_other_tracker_cannot_authorize_current_owner(setup):
    s,d,b,old=setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    before,after,_=T.validate_completion(complete)
    with pytest.raises(ValueError,match='another live tracker'):
        T.continuation_chain(record(d/'before.json'),asdict(old),after,new,takeover=complete,tracker_path='/another/0245.json')


def test_environment_mode_cannot_omit_existing_instruction_lock(setup):
    s,d,b,old=setup;(s.campaign_root/'work/0245/environment-worker').mkdir()
    with pytest.raises(ValueError,match='before instruction'):
        T.replace_expired(replace(s,worker_kind='environment'),d,clock=lambda:120)
    assert read(s.tracker)==b


@pytest.fixture
def driver_setup(setup):
    from scripts import harbor_campaign as campaign
    from scripts.batch.harbor_readiness import Selection
    from scripts.batch.harbor_complete_readiness import ReadinessSelection
    from scripts.batch import harbor_transition_codec as codec
    s,_,before,old=setup;c=s.campaign_root
    tracker=save(c/'trackers/workbooks/0245.json',before)
    run=c/'instruction';run.mkdir();(run/'worker.lock').touch()
    controls=campaign.Controls(c/'actual-workbook-slot.lock',None)
    config=save(c/'config.json',dict(schema_version='authoring-root-driver/v1',
        workbook='0245',run_root=str(c),controls=campaign.encode(controls)))
    guard=read(s.guard);intent=read(guard['intent'])
    guard['intent']=save(c/'guard/intent.json',dict(code=intent['driver'],controls=campaign.encode(controls)))
    guard['log']=save(c/'guard/step.log',{'completed':True});guard['failure']=None
    source=before['bindings']['original_raw_source']
    content=Selection('0245',c/'stage','a'*64,{'original_raw_source':source},c/'normalization',c/'nat',None,None)
    selected=ReadinessSelection(content,None,None,None)
    checkpoint=save(c.parent/'side-repair/latest.json',dict(selection=codec.encode(selected),lease=asdict(old)))
    return replace(s,tracker=tracker,guard=save(c/'guard/resource.json',guard),checkpoint=checkpoint,config=config),c/'lease-takeovers/one',before,old


def test_driver_bound_layout_uses_real_configured_locks_and_preserves_budget(driver_setup):
    s,d,b,old=driver_setup
    new,complete=T.replace_expired(s,d,clock=lambda:120)
    before,after,lease=T.validate_completion(complete)
    assert before==b and lease==new and after['lane_state']==b['lane_state']
    assert after['bindings']==b['bindings'] and after['repairs']==b['repairs']
    proof=read(read(complete)['stopped_owner_proof'])
    assert s.config in proof['evidence']
    assert proof['locks'][1]['path']==str(s.campaign_root/'instruction/worker.lock')
    T.continuation_chain(record(d/'before.json'),asdict(old),after,new,takeover=complete,tracker_path=s.tracker['path'])


@pytest.mark.parametrize('fault',['config','slot','checkpoint','active'])
def test_driver_binding_or_active_worker_blocks_takeover(driver_setup,fault):
    s,d,b,old=driver_setup
    if fault=='config':
        value=read(s.config);value['workbook']='0999';s=replace(s,config=save(Path(s.config['path']),value))
    elif fault=='slot':
        value=read(s.guard);intent=read(value['intent']);intent['controls']={'wrong':'slot'}
        value['intent']=save(Path(value['intent']['path']),intent);s=replace(s,guard=save(Path(s.guard['path']),value))
    elif fault=='checkpoint':
        value=read(s.checkpoint);value['lease']['token']='different';s=replace(s,checkpoint=save(Path(s.checkpoint['path']),value))
    if fault=='active':
        with (s.campaign_root/'instruction/worker.lock').open('a+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):T.replace_expired(s,d,clock=lambda:120)
    else:
        with pytest.raises(ValueError):T.replace_expired(s,d,clock=lambda:120)
    assert read(s.tracker)==b and not d.exists()


def test_three_chained_replacements_keep_all_proofs_and_fourth_is_rejected(setup):
    s,d,b,original=setup
    first_snapshot=None;completion=None
    for number in range(1,4):
        if completion:
            current=read(record(s.tracker['path']));old=current['leases']['task_agent']
            checkpoint=save(s.campaign_root/f'work/0245/guard/checkpoint-{number}.json',
                {'lease':asdict(Lease(old['owner'],old['token'],current['revision'],record(s.tracker['path'])['sha256']))})
            s=replace(s,tracker=record(s.tracker['path']),checkpoint=checkpoint,prior_completion=completion)
        new,completion=T.replace_expired(s,d.with_name(str(number)),clock=lambda:number*100+20)
        if first_snapshot is None:first_snapshot=record(d.with_name('1')/'before.json')
        before,after,proved=T.validate_completion(completion)
        assert proved==new and L.validate_lifecycle(after)==number
        assert after['lane_state']==b['lane_state'] and after['bindings']==b['bindings']
        T.continuation_chain(first_snapshot,asdict(original),after,new,takeover=completion,tracker_path=s.tracker['path'])
        assert completion in T.completion_records(completion)
    current=read(record(s.tracker['path']))
    s=replace(s,tracker=record(s.tracker['path']),prior_completion=completion,
        checkpoint=save(s.campaign_root/'work/0245/guard/fourth.json',{'lease':asdict(new)}))
    with pytest.raises(ValueError,match='budget exhausted'):
        T.replace_expired(s,d.with_name('4'),clock=lambda:420)
    assert read(s.tracker)==current


@pytest.mark.parametrize('fault',['omitted','substituted','active','budget_regressed'])
def test_replacement_chain_cannot_reset_history_or_steal_owner(setup,fault):
    s,d,b,old=setup;lease,complete=T.replace_expired(s,d,clock=lambda:120)
    before=read(record(s.tracker['path']))
    checkpoint=save(s.campaign_root/'work/0245/guard/second.json',{'lease':asdict(lease)})
    selected=replace(s,tracker=record(s.tracker['path']),checkpoint=checkpoint,prior_completion=complete)
    if fault=='omitted':selected=replace(selected,prior_completion=None)
    elif fault=='substituted':
        value=read(complete);proof=read(value['stopped_owner_proof']);proof['selection']['tracker']['path']='/foreign/tracker.json'
        value['stopped_owner_proof']=save(d/'forged-proof.json',proof)
        selected=replace(selected,prior_completion=save(d/'forged-complete.json',value))
    elif fault=='budget_regressed':
        # No new namespace can hide an injected different active identity.
        value=read(record(selected.tracker['path']));value['leases']['task_agent']['token']='injected'
        selected=replace(selected,tracker=save(Path(selected.tracker['path']),value))
    with pytest.raises(ValueError):T.replace_expired(selected,d.with_name('second'),clock=lambda:219 if fault=='active' else 220)
    assert not d.with_name('second').exists()


def test_driver_can_select_exact_previous_completion_without_fabricated_checkpoint(driver_setup):
    s,d,b,old=driver_setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    selected=replace(s,tracker=record(s.tracker['path']),prior_completion=complete,checkpoint=complete)
    lease,second=T.replace_expired(selected,d.with_name('second'),clock=lambda:220)
    assert T.validate_completion(second)[2]==lease
    assert read(read(second)['stopped_owner_proof'])['selection']['checkpoint']==complete


def test_changed_budget_or_handoff_prefix_cannot_bridge_lane_cas(setup):
    s,d,b,old=setup;new,complete=T.replace_expired(s,d,clock=lambda:120)
    selected=replace(s,tracker=record(s.tracker['path']),prior_completion=complete)
    before=read(record(s.tracker['path']))
    changed=copy.deepcopy(before);changed['revision']+=1;changed['handoffs']=changed['handoffs'][:-1]
    with pytest.raises(ValueError):T._prior_chain(selected,changed)
    changed=copy.deepcopy(before);changed['revision']-=1
    with pytest.raises(ValueError):T._prior_chain(selected,changed)


def test_second_committed_takeover_recovers_without_another_cas(driver_setup):
    s,d,b,old=driver_setup;lease,first=T.replace_expired(s,d,clock=lambda:120)
    selected=replace(s,tracker=record(s.tracker['path']),prior_completion=first,checkpoint=first)
    def interrupt(stage):
        if stage=='committed':raise RuntimeError('after owning CAS')
    second=d.with_name('second')
    with pytest.raises(RuntimeError):T.replace_expired(selected,second,clock=lambda:220,_fault=interrupt)
    tracker=record(s.tracker['path']);proof=record(second/'stopped-owner.json')
    new,complete=T.recover(proof)
    assert T.recover(proof)==(new,complete)
    assert record(s.tracker['path'])==tracker
    assert L.validate_lifecycle(read(tracker))==2


def test_exact_current_implementation_can_be_relocated_but_not_modified(tmp_path):
    relocated=tmp_path/'frozen/harbor_lease_takeover.py';relocated.parent.mkdir()
    relocated.write_bytes(Path(T.__file__).read_bytes())
    T._implementation(record(relocated))
    relocated.write_bytes(relocated.read_bytes()+b'\n# changed\n')
    with pytest.raises(ValueError,match='unreviewed'):
        T._implementation(record(relocated))


def test_retained_environment_takeover_stays_valid_after_instruction_start(setup):
    s,d,b,old=setup
    original=s.campaign_root/'work/0245/instruction-worker'
    (original/'worker.lock').unlink();original.rmdir()
    environment=original.with_name('environment-worker');environment.mkdir()
    selected=replace(s,worker_kind='environment')
    lease,complete=T.replace_expired(selected,d,clock=lambda:120)
    original.mkdir();(original/'worker.lock').touch()
    assert T.validate_completion(complete)[2]==lease
    newer=replace(selected,tracker=record(s.tracker['path']),prior_completion=complete,checkpoint=complete)
    with pytest.raises(ValueError,match='before instruction'):
        T._recipe(newer)
