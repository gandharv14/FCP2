"""Source-preserving 0013 normalization continuation from a real pending checkpoint.

Exactly one never-transported ordinal is continued through an explicit reader.
Original source, partition selection, completed Calls, and owning v8 guards stay
unchanged. This cannot perform first admission or restart reconstruction.
"""
from pathlib import Path
from types import FunctionType
import argparse,hashlib,importlib.util,json,os,shutil,sys,time
SCHEMA='fleet-source-undispatched-0013/v1'
SOURCE='e77c1209f33059f73fdeb2c2a378ab61db1152a3a1f5e2116e506a7d0fbe5d82'
VERSION='9869cf9db8bec4853a54610385216282f3dc826ae872115c453060cff13e4da4'
OWNER='hhu-server-5:slot-1:0013-undispatched-003'
PROOF='abad6514bbdad925b1dd41d97417e990578a6a4f484500de37c0c427b020e554'
POLICY='17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4'
GATE='8e284b66b43b8acd215700a812d242ae3eb8d6cb964122addfdb27c8441baf92'
EXECUTOR='fa2f397d4b8f50d841d4d59c8fe4070ad4fb88166b62f002cebaa2924d19d430'
FIELDS={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence','home','operations','source_plan','authoring_job','completed','failed','tracker','prepared','native_proof','central_proof','executor_adapter','partition_adapter','campaign_adapter','dispatch_adapter','model_gate','capacity_policy','lease_renewal'}
def require(v,m):
 if not v:raise ValueError(m)
def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'continuation symlink')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(b):
 require(binding(b['path'])==b,'continuation binding changed');return json.loads(Path(b['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'continuation module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def identity(p):
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0013'and p['source_sha256']==SOURCE and p['version']==VERSION and p['owner']==OWNER and p['expected_previous_fence']==3,'closed selected0013 recovery required')
 require(p['native_proof']['sha256']==PROOF and p['capacity_policy']['sha256']==POLICY and p['model_gate']['sha256']==GATE and p['executor_adapter']['sha256']==EXECUTOR,'unreviewed proof or gate')
def evidence(p):return [p[k]for k in ('source_plan','completed','failed','native_proof','central_proof','executor_adapter','partition_adapter','campaign_adapter','dispatch_adapter','model_gate','capacity_policy','lease_renewal')]
def configure_history(source,root,readonly):
 os.environ.update(HARBOR_FLEET_SOURCE_HISTORY_PATH=source['history']['path'],HARBOR_FLEET_SOURCE_HISTORY_SHA256=source['history']['sha256'],HARBOR_FLEET_WORKBOOK='0013')
 from scripts.fleet import source_authoring_history as N
 require(Path(N.__file__).resolve().is_relative_to(root),'foreign source history reader')
 if readonly:
  original=N.validate_certificate
  def proof_only(row,*,workbook,root=None,live=True):return original(row,workbook=workbook,root=root,live=False)
  N.validate_certificate=proof_only
 return N

def operations(dispatcher,config,p,initial):
 selected=Path(p['operations']);require(selected==Path(config['run_root'])/'dispatcher-v2/undispatched-003'and not any(x.is_symlink()for x in(selected,*selected.parents)),'exact original dispatcher namespace required')
 if initial:require(dispatcher.operation_namespace(config,str(selected))==selected,'owning dispatcher namespace rejected')
 else:require(selected.is_dir(),'declared owning child namespace absent')
 return selected

def retained_renewal(base,worker,p,checkpoint,previous,failed_result):
 receipt=read(p['lease_renewal']);directory=Path(p['failed']['path']).parent
 require(p['lease_renewal']==binding(directory/'lease-renewal.json')and set(receipt)=={'previous','lease','tracker'},'exact original guarded renewal receipt required')
 require(receipt['previous']==previous['lease']and receipt['lease']==failed_result['lease']and receipt['tracker']==p['tracker'],'failed guard renewal detached from both actual checkpoints')
 old=base.decode(receipt['previous']);new=base.decode(receipt['lease'])
 require(old.owner==new.owner=='hhu-server-5:slot-1:0013-source-first-authoring-002'and old.token==new.token and new.revision==old.revision+1 and new.sha256==p['tracker']['sha256'],'original same-owner renewal changed identity or CAS')
 original=base._prior
 def declared_prior(bound,descriptor):
  value,lease=original(bound,descriptor)
  if bound!=checkpoint:return value,lease
  require(descriptor==previous['descriptor']and value==previous and lease==old,'selected retained pending checkpoint changed')
  _,tracker=base.worker._active(worker,new)
  base.lifecycle.validate_lifecycle(tracker)
  # The return value remains the original checkpoint. Only the current lease
  # comes from the actual completed same-owner renewal in its failed child.
  return value,new
 base._prior=declared_prior
 return new

def load(bound,*,initial=False,readonly=False):
 p=read(bound);identity(p);source=read(p['source_plan']);snapshot=read(source['snapshot']);root=Path(snapshot['root']);require(root.name==VERSION,'original source code differs');sys.path.insert(0,str(root))
 # The original full inventory/history reader is retained. Only a labelled probe
 # suppresses its live heartbeat; no execute/run path can request that mode.
 configure_history(source,root,readonly)
 old=module('selected0013_source_author',binding(root/'scripts/fleet/source_authoring.py'));old.selected(p['source_plan'],live=not readonly)
 job=read(p['authoring_job']);dispatcher=module('selected0013_original_dispatcher',job['driver']);env=dispatcher.setup(job);base,C,*_=env;base._campaign(base.decode(read(env[5]['intake'])).campaign)
 from scripts.fleet import model_gate as old_gate
 gate=module('scripts.fleet.model_gate',p['model_gate']);setattr(sys.modules['scripts.fleet'],'model_gate',gate)
 module('scripts.fleet.capacity_policy',p['capacity_policy']);setattr(sys.modules['scripts.fleet'],'capacity_policy',sys.modules['scripts.fleet.capacity_policy'])
 checkpoint,previous=dispatcher.successful(p['completed'],env)
 require(previous['status']=='PENDING_MODEL'and previous['step']=='normalization_phase1','only completed original normalization progress')
 failure=read(p['failed']);resource=read(failure['resource']);failed_result=read(failure['result'])
 require(failure['status']=='COMPLETED_OPERATION' and resource['returncode']==0 and resource['failure']is None and failed_result['status']=='UNRESOLVED' and failed_result['descriptor']==previous['descriptor'],'actual unresolved owning normalization guard required')
 intent=read(resource['intent']);require(intent['action']=='step'and intent['arguments']==dict(descriptor=previous['descriptor'],checkpoint=checkpoint,captures=None),'failed step detached from previous pending checkpoint')
 worker,_,_=base.selected(previous['descriptor']);lease=retained_renewal(base,worker,p,checkpoint,previous,failed_result)
 from scripts.batch import harbor_normalization_partitioned as P,harbor_normalization_routing as routing
 U=module('selected0013_undispatched_executor',p['executor_adapter']);F=module('selected0013_partition_reader',p['partition_adapter']);proof=read(p['native_proof']);raw=read(proof['plan']);pc=P._prepared_call(p['prepared'],21,1);campaign=P.E.Campaign(Path(raw['campaign_root']),Path(raw['slot_root']),raw['model_slots'])
 recovery=U.Recovery(P.E,pc,campaign,native_proof=p['native_proof'],central_proof=p['central_proof']);selected=F.facade(P,recovery,p['prepared'],partition_code=binding(P.__file__),continuation_evidence=(bound,*evidence(p)))
 F.install(P,selected);routing.P=selected;routing.E=selected.E
 A=module('selected0013_campaign_adapter',p['campaign_adapter']);D=module('selected0013_dispatch_adapter',p['dispatch_adapter'])
 operations(dispatcher,env[5],p,initial)
 if initial:
  require(binding(p['tracker']['path'])==p['tracker'],'native pending tracker changed');base.worker._active(worker,lease)
  require(not Path(p['home']).exists()and not recovery.root.exists(),'recovery namespace already consumed')
 return p,dispatcher,env,worker,lease,recovery,A,D,job,checkpoint,previous

def selected_dispatcher(D,dispatcher,env,A,bound,p):
 f=D.selected_dispatcher;g={**f.__globals__,'__file__':__file__,'evidence':evidence};return FunctionType(f.__code__,g,f.__name__,f.__defaults__,f.__closure__)(dispatcher,env,A,bound,p)
def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_OWNER')==OWNER and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==4 and os.environ.get('HARBOR_FLEET_WORKBOOK')=='0013','exactnew0013 custody required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);s=c.call('status');module('selected0013_policy',p['capacity_policy']).validate_occupancy(s);rows=[x for x in s['workbooks']if x['workbook']=='0013'];require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==OWNER and rows[0]['fence']==4 and rows[0]['source_sha256']==SOURCE and rows[0]['version']==VERSION,'newlive0013source differs');c.call('heartbeat',workbook='0013',owner=OWNER,fence=4,token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
def owning_child(bound,kind,intent):
 p,d,env,w,lease,recovery,A,D,job,checkpoint,previous=load(bound);active(p);base,C,*_=env;producer={'environment':base,'instruction':C,'renewal':d}[kind];A.validate_child(producer,base=base,kind=kind,reader_plan=bound,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p));return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)
def probe(bound):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit read-only native probe required')
 p,d,env,w,lease,recovery,A,D,job,checkpoint,previous=load(bound,initial=True,readonly=True);selected_dispatcher(D,d,env,A,bound,p)
 return dict(status='SOURCE_UNDISPATCHED_0013_READY',plan=bound,reader=recovery.witness,tracker=p['tracker'],completed=p['completed'],semantic_ordinal=1,semantic_limit=2,models_dispatched=0,owning_operations=0,original_source_and_attempts_unchanged=True)
def run(bound):
 require(not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'read-only probe cannot run');p,d,env,w,lease,recovery,A,D,job,checkpoint,previous=load(bound,initial=True);active(p);require(shutil.disk_usage(env[6]).free>=12*1024**3,'original disk reserve required');home=Path(p['home']);home.mkdir(parents=True,mode=0o700);A.retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 selected,selected_env=selected_dispatcher(D,d,env,A,bound,p);job={**job,'outcome':p['completed'],'operations':p['operations']};jr=A.retain(home/'authoring-job.json',job)
 try:
  selected.run(jr);held=read(binding(Path(job['operations'])/'hold.json'));checkpoint,result=selected.successful(held['outcome'],selected_env);require(result['status']=='PUBLICATION_READY','original remaining authoring requires reconciliation')
  return A.retain(home/'result.json',dict(status='PUBLICATION_READY',workbook='0013',plan=bound,job=jr,config=job['config'],outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False,semantic_budget_reset=False))
 except Exception as e:
  A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0013',plan=bound,failure_type=type(e).__name__,automatic_retry=False));raise

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['probe','run','owning-child']);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--kind',choices=['environment','instruction','renewal']);p.add_argument('--intent');p.add_argument('--intent-sha256');a=p.parse_args();b=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256);result=owning_child(b,a.kind,dict(path=a.intent,sha256=a.intent_sha256))if a.action=='owning-child'else globals()[a.action](b);print(json.dumps(result))
if __name__=='__main__':main()
