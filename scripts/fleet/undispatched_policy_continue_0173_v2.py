"""Continue the completed 0173 writer after a pre-owning policy assertion.

The failed guard stays failed. This wrapper starts the unchanged instruction
producer from its real previous completion in a new guarded namespace.
"""
from pathlib import Path
from types import FunctionType
import argparse,hashlib,importlib.util,json,os,sys,time,shutil

SCHEMA='fleet-0173-policy-boundary-continuation/v2'
SOURCE='42cc706472a768c9fee76e653cb21c677cb76571b9f78075fad00ad287a7b638'
VERSION='6bc05b90a9daf15dd66ccc96058f5366a12068f6a133765ec2cc451e13d99dc8'
OWNER='hhu-server-4:slot-2:0173-authoring-policy-005'
TAIL_SHA='5d0074c174af53bbb8f83fd396c7ae94f547b5e90131541b8e6547e52270910a'
POLICY_SHA='17d52d922dc4141f33e9627ece86762e8ebb664233278956a9485b8a2b52b3a4'
COMPLETED='9c833f8fec0f72cef6302461561787b8ebb8396696451fc693f0db409f28e742'
FAILED='340215b1670dfcaedd6c7fa093c51bbd06cfe6d16354dd1d6e3eada99cef48a9'
TRACE='7e0ec11c280540cf800ea63f37847df9dbf867df3d58a2691cc52bdebf8a8d76'
FIELDS={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence','home','prior_tail_plan','prior_tail_driver','prior_job','completed','failed','tracker','stopped_proof','capacity_policy','namespace_failure','operations'}


def require(ok,message):
 if not ok:raise ValueError(message)


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'policy continuation symlink')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'policy continuation evidence changed')
 return json.loads(Path(row['path']).read_bytes())


def module(name,row):
 require(binding(row['path'])==row,'policy continuation code changed')
 s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m


def identity(p):
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['workbook']=='0173'
  and p['source_sha256']==SOURCE and p['version']==VERSION and p['owner']==OWNER
  and p['expected_previous_fence']==4,'closed exact policy continuation required')
 require(p['prior_tail_driver']['sha256']==TAIL_SHA and p['capacity_policy']['sha256']==POLICY_SHA
  and p['completed']['sha256']==COMPLETED and p['failed']['sha256']==FAILED,'unreviewed boundary or implementation')


def stopped(p):
 proof=read(p['stopped_proof'])
 require(proof['status']=='UNDISPATCHED_POLICY_BOUNDARY_STOPPED_RECONCILED'
  and proof['workbook']=='0173'and proof['central_fence']==4 and proof['tracker']==p['tracker']
  and proof['completed']==p['completed']and proof['failed']==p['failed']
  and proof['active_processes']==[]and proof['automatic_retry']is False,
  'exact stopped policy boundary required')
 return proof


def failed_boundary(p,previous,worker):
 failure=read(p['failed']);directory=Path(p['failed']['path']).parent
 require(directory.name=='015' and failure['status']=='RECONCILIATION_REQUIRED'
  and failure['resource']==binding(directory/'resource.json'),'failed original guard differs')
 resource=read(failure['resource']);require(resource['returncode']==1 and resource['failure']is None
  and resource['intent']==binding(directory/'intent.json')and resource['log']==binding(directory/'step.log')
  and resource['log']['sha256']==TRACE,'exact pre-owning policy assertion required')
 intent=read(resource['intent'])
 require(intent['action']=='step'and intent['arguments']==dict(descriptor=previous['descriptor'],checkpoint=read(p['completed'])['result'])
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8*1024**3
  and intent['models']is True and not(directory/'result.json').exists(),
  'failed guard is not the same unentered next producer step')
 # The retained traceback ends in the wrapper active() before producer.child.
 # A native lane directory would contradict that boundary and requires recovery.
 require(not(worker.run/'steps/nat-review').exists(),'naturalization review already entered; do not replay')


def validate_operations(dispatcher,config,correct,*,initial):
 require(correct==Path(config['run_root'])/'dispatcher-v2/authoring-policy-005'
  and not any(x.is_symlink()for x in(correct,*correct.parents)),
  'exact real operations namespace required')
 if initial:
  require(dispatcher.operation_namespace(config,str(correct))==correct,
   'original dispatcher rejected operation namespace')
 else:
  # The original dispatcher creates this directory before its guarded child.
  # A.validate_child then authenticates the specific intent and bound wrapper.
  require(correct.is_dir(),'owning child operations namespace absent')
 return correct


def namespace_failure(p,env,dispatcher,job,*,initial):
 proof=read(p['namespace_failure'])
 require(proof['status']=='EXACT_PRE_OWNING_NAMESPACE_FAILURE' and proof['workbook']=='0173'
  and proof['original_plan']['sha256']=='04e8b90a44a9d7135835fb9ee8e7f0591b3ca8571134be6917d064212d47459b'
  and proof['worker_outcome']['sha256']=='b9390a26759c34b486a9d9368fd97c51527f1c85dd147197dd65931f099d2d09'
  and proof['result']['sha256']=='bf618171ae181d4d255ac87ec4efc2cd3978f1287b32224d5a918cd00d1f8891'
  and proof['log']['sha256']=='c2d5f397043ebebcdf31bf835c94c23324da9fc77045f4b373a253a011f3be51',
  'exact pre-owning namespace failure required')
 for key in ('original_plan','worker_outcome','result','intent','job','log'):
  require(binding(proof[key]['path'])==proof[key],'retained namespace failure changed')
 old=read(proof['original_plan']);home=Path(old['home'])
 require(home==Path(p['home']).parent/'authoring-policy-004'
  and not (home/'operations').exists() and not (home/'ready.json').exists(),
  'failed namespace entered owning work')
 require(read(proof['result'])['status']=='RECONCILIATION_REQUIRED'
  and read(proof['result'])['failure_type']=='ValueError'
  and read(proof['job'])=={**job,'outcome':p['completed'],'operations':str(home/'operations')},
  'original namespace failure differs')
 correct=Path(env[5]['run_root'])/'dispatcher-v2/authoring-policy-005'
 require(p['operations']==str(correct),'exact unused owning namespace required')
 validate_operations(dispatcher,env[5],correct,initial=initial)
 return correct


def load(bound,*,initial=False):
 p=read(bound);identity(p);policy=module('selected0173_capacity_policy',p['capacity_policy'])
 D=module('retained0173_completed_tail',p['prior_tail_driver'])
 prior,D0,n,helper,env,environment,P,recovery,A,dispatcher,phase,emitted=D.load(p['prior_tail_plan'])
 require(prior['workbook']=='0173'and prior['source_sha256']==SOURCE and prior['version']==VERSION,
  'prior source/code differs')
 require(Path(p['home'])==Path(prior['home']).parent/'authoring-policy-005'
  and p['tracker']['path']==prior['tracker']['path'],'continued namespace or tracker differs')
 job=read(p['prior_job']);require(p['prior_job']==binding(Path(prior['home'])/'authoring-job.json')
  and job['operations']==str(Path(env[5]['run_root'])/'dispatcher-v2/normalization-authoring-003'),
  'original owning authoring job differs')
 checkpoint,previous=dispatcher.successful(p['completed'],env)
 require(previous['status']=='STEP_COMPLETE'and previous['next_step']=='nat-review',
  'real completed naturalization writer required')
 C=env[1];worker,controls,recipe=C.selected(previous['descriptor'])
 C._prior(checkpoint,previous['descriptor'])
 require(worker.root.name==VERSION and previous['selection']==read(checkpoint)['selection'],
  'original instruction context differs')
 stopped(p)
 namespace_failure(p,env,dispatcher,job,initial=initial)
 if initial:
  require(binding(p['tracker']['path'])==p['tracker'],'initial tracker changed')
  failed_boundary(p,previous,worker)
  C.worker.active(worker,C.codec.decode(previous['selection']),env[0].decode(previous['lease']))
 return p,D,env,A,dispatcher,job,checkpoint,previous,worker


def evidence(p):
 return [p[k]for k in ('prior_tail_plan','prior_tail_driver','completed','failed','stopped_proof','capacity_policy','namespace_failure')]


def selected_dispatcher(D,dispatcher,env,A,bound,p):
 # Original function bytecode remains exact. Only its declared wrapper/evidence
 # are supplied by this new, hash-bound runtime implementation.
 g={**D.selected_dispatcher.__globals__,'__file__':__file__,'evidence':evidence}
 f=FunctionType(D.selected_dispatcher.__code__,g,D.selected_dispatcher.__name__,D.selected_dispatcher.__defaults__,D.selected_dispatcher.__closure__)
 return f(dispatcher,env,A,bound,p)


def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')=='0173'and os.environ.get('HARBOR_FLEET_OWNER')==OWNER
  and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==5,'exact current policy custody required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);s=c.call('status')
 module('selected0173_capacity_policy',p['capacity_policy']).validate_occupancy(s)
 rows=[w for w in s['workbooks']if w['workbook']=='0173'];now=time.time()
 require(len(rows)==1 and rows[0]['state']=='active'and rows[0]['owner']==OWNER and rows[0]['fence']==5
  and rows[0]['source_sha256']==SOURCE and rows[0]['version']==VERSION and rows[0]['expires']>now,
  'current live source/version/fence differs')
 c.call('heartbeat',workbook='0173',owner=OWNER,fence=5,token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)


def owning_child(bound,kind,intent):
 p,D,env,A,dispatcher,job,checkpoint,previous,worker=load(bound);active(p)
 base,C,*_=env;producer={'environment':base,'instruction':C,'renewal':dispatcher}[kind]
 A.validate_child(producer,base=base,kind=kind,reader_plan=bound,child_wrapper=binding(__file__),intent=intent,reader_evidence=evidence(p))
 return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)


def probe(bound):
 p,D,env,A,dispatcher,job,checkpoint,previous,worker=load(bound,initial=True)
 require(not Path(p['home']).exists() and not Path(p['operations']).exists(),'policy continuation already attempted')
 require(shutil.disk_usage(env[6]).free>=12*1024**3,'original worker reserve unavailable')
 selected_dispatcher(D,dispatcher,env,A,bound,p)
 return dict(status='UNDISPATCHED_POLICY_REMAINING_READY',plan=bound,completed=p['completed'],next_step='nat-review',models_dispatched=0,owning_operations=0,semantic_budget_reset=False)


def run(bound):
 p,D,env,A,dispatcher,job,checkpoint,previous,worker=load(bound,initial=True);active(p)
 home=Path(p['home']);require(not home.exists() and not Path(p['operations']).exists(),'policy continuation exists; reconcile without replay')
 require(shutil.disk_usage(env[6]).free>=12*1024**3,'original worker reserve unavailable')
 home.mkdir(parents=True,mode=0o700);A.retain(home/'intent.json',dict(plan=bound,driver=binding(__file__),automatic_retry=False))
 selected,selected_env=selected_dispatcher(D,dispatcher,env,A,bound,p)
 job={**job,'outcome':p['completed'],'operations':p['operations']};jobrow=A.retain(home/'authoring-job.json',job)
 try:
  selected.run(jobrow)
  held=read(binding(Path(job['operations'])/'hold.json'));checkpoint,result=selected.successful(held['outcome'],selected_env)
  require(result['status']=='PUBLICATION_READY','remaining instruction requires reconciliation')
  sidecars=[binding(x)for x in sorted({*home.rglob('declared-runtime-adapter.json'),*Path(job['operations']).rglob('declared-runtime-adapter.json')})]
  return A.retain(home/'result.json',dict(status='PUBLICATION_READY',workbook='0173',plan=bound,job=jobrow,config=job['config'],outcome=held['outcome'],checkpoint=checkpoint,runtime_adapters=sidecars,publication_performed=False,automatic_retry=False,semantic_budget_reset=False))
 except Exception as exc:
  A.retain(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook='0173',plan=bound,failure_type=type(exc).__name__,automatic_retry=False,publication_performed=False,semantic_budget_reset=False));raise


def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe','run','owning-child']);parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True);parser.add_argument('--kind',choices=['environment','instruction','renewal']);parser.add_argument('--intent');parser.add_argument('--intent-sha256');a=parser.parse_args();b=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256)
 value=owning_child(b,a.kind,dict(path=a.intent,sha256=a.intent_sha256))if a.action=='owning-child'else globals()[a.action](b)
 print(json.dumps(value))
if __name__=='__main__':main()
