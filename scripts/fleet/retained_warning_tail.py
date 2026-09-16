"""Finish an exact retained warning checkpoint without repeating completed phases."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys,time
SCHEMA='harbor-retained-warning-tail/v1'
FIELDS={'schema_version','workbook','helper','prior_helper','prior_plan','reader','reader_profile','completed_outcomes','failed_outcome','output','owner','fence','source_sha256','capacity_policy'}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(p):
 p=Path(p).absolute();require(p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular bound tail file required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'tail evidence differs');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'tail module differs');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def context(row):
 p=read(row);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed retained tail plan required')
 prior=read(p['prior_plan']);require(prior['helper']==p['prior_helper'] and prior['workbook']==p['workbook'] and prior['source_sha256']==p['source_sha256'] and prior['capacity_policy']==p['capacity_policy'],'tail detached from original warning authority')
 M=module('retained_warning_parent',p['prior_helper'])
 profile=read(prior['warning_profile']);new=read(p['reader_profile'])
 require(new=={**profile,'adapter':p['reader'],'completed_lane_history':new.get('completed_lane_history'),'completed_lane':new.get('completed_lane')},'only exact completed lane history may extend retained warning reader')
 reader=module('retained_warning_completed_reader',p['reader']);original_loader=M.module
 # Declare the selected reader at the original installation boundary. Installing
 # twice would wrap an already scoped historical reader and lose its provenance.
 def selected_loader(name,bound):
  if name=='selected_warning_history' and bound==prior['warning_reader']:
   import types
   def install(bound_profile,**kw):
    require(bound_profile==prior['warning_profile'],'foreign prior warning profile')
    return reader.install(p['reader_profile'],**kw)
   return types.SimpleNamespace(install=install)
  return original_loader(name,bound)
 M.module=selected_loader
 values=M.context(p['prior_plan']);old,Mprep,prep,D,env,w,selection,lease,recipe,profile,manager=values
 try:
  C,I,P,W,R,config,root,controls=env
  require(len(p['completed_outcomes'])==3,'exact completed warning review/runtime/commit required')
  checkpoint=None;value=None
  for index,bound in enumerate(p['completed_outcomes']):
   require(Path(bound['path'])==Path(prior['output'])/('step-%02d'%index)/'outcome.json','completed warning outcome namespace changed')
   checkpoint,value=D.successful(bound,env)
   require(value['status']=='STEP_COMPLETE' and value['next_step']==('final-runtime','dialogue-commit','enqueue')[index],'completed warning stages skipped')
  failed=read(p['failed_outcome']);require(Path(p['failed_outcome']['path'])==Path(prior['output'])/'step-03/outcome.json' and failed['status']=='RECONCILIATION_REQUIRED','retained failed enqueue boundary required')
  guard=read(failed['resource']);intent=read(guard['intent'])
  require(guard['returncode']==1 and guard['failure']is None and intent['action']=='step' and intent['models']is False and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),'failure did not precede exact next enqueue')
  # Full selected child intent is retained; no owning enqueue intent was created.
  selected_worker,_,_=I.selected(value['descriptor'])
  require(selected_worker==w and not (W.step_directory(w,'enqueue')/'intent.json').exists(),'owning enqueue already started; explicit reconciliation required')
  require(Path(p['output']).parent==Path(prior['output']).parent and Path(p['output'])!=Path(prior['output']) and p['owner']==prior['owner'] and type(p['fence'])is int and p['fence']>prior['fence'],'new root-selected tail custody required')
  return p,M,prior,D,env,w,checkpoint,value,manager
 except BaseException:manager.__exit__(*sys.exc_info());raise

def bootstrap(row,C,I,M):
 original=C._run_in_process_group;p=read(row)
 def run(command,*args,**kwargs):
  require(len(command)==6 and command[1:4]==['-B',str(Path(I.__file__).resolve()),'_child'],'only original instruction child')
  intent=dict(path=command[4],sha256=command[5]);v=read(intent)
  require(v['action']=='step' and v['models']is False and v['code']==binding(I.__file__) and v['base_code']==binding(C.__file__),'original enqueue child changed')
  side=M.keep(Path(intent['path']).parent/'retained-tail-bootstrap.json',dict(schema_version=SCHEMA,plan=row,intent=intent,original_command=command,models=False))
  return original([command[0],'-I','-B',p['helper']['path'],'child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']],*args,**kwargs)
 C._run_in_process_group=run

def child(row,intent,side):
 require(sys.flags.isolated and sys.dont_write_bytecode,'cold isolated tail required')
 p,M,prior,D,env,w,checkpoint,value,manager=context(row)
 try:
  C,I=env[:2];v=read(side)
  require(v==dict(schema_version=SCHEMA,plan=row,intent=intent,original_command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']],models=False) and Path(side['path'])==Path(intent['path']).parent/'retained-tail-bootstrap.json','tail child sidecar changed')
  x=read(intent);require(x['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint) and x['models']is False,'child cannot repeat completed steps')
  return I.child(intent)
 finally:manager.__exit__(*sys.exc_info())

def run(row):
 p,M,prior,D,env,w,checkpoint,value,manager=context(row)
 try:
  C,I,P,W,R,config,root,controls=env
  require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','readonly tail cannot run')
  M.active({**prior,'owner':p['owner'],'fence':p['fence']},env)
  _,selected,lease=I._prior(checkpoint,value['descriptor'])
  W.active(w,selected,lease)
  out=Path(p['output']);require(not out.exists() and out.parent.is_dir(),'fresh tail output required');out.mkdir()
  intent=M.keep(out/'intent.json',dict(plan=row,retained=p['completed_outcomes'],next_step='enqueue',models=False,automatic_retry=False))
  bootstrap(row,C,I,M)
  with D.fleet_slot(p['workbook'])as fds:
   outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,out/'enqueue',models=False,commands=True,containers=True,guardian_lock_fds=fds)
   final,ready=D.successful(outcome,env);require(ready['status']=='PUBLICATION_READY','enqueue not ready')
   D.publication_request(env,ready)
  return M.keep(out/'result.json',dict(schema_version=SCHEMA,status='PUBLICATION_READY',workbook=p['workbook'],plan=row,intent=intent,config=prior['config'],retained_outcomes=p['completed_outcomes'],outcome=outcome,checkpoint=final,models_dispatched=0,publication_performed=False,automatic_retry=False))
 finally:manager.__exit__(*sys.exc_info())

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['run','probe','child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True)
 for n in('intent','intent-sha256','bootstrap','bootstrap-sha256'):a.add_argument('--'+n)
 x=a.parse_args();row=dict(path=str(Path(x.plan).absolute()),sha256=x.sha256)
 if x.action=='child':result=child(row,dict(path=x.intent,sha256=x.intent_sha256),dict(path=x.bootstrap,sha256=x.bootstrap_sha256))
 elif x.action=='run':result=run(row)
 else:
  *_,manager=context(row);manager.__exit__(None,None,None);result=dict(status='RETAINED_WARNING_TAIL_VALIDATED',models=0,task_writes=0)
 print(json.dumps(result))
if __name__=='__main__':main()
