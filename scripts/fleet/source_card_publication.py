"""Publication-only context for an actual retained source-card completion."""
from pathlib import Path
import argparse,json,os,sys,time,types,inspect,hashlib
KEY='source_card_history'

def history(plan,*,checked,binding,require):
 a=plan[KEY]
 fields={'tail_plan','tail_helper','root_launch','job','config','queued_transaction'}
 require(set(a) in (fields,fields|{'recovery_plan'}),'closed source-card publication history')
 tail=checked(a['tail_plan']);prior=checked(tail['prior_plan']);ready=checked(plan['ready']);job=checked(a['job'])
 require(tail['schema_version'] in {'fleet-source-card-completed-tail/v1','fleet-source-card-completed-tail/v2'} and tail['helper']==a['tail_helper'] and tail['workbook']==plan['workbook'] and tail['source']['sha256']==plan['source_sha256'] and tail['version']==Path(plan['code_root']).name,'source-card publication identity differs')
 selected=checked(a['recovery_plan'])if 'recovery_plan'in a else tail;selected_plan=a.get('recovery_plan',a['tail_plan'])
 if 'recovery_plan'in a:
  require(selected['schema_version']=='fleet-prepared-enqueue-recovery/v1' and selected['tail_plan']==a['tail_plan'] and selected['tail_helper']==a['tail_helper'] and selected['transaction']==a['queued_transaction'] and all(selected[k]==tail[k]for k in ('workbook','owner','source','version','runtime')),'prepared recovery detached from actual failed tail')
 require(ready['schema_version']==selected['schema_version'] and ready['status']=='PUBLICATION_READY' and ready['plan']==selected_plan and ready['workbook']==plan['workbook'] and ready['runtime']==selected['runtime_profile'] and ready['models_dispatched']==0 and ready['publication_performed']is False,'actual source-card READY required')
 require(plan['ready']['path']==str(Path(selected['output'])/'result.json'),'actual enqueue namespace differs')
 if 'recovery_plan'not in a:require(ready['outcome']['path']==str(Path(tail['operations'])/'000/outcome.json'),'actual enqueue operation differs')
 require(job['inputs'][0]==selected_plan and job['driver']==selected['helper'] and job['result']==plan['ready']['path'] and job['source']['sha256']==plan['source_sha256'] and job['version']==tail['version'],'source-card READY detached from worker')
 launch=checked(a['root_launch']);intent=checked(launch['intent']);root=checked(intent['plan'])
 require(root['job']==a['job'] and launch['remote']['job']==a['job'] and launch['remote']['root_intent']==launch['intent'] and launch['automatic_retry']is False,'source-card root launch changed')
 for row in job['inputs']:require(binding(row['path'])==row,'source-card launch input changed')
 require(checked(tail['job'])['config']==a['config'],'source-card config changed')
 return a,tail,prior,ready

def history_binding(plan,**kwargs):
 history(plan,**kwargs);return plan[KEY]['tail_plan']

def evidence(plan):
 return [*plan[KEY].values(),plan['ready'],*list(plan.get('queued_lease',{}).values())]

def projected_request(pub,plan,P,request):
 selected=plan.get('queued_lease')
 if selected is None:return request
 pub.require(set(selected)=={'reader','completion'},'closed queued lease reader required')
 reader=pub.load('source_card_queued_lease',selected['reader'])
 return reader.projected(P,request,selected['completion'])

def queued_ancestry(pub, plan, projection, request, P, ready_module, *, proof):
 """Recompute the original completed enqueue transaction without a CAS."""
 require=pub.require;transaction=plan[KEY]['queued_transaction'];d=pub.checked(transaction)
 require(set(d)=={'schema_version','workbook','action','prepared_at','before','after','notes','prior_notes','selection','published','lease_kind','lease','root_authorized'} and d['schema_version']==ready_module.SCHEMA and d['workbook']==plan['workbook'] and d['action']=='enqueue' and d['published']==ready_module.encode(None) and d['lease_kind']=='task_agent' and d['root_authorized']is False,'exact original enqueue transaction required')
 tx=Path(transaction['path']).parent;tracker=Path(request.tracker_root)/(plan['workbook']+'.json')
 require(tx.parent==tracker.parent/(plan['workbook']+'-notes-transactions') and Path(transaction['path']).name=='transaction.json','foreign queued transaction namespace')
 for key,name in (('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')):
  if d[key]:require(Path(d[key]['path'])==tx/name and pub.binding(d[key]['path'])==d[key],'queued transaction evidence changed')
 before=pub.checked(d['before']);after=pub.checked(d['after'])
 require(before==pub.checked(projection['after']) and d['before']['sha256']==projection['after']['sha256'],'enqueue is detached from actual dialogue commit')
 require(d['selection']==ready_module.encode(request.selection) and d['lease']==before['leases']['task_agent'] and d['lease']['heartbeat_at']<=d['prepared_at']<d['lease']['expires_at'],'enqueue source selection or original lease differs')
 require(request.lease.revision==after['revision'] and request.lease.sha256==d['after']['sha256'] and request.lease.owner==d['lease']['owner'] and request.lease.token==d['lease']['token'],'publication lease is detached from queued commit')
 require(pub.checked(pub.binding(tx/'complete.json'))==dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256']),'enqueue completion marker differs')
 if proof:
  verified=ready_module._proof(before,request.selection)
  prior_bytes=Path(d['prior_notes']['path']).read_bytes()if d['prior_notes']else None
  rebuilt,rendered=ready_module._after(before,'enqueue',request.selection,verified,d['prepared_at'],tracker.with_name(plan['workbook']+'-agent-notes.md'),None,prior_bytes)
  require(rebuilt==after and rendered==Path(d['notes']['path']).read_bytes(),'original enqueue CAS/evidence cannot be rederived')
 return after

def publication_tracker(pub, plan, P, request, queued, current):
 """Validate the original queue, acquisition, promotion and release chain."""
 if current==queued:return
 journal=Path(plan['journal'])/'owning-publication';intent=pub.checked(pub.binding(journal/'intent.json'))
 pub.require(intent['request']==P.encode(request) and pub.checked(intent['queued_tracker'])==queued and intent['code']==P._codes(),'publication acquisition lacks its exact owning intent')
 if current['execution_state']!='PROMOTED':
  P._check_lease_event(queued,current,'acquire','publication',P.OWNER,None);return
 acquired_row=pub.checked(pub.binding(journal/'acquire-publication.json'))
 pub.require(pub.checked(acquired_row['before'])==queued,'publication acquisition predecessor changed')
 acquired=pub.checked(acquired_row['after']);P._check_lease_event(queued,acquired,'acquire','publication',P.OWNER,None)
 completed=pub.checked(pub.binding(journal/'promoted.json'));transaction=completed['transaction'];d=pub.checked(transaction)
 tracker=Path(request.tracker_root)/(plan['workbook']+'.json');tx=Path(transaction['path']).parent
 pub.require(tx.parent==tracker.parent/(plan['workbook']+'-notes-transactions') and Path(transaction['path']).name=='transaction.json' and d['schema_version']==P.ready.SCHEMA and d['workbook']==plan['workbook'] and d['action']=='promoted' and d['lease_kind']=='publication' and d['root_authorized']is False,'foreign promotion transaction')
 for key,name in (('before','before.json'),('after','after.json'),('notes','notes.md'),('prior_notes','prior-notes.md')):
  pub.require(d[key] and Path(d[key]['path'])==tx/name and pub.binding(d[key]['path'])==d[key],'promotion evidence changed')
 promoted=pub.checked(d['after'])
 pub.require(pub.checked(d['before'])==acquired and pub.checked(completed['tracker'])==promoted and d['selection']==P.codec.encode(request.selection) and d['published']==pub.checked(pub.binding(journal/'release.json')) and d['lease']==acquired['leases']['publication'] and d['lease']['heartbeat_at']<=d['prepared_at']<d['lease']['expires_at'],'promotion detached from selected acquisition')
 pub.require(pub.checked(pub.binding(tx/'complete.json'))==dict(tracker_sha256=d['after']['sha256'],notes_sha256=d['notes']['sha256']),'promotion completion changed')
 proof=pub.checked(pub.binding(journal/'readiness.json'))
 rebuilt,rendered=P.ready._after(acquired,'promoted',request.selection,proof,d['prepared_at'],tracker.with_name(plan['workbook']+'-agent-notes.md'),P.codec.decode(d['published']),Path(d['prior_notes']['path']).read_bytes())
 pub.require(rebuilt==promoted and rendered==Path(d['notes']['path']).read_bytes(),'original promotion cannot be rederived')
 P.qualification.after_releases(promoted,current)

def install_publication_history(pub,plan,tail,reader,W,P,worker,request):
 """Pin historical author custody while all owning publication sees current state."""
 projection_module=pub.load('source_card_publication_projection',pub.checked(tail['projection'])['implementation']);p=projection_module.validate(tail['projection'])
 original=types.MethodType(type(reader).historical_lease,reader)
 pub.require(reader.authority==p['authority'] and pub.binding(inspect.getfile(type(reader)))==p['reader'] and hashlib.sha256(inspect.getsource(original).encode()).hexdigest()==p['method_sha256'] and str(worker.run)==p['worker_run'],'publication historical reader differs')
 from scripts.batch import harbor_ready_transitions as ready_module,harbor_tracker as schema,harbor_tracker_lifecycle as lifecycle
 pub.require(pub.binding(schema.__file__)==p['schema'] and pub.binding(lifecycle.__file__)==p['lifecycle'],'publication lifecycle dependencies differ')
 validator=pub.load('source_card_publication_completed_lane',p['validator']);queued=queued_ancestry(pub,plan,p,request,P,ready_module,proof=False)
 effective=projected_request(pub,plan,P,request);current_queue=queued
 if plan.get('queued_lease')is not None:
  renewed=pub.checked(plan['queued_lease']['completion'])
  pub.require(pub.checked(renewed['before'])==queued,'queued renewal detached from original completed enqueue')
  current_queue=pub.checked(renewed['after'])
 previous=reader.historical_lease
 def historical(self,observed,current):
  pub.require(observed==worker,'foreign worker in publication historical custody')
  projection_module.validate(tail['projection'])
  publication_tracker(pub,plan,P,effective,current_queue,current)
  return original(observed,pub.checked(p['before']))
 reader.historical_lease=types.MethodType(historical,reader)
 try:
  validator.validate(W=W,T=types.SimpleNamespace(schema=schema,life=lifecycle),worker=worker,before_binding=p['before'],after_binding=p['after'],completion=p['completion'],kind=p['kind'])
  queued_ancestry(pub,plan,p,request,P,ready_module,proof=True)
 except BaseException:
  reader.historical_lease=previous
  raise
 return reader

def recovered_phase(pub,authority,recovery,ready,tail,worker,W,C,final,previous):
 """Validate a truthful recovery guard separately from the failed owning step."""
 require=pub.require;out=Path(recovery['output']);receipt=pub.checked(ready['recovery']);origin=pub.checked(receipt['intent'])
 require(ready['instruction_result']['path']==str(out/'instruction-result.json') and ready['recovery']['path']==str(out/'recovery/complete.json') and receipt['intent']['path']==str(out/'recovery/intent.json'),'recovered enqueue namespace changed')
 require(receipt['schema_version']=='harbor-prepared-enqueue-recovery/v1' and receipt['status']=='PUBLICATION_READY' and receipt['transaction']==authority['queued_transaction']==recovery['transaction'] and receipt['checkpoint']==ready['checkpoint']==final['checkpoint'] and receipt['models_dispatched']==0 and receipt['publication_performed']is False and receipt['automatic_retry']is False,'prepared recovery completion differs')
 transaction=pub.checked(receipt['transaction']);failed=pub.checked(recovery['failed_outcome']);guard=pub.checked(failed['resource']);failed_guard_intent=pub.checked(guard['intent']);prior=pub.checked(previous)
 require(recovery['failed_outcome']['path']==str(Path(tail['operations'])/'000/outcome.json') and failed['status']=='RECONCILIATION_REQUIRED' and guard['returncode']==1 and guard['failure']is None and failed_guard_intent['action']=='step' and failed_guard_intent['models']is False and failed_guard_intent['arguments']==dict(descriptor=prior['descriptor'],checkpoint=previous),'original failed enqueue guard changed')
 old=pub.checked(origin['failed_intent']);oldproof=pub.checked(origin['failed_readiness']);exception=pub.checked(origin['failed_exception']);done=pub.checked(ready['checkpoint'])
 require(origin['schema_version']==receipt['schema_version'] and origin['worker_run']==str(worker.run) and origin['transaction']==receipt['transaction'] and origin['selection']==old['selection']==done['selection']==final['selection'] and origin['lease']==old['lease'] and origin['code']==old['code']==W.code(worker) and origin['predecessor']==old['previous']==prior['checkpoint'] and origin['models_dispatched']==0 and origin['automatic_retry']is False,'recovery changed original worker or selected commit')
 directory=Path(origin['failed_intent']['path']).parent
 require(origin['failed_intent']==pub.binding(directory/'intent.json') and origin['failed_exception']==pub.binding(directory/'exception.json') and origin['failed_readiness']==pub.binding(directory/'readiness.json') and ready['checkpoint']==pub.binding(directory/'complete.json') and done==dict(step='enqueue',status='completed',selection=old['selection'],lease=done['lease'],intent=origin['failed_intent']) and oldproof['publication_ready']is True and oldproof['blockers']==[] and oldproof['mutation_performed']is False and exception['automatic_replay']is False,'failed enqueue evidence was rewritten')
 before=pub.checked(transaction['before']);after=pub.checked(transaction['after']);lease=done['lease']
 require(origin['lease']['sha256']==transaction['before']['sha256'] and origin['lease']['revision']==before['revision'] and lease['sha256']==transaction['after']['sha256'] and lease['revision']==after['revision'] and (lease['owner'],lease['token'])==(origin['lease']['owner'],origin['lease']['token']),'recovery lease differs from original transaction')
 require(receipt['transaction_completion']==pub.binding(Path(receipt['transaction']['path']).parent/'complete.json') and pub.checked(receipt['transaction_completion'])==dict(tracker_sha256=transaction['after']['sha256'],notes_sha256=transaction['notes']['sha256']),'recovery lacks original transaction completion')
 resource=pub.checked(pub.binding(out/'resource.json'));launch=pub.checked(pub.binding(out/'intent.json'));expected=[sys.executable,'-I','-B',recovery['helper']['path'],'child','--plan',authority['recovery_plan']['path'],'--sha256',authority['recovery_plan']['sha256']]
 require(resource['returncode']==0 and resource['failure']is None and resource['seconds']==3300 and resource['memory_bytes']==8589934592 and resource['models_dispatched']==0 and resource['command']==expected and resource['log']==pub.binding(out/'step.log') and launch==dict(schema_version=recovery['schema_version'],plan=authority['recovery_plan'],command=expected,guard=recovery['guard'],guard_runtime=recovery['guard_runtime'],automatic_retry=False),'recovery guard or physical child changed')
 for bound in (recovery['helper'],recovery['core'],recovery['runtime'],recovery['runtime_profile'],recovery['projection'],recovery['guard_runtime']):require(pub.binding(bound['path'])==bound,'recovery implementation changed')
 require(final['status']=='PUBLICATION_READY' and final['next_step']is None and final['publication_performed']is False and final['evidence']==[ready['recovery'],tail['completed_outcomes'][-1]],'recovery native result differs')
 require(final==C._result('PUBLICATION_READY',final['descriptor'],C.codec.decode(final['selection']),C.base.decode(final['lease']),checkpoint=ready['checkpoint'],evidence=[ready['recovery'],tail['completed_outcomes'][-1]]) and C.selected(final['descriptor'])[0]==worker,'original recovered instruction serialization differs')


def context(pub,row,*,live,status_validator):
 plan=pub.checked(row);require=pub.require;a,tail,prior,ready=history(plan,checked=pub.checked,binding=pub.binding,require=require)
 recovery=pub.checked(a['recovery_plan'])if 'recovery_plan'in a else None
 selected_tail=dict(tail,projection=recovery['projection'],runtime_profile=recovery['runtime_profile'])if recovery else tail
 RT=pub.load('source_card_publication_runtime',tail['runtime']);rp=RT.profile(selected_tail['runtime_profile']);scope=RT.installed(selected_tail['runtime_profile']);scope.__enter__()
 try:
  # Loading retained code is read-only; the current publication claim is
  # separately validated below. No authoring eligibility function is re-entered.
  entry=RT.load_module('source_card_publication_entry',rp['entrypoint']);loaded=entry.load(*rp['read_call']['args'],probe=True)
  _,_,reader,selected,original,oldenv,runner=loaded
  C=selected['campaign'];W=selected['worker'];env=(oldenv[0],C,oldenv[2],W,selected['renewal'],*oldenv[5:])
  base,C,P,W,R,config,root,controls=env
  require(root==Path(plan['code_root']) and config==pub.checked(a['config']) and original.VERIFIER==plan['verifier'],'source-card publication context differs')
  D=entry.dispatcher(original,selected,runner,plan_binding=rp['read_call']['args'][0],entry=rp['entrypoint'],reader=reader)
  final=pub.checked(ready['instruction_result'])if recovery else pub.checked(pub.checked(ready['outcome'])['result']);worker,expected,recipe=C.selected(final['descriptor'])
  require(expected==controls and reader.is_card(worker) and str(worker.run)==pub.checked(tail['projection'])['worker_run'],'actual source-card worker differs')
  def selected_source(observed,value):
   require(observed==env,'foreign source-card publication environment')
   w,c,r=C.selected(value['descriptor']);require(w==worker and c==controls and reader.is_card(w) and W.code(w)==reader.owned()['code'],'foreign source-card publication selection')
   require(w.campaign.model_slots==1 and w.campaign.slot_root==D.MODEL,'historical model pool differs')
   return w,r
  D.selected=selected_source;D.declared_namespace['selected']=selected_source
  request=D.publication_request(env,final)
  P._checkpoint(request)
  install_publication_history(pub,plan,selected_tail,reader,W,P,worker,request)
  M=pub.load('source_card_publication_warning',prior['implementation'])
  previous,value=M.authorization_outcome(tail['prior_plan'],tail['authorizing_outcome'],C)
  require(value['next_step']=='final-runtime','completed warning predecessor differs')
  for index,outcome in enumerate([*tail['completed_outcomes']]+([]if recovery else[ready['outcome']])):
   result,current=D.successful(outcome,env);resource=pub.checked(pub.checked(outcome)['resource']);intent=resource['intent'];actual=pub.checked(intent)
   require(actual['action']=='step' and actual['models']is False and actual['arguments']==dict(descriptor=value['descriptor'],checkpoint=previous),'publication phase skipped or repeated')
   profile=tail['prior_runtime']if index<2 else tail['runtime_profile'];bound=RT.profile(profile)
   side=pub.checked(pub.binding(Path(outcome['path']).parent/'retained-runtime-bootstrap.json'))
   require(side['schema_version']==RT.SIDE and side['profile']==profile and side['intent']==intent and side['automatic_retry']is False and RT.child_intent(side['logical_command'],bound)==intent,'cold source-card reader sidecar differs')
   expected_command=[sys.executable,'-I','-B',tail['runtime']['path'],'child','--profile',profile['path'],'--sha256',profile['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
   require(side['physical_command']==expected_command and side['timeout_seconds']==actual['timeout_seconds'] and side['memory_limit_bytes']==actual['memory_limit_bytes'],'original cold guard changed')
   require(current['status']==('PUBLICATION_READY'if index==2 else'STEP_COMPLETE') and current['next_step']==('dialogue-commit','enqueue',None)[index],'source-card phase order changed')
   previous,value=result,current
  if recovery:
   recovered_phase(pub,a,recovery,ready,tail,worker,W,C,final,previous)
   value=final
  else:require(previous==ready['checkpoint'],'actual enqueue result differs')
  request=projected_request(pub,plan,P,D.publication_request(env,value))
  def forbidden(*args,**kwargs):raise RuntimeError('publication cannot author or execute models')
  import xl_transport
  from scripts.batch import harbor_model_executor as E
  for mod,names in ((xl_transport,('read_response','_read_response','model_slot')),(E,('execute','_open')),(base,('run',)),(C,('run',)),(W,('step','run'))):
   for name in names:
    if hasattr(mod,name):setattr(mod,name,forbidden)
  if live:
   require(os.environ.get('HARBOR_FLEET_PUBLICATION_ONLY')=='1' and not os.environ.get('HARBOR_FLEET_MODEL_JOURNAL'),'live publication-only custody required')
   os.environ.pop('HARBOR_FLEET_CONTINUATION_READ_ONLY',None)
   auth=dict(workbook=os.environ['HARBOR_FLEET_WORKBOOK'],owner=os.environ['HARBOR_FLEET_OWNER'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']))
   require(auth['workbook']==plan['workbook'],'publication workbook differs')
   from scripts.fleet.coordinator import Client
   client=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status_validator(client.call('status'),auth,row,plan,plan['source_sha256'],now=time.time(),require=require);client.call('heartbeat',**auth,lease_seconds=180)
  return plan,ready,D,env,request,scope
 except BaseException:scope.__exit__(*sys.exc_info());raise

def remote_child(pub,row,child_adapter,status_validator):
 plan,ready,D,env,request,scope=context(pub,row,live=True,status_validator=status_validator)
 try:
  P=env[2];selected=child_adapter.select(P,reader_plan=row,child_wrapper=pub.binding(pub.__file__),reader_evidence=evidence(plan))
  publication=selected(request,Path(plan['journal'])/'owning-publication');pub.require(pub.checked(publication)['status']=='PROMOTED','owning publication needs reconciliation')
  dest=Path(plan['journal'])/'independent';dest.mkdir();D.verify(dict(config=plan[KEY]['config']),env,dest)
  verification=pub.binding(dest/'independent-verification.json');package=pub.pack(Path(env[5]['output']),verification,publication,Path(plan['journal'])/'export')
  return pub.retain(Path(plan['journal'])/'result.json',dict(status='REMOTE_VERIFIED',workbook=plan['workbook'],plan=row,publication=publication,verification=verification,package=package,model_calls=0,automatic_retry=False))
 finally:scope.__exit__(None,None,None)

def publication_child(pub,child_adapter,status_validator):
 a=argparse.ArgumentParser();a.add_argument('action',choices=['publication-child'])
 for key in ('plan','plan-sha256','intent','intent-sha256','adapter'):a.add_argument('--'+key,required=True)
 x=a.parse_args();row=dict(path=x.plan,sha256=x.plan_sha256);plan,ready,D,env,request,scope=context(pub,row,live=True,status_validator=status_validator)
 try:
  intent=dict(path=x.intent,sha256=x.intent_sha256);child_adapter.validate(env[2],reader_plan=row,child_wrapper=pub.binding(pub.__file__),reader_evidence=evidence(plan),intent=intent,sidecar=x.adapter)
  pub.require(env[2].read(intent)['request']==env[2].encode(request),'owning publication request differs');env[2].execute(intent)
 finally:scope.__exit__(None,None,None)
