"""Append-only continuation of a sealed deterministic disclosure command failure.

Completed arbitration and earlier command receipts remain authoritative history.
A declared code successor resumes only the selected deterministic suffix. The
original worker owns the step intent/completion, command guards and later review.
"""
from pathlib import Path
from dataclasses import asdict,replace
import fcntl,shutil,json,ast,hashlib
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_instruction_worker as W
SCHEMA='harbor-disclosure-command-repair/v1'
PROFILE='harbor-disclosure-command-repair-profile/v1'
ALLOWED={'.cursor/skills/task-disclosure/scripts/disclose.py','.cursor/skills/task-disclosure/REGISTRY.md',
 'scripts/batch/harbor_disclosure_command_repair.py','scripts/batch/harbor_instruction_worker.py',
 'scripts/batch/harbor_instruction_repair.py','scripts/harbor_instruction_campaign.py',
 '.pipeline-code-manifest.json','.pipeline-code.json'}
FIELDS={'schema_version','source','workbook','old_snapshot','snapshot','initial','initial_tracker',
 'failure','command_plan','commands','role_requests','role_results','role_resolutions',
 'stage_tree','producer_tree','start_after','failed_after','proof','authorization','lease_history'}

def selected(worker):
 return worker.code_repair is not None and read(worker.code_repair).get('schema_version')==SCHEMA

def producer_run(worker):
 if worker.disclosure_revision is not None:
  revision=read(worker.disclosure_revision)
  if revision.get('source_revision') is not None:
   require(revision['worker_run']==str(worker.run),'foreign source revision producer')
   return Path(revision['producer_run'])
 return Path(worker.code_repair['path']).parent/'producer' if selected(worker) else worker.run/'producer'

def tree(path):
 return W.static.tree(Path(path))

def indexed_method_successor(old_root,new_root,changes):
 """Select only the reviewed complete vertical-family predicate and guard.

 The normalized predecessor must otherwise be identical. This is a typed
 successor, not permission to replace arbitrary indexed-method behaviour.
 """
 name='scripts/batch/harbor_method_items.py'
 require(changes<={name,'scripts/batch/harbor_disclosure_command_repair.py','.pipeline-code.json','.pipeline-code-manifest.json'}and name in changes,'unrelated indexed-method successor')
 core='scripts/batch/harbor_disclosure_command_repair.py'
 if core in changes:require(record(new_root/core)['sha256']==record(__file__)['sha256'],'indexed-method core is not the selected reader')
 before=ast.parse((old_root/name).read_text());after=ast.parse((new_root/name).read_text())
 added=[n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='top_labelled_vertical_family']
 require(len(added)==1 and hashlib.sha256(ast.dump(added[0],include_attributes=False).encode()).hexdigest()=='c5831a14fe27ee7902ecfaaa694cd175585a4a837c5da304c47f1a30456de6e5','indexed-method predicate differs')
 after.body.remove(added[0])
 expected=ast.dump(ast.parse('len(cells)>1 and len(set(labels))>1 and not top_labelled_vertical_family(D,gold,original,cells,labels)',mode='eval').body)
 guards=[n for n in ast.walk(after)if isinstance(n,ast.If)and ast.dump(n.test)==expected]
 require(len(guards)==1,'indexed-method split guard differs')
 guards[0].test=ast.parse('len(cells)>1 and len(set(labels))>1',mode='eval').body
 require(ast.dump(before)==ast.dump(after),'unrelated indexed-method code changed')

def snapshot_changes(old_binding,new_binding):
 old=read(old_binding);new=read(new_binding);before={x['relative']:x['sha256']for x in old['files']};after={x['relative']:x['sha256']for x in new['files']}
 require(len(before)==len(old['files'])and len(after)==len(new['files'])and set(before)<=set(after),'removed or duplicate code inventory')
 for root,files in ((Path(old['root']),before),(Path(new['root']),after)):
  require(all(not Path(name).is_absolute()and '..'not in Path(name).parts for name in files),'code inventory escapes root')
  require(tree(root)==files,'full frozen code inventory changed')
 changes={k for k,v in after.items()if before.get(k)!=v}
 if 'scripts/batch/harbor_method_items.py'in changes:
  indexed_method_successor(Path(old['root']),Path(new['root']),changes)
 else:require(changes<=ALLOWED and '.cursor/skills/task-disclosure/scripts/disclose.py'in changes,'undeclared disclosure code successor')
 for name in {'.pipeline-code.json','.pipeline-code-manifest.json'}&changes:
  manifest=read(record(Path(new['root'])/name))
  require(manifest=={k:v for k,v in after.items()if k!=name},'canonical successor inventory differs')
 return sorted(changes)

def profile(bound,worker):
 p=read(bound);require(set(p)==FIELDS and p['schema_version']==PROFILE,'closed disclosure continuation profile required')
 require(p['workbook']==read(p['initial_tracker'])['workbook_id']and p['source']==read(p['initial_tracker'])['bindings']['original_raw_source'],'original disclosure source changed')
 require(p['initial']==record(worker.run/'initial.json')and read(p['snapshot'])['root']==str(worker.root),'worker initial/code differs')
 require(type(p['failed_after'])is int and p['failed_after']in (2,3) and type(p['start_after'])is int and 0<=p['start_after']<=p['failed_after'],'only declared deterministic suffix allowed')
 for row in (p['source'],p['authorization'],p['failure'],p['proof']):W.checked_record(row)
 changes=snapshot_changes(p['old_snapshot'],p['snapshot'])
 require(read(p['failure'])==dict(type='AdmissionError',message_sha256=W.executor.sha(b'disclosure command failed; retained logs require owning diagnosis'),automatic_replay=False),'unrecognized disclosure failure')
 require(p['failure']==record(worker.run/'steps/disclosure-build/exception.json'),'failure belongs to another step')
 return p,changes

def command_status(value,*,failed):
 rc=value.get('returncode')
 require(type(rc)is int and rc in ((1,2)if failed else (0,)),'retained command outcome differs')

def inspect_history(bound,worker):
 p,changes=profile(bound,worker);plan=read(p['command_plan']);directory=worker.run/'steps/disclosure-build'
 require(p['command_plan']==record(directory/'command-plan.json')and plan['schema_version']=='harbor-disclosure-command-plan/v1','original fixed command plan changed')
 before=plan['before_role_resolution'];after=plan['after_zero_or_validated_roles']
 require(len(before)==3 and len(after)==5 and [x[5]for x in before]==['select','probe','roles']and [x[5]for x in after]==['detect','context','write','faithcheck','verify'],'unexpected disclosure command ordering')
 rows=p['commands'];names=[x['name']for x in rows]
 expected=['before-00','before-01','before-02']+(['roles-validate']if p['role_requests']else[])+['after-%02d'%i for i in range(p['failed_after']+1)]
 require(names==expected,'missing or reordered completed command history')
 last=None
 for row in rows:
  require(set(row)=={'name','intent','complete'}and row['intent']==record(directory/row['name']/'intent.json')and row['complete']==record(directory/row['name']/'complete.json'),'foreign retained command')
  intent=read(row['intent']);complete=read(row['complete']);argv=intent['argv'];W.checked_record(intent['entry']);W.checked_record(complete['log'])
  index=int(row['name'][-2:])if row['name']!='roles-validate'else None
  expected_argv=(before[index]if row['name'].startswith('before-')else after[index])if index is not None else before[0][:5]+['roles-validate','--task-dir',before[0][before[0].index('--task-dir')+1]]
  require(argv==expected_argv and intent['entry']==record(argv[2]),'retained command argv/code differs')
  command_status(complete,failed=row['name']=='after-%02d'%p['failed_after'])
  if last is not None:require(intent['before']==last,'retained command task history is discontinuous')
  last=complete['after']
 require(last==p['stage_tree'],'stopped stage differs from failed command')
 require(not any((directory/('after-%02d'%i)).exists()for i in range(p['failed_after']+1,5))and not(directory/'complete.json').exists(),'later deterministic work already exists')
 require(len(p['role_requests'])==len(p['role_results'])and len(p['role_requests'])<=2,'arbitration budget differs')
 cases=read(record(plan['cases_path']));require(cases['case_count']==len(cases['cases']),'invalid original role inventory')
 require(bool(cases['case_count'])==bool(p['role_requests']),'original role call evidence missing')
 if p['role_requests']:
  old_root=Path(read(p['old_snapshot'])['root']);renderer=W.producer.skill_module(old_root,'task-disclosure','disclose')
  bands=read(record(Path(plan['cases_path']).with_name('bands.json')));golden=record(bands['golden']);initial=W.codec.decode(read(p['initial'])['selection'])
  require(golden=={k:initial.content.bindings['source_file'][k]for k in ('path','sha256')},'arbitration source differs')
  derived=renderer.collect_ambiguous_role_cases(renderer.prepare_method_items(renderer.Book(Path(golden['path'])),bands['bands']))
  require(cases==dict(schema_version='1.0',task=initial.content.stage.name,cases=derived,case_count=len(derived),band_count=sum(c['band_count']for c in derived)),'arbitration cases differ from actual source')
  for index,(request_binding,result_binding)in enumerate(zip(p['role_requests'],p['role_results']),1):
   require(request_binding==record(directory/('role-request-%02d.json'%index)),'foreign role request')
   request=W.request_saved(Path(request_binding['path']));observed=W.executor.inspect(request.prepared,worker.campaign)
   require(result_binding==record(worker.run/'producer/role-arbitration'/('attempt-%02d'%index)/'result.json')and (request.prepared.ordinal,request.prepared.limit)==(index,2),'role request/result ordinal differs')
   context=read(request.context);require(context['kind']=='disclosure-role-arbitration'and context['original']==p['source']and context['workbook']==p['workbook']and context['request']==request.prepared.request,'arbitration request source/context differs')
   for item in context['budget_evidence']:W.checked_record(item)
   result=read(result_binding)
   require(observed.status=='completed'and observed.call is not None and result['call']==asdict(observed.call)and result['ordinal']==index,'arbitration is not the original completed Call')
   parsed=W.producer.validate_call(observed.call,read(observed.call.request),policy=request.prepared.policy,json_response=False)
   require(W.producer.text(result['raw'])==parsed['review']and read(result['cases'])==cases,'arbitration response/context changed')
   body=json.loads(read(observed.call.request)['messages'][1]['content']);require(body.get('cases')==cases and set(body)==({'cases'}if index==1 else {'cases','previous_response','validation_errors'}),'arbitration request body changed')
   if index==2:
    prior=read(p['role_results'][0]);require(body['previous_response']==W.producer.text(prior['raw'])and body['validation_errors']==prior['errors'],'role format repair detached from original failure')
   normalized,errors,_=W.roles._validation(renderer,Path(result['raw']['path']),cases)
   require(errors==result['errors']and result['passed']==(normalized is not None and not errors),'arbitration verdict changed')
   if index==len(p['role_requests']):require(result['passed']and normalized==read(p['role_resolutions']),'last arbitration must be the exact applied successful result')
   else:require(not result['passed'],'completed successful arbitration cannot be repeated')
 proof=read(p['proof'])
 require(proof['status']=='PASS'and proof['models']==proof['actual_task_mutations']==0 and proof['source']==p['source']and proof['snapshot']==p['snapshot']and proof['original_stage']==p['stage_tree']and proof['start_after']==p['start_after'],'candidate source/command proof differs')
 require(proof['faithcheck']['passed']is True and proof['faithcheck']['faults']==[],'candidate disclosure is not mechanically qualified')
 return p,changes

def authorize(worker,selection,lease,*,profile_binding,directory):
 from scripts.batch import harbor_instruction_repair as R
 require(all(getattr(worker,k,None)is None for k in ('code_repair','retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','warning_requalification','disclosure_repair','lease_renewal','disclosure_revision','dialogue_pre_review_repair')),'detached continuation kinds cannot combine')
 p,changes=inspect_history(profile_binding,worker)
 with (worker.run/'worker.lock').open('a+b')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  marker=worker.run/'disclosure-command-repair.json';directory=Path(directory)
  require(not marker.exists()and not directory.exists(),'disclosure repair already reserved; reconcile')
  require(not any((worker.run/'steps'/phase).exists()for phase in W.STEPS[1:]),'later instruction/model work exists')
  current=W.refreshed(selection);_,before,tracker=W.active(worker,current,lease)
  require(tree(current.content.stage)==p['stage_tree']and tree(worker.run/'producer')==p['producer_tree'],'stopped disclosure artifacts changed')
  require(before['bindings']['original_raw_source']==p['source'],'current source differs')
  require(read(p['initial'])['selection']==W.codec.encode(selection),'original initial selection required')
  from scripts.batch import harbor_lease_takeover as T
  T.continuation_chain(p['initial_tracker'],read(p['initial'])['lease'],before,lease,takeover=p['lease_history'],tracker_path=tracker['path'])
  prefix=R.prefix(before,current,worker.context,stop='disclosure')
  W.keep(marker,dict(schema_version=SCHEMA,profile=profile_binding,directory=str(directory),automatic_retry=False))
  shutil.copytree(current.content.stage,directory/'retained-stage')
  require(tree(directory/'retained-stage')==p['stage_tree'],'stopped stage retention failed')
  return W.keep(directory/'permit.json',dict(schema_version=SCHEMA,phase='disclosure-build',worker_run=str(worker.run),profile=profile_binding,initial=p['initial'],original=p['source'],predecessor=None,current_code=W.code(worker),changes=changes,tracker=W.snapshot(directory/'tracker.before.json',tracker),prefix=prefix,selection=W.codec.encode(current),lease=asdict(lease),model_calls_added=0,semantic_budget_reset=False))

def validate(worker,before):
 permit=read(worker.code_repair);require(permit['schema_version']==SCHEMA and permit['phase']=='disclosure-build'and permit['worker_run']==str(worker.run),'foreign disclosure command permit')
 p,changes=inspect_history(permit['profile'],worker)
 require(permit['initial']==p['initial']and permit['original']==p['source']==before['bindings']['original_raw_source']and permit['changes']==changes and permit['current_code']==W.code(worker),'disclosure permit source/code/history differs')
 require(permit['model_calls_added']==0 and permit['semantic_budget_reset']is False,'disclosure repair changes model budget')
 marker=read(record(worker.run/'disclosure-command-repair.json'));require(marker==dict(schema_version=SCHEMA,profile=permit['profile'],directory=str(Path(worker.code_repair['path']).parent),automatic_retry=False),'disclosure repair reservation differs')
 require(tree(Path(worker.code_repair['path']).parent/'retained-stage')==p['stage_tree'],'retained stopped stage changed')
 from scripts.batch import harbor_lease_takeover as T
 from scripts.batch.harbor_workbook_bootstrap import Lease
 T.continuation_chain(p['initial_tracker'],read(p['initial'])['lease'],read(permit['tracker']),Lease(**permit['lease']),takeover=p['lease_history'],tracker_path=str(worker.context.tracker_root/(p['workbook']+'.json')))
 return permit

def remaining_operation(worker,selection,lease,directory,*,commands,lock_fd):
 _,before,tracker=W.active(worker,selection,lease);permit=validate(worker,before);p=read(permit['profile'])
 if not commands:return selection,lease,'COMMANDS_REQUIRED'
 destination=producer_run(worker);require(not destination.exists(),'remaining disclosure suffix already started; reconcile')
 require(tree(worker.run/'producer')==p['producer_tree'],'original producer history changed')
 shutil.copytree(worker.run/'producer',destination)
 require(tree(destination)==p['producer_tree'],'producer history retention failed')
 task=W.task(worker,selection,tracker);require(task.run==destination,'selected producer continuation missing')
 plan=W.producer.disclosure_command_plan(task);suffix=plan['after_zero_or_validated_roles'][p['start_after']:]
 W.keep(Path(directory)/'remaining-command-plan.json',dict(original=p['command_plan'],profile=permit['profile'],remaining=suffix,role_arbitration_replayed=False))
 for i,argv in enumerate(suffix,start=p['start_after']):W._guarded(worker,task,directory,'after-%02d'%i,argv,lock_fd)
 return W.refreshed(selection),lease,None
