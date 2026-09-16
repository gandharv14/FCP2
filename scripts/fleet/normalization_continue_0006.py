"""Continue exact retained 0006 research through original guarded owning code.

The expired-lease transaction is explicit and once-only. Its new implementation
is declared rather than installed over the original snapshot. The completed audit and normalization Calls are read-only; the first guarded
operation commits the retained phase1 gate without reentering a producer.
"""
from pathlib import Path
from dataclasses import asdict
from types import FunctionType, ModuleType, SimpleNamespace
import argparse,hashlib,importlib.util,json,os,shutil,sys,time

SCHEMA='0006-retained-normalization-continuation/v1'
FIELDS={'schema_version','workbook','source_sha256','version','owner','previous_owner','expected_previous_fence','home',
 'profile','reader_helper','reader_builder','takeover_reader','campaign_adapter','model_gate','stopped_proof','native_gate_probe','native_takeover_probe','original_research_helper','original_research_profile','capacity_policy'}
SOURCE='81953973e0bbde9508ba0c7ae53b5b725ffb988cf8cc072367e5d5bd0c4ec960'
VERSION='1857e0ce39d62f949149c1ad9d4b7a8e941153f6e7ec8c8b4a2ffe660b54b96a'

def require(ok,message):
 if not ok:raise ValueError(message)

def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink continuation evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
 require(type(row)is dict and set(row)=={'path','sha256'}and binding(row['path'])==row,'continuation evidence changed')
 return json.loads(Path(row['path']).read_bytes())

def module(name,row):
 require(binding(row['path'])==row,'continuation code changed')
 s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def load(bound):
 p=read(bound);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0006'
  and p['source_sha256']==SOURCE and p['version']==VERSION and p['expected_previous_fence']==3
  and p['previous_owner']=='hhu-server-8:slot-2:0006-research-002'
  and p['owner']=='hhu-server-8:slot-2:0006-normalization-003','closed exact 0006 continuation required')
 require(Path(p['home'])==Path(bound['path']).parents[2]/'workbooks/0006/normalization-continuation-003','unexpected continuation home')
 H=module('selected_0006_normalization_reader',p['reader_helper']);q=H.profile(p['profile'])
 oldH=module('original_0006_research_reader',p['original_research_helper']);original,old,d,job,env,w=oldH.context(p['original_research_profile'])
 require(q['original_profile']==p['original_research_profile'],'retained research lineage differs')
 require((q['source_sha256'],q['version'])==(SOURCE,VERSION),'original source/version differs')
 B=module('selected_0006_takeover_builder',p['reader_builder']);T=B.install(p['takeover_reader'])
 meta=read(p['takeover_reader']);require(meta['profile']==p['profile']and meta['helper']==p['reader_helper'],'takeover profile differs')
 A=module('selected_0006_research_campaign',p['campaign_adapter'])
 require(p['model_gate']['sha256']=='5293c91e3adb019cd86d0bd876ef8f3bf20b82c2ed4a42e9de9eaa43809b6bd5','reviewed global model wait policy required')
 require(p['capacity_policy']['sha256']=='17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4','reviewed capacity policy differs')
 capacity=module('scripts.fleet.capacity_policy',p['capacity_policy'])
 gate=module('scripts.fleet.model_gate',p['model_gate']);import scripts.fleet
 scripts.fleet.capacity_policy=capacity
 scripts.fleet.model_gate=gate
 stop=read(p['stopped_proof']);require(p['stopped_proof']['sha256']=='6b1367e8e3adcc6c232641bfe0cf313066ac2e479f710df21ae9d372c72babec','exact root stopped proof required')
 for key,status in [('native_gate_probe','ACTUAL_RETAINED_NORMALIZATION_PHASE1_GATE_VALIDATED'),('native_takeover_probe','EXACT_EXTERNAL_NORMALIZATION_TIMEOUT_TAKEOVER_THREE_PROBE_PASSED')]:
  value=read(p[key]);require(value['status']==status and value['profile']==p['profile']and value['models_dispatched']==value['owning_operations']==0,'actual native zero-model proof required')
 os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
 return p,H,q,old,d,job,env,w,T,A

def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0006'and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
  and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==4,'exact live continuation claim required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status=c.call('status')
 rows=[r for r in status['workbooks']if r['workbook']=='0006']
 require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==p['owner']and rows[0]['fence']==4
  and rows[0]['source_sha256']==SOURCE and rows[0]['version']==VERSION and rows[0]['expires']>time.time(),'live source/limits differ')
 module('selected0006capacity',p['capacity_policy']).validate_occupancy(status)
 c.call('heartbeat',workbook='0006',owner=p['owner'],fence=4,token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)

def selection(p,q,env,T):
 return T.Selection(Path(env[5]['run_root']),'0006',q['tracker'],'environment',q['guard'],q['checkpoint'],p['owner'],config=q['config'],prior_completion=q['prior_takeover'])

def evidence(p):return [p[k]for k in ('profile','reader_helper','reader_builder','takeover_reader','model_gate','stopped_proof','original_research_helper','original_research_profile','capacity_policy')]

def dispatcher_selected(d,env,A,bound,p):
 base,I,*_=env;common=dict(base=base,reader_plan=bound,child_wrapper=binding(__file__),reader_evidence=evidence(p))
 b=SimpleNamespace(**base.__dict__);i=SimpleNamespace(**I.__dict__)
 b.run=A.select_run(base,kind='environment',producer_code=binding(base.__file__),**common)
 i.run=A.select_run(I,kind='instruction',producer_code=binding(I.__file__),**common)
 renew=A.select_run(d,kind='renewal',producer_code=binding(d.__file__),**common);b._run_in_process_group=renew.declared_runner
 clone=ModuleType(d.__name__);clone.__dict__.update(d.__dict__)
 for name,f in d.__dict__.items():
  if isinstance(f,FunctionType)and f.__globals__ is d.__dict__:
   copied=FunctionType(f.__code__,clone.__dict__,f.__name__,f.__defaults__,f.__closure__);copied.__kwdefaults__=f.__kwdefaults__;clone.__dict__[name]=copied
 original=d.setup
 def setup(job):
  current=original(job);require(current[0]is base and current[1]is I,'owning producer changed');return(b,i,*current[2:])
 clone.setup=setup;return clone,(b,i,*env[2:])

def normalization_commit(C,w,lease):
 """Original W.advance owns the phase1 CAS; no source/model producer is callable."""
 def forbidden(*a,**k):raise ValueError('retained research cannot dispatch or reopen an audit')
 C.executor.execute=forbidden;C.executor._open=forbidden;C.worker.audit_bridge.run=forbidden
 C.worker.normalization.prepare=C.worker.normalization.emit_phase1=C.worker.normalization.dispatch=forbidden
 require((w.run/'normalization_phase1.json').is_file(),'retained normalization checkpoint absent')
 result=C.worker.advance(w,lease,dispatch=False)
 # Preserve the actual owning outcome, including a genuine gate failure.
 return {**result,'lease':asdict(result['lease']),'selection':C.content_codec.encode(result['selection'])}

def normalization_child(bound,lease_record,intent):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);home=Path(p['home'])
 require(intent==binding(home/'normalization-gate/intent.json'),'guard intent differs')
 expected=dict(schema_version='0006-retained-normalization-gate/v1',plan=bound,lease=lease_record,driver=binding(__file__),timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=False,automatic_retry=False)
 require(read(intent)==expected,'closed zero-model guard required')
 completed=read(lease_record);_,_,lease=T.validate_completion(completed['takeover'])
 require(completed['lease']==asdict(lease),'takeover lease differs')
 require(H.binding(w.run/'normalization_phase1.json')==read(p['native_gate_probe'])['checkpoint'],'retained phase1 checkpoint changed')
 result=normalization_commit(env[0],w,lease);return A.retain(home/'normalization-gate/result.json',result)

def owning_child(bound,kind,intent):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);base,I,*_=env
 producer={'environment':base,'instruction':I,'renewal':d}[kind]
 A.validate_child(producer,base=base,kind=kind,reader_plan=bound,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p))
 return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)

def probe(bound):
 p,H,q,old,d,job,env,w,T,A=load(bound)
 require(not Path(p['home']).exists(),'continuation already attempted')
 s=selection(p,q,env,T);before,lease,_,_=T._recipe(s);T._prior_chain(s,before)
 require(T.life.validate_lifecycle(before)==2 and lease['expires_at']<=time.time_ns(),'only expired replacement three qualifies')
 dispatcher_selected(d,env,A,bound,p)
 d.operation_namespace(env[5],str(Path(env[5]['run_root'])/'dispatcher-v2/fleet-normalization-timeout-003'))
 require(H.binding(w.run/'normalization_phase1.json')==read(p['native_gate_probe'])['checkpoint'],'retained normalization checkpoint differs')
 return dict(status='RETAINED_NORMALIZATION_CONTINUATION_READY',plan=bound,tracker=q['tracker'],models_dispatched=0,owning_operations=0,semantic_budget_reset=False)

def run(bound):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);home=Path(p['home']);C=env[0]
 require(not home.exists()and binding(q['tracker']['path'])==q['tracker'],'continuation exists or tracker changed; reconcile')
 require(shutil.disk_usage(env[6]).free>=12*1024**3,'worker disk reserve unavailable')
 d.operation_namespace(env[5],str(Path(env[5]['run_root'])/'dispatcher-v2/fleet-normalization-timeout-003'))
 home.mkdir(parents=True,mode=0o700);A.retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 try:
  s=selection(p,q,env,T);lease,takeover=T.replace_expired(s,Path(env[5]['run_root'])/'lease-takeovers/fleet-normalization-timeout-003')
  lease_record=A.retain(home/'takeover-result.json',dict(lease=asdict(lease),takeover=takeover))
  stage=home/'normalization-gate';stage.mkdir();intent=A.retain(stage/'intent.json',dict(schema_version='0006-retained-normalization-gate/v1',plan=bound,lease=lease_record,driver=binding(__file__),timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=False,automatic_retry=False))
  command=[sys.executable,'-I','-B',__file__,'normalization-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],'--lease',lease_record['path'],'--lease-sha256',lease_record['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
  metrics={};rc=None;failure=None;start=time.monotonic()
  try:
   with(stage/'step.log').open('xb')as log:rc=C._run_in_process_group(command,cwd=C.ROOT,stdout=log,timeout_seconds=3300,memory_limit_bytes=8*1024**3,grace_seconds=3,metrics=metrics)
  except Exception as exc:failure=type(exc).__name__
  resource=A.retain(stage/'resource.json',dict(intent=intent,command=command,returncode=rc,failure_type=failure,metrics=metrics,elapsed_seconds=time.monotonic()-start))
  require(rc==0 and failure is None,'normalization phase1 guard requires reconciliation')
  actual=binding(stage/'result.json');result=read(actual)
  require(result['status']=='STEP_COMPLETE'and result['step']=='normalization_phase1','actual normalization phase1 gate did not complete')
  proof=H.read(q['normalization_proof']);require(all(binding(x['path'])==x for x in proof['call'].values()),'retained normalization Call changed')
  d,selected=dispatcher_selected(d,env,A,bound,p)
  legacy=A.retain(home/'legacy.json',dict(worker=C.worker.bootstrap.serial(asdict(w)),lease=asdict(lease)))
  outcome=selected[0].run('import',dict(legacy=legacy,checkpoint=actual),env[-1],home/'import')
  value=read(outcome);require(value['status']=='COMPLETED_OPERATION','owning import requires reconciliation')
  checkpoint=value['result'];imported=read(checkpoint);require(imported['status']=='IMPORTED','owning import did not complete')
  outcome=selected[0].run('step',dict(descriptor=imported['descriptor'],checkpoint=checkpoint,captures=None),env[-1],home/'bridge-step',dispatch=True)
  d.successful(outcome,selected)
  nextjob={**job,'outcome':outcome,'operations':str(Path(env[5]['run_root'])/'dispatcher-v2/fleet-normalization-timeout-003')}
  job_binding=A.retain(home/'authoring-job.json',nextjob);d.run(job_binding)
  hold=read(binding(Path(nextjob['operations'])/'hold.json'));checkpoint,result=d.successful(hold['outcome'],selected)
  require(result['status']=='PUBLICATION_READY','remaining authoring requires reconciliation')
  return A.retain(home/'result.json',dict(status='PUBLICATION_READY',workbook='0006',plan=bound,job=job_binding,config=job['config'],outcome=hold['outcome'],checkpoint=checkpoint,takeover=takeover,normalization_gate=actual,normalization_resource=resource,publication_performed=False,automatic_retry=False,semantic_budget_reset=False))
 except Exception as exc:
  A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0006',plan=bound,failure_type=type(exc).__name__,automatic_retry=False,semantic_budget_reset=False));raise

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','normalization-child','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True)
 a.add_argument('--lease');a.add_argument('--lease-sha256');a.add_argument('--intent');a.add_argument('--intent-sha256');a.add_argument('--kind',choices=['environment','instruction','renewal']);v=a.parse_args();bound=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
 if v.action=='normalization-child':value=normalization_child(bound,dict(path=v.lease,sha256=v.lease_sha256),dict(path=v.intent,sha256=v.intent_sha256))
 elif v.action=='owning-child':value=owning_child(bound,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
 else:value=globals()[v.action](bound)
 print(json.dumps(value),flush=True)
if __name__=='__main__':main()
