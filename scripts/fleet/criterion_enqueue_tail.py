"""Enqueue an accepted criterion dialogue after its actual completed commit.

The scoped historical reader proves the original commit CAS. The unchanged
instruction campaign owns exactly one enqueue, with no author, review or renewal.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,contextlib,hashlib,importlib.util,json,os,sys,time
SCHEMA='fleet-criterion-enqueue-tail/v1'
RETRY='fleet-criterion-enqueue-tail/v2'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),'regular enqueue evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'enqueue evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'enqueue module changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def evidence(p,reader):
 certificate=read(p['certificate'])
 return [*reader.dependencies(),p['entry'],p['certificate'],certificate['implementation'],certificate['validator'],certificate['after'],*([certificate['routing']['implementation']]if certificate['schema_version']=='harbor-completed-lane-projection/v3'else [])]

def stopped(p,prior,checkpoint,value):
 old=read(p['prior_worker_job']);outer=read(p['prior_worker_outcome']);intent=read(outer['intent']);process=read(p['prior_process'])
 require(process['job']==p['prior_worker_job'] and process['automatic_retry']is False and type(process['pid'])is int and process['pid']>0 and isinstance(process['start_ticks'],str)and process['start_ticks'].isdigit(),'authentic prior process required')
 require(process['command'][process['command'].index('--job')+1]==p['prior_worker_job']['path'] and process['command'][process['command'].index('--job-sha256')+1]==p['prior_worker_job']['sha256'],'prior process command differs')
 require(intent['job']==p['prior_worker_job'] and old['workbook']==p['workbook'] and old['version']==p['version'] and old['source']==p['source'],'stopped criterion worker differs')
 require(outer['status']=='RECONCILIATION_REQUIRED' and outer['automatic_retry']is False and outer['returncode']==1 and outer['failure_type']is None,'actual stopped pre-enqueue worker required')
 failed=read(p['failed_outcome']);directory=Path(p['failed_outcome']['path']).parent
 if p['schema_version']==RETRY:
  previous=read(p['previous_enqueue'])
  require(previous['schema_version']==SCHEMA and binding(previous['entry']['path'])==previous['entry'] and old['inputs'][0]==p['previous_enqueue'] and old['result']==previous['output']+'/result.json','exact previous enqueue worker required')
  require(all(previous[k]==p[k]for k in('completed_outcome','prior_plan','owner','workbook','source','version')) and directory==Path(previous['operations'])/'000','enqueue retry changed completed history')
  read(previous['failed_outcome']);read(previous['prior_worker_outcome'])
 else:require(Path(p['completed_outcome']['path']).parent==directory.parent/'000' and directory.name=='001','exact original tail boundary required')
 require(failed['status']=='RECONCILIATION_REQUIRED' and failed['automatic_retry']is False and failed.get('result')is None and failed['resource']==binding(directory/'resource.json'),'failed envelope changed')
 resource=read(failed['resource']);guard=read(resource['intent'])
 require(resource['returncode']==1 and resource['failure']is None and resource['intent']==binding(directory/'intent.json') and resource['log']==binding(directory/'step.log'),'failed guard changed')
 require(guard['action']=='step' and guard['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),'failed guard selected a different checkpoint')
 require(not(directory/'result.json').exists(),'failed enqueue has a result; reconcile')
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit()or int(proc.name)==os.getpid():continue
  try:
   stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
   if stat[0]=='Z':continue
   argv=(proc/'cmdline').read_bytes().decode(errors='replace')
   same_process=int(proc.name)==process['pid'] and len(stat)>19 and stat[19]==process['start_ticks']
   require(not same_process and not any(n in argv for n in (old['run'],old['result'],p['prior_worker_job']['path'],resource['intent']['path'])),'prior worker still active')
  except(FileNotFoundError,ProcessLookupError):pass

@contextlib.contextmanager
def context(row,*,probe):
 p=read(row);fields={'schema_version','entry','prior_plan','prior_worker_job','prior_worker_outcome','prior_process','completed_outcome','failed_outcome','certificate','output','operations','owner','workbook','source','version'}
 require(set(p)==(fields|{'previous_enqueue'}if p['schema_version']==RETRY else fields) and p['schema_version']in(SCHEMA,RETRY) and p['entry']==binding(__file__),'closed enqueue-only selection required')
 prior=read(p['prior_plan']);require(all(prior[k]==p[k]for k in('owner','workbook','version')),'criterion custody changed')
 E=module('criterion_enqueue_original_execution',prior['entry'])
 try:
  _,runtime,reader,selected,original,env,runner=E.load(p['prior_plan'],probe=probe)
  require(reader.plan['source']==p['source'],'criterion source changed')
  cert=read(p['certificate']);projection=module('criterion_enqueue_completed_projection',cert['implementation'])
  C=selected['campaign'];W=selected['worker'];projection.install_projection(W,p['certificate'],reader,router=selected['q']if cert['schema_version']=='harbor-completed-lane-projection/v3'else None)
  env=(env[0],C,env[2],W,selected['renewal'],*env[5:])
  proxy=SimpleNamespace(dependencies=lambda:evidence(p,reader))
  D=E.dispatcher(original,selected,runner,plan_binding=row,entry=p['entry'],reader=proxy)
  checkpoint,value=D.successful(p['completed_outcome'],env)
  require(value['status']=='STEP_COMPLETE' and value['next_step']=='enqueue' and value['checkpoint']==cert['completion'],'actual completed dialogue commit required')
  worker,controls,_=C.selected(value['descriptor']);_,selection,lease=C._prior(checkpoint,value['descriptor'])
  require(reader.is_card(worker) and controls==env[7] and worker.root==env[6] and str(worker.run)==cert['worker_run'],'original criterion worker changed')
  live=binding(worker.context.tracker_root/(p['workbook']+'.json'));require(live['path']==cert['live_tracker'] and live['sha256']==cert['after']['sha256'],'current tracker differs from completed commit')
  reader.historical_lease(worker,read(live));W.active(worker,selection,lease)
  require(read(live)['leases']['task_agent']['expires_at']-time.time_ns()>(3330+60)*10**9,'insufficient native enqueue runway')
  require(not W.step_directory(worker,'enqueue').exists(),'owning enqueue already attempted')
  stopped(p,prior,checkpoint,value)
  root=Path(env[5]['run_root'])/'dispatcher-v2'
  require(Path(p['output']).parent==Path(p['operations']).parent==root and p['output']!=p['operations'] and p['operations']!=prior['operations'],'new sibling enqueue namespaces required')
  yield p,prior,E,D,env,reader,selected,runner,checkpoint,value
 finally:
  while E._CONTEXTS:E._CONTEXTS.pop().__exit__(None,None,None)

def run(row):
 with context(row,probe=False)as(p,prior,E,D,env,reader,selected,runner,checkpoint,value):
  E.live(prior,reader)
  output=Path(p['output']);require(not output.exists() and not Path(p['operations']).exists(),'enqueue namespace consumed');output.mkdir()
  runtime=read(prior['runtime']);old=read(runtime['original_authoring_job']);job={**old,'outcome':p['completed_outcome'],'operations':p['operations'],'maximum_steps':1,'models':False,'publish':False}
  bound=D.retain(output/'job.json',job);D.run(bound)
  held=read(binding(Path(p['operations'])/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
  require(value['status']=='PUBLICATION_READY','enqueue held; reconcile without replay')
  D.publication_request(env,value)
  return D.retain(output/'result.json',dict(schema_version=SCHEMA,status='PUBLICATION_READY',PUBLICATION_READY=True,workbook=p['workbook'],plan=row,job=bound,config=old['config'],outcome=held['outcome'],checkpoint=checkpoint,models_dispatched=0,publication_performed=False,automatic_retry=False))

def child(row,intent):
 with context(row,probe=False)as(p,prior,E,D,env,reader,selected,runner,checkpoint,value):
  producer=selected['campaign'];runner.validate_child(producer,base=env[0],kind='instruction',reader_plan=row,child_wrapper=p['entry'],intent=intent,reader_evidence=evidence(p,reader))
  guard=read(intent);require(guard['action']=='step' and guard['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint) and Path(intent['path'])==Path(p['operations'])/'000/intent.json','only original enqueue step is authorized')
  E.live(prior,reader);producer.child(intent)

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=('probe','run','owning-child'));a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind');a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
 if v.action=='owning-child':require(v.kind=='instruction' and v.intent and v.intent_sha256,'only instruction child allowed');child(row,dict(path=v.intent,sha256=v.intent_sha256));return
 require(v.kind is v.intent is v.intent_sha256 is None,'unexpected child fields')
 if v.action=='run':result=run(row)
 else:
  with context(row,probe=True):result=dict(status='CRITERION_ENQUEUE_ONLY_VALIDATED',models=0,task_writes=0,lease_mutations=0)
 print(json.dumps(result))
if __name__=='__main__':main()
