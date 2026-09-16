"""Explicit expired queued custody recovery; no authoring or publication replay.

A full retained qualification and independently checked stopped-owner proof
precede the original lifecycle CAS. The original enqueue stays immutable.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy,time
SCHEMA='harbor-expired-queued-publication/v1'
DURATION_NS=14400*10**9

def require(ok,message):
    if not ok:raise ValueError(message)

def evidence(P,request,profile,before):
    require(set(profile)=={'schema_version','workbook','source_sha256','tracker','ready_proof','allowance','home','owner','stopped_owner_proof'}
        and profile['schema_version']==SCHEMA,'closed expired queued recovery required')
    proof=P.read(profile['ready_proof']);qualification=P.read(proof['qualification'])
    require(proof['status']=='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED'
        and proof['tracker']==profile['tracker'] and proof['request']==P.encode(request)
        and proof['models_dispatched']==proof['publication_mutations']==proof['lease_mutations']==0
        and qualification['schema_version']=='harbor-complete-readiness/v1'
        and qualification['workbook']==profile['workbook']
        and qualification['stage_sha256']==request.selection.content.stage_sha256
        and qualification['publication_ready']is True and qualification['blockers']==[],
        'full original queued qualification proof required')
    require(before['workbook_id']==profile['workbook']==request.selection.content.workbook
        and before['bindings']['original_raw_source']['sha256']==profile['source_sha256']
        and before['execution_state']=='publication_queued' and before['terminal_verdict']=='PUBLICATION_READY'
        and before['promotion_failure']is None and before['leases']['publication']is None
        and all(row['state']=='passed' and row['current_confidence']=='high'for row in before['lane_state'].values()),
        'exact unpromoted strictly qualified queue required')
    old=before['leases']['task_agent']
    require(profile['tracker']['path']==str(request.tracker_root/(profile['workbook']+'.json'))
        and before['revision']==request.lease.revision and profile['tracker']['sha256']==request.lease.sha256
        and old['owner']==request.lease.owner and old['token']==request.lease.token
        and old['scope']=='workbook:'+profile['workbook'],'expired request identity differs')
    require(type(profile['owner'])is str and bool(profile['owner'].strip()),'explicit successor owner required')
    P.lifecycle.checked_record(profile['stopped_owner_proof']);P._checkpoint(request)
    return old

def inspect(P,request,profile,*,now_ns=None):
    before=P.read(profile['tracker']);old=evidence(P,request,profile,before)
    require(P.record(profile['tracker']['path'])==profile['tracker'],'queued tracker changed')
    now=time.time_ns()if now_ns is None else now_ns
    require(now>=old['expires_at'],'live queued lease cannot be replaced')
    from scripts.batch.harbor_continuation_allowance import lease_limits
    count,duration=lease_limits(profile['allowance'],before,at=now)
    require(count>P.lifecycle.validate_lifecycle(before) and duration>=DURATION_NS,'new finite expired replacement allowance required')
    return before,count

def delta(P,before,after,profile):
    event=after['handoffs'][-1];old=before['leases']['task_agent'];new=after['leases']['task_agent']
    require(after['revision']==before['revision']+1 and len(after['handoffs'])==len(before['handoffs'])+1
        and event['operation']=='replace_expired' and event['lease_kind']=='task_agent'
        and event['root_actor']=='harbor-orchestrator' and event['before']==old and event['after']==new
        and event['allowance']==profile['allowance'] and event['stopped_owner_proof']==profile['stopped_owner_proof']
        and event['at']>=old['expires_at'] and event['revision']==after['revision'],
        'exact expired lifecycle replacement required')
    require(new==dict(owner=profile['owner'],token=new['token'],scope=old['scope'],acquired_at=event['at'],
        heartbeat_at=event['at'],expires_at=event['at']+DURATION_NS,acquisition_revision=after['revision'])
        and new['token']!=old['token'],'replacement must mint a new scoped token')
    expected=copy.deepcopy(before);expected.update(revision=after['revision'],updated_at=event['at'],handoffs=after['handoffs']);expected['leases']['task_agent']=new
    require(expected==after,'replacement changed task, source, verdict or consumed evidence')
    P.lifecycle.validate_lifecycle(after)
    require(P.lifecycle.validate_lifecycle(after)==P.lifecycle.validate_lifecycle(before)+1,'expired replacement consumption lost')

def recover(P,request,profile,*,stopped_validator):
    before,count=inspect(P,request,profile);old=before['leases']['task_agent']
    require(stopped_validator(before,old,profile['stopped_owner_proof'])is True,'independent stopped-owner validation required')
    home=Path(profile['home']);require(not home.exists(),'expired recovery consumed; reconcile without replay');home.mkdir(parents=True,mode=0o700)
    before_row=P._keep(home/'tracker-before.json',Path(profile['tracker']['path']).read_bytes())
    intent=P._keep(home/'intent.json',dict(schema_version=SCHEMA,profile=profile,request=P.encode(request),before=before_row,automatic_retry=False))
    expected=dict(operation='replace_expired',lease_kind='task_agent',owner=profile['owner'],scope=old['scope'],token=None,
        duration_ns=DURATION_NS,prior_lease=old,stopped_owner_proof=profile['stopped_owner_proof'])
    path=Path(profile['tracker']['path'])
    authority=P.lifecycle.RootAuthority('harbor-orchestrator',DURATION_NS,
        lambda op,p,b,r:op=='replace_expired' and p==str(path) and b==before and r==expected,
        max_replacements=count,allowance=profile['allowance'])
    P.lifecycle.lease_operation(path,authority=authority,expected_revision=before['revision'],expected_sha256=profile['tracker']['sha256'],
        prior_owner_stopped_validator=stopped_validator,**expected)
    after_row=P.record(path);after=P.read(after_row);delta(P,before,after,profile)
    retained=P._keep(home/'tracker-after.json',path.read_bytes());lease=after['leases']['task_agent']
    updated=replace(request.lease,owner=lease['owner'],token=lease['token'],revision=after['revision'],sha256=retained['sha256'])
    projected=P._keep(home/'lease-projected-enqueue.json',{**P._checkpoint(request),'lease':asdict(updated)})
    return P._keep(home/'complete.json',dict(schema_version=SCHEMA,status='EXPIRED_QUEUED_LEASE_REPLACED',intent=intent,before=before_row,
        after=retained,original_checkpoint=request.checkpoint,projected_checkpoint=projected,models_dispatched=0,
        original_checkpoint_rewritten=False,automatic_retry=False))

def projected(P,request,completion):
    v=P.read(completion);require(set(v)=={'schema_version','status','intent','before','after','original_checkpoint','projected_checkpoint','models_dispatched','original_checkpoint_rewritten','automatic_retry'}
        and v['schema_version']==SCHEMA and v['status']=='EXPIRED_QUEUED_LEASE_REPLACED' and v['models_dispatched']==0
        and v['original_checkpoint_rewritten']is False and v['automatic_retry']is False,'closed completed expired recovery required')
    intent=P.read(v['intent']);p=intent['profile'];before=P.read(v['before']);after=P.read(v['after'])
    require(intent['schema_version']==SCHEMA and intent['before']==v['before'] and intent['request']==P.encode(request)
        and intent['automatic_retry']is False and v['original_checkpoint']==request.checkpoint
        and v['before']['sha256']==p['tracker']['sha256'],'expired recovery lost original checkpoint')
    evidence(P,request,p,before);delta(P,before,after,p)
    lease=after['leases']['task_agent'];updated=replace(request.lease,owner=lease['owner'],token=lease['token'],revision=after['revision'],sha256=v['after']['sha256'])
    require(P.read(v['projected_checkpoint'])=={**P._checkpoint(request),'lease':asdict(updated)},'projection changed completed enqueue content')
    result=replace(request,lease=updated,checkpoint=v['projected_checkpoint']);P._checkpoint(result);return result
