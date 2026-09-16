"""One exact source-backed 0190 warning permit and native retry handoff.

Only Q.authorize writes the permit. No model, lease operation, review reset or
publication is allowed here. A subsequent declared coordinator child consumes
its actual RETRY_READY result through the unchanged owning warning branch.
"""
from pathlib import Path
from dataclasses import asdict,replace
import argparse,hashlib,importlib.util,json,os,sys,time,shutil

SCHEMA='0190-source-warning-preparation/v1'
SOURCE='c6e9acb47959d5bcd2c6abac3f3111932a6e305025f59fe1881a03fe9f4292da'
VERSION='c31d1c0c5f9a419c6c7466e1971ab8559c63d18f7ea06a94ce9f2feb2b4e5218'
FIELDS={'schema_version','workbook','source_sha256','version','owner','fence','home','code_root','snapshot',
 'old_snapshot','old_result','native_prefix','takeover','takeover_alias','takeover_witness','capacity_policy','retained_plan'}

def require(ok,message):
 if not ok:raise ValueError(message)

def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink warning evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
 require(type(row)is dict and set(row)=={'path','sha256'}and binding(row['path'])==row,'warning evidence changed')
 return json.loads(Path(row['path']).read_bytes())

def module(name,row):
 require(binding(row['path'])==row,'warning implementation changed')
 s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def retain(path,value):
 path=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return binding(path)

def identity(p):
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0190'and p['source_sha256']==SOURCE
  and p['version']==VERSION and Path(p['code_root']).name==VERSION
  and p['owner']=='hhu-server-7:slot-2:0190-warning-continuation-001'and p['fence']==3,
  'closed source-warning preparation required')
 require(p['native_prefix']['sha256']=='06e70d6e024df9230491cd9c607b8f409d1b41b1205a93dd8aa11df2e326cb08'
  and p['snapshot']['sha256']=='b70950121346f33de6e6bf40fc6c203ce1be5a99aed7e6b67d7e98ea14b71767'
  and p['takeover_alias']['sha256']=='3474e7cf2686ceb90bdec662969bcff885847d4a7d5b2b396de3d8efbfd2b638'
  and p['capacity_policy']['sha256']=='17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4',
  'unreviewed source/code/runtime/takeover policy')
 require(Path(p['home'])==Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/vm-fleet-20260912/workbooks/0190/warning-preparation-001'),'unexpected warning namespace')

def no_models():
 def forbidden(*a,**k):raise ValueError('warning continuation forbids every new model call')
 import xl_transport
 from scripts.batch import harbor_model_executor as E
 E.execute=E._open=xl_transport.read_response=xl_transport._read_response=xl_transport.model_slot=forbidden
 return forbidden

def context(bound):
 p=read(bound);identity(p);root=Path(p['code_root']);sys.path.insert(0,str(root))
 require(p['retained_plan']['sha256']=='1fb5e048de0a3edb55bfbf36fadef7421a182b382f97c13f56da18da2a86e8ed','original admitted history changed')
 read(p['retained_plan'])
 os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN']=p['retained_plan']['path']
 os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256']=p['retained_plan']['sha256']
 from scripts import harbor_campaign as C,harbor_instruction_campaign as I
 from scripts.batch import harbor_instruction_worker as W,harbor_dialogue_warning_requalification as Q
 from scripts.fleet import dialogue_warning_lineage_0190 as Lineage
 require(all(Path(m.__file__).resolve().is_relative_to(root)for m in(C,I,W,Q,Lineage)),'warning imports escaped exact successor')
 Lineage.predecessor();snapshot=read(p['snapshot']);native=read(p['native_prefix'])
 require(native['status']=='PROSPECTIVE_WARNING_FULL_PREFIX_VALIDATED'and native['snapshot']==p['snapshot']
  and native['models']==native['owning_changes']==0 and native['warning_permit_created']is False,
  'actual source/runtime prefix proof required')
 old=read(p['old_result']);descriptor=read(old['descriptor'])
 require(p['old_result']['sha256']=='8b5fe89a032865e4c7081dea4a1c1547532d4e76b93fdc85423b7a276029107f'
  and old['status']=='DIALOGUE_REPAIR_REQUIRED'and old['next_step']=='dialogue-review'
  and descriptor['code']['sha256']==binding(I.__file__)['sha256'],'exact completed rejected review boundary required')
 for row in descriptor['origin']:require(binding(row['path'])==row,'old descriptor evidence changed')
 w=replace(I.worker_decode(descriptor['worker']),root=root);selection=I.codec.decode(old['selection'])
 require(selection.content.workbook=='0190'and selection.content.bindings['original_raw_source']['sha256']==SOURCE,
  'warning source/workbook changed')
 controls=C.decode(descriptor['controls']);recipe=C.decode(descriptor['dependencies']);C._controls(controls);C._campaign(w.campaign)
 require(w.warning_requalification is None,'warning already selected')
 witness=read(p['takeover_witness']);require(witness['successor']==p['takeover_alias'],
  'declared takeover alias differs')
 T=module('scripts.batch.harbor_lease_takeover',p['takeover_alias']);import scripts.batch
 scripts.batch.harbor_lease_takeover=T
 forbidden=no_models();T.replace_expired=T.recover=T.life.lease_operation=forbidden
 os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
 return p,C,I,W,Q,w,selection,controls,recipe,old,T

def load(bound):
 p,C,I,W,Q,w,selection,controls,recipe,old,T=context(bound)
 _,after,lease=T.validate_completion(p['takeover'])
 require(T.life.validate_lifecycle(after)==2 and lease.owner==p['owner']
  and after['bindings']['original_raw_source']['sha256']==SOURCE,'exact supported second takeover required')
 current=read(binding(w.context.tracker_root/'0190.json'))
 require(current==after and current['leases']['task_agent']['expires_at']-time.time_ns()>3330*10**9,
  'fresh warning takeover and original guard runway required')
 os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
 return p,C,I,W,Q,w,selection,lease,controls,recipe,old

def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0190'and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
  and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==p['fence'],'exact central warning custody required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);s=c.call('status')
 module('selected0190capacity',p['capacity_policy']).validate_occupancy(s)
 rows=[r for r in s['workbooks']if r['workbook']=='0190']
 require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==p['owner']and rows[0]['fence']==p['fence']
  and rows[0]['source_sha256']==SOURCE and rows[0]['version']==VERSION and rows[0]['expires']>time.time(),'warning source/version/fence differs')
 c.call('heartbeat',workbook='0190',owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)

def handoff(C,I,w,selection,lease,controls,recipe,permit,origin,directory):
 descriptor=retain(directory/'descriptor.json',dict(schema_version=I.SCHEMA,worker=I.worker_encode(w),controls=C.encode(controls),
  dependencies=C.encode(recipe),origin=origin,code=binding(I.__file__),initial=dict(selection=I.codec.encode(selection),lease=C.encode(lease))))
 result=I._result('RETRY_READY',descriptor,selection,lease,checkpoint=permit,next_step='dialogue-review',evidence=origin)
 bound=retain(directory/'result.json',result)
 I.selected(descriptor);I._prior(bound,descriptor)
 return bound

def scope_context(selection,directory,*,root):
 """Issue the owning code-equivalence receipt; preserve every financial field."""
 from scripts.batch.harbor_dialogue_scope_context import requalify_selected,validate_equivalence
 content,receipt=requalify_selected(selection.content,directory,root=root)
 require(replace(content,dialogue_scope_receipt=selection.content.dialogue_scope_receipt)==selection.content,
  'scope equivalence changed financial selection')
 require(read(receipt).get('replaces',read(receipt)['previous'])==selection.content.dialogue_scope_receipt,
  'scope equivalence detached from retained receipt')
 validate_equivalence(content,root=root)
 return replace(selection,content=content),receipt

def run(bound):
 p,C,I,W,Q,w,selection,lease,controls,recipe,old=load(bound);active(p);home=Path(p['home'])
 require(not home.exists(),'warning preparation already attempted; reconcile without replay')
 require(shutil.disk_usage(p['code_root']).free>=12*1024**3,'original worker reserve unavailable')
 home.mkdir(parents=True,mode=0o700);intent=retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),models=False,automatic_retry=False))
 selection,scope_receipt=scope_context(selection,home/'scope-equivalence',root=w.root)
 worker,selected,current,permit=Q.authorize(w,selection,lease,blocked=old['checkpoint'],old_snapshot=p['old_snapshot'],directory=home/'warning',takeover=p['takeover'])
 require(selected==selection and current==lease and read(permit)['model_calls']==0 and read(permit)['semantic_budget_reset']is False,
  'warning changed source/budget/lease')
 origin=[bound,intent,p['old_result'],p['native_prefix'],p['snapshot'],p['takeover'],p['takeover_alias'],p['takeover_witness'],scope_receipt,permit]
 return handoff(C,I,worker,selection,lease,controls,recipe,permit,origin,home)

def probe(bound):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','read-only warning context required')
 p,C,I,W,Q,w,selection,controls,recipe,old,T=context(bound)
 require(p['takeover'] is None,'pre-takeover probe cannot claim a completed takeover')
 before=binding(w.context.tracker_root/'0190.json')
 require(before['sha256']=='977350e3c011fc19599bf65404384e741bafc519c44f9bddb5e443497433a570','pre-takeover tracker changed')
 require(Q.changes(p['old_snapshot'],w.root)==read(p['native_prefix'])['changes'],'native warning code delta differs')
 require(not Path(p['home']).exists(),'warning preparation already exists')
 return dict(status='PRE_TAKEOVER_WARNING_NATIVE_CONTEXT_VALIDATED',plan=bound,tracker=before,
  old_result=p['old_result'],snapshot=p['snapshot'],actual_takeover_validated=False,
  warning_permit_created=False,models_dispatched=0,owning_operations=0)


def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);v=a.parse_args()
 print(json.dumps(globals()[v.action](dict(path=str(Path(v.plan).absolute()),sha256=v.sha256))))
if __name__=='__main__':main()
