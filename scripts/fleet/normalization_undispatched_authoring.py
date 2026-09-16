"""Declared retained-normalization reader through original owning authoring.

This successor consumes a real NORMALIZATION_PHASE1_READY result. It does not
restart partition dispatch. Original campaign functions own refresh, each
remaining step, review limits, native runtime and the publication-ready gate.
"""
from pathlib import Path
from dataclasses import asdict, replace
from types import FunctionType, ModuleType, SimpleNamespace
import argparse
import importlib.util
import hashlib
import json
import os
import shutil
import sys

SCHEMA='fleet-undispatched-normalization-authoring/v1'
HOSTS={'0173':4,'0303':5,'0077':6}
NORMALIZATION_DRIVER_SHA='9389826dcc71214e07711f4e289b48b87b5e361b338f5acd53d3c5401568fd10'


def require(ok,message):
 if not ok:raise ValueError(message)


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink reader evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
 require(binding(row['path'])==row,'reader evidence changed');return json.loads(Path(row['path']).read_bytes())


def module(name,row):
 require(binding(row['path'])==row,'reader implementation changed')
 spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def load(bound):
 p=read(bound)
 require(set(p)=={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence','home',
  'normalization_plan','normalization_driver','phase1_result','campaign_adapter','tracker','stopped_proof'}
  and p['schema_version']==SCHEMA,'closed owning reader selection required')
 require(p['workbook']in {'0173','0303','0077'} and p['expected_previous_fence']==2
  and p['owner']==f'hhu-server-{HOSTS[p["workbook"]]}:slot-2:{p["workbook"]}-normalization-003',
  'only the completed selected phase1 may continue')
 # Each plan binds the previously reviewed immutable normalization driver;
 # its own closed loader authenticates full original code/source/proof ancestry.
 require(p['normalization_driver']['sha256']==NORMALIZATION_DRIVER_SHA,'only the reviewed frozen phase1 producer may continue')
 D=module('completed_undispatched_normalization',p['normalization_driver'])
 n,helper,env,worker,P,recovery=D.load(p['normalization_plan'])
 require((n['workbook'],n['source_sha256'],n['version'])==(p['workbook'],p['source_sha256'],p['version']),
  'completed normalization identity differs')
 phase=read(p['phase1_result'])
 require(set(phase)=={'status','workbook','plan','phase1','lease','reader','automatic_retry','semantic_budget_reset',
  'downstream_refresh_reader_required','publication_performed'} and phase['status']=='NORMALIZATION_PHASE1_READY'
  and phase['workbook']==p['workbook'] and phase['plan']==p['normalization_plan']
  and phase['reader']==P.undispatched_reader_witness and phase['automatic_retry']is False
  and phase['semantic_budget_reset']is False and phase['publication_performed']is False
  and phase['downstream_refresh_reader_required']is True,'real unchanged phase1 completion required')
 require(Path(p['phase1_result']['path'])==Path(n['home'])/'result.json','phase1 result is outside owning home')
 emitted=read(phase['phase1']);require(emitted['status']=='phase1_evidence_ready','phase1 did not qualify')
 # The facade executes the original semantic/coverage/independence validator.
 # Equality is canonical because retained JSON is the serialization boundary.
 actual=P.aggregate(n['prepared']);retained=read(emitted['normalization_partitioned_review'])
 require(json.dumps(actual,sort_keys=True)==json.dumps(retained,sort_keys=True),'retained aggregate differs from real Calls')
 original=sys.modules[P.__name__]
 R=sys.modules['selected_undispatched_partition'];R.install(original,P)
 A=module('selected_undispatched_campaign',p['campaign_adapter'])
 stopped=read(p['stopped_proof'])
 require(stopped.get('status')=='UNDISPATCHED_PHASE1_STOPPED_RECONCILED'
  and stopped.get('workbook')==p['workbook'] and stopped.get('phase1_result')==p['phase1_result']
  and stopped.get('tracker')==p['tracker'] and stopped.get('central_fence')==2
  and stopped.get('active_processes')==[] and stopped.get('automatic_retry')is False,
  'exact stopped phase1 custody proof required')
 old=read(n['old_plan']);job=read(old['original_job'])
 dispatcher=module('completed_partition_dispatcher',job['driver'])
 require(binding(dispatcher.__file__)==job['driver'],'original dispatcher changed')
 return p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted


def evidence(p):
 return [p[k]for k in ('normalization_plan','normalization_driver','phase1_result','campaign_adapter','stopped_proof')]


def selected_dispatcher(dispatcher,env,A,bound,p):
 base,C,*_=env;common=dict(base=base,reader_plan=bound,child_wrapper=binding(__file__),reader_evidence=evidence(p))
 base_run=A.select_run(base,kind='environment',producer_code=binding(base.__file__),**common)
 instruction_run=A.select_run(C,kind='instruction',producer_code=binding(C.__file__),**common)
 renewal=A.select_run(dispatcher,kind='renewal',producer_code=binding(dispatcher.__file__),**common)
 bproxy=SimpleNamespace(**base.__dict__);bproxy.run=base_run;bproxy._run_in_process_group=renewal.declared_runner
 iproxy=SimpleNamespace(**C.__dict__);iproxy.run=instruction_run
 clone=ModuleType(dispatcher.__name__);clone.__dict__.update(dispatcher.__dict__)
 for name,value in dispatcher.__dict__.items():
  if isinstance(value,FunctionType)and value.__globals__ is dispatcher.__dict__:
   f=FunctionType(value.__code__,clone.__dict__,value.__name__,value.__defaults__,value.__closure__)
   f.__kwdefaults__=value.__kwdefaults__;clone.__dict__[name]=f
 original_setup=dispatcher.setup
 def setup(job):
  current=original_setup(job)
  require(current[0]is base and current[1]is C,'owning dispatcher imported a different producer')
  return (bproxy,iproxy,*current[2:])
 clone.setup=setup
 return clone,(bproxy,iproxy,*env[2:])


def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'] and os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']
  and int(os.environ['HARBOR_FLEET_FENCE'])==3,'exact owning continuation custody required')
 owner=dict(workbook=p['workbook'],owner=p['owner'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=3)
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status=c.call('status')
 rows=[r for r in status['workbooks']if r['workbook']==p['workbook']]
 require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==p['owner'] and rows[0]['fence']==3
  and rows[0]['source_sha256']==p['source_sha256']and rows[0]['version']==p['version']
  and status['model_limit']==status['publication_limit']==1,'live source/claim/global limits differ')
 c.call('heartbeat',lease_seconds=180,**owner)


def owning_child(bound,kind,intent):
 p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=load(bound);active(p)
 base,C,*_=env;producer={'environment':base,'instruction':C,'renewal':dispatcher}[kind]
 A.validate_child(producer,base=base,kind=kind,reader_plan=bound,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p))
 # Original functions revalidate the complete original intent, source, lease,
 # capabilities, prior checkpoint and budgets before any owning operation.
 return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)


def probe(bound):
 p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=load(bound)
 require(not Path(p['home']).exists(),'owning continuation already attempted')
 require(binding(p['tracker']['path'])==p['tracker'],'initial tracker changed')
 env[0].worker._active(worker,env[0].decode(phase['lease']))
 selected_dispatcher(dispatcher,env,A,bound,p)
 require(shutil.disk_usage(read(n['old_plan'])['code_root']).free>=12*1024**3,'worker reserve unavailable')
 return dict(status='UNDISPATCHED_PHASE1_OWNING_READER_READY',plan=bound,reader=phase['reader'],calls_dispatched=0,
  owning_operations=0,original_snapshot_unchanged=True,semantic_budget_reset=False)


def run(bound):
 p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=load(bound);active(p)
 home=Path(p['home']);require(not home.exists(),'continuation exists; reconcile without replay')
 require(binding(p['tracker']['path'])==p['tracker'],'initial tracker changed')
 require(shutil.disk_usage(read(n['old_plan'])['code_root']).free>=12*1024**3,'worker reserve unavailable')
 base=env[0];base.worker._active(worker,base.decode(phase['lease']))
 home.mkdir(parents=True,mode=0o700);A.retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 dispatcher,selected_env=selected_dispatcher(dispatcher,env,A,bound,p);selected_base=selected_env[0]
 try:
  lease=base.encode(base._lease(worker,base.decode(phase['lease']),home))
  oldplan=read(n['old_plan']);old=base.content_codec.decode(read(oldplan['research'])['selection']);content=old.content
  r=P.selection(n['prepared']).run
  bindings={**content.bindings,'normalization_partitioned_review':emitted['normalization_partitioned_review'],
   'normalization_draft':binding(r/'draft.json'),'normalizer':binding(r/f'normalize_{p["workbook"]}.py'),
   'normalization_prepared_manifest':n['prepared'],'normalized_phase1':emitted['phase1'],
   'normalized_final':binding(r/'normalized.json'),'normalization_report':binding(r/'normalization_report.json'),
   'normalization_exclusions':binding(r/'exclusions.json')}
  current=replace(old,content=replace(content,bindings=bindings,normalization_run=r,normalization_prepared=n['prepared'],normalization_call=None))
  legacy=A.retain(home/'legacy.json',dict(worker=base.worker.bootstrap.serial(asdict(worker)),lease=lease))
  arguments=dict(legacy=legacy,checkpoint=oldplan['research'],current=A.retain(home/'current.json',base.content_codec.encode(current)),lease=A.retain(home/'lease.json',lease))
  active(p);outcome=selected_base.run('refresh',arguments,env[-1],home/'refresh')
  value=read(outcome);require(value['status']=='COMPLETED_OPERATION','refresh requires reconciliation')
  checkpoint=value['result'];refreshed=read(checkpoint);require(refreshed['status']=='IMPORTED','refresh did not import')
  outcome=selected_base.run('step',dict(descriptor=refreshed['descriptor'],checkpoint=checkpoint,captures=None),env[-1],home/'bridge-step',dispatch=True)
  dispatcher.successful(outcome,selected_env)
  job={**read(oldplan['original_job']),'outcome':outcome,'operations':str(Path(env[5]['run_root'])/'dispatcher-v2'/home.name)}
  job_binding=A.retain(home/'authoring-job.json',job);dispatcher.run(job_binding)
  held=read(binding(Path(job['operations'])/'hold.json'));checkpoint,result=dispatcher.successful(held['outcome'],selected_env)
  require(result['status']=='PUBLICATION_READY','remaining owning authoring requires reconciliation')
  sidecars=[binding(x)for root in (home,Path(job['operations']))for x in sorted(root.rglob('declared-runtime-adapter.json'))]
  return A.retain(home/'result.json',dict(status='PUBLICATION_READY',workbook=p['workbook'],plan=bound,job=job_binding,
   config=job['config'],outcome=held['outcome'],checkpoint=checkpoint,runtime_adapters=sidecars,
   publication_performed=False,automatic_retry=False,semantic_budget_reset=False))
 except Exception as exc:
  A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook=p['workbook'],plan=bound,
   failure_type=type(exc).__name__,automatic_retry=False,publication_performed=False,semantic_budget_reset=False))
  raise


def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe','run','owning-child'])
 parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True)
 parser.add_argument('--kind',choices=['environment','instruction','renewal']);parser.add_argument('--intent');parser.add_argument('--intent-sha256')
 a=parser.parse_args();bound=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256)
 if a.action=='owning-child':value=owning_child(bound,a.kind,dict(path=a.intent,sha256=a.intent_sha256))
 else:value=globals()[a.action](bound)
 print(json.dumps(value),flush=True)

if __name__=='__main__':main()
