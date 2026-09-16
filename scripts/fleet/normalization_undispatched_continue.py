"""Guarded remaining normalization reviews; explicit pretransport recovery only.

Ends at genuine phase-one evidence. It does not pretend the later historical
refresh/publication subprocesses already have the required reader adapter.
"""
from pathlib import Path
from dataclasses import asdict
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys

SCHEMA='fleet-undispatched-normalization-continuation/v1'
SECONDS=3300
MEMORY=8*1024**3
NATIVE_PROOFS={
 '0173':'b2b9a69227d48e2e006571548d205c43765d877933f152f6d2a78da4f89693cd',
 '0303':'104a6103f112cbb750182c70eda7f7a19fba311911dc768e4c12ea4587bf56c1',
 '0077':'5fe238124e91860b9d6cd42fa7ed6a8d684663b53c3628db440c1d8eef29aa53'}
OWNERS={wb:f'hhu-server-{host}:slot-2:{wb}-normalization-002' for wb,host in [('0173',4),('0303',5),('0077',6)]}


def require(ok,message):
 if not ok:raise ValueError(message)


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
 require(binding(row['path'])==row,'bound evidence changed');return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
 with p.open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return binding(p)


def module(name,row):
 require(binding(row['path'])==row,'selected module changed')
 spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def load(bound):
 p=read(bound)
 require(set(p)=={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence',
   'old_helper','old_plan','executor_adapter','partition_adapter','model_gate','native_proof','central_proof',
   'prepared','original_custody','tracker','native_probe','home'} and p['schema_version']==SCHEMA,
   'closed continuation plan required')
 require(p['workbook']in NATIVE_PROOFS and p['native_proof']['sha256']==NATIVE_PROOFS[p['workbook']]
   and p['expected_previous_fence']==1 and p['owner']==OWNERS[p['workbook']],'only the reviewed original held attempts qualify')
 native=read(p['native_probe'])
 require(native['status']=='OWNING_SOURCE_AND_RETAINED_REVIEW_PREFIX_PASSED'
   and native['workbook']==p['workbook'] and native['models_dispatched']==0
   and native['claim_changed'] is False,'actual native source/prefix proof required')
 old=read(p['old_plan']);require(old['workbook']==p['workbook'],'original workbook differs')
 root=Path(old['code_root']);require(root.name==p['version'],'original code version differs')
 require(Path(p['home']).is_absolute() and not Path(p['home']).is_relative_to(root),'isolated continuation home required')
 sys.path.insert(0,str(root))
 helper=module('original_normalization_helper',p['old_helper']);dispatcher,env,P,selection,worker=helper.load(old)
 helper.configure_campaign(env[0],worker.campaign)
 require(env[0].SECONDS==SECONDS and env[0].MEMORY==MEMORY and worker.campaign.model_slots==1,'original resource/model guards differ')
 U=module('selected_undispatched_executor',p['executor_adapter']);R=module('selected_undispatched_partition',p['partition_adapter'])
 gate=module('scripts.fleet.model_gate',p['model_gate'])
 setattr(sys.modules['scripts.fleet'],'model_gate',gate)
 np=read(p['native_proof']);cp=read(p['central_proof']);raw=read(np['plan']);E=P.E
 require(raw['scope']['original_sha256']==p['source_sha256'],'original source differs')
 pc=E.PreparedCall(raw['request'],E.Policy(**raw['policy']),E.SemanticScope(**raw['scope']),raw['ordinal'],raw['limit'],raw['attempt_id'],tuple(raw['budget_evidence']),raw['response_mode'])
 require(pc.budget_evidence[-1]==p['prepared'],'only exact retained partitions may continue')
 campaign=E.Campaign(Path(raw['campaign_root']),Path(raw['slot_root']),raw['model_slots'])
 recovery=U.Recovery(E,pc,campaign,native_proof=p['native_proof'],central_proof=p['central_proof'])
 selected=R.facade(P,recovery,p['prepared'],partition_code=U.binding(P.__file__),continuation_evidence=(bound,p['model_gate'],p['old_helper']))
 return p,helper,env,worker,selected,recovery


def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'] and os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']
   and int(os.environ['HARBOR_FLEET_FENCE'])==p['expected_previous_fence']+1,'exact new fleet custody required')
 owner=dict(workbook=p['workbook'],owner=p['owner'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
 client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status=client.call('status')
 rows=[w for w in status['workbooks']if w['workbook']==p['workbook']]
 require(len(rows)==1 and rows[0]['state']=='active' and rows[0]['owner']==p['owner']
   and rows[0]['fence']==owner['fence'] and rows[0]['source_sha256']==p['source_sha256']
   and rows[0]['version']==p['version'] and status['model_limit']==status['publication_limit']==1,'live source/code/custody differs')
 client.call('heartbeat',lease_seconds=180,**owner)


def probe(bound):
 p,helper,env,worker,P,recovery=load(bound)
 require(not Path(p['home']).exists() and not recovery.root.exists(),'continuation intent already exists')
 require(binding(p['tracker']['path'])==p['tracker'],'initial tracker changed')
 lease=env[0].decode(read(p['original_custody'])['lease']);env[0].worker._active(worker,lease)
 require(shutil.disk_usage(read(p['old_plan'])['code_root']).free>=12*1024**3,'worker disk reserve unavailable')
 return dict(status='GUARDED_UNDISPATCHED_NORMALIZATION_READY',plan=bound,reader=P.undispatched_reader_witness,
   native_lease_live=True,tracker=p['tracker'],calls_dispatched=0,owning_operations=0,
   semantic_budget_reset=False,downstream_refresh_reader_required=True)


def child(intent_bound):
 intent=read(intent_bound);p,helper,env,worker,P,recovery=load(intent['plan']);active(p)
 require(intent['driver']==binding(__file__) and intent['seconds']==SECONDS and intent['memory']==MEMORY,'guarded driver changed')
 home=Path(intent_bound['path']).parent;base=env[0];lease=base._lease(worker,base.decode(intent['lease']),home)
 action=intent['action'];args=intent['arguments']
 if action=='dispatch':
  from xl_task_build import read_env_key
  credential=read_env_key(env[-1].env_file);require(bool(credential),'configured credential missing')
  P.dispatch(p['prepared'],args['index'],args['ordinal'],credential=credential)
  _,_,campaign,_,rows=P._current(p['prepared'])
  value=retain(home/'review.json',P._review(p['prepared'],args['index'],args['ordinal'],campaign,rows))
 elif action=='emit':value=P.emit_phase1(p['prepared'])
 else:raise ValueError('unsupported remaining normalization action')
 return retain(home/'result.json',dict(value=value,lease=base.encode(lease),automatic_retry=False))


def guarded(bound,action,args,lease,name,env):
 p=read(bound);home=Path(p['home'])/'stages'/name
 require(not home.exists(),'guarded stage exists; reconcile without replay')
 require(shutil.disk_usage(read(p['old_plan'])['code_root']).free>=MEMORY,'8-GiB reserve required')
 home.mkdir(parents=True);intent=retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),action=action,
   arguments=args,lease=lease,seconds=SECONDS,memory=MEMORY))
 from scripts.batch.hardened_runner import _run_in_process_group
 metrics={};rc=None;error=None
 try:
  with (home/'step.log').open('xb')as log:
   rc=_run_in_process_group([sys.executable,'-I','-B',str(Path(__file__).absolute()),'child','--plan',intent['path'],'--plan-sha256',intent['sha256']],
    stdout=log,timeout_seconds=SECONDS,grace_seconds=3,memory_limit_bytes=MEMORY,metrics=metrics,
    cwd=Path(read(p['old_plan'])['code_root']),env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 except Exception as exc:error=type(exc).__name__
 retain(home/'resource.json',dict(intent=intent,returncode=rc,failure_type=error,metrics=metrics,log=binding(home/'step.log'),automatic_retry=False))
 require(rc==0 and error is None,'remaining normalization stage stopped; reconcile')
 result=read(binding(home/'result.json'));return result['value'],result['lease']


def run(bound):
 p,helper,env,worker,P,recovery=load(bound);active(p);home=Path(p['home'])
 require(not home.exists() and not recovery.root.exists(),'continuation already attempted')
 require(binding(p['tracker']['path'])==p['tracker'],'initial tracker changed')
 lease=read(p['original_custody'])['lease'];env[0].worker._active(worker,env[0].decode(lease))
 home.mkdir(parents=True);retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 try:
  lease=env[0].encode(env[0]._lease(worker,env[0].decode(lease),home))
  m,_,campaign,_,rows=P._current(p['prepared']);first=[]
  for index in range(len(m['partitions'])):
   pc=P._prepared_call(p['prepared'],index,1);location=P.E.location(pc,campaign)
   if location.exists():
    review=P._review(p['prepared'],index,1,campaign,rows)
    require(review['valid'],'retained rejected first review requires explicit remaining-round reconciliation')
   else:
    result,lease=guarded(bound,'dispatch',dict(index=index,ordinal=1),lease,f'partition-{index:04d}-round-1',env)
    review=read(result)
   first.append(review)
  for index,review in enumerate(first):
   if not review['valid']:
    result,lease=guarded(bound,'dispatch',dict(index=index,ordinal=2),lease,f'partition-{index:04d}-round-2',env)
    require(read(result)['valid'],'original two-review normalization budget exhausted')
  emitted,lease=guarded(bound,'emit',{},lease,'emit',env)
  require(read(emitted)['status']=='phase1_evidence_ready','normalization phase1 did not qualify')
  result=dict(status='NORMALIZATION_PHASE1_READY',workbook=p['workbook'],plan=bound,phase1=emitted,
    lease=lease,reader=P.undispatched_reader_witness,automatic_retry=False,semantic_budget_reset=False,
    downstream_refresh_reader_required=True,publication_performed=False)
  return retain(home/'result.json',result)
 except Exception as exc:
  retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook=p['workbook'],plan=bound,
    failure_type=type(exc).__name__,automatic_retry=False,semantic_budget_reset=False))
  raise


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['probe','run','child']);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True)
 a=p.parse_args();value=globals()[a.action](dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256));print(json.dumps(value),flush=True)

if __name__=='__main__':main()
