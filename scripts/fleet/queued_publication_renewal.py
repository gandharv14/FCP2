"""Root-invoked same-owner lease extension for a fully proved queued publisher.

The caller holds the fleet launch mutex and validates the stopped central claim.
This native runner owns original publication locks and checks stopped processes.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse,hashlib,importlib.util,json,os,sys
SCHEMA='fleet-queued-publication-renewal/v1'

def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute() and p.is_file() and not any(q.is_symlink()for q in(p,*p.parents)),'regular queued renewal input required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'} and binding(row['path'])==row,'queued renewal input changed');return json.loads(Path(row['path']).read_bytes())
def load(name,row):
 require(binding(row['path'])==row,'queued renewal code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def stopped(p):
 job=read(p['job']);outcome=read(p['outcome']);intent=read(outcome['intent'])
 process=None if 'pool_custody'in p else read(p['process'])
 if 'pool_custody'in p:
  c=p['pool_custody'];require(set(c)=={'reader','certificate','host','owner'} and p['process']is None and 'completed_ready'not in p,'closed queued pool custody required')
  reader=load('queued_pool_completion_reader',c['reader'])
  reader.pool_completed(dict(pool_completion=c['certificate'],worker_outcome=p['outcome'],expected_owner=c['owner'],host=c['host'],process=None,process_copy=None),read(p['publication_plan']),read,binding)
 require((process is None or process['job']==p['job']) and p['job']==intent['job'] and outcome['automatic_retry']is False,'exact stopped worker identity required')
 if p.get('completed_ready')is not None:
  reader=load('queued_completed_ready_reader',p['completed_ready_reader'])
  reader.completed_ready(dict(completed_ready=p['completed_ready'],worker_outcome=p['outcome']),read(p['publication_plan']),read,binding)
 else:require(outcome['status']=='PUBLICATION_READY' and outcome['returncode']==0 and outcome['result']['path']==job['result'],'exact stopped queued worker required')
 needles=[job['run'],job['result'],p['job']['path']]
 for entry in Path('/proc').iterdir():
  if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
  try:
   stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
   if stat[0]=='Z':continue
   argv=[x.decode(errors='replace')for x in(entry/'cmdline').read_bytes().split(b'\0')if x]
   require(not any(any(a==n or a.startswith(n+'/')for n in needles)for a in argv),'queued worker or descendant still active')
   require(process is None or int(entry.name)!=process['pid'] or stat[19]!=str(process['start_ticks']),'prior queued worker still active')
  except(FileNotFoundError,ProcessLookupError):continue

@contextmanager
def historical_survey(H,workbook,history,*,legacy=False):
 original=H.projected_survey
 def survey(roots,ids):
  if legacy:
   value=H.validate(history,workbook=workbook,history_roots=roots,known_ids=ids)
   return value['excluded'],value['histories']
  return H.inspect_projection(roots,ids,workbook,history)
 H.projected_survey=survey
 try:yield
 finally:H.projected_survey=original

def publication_context(pub,row):
 """Load the original qualified request without requiring a publication claim."""
 if hasattr(pub,'warning_publication'):
  return pub.warning_publication.context(pub,row,live=False,status_validator=pub.publication_status.validate_status)
 require(hasattr(pub,'fresh_publication') and hasattr(pub,'pinned_dispatcher'),'unsupported queued publication context')
 plan=pub.checked(row);root=Path(plan['code_root']);sys.path.insert(0,str(root))
 history=pub.history_binding(plan);certificate=pub.checked(history)
 completed_semantic=plan.get('semantic_continuation',{}).get('completed_reader')is not None
 derivative=plan.get('source_revision',{}).get('completed_reader')is not None
 if 'model_tail'in plan or completed_semantic or derivative:
  from contextlib import ExitStack
  prefix=pub.fresh_publication.history_prefix(plan)
  os.environ.update(HARBOR_FLEET_WORKBOOK=plan['workbook'])
  os.environ.update({prefix+'PATH':history['path'],prefix+'SHA256':history['sha256']})
  pub.fresh_publication.history_survey(pub,plan,history,pub.fresh_publication.selected_history_survey(plan))
  ready=pub.fresh_publication.ready_value(pub,plan)if derivative else pub.checked(plan['ready']);job=pub.checked(ready['job']);scope=ExitStack()
  try:
   D=pub.fresh_publication.dispatcher(pub,pub.pinned_dispatcher,plan,ready,job)
   if derivative:
    scope.callback(D._derivative_context_manager.__exit__,None,None,None)
    scope.callback(D._source_revision_publication_scope.__exit__,None,None,None)
   elif completed_semantic:
    scope.callback(D._semantic_publication_manager.__exit__,None,None,None)
    scope.callback(D._semantic_publication_scope.__exit__,None,None,None)
   else:scope.callback(D._model_publication_manager.__exit__,None,None,None)
   pub.publication_only();env=D.setup(job);checkpoint,value=D.successful(ready['outcome'],env)
   require(checkpoint==ready['checkpoint']and value['status']=='PUBLICATION_READY','actual completed model queued checkpoint differs')
   request=pub.fresh_publication.projected_request(pub,env[2],D.publication_request(env,value),plan)
   return plan,ready,D,env,request,scope
  except BaseException:scope.close();raise
 legacy=plan.get('legacy_research_history')is not None
 if legacy:
  from scripts.batch import harbor_legacy_research_history as H
 else:
  from scripts.fleet import history as H
 # Original pure proof only; no authoring or coordinator custody is granted.
 prefix=pub.fresh_publication.history_prefix(plan)
 os.environ.update(HARBOR_FLEET_WORKBOOK=plan['workbook'])
 os.environ.update({prefix+'PATH':history['path'],prefix+'SHA256':history['sha256']})
 from contextlib import ExitStack
 scope=ExitStack();scope.enter_context(historical_survey(H,plan['workbook'],history,legacy=legacy))
 try:
  pub.fresh_publication.history_survey(pub,plan,history,H.projected_survey)
  semantic=plan.get('semantic_continuation')is not None
  if not semantic:pub.publication_only()
  ready=pub.checked(plan['ready']);job=pub.checked(ready['job'])
  D=pub.fresh_publication.dispatcher(pub,pub.pinned_dispatcher,plan,ready,job)
  if semantic:
   scope.callback(D._semantic_publication_manager.__exit__,None,None,None)
   pub.publication_only()
  env=D.setup(job)
  checkpoint,value=D.successful(ready['outcome'],env)
  require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY','actual fresh queued checkpoint differs')
  request=pub.fresh_publication.projected_request(pub,env[2],D.publication_request(env,value),plan)
  return plan,ready,D,env,request,scope
 except BaseException:scope.__exit__(*sys.exc_info());raise

def run(row,*,renew=False):
 p=read(row);require(set(p)-{'completed_ready','completed_ready_reader','pool_custody'}=={'schema_version','implementation','publisher','publication_plan','queued_reader','lease_profile','process','job','outcome'} and p['schema_version']==SCHEMA and p['implementation']==binding(__file__),'closed queued publication renewal required')
 require(('completed_ready'in p)==('completed_ready_reader'in p),'complete READY reader declaration required')
 os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','');os.environ['HARBOR_FLEET_PUBLICATION_ONLY']='1';require(not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL'),'queued renewal cannot author')
 pub=load('queued_renewal_publisher',p['publisher']);plan,ready,D,env,request,scope=publication_context(pub,p['publication_plan'])
 try:
  Q=load('queued_renewal_reader',p['queued_reader']);profile=read(p['lease_profile']);P=env[2]
  require(p.get('completed_ready')is not None or plan['ready']==read(p['outcome'])['result'],'renewal must use original fully proved ready request')
  require(plan.get('queued_lease')is None or profile['schema_version']=='harbor-expired-queued-publication/v1',
          'completed queued custody may only precede explicit expired recovery')
  with P.locks(request):
   stopped(p);before=Q.inspect(P,request,profile)
   if not renew:return dict(status='QUEUED_RENEWAL_CONTEXT_VALIDATED',plan=row,tracker=profile['tracker'],models_dispatched=0,lease_mutations=0)
   if profile['schema_version']=='harbor-expired-queued-publication/v1':
    def verify_stopped(observed,old,proof):
     witness=P.read(proof)
     require(witness==dict(schema_version='fleet-expired-queued-stopped-owner/v1',tracker=profile['tracker'],
      process=p['process'],job=p['job'],outcome=p['outcome']) and proof==profile['stopped_owner_proof']
      and observed==P.read(profile['tracker']) and old==observed['leases']['task_agent'],
      'expired queued stopped-owner witness differs')
     stopped(p);return True
    completion=Q.recover(P,request,profile,stopped_validator=verify_stopped)
   else:completion=Q.renew(P,request,profile)
  return dict(status=P.read(completion).get('status','QUEUED_LEASE_RENEWED'),plan=row,completion=completion,models_dispatched=0,automatic_retry=False)
 finally:scope.__exit__(None,None,None)

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--renew',action='store_true');x=a.parse_args();print(json.dumps(run(dict(path=x.plan,sha256=x.sha256),renew=x.renew)))
