"""Append-only custody extension for an already completed enqueue.

Original enqueue bytes stay immutable. The derived checkpoint is explicitly a
lease projection, with a separate certificate validated in publication readers.
No models, task execution, quality decision, or expired-lease replay occurs here.
"""
from dataclasses import asdict,replace
from pathlib import Path
import copy,json,time
SCHEMA='harbor-queued-publication-lease/v1'
DURATION_NS=14400*10**9

def require(ok,message):
    if not ok:raise ValueError(message)

def validate_delta(life,before,after,allowance,*,at=None):
    require(after['revision']==before['revision']+1 and len(after['handoffs'])==len(before['handoffs'])+1,'queued renewal revision/history differs')
    event=after['handoffs'][-1];old=before['leases']['task_agent'];new=after['leases']['task_agent']
    require(event['operation']=='renew' and event['lease_kind']=='task_agent'
        and event['before']==old and event['after']==new and event['allowance']==allowance
        and event['root_actor']=='harbor-orchestrator' and event['revision']==after['revision']
        and old['heartbeat_at']<=event['at']<old['expires_at'], 'exact live same-owner renewal required')
    require(new=={**old,'heartbeat_at':event['at'],'expires_at':event['at']+DURATION_NS},'queued renewal owner/token/scope/duration changed')
    expected=copy.deepcopy(before);expected.update(revision=after['revision'],updated_at=event['at'],handoffs=after['handoffs']);expected['leases']['task_agent']=new
    require(expected==after,'queued renewal changed source, task, verdict, or lane evidence')
    life.validate_lifecycle(after)
    return event

def inspect(P,request,profile,*,now_ns=None):
    require(set(profile)=={'schema_version','workbook','source_sha256','tracker','ready_proof','allowance','home'}
        and profile['schema_version']==SCHEMA,'closed queued renewal plan required')
    before=P.read(profile['tracker']);proof=P.read(profile['ready_proof']);now=time.time_ns()if now_ns is None else now_ns
    require(proof['status']=='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'
        and proof['tracker']==profile['tracker'] and proof['models_dispatched']==0
        and proof['publication_mutations']==0,'actual full prior publication proof required')
    require(profile['tracker']==P.record(request.tracker_root/(profile['workbook']+'.json'))
        and before['workbook_id']==profile['workbook']
        and before['bindings']['original_raw_source']['sha256']==profile['source_sha256']
        and before['execution_state']=='publication_queued' and before['terminal_verdict']=='PUBLICATION_READY'
        and before['promotion_failure']is None and before['leases']['publication']is None,'exact unpromoted queued tracker required')
    require(before['revision']==request.lease.revision and profile['tracker']['sha256']==request.lease.sha256,'queued request stale')
    P._checkpoint(request)
    lease=before['leases']['task_agent'];require(lease['owner']==request.lease.owner and lease['token']==request.lease.token
        and lease['scope']=='workbook:'+profile['workbook'] and now+60*10**9<lease['expires_at'],'queued lease is expired or lacks renewal runway')
    from scripts.batch import harbor_continuation_allowance as A
    count,duration=A.lease_limits(profile['allowance'],before,at=now)
    require(duration>=DURATION_NS and count>=P.lifecycle.validate_lifecycle(before),'selected queued lease allowance insufficient')
    return before

def renew(P,request,profile):
    """One original lifecycle CAS under the caller's stopped-owner/root locks."""
    before=inspect(P,request,profile);home=Path(profile['home']);require(not home.exists(),'queued renewal consumed; reconcile without replay')
    home.mkdir(parents=True,mode=0o700);before_row=P._keep(home/'tracker-before.json',Path(profile['tracker']['path']).read_bytes())
    intent=P._keep(home/'intent.json',dict(schema_version=SCHEMA,profile=profile,request=P.encode(request),before=before_row,automatic_retry=False))
    lease=before['leases']['task_agent'];path=Path(profile['tracker']['path'])
    expected=dict(operation='renew',lease_kind='task_agent',owner=lease['owner'],scope=lease['scope'],token=lease['token'],duration_ns=DURATION_NS,prior_lease=None,stopped_owner_proof=None)
    authority=P.lifecycle.RootAuthority(actor='harbor-orchestrator',authorize=lambda op,p,b,r:op=='renew' and p==str(path) and b==before and r==expected,max_duration_ns=DURATION_NS,max_replacements=6,allowance=profile['allowance'])
    P.lifecycle.lease_operation(path,authority=authority,expected_revision=before['revision'],expected_sha256=profile['tracker']['sha256'],**expected)
    after=P.read(P.record(path));validate_delta(P.lifecycle,before,after,profile['allowance'])
    after_row=P._keep(home/'tracker-after.json',path.read_bytes())
    updated=replace(request.lease,revision=after['revision'],sha256=after_row['sha256'])
    original=P.read(request.checkpoint);projected=P._keep(home/'lease-projected-enqueue.json',{**original,'lease':asdict(updated)})
    result=dict(schema_version=SCHEMA,status='QUEUED_LEASE_RENEWED',intent=intent,before=before_row,after=after_row,original_checkpoint=request.checkpoint,projected_checkpoint=projected,allowance=profile['allowance'],duration_ns=DURATION_NS,models_dispatched=0,original_checkpoint_rewritten=False,automatic_retry=False)
    return P._keep(home/'complete.json',result)

def projected(P,request,completion):
    """Validate the entire renewal and projection each time a reader selects it."""
    value=P.read(completion);require(set(value)=={'schema_version','status','intent','before','after','original_checkpoint','projected_checkpoint','allowance','duration_ns','models_dispatched','original_checkpoint_rewritten','automatic_retry'},'closed queued completion required')
    require(value['schema_version']==SCHEMA and value['status']=='QUEUED_LEASE_RENEWED'
        and value['models_dispatched']==0 and value['original_checkpoint_rewritten']is False
        and value['automatic_retry']is False and value['duration_ns']==DURATION_NS,'unknown queued lease completion')
    intent=P.read(value['intent']);profile=intent['profile'];before=P.read(value['before']);after=P.read(value['after'])
    require(intent['schema_version']==SCHEMA and intent['request']==P.encode(request)
        and intent['before']==value['before'] and value['allowance']==profile['allowance']
        and value['original_checkpoint']==request.checkpoint,'queued renewal detached from original request')
    proof=P.read(profile['ready_proof']);require(proof['status']=='ACTUAL_READY_FULL_OWNING_PUBLICATION_PROOF_PASSED'
        and proof['models_dispatched']==0 and proof['publication_mutations']==0
        and proof['tracker']==profile['tracker'] and profile['tracker']['sha256']==value['before']['sha256']
        and before['workbook_id']==profile['workbook']==request.selection.content.workbook
        and before['bindings']['original_raw_source']['sha256']==profile['source_sha256']
        and before['revision']==request.lease.revision and value['before']['sha256']==request.lease.sha256
        and before['execution_state']=='publication_queued' and before['terminal_verdict']=='PUBLICATION_READY'
        and before['promotion_failure']is None and before['leases']['publication']is None,
        'queued renewal proof detached')
    validate_delta(P.lifecycle,before,after,value['allowance']);original=P._checkpoint(request)
    updated=replace(request.lease,revision=after['revision'],sha256=value['after']['sha256'])
    require(P.read(value['projected_checkpoint'])=={**original,'lease':asdict(updated)},'lease projection changed original completed enqueue')
    result=replace(request,lease=updated,checkpoint=value['projected_checkpoint']);P._checkpoint(result)
    return result
