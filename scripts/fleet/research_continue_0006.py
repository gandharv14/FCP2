"""Continue exact retained 0006 research through original guarded owning code.

The expired-lease transaction is explicit and once-only. Its new implementation
is declared rather than installed over the original snapshot. The two completed
audit Calls are read-only; the first guarded operation commits their real gate.
"""
from pathlib import Path
from dataclasses import asdict
from types import FunctionType, ModuleType, SimpleNamespace
import argparse,hashlib,importlib.util,json,os,shutil,sys,time

SCHEMA='0006-retained-research-continuation/v1'
FIELDS={'schema_version','workbook','source_sha256','version','owner','previous_owner','expected_previous_fence','home',
 'profile','reader_helper','reader_builder','takeover_reader','campaign_adapter','model_gate','stopped_proof','native_gate_probe','native_takeover_probe'}
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
  and p['source_sha256']==SOURCE and p['version']==VERSION and p['expected_previous_fence']==2
  and p['previous_owner']=='hhu-server-8:slot-2:0006-remaining-environment-001'
  and p['owner']=='hhu-server-8:slot-2:0006-research-002','closed exact 0006 continuation required')
 require(Path(p['home'])==Path(bound['path']).parents[2]/'workbooks/0006/research-continuation-002','unexpected continuation home')
 H=module('selected_0006_research_reader',p['reader_helper']);q,old,d,job,env,w=H.context(p['profile'])
 require((q['source_sha256'],q['version'])==(SOURCE,VERSION),'original source/version differs')
 B=module('selected_0006_takeover_builder',p['reader_builder']);T=B.install(p['takeover_reader'])
 meta=read(p['takeover_reader']);require(meta['profile']==p['profile']and meta['helper']==p['reader_helper'],'takeover profile differs')
 A=module('selected_0006_research_campaign',p['campaign_adapter'])
 require(p['model_gate']['sha256']=='265eaa8a4b9b5881d09c50b1d1b373029ea3820bd46b7372eab122322f1a7c38','reviewed global model wait policy required')
 gate=module('scripts.fleet.model_gate',p['model_gate']);import scripts.fleet
 scripts.fleet.model_gate=gate
 stop=read(p['stopped_proof']);require(p['stopped_proof']['sha256']=='93974179a62b4054de70dd0314c2caa97286e24d4d1d869662807dc7cb6f0ba3','exact root stopped proof required')
 for key,status in [('native_gate_probe','RETAINED_RESEARCH_OWNING_GATE_PROBE_PASSED'),('native_takeover_probe','EXACT_TIMEOUT_TAKEOVER_TWO_READONLY_PROBE_PASSED')]:
  value=read(p[key]);require(value['status']==status and value['profile']==p['profile']and value['models_dispatched']==value['owning_operations']==0,'actual native zero-model proof required')
 os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
 return p,H,q,old,d,job,env,w,T,A

def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0006'and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
  and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==3,'exact live continuation claim required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status=c.call('status')
 rows=[r for r in status['workbooks']if r['workbook']=='0006']
 require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==p['owner']and rows[0]['fence']==3
  and rows[0]['source_sha256']==SOURCE and rows[0]['version']==VERSION and status['model_limit']==status['publication_limit']==1,'live source/limits differ')
 c.call('heartbeat',workbook='0006',owner=p['owner'],fence=3,token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)

def selection(p,q,env,T):
 return T.Selection(Path(env[5]['run_root']),'0006',q['tracker'],'environment',q['guard'],q['checkpoint'],p['owner'],config=q['config'],prior_completion=q['prior_takeover'])

def evidence(p):return [p[k]for k in ('profile','reader_helper','reader_builder','takeover_reader','model_gate','stopped_proof')]

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

def research_commit(C,w,lease):
 """Original W.advance owns the lane CAS; no audit producer is callable."""
 def forbidden(*a,**k):raise ValueError('retained research cannot dispatch or reopen an audit')
 C.executor.execute=forbidden;C.executor._open=forbidden;C.worker.audit_bridge.run=forbidden
 result=C.worker.advance(w,lease,dispatch=False)
 # Preserve the actual owning outcome, including a genuine gate failure.
 return {**result,'lease':asdict(result['lease']),'selection':C.content_codec.encode(result['selection'])}

def research_child(bound,lease_record,intent):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);home=Path(p['home'])
 require(intent==binding(home/'research-gate/intent.json'),'guard intent differs')
 expected=dict(schema_version='0006-retained-research-gate/v1',plan=bound,lease=lease_record,driver=binding(__file__),timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=False,automatic_retry=False)
 require(read(intent)==expected,'closed zero-model guard required')
 completed=read(lease_record);_,_,lease=T.validate_completion(completed['takeover'])
 require(completed['lease']==asdict(lease),'takeover lease differs')
 result=research_commit(env[0],w,lease);return A.retain(home/'research-gate/result.json',result)

def owning_child(bound,kind,intent):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);base,I,*_=env
 producer={'environment':base,'instruction':I,'renewal':d}[kind]
 A.validate_child(producer,base=base,kind=kind,reader_plan=bound,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p))
 return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)

def probe(bound):
 p,H,q,old,d,job,env,w,T,A=load(bound)
 require(not Path(p['home']).exists(),'continuation already attempted')
 s=selection(p,q,env,T);before,lease,_,_=T._recipe(s);T._prior_chain(s,before)
 require(T.life.validate_lifecycle(before)==1 and lease['expires_at']<=time.time_ns(),'only expired replacement two qualifies')
 dispatcher_selected(d,env,A,bound,p)
 return dict(status='RETAINED_RESEARCH_CONTINUATION_READY',plan=bound,tracker=q['tracker'],models_dispatched=0,owning_operations=0,semantic_budget_reset=False)

def run(bound):
 p,H,q,old,d,job,env,w,T,A=load(bound);active(p);home=Path(p['home']);C=env[0]
 require(not home.exists()and binding(q['tracker']['path'])==q['tracker'],'continuation exists or tracker changed; reconcile')
 require(shutil.disk_usage(q['code_root']).free>=12*1024**3,'worker disk reserve unavailable')
 home.mkdir(parents=True,mode=0o700);A.retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 try:
  s=selection(p,q,env,T);lease,takeover=T.replace_expired(s,Path(env[5]['run_root'])/'lease-takeovers/fleet-research-timeout-002')
  lease_record=A.retain(home/'takeover-result.json',dict(lease=asdict(lease),takeover=takeover))
  stage=home/'research-gate';stage.mkdir();intent=A.retain(stage/'intent.json',dict(schema_version='0006-retained-research-gate/v1',plan=bound,lease=lease_record,driver=binding(__file__),timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=False,automatic_retry=False))
  command=[sys.executable,'-I','-B',__file__,'research-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],'--lease',lease_record['path'],'--lease-sha256',lease_record['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
  metrics={};rc=None;failure=None;start=time.monotonic()
  try:
   with(stage/'step.log').open('xb')as log:rc=C._run_in_process_group(command,cwd=C.ROOT,stdout=log,timeout_seconds=3300,memory_limit_bytes=8*1024**3,grace_seconds=3,metrics=metrics)
  except Exception as exc:failure=type(exc).__name__
  resource=A.retain(stage/'resource.json',dict(intent=intent,command=command,returncode=rc,failure_type=failure,metrics=metrics,elapsed_seconds=time.monotonic()-start))
  require(rc==0 and failure is None,'research guard requires reconciliation')
  actual=binding(stage/'result.json');result=read(actual)
  require(result['status']=='STEP_COMPLETE'and result['step']=='research','actual research gate did not complete')
  require(all(binding(x['path'])==x for x in q['model_outcomes']),'retained Calls changed')
  d,selected=dispatcher_selected(d,env,A,bound,p)
  legacy=A.retain(home/'legacy.json',dict(worker=C.worker.bootstrap.serial(asdict(w)),lease=asdict(lease)))
  outcome=selected[0].run('import',dict(legacy=legacy,checkpoint=actual),env[-1],home/'import')
  value=read(outcome);require(value['status']=='COMPLETED_OPERATION','owning import requires reconciliation')
  checkpoint=value['result'];imported=read(checkpoint);require(imported['status']=='IMPORTED','owning import did not complete')
  outcome=selected[0].run('step',dict(descriptor=imported['descriptor'],checkpoint=checkpoint,captures=None),env[-1],home/'bridge-step',dispatch=True)
  d.successful(outcome,selected)
  nextjob={**job,'outcome':outcome,'operations':str(Path(env[5]['run_root'])/'dispatcher-v2/fleet-research-timeout-002')}
  job_binding=A.retain(home/'authoring-job.json',nextjob);d.run(job_binding)
  hold=read(binding(Path(nextjob['operations'])/'hold.json'));checkpoint,result=d.successful(hold['outcome'],selected)
  require(result['status']=='PUBLICATION_READY','remaining authoring requires reconciliation')
  return A.retain(home/'result.json',dict(status='PUBLICATION_READY',workbook='0006',plan=bound,job=job_binding,config=job['config'],outcome=hold['outcome'],checkpoint=checkpoint,takeover=takeover,research_gate=actual,research_resource=resource,publication_performed=False,automatic_retry=False,semantic_budget_reset=False))
 except Exception as exc:
  A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0006',plan=bound,failure_type=type(exc).__name__,automatic_retry=False,semantic_budget_reset=False));raise

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','research-child','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True)
 a.add_argument('--lease');a.add_argument('--lease-sha256');a.add_argument('--intent');a.add_argument('--intent-sha256');a.add_argument('--kind',choices=['environment','instruction','renewal']);v=a.parse_args();bound=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
 if v.action=='research-child':value=research_child(bound,dict(path=v.lease,sha256=v.lease_sha256),dict(path=v.intent,sha256=v.intent_sha256))
 elif v.action=='owning-child':value=owning_child(bound,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
 else:value=globals()[v.action](bound)
 print(json.dumps(value),flush=True)
if __name__=='__main__':main()
