"""Historical stop observations survive authorized later worker creation."""
from dataclasses import asdict,replace
import pytest
from scripts.batch import harbor_pause_recovery as R,harbor_lease_takeover as T,harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,save
from test_harbor_tracker_lifecycle import authority,kw


@pytest.fixture
def environment_pause(setup):
    s,d,b,old=setup
    home=s.campaign_root/'work'/s.workbook
    (home/'instruction-worker').rename(home/'environment-worker')
    s=replace(s,worker_kind='environment')
    new,completed=T.replace_expired(s,d,clock=lambda:120)
    checkpoint=save(d/'environment-checkpoint.json',{'lease':asdict(new),'status':'STEP_COMPLETE'})
    before=read(record(s.tracker['path']));prior=record(s.tracker['path'])
    request=save(d/'continue.json',{'request':'Continue the authorized rebuild after pause.'})
    auth=save(d/'pause-auth.json',dict(schema_version=R.AUTH,root_actor='harbor-orchestrator',operation=R.OPERATION,campaign_root=str(s.campaign_root),workbook=s.workbook,prior_tracker=prior,prior_lease=before['leases']['task_agent'],continuation_request=request,reason='coordinator_pause',additional_recoveries=1,semantic_budget_reset=False))
    selected=R.Selection(s.campaign_root,s.workbook,prior,checkpoint,s.guard,auth,'resumed-root','environment',100)
    return selected,home/'pause-recoveries/one',before,new


def test_historical_environment_pause_remains_valid_after_instruction_initialization(environment_pause):
    s,d,b,old=environment_pause
    new,completion=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    original=read(record(s.tracker['path']))
    worker=s.campaign_root/'work'/s.workbook/'instruction-worker';worker.mkdir();(worker/'worker.lock').touch()
    assert R.validate_completion(completion)[2]==new
    assert L.validate_lifecycle(read(record(s.tracker['path'])))==1
    assert read(record(s.tracker['path']))==original
    # Same-owner renewals remain possible; no expired replacement/semantic reset.
    renewed=L.lease_operation(s.tracker['path'],operation='renew',duration_ns=100,token=new.token,**kw(dict(revision=new.revision,sha256=new.sha256),owner=new.owner,clock=lambda:230))
    assert renewed['tracker']['lane_state']==original['lane_state']


def test_live_environment_pause_still_rejects_an_existing_instruction_worker(environment_pause):
    s,d,b,old=environment_pause
    (s.campaign_root/'work'/s.workbook/'instruction-worker').mkdir()
    with pytest.raises(ValueError,match='instruction worker'):
        R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    assert not d.exists() and read(s.tracker)==b


@pytest.mark.parametrize('change',['guard','checkpoint','authorization','implementation','before','lock_observation'])
def test_history_transition_does_not_accept_altered_original_stop_evidence(environment_pause,change):
    s,d,b,old=environment_pause
    new,completion=R.recover_pause(s,d,authority=authority(),clock=lambda:220)
    (s.campaign_root/'work'/s.workbook/'instruction-worker').mkdir()
    v=read(completion);event=v['event'];stop=read(event['stopped_owner_proof'])
    if change in ('guard','checkpoint','authorization'):
        binding=getattr(s,change);__import__('pathlib').Path(binding['path']).write_text('{}')
    elif change in ('implementation','before'):
        __import__('pathlib').Path(stop[change]['path']).write_text('{}')
    else:
        stop['locks'][0]['path']+='wrong';event['stopped_owner_proof']=save(d/'forged-stop.json',stop)
        completion=save(d/'forged-completion.json',v)
    with pytest.raises((ValueError,KeyError)):
        R.validate_completion(completion)
