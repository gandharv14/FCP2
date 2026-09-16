"""Publication-only retained warning lineage and explicit cold dependency loader."""
from pathlib import Path
import argparse,json,os,sys,time
KEY='retained_warning'
SCHEMA='harbor-retained-warning-publication/v1'
DIRECT_FIELDS={'continuation_plan','continuation_helper','root_launch','job'}

def direct_history(plan,*,checked,binding,require):
 """Authenticate the actual four-stage direct continuation, not a fresh claim."""
 a=plan[KEY];require(set(a)==DIRECT_FIELDS,'closed direct warning publication history')
 prior=checked(a['continuation_plan']);ready=checked(plan['ready']);job=checked(a['job'])
 require(prior['schema_version']=='harbor-direct-warning-continuation/v1'
  and prior['helper']==a['continuation_helper'] and prior['workbook']==plan['workbook']
  and prior['source_sha256']==plan['source_sha256'],'direct warning source changed')
 snapshot=checked(prior['snapshot'])
 require(snapshot['root']==plan['code_root'],'direct warning snapshot differs')
 require(ready['schema_version']=='harbor-retained-warning-continuation/v1'
  and ready['status']=='PUBLICATION_READY' and ready['plan']==a['continuation_plan']
  and ready['workbook']==plan['workbook'] and ready['config']==prior['config']
  and ready['takeover_profile']==prior['takeover_plan'] and ready['models_dispatched']==0
  and ready['publication_performed']is False and ready['automatic_retry']is False
  and len(ready['outcomes'])==4 and ready['outcome']==ready['outcomes'][-1],
  'actual four-stage direct READY required')
 require(plan['ready']['path']==str(Path(prior['output'])/'result.json')
  and all(row['path']==str(Path(prior['output'])/('step-%02d'%i)/'outcome.json')
   for i,row in enumerate(ready['outcomes'])),'direct completed namespace differs')
 require(job['inputs'][0]==a['continuation_plan'] and job['driver']==a['continuation_helper']
  and job['result']==plan['ready']['path'] and job['source']['sha256']==plan['source_sha256']
  and job['version']==Path(plan['code_root']).name,'direct publication detached from actual worker')
 launch=checked(a['root_launch']);intent=checked(launch['intent']);root=checked(intent['plan'])
 require(root['job']==a['job'] and launch['remote']['job']==a['job']
  and launch['remote']['root_intent']==launch['intent'] and launch['automatic_retry']is False,
  'actual direct launch chain changed')
 direct_inputs(a['continuation_plan'],prior,job,checked=checked,binding=binding,require=require,root=root)
 return a,prior,ready

def direct_inputs(authority,prior,job,*,checked,binding,require,root=None):
 """Authenticate the archived admission tracker after the owning stages advance it."""
 semantic=prior.get('live_renewal',{}).get('completed_semantic')
 resumed=None
 if prior.get('resume'):
  require(root is not None,'resumed admission requires actual root launch')
  previous=checked(prior['resume']['prior_plan']);runway=checked(root['native_lease_runway'])
  require(previous['live_renewal']==prior['live_renewal'] and previous['workbook']==prior['workbook']
   and runway['workbook']==prior['workbook'] and runway['source_sha256']==prior['source_sha256'],
   'resumed admission source or renewal changed')
  resumed=runway['tracker'];copies=checked(root['offline_bindings'])['copies']
  found=[x for x in copies if x['native']==resumed]
  require(job['inputs'].count(resumed)==1 and len(found)==1,'unique actual resumed admission input required')
  archive=found[0]['copy'];require(archive['sha256']==resumed['sha256'] and binding(archive['path'])==archive,
   'resumed admission archive changed')
  before=checked(archive)
  require(before['workbook_id']==prior['workbook'] and before['bindings']['original_raw_source']['sha256']==prior['source_sha256'],
   'resumed admission archive belongs to another source')
 for row in job['inputs']:
  if row==resumed:continue
  if semantic is None or row!=semantic['tracker']:
   require(binding(row['path'])==row,'direct job dependency changed');continue
  home=Path(prior['output']);archive=binding(home/'semantic-live-before.json')
  require(archive['sha256']==row['sha256'],'admission tracker archive differs')
  before=checked(archive)
  require(before['workbook_id']==prior['workbook']
   and before['bindings']['original_raw_source']['sha256']==prior['source_sha256'],
   'admission tracker belongs to another source')
  retained=checked(binding(home/'live-renewal/retained-live.json'))
  require(retained['schema_version']=='fleet-retained-live-continuation-lease/v1'
   and retained['authority']==authority and retained['tracker']==row
   and archive in retained['validated_evidence'] and retained['lease_operations']==0
   and retained['automatic_retry']is False and retained['semantic_budget_reset']is False
   and retained['lease']['sha256']==row['sha256']
   and retained['lease']['revision']==before['revision']
   and retained['lease']['owner']==before['leases']['task_agent']['owner'],
   'admission tracker lacks original retained custody')

def history(plan,*,checked,binding,require):
 require(set(plan[KEY])=={'tail_plan','tail_helper','original_history_proof','original_history_guard','root_launch','job'},'closed retained warning publication history')
 a=plan[KEY];tail=checked(a['tail_plan']);prior=checked(tail['prior_plan']);ready=checked(plan['ready']);job=checked(a['job'])
 require(tail['schema_version']=='harbor-retained-warning-tail/v1' and tail['helper']==a['tail_helper'] and tail['workbook']==prior['workbook']==plan['workbook'] and tail['source_sha256']==prior['source_sha256']==plan['source_sha256'],'retained warning source changed')
 require(ready['schema_version']==tail['schema_version'] and ready['status']=='PUBLICATION_READY' and ready['plan']==a['tail_plan'] and ready['workbook']==plan['workbook'] and ready['config']==prior['config'] and ready['retained_outcomes']==tail['completed_outcomes'] and ready['models_dispatched']==0 and ready['publication_performed']is False and ready['automatic_retry']is False,'actual retained enqueue READY required')
 require(plan['ready']['path']==str(Path(tail['output'])/'result.json') and ready['outcome']['path']==str(Path(tail['output'])/'enqueue/outcome.json'),'actual tail completion namespace differs')
 require(job['inputs'][0]==a['tail_plan'] and job['driver']==a['tail_helper'] and job['result']==plan['ready']['path'] and job['source']['sha256']==plan['source_sha256'] and job['version']==Path(plan['code_root']).name,'publication detached from actual worker')
 launch=checked(a['root_launch']);intent=checked(launch['intent']);root=checked(intent['plan'])
 require(root['job']==a['job'] and launch['remote']['job']==a['job'] and launch['remote']['root_intent']==launch['intent'] and launch['automatic_retry']is False,'actual launch chain changed')
 for row in job['inputs']:require(binding(row['path'])==row,'retained job dependency changed')
 base=checked(prior['base_remaining_plan']);prep=checked(base['prepare_plan']);retained=checked(prep['retained_plan'])
 proof=checked(a['original_history_proof']);guard=checked(a['original_history_guard']);seed=checked(proof['authority'])
 require(proof['status']=='FULL_ORIGINAL_HISTORY_VALIDATED' and proof['retained_plan']==prep['retained_plan'] and proof['history']==retained['history_authority']['existing_review_history'] and proof['lineage']==retained['history_authority']['existing_review_lineage'] and proof['models_dispatched']==proof['owning_mutations']==0,'complete original refresh history required')
 last=checked(tail['completed_outcomes'][-1])
 require(seed['checkpoint']==last['result']==proof['checkpoint'] and seed['remaining_plan']==tail['prior_plan'] and seed['retained_plan']==prep['retained_plan'],'old history check detached from current completed commit')
 require(guard['returncode']==0 and guard['failure']is None and guard['seconds']==3300 and guard['memory_bytes']==8589934592 and json.loads(guard['log'])==proof,'full original refresh guard changed')
 return a,tail,prior,ready

def history_binding(plan,**kw):
 if 'criterion_history'in plan:
  criterion_history(plan,**kw);return plan['criterion_history']['enqueue_plan']
 if 'continuation_plan' in plan[KEY]:
  direct_history(plan,**kw);return plan[KEY]['continuation_plan']
 history(plan,**kw);return plan[KEY]['tail_plan']

def evidence(plan):
 if 'criterion_history'in plan:return [*plan['criterion_history'].values(),plan['ready'],*plan.get('queued_lease',{}).values()]
 a=plan[KEY]
 if 'continuation_plan' in a:return [a['continuation_plan'],a['continuation_helper'],plan['ready'],a['job'],a['root_launch'],*plan.get('queued_lease',{}).values()]
 return [a['tail_plan'],a['tail_helper'],a['original_history_proof'],a['original_history_guard'],plan['ready'],a['job'],a['root_launch']]

def direct_context(pub,plan_binding,*,live,status_validator):
 plan=pub.checked(plan_binding)
 a,prior,ready=direct_history(plan,checked=pub.checked,binding=pub.binding,require=pub.require)
 M=pub.load('publication_direct_warning',a['continuation_helper'])
 p,prepare,prep,D,env,w,selection,lease,recipe,profile,manager=M.context(a['continuation_plan'])
 try:
  C,I,P,W,R,config,root,controls=env;require=pub.require
  require(root==Path(plan['code_root']) and prior['config']==ready['config']
   and plan['verifier']==D.VERIFIER,'direct publication owning context changed')
  previous=pub.binding(Path(prior['output'])/'handoff/result.json')
  previous_value=pub.checked(previous)
  require(previous_value['status']=='RETRY_READY' and previous_value['next_step']=='dialogue-review',
   'original direct warning handoff differs')
  for index,row in enumerate(ready['outcomes']):
   result,value=D.successful(row,env);resource=pub.checked(pub.checked(row)['resource'])
   intent=resource['intent'];actual=pub.checked(intent)
   require(actual['models']is False and actual['action']=='step'
    and actual['arguments']==dict(descriptor=previous_value['descriptor'],checkpoint=previous),
    'direct publication skipped or repeated a phase')
   M.validate_bootstrap(pub.binding(Path(row['path']).parent/'warning-bootstrap.json'),
    a['continuation_plan'],intent,
    [sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']])
   require(value['next_step']==('final-runtime','dialogue-commit','enqueue',None)[index]
    and value['status']==('PUBLICATION_READY'if index==3 else'STEP_COMPLETE'),
    'direct publication phase completion differs')
   previous,previous_value=result,value
  require(previous==ready['checkpoint'],'direct enqueue checkpoint differs')
  request=queued_request(pub,plan,P,D.publication_request(env,previous_value))
  def forbidden(*args,**kwargs):raise RuntimeError('publication cannot execute models or authoring')
  import xl_transport
  from scripts.batch import harbor_model_executor as E
  for module,names in((xl_transport,('read_response','_read_response','model_slot')),
   (E,('execute','_open')),(C,('run',)),(I,('run',)),(W,('step','run'))):
   for name in names:
    if hasattr(module,name):setattr(module,name,forbidden)
  if live:
   require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1'
    and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL')
    and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'live publication-only custody required')
   auth=dict(workbook=os.environ['HARBOR_FLEET_WORKBOOK'],owner=os.environ['HARBOR_FLEET_OWNER'],
    token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
   require(auth['workbook']==plan['workbook'],'direct publication workbook changed')
   from scripts.fleet.coordinator import Client
   client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
   status_validator(client.call('status'),auth,plan_binding,plan,plan['source_sha256'],now=time.time(),require=require)
   client.call('heartbeat',**auth,lease_seconds=180)
  return plan,ready,D,env,request,manager
 except BaseException:manager.__exit__(*sys.exc_info());raise

def queued_request(pub,plan,P,request):
 """Select a proved lease-only projection after the original enqueue history."""
 queued=plan.get('queued_lease')
 if queued is None:return request
 pub.require(set(queued)=={'reader','completion'},'closed queued direct custody required')
 Q=pub.load('direct_warning_queued_custody',queued['reader'])
 return Q.projected(P,request,queued['completion'])

def context(pub,plan_binding,*,live,status_validator):
 plan=pub.checked(plan_binding)
 if 'criterion_history'in plan:return criterion_context(pub,plan_binding,live=live,status_validator=status_validator)
 if 'continuation_plan' in plan[KEY]:return direct_context(pub,plan_binding,live=live,status_validator=status_validator)
 a,tail,prior,ready=history(plan,checked=pub.checked,binding=pub.binding,require=pub.require)
 require=pub.require
 M=pub.load('publication_original_warning',tail['prior_helper'])
 p,prepare,prep,D,env,w,selection,lease,recipe,profile,manager=M.context(tail['prior_plan'])
 try:
  C,I,P,W,R,config,root,controls=env
  require(root==Path(plan['code_root']) and prior['config']==ready['config'] and plan['verifier']==D.VERIFIER,'publication owning context changed')
  # Validate all actual guarded phases and their declared cold bootstrap. The
  # enqueue reader is not re-entered as an authoring eligibility check here.
  previous=None
  for index,row in enumerate(tail['completed_outcomes']):
   result,value=D.successful(row,env);resource=pub.checked(pub.checked(row)['resource']);intent=resource['intent']
   command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']]
   M.validate_bootstrap(pub.binding(Path(row['path']).parent/'warning-bootstrap.json'),tail['prior_plan'],intent,command)
   require(value['next_step']==('final-runtime','dialogue-commit','enqueue')[index],'completed phase differs')
   previous=result
  result,value=D.successful(ready['outcome'],env);require(result==ready['checkpoint'],'enqueue checkpoint differs')
  resource=pub.checked(pub.checked(ready['outcome'])['resource']);intent=resource['intent'];actual=pub.checked(intent)
  require(actual['models']is False and actual['arguments']==dict(descriptor=value['descriptor'],checkpoint=previous),'enqueue repeated or skipped prior work')
  side=pub.checked(pub.binding(Path(intent['path']).parent/'retained-tail-bootstrap.json'))
  require(side==dict(schema_version=tail['schema_version'],plan=a['tail_plan'],intent=intent,original_command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']],models=False),'cold enqueue dependency differs')
  request=D.publication_request(env,value)
  def forbidden(*args,**kwargs):raise RuntimeError('publication cannot execute models or authoring')
  import xl_transport
  from scripts.batch import harbor_model_executor as E
  for module,names in((xl_transport,('read_response','_read_response','model_slot')),(E,('execute','_open')),(C,('run',)),(I,('run',)),(W,('step','run'))):
   for name in names:
    if hasattr(module,name):setattr(module,name,forbidden)
  if live:
   require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL') and not os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY'),'live publication-only custody required')
   auth=dict(workbook=os.environ['HARBOR_FLEET_WORKBOOK'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
   require(auth['workbook']==plan['workbook'],'publication workbook changed')
   from scripts.fleet.coordinator import Client
   client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
   status_validator(client.call('status'),auth,plan_binding,plan,plan['source_sha256'],now=time.time(),require=require)
   client.call('heartbeat',**auth,lease_seconds=180)
  return plan,ready,D,env,request,manager
 except BaseException:manager.__exit__(*sys.exc_info());raise

def criterion_history(plan,*,checked,binding,require):
 """Select the actual corrected-dialogue enqueue, not pre-enqueue eligibility."""
 a=plan['criterion_history'];require(set(a)=={'enqueue_plan','enqueue_helper','root_launch','job'},'closed criterion publication history')
 p=checked(a['enqueue_plan']);prior=checked(p['prior_plan']);ready=checked(plan['ready']);job=checked(a['job'])
 require(p['schema_version']=='fleet-criterion-enqueue-tail/v2' and p['entry']==a['enqueue_helper']
  and p['workbook']==plan['workbook'] and p['source']['sha256']==plan['source_sha256']
  and p['version']==Path(plan['code_root']).name and prior['workbook']==p['workbook']
  and prior['version']==p['version'],'criterion source/version identity differs')
 require(ready['schema_version']=='fleet-criterion-enqueue-tail/v1' and ready['status']=='PUBLICATION_READY'
  and ready['PUBLICATION_READY']is True and ready['plan']==a['enqueue_plan']
  and ready['workbook']==plan['workbook'] and ready['models_dispatched']==0
  and ready['publication_performed']is False and ready['automatic_retry']is False
  and plan['ready']['path']==str(Path(p['output'])/'result.json')
  and ready['outcome']['path']==str(Path(p['operations'])/'000/outcome.json')
  and ready['checkpoint']['path']==str(Path(p['operations'])/'000/result.json'),
  'actual original criterion enqueue result required')
 require(job['inputs'][0]==a['enqueue_plan'] and job['driver']==a['enqueue_helper']
  and job['source']==p['source'] and job['version']==p['version'] and job['result']==plan['ready']['path'],
  'criterion enqueue detached from worker')
 launch=checked(a['root_launch']);intent=checked(launch['intent']);root=checked(intent['plan'])
 require(root['job']==a['job'] and launch['remote']['job']==a['job']
  and launch['remote']['root_intent']==launch['intent'] and launch['automatic_retry']is False,
  'actual criterion root launch changed')
 for row in job['inputs']:require(binding(row['path'])==row,'criterion job input changed')
 return a,p,prior,ready

def criterion_context(pub,row,*,live,status_validator):
 from types import SimpleNamespace
 from contextlib import ExitStack
 plan=pub.checked(row);a,p,prior,ready=criterion_history(plan,checked=pub.checked,binding=pub.binding,require=pub.require)
 M=pub.load('publication_original_criterion_enqueue',a['enqueue_helper'])
 E=pub.load('publication_original_criterion_execution',prior['entry']);manager=ExitStack()
 def close():
  while E._CONTEXTS:E._CONTEXTS.pop().__exit__(None,None,None)
 manager.callback(close)
 try:
  _,runtime,reader,selected,original,oldenv,runner=E.load(p['prior_plan'],probe=True)
  cert=pub.checked(p['certificate']);projection=pub.load('publication_criterion_projection',cert['implementation'])
  C=selected['campaign'];W=selected['worker'];projection.install_projection(W,p['certificate'],reader,router=selected['q'])
  env=(oldenv[0],C,oldenv[2],W,selected['renewal'],*oldenv[5:]);P=env[2]
  D=E.dispatcher(original,selected,runner,plan_binding=a['enqueue_plan'],entry=a['enqueue_helper'],reader=SimpleNamespace(dependencies=lambda:M.evidence(p,reader)))
  checkpoint,value=D.successful(ready['outcome'],env)
  pub.require(checkpoint==ready['checkpoint'] and value['status']=='PUBLICATION_READY' and value['next_step']is None
   and value['publication_performed']is False and str(env[6])==plan['code_root']
   and ready['config']==pub.checked(runtime['original_authoring_job'])['config']
   and plan['verifier']==D.VERIFIER,'original criterion publication context differs')
  request=D.publication_request(env,value);P._checkpoint(request)
  queued=plan.get('queued_lease')
  if queued is not None:
   pub.require(set(queued)=={'reader','completion'},'closed queued criterion custody required')
   Q=pub.load('criterion_queued_custody',queued['reader']);request=Q.projected(P,request,queued['completion'])
  def forbidden(*args,**kwargs):raise RuntimeError('publication cannot author or execute models')
  import xl_transport
  from scripts.batch import harbor_model_executor as executor
  for mod,names in((xl_transport,('read_response','_read_response','model_slot')),(executor,('execute','_open')),
   (env[0],('run',)),(C,('run',)),(W,('step','run'))):
   for name in names:
    if hasattr(mod,name):setattr(mod,name,forbidden)
  if live:
   pub.require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL'),'live criterion publication-only custody required')
   os.environ.pop('HARBOR_FLEET_CONTINUATION_READ_ONLY',None)
   auth=dict(workbook=os.environ['HARBOR_FLEET_WORKBOOK'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
   pub.require(auth['workbook']==plan['workbook'],'criterion publication workbook differs')
   from scripts.fleet.coordinator import Client
   client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE'])
   status_validator(client.call('status'),auth,row,plan,plan['source_sha256'],now=time.time(),require=pub.require);client.call('heartbeat',**auth,lease_seconds=180)
  return plan,ready,D,env,request,manager
 except BaseException:manager.__exit__(*sys.exc_info());raise

def remote_child(pub,plan_binding,child_adapter,status_validator):
 plan,ready,D,env,request,manager=context(pub,plan_binding,live=True,status_validator=status_validator)
 try:
  P=env[2];selected=child_adapter.select(P,reader_plan=plan_binding,child_wrapper=pub.binding(pub.__file__),reader_evidence=evidence(plan))
  publication=selected(request,Path(plan['journal'])/'owning-publication')
  pub.require(pub.checked(publication)['status']=='PROMOTED','owning publication needs reconciliation')
  dest=Path(plan['journal'])/'independent';dest.mkdir();D.verify(dict(config=ready['config']),env,dest)
  verification=pub.binding(dest/'independent-verification.json')
  package=pub.pack(Path(env[5]['output']),verification,publication,Path(plan['journal'])/'export')
  return pub.retain(Path(plan['journal'])/'result.json',dict(status='REMOTE_VERIFIED',workbook=plan['workbook'],plan=plan_binding,publication=publication,verification=verification,package=package,model_calls=0,automatic_retry=False))
 finally:manager.__exit__(*sys.exc_info())

def publication_child(pub,child_adapter,status_validator):
 a=argparse.ArgumentParser();a.add_argument('action',choices=['publication-child'])
 for k in('plan','plan-sha256','intent','intent-sha256','adapter'):a.add_argument('--'+k,required=True)
 x=a.parse_args();row=dict(path=x.plan,sha256=x.plan_sha256)
 plan,ready,D,env,request,manager=context(pub,row,live=True,status_validator=status_validator)
 try:
  intent=dict(path=x.intent,sha256=x.intent_sha256)
  child_adapter.validate(env[2],reader_plan=row,child_wrapper=pub.binding(pub.__file__),reader_evidence=evidence(plan),intent=intent,sidecar=x.adapter)
  pub.require(env[2].read(intent)['request']==env[2].encode(request),'owning child request detached')
  env[2].execute(intent)
 finally:manager.__exit__(*sys.exc_info())
