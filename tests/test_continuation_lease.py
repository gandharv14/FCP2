from pathlib import Path
from dataclasses import asdict,replace
import pytest
from scripts.fleet import continuation_lease as F
from scripts.batch import harbor_tracker_lifecycle as life,harbor_dialogue_semantic_retry as retry,harbor_instruction_worker as w
from scripts.batch.harbor_admission import read,record
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup

def renew(failed,tmp_path,**extra):
    worker,s,lease,blocked,task,*_=failed
    return F.renew(life=life,read=read,record=record,keep=w.keep,path=task.tracker['path'],lease=lease,
        authority_record={'fixture':'selected root policy'},validation=lambda before,l:retry._failure(worker,s,l,blocked)[3],
        custody=lambda:None,directory=tmp_path/'continued',**extra)

def test_original_failed_review_and_next_round_survive_two_hour_lease(failed,tmp_path):
    worker,s,lease,blocked,task,_,_,calls=failed;old=Path(blocked['path']).read_bytes()
    current,receipt=renew(failed,tmp_path)
    assert current.owner==lease.owner and current.token==lease.token and current.revision==lease.revision+1
    value=read(receipt);assert value['duration_ns']==7200*10**9 and value['original_expired_replacements']==0
    assert Path(blocked['path']).read_bytes()==old and len(calls)==3
    worker,s,current,permit=retry.authorize(worker,s,current,blocked=blocked)
    result=w.step(worker,s,current,checkpoint=permit)
    assert result.status=='MODEL_REQUIRED' and len(calls)==3
    request=w.request_saved(w.step_directory(worker,'dialogue-writer')/'request.json')
    assert request.prepared.ordinal==request.prepared.limit==2
    with pytest.raises(ValueError,match='already attempted'):renew(failed,tmp_path)

@pytest.mark.parametrize('stage',['before_cas','after_cas'])
def test_uncertain_never_replays_cas(failed,tmp_path,stage):
    old=read(record(failed[4].tracker['path']))
    def fault(at):
        if at==stage:raise RuntimeError('uncertain')
    with pytest.raises(RuntimeError):renew(failed,tmp_path,_fault=fault)
    after=read(record(failed[4].tracker['path']))
    assert after['revision']==old['revision']+(stage=='after_cas')
    assert (tmp_path/'continued/intent.json').is_file() and not (tmp_path/'continued/complete.json').exists()
    with pytest.raises(ValueError,match='already attempted'):renew(failed,tmp_path)
    assert read(record(failed[4].tracker['path']))==after

def test_expired_custody_rejected_without_consuming_replacement(failed,tmp_path):
    before=read(record(failed[4].tracker['path']))
    with pytest.raises(ValueError,match='expired lease'):renew(failed,tmp_path,clock=lambda:before['leases']['task_agent']['expires_at']+1)
    assert not (tmp_path/'continued').exists() and read(record(failed[4].tracker['path']))==before

def test_sufficient_live_lease_retained_without_cas_or_review_replay(failed,tmp_path):
    worker,s,lease,blocked,task,_,_,calls=failed
    result=life.lease_operation(task.tracker['path'],authority=life.RootAuthority('harbor-orchestrator',14400*10**9,lambda *a:True),operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+s.content.workbook,token=lease.token,duration_ns=14400*10**9,expected_revision=lease.revision,expected_sha256=lease.sha256)
    lease=replace(lease,revision=result['revision'],sha256=result['sha256']);failed=(worker,s,lease,blocked,task,*failed[5:])
    before=read(record(task.tracker['path']));at=before['leases']['task_agent']['expires_at']-F.DURATION_NS
    current,receipt=renew(failed,tmp_path,clock=lambda:at,retain_sufficient=True)
    assert current==lease and read(record(task.tracker['path']))==before
    value=read(receipt);assert value['schema_version']=='fleet-retained-live-continuation-lease/v1'
    assert value['lease_operations']==0 and not (tmp_path/'continued/intent.json').exists()
    worker,s,current,permit=retry.authorize(worker,s,current,blocked=blocked)
    result=w.step(worker,s,current,checkpoint=permit)
    assert result.status=='MODEL_REQUIRED' and len(calls)==3
    with pytest.raises(ValueError,match='already attempted'):renew(failed,tmp_path,clock=lambda:at,retain_sufficient=True)

def test_insufficient_live_runway_still_uses_original_renewal(failed,tmp_path):
    old=read(record(failed[4].tracker['path']));at=old['leases']['task_agent']['heartbeat_at']+1
    current,receipt=renew(failed,tmp_path,clock=lambda:at,retain_sufficient=True)
    assert current.revision==failed[2].revision+1
    assert read(receipt)['schema_version']==F.SCHEMA


def test_completed_renewal_archives_validate_without_replaying(failed,tmp_path):
    import json
    worker,s,lease,blocked,task,*_=failed
    before=tmp_path/'before.json';before.write_bytes(Path(task.tracker['path']).read_bytes())
    current,receipt=renew(failed,tmp_path)
    after=tmp_path/'after.json';after.write_bytes(Path(task.tracker['path']).read_bytes())
    args=dict(life=life,read=read,record=record,path=task.tracker['path'],previous=lease,completion=receipt,before_binding=record(before),after_binding=record(after),authority_record={'fixture':'selected root policy'})
    assert F.validate_completed(**args)==current
    with pytest.raises(ValueError):F.validate_completed(**dict(args,authority_record={'foreign':True}))
    with pytest.raises(ValueError):F.validate_completed(**dict(args,previous=replace(lease,owner='foreign')))
    with pytest.raises(ValueError):F.validate_completed(**dict(args,before_binding=record(after)))
    changed=read(record(after));changed['lane_state']['harbor-dialogue-author']['attempt']=99
    after.write_text(json.dumps(changed));newrow=record(after)
    with pytest.raises(Exception):F.validate_completed(**dict(args,after_binding=newrow))
