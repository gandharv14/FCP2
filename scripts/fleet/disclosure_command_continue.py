"""Resume a proven deterministic disclosure suffix under retained source controls.

The original instruction and dispatcher APIs own every step. A declared runtime
selects only the reviewed worker/repair dependencies and checkpoint reader.
"""
from pathlib import Path
from dataclasses import replace,asdict
from contextlib import ExitStack,contextmanager
import argparse,ast,fcntl,hashlib,importlib.util,json,os,sys,time,types

SCHEMA='fleet-disclosure-command-continuation/v1'
FIELDS={'schema_version','helper','utilities','stdlib','workbook','source','owner','fence',
 'capacity_policy','authorization','context','original_job','descriptor','initial_result',
 'failure_outcome','tracker','lease','profile','core','runtime','runtime_profile','output','renewal_module'}

def require(ok,message):
 if not ok:raise ValueError(message)

def source_review_checkpoint(value,worker,renewal):
 """A renewed first-review boundary must retain its original completed build."""
 if value['next_step']!='disclosure-review':return False
 if value['status']=='STEP_COMPLETE':return True
 if value['status']!='RETRY_READY'or worker.lease_renewal is None or value['checkpoint']!=worker.lease_renewal:return False
 checkpoint=renewal.checkpoint_predecessor(worker,value['checkpoint'])
 require(checkpoint==binding(worker.run/'steps/disclosure-build/complete.json'),
  'source review renewal is not the original completed disclosure build')
 return True
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical evidence required')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'selected evidence changed')
 return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'selected code changed')
 s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def prepermit_stop(p,*,continued=False):
 """Authenticate a sealed admission refusal before any repair reservation."""
 a=read(p['prepermit_failure'])
 require(set(a)=={'plan','job','process','outcome','claim','guard'},'closed pre-permit stopped custody required')
 old=read(a['plan']);home=Path(old['output']);job=read(a['job']);process=read(a['process']);outer=read(a['outcome']);claim=read(a['claim'])
 changed={'helper','owner','fence','output','runtime_profile','prepermit_failure','prepermit_renewal_first'}
 require('prepermit_renewal_first'not in p or p['prepermit_renewal_first']is True and p.get('renewal_module')is not None,
  'explicit first renewal requires original live lease authority')
 if p.get('renewal_module')is not None:
  require(old['renewal_module']is None and 'completed_takeover'not in p,'prepermit renewal requires the failed-step retained context')
  previous_context=read(old['context']['plan']);selected_context=read(p['context']['plan'])
  require(previous_context['schema_version']=='fleet-failed-package-first-step-context/v1'
   and p['context']==dict(old['context'],helper=p['context']['helper'],plan=p['context']['plan'])
   and selected_context==dict(previous_context,helper=p['context']['helper'],bridge=selected_context['bridge'],
      lease_renewal=dict(module=p['renewal_module'],directory=str(Path(p['output'])/'renewal'))),
   'prepermit renewal changed failed source context')
  changed.update({'context','renewal_module'})
 require('prepermit_failure'not in old and not({'completed_permit','completed_step','settled_permit','source_cache_resume','precommand_failure'}&set(old))
  and {k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
  and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'],
  'pre-permit successor changed original source, lease or controls')
 require(set(x.name for x in home.iterdir())=={'intent.json','permit-resource.json','permit.log'}
  and read(binding(home/'intent.json'))==dict(plan=a['plan'],helper=old['helper'],automatic_retry=False),
  'repair permit or later task effects exist')
 marker=Path(read(old['profile'])['initial']['path']).parent/'disclosure-command-repair.json'
 if marker.exists():
  require(continued and read(binding(marker))==dict(schema_version='harbor-disclosure-command-repair/v1',profile=p['profile'],directory=str(Path(p['output'])/'permit'),automatic_retry=False),'another repair reservation already exists')
 guard=read(a['guard']);require(a['guard']==binding(home/'permit-resource.json')
  and guard['intent']==binding(home/'intent.json')and guard['returncode']==1 and guard['failure']is None
  and guard['seconds']==3300 and guard['memory_bytes']==8589934592 and guard['models_dispatched']==0
  and guard['log']==binding(home/'permit.log')
  and guard['command']==[job['command'][0],'-I','-B',old['helper']['path'],'permit-child','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']],
  'pre-permit guard differs from original child')
 text=Path(guard['log']['path']).read_text()
 require(text.rstrip().endswith('ValueError: unreconciled model effect or quarantined custody')
  and 'in permit_child' in text and 'U.custody(p,w.root)'in text and 'in custody'in text,
  'failure is not the original admission refusal before authorize')
 intent=read(outer['intent'])
 require(outer['schema_version']=='harbor-fleet-worker-outcome/v1'and outer['status']=='RECONCILIATION_REQUIRED'
  and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and outer['automatic_retry']is False and outer['publication_performed']is False
  and a['outcome']==binding(Path(job['run'])/'outcome.json')and a['claim']==binding(Path(job['run'])/'claim.json')
  and intent['job']==a['job']and intent['owner']==old['owner']and intent['automatic_retry']is False
  and claim['claim_id']==intent['claim_id']and claim['owner']==old['owner']and claim['fence']==old['fence']
  and claim['workbook']==job['workbook']==old['workbook']and claim['source_sha256']==old['source']['sha256']
  and claim['version']==job['version']and job['driver']==old['helper']and job['source']==old['source']
  and job['inputs'][0]==a['plan']and job['result']==str(home/'result.json')
  and process['job']==a['job']and process['automatic_retry']is False,
  'pre-permit original worker or claim differs')
 require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(),
  'pre-permit original worker may still run')
 return old

def direct_permit_reader(original,selected):
 """Authenticate the existing direct-permit branch and no other code change."""
 require(binding(original['path'])==original and binding(selected['path'])==selected,'checkpoint reader changed')
 before=ast.parse(Path(original['path']).read_text());after=ast.parse(Path(selected['path']).read_text())
 functions=[n for n in after.body if isinstance(n,ast.FunctionDef)and n.name=='_prior']
 require(len(functions)==1,'one original checkpoint reader required')
 expected=ast.parse("if completion.get('schema_version')=='harbor-disclosure-command-repair/v1':\n configured=worker_decode(read(descriptor)['worker'])\n require(configured.code_repair==bound,'disclosure command checkpoint detached from worker')\n permit=completion\n").body[0]
 matches=[n for n in ast.walk(functions[0])if isinstance(n,ast.If)and ast.dump(n.test)==ast.dump(expected.test)]
 require(len(matches)==1 and ast.dump(ast.Module(body=matches[0].body,type_ignores=[]))==ast.dump(ast.Module(body=expected.body,type_ignores=[]))
  and len(matches[0].orelse)==1 and isinstance(matches[0].orelse[0],ast.If),'exact direct command-permit branch required')
 target=matches[0]
 class Remove(ast.NodeTransformer):
  def visit_If(self,node):
   if node is target:return node.orelse[0]
   return self.generic_visit(node)
 Remove().visit(after)
 require(ast.dump(before)==ast.dump(after),'unrelated checkpoint reader change')
 return selected

def routed_source(text,kind):
 """The already-tested shared command routes, applied to the exact predecessor."""
 if kind=='worker':
  edits=[("def task(worker, selection, tracker):\n", "def task(worker, selection, tracker):\n    from scripts.batch.harbor_disclosure_command_repair import producer_run\n"),
   ("                         worker.run/'producer', selection.content.model_policy", "                         producer_run(worker), selection.content.model_policy"),
   ("def _operation(worker, selection, lease, phase, directory, *, models, containers, commands, credential, lock_fd):\n", "def _operation(worker, selection, lease, phase, directory, *, models, containers, commands, credential, lock_fd):\n    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if phase=='disclosure-build' and command_repair.selected(worker):\n        return command_repair.remaining_operation(worker,selection,lease,directory,commands=commands,lock_fd=lock_fd)\n"),
   ("    required=record(pending_now[-1]) if pending_now else preceding\n", "    required=record(pending_now[-1]) if pending_now else preceding\n    if worker.code_repair is not None and not pending_now and next_phase=='disclosure-build':\n        from scripts.batch.harbor_disclosure_command_repair import selected as command_repair_selected\n        if command_repair_selected(worker):required=worker.code_repair\n")]
 elif kind=='repair':
  edits=[("def validate(worker,before):\n", "def validate(worker,before):\n    from scripts.batch import harbor_disclosure_command_repair as command_repair\n    if command_repair.selected(worker):return command_repair.validate(worker,before)\n")]
 else:raise ValueError('foreign command route kind')
 for before,after in edits:
  require(text.count(before)==1,'exact original command route required');text=text.replace(before,after)
 return text

@contextmanager
def command_routing(p,env):
 """Only declared function bodies change; original roots and code bytes remain."""
 rows=p['command_routing'];require(set(rows)=={'worker','repair'},'closed command routing selection required')
 restored=[]
 try:
  for kind,original,names in [('worker',env[3],('task','_operation','_step')),('repair',env[4],('validate',))]:
   selected=rows[kind];require(binding(selected['path'])==selected,'selected command route changed')
   text=Path(selected['path']).read_text();expected=routed_source(Path(original.__file__).read_text(),kind)
   require(ast.dump(ast.parse(text))==ast.dump(ast.parse(expected)),'unrelated command routing changes')
   tree=ast.parse(text);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in names]
   require({n.name for n in nodes}==set(names),'command route symbols missing')
   values=dict(original.__dict__);exec(compile(ast.Module(body=nodes,type_ignores=[]),selected['path'],'exec'),values)
   for name in names:
    old=getattr(original,name);fn=values[name];new=types.FunctionType(fn.__code__,original.__dict__,name,fn.__defaults__,fn.__closure__);new.__kwdefaults__=fn.__kwdefaults__
    restored.append((original,name,old));setattr(original,name,new)
  yield
 finally:
  for original,name,old in reversed(restored):setattr(original,name,old)

def precommand_stop(p,env):
 a=read(p['precommand_failure']);require(set(a)=={'plan','outcome','job','process','step_outcome'},'closed stopped pre-command evidence required')
 old=read(a['plan']);home=Path(old['output']);job=read(a['job']);process=read(a['process']);outer=read(a['outcome'])
 require(old['completed_permit']==p['completed_permit']and 'precommand_failure'not in old,'one exact completed-permit routing failure required')
 changed={'helper','owner','fence','output','runtime_profile','precommand_failure','command_routing'}
 require({k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
  and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],'pre-command continuation changed scope')
 require(job['driver']==old['helper']and job['inputs'][0]==a['plan']and job['result']==str(home/'result.json')
  and process['job']==a['job']and a['outcome']==binding(Path(job['run'])/'outcome.json')
  and outer['intent']==binding(Path(job['run'])/'intent.json')and read(outer['intent'])['job']==a['job']
  and read(outer['intent'])['owner']==old['owner']and outer['status']=='RECONCILIATION_REQUIRED'
  and outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and outer['automatic_retry']is False and outer['publication_performed']is False,'pre-command stopped worker differs')
 require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(),'pre-command worker may still run')
 out=read(a['step_outcome']);resource=read(out['resource']);intent=read(resource['intent']);cp=read(p['completed_permit'])['checkpoint'];checkpoint=read(cp)
 require(a['step_outcome']==binding(home/'first-step/outcome.json')and out['status']=='RECONCILIATION_REQUIRED'
  and out.get('result')is None and out['automatic_retry']is False and resource['returncode']==1 and resource['failure']is None
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592
  and intent['action']=='step'and intent['arguments']==dict(descriptor=checkpoint['descriptor'],checkpoint=cp)
  and intent['code']==binding(env[1].__file__)and intent['controls']==env[0].encode(env[7])
  and resource['log']==binding(home/'first-step/step.log')
  and Path(resource['log']['path']).read_text().rstrip().endswith('AdmissionError: foreign instruction repair'),
  'exact failure before command intent required')
 require(not(home/'result.json').exists()and not(Path(env[5]['run_root'])/'dispatcher-v2'/home.name).exists(),'pre-command parent advanced beyond stopped guard')
 return old

def completed_permit(p,env,w,selection,lease,K,*,continued):
 """Reconcile a written permit whose final read failed before any command."""
 a=read(p['completed_permit']);old=read(a['plan']);home=Path(old['output'])
 require(set(a)=={'plan','checkpoint','guard','outcome','job','process','reader'},'closed written-permit custody required')
 changed={'helper','owner','fence','output','runtime_profile','completed_permit','precommand_failure','command_routing'}
 previous=precommand_stop(p,env)if 'precommand_failure'in p else old
 require({k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
  and 'completed_permit'not in old and p['owner']!=previous['owner']and p['fence']==previous['fence']+1
  and p['output']!=previous['output'],'written permit continuation changed source or controls')
 cp=read(a['checkpoint']);guard=read(a['guard']);outcome=read(a['outcome']);job=read(a['job']);process=read(a['process'])
 require(a['checkpoint']==binding(home/'permit/result.json')and cp['status']=='RETRY_READY'
  and cp['next_step']=='disclosure-build'and cp['checkpoint']==binding(home/'permit/permit.json'),'actual written command permit required')
 require(a['guard']==binding(home/'permit-resource.json')and guard['returncode']==1 and guard['failure']is None
  and guard['seconds']==3300 and guard['memory_bytes']==8589934592 and guard['models_dispatched']==0
  and read(guard['intent'])==dict(plan=a['plan'],helper=old['helper'],automatic_retry=False)
  and guard['log']==binding(home/'permit.log')and Path(guard['log']['path']).read_text().rstrip().endswith("KeyError: 'permit'"),
  'only diagnosed post-write checkpoint failure is supported')
 require(guard['command']==[job['command'][0],'-I','-B',old['helper']['path'],'permit-child','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']],
  'written permit command differs')
 require(job['driver']==old['helper']and job['inputs'][0]==a['plan']and job['result']==str(home/'result.json')
  and process['job']==a['job']and a['outcome']==binding(Path(job['run'])/'outcome.json')
  and outcome['intent']==binding(Path(job['run'])/'intent.json')and read(outcome['intent'])['job']==a['job']
  and read(outcome['intent'])['owner']==old['owner']and outcome['status']=='RECONCILIATION_REQUIRED'
  and outcome['returncode']==1 and outcome['failure_type']is None and outcome['result']is None
  and outcome['automatic_retry']is False and outcome['publication_performed']is False,'stopped original permit worker differs')
 require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(),'original permit worker may still run')
 for path in Path('/proc').glob('[0-9]*/cmdline'):
  if int(path.parent.name)==os.getpid():continue
  try:args=path.read_bytes().split(b'\0')
  except(FileNotFoundError,PermissionError):continue
  require(not any(x.encode()in args for x in(a['job']['path'],a['plan']['path'])),'original permit child may still run')
 require(not(home/'first-step').exists()and not(home/'result.json').exists()
  and not(Path(env[5]['run_root'])/'dispatcher-v2'/home.name).exists(),'original permit has later owning effects')
 C,I=env[:2];_,s,l=I._prior(a['checkpoint'],cp['descriptor']);actual=I.worker_decode(read(cp['descriptor'])['worker'])
 require(actual.root==w.root and actual.run==w.run and actual.code_repair==cp['checkpoint']
  and C.encode(l)==C.encode(lease),'written permit worker or lease differs')
 K.validate(actual,read(binding(p['tracker']['path'])))
 # authorize() refreshes stage/context hashes after the original failed commands.
 # Recompute exactly that refresh from its authenticated pre-command stage copy;
 # later suffix writes must not turn current task bytes into historical inputs.
 historical=replace(selection,content=replace(selection.content,stage=home/'permit/retained-stage'))
 refreshed=K.W.refreshed(historical)
 expected=replace(refreshed,content=replace(refreshed.content,stage=selection.content.stage))
 require(I.codec.encode(s)==I.codec.encode(expected),'written permit refreshed selection differs')
 if not continued:
  require(K.tree(s.content.stage)==read(p['profile'])['stage_tree']and not K.producer_run(actual).exists()
   and not K.W.step_directory(actual,'disclosure-build').exists(),'written permit suffix already started')
 return actual,s,l

def runtime_selection(p,env):
 q=read(p['runtime_profile']);profile=read(p['profile']);old=Path(read(profile['old_snapshot'])['root']);new=Path(read(profile['snapshot'])['root'])
 require(q['runtime']==p['runtime']and q['entrypoint']==p['context']['helper']
  and not q['patches']and not q['guard_modules']and not q['child_plans']and not q['journal_roots']
  and not q.get('factories')and not q.get('historical_imports')
  and q['read_call']==dict(function=p['context']['function'],args=[p['context']['plan']],kwargs=p['context']['kwargs']),'only explicit disclosure dependencies may change')
 names=['harbor_readiness','harbor_ready_transitions','harbor_instruction_worker','harbor_instruction_repair']
 if p.get('source_revision') is True:
  names+=['harbor_disclosure_revision']
  lease='scripts/batch/harbor_instruction_lease_renewal.py'
  if binding(old/lease)['sha256']!=binding(new/lease)['sha256']:names+=['harbor_instruction_lease_renewal']
 expected=[dict(module_name='scripts.batch.'+n,original=binding(old/'scripts/batch'/(n+'.py')),selected=binding(new/'scripts/batch'/(n+'.py')))for n in names]
 require(q['dependencies']==expected,'disclosure dependency selection differs')
 selected=binding(new/'scripts/harbor_instruction_campaign.py')
 if 'completed_permit'in p:selected=direct_permit_reader(selected,read(p['completed_permit'])['reader'])
 require(q['symbols']==[dict(module_name='scripts.harbor_instruction_campaign',original=binding(env[1].__file__),selected=selected,names=['_prior'])],'only instruction checkpoint reader may change')
 core='harbor_disclosure_source_revision.py' if p.get('source_revision') is True else 'harbor_disclosure_command_repair.py'
 require(p['core']==binding(new/'scripts/batch'/core),'repair core differs from successor')
 return new

def completed_lease(p,U):
 c=p['context']
 require(p['renewal_module']is None and p['lease']is None,'completed takeover cannot renew or select another lease')
 selected=read(p['completed_takeover']['plan']);history=read(p['profile'])
 require(selected['original_snapshot']==history['old_snapshot']
  and history['lease_history']==p['completed_takeover']['completion'],'disclosure takeover differs from retained source/history')
 # The shared reader checks the actual stopped guard, original checkpoint,
 # complete lease delta and every declared dependency. It never re-runs
 # expired-owner eligibility against the new live tracker.
 lease,retained=U.retained_lease(dict(completed_takeover=p['completed_takeover'],
  retained_context=dict(helper=c['helper'],plan=c['plan']),workbook=p['workbook'],
  retained_read_call=dict(function=c['function'],args=[c['plan']],kwargs=c['kwargs']),
  result=p['initial_result'],outcome=p['failure_outcome'],tracker=p['tracker'],
  owner=read(read(p['completed_permit'])['plan'])['owner']if 'completed_permit'in p else read(read(p['source_cache_resume'])['plan'])['owner']if 'source_cache_resume'in p else read(read(p['settled_permit'])['plan'])['owner'] if 'settled_permit'in p else read(read(p['completed_step'])['plan'])['owner']if 'completed_step'in p else p['owner'],source=p['source']))
 return lease,retained

def corrected_operation(K,row):
 old=Path(K.__file__).read_text();new=Path(row['path']).read_text()
 require(binding(row['path'])==row,'selected tracker correction changed')
 expected=old.replace("_,before,_=W.active(worker,selection,lease);permit=validate(worker,before)","_,before,tracker=W.active(worker,selection,lease);permit=validate(worker,before)")
 expected=expected.replace('task=W.task(worker,selection,before);require(task.run==destination', 'task=W.task(worker,selection,tracker);require(task.run==destination')
 require(expected!=old and ast.dump(ast.parse(expected))==ast.dump(ast.parse(new)),
  'only the exact producer tracker binding correction is supported')
 fixed=module('disclosure_corrected_operation',row).remaining_operation
 return types.FunctionType(fixed.__code__,K.__dict__,fixed.__name__,fixed.__defaults__,fixed.__closure__)

@contextmanager
def operation_scope(K,worker,output,operation):
 prior_run=K.producer_run;prior_step=K.W.step_directory;prior_operation=K.remaining_operation
 def selected(other):return other.code_repair==worker.code_repair and other.run==worker.run and other.root==worker.root
 def producer_run(other):return Path(output)/'producer' if selected(other)else prior_run(other)
 def step_directory(other,phase):return Path(output)/'steps'/phase if selected(other)and phase=='disclosure-build'else prior_step(other,phase)
 K.producer_run=producer_run;K.W.step_directory=step_directory;K.remaining_operation=operation
 try:yield
 finally:K.producer_run=prior_run;K.W.step_directory=prior_step;K.remaining_operation=prior_operation

def renewal_preparation_failure(p,env):
 a=read(p['renewal_preparation_failure']);require(set(a)=={'plan','outcome','marker'},'closed unused renewal failure required')
 old=read(a['plan']);changed={'helper','owner','fence','output'}
 require('renewal_preparation_failure'not in old and 'preparation_resume'in old
  and {k:v for k,v in p.items()if k not in changed|{'renewal_preparation_failure'}}=={k:v for k,v in old.items()if k not in changed}
  and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'],'renewal failure changed retained authority')
 home=Path(old['output']);require(set(x.name for x in home.iterdir())=={'intent.json'}and a['marker']==binding(home/'intent.json')
  and read(a['marker'])==dict(plan=a['plan'],helper=old['helper'],automatic_retry=False),'owning renewal already began')
 outer=read(a['outcome']);intent=read(outer['intent']);job=read(intent['job']);require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and intent['owner']==old['owner']and job['driver']==old['helper']and job['result']==str(home/'result.json'),'unused renewal failure custody differs')
 log=outer['log'];require(binding(log['path'])==log and Path(log['path']).read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: selected stage changed'),'unrecognized unused renewal failure')
 require(not(Path(env[5]['run_root'])/'dispatcher-v2'/home.name).exists(),'renewal operation already exists')
 return old

def preparation_resume(p,env,worker,*,continued):
 a=read(p['preparation_resume']);require(set(a)=={'plan','outcome','worker_outcome','step_intent','producer_tree','reader','proof','renewal','allowance','duration_ns'},'closed preparation resource continuation required')
 old=read(a['plan']);changed={'helper','owner','fence','output'}
 selected=renewal_preparation_failure(p,env)if 'renewal_preparation_failure'in p else p
 require('preparation_resume'not in old and 'completed_step'in old
  and {k:v for k,v in selected.items()if k not in changed|{'preparation_resume'}}=={k:v for k,v in old.items()if k not in changed}
  and selected['owner']!=old['owner']and selected['fence']==old['fence']+1 and selected['output']!=old['output'], 'resource continuation changed retained code/source/attempts')
 C,I=env[:2];out=read(a['outcome']);guard=read(out['resource']);intent=read(guard['intent']);done=read(read(old['completed_step'])['outcome']);cp=read(done['result'])
 ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(old['output']).name
 require(a['outcome']==binding(ops/'000/outcome.json')and out['status']=='RECONCILIATION_REQUIRED'
  and guard['returncode']is None and guard['failure']['type']=='ConversionTimeout'
  and guard['metrics']['limit_reason']=='process_group_rss'and guard['metrics']['peak_rss_bytes']>=8589934592
  and binding(guard['log']['path'])==guard['log']and Path(guard['log']['path']).stat().st_size==0
  and intent['action']=='step'and intent['code']==binding(I.__file__)
  and intent['arguments']==dict(descriptor=cp['descriptor'],checkpoint=done['result'])
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592
  and intent['controls']==C.encode(env[7])and not(ops/'000/result.json').exists(), 'exact pre-request original memory hold required')
 outer=read(a['worker_outcome']);job=read(read(outer['intent'])['job'])
 require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and job['driver']==old['helper']and job['result']==str(Path(old['output'])/'result.json'), 'resource hold outer custody differs')
 prior=worker.run/'steps/disclosure-review';require(a['step_intent']==binding(prior/'intent.json')
  and set(x.name for x in prior.iterdir())=={'intent.json'},'review request or other work already exists')
 wi=read(a['step_intent']);require(wi['step']=='disclosure-review'and wi['previous']==cp['checkpoint']
  and wi['selection']==cp['selection']and wi['lease']==read(cp['checkpoint'])['lease'],'unused review intent differs')
 from scripts.batch import harbor_disclosure_command_repair as K
 producer=K.producer_run(worker);actual=K.tree(producer)
 require(all(actual.get(k)==v for k,v in a['producer_tree'].items())and (continued or actual==a['producer_tree']), 'retained producer preparation changed')
 if not continued:require(not(producer/'requests/disclosure-review').exists(),'review preparation already has an attempt')
 proof=read(a['proof']);require(proof['returncode']==0,'native sparse proof failed');proof=json.loads(proof['stdout'])
 require(proof['status']=='PASS'and proof['models']==proof['actual_task_mutations']==0
  and proof['source']==p['source']and proof['plan']==a['plan']and proof['reader']==a['reader']
  and proof['peak_rss_bytes']<8589934592,'sparse reader not proven on actual retained evidence')
 require(a['duration_ns']==7200*10**9,'bounded two-hour renewal required')
 return a

@contextmanager
def preparation_runtime(p,env,worker,*,continued):
 a=preparation_resume(p,env,worker,continued=continued)
 from scripts.batch import harbor_semantic_readiness as S
 from scripts.batch import harbor_continuation_allowance as A
 from scripts.batch import harbor_tracker_lifecycle as life
 import scripts.batch as package
 proof=read(p['profile']);snapshot=proof['snapshot'];root=Path(read(snapshot)['root'])
 require(root==worker.root,'completed producer root differs')
 A.lease_limits(a['allowance'],read(binding(p['tracker']['path'])),at=time.time_ns())
 source=Path(a['reader']['path']);require(binding(source)==a['reader'],'selected sparse reader changed')
 tree=ast.parse(source.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='workbook_evidence')
 ns=dict(S.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns);selected=ns['workbook_evidence'];original=S.workbook_evidence;aliases=[]
 for mod in list(sys.modules.values()):
  if not isinstance(mod,types.ModuleType):continue
  for name,value in list(vars(mod).items()):
   if value is original:aliases.append((mod,name,value));setattr(mod,name,selected)
 canonical=sys.modules.get('scripts.batch.harbor_instruction_lease_renewal');old_attr=getattr(package,'harbor_instruction_lease_renewal',None)
 R=None;prior_step=env[3].step_directory
 try:
  R=module('scripts.batch.harbor_instruction_lease_renewal',a['renewal']);package.harbor_instruction_lease_renewal=R
  authorize=R.authorize
  def renew(w,s,l,checkpoint,old_snapshot,**kwargs):
   require(not kwargs and old_snapshot==env[5]['code']and w.root==worker.root and w.run==worker.run,'renewal detached from original dispatcher')
   return authorize(w,s,l,checkpoint,snapshot,duration_ns=a['duration_ns'],allowance=a['allowance'])
  R.authorize=renew
  def step(w,phase):
   return Path(p['output'])/'steps/disclosure-review'if w.run==worker.run and w.root==worker.root and w.code_repair==worker.code_repair and phase=='disclosure-review'else prior_step(w,phase)
  env[3].step_directory=step
  yield (*env[:4],R,*env[5:])
 finally:
  env[3].step_directory=prior_step
  if R is not None and 'authorize'in locals():R.authorize=authorize
  if canonical is None:sys.modules.pop('scripts.batch.harbor_instruction_lease_renewal',None)
  else:sys.modules['scripts.batch.harbor_instruction_lease_renewal']=canonical
  package.harbor_instruction_lease_renewal=old_attr
  for mod,name,value in reversed(aliases):setattr(mod,name,value)

@contextmanager
def skill_modules(old_snapshot,new_snapshot):
 """Use the original skill loader with only declared old/new cached origins."""
 snapshots=[read(old_snapshot),read(new_snapshot)];roots={Path(x['root'])for x in snapshots}
 inventories={Path(x['root']):{r['relative']:r['sha256']for r in x['files']}for x in snapshots}
 require(len(roots)==2 and all(len(x['files'])==len(inventories[Path(x['root'])])for x in snapshots),'distinct complete skill inventories required')
 functions={}
 for mod in list(sys.modules.values()):
  fn=getattr(mod,'skill_module',None)
  if isinstance(fn,types.FunctionType)and '_SKILL_FILES'in fn.__globals__ and Path(fn.__globals__.get('__file__','')).parent.name=='batch':functions[id(fn)]=fn
 require(functions,'original skill loader required')
 groups={}
 for fn in functions.values():
  for group,names in fn.__globals__['_SKILL_FILES'].items():
   require(group not in groups or groups[group]==names,'skill dependency groups differ');groups[group]=names
 originals={name:sys.modules.get(name)for names in groups.values()for name in names};changed=[]
 def factory(original):
  def selected(root,skill,name):
   root=Path(root).resolve();require(root in roots and skill in groups and name in groups[skill],'skill request outside declared snapshots')
   chosen=['task-disclosure',skill]if skill=='additional-assumptions-dialogue'else[skill]
   for group in chosen:
    for module_name in groups[group]:
     relative='.cursor/skills/'+group+'/scripts/'+module_name+'.py';expected=dict(path=str(root/relative),sha256=inventories[root][relative])
     require(binding(expected['path'])==expected,'selected skill snapshot changed')
     loaded=sys.modules.get(module_name)
     if loaded is not None:
      path=Path(getattr(loaded,'__file__','')).resolve();allowed=[r for r in roots if path==r/relative]
      require(len(allowed)==1 and binding(path)==dict(path=str(path),sha256=inventories[allowed[0]][relative]),'foreign cached skill origin')
      if path!=root/relative:del sys.modules[module_name]
   return original(root,skill,name)
  return selected
 wrappers={key:factory(fn)for key,fn in functions.items()}
 for mod in list(sys.modules.values()):
  if not isinstance(mod,types.ModuleType):continue
  for name,value in list(vars(mod).items()):
   if id(value)in wrappers:changed.append((mod,name,value));setattr(mod,name,wrappers[id(value)])
 try:yield
 finally:
  for mod,name,value in reversed(changed):setattr(mod,name,value)
  for name,value in originals.items():
   if value is None:sys.modules.pop(name,None)
   else:sys.modules[name]=value

@contextmanager
def dispatcher_selection(D,env,worker):
 # The complete declared successor inventory is already validated by context.
 # Nested continuations may already carry that exact projected root. All
 # modules, configuration and controls must still match the captured context.
 original=D.selected
 projected=(*env[:6],worker.root,env[7])
 def selected(observed,value):
  require(observed==env or observed==projected,'dispatcher environment differs from retained context')
  candidate,_,_=env[1].selected(value['descriptor'])
  require(candidate.root==worker.root and candidate.run==worker.run,
   'dispatcher descriptor is outside the declared successor')
  return original(projected,value)
 D.selected=selected
 try:yield
 finally:D.selected=original

def completed_step(p,env):
 if 'preparation_resume'in p:p=read(read(p['preparation_resume'])['plan'])
 a=read(p['completed_step']);require(set(a)=={'plan','outcome','worker_outcome'},'closed completed disclosure handoff required')
 old=read(a['plan']);changed={'helper','owner','fence','output','stop_after_disclosure_build'}
 require({k:v for k,v in p.items()if k not in changed|{'completed_step'}}=={k:v for k,v in old.items()if k not in changed}
  and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'],
  'completed disclosure continuation changed retained selection')
 require('completed_step'not in old,'single completed permit handoff required')
 home=Path(old['output']);C,I=env[:2];out=read(a['outcome']);resource=read(out['resource']);intent=read(resource['intent'])
 cp=read(out['result']);prior_row=read(old['settled_permit'])['checkpoint']if 'settled_permit'in old else binding(home/'permit/result.json');prior=read(prior_row)
 require(a['outcome']==binding(home/'first-step/outcome.json')and out['status']=='COMPLETED_OPERATION'
  and out['result']==binding(home/'first-step/result.json')and resource['returncode']==0 and resource['failure']is None
  and intent['action']=='step'and intent['code']==binding(I.__file__)
  and intent['arguments']==dict(descriptor=prior['descriptor'],checkpoint=prior_row)
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592 and intent['controls']==C.encode(env[7])
  and cp['status']=='STEP_COMPLETE'and cp['next_step']=='disclosure-review'and cp['descriptor']==prior['descriptor'],
  'original successful disclosure step required')
 I._prior(out['result'],cp['descriptor'])
 outer=read(a['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
 require(job['result']==str(home/'result.json')and job['driver']==old['helper']and wi['owner']==old['owner'], 'completed outer custody differs')
 ops=Path(env[5]['run_root'])/'dispatcher-v2'/home.name
 if old.get('stop_after_disclosure_build')is True:
  require(outer['returncode']==0 and outer['failure_type']is None and outer['result']==binding(home/'result.json')and not ops.exists(),'explicit stopped disclosure outer differs')
  value=read(outer['result']);require(value==dict(status='DISCLOSURE_BUILD_COMPLETE',workbook=old['workbook'],continuation=a['plan'],config=read(old['original_job'])['config'],outcome=a['outcome'],checkpoint=out['result'],permit_guard=binding(home/'permit-resource.json'),publication_performed=False,automatic_retry=False),'stopped disclosure result differs')
 else:
  require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None and not(home/'result.json').exists(),'exact stopped outer continuation required')
  text=Path(outer['log']['path']).read_text();require(binding(outer['log']['path'])==outer['log']and text.rstrip().endswith('ValueError: instruction selection differs'), 'unsupported post-step failure')
  require(ops.is_dir()and set(x.name for x in ops.iterdir())=={'job.json'},'dispatcher attempted another task step')
  saved=read(binding(ops/'job.json'));require(saved['outcome']==a['outcome']and saved['operations']==str(ops),'retained dispatcher boundary differs')
 return old,a['outcome']

@contextmanager
def settled_context(p,env,w,selection,lease,K,*,continued):
 a=read(p['settled_permit'])
 require(set(a)=={'plan','checkpoint','outcome','step_intent','exception','core'},'closed settled permit continuation required')
 old=read(a['plan']);changed={'helper','owner','fence','output'}
 selected_p=read(read(p['completed_step'])['plan'])if 'completed_step'in p else p
 require({k:v for k,v in selected_p.items()if k not in changed|{'settled_permit'}}=={k:v for k,v in old.items()if k not in changed}
  and selected_p['owner']!=old['owner'] and selected_p['fence']==old['fence']+1 and selected_p['output']!=old['output'],
  'settled continuation changed original source, code, lease or permit selection')
 home=Path(old['output']);C,I=env[:2];cp=read(a['checkpoint'])
 require(a['checkpoint']==binding(home/'permit/result.json') and cp['status']=='RETRY_READY'
  and cp['next_step']=='disclosure-build' and cp['checkpoint']==binding(home/'permit/permit.json'),
  'actual original repair permit handoff required')
 value,s,l=I._prior(a['checkpoint'],cp['descriptor']);worker=I.worker_decode(read(cp['descriptor'])['worker'])
 require(worker.root==w.root and worker.run==w.run and worker.code_repair==cp['checkpoint']
  and C.encode(l)==C.encode(lease),'settled permit worker or lease differs')
 out=read(a['outcome']);guard=read(out['resource']);intent=read(guard['intent'])
 require(a['outcome']==binding(home/'first-step/outcome.json') and out['status']=='RECONCILIATION_REQUIRED'
  and guard['returncode']==1 and guard['failure']is None and intent['code']==binding(I.__file__)
  and intent['action']=='step' and intent['arguments']==dict(descriptor=cp['descriptor'],checkpoint=a['checkpoint'])
  and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592
  and intent['controls']==C.encode(env[7]) and intent['models']is True
  and not(home/'first-step/result.json').exists(),'sealed pre-command instruction failure required')
 step=home/'permit/steps/disclosure-build';exception=read(a['exception']);step_intent=read(a['step_intent'])
 require(a['exception']==binding(step/'exception.json') and a['step_intent']==binding(step/'intent.json')
  and K.tree(step)=={'exception.json':a['exception']['sha256'],'intent.json':a['step_intent']['sha256']}
  and exception==dict(type='Conflict',message_sha256=hashlib.sha256(b'expected exact path/hash evidence record').hexdigest(),automatic_replay=False)
  and set(step_intent)=={'code','initial','lease','previous','selection','step'}
  and step_intent['previous']==worker.code_repair and step_intent['step']=='disclosure-build'
  and step_intent['selection']==I.codec.encode(s) and step_intent['lease']==read(worker.code_repair)['lease']
  and step_intent['code']==K.W.code(worker) and step_intent['initial']==read(p['profile'])['initial'],
  'only the exact failure before any suffix command is resumable')
 profile=read(p['profile']);before=read(binding(p['tracker']['path']))
 K.validate(worker,before)
 require(K.tree(home/'permit/producer')==profile['producer_tree'],'original copied producer evidence changed')
 if not continued and 'completed_step'not in p:require(K.tree(s.content.stage)==profile['stage_tree'],'pre-command source stage changed')
 with operation_scope(K,worker,selected_p['output']if 'completed_step'in p else p['output'],corrected_operation(K,a['core'])):
  yield worker,s,l

def runtime_inputs(p):
 """Required direct dependencies, shared by packaging and native cold checks."""
 keys=('helper','utilities','stdlib','capacity_policy','authorization','profile','core','runtime','runtime_profile')
 rows=[p[key]for key in keys]+[p['context']['helper']]
 if 'prepermit_failure'in p:rows.extend([p['prepermit_failure'],*read(p['prepermit_failure']).values()])
 if 'completed_permit'in p:rows.extend([p['completed_permit'],*read(p['completed_permit']).values()])
 if 'precommand_failure'in p:rows.extend([p['precommand_failure'],*read(p['precommand_failure']).values()])
 if 'command_routing'in p:rows.extend(p['command_routing'].values())
 if p['renewal_module']is not None:rows.append(p['renewal_module'])
 if 'source_cache_resume'in p:rows.append(read(p['source_cache_resume'])['helper'])
 if 'completed_takeover'in p:
  t=p['completed_takeover'];rows.extend(t[key]for key in('runtime','plan','completion','dependency_runtime','dependency_profile'))
 for row in rows:
  require(set(row)=={'path','sha256'}and binding(row['path'])==row,'direct disclosure runtime dependency missing or changed')
 return list({row['path']:row for row in rows}.values())

@contextmanager
def retained_loader(c):
 """Keep a declared original context alive through every continuation check."""
 require(c['manager']in(None,'package_runtime','contextmanager'),'unsupported retained source manager')
 M=module('disclosure_original_context',c['helper'])
 loaded=getattr(M,c['function'])(c['plan'],**c['kwargs'])
 if c['manager']=='contextmanager':
  require(c['function']in('context','cold_context'),'context-manager loader must name an original context')
  with loaded as value:
   require(isinstance(value,tuple),'original context must yield its typed tuple')
   yield M,value
 else:
  require(isinstance(loaded,tuple),'original loader must return its typed tuple')
  yield M,loaded

@contextmanager
def context(row,*,continued=False,permit=False):
 p=read(row);require(set(p)-{'completed_takeover','settled_permit','completed_step','preparation_resume','stop_after_disclosure_build','renewal_preparation_failure','source_revision','source_cache_resume','completed_permit','precommand_failure','command_routing','prepermit_failure','prepermit_renewal_first'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed disclosure continuation required')
 require('prepermit_renewal_first'not in p or 'prepermit_failure'in p and p['prepermit_renewal_first']is True,'first renewal requires sealed prepermit refusal')
 require(('precommand_failure'in p)==('command_routing'in p)and ('precommand_failure'not in p or 'completed_permit'in p),'command routing requires exact stopped written permit')
 require('completed_permit'not in p or not({'settled_permit','completed_step','preparation_resume','renewal_preparation_failure','source_revision','source_cache_resume'}&set(p)),'written permit cannot combine recovery kinds')
 source_revision=p.get('source_revision') is True
 require('source_cache_resume'not in p or source_revision and not({'settled_permit','completed_step','preparation_resume','stop_after_disclosure_build','renewal_preparation_failure'}&set(p)), 'cache recovery requires only the original source revision')
 require('source_revision'not in p or source_revision and not({'settled_permit','completed_step','preparation_resume','stop_after_disclosure_build','renewal_preparation_failure'}&set(p)), 'source correction selects only an ordinary sealed review')
 require('stop_after_disclosure_build'not in p or p['stop_after_disclosure_build']is True,'explicit disclosure stop flag required')
 require(not p.get('stop_after_disclosure_build')or not({'settled_permit','completed_step','preparation_resume'}&set(p)),'stop flag selects the original permit suffix only')
 if 'prepermit_failure'in p:prepermit_stop(p,continued=continued)
 runtime_inputs(p)
 module('disclosure_stdlib',p['stdlib']).preload_inspect()
 U=module('disclosure_continuation_utilities',p['utilities'])
 require(p['authorization']['sha256']==U.AUTHORIZATION_SHA and binding(p['authorization']['path'])==p['authorization'],'explicit continuation authorization required')
 c=p['context'];require(set(c)=={'helper','plan','function','kwargs','projection','manager'}and c['function']in {'context','cold_context','completed_context','load'},'closed historical loader required')
 require(set(c['projection'])=={'dispatcher','environment'}and all(type(i)is int and i>=0 for i in c['projection'].values())and len(set(c['projection'].values()))==2,'explicit context positions required')
 with retained_loader(c)as(M,loaded):
  D=loaded[c['projection']['dispatcher']];env=loaded[c['projection']['environment']]
  require(isinstance(env,tuple)and len(env)==8,'original dispatcher environment required')
  C,I,_,oldW,oldR,config,root,controls=env;raw_guard=C._run_in_process_group
  job=read(p['original_job']);require(read(job['config'])==config,'original configuration differs')
  require(job['driver']==binding(D.__file__)and config['workbook']==p['workbook']and job['publish']is False
   and all(job[k]is True for k in ('models','commands','containers'))and C.SECONDS==3300 and C.MEMORY==8589934592,'original source controls or capabilities changed')
  original_worker,observed,recipe=I.selected(p['descriptor']);value,selection,initial_lease=I._prior(p['initial_result'],p['descriptor'])
  require((source_review_checkpoint(value,original_worker,oldR) if source_revision else value['status']=='INSTRUCTION_READY'and value['checkpoint']is None) and observed==controls
   and original_worker.root==root and original_worker.run==Path(config['run_root'])/'instruction','actual initial instruction handoff required')
  outcome=read(p['failure_outcome']);guard=read(outcome['resource']);intent=read(guard['intent'])
  require(outcome['status']=='RECONCILIATION_REQUIRED'and guard['returncode']==1 and guard['failure']is None
   and intent['code']==binding(I.__file__)and intent['action']=='step'and intent['arguments']==dict(descriptor=p['descriptor'],checkpoint=p['initial_result'])
   and intent['controls']==C.encode(controls)and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592,'original failed disclosure guard required')
  require(selection.content.bindings['original_raw_source']==p['source']and binding(p['source']['path'])==p['source'],'original raw source changed')
  require(p['tracker']['path']==str(original_worker.context.tracker_root/(p['workbook']+'.json')),'tracker location changed')
  if not continued and not p.get('prepermit_renewal_first'):require(binding(p['tracker']['path'])==p['tracker'],'current custody changed')
  lease=C.decode(read(p['lease'])) if p['lease']is not None else initial_lease
  output=Path(p['output']);require(output.is_absolute()and not output.is_relative_to(root),'isolated continuation output required')
  operations=Path(config['run_root'])/'dispatcher-v2'/output.name
  if not continued:
   if permit:require(output.is_dir()and (output/'intent.json').is_file()and not(output/'permit').exists(),'permit namespace consumed')
   else:require(not output.exists(),'continuation namespace consumed')
   D.operation_namespace(config,str(operations))
  with ExitStack()as stack:
   if c['manager']=='package_runtime':
    require(c['manager']=='package_runtime','unsupported retained source manager')
    stack.enter_context(M.package_runtime(c['plan'],loaded[0],D,env))
   if 'completed_takeover'in p:
    lease,retained=completed_lease(p,U)
    stack.callback(retained.manager.__exit__,None,None,None)
   if 'prepermit_failure'in p and p['renewal_module']is not None:
    context_profile=read(c['plan']);old=read(context_profile['previous'])
    lease=M.failed_renewal_lease(c['plan'],context_profile,read(old['checkpoint_takeover']['profile']),lease,C.lifecycle)
   new=runtime_selection(p,env)
   core_name='harbor_disclosure_source_revision' if source_revision else 'harbor_disclosure_command_repair'
   module('scripts.batch.'+core_name,p['core'])
   RT=module('disclosure_selected_runtime',p['runtime']);stack.enter_context(RT.installed(p['runtime_profile']))
   from scripts.batch import harbor_instruction_worker as W,harbor_instruction_repair as R
   K=sys.modules['scripts.batch.'+core_name]
   require(binding(W.__file__)==binding(new/'scripts/batch/harbor_instruction_worker.py')and K.W is W,'installed disclosure worker differs')
   worker=replace(I.worker_decode(read(p['descriptor'])['worker']),root=new)
   selection=I.codec.decode(value['selection'])
   require(I.codec.encode(selection)==value['selection'],'selected typed source selection changed')
   selected_env=(C,I,env[2],W,R,config,root,controls)
   if 'command_routing'in p:stack.enter_context(command_routing(p,selected_env))
   K.inspect_history(p['profile'],worker)
   if 'completed_permit'in p:worker,selection,lease=completed_permit(p,selected_env,worker,selection,lease,K,continued=continued)
   if 'source_cache_resume'in p:
    cache=module('disclosure_revision_cache',read(p['source_cache_resume'])['helper'])
    _,worker,selection,lease=cache.validate(p,selected_env,worker,K,continued=continued)
    stack.enter_context(cache.step_scope(p,W,worker))
   if 'settled_permit'in p:
    worker,selection,lease=stack.enter_context(settled_context(p,selected_env,worker,selection,lease,K,continued=continued))
   if 'completed_step'in p and 'settled_permit'not in p:
    prior=read(read(p['completed_step'])['plan']);cp=binding(Path(prior['output'])/'permit/result.json');v=read(cp)
    require(v['status']=='RETRY_READY'and v['next_step']=='disclosure-build'and v['checkpoint']==binding(Path(prior['output'])/'permit/permit.json'),'actual direct repair permit required')
    _,selection,lease=I._prior(cp,v['descriptor']);actual=I.worker_decode(read(v['descriptor'])['worker'])
    require(actual.root==worker.root and actual.run==worker.run and actual.code_repair==v['checkpoint'],'direct completed permit worker differs')
    K.validate(actual,read(binding(p['tracker']['path'])));worker=actual
   if 'completed_step'in p:completed_step(p,selected_env)
   if 'preparation_resume'in p:selected_env=stack.enter_context(preparation_runtime(p,selected_env,worker,continued=continued))
   profile=read(p['profile']);stack.enter_context(skill_modules(profile['old_snapshot'],profile['snapshot']))
   stack.enter_context(dispatcher_selection(D,selected_env,worker))
   yield p,U,D,selected_env,worker,selection,lease,K,raw_guard,job

def handoff(U,p,I,C,w,selection,lease,permit):
 home=Path(p['output'])/'permit';old=read(p['descriptor'])
 w=replace(w,**({'disclosure_revision':permit}if p.get('source_revision')is True else {'code_repair':permit}));descriptor=U.keep(home/'descriptor.json',{**old,'worker':I.worker_encode(w),'initial':dict(selection=I.codec.encode(selection),lease=C.encode(lease)),
  'origin':[*old['origin'],p['descriptor'],p['initial_result'],p['profile'],permit]})
 checkpoint=U.keep(home/'result.json',I._result('RETRY_READY',descriptor,selection,lease,checkpoint=permit,next_step='disclosure-review'if p.get('source_revision')is True else 'disclosure-build',evidence=[p['initial_result'],p['profile'],permit]))
 I.selected(descriptor);I._prior(checkpoint,descriptor)
 return checkpoint

def install_commands(row,p,U,D,env,raw_guard,job):
 C,I=env[:2];operations=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
 def setup(current):
  require(current==dict(job,outcome=current['outcome'],operations=str(operations)),'original authoring job changed beyond retained handoff')
  return env
 D.setup=setup
 def guarded(command,*args,**options):
  kind,intent=U.retained_command(command,p,D,env);seconds=U.retained_seconds(kind,intent,env)
  require(options['timeout_seconds']==seconds and options['memory_limit_bytes']==8589934592,'original child guard changed')
  side=U.keep(Path(intent['path']).parent/'disclosure-bootstrap.json',dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=command,seconds=seconds,memory_bytes=8589934592))
  argv=[command[0],'-I','-B',p['helper']['path'],'child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']]
  return raw_guard(argv,*args,**options)
 C._run_in_process_group=guarded

def renew_for_permit(row,p,U,env,w,s,lease,K):
 if p.get('prepermit_renewal_first'):
  complete=binding(Path(p['output'])/'renewal/complete.json');value=read(complete)
  require(value['lease']==asdict(lease)and value['tracker']==binding(p['tracker']['path']),
   'first renewal lost current original custody')
  K.W.active(w,K.W.refreshed(s),lease)
  return lease,complete
 if p['renewal_module']is None:
  before=read(binding(p['tracker']['path']))
  require(before['leases']['task_agent']['expires_at']-time.time_ns()>3400*10**9,'first instruction guard lacks lease runway')
  return lease,None
 L=module('disclosure_live_renewal',p['renewal_module'])
 require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,'bounded same-owner renewal required')
 def validation(before,current):
  K.inspect_history(p['profile'],w);selected=K.W.refreshed(s)
  _,actual,_=K.W.active(w,selected,current);require(actual==before,'live custody changed during prefix validation')
  proof=env[4].prefix(before,selected,w.context,stop='disclosure')
  bound=U.keep(Path(p['output'])/'validated-prefix.json',dict(profile=p['profile'],source=p['source'],proof=proof,models_dispatched=0))
  return [p['profile'],p['source'],bound]
 from scripts.batch import harbor_tracker_lifecycle as life
 with (w.run/'worker.lock').open('a+b')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  archives='prepermit_failure'in p
  before_raw=Path(p['tracker']['path']).read_bytes()if archives else None
  result=L.renew(life=life,read=read,record=binding,keep=U.keep,path=p['tracker']['path'],lease=lease,
   authority_record=row,validation=validation,custody=lambda:U.custody(p,w.root),
   directory=Path(p['output'])/'renewal',retain_sufficient=True)
  if archives:
   home=Path(p['output'])/'renewal'
   require(result[1]['path']==str(home/'complete.json'),'prepermit path requires a completed original renewal')
   require(hashlib.sha256(before_raw).hexdigest()==lease.sha256,'pre-renewal archive changed')
   for path,raw in [(home/'before.json',before_raw),(home/'after.json',Path(p['tracker']['path']).read_bytes())]:
    with path.open('xb')as stream:stream.write(raw)
   require(binding(home/'after.json')['sha256']==result[0].sha256,'post-renewal archive changed')
  return result


def permit_child(row):
 with context(row,permit=True)as(p,U,D,env,w,s,l,K,raw,job):
  U.custody(p,w.root);home=Path(p['output'])
  require(read(binding(home/'intent.json'))==dict(plan=row,helper=p['helper'],automatic_retry=False),'permit parent intent differs')
  l,renewal=renew_for_permit(row,p,U,env,w,s,l,K)
  permit=K.authorize(w,s,l,profile_binding=p['profile'],directory=home/'permit')
  return handoff(U,p,env[1],env[0],w,env[1].codec.decode(read(permit)['selection']),l,permit)

def child(row,intent,sidecar):
 require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated child required')
 with context(row,continued=True)as(p,U,D,env,w,s,l,K,raw,job):
  U.custody(p,w.root);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
  require(actual==intent and side==dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592)
   and Path(sidecar['path'])==Path(intent['path']).parent/'disclosure-bootstrap.json','child detached from original intent')
  install_commands(row,p,U,D,env,raw,job)
  return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)

def stopped_disclosure(U,p,row,job,guard,outcome,D,env):
 checkpoint,result=D.successful(outcome,env)
 require(result['status']=='STEP_COMPLETE'and result['next_step']=='disclosure-review','completed disclosure boundary required')
 return U.keep(Path(p['output'])/'result.json',dict(status='DISCLOSURE_BUILD_COMPLETE',workbook=p['workbook'],continuation=row,config=job['config'],outcome=outcome,checkpoint=checkpoint,permit_guard=guard,publication_performed=False,automatic_retry=False))

def renew_only(row):
 """Run original early lease hold and owning renewal, then stop before review."""
 with context(row)as(p,U,D,env,w,s,l,K,raw,job):
  require('preparation_resume'in p and not p.get('stop_after_disclosure_build'),'exact unused-review recovery required')
  U.custody(p,w.root);home=Path(p['output']);home.mkdir(parents=True)
  U.keep(home/'intent.json',dict(plan=row,helper=p['helper'],automatic_retry=False))
  _,outcome=completed_step(p,env);checkpoint,value=D.successful(outcome,env)
  _,s,l=env[1]._prior(checkpoint,value['descriptor']);w=env[1].worker_decode(read(value['descriptor'])['worker'])
  remaining=K.W.active(w,s,l)[1]['leases']['task_agent']['expires_at']-time.time_ns()
  require(930*10**9<remaining<=3330*10**9,'original renewal-only runway boundary required')
  operations=U.operations_for(D,env[5],home);current={**job,'outcome':outcome,'operations':str(operations)};bound=U.keep(home/'authoring-job.json',current)
  install_commands(row,p,U,D,env,raw,job)
  with D.fleet_slot(p['workbook'])as fleet_fd:
   operations.mkdir(parents=True);U.keep(operations/'job.json',current)
   held=env[1].run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),env[7],operations/'000',models=False,commands=False,containers=False,guardian_lock_fds=fleet_fd)
   selected,observed=D.successful(held,env)
   require(observed['status']=='LEASE_RENEWAL_REQUIRED'and observed['checkpoint']==value['checkpoint']and observed['next_step']=='disclosure-review','original early renewal hold required')
   renewed=D.renew(bound,env,selected,operations/'001',fleet_fd)
  final,result=D.successful(renewed,env)
  require(result['status']=='RETRY_READY'and result['next_step']=='disclosure-review','original renewal completion required')
  return U.keep(home/'result.json',dict(status='LEASE_RENEWED',workbook=p['workbook'],continuation=row,job=bound,config=job['config'],outcome=renewed,checkpoint=final,review_dispatched=False,publication_performed=False,automatic_retry=False))

@contextmanager
def first_renewal_authority(row):
 """Read the original completed takeover before any expensive quality replay."""
 p=read(row);require(p.get('prepermit_renewal_first')is True and p['helper']==binding(__file__),
  'explicit selected first-renewal driver required')
 prepermit_stop(p);runtime_inputs(p)
 module('disclosure_stdlib',p['stdlib']).preload_inspect()
 U=module('disclosure_first_renewal_utilities',p['utilities'])
 require(p['authorization']['sha256']==U.AUTHORIZATION_SHA and binding(p['authorization']['path'])==p['authorization'],
  'explicit original renewal authorization required')
 cp=read(p['context']['plan']);old=read(cp['previous']);profile=read(old['checkpoint_takeover']['profile'])
 tp=read(profile['takeover_plan']);source=p['source'];newroot=Path(read(read(p['profile'])['snapshot'])['root'])
 require(profile['source']==old['source']==source and binding(source['path'])==source
  and profile['workbook']==old['workbook']==p['workbook']and profile['tracker']==p['tracker']
  and profile['takeover_module']==tp['shared']['takeover']and profile['worker_run']==str(Path(read(p['descriptor'])['worker']['run']))
  and cp['lease_renewal']==dict(module=p['renewal_module'],directory=str(Path(p['output'])/'renewal')),
  'first renewal detached from failed source or original T profile')
 before_path=list(sys.path);sys.path.insert(0,profile['worker_root'])
 import scripts.batch as package
 restored=[]
 try:
  for key,name in [('lifecycle','harbor_tracker_lifecycle'),('allowance','harbor_continuation_allowance')]:
   full='scripts.batch.'+name;restored.append((full,sys.modules.get(full),name,getattr(package,name,None)))
   setattr(package,name,module(full,tp['shared'][key]))
  T=module('disclosure_first_original_takeover',tp['shared']['takeover'])
  def validate():
   before,after,lease=T.validate_completion(profile['completion']);complete=read(profile['completion']);stop=read(complete['stopped_owner_proof'])
   require(stop['selection']==T._selection_value(T._selection(tp['selection']))
    and after['workbook_id']==p['workbook']and after['bindings']['original_raw_source']==source
    and lease.sha256==p['tracker']['sha256']and binding(p['tracker']['path'])==p['tracker']
    and read(p['tracker'])==after,'original completed takeover no longer owns current tracker')
   return lease
  lease=validate();L=module('disclosure_first_original_lease',p['renewal_module'])
  require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,'bounded original renewal required')
  yield p,U,profile,T,L,lease,newroot,validate
 finally:
  for full,previous,name,attribute in reversed(restored):
   if previous is None:sys.modules.pop(full,None)
   else:sys.modules[full]=previous
   if attribute is None:
    if hasattr(package,name):delattr(package,name)
   else:setattr(package,name,attribute)
  sys.path[:]=before_path


def renew_before_context(row):
 with first_renewal_authority(row)as(p,U,profile,T,L,lease,newroot,validate):
  home=Path(p['output']);require(not home.exists(),'first renewal already attempted; reconcile')
  with (Path(profile['worker_run'])/'worker.lock').open('a+b')as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   U.custody(p,newroot);require(validate()==lease,'takeover changed before renewal')
   before=Path(p['tracker']['path']).read_bytes()
   def validation(current,observed):
    require(observed==lease and validate()==lease and read(p['tracker'])==current,
     'current owner changed before original renewal CAS')
    return [profile['completion'],p['context']['plan'],p['prepermit_failure'],p['source'],p['profile']]
   home.mkdir(parents=True);U.keep(home/'intent.json',dict(plan=row,helper=p['helper'],automatic_retry=False))
   current,receipt=L.renew(life=T.life,read=read,record=binding,keep=U.keep,path=p['tracker']['path'],lease=lease,
    authority_record=row,validation=validation,custody=lambda:U.custody(p,newroot),directory=home/'renewal')
   for path,raw in [(home/'renewal/before.json',before),(home/'renewal/after.json',Path(p['tracker']['path']).read_bytes())]:
    with path.open('xb')as stream:stream.write(raw)
   require(binding(home/'renewal/before.json')['sha256']==lease.sha256
    and binding(home/'renewal/after.json')['sha256']==current.sha256,'original first renewal archive changed')
   return receipt


def run(row):
 selected=read(row);early=selected.get('prepermit_renewal_first')is True
 if early:renew_before_context(row)
 with context(row,permit=early)as(p,U,D,env,w,s,l,K,raw,job):
  U.custody(p,w.root);home=Path(p['output'])
  if early:
   marker=binding(home/'intent.json');require(read(marker)==dict(plan=row,helper=p['helper'],automatic_retry=False),'first-renewal parent intent differs')
  else:
   home.mkdir(parents=True);marker=U.keep(home/'intent.json',dict(plan=row,helper=p['helper'],automatic_retry=False))
  if 'completed_permit'in p:
   saved=read(p['completed_permit']);checkpoint=saved['checkpoint'];guard=saved['guard']
  elif 'source_cache_resume'in p:
   cache=module('disclosure_revision_cache',read(p['source_cache_resume'])['helper']);a,_=cache.selection(p)
   # Original live ownership is checked again immediately before the move.
   env[3].active(w,s,l);U.custody(p,w.root);cache.move(p,a,w,K,U.keep)
   checkpoint=a['checkpoint'];guard=binding(Path(read(a['plan'])['output'])/'permit-resource.json')
  elif 'completed_step'in p and 'settled_permit'not in p:
   prior=read(read(p['completed_step'])['plan']);checkpoint=binding(Path(prior['output'])/'permit/result.json');guard=binding(Path(prior['output'])/'permit-resource.json')
  elif 'settled_permit'in p:
   settled=read(p['settled_permit']);checkpoint=settled['checkpoint']
   guard=binding(Path(read(settled['plan'])['output'])/'permit-resource.json')
  else:
   # The permit child revalidates the full context before any repair reservation.
   command=[sys.executable,'-I','-B',__file__,'permit-child','--plan',row['path'],'--sha256',row['sha256']]
   metrics={};start=time.monotonic();rc=None;failure=None
   try:
    with (home/'permit.log').open('xb')as log:rc=raw(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=8589934592,metrics=metrics,cwd=env[6],env=os.environ.copy())
   except BaseException as exc:failure=type(exc).__name__
   guard=U.keep(home/'permit-resource.json',dict(intent=marker,returncode=rc,failure=failure,command=command,seconds=3300,memory_bytes=8589934592,metrics=metrics,elapsed_seconds=time.monotonic()-start,log=binding(home/'permit.log'),models_dispatched=0))
   require(rc==0 and failure is None,'repair permit requires reconciliation; no replay')
   checkpoint=binding(home/'permit/result.json')
  value=read(checkpoint);env[1]._prior(checkpoint,value['descriptor'])
  install_commands(row,p,U,D,env,raw,job);U.custody(p,w.root)
  if 'completed_step'in p:_,outcome=completed_step(p,env)
  else:outcome=env[1].run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),env[7],home/'first-step',models=True,commands=True,containers=True)
  if p.get('stop_after_disclosure_build')is True:return stopped_disclosure(U,p,row,job,guard,outcome,D,env)
  D.successful(outcome,env)
  current={**job,'outcome':outcome,'operations':str(U.operations_for(D,env[5],home))};bound=U.keep(home/'authoring-job.json',current)
  D.run(bound);held=read(binding(Path(current['operations'])/'hold.json'));last,result=D.successful(held['outcome'],env)
  require(result['status']=='PUBLICATION_READY','remaining authoring reached a retained hold')
  return U.keep(home/'result.json',dict(status='PUBLICATION_READY',workbook=p['workbook'],continuation=row,job=bound,config=job['config'],outcome=held['outcome'],checkpoint=last,permit_guard=guard,publication_performed=False,automatic_retry=False))

def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=('probe','run','renew-only','permit-child','child'));a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True)
 for n in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):a.add_argument('--'+n)
 q=a.parse_args();row=dict(path=q.plan,sha256=q.sha256)
 if q.action=='probe':
  with context(row)as(p,U,D,env,w,s,l,K,raw,job):
   K.W.active(w,K.W.refreshed(s),l);result=dict(status='PASS',plan=row,models_dispatched=0,task_mutations=0)
   if 'command_routing'in p:
    _,before,tracker=K.W.active(w,s,l);env[4].validate(w,before)
    require(K.W.task(w,s,tracker).run==K.producer_run(w),'selected producer route differs')
    observed=K.W._operation(w,s,l,'disclosure-build',Path(p['output'])/'probe-not-created',models=False,containers=False,commands=False,credential=None,lock_fd=None)
    require(observed==(s,l,'COMMANDS_REQUIRED')and not Path(p['output']).exists(),'read-only routed operation changed task or capability')
    result['command_routes_validated']=True
 elif q.action=='run':result=run(row)
 elif q.action=='renew-only':result=renew_only(row)
 elif q.action=='permit-child':result=permit_child(row)
 else:result=child(row,dict(path=q.intent,sha256=q.intent_sha256),dict(path=q.bootstrap,sha256=q.bootstrap_sha256))
 print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
