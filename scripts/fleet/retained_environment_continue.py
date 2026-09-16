"""Consume an actual native import once, then run ordinary guarded authoring.

The first source/profile step is a real zero-model owning operation. Only its
validated successful outcome becomes the starting point for remaining authoring.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys

def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not p.is_symlink(),'bound native evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'selected continuation bytes changed');return json.loads(Path(row['path']).read_bytes())
def retain(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
 return binding(p)
def load_module(name,row):
 require(binding(row['path'])==row,'selected continuation implementation changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m

def completed_import(plan):
 prior=checked(plan['prior_worker_outcome']);intent=checked(prior['intent']);job=checked(intent['job']);result=checked(plan['import_result'])
 require(prior['schema_version']=='harbor-fleet-worker-outcome/v1' and prior['returncode']==0 and prior['failure_type'] is None and prior['automatic_retry'] is False,'bootstrap worker not complete; reconcile')
 require(intent['job']==plan['prior_worker_job'] and job['workbook']==plan['workbook'] and job['source']['sha256']==plan['source_sha256'] and job['version']==plan['version'],'bootstrap job/source/code differs')
 require(prior['result']==plan['import_result'] and result['status']=='IMPORTED' and result['model_calls']==0 and result['semantic_budget_reset'] is False,'actual unchanged-budget import required')
 if plan['workbook']=='0006':
  require(result['new_admission'] is False and result['config']==plan['config'],'actual source-only import differs');checkpoint=result['checkpoint'];descriptor=result['descriptor']
 else:
  require(plan['workbook']=='0190' and result['emission_replayed'] is False and result['publication_ready'] is False,'existing-review import differs');checkpoint=plan['import_result'];descriptor=result['descriptor']
 value=checked(checkpoint);expected='source' if plan['workbook']=='0006' else 'normalization_phase1'
 require(value['status']=='IMPORTED' and value['step']==expected and value['descriptor']==descriptor,'owning import boundary differs')
 return checkpoint,value

def load(row):
 plan=checked(row);fields={'schema_version','workbook','source_sha256','version','publisher','history_authority','code_root','config','prior_worker_outcome','prior_worker_job','import_result','namespace_adapter','authoring_job','home'}
 require(set(plan)==fields and plan['schema_version']=='harbor-fleet-retained-environment-continuation/v1' and plan['workbook'] in {'0006','0190'},'closed retained continuation plan required')
 meta=checked(plan['namespace_adapter'])
 for b in meta['files']:require(binding(b['path'])==b,'namespace adapter closure changed')
 require(meta['dispatcher'] in meta['files'] and meta['namespace'] in meta['files'],'adapter entry unbound')
 os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN']=row['path'];os.environ['HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256']=row['sha256']
 checkpoint,value=completed_import(plan);job=checked(plan['authoring_job'])
 require(job['driver']==meta['dispatcher'] and job['config']==plan['config'] and job['models'] is True and job['commands'] is True and job['containers'] is True and job['publish'] is False,'remaining capabilities/config differ')
 driver=load_module('selected_retained_environment_dispatcher',meta['dispatcher']);env=driver.setup(job)
 require(env[6]==Path(plan['code_root']) and env[0].SECONDS==3300 and env[0].MEMORY==8*1024**3,'original owning code/guards differ')
 worker,lease,controls=env[0].selected(value['descriptor']);env[0]._prior(checkpoint,value['descriptor'])
 require(worker.bootstrap.workbook==plan['workbook'] and worker.campaign.model_slots==1,'owning workbook/global model limit differs')
 require(not Path(plan['home']).exists() and not Path(job['operations']).exists(),'continuation namespace consumed; reconcile')
 return plan,driver,env,job,checkpoint,value,worker

def probe(row):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting inspection required')
 plan,driver,env,job,checkpoint,value,worker=load(row)
 def forbidden(*args,**kwargs):raise ValueError('models forbidden in continuation probe')
 env[0].executor.execute=forbidden
 return dict(status='PASS',result=dict(plan=row,workbook=plan['workbook'],checkpoint=checkpoint,next_operation='ordinary-zero-model-environment-step',model_slots=1,stage_seconds=3300,memory_limit_bytes=8*1024**3),models_dispatched=0,tracker_modified=False,execution_performed=False)

def run(row):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only continuation cannot execute')
 plan,driver,env,job,checkpoint,value,worker=load(row);base=env[0];base._campaign(worker.campaign)
 driver.namespace.live(plan,driver.namespace.PROFILES[plan['workbook']])
 home=Path(plan['home']);home.mkdir(parents=True);intent=retain(home/'intent.json',dict(plan=row,helper=binding(__file__),automatic_retry=False));outcome=None
 try:
  # Retained IMPORTED evidence is never fabricated as an ordinary guard result.
  # This genuine owning step bridges it before v7.successful may accept it.
  outcome=base.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint,captures=None),env[7],home/'native-bridge',dispatch=False)
  _,first=driver.successful(outcome,env)
  require(first['status'] in {'STEP_COMPLETE','PENDING_MODEL','ENVIRONMENT_READY'},'native bridge did not qualify; reconcile')
  actual=dict(job,outcome=outcome);bound=retain(home/'authoring-job.json',actual);driver.run(bound)
  hold=checked(binding(Path(actual['operations'])/'hold.json'));last,current=driver.successful(hold['outcome'],env)
  require(current['status']=='PUBLICATION_READY','remaining authoring stopped on owning hold')
  result=dict(status='PUBLICATION_READY',workbook=plan['workbook'],plan=row,job=bound,config=plan['config'],outcome=hold['outcome'],checkpoint=last,namespace_adapter=plan['namespace_adapter'],publication_performed=False,automatic_retry=False)
 except Exception as exc:
  result=dict(status='RECONCILIATION_REQUIRED',workbook=plan['workbook'],intent=intent,native_bridge_outcome=outcome,failure_type=type(exc).__name__,publication_performed=False,automatic_retry=False)
  retain(home/'failure.json',dict(failure_type=type(exc).__name__,message=str(exc)))
 return retain(home/'result.json',result)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['probe','run']);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);a=p.parse_args();row=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256);print(json.dumps((probe if a.action=='probe' else run)(row)))
if __name__=='__main__':main()
