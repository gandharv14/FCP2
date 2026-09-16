"""Requalify unchanged ordinary dialogue authors with an explicit checker.

Original fresh history, author Calls and owning instruction guards remain the
validators. The mechanical checkpoint is additive and cannot dispatch authors.
"""
from pathlib import Path
from contextlib import contextmanager,ExitStack
from dataclasses import asdict
import argparse,fcntl,hashlib,importlib.util,json,os,sys,time
SCHEMA='fleet-ordinary-mechanical-dialogue/v1'
PROFILE='fleet-retained-runtime/mechanical-dialogue/v1'
FIELDS={'schema_version','helper','utilities','core','fresh_plan','original_job','outcome','result','descriptor','blocked','source','tracker','workbook','owner','fence','capacity_policy','authorization','validator','runtime_profile','renewal','output'}
AUTH='acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(q.is_symlink()for q in(p,*p.parents)),'canonical mechanical evidence required')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):
 require(set(row)=={'path','sha256'}and binding(row['path'])==row,'mechanical evidence changed')
 return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
 p=Path(path);fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'w')as f:json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
 return binding(p)
def module(name,row):
 require(binding(row['path'])==row,'mechanical implementation changed')
 spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def history(p,*,probe):
 fresh=read(p['fresh_plan']);job=read(p['original_job']);root=Path(fresh['code_root']);config=read(job['config'])
 require(fresh['schema_version']=='harbor-fleet-fresh-plan/v1'and fresh['workbook']==p['workbook']
  and job['driver']==fresh['dispatcher']and job['config']==binding(Path(fresh['home'])/'authoring/config.json')
  and read(config['code'])['root']==str(root),'original fresh job differs')
 sys.path.insert(0,str(root));F=module('mechanical_original_fresh',binding(root/'scripts/fleet/fresh.py'))
 legacy=fresh.get('legacy_research_history')
 if legacy is not None:
  from scripts.batch import harbor_legacy_research_history as L
  inventory=read(fresh['inventory']);ids={r['workbook_id']for r in inventory['sources']}
  inherited=L.validate(legacy,workbook=p['workbook'],original=p['source'],history_roots=fresh['history_roots'],known_ids=ids)
  source=F._validate(fresh,lambda roots,known:L.author_survey(inherited))
  L.source_handoff(legacy,read(fresh['reconstruction']['handoff']))
  for suffix,value in [('PATH',legacy['path']),('SHA256',legacy['sha256'])]:
   key='HARBOR_FLEET_LEGACY_RESEARCH_'+suffix;require(os.environ.get(key)in(None,value),'foreign legacy history');os.environ[key]=value
  require(not any(os.environ.get(k)for k in('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH','HARBOR_FLEET_SOURCE_HISTORY_PATH')),'foreign history authority')
  if probe:
   def projected(roots,known):return L.author_survey(L.validate(legacy,workbook=p['workbook'],original=p['source'],history_roots=roots,known_ids=known))
   L.projected_survey=projected
 else:
  source=F.validate(fresh)
 require({k:source[k]for k in('path','sha256')}==p['source'],'fresh source differs')
 return fresh,job,root

def boundary(p,D,env):
 C,I,P,W,R,config,root,controls=env
 checkpoint,value=D.successful(p['outcome']);failure=read(p['blocked'])
 require(checkpoint==p['result']and value['schema_version']==I.SCHEMA and value['status']=='DIALOGUE_REPAIR_REQUIRED'
  and value['next_step']=='dialogue-paraphraser'and value['checkpoint']==p['blocked']and value['descriptor']==p['descriptor']
  and value['publication_performed']is False,'exact stopped mechanical author result required')
 directory=Path(p['outcome']['path']).parent;intent=read(binding(directory/'intent.json'))
 require(intent['code']==binding(I.__file__)and intent['base_code']==binding(C.__file__)and intent['controls']==C.encode(controls)
  and intent['action']=='step'and intent['arguments']['descriptor']==p['descriptor']and value['evidence'][0]==intent['arguments']['checkpoint']
  and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments'])and intent['memory_limit_bytes']==8589934592,
  'original mechanical guard changed')
 w,expected,_=I.selected(p['descriptor']);s=I.codec.decode(value['selection']);l=C.decode(value['lease'])
 require(expected==controls and w.root==root and w.run==Path(config['run_root'])/'instruction'
  and s.content.bindings['original_raw_source']==p['source']and failure['selection']==value['selection']and failure['lease']==asdict(l),
  'original mechanical worker/source/lease differs')
 require(all(getattr(w,k,None)is None for k in('retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair','disclosure_revision','dialogue_pre_review_repair')),'ordinary mechanical author required')
 if w.lease_renewal is not None:R.validate(w,read(binding(w.context.tracker_root/(p['workbook']+'.json'))))
 return w,s,l

def profile(p,root):
 value=read(p['runtime_profile']);expected=dict(schema_version=PROFILE,helper=p['helper'],core=p['core'],fresh_plan=p['fresh_plan'],
  original_job=p['original_job'],original_validator=binding(root/'.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'),validator=p['validator'])
 if 'preserved_context'in p:expected['preserved_context']=p['preserved_context']
 if 'semantic_context'in p:expected['semantic_context']=p['semantic_context']
 require(value==expected,'closed mechanical runtime selection differs');return value

def preserved_boundary(p,old,env,D):
 """Authenticate the actual guarded prefix ending in a mechanical hold."""
 C,I,_,W=env[:4];spec=p['preserved_context'];previous=None
 require(p['fresh_plan']is None and old['source']==p['source']and old['workbook']==p['workbook']
  and p['fence']==old['fence']+1,'preserved mechanical source or ownership differs')
 rows=spec['outcomes'];require(type(rows)is list and 2<=len(rows)<=30,'complete bounded guarded prefix required')
 for index,row in enumerate(rows):
  out=read(row);guard=read(out['resource']);intent=read(guard['intent']);value=read(out['result'])
  require(D.successful(row)==(out['result'],value),'original guarded outcome validator differs')
  side=read(binding(Path(guard['intent']['path']).parent/'preserved-gate-bootstrap.json'))
  require(out['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard.get('failure')is None
   and intent['code']==binding(I.__file__)and intent['base_code']==binding(C.__file__)
   and intent['controls']==C.encode(env[7])and side['plan']==spec['plan']and side['helper']==old['helper']
   and intent['action']=='step'and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments'])
   and intent['memory_limit_bytes']==8589934592
   and side['intent']==guard['intent']and side['automatic_retry']is False,
   'preserved mechanical prefix lost original guard or parent')
  if previous is not None:require(intent['arguments']['checkpoint']==previous and value['evidence'][0]==previous,'preserved mechanical prefix is discontinuous')
  else:require(row==binding(Path(old['output'])/'first-step/outcome.json'),'preserved mechanical prefix lacks original first step')
  if index<len(rows)-1:require(value['status']=='STEP_COMPLETE','earlier preserved step did not complete')
  previous=out['result']
 require(rows[-1]==p['outcome']and previous==p['result']and value['status']=='DIALOGUE_REPAIR_REQUIRED'
  and value['next_step']=='dialogue-paraphraser'and value['checkpoint']==p['blocked']and value['descriptor']==p['descriptor'],
  'preserved mechanical hold differs')
 failure=read(p['blocked']);w,controls,_=I.selected(p['descriptor']);s=I.codec.decode(value['selection']);l=C.decode(value['lease'])
 require(controls==env[7]and failure['selection']==value['selection']and failure['lease']==asdict(l)
  and s.content.bindings['original_raw_source']==p['source'],'preserved mechanical selected content differs')
 return w,s,l

@contextmanager
def clause_library(validator,selected,original,*,atom='otherwise'):
 """Select only the declared zero-unless atom addition in original globals."""
 import ast,inspect,textwrap
 from types import FunctionType
 old=validator.clause_covered;require(binding(old.__code__.co_filename)==original,'original clause library changed')
 lib=module('mechanical_selected_clause_library',selected)
 before=ast.parse(textwrap.dedent(inspect.getsource(old)));after=ast.parse(textwrap.dedent(inspect.getsource(lib.clause_covered)))
 body=after.body[0].body;matches=[n for n in body if isinstance(n,ast.If)and ast.dump(n.test)==ast.dump(ast.parse('needle == '+repr(atom),mode='eval').body)]
 require(atom in {'otherwise','held flat'} and len(matches)==1,'one explicit supported atom addition required');body.remove(matches[0])
 require(ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False),'clause library changed beyond otherwise atom')
 chosen=FunctionType(lib.clause_covered.__code__,old.__globals__,old.__name__,old.__defaults__,old.__closure__)
 validator.clause_covered=chosen
 try:yield validator
 finally:validator.clause_covered=old

@contextmanager
def preserved_step_directory(W,core):
 """Keep the exact preserved worker clone attached to its selected directory."""
 import inspect
 step=W._step
 require(binding(inspect.getsourcefile(step))==core and step.__name__=='step','exact preserved worker wrapper required')
 scope=inspect.getclosurevars(step).nonlocals.get('scope')
 require(isinstance(scope,dict)and callable(scope.get('step_directory')),'preserved worker directory scope missing')
 old=scope['step_directory']
 require(old is W.step_directory or getattr(old,'_mechanical_worker',None)is W,'preserved worker directory callback diverged')
 def selected(*args,**kwargs):return W.step_directory(*args,**kwargs)
 selected._mechanical_worker=W;scope['step_directory']=selected
 try:yield
 finally:scope['step_directory']=old


def install_mechanical_projection(K,W,certificate):
 selected=getattr(W,'_fleet_mechanical_certificate',None)
 if selected is not None:
  require(selected==certificate,'foreign mechanical completion already installed');K.validate(certificate)
  if getattr(W,'_fleet_mechanical_directory',None)is W.step_directory:return
 # A nested original worker scope restores its directory callback when it exits.
 # Its historical certificate marker alone does not mean the projection remains.
 K.install_projection(W,certificate);W._fleet_mechanical_certificate=certificate
 W._fleet_mechanical_directory=W.step_directory


@contextmanager
def renewal_runtime(p,env,worker,A,D):
 """Select the existing source-bound aggregate renewal only where declared."""
 if 'renewal_runtime'not in p:
  yield env;return
 require('preserved_context'in p and 'completed_authorization'in p,
  'mechanical renewal requires an authentic completed preserved checkpoint')
 selected=read(p['renewal_runtime']);require(selected.get('schema_version')=='fleet-adoption-command-renewal/v1',
  'unsupported completed aggregate renewal runtime')
 R=module('mechanical_completed_renewal_runtime',selected['helper'])
 with R.installed(sys.modules[__name__],p['renewal_runtime'],env,worker,A,D)as current:yield current


@contextmanager
def preserved_archive_module(parent,old,spec):
 """Select a declared archive reader only at the original historical import."""
 if 'semantic_archive'not in spec:
  yield;return
 selection=spec['semantic_archive'];require(set(selection)=={'original','selected'}and selection['original']==old['semantic_archive']['helper'],
  'historical archive substitution differs from original selected reader')
 require(binding(selection['original']['path'])==selection['original'],'original archive module changed')
 replacement=module('mechanical_completed_aggregate_archive',selection['selected'])
 require(replacement.SCHEMA=='fleet-disclosure-semantic-archive/v1','unsupported completed aggregate archive reader')
 original=parent.module
 def selected(name,row):
  if row==selection['original']:return replacement
  return original(name,row)
 parent.module=selected
 try:yield
 finally:parent.module=original


@contextmanager
def preserved_context(row,p,*,invocation=None):
 authrow,authp=authorization_selection(row,p)
 spec=p['preserved_context'];require(set(spec)-{'semantic_archive'}=={'plan','adapter','witness','outcomes','library','original_library','instruction_history'},'closed preserved mechanical context required')
 old=read(spec['plan']);parent=module('mechanical_preserved_parent',old['helper']);adapter=module('mechanical_preserved_checkpoint',spec['adapter'])
 source=read(old['previous']);require(p['original_job']==source['original_job'],'preserved original job selection differs')
 K=module('mechanical_checkpoint_core',p['core']);U=module('mechanical_original_context',p['utilities']);L=module('mechanical_live_lease',p['renewal'])
 require(K.SCHEMA=='harbor-dialogue-mechanical-requalification/v1'and L.DURATION_NS==7200*10**9,'original bounded mechanical APIs required')
 active=[]
 @contextmanager
 def runtime(W,I,A):
  with ExitStack()as stack:
   stack.enter_context(preserved_step_directory(W,old['core']))
   outcome=Path(authp['output'])/'authorization/outcome.json'
   if outcome.exists():
    result=read(read(binding(outcome))['result']);certificate=read(result['checkpoint'])['mechanical_requalification'];proof=read(certificate)
    changed=[r for r in proof['evidence']if Path(r['path']).name=='instruction.md'and binding(r['path'])!=r]
    if changed:
     require(len(changed)==1,'multiple changed mechanical instruction records')
     writer=W.request_saved(W.step_directory(I.selected(result['descriptor'])[0],'dialogue-writer')/'request.json');context=read(writer.context);call=proof['author_calls'][0]
     index=context['evidence'].index(changed[0]);h=dict(context=writer.context,submitted=call['submitted'],original=changed[0],archive=context['budget_evidence'][index+1],current=binding(changed[0]['path']))
     tail=module('mechanical_actual_instruction_history',spec['instruction_history']);validator=W.producer.skill_module(I.selected(result['descriptor'])[0].root,'additional-assumptions-dialogue','validate_dialogue')
     stack.enter_context(tail.instruction_history(K,certificate,h,validator))
    install_mechanical_projection(K,W,certificate)
   if 'checkpoint_takeover'in p:
    bridge=module('mechanical_checkpoint_takeover_runtime',p['checkpoint_takeover']['helper']);stack.enter_context(bridge.installed(sys.modules[__name__],p,I,W,K,authrow))
   active.append(A)
   try:yield
   finally:active.pop()
 def current(env,worker):
  originalD=module('mechanical_preserved_dispatcher',read(p['original_job'])['driver'])
  I=env[1];w,s,l=preserved_boundary(authp,old,env,originalD)
  outcome=Path(authp['output'])/'authorization/outcome.json'
  if invocation is not None:
   intent,side=invocation;value=read(side);D=module('mechanical_actual_dispatcher',read(p['original_job'])['driver']);kind,actual=U.retained_command(value['original_command'],p,D,env)
   require(actual==intent and value['plan']==row and value['helper']==p['helper'],'mechanical cold checkpoint detached from parent')
   if kind=='instruction':args=read(intent)['arguments'];w,controls,_=I.selected(args['descriptor'])
   else:held=read(read(intent)['checkpoint']);w,controls,_=I.selected(held['descriptor'])
   require(active,'completed mechanical runtime missing')
   with renewal_runtime(p,(*env[:6],w.root,env[7]),w,active[-1],D)as current_env:
    if kind=='instruction':_,s,l=I._prior(args['checkpoint'],args['descriptor'])
    else:_,w,_,s,l=D.renewal_context(current_env,read(intent)['checkpoint'])
  elif outcome.exists():
   result=read(read(binding(outcome))['result']);w,controls,_=I.selected(result['descriptor'])
   require(active,'completed mechanical runtime missing')
   with renewal_runtime(p,(*env[:6],w.root,env[7]),w,active[-1],originalD):
    _,result=authorization_outcome(authrow,binding(outcome),I,K);_,s,l=I._prior(binding(Path(authp['output'])/'authorization/result.json'),result['descriptor'])
  if 'checkpoint_takeover'in p and invocation is None:
   bridge=module('mechanical_checkpoint_takeover_current',p['checkpoint_takeover']['helper']);w,s,l=bridge.current(sys.modules[__name__],p,I,env[3],K,authrow)
  return w,env[7],s,l
 with preserved_archive_module(parent,old,spec),adapter.installed(parent,spec['plan'],witness=spec['witness'],checkpoint=current,runtime=runtime)as(_,S,priorK,priorprofile,priorU,D,env,w,s,l,A,raw,job):
  require(job==read(p['original_job'])and w.root==Path(p['validator']['path']).parents[4],
   'preserved original author job or selected worker root differs');profile(p,w.root)
  V=env[3].producer.skill_module(w.root,'additional-assumptions-dialogue','validate_dialogue');require(binding(V.__file__)==p['validator'],'mechanical original validator differs')
  current_env=(*env[:6],w.root,env[7])
  with dispatcher_environment(D,env,current_env,w),clause_library(V,spec['library'],spec['original_library']),checker_scope(env[3].producer,w.root,V),renewal_runtime(p,current_env,w,A,D)as selected_env:yield p,U,D,selected_env,w,s,l,K,V,L


@contextmanager
def dispatcher_environment(D,source,current,worker):
 """Keep the original retained selector's source environment and checks."""
 require(len(source)==len(current)==8 and source[:6]==current[:6]and source[7]==current[7]
  and current[6]==worker.root,'only selected worker root may differ')
 original=D.selected
 def selected(observed,value):
  require(observed==current,'dispatcher changed current mechanical environment')
  candidate,_,_=current[1].selected(value['descriptor'])
  require(candidate.root==worker.root and candidate.run==worker.run,'mechanical dispatcher changed worker')
  return original(source,value)
 D.selected=selected
 try:yield
 finally:D.selected=original


def authorization_selection(row,p):
 if 'completed_authorization'not in p:return row,p
 c=p['completed_authorization'];require(set(c)=={'plan','worker_outcome','log'},'closed completed authorization custody required')
 old=read(c['plan']);changed={'helper','owner','fence','output','tracker','runtime_profile','completed_authorization','renewal_runtime','checkpoint_takeover'}
 require('completed_authorization'not in old and 'preserved_context'in old
  and {k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
  and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
  'completed authorization changed original work')
 before=read(old['runtime_profile']);after=read(p['runtime_profile'])
 require({k:v for k,v in before.items()if k!='helper'}=={k:v for k,v in after.items()if k!='helper'}
  and before['helper']==old['helper']and after['helper']==p['helper'],'completed checker profile differs')
 home=Path(old['output']);outer=read(c['worker_outcome']);intent=read(outer['intent']);job=read(intent['job']);log=Path(c['log']['path'])
 require(binding(log)==c['log'],'stopped worker log changed')
 require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None
  and intent['owner']==old['owner']and job['driver']==old['helper']and job['result']==str(home/'result.json')
  and log==Path(job['run'])/'worker.log'and log.read_text().rstrip().endswith('ValueError: dispatcher environment differs from retained context')
  and 'worker,recipe=selected(env,value)'in log.read_text(),'exact pre-dispatch environment failure required')
 author=read(binding(home/'authoring-job.json'));ops=Path(author['operations'])
 require(set(x.name for x in home.iterdir())=={'authorization','authoring-job.json'}
  and set(x.name for x in ops.iterdir())=={'job.json'}and read(binding(ops/'job.json'))==author
  and author==dict(read(old['original_job']),outcome=binding(home/'authorization/outcome.json'),operations=str(ops))
  and p['tracker']['path']==old['tracker']['path']and (p['tracker']['sha256']==read(binding(home/'authorization/mechanical/complete.json'))['lease']['sha256']or('checkpoint_takeover'in p and read(p['checkpoint_takeover']['profile'])['tracker']==p['tracker']and read(p['checkpoint_takeover']['profile'])['prior_takeover_chain']['original_tracker']['sha256']==read(binding(home/'authorization/mechanical/complete.json'))['lease']['sha256'])),
  'a downstream instruction or changed authorization tracker exists')
 return c['plan'],old

def pair_verifier(P,root,*,generation=1):
 def verify(task,text,*,calls):
  from scripts.batch import harbor_dialogue_authoring as A
  identity=P.task_context(task)[1];actual=read(calls[0].submitted).get('semantic_ordinal')
  require(type(actual)is int and type(generation)is int and generation in (1,2) and actual==generation,
   'only the original first author pair may requalify'if generation==1 else'exact completed second author generation required')
  _,claims,_,_=P._dialogue(task,generation,root)
  return A.verify_pair(calls,policy=task.policy,claims=claims,draft=text,round=generation,root=root,
   workbook=identity['workbook'],original_sha256=identity['original']['sha256'])
 return verify

@contextmanager
def checker_scope(P,root,selected):
 """Select only this validator; every other original skill lookup delegates."""
 original=P.skill_module;replacements=[]
 def skill(path,name,part):
  if Path(path)==root and name=='additional-assumptions-dialogue'and part=='validate_dialogue':return selected
  return original(path,name,part)
 for m in tuple(sys.modules.values()):
  ns=getattr(m,'__dict__',None)
  if ns is not None:
   for key,value in tuple(ns.items()):
    if value is original:replacements.append((ns,key,value));ns[key]=skill
 try:yield
 finally:
  for ns,key,value in reversed(replacements):ns[key]=value

def runtime_inputs(p):
 rows=[p[key]for key in('helper','utilities','core','capacity_policy','authorization','validator','runtime_profile','renewal')]
 if 'semantic_context'in p:
  rows.extend(v for v in p['semantic_context'].values()if isinstance(v,dict)and set(v)=={'path','sha256'})
 if 'renewal_runtime'in p:
  rows.append(p['renewal_runtime']);rows.extend(v for v in read(p['renewal_runtime']).values()if isinstance(v,dict)and set(v)=={'path','sha256'})
 if 'checkpoint_takeover'in p:rows.extend(p['checkpoint_takeover'].values())
 if 'completed_takeover'in p:rows.extend(p['completed_takeover'][key]for key in('runtime','plan','completion','dependency_runtime','dependency_profile'))
 for row in rows:require(set(row)=={'path','sha256'}and binding(row['path'])==row,'missing mechanical runtime dependency')
 return list({row['path']:row for row in rows}.values())

@contextmanager
def context(row,*,probe=False,invocation=None):
 p=read(row);require(set(p)-{'completed_takeover','preauthorization_failure','preserved_context','authorization_seconds','completed_authorization','renewal_runtime','semantic_context','checkpoint_takeover'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed mechanical continuation required')
 runtime_inputs(p)
 if 'preauthorization_failure'in p:preauthorization_failure(p)
 require(p['authorization']['sha256']==AUTH and binding(p['authorization']['path'])==p['authorization'],'explicit mechanical authorization required')
 require(type(p['fence'])is int and p['fence']>0 and p['owner'],'exact central identity required')
 if 'semantic_context'in p:
  Q=module('mechanical_semantic_context',p['semantic_context']['reader'])
  with Q.context(sys.modules[__name__],row,p,probe=probe,invocation=invocation)as value:yield value
  return
 if 'preserved_context'in p:
  with preserved_context(row,p,invocation=invocation)as value:yield value
  return
 U=module('mechanical_original_context',p['utilities']);fresh,job,root=history(p,probe=probe)
 _,loaded,D,env=U.load(dict(workbook=p['workbook'],fresh_plan=p['fresh_plan'],original_job=p['original_job']))
 require(loaded==job and env[6]==root and all(job[k]is True for k in('models','commands','containers'))and job['publish']is False,'original dispatcher policy changed')
 w,s,l=boundary(p,D,env);profile(p,root);K=module('mechanical_checkpoint_core',p['core'])
 # The original skill loader installs its exact unchanged sibling closure.
 original_validator=env[3].producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
 require(binding(original_validator.__file__)==read(p['runtime_profile'])['original_validator'],'original checker dependency closure differs')
 V=module('mechanical_selected_validator',p['validator']);L=module('mechanical_live_lease',p['renewal'])
 require(K.SCHEMA=='harbor-dialogue-mechanical-requalification/v1'and L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,'mechanical core or bounded renewal differs')
 manager=None
 if 'completed_takeover'in p:
  lease_plan={k:p[k]for k in('workbook','fresh_plan','original_job','result','outcome','tracker','source','owner','completed_takeover')}
  lease_plan['helper']=p['utilities'];l,retained=U.retained_lease(lease_plan);manager=retained.manager
  from scripts.batch import harbor_lease_takeover as T
  L.takeover=retained.takeover;L.takeover_reader=T
 try:
  with checker_scope(env[3].producer,root,V):yield p,U,D,env,w,s,l,K,V,L
 finally:
  if manager is not None:manager.__exit__(*sys.exc_info())

def takeover_options(L):
 return dict(takeover=L.takeover,takeover_reader=L.takeover_reader)if hasattr(L,'takeover')else {}

def completed_prefix(env,before,selection,worker):
 from scripts.batch import harbor_instruction_repair as R
 require(binding(R.__file__)==binding(env[6]/'scripts/batch/harbor_instruction_repair.py'),'original completed-prefix reader differs')
 return R.prefix(before,selection,worker.context,stop='dialogue')

def preauthorization_failure(p):
 """Retain the exact stopped API failure before any mechanical checkpoint."""
 c=p['preauthorization_failure'];require(set(c)=={'plan','outcome'},'closed preauthorization failure required')
 old=read(c['plan']);require('preauthorization_failure'not in old and 'completed_takeover'in old,'only first completed-takeover authorization failure supported')
 changed={'helper','runtime_profile','output','fence','preauthorization_failure'}
 require({k:v for k,v in p.items()if k not in changed}=={k:v for k,v in old.items()if k not in changed}
  and p['fence']==old['fence']+1 and p['output']!=old['output'],'preauthorization continuation changed original work')
 previous=read(old['runtime_profile']);current=read(p['runtime_profile'])
 require({k:v for k,v in previous.items()if k!='helper'}=={k:v for k,v in current.items()if k!='helper'}
  and previous['helper']==old['helper']and current['helper']==p['helper'],'preauthorization checker profile changed')
 home=Path(old['output'])/'authorization';outcome=read(c['outcome'])
 require(c['outcome']==binding(home/'outcome.json')and outcome==dict(status='RECONCILIATION_REQUIRED',resource=binding(home/'resource.json'),result=None,automatic_retry=False),'exact stopped authorization required')
 guard=read(outcome['resource']);intent=read(guard['intent'])
 require(guard['returncode']==1 and guard['failure']is None and guard['intent']==binding(home/'intent.json')and guard['log']==binding(home/'step.log')
  and intent==dict(schema_version=SCHEMA,plan=c['plan'],seconds=900,memory_bytes=8589934592,models=False,automatic_retry=False),'preauthorization guard changed')
 command=guard['command'];require(command==[command[0],'-I','-B',old['helper']['path'],'authorize-child','--plan',c['plan']['path'],'--sha256',c['plan']['sha256'],'--intent',guard['intent']['path'],'--intent-sha256',guard['intent']['sha256']]
  and Path(command[0]).is_absolute(),'preauthorization command changed')
 log=Path(guard['log']['path']).read_text()
 require(log.rstrip().endswith("AttributeError: module 'scripts.batch.harbor_instruction_lease_renewal' has no attribute 'prefix'. Did you mean: '_prefix'?")
  and "prefix=env[4].prefix(before,s,w.context,stop='dialogue')"in log,'unsupported authorization failure')
 require({x.name for x in home.iterdir()}=={'intent.json','resource.json','outcome.json','step.log','before-lease.json'}
  and {x.name for x in home.parent.iterdir()}=={'authorization'}
  and read(binding(home/'before-lease.json'))==read(p['tracker'])and p['tracker']==old['tracker'],'authorization made progress or custody changed')
 return old

def inspect(row):
 with context(row,probe=True)as(p,U,D,env,w,s,l,K,V,L):
  require(binding(p['tracker']['path'])==p['tracker']and not Path(p['output']).exists(),'changed custody or consumed mechanical namespace')
  proof=K.inspect_boundary(env[3],env[3].producer,w,s,l,blocked=p['blocked'],verify_pair=pair_verifier(env[3].producer,env[6],generation=2 if 'semantic_context'in p else 1),validator=V,live=True,**takeover_options(L))
  return dict(status='PASS',plan=row,workbook=p['workbook'],tracker=proof['tracker'],author_calls=len(proof['author_calls']),mechanical_report=proof['report'],models_dispatched=0,task_mutations=0)

def authorization_seconds(p):
 value=p.get('authorization_seconds',900)
 require(type(value)is int and (value==900 or ('preserved_context'in p and 900<value<=3300)),
  'bounded preserved-history authorization guard required')
 return value


def authorization_outcome(row,outcome,I,K):
 p=read(row);home=Path(p['output'])/'authorization';value=read(outcome)
 require(outcome==binding(home/'outcome.json')and value==dict(status='COMPLETED_OPERATION',resource=binding(home/'resource.json'),result=binding(home/'result.json'),automatic_retry=False),'exact mechanical owning guard required')
 resource=read(value['resource']);intent=read(resource['intent'])
 require(resource['returncode']==0 and resource['failure']is None and resource['intent']==binding(home/'intent.json')and resource['log']==binding(home/'step.log')
  and intent==dict(schema_version=SCHEMA,plan=row,seconds=authorization_seconds(p),memory_bytes=8589934592,models=False,automatic_retry=False),'mechanical guard failed or differs')
 require(resource['command']==[sys.executable,'-I','-B',p['helper']['path'],'authorize-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],'mechanical child command differs')
 result=read(value['result']);certificate=read(result['checkpoint'])['mechanical_requalification'];proof=K.validate(certificate)
 require(result['status']=='STEP_COMPLETE'and result['next_step']=='dialogue-review'and result['descriptor']==binding(home/'descriptor.json')
  and result['evidence']==[p['blocked'],row,result['checkpoint']]and proof['runtime_profile']==p['runtime_profile']and proof['blocked']==p['blocked'],
  'mechanical completion lost original author boundary')
 I.selected(result['descriptor']);I._prior(value['result'],result['descriptor']);return value['result'],result

def authorize_child(row,intent):
 with context(row)as(p,U,D,env,w,s,l,K,V,L):
  home=Path(p['output'])/'authorization'
  require(sys.argv==[__file__,'authorize-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
   and intent==binding(home/'intent.json')and read(intent)==dict(schema_version=SCHEMA,plan=row,seconds=authorization_seconds(p),memory_bytes=8589934592,models=False,automatic_retry=False),'actual mechanical guard differs')
  U.custody(p,env[6]);W=env[3];P=W.producer;I=env[1];C=env[0];verify=pair_verifier(P,env[6],generation=2 if 'semantic_context'in p else 1)
  from scripts.batch import harbor_model_executor as E,harbor_tracker_lifecycle as life
  def forbidden(*a,**k):raise ValueError('mechanical checkpoint cannot dispatch a model')
  E.execute=E._open=forbidden
  require(binding(p['tracker']['path'])==p['tracker'],'mechanical entry tracker changed')
  keep(home/'before-lease.json',read(p['tracker']))
  def validation(before,current):
   proof=K.inspect_boundary(W,P,w,s,current,blocked=p['blocked'],verify_pair=verify,validator=V,live=True,**takeover_options(L))
   require(proof['tracker']==binding(p['tracker']['path']),'mechanical live validation drifted')
   prefix=completed_prefix(env,before,s,w)
   report=keep(home/'renewal-validation.json',dict(blocked=p['blocked'],author_calls=proof['author_calls'],report=proof['report'],prefix=prefix,models_dispatched=0))
   return [p['blocked'],p['source'],p['runtime_profile'],report]
  if 'completed_takeover'in p:
   validation(read(p['tracker']),l);renewal=p['completed_takeover']['completion']
  else:
   with(w.run/'worker.lock').open('a+b')as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    l,renewal=L.renew(life=life,read=read,record=binding,keep=keep,path=p['tracker']['path'],lease=l,authority_record=row,
    validation=validation,custody=lambda:U.custody(p,env[6]),directory=home/'renewal',retain_sufficient=True)
  keep(home/'after-lease.json',read(binding(p['tracker']['path'])))
  complete=K.authorize(W,P,w,s,l,blocked=p['blocked'],verify_pair=verify,validator=V,directory=home/'mechanical',runtime_profile=p['runtime_profile'],**takeover_options(L))
  old=read(p['descriptor']);certificate=read(complete)['mechanical_requalification']
  descriptor=keep(home/'descriptor.json',dict(old,initial=dict(selection=I.codec.encode(s),lease=C.encode(l)),origin=[*old['origin'],p['descriptor'],p['blocked'],row,renewal,certificate]))
  result=keep(home/'result.json',I._result('STEP_COMPLETE',descriptor,s,l,checkpoint=complete,next_step='dialogue-review',evidence=[p['blocked'],row,complete]))
  I.selected(descriptor);I._prior(result,descriptor)

def install_completed(row,p,U,D,env,K):
 authrow,authp=authorization_selection(row,p)
 outcome=binding(Path(authp['output'])/'authorization/outcome.json');checkpoint,value=authorization_outcome(authrow,outcome,env[1],K)
 certificate=read(value['checkpoint'])['mechanical_requalification'];install_mechanical_projection(K,env[3],certificate)
 original=D.successful
 def successful(bound,observed=None):
  if 'checkpoint_takeover'in p:
   bridge=module('mechanical_checkpoint_takeover_completed',p['checkpoint_takeover']['helper']);q=read(p['checkpoint_takeover']['profile']);done=Path(q['directory'])/'handoff.json'
   if done.exists()and bound==binding(done):return bridge.completed(sys.modules[__name__],p,env[1])[1]
  if bound==outcome:return authorization_outcome(authrow,bound,env[1],K)
  return original(bound,observed)
 D.successful=successful;U.install_retained_commands(row,p,D,env)
 return outcome

def retained_child(row,intent,sidecar):
 require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated owning child required')
 with context(row,invocation=(intent,sidecar))as(p,U,D,env,w,s,l,K,V,L):
  U.custody(p,env[6]);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
  require(actual==intent and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=row,helper=p['helper'],kind=kind,
   intent=intent,original_command=side['original_command'],seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592,automatic_retry=False)
   and Path(sidecar['path'])==Path(intent['path']).parent/'semantic-bootstrap.json','original owning child detached from mechanical checkpoint')
  expected=[p['helper']['path'],'retained-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',sidecar['path'],'--bootstrap-sha256',sidecar['sha256']]
  require(sys.argv==expected,'actual mechanical cold argv differs');install_completed(row,p,U,D,env,K)
  return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)

def run(row):
 with context(row)as(p,U,D,env,w,s,l,K,V,L):
  U.custody(p,env[6]);require(binding(p['tracker']['path'])==p['tracker'],'mechanical entry tracker changed')
  if 'completed_authorization'in p:
   env[3].active(w,s,l);home=Path(p['output']);require(home.is_absolute()and not home.exists()and not home.is_relative_to(env[6]),'completed authorization namespace consumed')
   operations=U.operations_for(D,env[5],home);home.mkdir();outcome=install_completed(row,p,U,D,env,K)
   with ExitStack()as stack:
    if 'checkpoint_takeover'in p:
     authrow,_=authorization_selection(row,p);bridge=module('mechanical_checkpoint_takeover_owning',p['checkpoint_takeover']['helper']);outcome=stack.enter_context(bridge.create_handoff(sys.modules[__name__],p,env[1],env[3],K,authrow))
    job=keep(home/'authoring-job.json',dict(read(p['original_job']),outcome=outcome,operations=str(operations)))
    D.run(job);held=read(binding(operations/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
   return keep(home/'result.json',dict(status=value['status'],workbook=p['workbook'],plan=row,job=job,outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))
  before=read(p['tracker']);require(before['leases']['task_agent']['expires_at']-time.time_ns()>(4300 if 'completed_takeover'in p else authorization_seconds(p)+30)*10**9,'mechanical owning guard lacks lease runway')
  home=Path(p['output']);require(home.is_absolute()and not home.exists()and not home.is_relative_to(env[6]),'mechanical namespace already consumed')
  operations=U.operations_for(D,env[5],home);C=env[0];home.mkdir();directory=home/'authorization';directory.mkdir()
  with D.fleet_slot(p['workbook'])as fleet_fds:
   fd=os.open(C._controls(env[7]),os.O_RDWR|os.O_NOFOLLOW)
   try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);intent=keep(directory/'intent.json',dict(schema_version=SCHEMA,plan=row,seconds=authorization_seconds(p),memory_bytes=8589934592,models=False,automatic_retry=False))
    command=[sys.executable,'-I','-B',p['helper']['path'],'authorize-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
    rc=None;failure=None;metrics={}
    try:
     with(directory/'step.log').open('xb')as log:rc=C._run_in_process_group(command,stdout=log,timeout_seconds=authorization_seconds(p),memory_limit_bytes=8589934592,grace_seconds=3,metrics=metrics,cwd=env[6],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fds))
    except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
    resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),command=command))
    result=binding(directory/'result.json')if rc==0 and failure is None and(directory/'result.json').exists()else None
    keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION'if result else'RECONCILIATION_REQUIRED',resource=resource,result=result,automatic_retry=False))
   finally:os.close(fd)
  require(result is not None,'mechanical guard stopped; no automatic replay')
  outcome=install_completed(row,p,U,D,env,K);job=keep(home/'authoring-job.json',dict(read(p['original_job']),outcome=outcome,operations=str(operations)))
  D.run(job);held=read(binding(operations/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
  return keep(home/'result.json',dict(status=value['status'],workbook=p['workbook'],plan=row,job=job,outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['probe','run','authorize-child','retained-child']);parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--intent');parser.add_argument('--intent-sha256');parser.add_argument('--bootstrap');parser.add_argument('--bootstrap-sha256');args=parser.parse_args();row=dict(path=args.plan,sha256=args.sha256)
 if args.action=='probe':print(json.dumps(inspect(row)))
 elif args.action=='run':print(json.dumps(run(row)))
 elif args.action=='authorize-child':authorize_child(row,dict(path=args.intent,sha256=args.intent_sha256))
 else:retained_child(row,dict(path=args.intent,sha256=args.intent_sha256),dict(path=args.bootstrap,sha256=args.bootstrap_sha256))
