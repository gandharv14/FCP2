"""Resume an actual initial handoff after a sealed pre-child wrapper failure.

The original handoff, lease takeover and failed guard are retained. A declared
location-only reader lets cold children load their existing parent namespace;
original child intents and the fresh dispatcher gate still control execution.
"""
from pathlib import Path
from types import FunctionType,ModuleType,SimpleNamespace
import argparse,ast,hashlib,importlib.util,inspect,json,os,sys,textwrap,time
SCHEMA='fleet-retained-initial-continuation/v1'
FIELDS={'schema_version','entry','stdlib','context','context_plan','load_ast','adapter','handoff','failed_outcome','failure_log','tracker','workbook','source','version','owner','fence','home','operations','original_job'}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and not any(x.is_symlink()for x in(p,*p.parents)),'regular bound file required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'bound evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'selected code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def keep(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 return binding(p)
def source_hash(function):return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(function))),include_attributes=False).encode()).hexdigest()
def location_loader(original,expected,namespace):
 """One exact read-only layout call changes; no owning function is rewritten."""
 require(original.__name__=='load' and original.__closure__ is None and source_hash(original)==expected,'original retained loader changed')
 tree=ast.parse(textwrap.dedent(inspect.getsource(original)));calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and isinstance(n.func.value,ast.Name)and n.func.value.id=='dispatcher'and n.func.attr=='operation_namespace']
 require(len(calls)==1 and len(calls[0].args)==2 and not calls[0].keywords,'one original namespace validation required')
 calls[0].func=ast.Name(id='_retained_location',ctx=ast.Load());ast.fix_missing_locations(tree)
 scope={**original.__globals__,'_retained_location':namespace};exec(compile(tree,original.__code__.co_filename,'exec'),scope)
 return scope['load']
def evidence(p):return [p[k]for k in ('entry','stdlib','context','context_plan','adapter','handoff','failed_outcome','failure_log','original_job')]
def context(bound,*,initial=True):
 p=checked(bound);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['entry']==binding(__file__),'closed retained initial plan required')
 module('retained_initial_stdlib',p['stdlib']).preload_inspect()
 for row in evidence(p):require(binding(row['path'])==row,'retained dependency changed')
 M=module('retained_initial_original_context',p['context']);A=module('retained_initial_adapter',p['adapter'])
 old,Mdriver,env,oldA,D,oldjob,T,selection,native,policy=location_loader(M.load,p['load_ast'],A.selected_operation_namespace)(p['context_plan'])
 require(old['workbook']==p['workbook'] and old['source']==p['source']['sha256'] and old['version']==p['version'] and env[6].name==p['version'],'retained original source/code differs')
 require(checked(p['original_job'])==oldjob,'retained authoring job differs')
 A.selected_operation_namespace(env[5],p['operations'])
 require(Path(p['home']).parent==Path(env[5]['run_root'])/'continuations','continuation home escapes workbook')
 handoff=checked(p['handoff']);require(handoff['status']=='INITIAL_HANDOFF_READY' and handoff['automatic_retry']is False,'actual completed initial handoff required')
 if initial:
  checkpoint,value=M.validate_handoff(p['context_plan'],p['handoff'],env)
  require(value['status']=='INSTRUCTION_READY'and value['checkpoint']is None and value['next_step']=='disclosure-build','first original instruction boundary changed')
  failed=checked(p['failed_outcome']);resource=checked(failed['resource']);intent=checked(resource['intent'])
  require(set(failed) in ({'status','resource','automatic_retry','terminal_verdict','source_invalidity_claimed'},{'status','resource','result','automatic_retry'})and failed['status']=='RECONCILIATION_REQUIRED'and failed.get('result')is None and failed.get('terminal_verdict')is None and failed.get('source_invalidity_claimed',False)is False and failed['automatic_retry']is False
   and resource['returncode']==1 and resource['failure']is None and resource['log']==p['failure_log'],'sealed pre-child failed guard required')
  require(intent['action']=='step'and intent['arguments']==dict(checkpoint=checkpoint,descriptor=value['descriptor'])
   and intent['code']==binding(env[1].__file__),'failed child detached from actual handoff')
  log=Path(p['failure_log']['path']).read_text();require(log.rstrip().endswith('ValueError: dispatcher namespace exists; no replay')and 'in owning_child' in log and 'in load' in log,'different failure needs reconciliation')
  worker,_,_=env[1].selected(value['descriptor'])
  require(not worker.run.exists()and not Path(resource['intent']['path']).with_name('result.json').exists(),'first instruction child already entered')
  tracker=checked(p['tracker']);require(p['tracker']['path']==str(selection.tracker['path']),'tracker path differs')
  require(tracker['workbook_id']==p['workbook']and tracker['leases']['task_agent']['expires_at']-time.time_ns()>3600*10**9,'remaining original guard lacks runway')
  require(not Path(p['home']).exists(),'continuation home consumed');D.operation_namespace(env[5],p['operations'])
 return p,M,A,D,env

def active(p):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner']and os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']and os.environ.get('HARBOR_FLEET_FENCE')==str(p['fence']),'exclusive current owner required')
 value=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
 require(value['state']=='active'and value['source_sha256']==p['source']['sha256']and value['version']==p['version'],'current source/version custody differs')

def selected_dispatcher(bound,p,M,A,D,env):
 base,I=env[:2];common=dict(base=base,reader_plan=bound,child_wrapper=p['entry'],reader_evidence=evidence(p))
 br=A.select_run(base,kind='environment',producer_code=binding(base.__file__),**common)
 ir=A.select_run(I,kind='instruction',producer_code=binding(I.__file__),**common)
 renewal=A.select_run(D,kind='renewal',producer_code=binding(D.__file__),**common)
 bp=SimpleNamespace(**base.__dict__);bp.run=br;bp._run_in_process_group=renewal.declared_runner
 ip=SimpleNamespace(**I.__dict__);ip.run=ir
 clone=ModuleType(D.__name__);clone.__dict__.update(D.__dict__)
 for name,value in D.__dict__.items():
  if isinstance(value,FunctionType)and value.__globals__ is D.__dict__:
   f=FunctionType(value.__code__,clone.__dict__,value.__name__,value.__defaults__,value.__closure__);f.__kwdefaults__=value.__kwdefaults__;clone.__dict__[name]=f
 def setup(job):
  current=D.setup(job);require(current[0]is base and current[1]is I,'owning producer identity changed');return(bp,ip,*current[2:])
 def successful(outcome,current=None):
  if outcome==p['handoff']:return M.validate_handoff(p['context_plan'],outcome,current or env)
  return D.successful(outcome,current or env)
 clone.setup=setup;clone.successful=successful
 return clone,(bp,ip,*env[2:])

def owning_child(bound,kind,intent):
 p,M,A,D,env=context(bound,initial=False);active(p)
 require(kind in {'environment','instruction','renewal'},'unsupported owning child')
 producer={'environment':env[0],'instruction':env[1],'renewal':D}[kind]
 A.validate_child(producer,base=env[0],kind=kind,reader_plan=bound,child_wrapper=p['entry'],intent=intent,reader_evidence=evidence(p))
 return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)

def probe(bound):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit no-owning probe required')
 p,M,A,D,env=context(bound);selected_dispatcher(bound,p,M,A,D,env)
 return dict(status='PASS',models_dispatched=0,result=dict(plan=bound,tracker=p['tracker'],initial_handoff=p['handoff'],first_step='disclosure-build',claims_changed=0,lease_mutations=0))

def run(bound):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot continue')
 p,M,A,D,env=context(bound);active(p);chosen,selected_env=selected_dispatcher(bound,p,M,A,D,env);home=Path(p['home']);home.mkdir(parents=True)
 keep(home/'intent.json',dict(plan=bound,handoff=p['handoff'],failed=p['failed_outcome'],automatic_retry=False))
 job=keep(home/'authoring-job.json',{**checked(p['original_job']),'outcome':p['handoff'],'operations':p['operations']})
 try:
  chosen.run(job);hold=checked(binding(Path(p['operations'])/'hold.json'));checkpoint,value=chosen.successful(hold['outcome'],selected_env)
  return keep(home/'result.json',dict(status=value['status'],workbook=p['workbook'],plan=bound,job=job,config=checked(job)['config'],outcome=hold['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))
 except Exception as error:
  keep(home/'result.json',dict(status='RECONCILIATION_REQUIRED',workbook=p['workbook'],plan=bound,failure_type=type(error).__name__,publication_performed=False,automatic_retry=False));raise

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind');a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();bound=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
 if v.action=='owning-child':owning_child(bound,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
 else:print(json.dumps(globals()[v.action](bound)))
if __name__=='__main__':main()
