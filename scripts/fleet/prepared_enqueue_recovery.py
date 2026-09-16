"""Guarded retained-runtime runner for an explicitly selected prepared enqueue."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys,time
SCHEMA='fleet-prepared-enqueue-recovery/v1'

def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in (p,*p.parents)),'regular recovery evidence required')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'recovery binding changed');return json.loads(Path(row['path']).read_bytes())
def load(name,row):
 require(binding(row['path'])==row,'recovery implementation changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def keep(path,value):
 with Path(path).open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')
 return binding(path)

def context(row):
 p=read(row);require(set(p)=={'schema_version','helper','tail_plan','tail_helper','runtime','runtime_profile','projection','core','failed_outcome','transaction','guard','guard_runtime','output','owner','workbook','source','version'} and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed prepared enqueue runtime required')
 tail=read(p['tail_plan']);require(tail['helper']==p['tail_helper'] and all(p[k]==tail[k]for k in ('runtime','owner','workbook','source','version')),'recovery detached from retained tail')
 old=read(tail['runtime_profile']);new=read(p['runtime_profile']);oldp=read(tail['projection']);newp=read(p['projection'])
 require(set(newp)==set(oldp) and all(newp[k]==oldp[k]for k in oldp if k!='validator'),'only completed historical synchronization may change')
 expected=json.loads(json.dumps(old));found=0
 for factory in expected['factories']:
  if factory.get('certificate')==tail['projection']:factory['certificate']=p['projection'];found+=1
 require(found==1 and expected==new and p['guard']==dict(seconds=3300,memory_bytes=8589934592),'runtime or original recovery guard differs')
 failed=read(p['failed_outcome']);require(Path(p['failed_outcome']['path'])==Path(tail['operations'])/'000/outcome.json' and failed['status']=='RECONCILIATION_REQUIRED','exact owning enqueue failure required')
 g=read(failed['resource']);require(g['returncode']==1 and g['failure']is None,'uncertain resource failure cannot be recovered')
 RT=load('prepared_enqueue_runtime',p['runtime']);scope=RT.installed(p['runtime_profile']);scope.__enter__()
 try:
  entry=RT.load_module('prepared_enqueue_entry',new['entrypoint']);loaded=entry.load(*new['read_call']['args'],probe=True)
  _,_,reader,selected,original,oldenv,runner=loaded;C=selected['campaign'];W=selected['worker'];env=(oldenv[0],C,oldenv[2],W,selected['renewal'],*oldenv[5:])
  D=entry.dispatcher(original,selected,runner,plan_binding=new['read_call']['args'][0],entry=new['entrypoint'],reader=reader)
  checkpoint,value=D.successful(tail['completed_outcomes'][-1],env)
  require(value['status']=='STEP_COMPLETE' and value['next_step']=='enqueue','actual completed dialogue commit required')
  worker,controls,recipe=C.selected(value['descriptor']);prior,selection,lease=C._prior(checkpoint,value['descriptor'])
  intent=read(g['intent']);require(intent['action']=='step' and intent['models']is False and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),'failed guard did not own selected enqueue')
  require(str(worker.run)==newp['worker_run'] and controls==env[7] and reader.is_card(worker),'foreign selected recovery worker')
  directory=W.step_directory(worker,'enqueue');core=load('prepared_enqueue_core',p['core'])
  args=dict(transaction=p['transaction'],failed_intent=binding(directory/'intent.json'),failed_exception=binding(directory/'exception.json'),failed_readiness=binding(directory/'readiness.json'),predecessor=prior['checkpoint'])
  core.validate(W,worker,selection,lease,**args)
  return p,tail,RT,entry,loaded,D,env,worker,selection,lease,value,args,core,scope
 except BaseException:scope.__exit__(*sys.exc_info());raise

def child(row):
 p,tail,RT,entry,loaded,D,env,worker,selection,lease,value,args,core,scope=context(row)
 try:
  prior=read(tail['prior_plan']);M=load('prepared_enqueue_custody',prior['implementation']);M.live_custody(p)
  os.environ.pop('HARBOR_FLEET_CONTINUATION_READ_ONLY',None)
  updated,checkpoint,receipt=core.recover(env[3],worker,selection,lease,**args,directory=Path(p['output'])/'recovery')
  result=env[1]._result('PUBLICATION_READY',value['descriptor'],selection,updated,checkpoint=checkpoint,evidence=[receipt,tail['completed_outcomes'][-1]])
  selected_worker=env[1].selected(result['descriptor'])[0]
  def selected(observed,v):
   require(observed==env and env[1].selected(v['descriptor'])[0]==selected_worker,'foreign recovered publication worker');return selected_worker,env[1].selected(v['descriptor'])[2]
  D.selected=selected;D.declared_namespace['selected']=selected
  request=D.publication_request(env,result);env[2]._checkpoint(request)
  native=keep(Path(p['output'])/'instruction-result.json',result)
  return keep(Path(p['output'])/'result.json',dict(schema_version=SCHEMA,status='PUBLICATION_READY',plan=row,workbook=p['workbook'],instruction_result=native,checkpoint=checkpoint,recovery=receipt,runtime=p['runtime_profile'],models_dispatched=0,publication_performed=False,automatic_retry=False))
 finally:scope.__exit__(None,None,None)

def run(row):
 p=read(row);require(p['helper']==binding(__file__),'recovery driver changed');out=Path(p['output']);require(not out.exists(),'recovery run already attempted');out.mkdir()
 command=[sys.executable,'-I','-B',__file__,'child','--plan',row['path'],'--sha256',row['sha256']]
 guard_runtime=read(p['guard_runtime'])
 require(guard_runtime['schema_version']=='fleet-worker-runtime/v1','closed guardian runtime required')
 for item in guard_runtime['files'].values():require(binding(item['path'])==item,'guardian dependency changed')
 sys.path.insert(0,guard_runtime['root'])
 from scripts.batch.hardened_runner import _run_in_process_group
 keep(out/'intent.json',dict(schema_version=SCHEMA,plan=row,command=command,guard=p['guard'],guard_runtime=p['guard_runtime'],automatic_retry=False))

 metrics={};failure=None;rc=None;start=time.monotonic()
 try:
  with(out/'step.log').open('xb')as log:rc=_run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=out,env=os.environ.copy())
 except BaseException as exc:failure=type(exc).__name__
 resource=keep(out/'resource.json',dict(returncode=rc,failure=failure,elapsed_seconds=time.monotonic()-start,seconds=3300,memory_bytes=8589934592,metrics=metrics,command=command,log=binding(out/'step.log'),models_dispatched=0))
 require(rc==0 and failure is None,'prepared enqueue recovery requires reconciliation')
 require(read(binding(out/'result.json'))['status']=='PUBLICATION_READY','missing recovered ready result');return resource

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['run','child','probe']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);x=a.parse_args();row=dict(path=x.plan,sha256=x.sha256)
 if x.action=='run':print(json.dumps(run(row)))
 elif x.action=='child':print(json.dumps(child(row)))
 else:
  *_,scope=context(row);scope.__exit__(None,None,None);print(json.dumps(dict(status='PREPARED_ENQUEUE_CONTEXT_PASSED',models_dispatched=0,task_mutations=0)))
