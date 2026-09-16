"""Explicit bounded publication admission wait; owning quality guards are unchanged."""
import time
SCHEMA='harbor-fleet-publication-wait/v1'
AUTHORIZATION_SHA256='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
FIELDS={'schema_version','workbook','source_sha256','maximum_wait_seconds','authorization','lease_tracker'}
def configuration(plan,*,checked,binding,require):
 row=plan.get('publication_wait')
 if row is None:return dict(maximum_wait_seconds=3300,policy=None)
 p=checked(row)
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['maximum_wait_seconds']==120 and type(p['maximum_wait_seconds'])is int,'closed authorized publication wait required')
 require(plan['schema_version']=='harbor-fleet-remote-publication/v1' and p['workbook']==plan['workbook'] and p['source_sha256']==plan['source_sha256'],'publication wait detached from task')
 require(p['authorization']['sha256']==AUTHORIZATION_SHA256 and binding(p['authorization']['path'])==p['authorization'],'publication wait authorization changed')
 require(set(p['lease_tracker'])=={'path','sha256'},'bound queued lease tracker required')
 return dict(maximum_wait_seconds=120,policy=row,value=p)
def enforce_after_slot(selected,*,checked,require,now_ns=None):
 if selected['policy']is None:return None
 p=selected['value'];tracker=checked(p['lease_tracker']);now=time.time_ns()if now_ns is None else now_ns
 require(tracker['workbook_id']==p['workbook'] and tracker['bindings']['original_raw_source']['sha256']==p['source_sha256'] and tracker['execution_state']=='publication_queued' and tracker['terminal_verdict']=='PUBLICATION_READY' and tracker['promotion_failure']is None,'queued publication identity changed')
 lease=tracker['leases']['task_agent']
 require(isinstance(lease,dict) and type(lease.get('expires_at'))is int and lease['expires_at']-now>3700*10**9,'insufficient native lease after publication slot acquired')
 return dict(policy=selected['policy'],remaining_seconds=(lease['expires_at']-now)/10**9)
