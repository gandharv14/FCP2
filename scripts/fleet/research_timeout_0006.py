"""Exact retained 0006 research audit and timeout continuation evidence.

The two completed audit Calls remain consumed. Inspection invokes only owning
readers. The prospective expired-lease reader admits one exact stopped wall-time
record, retaining the original lifecycle's locks, CAS and replacement budget.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time

SCHEMA='0006-retained-research-timeout-profile/v1'
SOURCE='81953973e0bbde9508ba0c7ae53b5b725ffb988cf8cc072367e5d5bd0c4ec960'
VERSION='1857e0ce39d62f949149c1ad9d4b7a8e941153f6e7ec8c8b4a2ffe660b54b96a'
GUARD_SHA='6ad9fc7b951d622909dc14b02e2808744c8f79353aa0edb7e0d2ca3db63e1e9e'
TRACKER_SHA='de776c4bab82c97809a3a83facff8aa60d8f4eb9601a88e537600cff222e4f49'
FIELDS={'schema_version','workbook','source_sha256','version','original_plan','captures','config','code','code_root',
 'guard','guard_outcome','guard_intent','checkpoint','descriptor','tracker','prior_takeover','research','audit_complete',
 'audit_plan','prepared_calls','model_outcomes','model_calls','audit_limit','semantic_budget_reset','original_takeover_module','original_takeover_copy'}


def require(ok,message):
 if not ok:raise ValueError(message)


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink research evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
 require(type(row)is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'research evidence changed')
 return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
 p=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return binding(p)


def module(name,row):
 require(binding(row['path'])==row,'selected research implementation changed')
 spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def profile(row):
 p=read(row)
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0006'
  and p['source_sha256']==SOURCE and p['version']==VERSION and p['guard']['sha256']==GUARD_SHA
  and p['tracker']['sha256']==TRACKER_SHA and p['model_calls']==p['audit_limit']==2
  and p['semantic_budget_reset']is False,'only the reviewed exact0006 timeout qualifies')
 require(len(p['prepared_calls'])==len(p['model_outcomes'])==2,'both consumed audit Calls required')
 captured={}
 for entry in p['captures']:
  for item in read(entry)['files']:
   raw=item['text'].encode();require(hashlib.sha256(raw).hexdigest()==item['sha256']and len(raw)==item['size'],'captured bytes differ')
   captured[item['path']]=item
 for key in ('original_plan','config','guard','guard_outcome','guard_intent','descriptor','tracker','research','audit_complete','audit_plan'):
  b=p[key];require(b['path']in captured and captured[b['path']]['sha256']==b['sha256'],'profile differs from stopped native capture')
 require(binding(p['original_takeover_module']['path'])==p['original_takeover_module']
  and binding(p['original_takeover_copy']['path'])==p['original_takeover_copy']
  and p['original_takeover_copy']['sha256']==p['original_takeover_module']['sha256'],'original takeover producer differs')
 guard=read(p['guard']);intent=read(p['guard_intent']);outcome=read(p['guard_outcome'])
 require(set(guard)=={'intent','log','metrics','returncode','failure'}and guard['returncode']is None
  and guard['failure']=={'type':'ConversionTimeout','message_sha256':'4a71c32674908a31e9df52d0938f3a00707c49e75029d252df1d601e1970ada3'}
  and guard['metrics']=={'limit_reason':'wall_time','peak_rss_bytes':896258048}and guard['intent']==p['guard_intent'],
  'exact original wall-time guard required')
 require(binding(guard['log']['path'])==guard['log'],'retained empty guard log differs')
 require(intent['schema_version']=='harbor-coordinator-step/v1'and intent['action']=='step'
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8*1024**3 and intent['dispatch']is True
  and intent['arguments']==dict(captures=None,checkpoint=p['checkpoint'],descriptor=p['descriptor']),
  'original guard action/limits/checkpoint differ')
 require(outcome['status']=='RECONCILIATION_REQUIRED'and outcome['resource']==p['guard']and outcome['automatic_retry']is False
  and outcome.get('result')is None,'partial timeout outcome must remain unresolved')
 return p


def context(row):
 p=profile(row);old=read(p['original_plan']);meta=read(old['namespace_adapter'])
 for entry in meta['files']:require(binding(entry['path'])==entry,'original namespace adapter changed')
 os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN']=p['original_plan']['path']
 os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256']=p['original_plan']['sha256']
 sys.path.insert(0,p['code_root'])
 dispatcher=module('selected_0006_research_dispatcher',meta['dispatcher']);job=read(old['authoring_job']);env=dispatcher.setup(job)
 require(env[6]==Path(p['code_root']) and env[5]['code']==p['code'] and job['publish']is False,'original task code/namespace differs')
 C=env[0];w,initial,controls=C.selected(p['descriptor']);C._campaign(w.campaign)
 require(w.bootstrap.workbook=='0006' and w.campaign.model_slots==1 and C.SECONDS==3300 and C.MEMORY==8*1024**3,'owning source/guards differ')
 return p,old,dispatcher,job,env,w


def validate_audit(p,env,w):
 C=env[0];W=C.worker;E=C.executor;bridge=W.audit_bridge
 actual=[]
 for ordinal,(prepared,outcome)in enumerate(zip(p['prepared_calls'],p['model_outcomes']),1):
  pc=bridge.prepared_from(read(prepared));require(pc.ordinal==ordinal and pc.limit==2
   and pc.scope.workbook_id=='0006' and pc.scope.original_sha256==SOURCE and pc.scope.lane=='harbor-research-auditor',
   'consumed audit ordinal/source differs')
  result=E.inspect(pc,w.campaign)
  require(result.status=='completed'and result.call is not None and result.outcome==outcome,'audit Call remains unresolved')
  actual.append(result.outcome)
 complete=bridge.validate_completed(w.run/'audit-bridge',w.bootstrap.run/'audit'/'0006-variable-sources',p['audit_plan'],w.bootstrap.model_policy,w.campaign)
 require(json.dumps(complete,sort_keys=True)==json.dumps(read(p['audit_complete']),sort_keys=True),'retained complete audit differs from owning validation')
 selection=W._read_checkpoint(w,'research')
 require(binding(w.run/'research.json')==p['research'] and all(selection.content.bindings[k]==v for k,v in complete['bindings'].items()),
  'retained research checkpoint differs from actual audit')
 require(selection.content.bindings['original_raw_source']['sha256']==SOURCE,'research source differs')
 return selection,actual


def allowed_timeout(selection,guard,profile_binding):
 """Only the exactly witnessed stopped guard changes the original T predicate."""
 p=profile(profile_binding)
 require(selection.workbook=='0006'and selection.worker_kind=='environment'and selection.guard==p['guard']
  and selection.checkpoint==p['checkpoint']and selection.config==p['config']and selection.tracker==p['tracker']
  and selection.prior_completion==p['prior_takeover'] and selection.duration_ns==7200*10**9,
  'timeout selection differs from retained source/lease lineage')
 require(guard==read(p['guard']),'timeout body differs')
 # These exact sealed terminal outcomes are additional to the later original
 # T checkpoint, source, old-owner/token, lock and lifecycle checks.
 for row in p['model_outcomes']:require(read(row)['status']=='completed','an audit Call is uncertain')
 require(binding(p['research']['path'])==p['research'] and binding(p['audit_complete']['path'])==p['audit_complete'],
  'retained audit/research changed')
 return True


def probe(row):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting probe required')
 p,old,dispatcher,job,env,w=context(row);C=env[0];W=C.worker;before=read(p['tracker'])
 require(before['revision']==4 and before['bindings']['original_raw_source']['sha256']==SOURCE
  and before['lane_state']['harbor-research-auditor']['state']=='pending'
  and all(before['lane_state'][x]['state']=='passed'for x in ('harbor-source-warden','harbor-segmentation-analyst')),
  'exact pre-research lane state required')
 def forbidden(*a,**k):raise ValueError('owning/model operation forbidden in read-only research probe')
 C.executor.execute=forbidden;C.executor._open=forbidden;W.audit_bridge.run=forbidden
 W.HarborLaneLifecycle.complete=forbidden;W.HarborLaneLifecycle.record_failure=forbidden
 from scripts.batch import harbor_tracker_lifecycle as life
 life.lease_operation=forbidden
 selection,calls=validate_audit(p,env,w)
 start=time.monotonic();result=W.lanes.evaluate_completed_gate(before,W.lanes.CompletionEvidence('research',selection),W._context(w))
 require(binding(p['tracker']['path'])==p['tracker'],'read-only gate changed tracker')
 require(result['passed'],'actual retained research gate rejected')
 return dict(status='RETAINED_RESEARCH_OWNING_GATE_PROBE_PASSED',profile=row,research=p['research'],audit_complete=p['audit_complete'],
  calls=calls,tracker=p['tracker'],gate=result,gate_elapsed_seconds=time.monotonic()-start,
  native_lease_expired=before['leases']['task_agent']['expires_at']<=time.time_ns(),
  expired_replacements_consumed=life.validate_lifecycle(before),models_dispatched=0,owning_operations=0,
  audit_bridge_reentered=False,semantic_budget_reset=False)


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['probe']);p.add_argument('--profile',required=True);p.add_argument('--sha256',required=True)
 a=p.parse_args();value=probe(dict(path=a.profile,sha256=a.sha256));print(json.dumps(value),flush=True)

if __name__=='__main__':main()
