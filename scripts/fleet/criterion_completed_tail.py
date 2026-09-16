"""Finish an accepted criterion review after an authenticated pre-commit stop.

The actual review and runtime remain consumed. Only original dialogue-commit and
enqueue may run, with no models, fresh authorization, takeover, or publication.
"""
from pathlib import Path
import argparse,contextlib,hashlib,importlib.util,json,os,sys
SCHEMA='fleet-criterion-completed-tail/v1'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(p):
 p=Path(p);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular tail evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'tail evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'tail module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def interrupted(p,prior,value,checkpoint):
 outer=read(p['prior_worker_outcome']);oldjob=read(p['prior_worker_job']);intent=read(outer['intent'])
 require(intent['job']==p['prior_worker_job'] and oldjob['workbook']==p['workbook'] and oldjob['version']==p['version'] and oldjob['source']['sha256']==p['source']['sha256'],'stopped worker identity changed')
 require(outer['status']=='RECONCILIATION_REQUIRED' and outer['failure_type']=='TimeoutError' and outer['returncode'] is None and outer['automatic_retry'] is False,'exact stopped timeout required')
 directory=Path(prior['operations'])/'002';x=read(p['interrupted_intent'])
 require(p['interrupted_intent']==binding(directory/'intent.json') and x['action']=='step' and x['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),'interrupted guard did not select the completed runtime')
 require({r.name for r in directory.iterdir()}=={'intent.json','step.log','declared-runtime-adapter.json'} and (directory/'step.log').stat().st_size==0,'guard has later evidence; reconcile explicitly')
 active=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit() or int(proc.name)==os.getpid():continue
  try:
   if (proc/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':continue
   argv=(proc/'cmdline').read_bytes().decode(errors='replace')
   if oldjob['run'] in argv or p['interrupted_intent']['path'] in argv:active.append(int(proc.name))
  except(FileNotFoundError,PermissionError,ProcessLookupError):pass
 require(not active,'prior worker or owning child still active')

@contextlib.contextmanager
def context(row,*,probe):
 p=read(row);require(set(p)=={'schema_version','helper','prior_plan','prior_worker_job','prior_worker_outcome','authorization_outcome','completed_outcomes','interrupted_intent','tracker','output','operations','owner','workbook','source','version'} and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed criterion tail required')
 prior=read(p['prior_plan']);require(all(p[k]==prior[k]for k in('owner','workbook','version')),'tail changed original criterion custody')
 E=module('criterion_tail_original_execution',prior['entry'])
 try:
  _,runtime,reader,selected,original,env,runner=E.load(p['prior_plan'],probe=probe)
  require(reader.plan['source']==p['source'],'tail source differs from original correction')
  C=selected['campaign'];W=selected['worker'];env=(env[0],C,env[2],W,selected['renewal'],*env[5:])
  D=E.dispatcher(original,selected,runner,plan_binding=p['prior_plan'],entry=prior['entry'],reader=reader)
  E.authorization_outcome(p['authorization_outcome'],p=prior,plan_binding=p['prior_plan'],reader=reader,env=env)
  require(len(p['completed_outcomes'])==2,'exact review and runtime outcomes required')
  for i,b in enumerate(p['completed_outcomes']):
   require(Path(b['path'])==Path(prior['operations'])/('%03d'%i)/'outcome.json','foreign completed criterion stage')
   checkpoint,value=D.successful(b,env)
   require(value['status']=='STEP_COMPLETE' and value['next_step']==('final-runtime','dialogue-commit')[i],'completed criterion phases differ')
  interrupted(p,prior,value,checkpoint)
  worker,controls,_=C.selected(value['descriptor']);_,selection,lease=C._prior(checkpoint,value['descriptor'])
  require(reader.is_card(worker) and worker.root==env[6] and controls==env[7],'retained criterion worker differs')
  require(binding(worker.context.tracker_root/(p['workbook']+'.json'))==p['tracker'] and p['tracker']['sha256']==lease.sha256,'commit may have changed tracker; reconcile')
  W.active(worker,selection,lease)
  require(not W.step_directory(worker,'dialogue-commit').exists() and not W.step_directory(worker,'enqueue').exists(),'owning tail already attempted')
  require(Path(p['output']).parent==Path(p['operations']).parent==Path(env[5]['run_root'])/'dispatcher-v2' and p['operations']!=prior['operations'] and p['output']!=prior['home'],'new sibling tail namespaces required')
  yield p,prior,E,D,env,reader,checkpoint,value
 finally:
  while E._CONTEXTS:E._CONTEXTS.pop().__exit__(None,None,None)

def run(row):
 with context(row,probe=False)as(p,prior,E,D,env,reader,checkpoint,value):
  # Same selected owner, source and code; the new root claim supplies its new
  # token and fence. The original child validates each real heartbeat again.
  E.live(prior,reader)
  output=Path(p['output']);require(not output.exists() and not Path(p['operations']).exists(),'tail namespace consumed; no replay');output.mkdir()
  old=read(read(prior['runtime'])['original_authoring_job']);job={**old,'outcome':p['completed_outcomes'][-1],'operations':p['operations'],'maximum_steps':2,'models':False,'publish':False}
  bound=D.retain(output/'job.json',job);D.run(bound)
  held=read(binding(Path(p['operations'])/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
  result=D.retain(output/'result.json',dict(schema_version=SCHEMA,status=value['status'],workbook=p['workbook'],plan=row,job=bound,config=old['config'],outcome=held['outcome'],checkpoint=checkpoint,models_dispatched=0,publication_performed=False,automatic_retry=False))
  require(value['status']=='PUBLICATION_READY','tail requires reconciliation')
  D.publication_request(env,value)
  return result

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=('probe','run'));a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);v=a.parse_args();row=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
 if v.action=='run':result=run(row)
 else:
  with context(row,probe=True):result=dict(status='CRITERION_COMPLETED_TAIL_VALIDATED',models=0,task_writes=0)
 print(json.dumps(result))
if __name__=='__main__':main()
