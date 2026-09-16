"""Explicit root policy for a live, same-owner continuation lease.

This changes lease scheduling time, never a task's wall/memory/model budget.
Caller holds the original workbook locks and exact central custody. Expired or
uncertain custody cannot enter this path; every CAS has a durable fresh intent.
"""
from dataclasses import replace,asdict
from pathlib import Path
import os,time

SCHEMA='fleet-validated-continuation-lease/v1'
DURATION_NS=7200*10**9

def require(value,message):
    if not value:raise ValueError(message)

def renew(*,life,read,record,keep,path,lease,authority_record,validation,custody,directory,clock=time.time_ns,_fault=lambda _:None,retain_sufficient=False):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot renew')
    path=Path(path);directory=Path(directory)
    require(not directory.exists(),'continuation lease already attempted; reconcile, never replay')
    custody();before=read(record(path));selected=record(path);old=before['leases']['task_agent']
    require(before['revision']==lease.revision and selected['sha256']==lease.sha256
            and old['owner']==lease.owner and old['token']==lease.token
            and old['scope']=='workbook:'+before['workbook_id'],'exact live native owner/revision required')
    require(old['expires_at']>clock(),'expired lease requires separate original takeover reconciliation')
    evidence=validation(before,lease)
    require(record(path)==selected,'tracker changed during full continuation validation')
    count=life.validate_lifecycle(before)
    require(type(retain_sufficient)is bool,'explicit retain-live policy required')
    if retain_sufficient and old['expires_at']-clock()>=DURATION_NS:
        require(clock()>=old['heartbeat_at'],'clock precedes existing heartbeat')
        custody();require(record(path)==selected,'tracker changed before retaining live custody')
        directory.mkdir()
        receipt=keep(directory/'retained-live.json',dict(schema_version='fleet-retained-live-continuation-lease/v1',
            authority=authority_record,lease=asdict(lease),tracker=selected,validated_evidence=evidence,
            minimum_remaining_ns=DURATION_NS,observed_at_ns=clock(),original_expired_replacements=count,
            lease_operations=0,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False))
        require(record(path)==selected,'tracker changed while retaining live custody')
        return lease,receipt
    directory.mkdir()
    intent=keep(directory/'intent.json',dict(schema_version=SCHEMA,authority=authority_record,
        previous=asdict(lease),tracker=selected,validated_evidence=evidence,duration_ns=DURATION_NS,
        original_expired_replacements=count,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False))
    def exact(operation,target,current,request):
        return (operation=='renew' and target==str(path) and current==before
                and request['owner']==lease.owner and request['token']==lease.token
                and request['scope']=='workbook:'+before['workbook_id'] and request['duration_ns']==DURATION_NS)
    authority=life.RootAuthority('harbor-orchestrator',DURATION_NS,exact)
    custody();_fault('before_cas')
    result=life.lease_operation(path,authority=authority,operation='renew',lease_kind='task_agent',
        owner=lease.owner,scope='workbook:'+before['workbook_id'],token=lease.token,duration_ns=DURATION_NS,
        expected_revision=lease.revision,expected_sha256=lease.sha256,clock=clock)
    _fault('after_cas')
    current=replace(lease,revision=result['revision'],sha256=result['sha256']);after=read(record(path))
    require(current.revision==lease.revision+1 and life.validate_lifecycle(after)==count,
            'renewal changed original replacement consumption')
    for key in ('lane_state','bindings','generation_ids','promotion_ids'):
        require(after[key]==before[key],'renewal changed task history: '+key)
    require(after['handoffs'][:-1]==before['handoffs'] and len(after['handoffs'])==len(before['handoffs'])+1,
            'renewal changed earlier lifecycle history')
    event=after['handoffs'][-1]
    require(event['schema_version']==life.EVENT_SCHEMA and event['operation']=='renew'
            and event['before']==old and event['after']==after['leases']['task_agent']
            and event['prior_tracker_sha256']==selected['sha256'], 'exact original renewal event required')
    complete=keep(directory/'complete.json',dict(schema_version=SCHEMA,intent=intent,previous=asdict(lease),
        lease=asdict(current),tracker=record(path),duration_ns=DURATION_NS,original_expired_replacements=count,
        task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False))
    return current,complete


def validate_completed(*,life,read,record,path,previous,completion,before_binding,after_binding,authority_record):
    """Read one exact completed renewal through immutable before/after archives.

    This proves historical custody only. The owning worker must independently
    validate its current checkpoint/lease immediately before every transition.
    """
    import copy
    value=read(completion);intent=read(value['intent']);before=read(before_binding);after=read(after_binding)
    require(value['schema_version']==SCHEMA and value['previous']==asdict(previous)
            and value['duration_ns']==DURATION_NS and value['task_guards_changed']is False
            and value['semantic_budget_reset']is False and value['automatic_retry']is False,
            'completed continuation authority differs')
    require(Path(completion['path']).name=='complete.json' and Path(value['intent']['path'])==Path(completion['path']).parent/'intent.json','continuation journal differs')
    require(intent==dict(schema_version=SCHEMA,authority=authority_record,previous=asdict(previous),
        tracker=dict(path=str(path),sha256=before_binding['sha256']),validated_evidence=intent['validated_evidence'],duration_ns=DURATION_NS,
        original_expired_replacements=value['original_expired_replacements'],task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False),
        'renewal intent detached from original exact authority')
    require(value['tracker']==dict(path=str(path),sha256=after_binding['sha256'])
            and before_binding['sha256']==previous.sha256 and before['revision']==previous.revision,
            'renewal immutable tracker archives differ')
    old=before['leases']['task_agent'];new=after['leases']['task_agent']
    require((old['owner'],old['token'])==(previous.owner,previous.token) and old['scope']=='workbook:'+before['workbook_id'],
            'renewal original lease identity differs')
    count=life.validate_lifecycle(before);require(life.validate_lifecycle(after)==count==value['original_expired_replacements'],
            'renewal changed expired takeover consumption')
    require(len(after['handoffs'])==len(before['handoffs'])+1 and after['handoffs'][:-1]==before['handoffs'],
            'renewal changed retained history')
    event=after['handoffs'][-1]
    require(event['schema_version']==life.EVENT_SCHEMA and event['operation']=='renew' and event['before']==old and event['after']==new
            and event['prior_tracker_sha256']==before_binding['sha256'] and event['at']==after['updated_at']
            and old['heartbeat_at']<=event['at']<old['expires_at'] and new['heartbeat_at']==event['at']
            and new['expires_at']==event['at']+DURATION_NS and all(new[k]==old[k]for k in('owner','scope','token')),
            'completed event is not the original bounded same-owner renewal')
    expected=copy.deepcopy(before);expected['leases']['task_agent']=new;expected['handoffs']=after['handoffs'];expected['revision']+=1;expected['updated_at']=event['at']
    require(expected==after,'renewal changed task data or lane state')
    current=replace(previous,revision=after['revision'],sha256=after_binding['sha256'])
    require(value['lease']==asdict(current),'completed renewal typed lease differs')
    for row in intent['validated_evidence']:
        if row['path']==str(path):
            require(row['sha256']==before_binding['sha256'] and record(before_binding['path'])==before_binding,'pre-renewal tracker witness differs')
        else:require(record(row['path'])==row,'retained pre-renewal validation evidence changed')
    return current
