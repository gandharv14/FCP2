"""Generic warning continuation over retained or live same-owner custody.

Original instruction commands, evidence, guards and warning semantics remain
selected by the exact retained profile. Direct paths run original Q once; the
live variant first validates and renews the existing owner without a takeover.
"""
from pathlib import Path
from dataclasses import replace
import argparse,ast,fcntl,hashlib,importlib.util,json,os,shutil,sys,time
from contextlib import nullcontext, contextmanager, ExitStack
SCHEMA='harbor-retained-warning-continuation/v1'
FIELDS={'schema_version','workbook','helper','base_remaining_plan','history_reader','warning_reader','warning_profile','allowance_history','takeover_runtime','takeover_plan','completion','dependency_runtime','dependency_profile','config','snapshot','output','maximum_steps','owner','fence','capacity_policy','source_sha256','failed_guard','stdlib'}
PHASES=('dialogue-review','final-runtime','dialogue-commit','enqueue')
MEMORY=8589934592
DIRECT_SCHEMA='harbor-direct-warning-continuation/v1'
DIRECT_FIELDS={'schema_version','workbook','helper','prepare_helper','dispatcher','snapshot','old_snapshot','old_result','config','takeover_plan','completion','capacity_policy','source_sha256','owner','fence','output','maximum_steps','stdlib'}
def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).absolute();require(p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)),'regular warning artifact required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(type(row) is dict and set(row)=={'path','sha256'} and binding(row['path'])==row,'warning artifact differs')
    return json.loads(Path(row['path']).read_bytes())

def keep(path,value):
    path=Path(path);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
    require(not any(p.is_symlink() for p in (path,*path.parents)),'unsafe warning receipt')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(path)

def module(name,row):
    require(binding(row['path'])==row,'warning module differs')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def context(row):
 p=read(row)
 if p.get('schema_version')==DIRECT_SCHEMA:return direct_context(row,p)
 require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__) and p['maximum_steps']==len(PHASES),'closed generic warning plan required')
 module('warning_stdlib',p['stdlib']).preload_inspect()
 os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','')
 base=read(p['base_remaining_plan']);M=module('retained_warning_prepare',base['prepare_helper'])
 prep,C,I,W,Q,w,selection,controls,recipe,result,oldT=M.context(base['prepare_plan'])
 require(p['snapshot']==base['snapshot']==prep['snapshot'] and C.SECONDS==3300 and C.MEMORY==MEMORY,'original guard/code contract differs')
 config=read(p['config']);retained=read(prep['retained_plan']);original_config=read(retained['config'])
 require(p['config']==base['config'] and config=={**original_config,'code':p['snapshot']} and config['workbook']==p['workbook'],'retained config changed')
 require(C.decode(config['controls'])==controls and w.run==Path(config['run_root'])/'instruction','original worker/controls differ')
 module('author_workbook',base['author_helper']);D=module('retained_warning_dispatcher',base['dispatcher'])
 from scripts import harbor_publish as P
 from scripts.batch import harbor_instruction_lease_renewal as R
 env=(C,I,P,W,R,config,Path(prep['code_root']),controls)
 require(all(Path(m.__file__).resolve().is_relative_to(env[6]) for m in (C,I,P,W,R)), 'original warning module inventory differs')
 require(p['owner']==prep['owner'] and type(p['fence']) is int and p['fence']>0,'selected warning custody differs')
 require(Path(p['output']).parent==Path(prep['home']).parent and Path(p['output'])!=Path(base['output']), 'fresh sibling continuation namespace required')
 # The loader replaces the canonical lifecycle slot. Keep the exact original
 # object registered so the declared rebinder can find its cached consumers.
 sys.modules['_retained_original_warning_lifecycle']=sys.modules['scripts.batch.harbor_tracker_lifecycle']
 H=module('completed_allowance_history',p['allowance_history'])
 _,T,lease,witness=H.load(p['takeover_runtime'],p['takeover_plan'],p['completion'])
 # Preserve original module identities for the explicit shared rebinder.
 sys.modules['_retained_original_warning_takeover']=oldT
 sys.modules['scripts.batch.harbor_lease_takeover']=T
 import scripts.batch as package
 package.harbor_lease_takeover=T
 runtime=module('retained_dependency_runtime',p['dependency_runtime'])
 dependencies=read(p['dependency_profile'])
 require(dependencies['runtime']==p['dependency_runtime'] and dependencies['entrypoint']==p['helper'] and not dependencies['patches'] and not dependencies['symbols'] and not dependencies['guard_modules'],'only declared dependency rebinding required')
 manager=runtime.installed(p['dependency_profile']);manager.__enter__()
 try:
  profile=read(p['warning_profile']);reader=module('selected_warning_history',p['warning_reader']);history=module('original_warning_history',p['history_reader'])
  require(profile['adapter']==p['warning_reader'] and profile['instruction_reader']==p['history_reader'] and profile['takeover']==p['completion'] and profile['takeover_runtime']==p['takeover_plan'],'warning reader selection differs')
  reader.install(p['warning_profile'],I=I,W=W,Q=Q,R=R,T=T,original_history_reader=history)
  side=read(profile['context']);w=replace(w,warning_requalification=profile['context']);selection=I.codec.decode(side['selection'])
  require(side['lease']==__import__('dataclasses').asdict(lease),'warning descriptor lease differs from actual completion')
  # Runtime loading proves history. Each original W operation checks live custody.
  return p,M,prep,D,env,w,selection,lease,recipe,profile,manager
 except BaseException:
  manager.__exit__(*sys.exc_info());raise

def direct_context(row,p):
 """Load ordinary completed history under an explicit prospective code policy.

 No expired-lease eligibility check is reapplied. The selected T validator
 authenticates the completed replacement and all of its predecessor records.
 """
 if 'live_renewal' in p:return live_context(row,p)
 require(DIRECT_FIELDS<=set(p)<=DIRECT_FIELDS|{'normalization_profile','cache_lineage_profile','resume','source_history'} and p['helper']==binding(__file__) and p['maximum_steps']==len(PHASES),'closed direct warning plan required')
 module('warning_stdlib',p['stdlib']).preload_inspect()
 snapshot=read(p['snapshot']);root=Path(snapshot['root'])
 require(root.is_absolute() and root.is_dir(),'selected warning code root required')
 expected={x['relative']:x['sha256'] for x in snapshot['files']}
 actual={str(f.relative_to(root)):binding(f)['sha256'] for f in root.rglob('*') if f.is_file() and '__pycache__' not in f.parts}
 require(actual==expected and len(expected)==len(snapshot['files']),'complete prospective warning inventory changed')
 require('scripts' not in sys.modules,'direct warning requires cold isolated imports')
 sys.path.insert(0,str(root));os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','')
 tp=read(p['takeover_plan'])
 require(tp['schema_version']=='harbor-allowance-takeover-direct/v1' and tp['original_snapshot']==p['old_snapshot'],'ordinary configured takeover required')
 select_direct_history(p,tp)
 import scripts.batch as package
 for key,name in [('lifecycle','harbor_tracker_lifecycle'),('allowance','harbor_continuation_allowance'),('takeover','harbor_lease_takeover')]:
  selected=module('scripts.batch.'+name,tp['shared'][key]);setattr(package,name,selected)
 T=package.harbor_lease_takeover
 from scripts import harbor_campaign as C,harbor_instruction_campaign as I,harbor_publish as P
 from scripts.batch import harbor_instruction_worker as W,harbor_dialogue_warning_requalification as Q,harbor_instruction_lease_renewal as R
 before,after,lease=T.validate_completion(p['completion'])
 stop=read(read(p['completion'])['stopped_owner_proof'])
 require(T._selection(stop['selection'])==T._selection(tp['selection']) and stop['selection']['workbook']==p['workbook'] and lease.owner==p['owner'],'completed warning takeover selection differs')
 old=read(p['old_result']);desc=read(old['descriptor']);config=read(p['config']);original_config=read(tp['selection']['config'])
 require(p['old_result']==tp['selection']['checkpoint'] and old['status']=='DIALOGUE_REPAIR_REQUIRED' and old['next_step']=='dialogue-review','exact completed rejected review required')
 require(config=={**original_config,'code':p['snapshot']} and config['workbook']==p['workbook'],'only prospective code may change in warning config')
 for evidence in desc['origin']:read_or_check(evidence)
 w=replace(I.worker_decode(desc['worker']),root=root);selection=I.codec.decode(old['selection'])
 require(selection.content.workbook==p['workbook'] and selection.content.bindings['original_raw_source']['sha256']==p['source_sha256'] and w.run==Path(config['run_root'])/'instruction','warning source or worker differs')
 select_normalization_profile(p,selection)
 controls=C.decode(desc['controls']);recipe=C.decode(desc['dependencies']);C._controls(controls);C._campaign(w.campaign)
 require(T._controls_match(T._selection(tp['selection']),desc['controls'],original_config['controls']),'warning controls lack exact retained migration')
 M=module('ordinary_warning_prepare',p['prepare_helper']);M.no_models()
 D=module('ordinary_warning_dispatcher',p['dispatcher']);env=(C,I,P,W,R,config,root,controls)
 require(all(Path(m.__file__).resolve().is_relative_to(root) for m in (C,I,P,W,R,Q)) and C.SECONDS==3300 and C.MEMORY==MEMORY,'owning warning code and guards changed')
 manager=nullcontext();prepared=Path(p['output'])/'preparation.json';profile=None;preparation_plan=row
 if p.get('resume'):
  resumed=p['resume']
  require(set(resumed)=={'prior_plan','failed_outcome','runtime','runtime_profile'},'closed completed-warning resume required')
  prior=read(resumed['prior_plan'])
  require('resume' not in prior and prior=={**{k:v for k,v in p.items()if k!='resume'},
   'helper':prior['helper'],'output':prior['output'],'fence':prior['fence']}
   and p['fence']>prior['fence'] and p['output']!=prior['output'],
   'resume may change only declared runtime, process fence and output')
  read_or_check(prior['helper'])
  failed=read(resumed['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent'])
  require(resumed['failed_outcome']['path']==str(Path(prior['output'])/'step-00/outcome.json')
   and failed['status']=='RECONCILIATION_REQUIRED' and guard['returncode']==1
   and guard['failure']is None and intent['models']is False and intent['action']=='step'
   and intent['arguments']['checkpoint']==binding(Path(prior['output'])/'handoff/result.json'),
   'only failed first phase before owning intent may resume')
  read_or_check(guard['log'])
  rp=read(resumed['runtime_profile'])
  require(rp['entrypoint']==p['helper'] and rp['runtime']==resumed['runtime']
   and not rp['patches'] and not rp['dependencies'] and not rp.get('factories')
   and not rp['guard_modules'] and not rp['child_plans'] and not rp['journal_roots']
   and len(rp['symbols'])==1 and rp['symbols'][0]['names']==['historical_lease']
   and rp['symbols'][0]['module_name']==Q.__name__
   and rp['symbols'][0]['original']==binding(Q.__file__),
   'warning resume permits only declared historical lease reader correction')
  runtime=module('direct_warning_resume_runtime',resumed['runtime'])
  manager=runtime.installed(resumed['runtime_profile']);manager.__enter__()
  prepared=Path(prior['output'])/'preparation.json';preparation_plan=resumed['prior_plan']
  require(prepared.is_file(),'actual completed warning preparation required')
 if prepared.exists():
  receipt=read(binding(prepared));require(receipt['plan']==preparation_plan and receipt['takeover']==p['completion'],'warning preparation belongs to another operation')
  permit=receipt['permit'];v=read(permit);w=replace(w,warning_requalification=permit);selection=I.codec.decode(v['selection'])
  require(v['lease']==__import__('dataclasses').asdict(lease) and v['lease_takeover']==p['completion'] and v['blocked']==old['checkpoint'],'ordinary warning permit detached from actual history')
  profile=dict(context=permit,original_permit=permit,takeover=p['completion'])
  if p.get('resume'):
   first_outcome=Path(p['output'])/'step-00/outcome.json'
   if first_outcome.exists():
    _,completed=D.successful(binding(first_outcome),env)
    require(completed['status']=='STEP_COMPLETE' and completed['next_step']=='final-runtime',
     'resumed first phase did not complete')
   else:
    require(not (W.step_directory(w,'dialogue-review')/'intent.json').exists(),
     'owning review already began; explicit later-boundary reconciliation required')
 return p,M,dict(old=old,Q=Q,old_snapshot=p['old_snapshot']),D,env,w,selection,lease,recipe,profile,manager

def live_lease(row,p,C,old):
    """The original renewal receipt is history; current custody remains W.active."""
    retained=Path(p['output'])/'live-renewal/retained-live.json'
    if retained.exists():
        require('completed_semantic'in p['live_renewal'],'retained live warning requires completed semantic history')
        require(not(Path(p['output'])/'live-renewal/complete.json').exists(),'live warning has conflicting renewal receipts')
        value=read(binding(retained));previous=__import__('dataclasses').asdict(C.decode(old['lease']));selected=live_review(p)
        require(set(value)=={'schema_version','authority','lease','tracker','validated_evidence','minimum_remaining_ns','observed_at_ns','original_expired_replacements','lease_operations','task_guards_changed','semantic_budget_reset','automatic_retry'}
            and value['schema_version']=='fleet-retained-live-continuation-lease/v1'and value['authority']==row
            and value['lease']==previous and value['tracker']==selected['tracker']
            and value['minimum_remaining_ns']==7200*10**9 and value['lease_operations']==0
            and value['task_guards_changed']is False and value['semantic_budget_reset']is False and value['automatic_retry']is False,
            'retained live warning custody changed')
        for evidence in value['validated_evidence']:read_or_check(evidence)
        # The archived before tracker is in original semantic completion evidence.
        tracker=next((read(r)for r in value['validated_evidence']if r['sha256']==previous['sha256']),None)
        require(tracker is not None and tracker['leases']['task_agent']['expires_at']-value['observed_at_ns']>=value['minimum_remaining_ns']
            and tracker['leases']['task_agent']['heartbeat_at']<=value['observed_at_ns'],
            'retained warning runway lacks authenticated before bytes')
        return C.decode(old['lease'])
    path=Path(p['output'])/'live-renewal/complete.json'
    if not path.exists():return C.decode(old['lease'])
    v=read(binding(path));intent=read(v['intent']);selected=live_review(p)
    previous=__import__('dataclasses').asdict(C.decode(old['lease']))
    require(v['schema_version']=='fleet-validated-continuation-lease/v1'
        and v['duration_ns']==7200*10**9 and v['previous']==previous
        and v['task_guards_changed'] is False and v['semantic_budget_reset'] is False
        and v['automatic_retry'] is False and intent['authority']==row
        and intent['previous']==previous and intent['tracker']==selected['tracker']
        and intent['duration_ns']==v['duration_ns']
        and v['original_expired_replacements']==intent['original_expired_replacements'],
        'warning live renewal detached from original request or authority')
    lease=C.bootstrap.Lease(**v['lease'])
    require(lease.owner==previous['owner'] and lease.token==previous['token']
        and lease.revision==previous['revision']+1 and lease.sha256==v['tracker']['sha256'],
        'warning live renewal changed native owner or revision')
    return lease

def live_renewal_receipt(home):
    """Select the one actual scheduler receipt already checked by live_lease."""
    directory=Path(home)/'live-renewal'
    found=[directory/name for name in ('complete.json','retained-live.json')if (directory/name).is_file()]
    require(len(found)==1,'exactly one completed live scheduler receipt required')
    result=binding(found[0]);value=read(result)
    require(value['schema_version']==('fleet-validated-continuation-lease/v1'if found[0].name=='complete.json'
        else 'fleet-retained-live-continuation-lease/v1'),'live scheduler receipt type changed')
    return result


def live_resume(p):
    """Validate a stopped first child while retaining its completed renewal/Q."""
    resumed=p['resume']
    takeover=set(resumed)=={'prior_plan','failed_outcome','takeover_history'}
    require(takeover or set(resumed)=={'prior_plan','failed_outcome','runtime','runtime_profile'},
            'closed live warning resume required')
    prior=read(resumed['prior_plan'])
    expected={**{k:v for k,v in p.items() if k!='resume'},
            'helper':prior['helper'],'output':prior['output'],'owner':prior['owner'],'fence':prior['fence']}
    if takeover:
        expected['takeover_history']=prior['takeover_history']
        require(resumed['takeover_history']==p['takeover_history'],'resume history selection differs')
        takeover_resume_delta(prior['takeover_history'],p['takeover_history'])
    require('resume' not in prior and 'live_renewal' in prior
        and prior==expected
        and p['fence']>prior['fence'] and p['output']!=prior['output'],
        'live resume may change only helper, central custody and output')
    read_or_check(prior['helper'])
    home=Path(prior['output']);failed=read(resumed['failed_outcome'])
    guard=read(failed['resource']);intent=read(guard['intent'])
    require(resumed['failed_outcome']['path']==str(home/'step-00/outcome.json')
        and failed['status']=='RECONCILIATION_REQUIRED' and guard['returncode']==1
        and guard['failure'] is None and intent['models'] is False and intent['action']=='step'
        and intent['arguments']['checkpoint']==binding(home/'handoff/result.json'),
        'only stopped first warning child may resume')
    read_or_check(guard['log'])
    ending=('ValueError: takeover history may validate only original historical worker\n' if takeover else
        'scripts.batch.harbor_admission.AdmissionError: ordinary warning lacks authenticated renewal history\n')
    require(Path(guard['log']['path']).read_text().endswith(ending),
        'different live warning failure requires its own reconciliation')
    for name in ('preparation.json','live-renewal/complete.json','renew-resource.json'):
        read(binding(home/name))
    return prior


def takeover_resume_delta(old,selected):
    require(set(old)==set(selected)=={'helper','profile'},'closed takeover history delta required')
    before=read(old['profile']);after=read(selected['profile'])
    require(after==dict(before,helper=selected['helper']),'takeover authority changed during resume')
    read_or_check(old['helper']);read_or_check(selected['helper'])
    anchor="        require(selected.run==worker.run and selected.root==root and selected.warning_requalification is None,\n"
    extra=("        if selected==worker and selected.root!=root and selected.warning_requalification is not None and historical_code_inventory is None:\n"
           "            return original(selected,before,seen,historical_code_inventory=None)\n")
    original=Path(old['helper']['path']).read_text();current=Path(selected['helper']['path']).read_text()
    require(original.count(anchor)==1 and ast.dump(ast.parse(original.replace(anchor,extra+anchor)),include_attributes=False)
        ==ast.dump(ast.parse(current),include_attributes=False),'only exact current warning original-validator routing may change')


def live_resume_runtime(p,Q):
    resumed=p['resume']
    if 'takeover_history'in resumed:
        takeover_resume_delta(read(resumed['prior_plan'])['takeover_history'],p['takeover_history'])
        return nullcontext()
    rp=read(resumed['runtime_profile'])
    require(rp['entrypoint']==p['helper'] and rp['runtime']==resumed['runtime']
        and not rp['patches'] and not rp['dependencies'] and not rp.get('factories')
        and not rp['guard_modules'] and not rp['child_plans'] and not rp['journal_roots']
        and len(rp['symbols'])==1,'only one historical reader correction permitted')
    symbol=rp['symbols'][0]
    require(symbol['module_name']==Q.__name__ and symbol['original']==binding(Q.__file__)
        and symbol['names']==['historical_lease'],'foreign warning history reader')
    old=Path(Q.__file__).read_text();read_or_check(symbol['selected'])
    anchor='        proof=first.validate_history(worker,before,p)\n'
    extra=("        if worker.lease_renewal is None:\n"
           "            require('lease_renewal' not in proof and 'renewal_history' not in proof,\n"
           "                    'ordinary warning invented renewal history')\n"
           "            return proof\n")
    require(old.count(anchor)==1 and extra not in old
        and ast.dump(ast.parse(old.replace(anchor,anchor+extra)),include_attributes=False)
            ==ast.dump(ast.parse(Path(symbol['selected']['path']).read_text()),include_attributes=False),
        'only authenticated ordinary history without renewal may be added')
    runtime=module('live_warning_resume_runtime',resumed['runtime'])
    manager=runtime.installed(resumed['runtime_profile']);manager.__enter__();return manager


def original_review_guard(prior):
    """Reject outer worker outcomes before opening a continuation namespace.

    This admission check does not replace the original renewal child's full
    completed_failure validation; it catches detached or uncertain guards early.
    """
    out=read(prior['outcome']);home=Path(prior['outcome']['path']).parent
    require(out.get('status')=='COMPLETED_OPERATION' and out.get('automatic_retry',False)is False
        and out.get('result')==prior['result'] and out.get('resource')==binding(home/'resource.json'),
        'original completed review guard required before continuation')
    resource=read(out['resource'])
    require(resource.get('returncode')==0 and resource.get('failure')is None
        and resource.get('intent')==binding(home/'intent.json') and resource.get('log')==binding(home/'step.log'),
        'original review guard is uncertain or detached')
    intent=read(resource['intent']);result=read(prior['result'])
    require(intent.get('action')=='step' and intent.get('arguments',{}).get('descriptor')==prior['descriptor']
        and result.get('descriptor')==prior['descriptor'] and result.get('checkpoint')==prior['blocked']
        and result.get('status')=='DIALOGUE_REPAIR_REQUIRED' and result.get('next_step')=='dialogue-review',
        'original review boundary differs')
    return out


def live_context(row,p):
    require(set(p)-{'normalization_profile','cache_lineage_profile','resume','takeover_history','native_lifecycle'}==DIRECT_FIELDS|{'live_renewal'} and p['helper']==binding(__file__)
        and p['maximum_steps']==len(PHASES) and p['takeover_plan'] is None and p['completion'] is None,
        'closed live warning continuation required')
    custody=live_resume(p) if p.get('resume') else p
    authority=p['resume']['prior_plan'] if p.get('resume') else row
    live=p['live_renewal'];require(set(live)in ({'context','plan'},{'context','plan','completed_semantic'}),'closed original renewal context required')
    prior=live_review(custody);read_or_check(live['context'])
    original_review_guard(prior)
    select_live_history(custody)
    require(prior['helper']==live['context'] and prior['workbook']==p['workbook']
        and prior['result']==p['old_result'] and prior['source']['sha256']==p['source_sha256']
        and prior['owner']==custody['owner'] and prior['fence']==custody['fence'],'live warning differs from original failure selection')
    module('warning_stdlib',p['stdlib']).preload_inspect()
    snapshot=read(p['snapshot']);root=Path(snapshot['root']);old_snapshot=read(p['old_snapshot'])
    for snap in (snapshot,old_snapshot):
        base=Path(snap['root']);expected={r['relative']:r['sha256'] for r in snap['files']}
        actual={str(f.relative_to(base)):binding(f)['sha256'] for f in base.rglob('*') if f.is_file() and '__pycache__' not in f.parts}
        require(actual==expected and len(expected)==len(snap['files']),'complete warning code inventory changed')
    require('scripts' not in sys.modules,'live warning requires cold isolated imports')
    sys.path.insert(0,str(root));os.environ['PATH']=str(Path(sys.executable).absolute().parent)+os.pathsep+os.environ.get('PATH','')
    manager=ExitStack();native_lifecycle=manager.enter_context(native_lifecycle_scope(p))
    try:
        from scripts import harbor_campaign as C,harbor_instruction_campaign as I,harbor_publish as P
        from scripts.batch import harbor_instruction_worker as W,harbor_dialogue_warning_requalification as Q,harbor_instruction_lease_renewal as R
        old=read(p['old_result']);descriptor=read(old['descriptor']);config=read(p['config']);old_job=read(prior['original_job']);original_config=read(old_job['config'])
        require(original_config['code']==p['old_snapshot'] and config=={**original_config,'code':p['snapshot']}
            and old['status']=='DIALOGUE_REPAIR_REQUIRED' and old['next_step']=='dialogue-review'
            and descriptor['controls']==original_config['controls'],'live warning config or completed review changed')
        for evidence in descriptor['origin']:read_or_check(evidence)
        w=replace(I.worker_decode(descriptor['worker']),root=root);selection=I.codec.decode(old['selection'])
        require(selection.content.workbook==p['workbook'] and selection.content.bindings['original_raw_source']['sha256']==p['source_sha256']
            and w.run==Path(config['run_root'])/'instruction','live warning source or worker changed')
        select_normalization_profile(p,selection)
        Q.changes(p['old_snapshot'],root)
        controls=C.decode(descriptor['controls']);recipe=C.decode(descriptor['dependencies']);C._controls(controls);C._campaign(w.campaign)
        M=module('ordinary_warning_prepare',p['prepare_helper']);M.no_models();D=module('ordinary_warning_dispatcher',p['dispatcher'])
        env=(C,I,P,W,R,config,root,controls);require(C.SECONDS==3300 and C.MEMORY==MEMORY,'original warning guards changed')
        lease=live_lease(authority,custody,C,old);profile=None;prepared=Path(custody['output'])/'preparation.json'
        if prepared.exists():
            receipt=read(binding(prepared));require(receipt['plan']==authority and receipt['takeover'] is None,'live warning preparation detached')
            permit=receipt['permit'];v=read(permit);w=replace(w,warning_requalification=permit);selection=I.codec.decode(v['selection'])
            require(v['lease']==__import__('dataclasses').asdict(lease) and v['lease_takeover'] is None and v['blocked']==old['checkpoint'],
                'live warning permit changed native custody or consumed review')
            profile=dict(context=permit,original_permit=permit,takeover=None)
        if native_lifecycle is not None:
            after,issued=native_lifecycle;current=read(binding(w.context.tracker_root/(p['workbook']+'.json')))
            require((issued.owner,issued.token)==(lease.owner,lease.token)
                and current['handoffs'][:len(after['handoffs'])]==after['handoffs']
                and current['revision']>=after['revision']
                and current['bindings']['original_raw_source']==after['bindings']['original_raw_source'],
                'current warning custody detached from actual takeover lifecycle')
        if 'takeover_history'in p:
            selected=p['takeover_history']
            require(set(selected)=={'helper','profile'}and 'completed_semantic'in live,'closed completed semantic takeover history required')
            H=module('warning_retained_takeover_history',selected['helper'])
            require(read(selected['profile'])['helper']==selected['helper'],'takeover history helper differs')
            manager.enter_context(H.installed(selected['profile'],R,W,w,p['old_snapshot']))
        if p.get('resume'):
            require(profile is not None,'completed live warning permit required')
            first=Path(p['output'])/'step-00/outcome.json'
            if first.exists():
                _,completed=D.successful(binding(first),env)
                require(completed['status']=='STEP_COMPLETE' and completed['next_step']=='final-runtime','resumed warning first phase incomplete')
            else:
                require(not (W.step_directory(w,'dialogue-review')/'intent.json').exists(),'warning owning review already began')
            nested=live_resume_runtime(p,Q);manager.callback(nested.__exit__,None,None,None)
        return p,M,dict(old=old,Q=Q,old_snapshot=p['old_snapshot']),D,env,w,selection,lease,recipe,profile,manager
    except BaseException:
        manager.__exit__(*sys.exc_info());raise



@contextmanager
def native_lifecycle_scope(p):
    """Current completed takeover readers do not redefine past runtime code."""
    selected=p.get('native_lifecycle')
    if selected is None:
        yield None;return
    require(set(selected)=={'profile','completion'}and 'completed_semantic'in p['live_renewal'],
            'completed semantic native lifecycle selection required')
    profile=read(selected['profile']);require(profile['schema_version']=='harbor-allowance-takeover-direct/v1'
        and profile['original_snapshot']==p['old_snapshot']
        and set(profile['shared'])=={'lifecycle','allowance','takeover','stdlib'}
        and profile['selection']['workbook']==p['workbook']
        and profile['selection']['allowance']==profile['allowance'], 'native lifecycle profile detached')
    import scripts.batch as package
    previous=[]
    try:
        for key,name in [('lifecycle','harbor_tracker_lifecycle'),('allowance','harbor_continuation_allowance'),('takeover','harbor_lease_takeover')]:
            full='scripts.batch.'+name;previous.append((full,name,sys.modules.get(full),getattr(package,name,None)))
            got=module(full,profile['shared'][key]);setattr(package,name,got)
        T=package.harbor_lease_takeover
        before,after,lease=T.validate_completion(selected['completion'])
        completion=read(selected['completion']);stop=read(completion['stopped_owner_proof'])
        require(T._selection(stop['selection'])==T._selection(profile['selection'])
            and lease.owner==profile['selection']['owner']
            and before['workbook_id']==after['workbook_id']==p['workbook']
            and before['bindings']['original_raw_source']==after['bindings']['original_raw_source']
            and after['bindings']['original_raw_source']['sha256']==p['source_sha256'],
            'native lifecycle lost completed takeover source or ownership')
        yield after,lease
    finally:
        for full,name,old,attribute in reversed(previous):
            if old is None:sys.modules.pop(full,None)
            else:sys.modules[full]=old
            if attribute is None:
                if hasattr(package,name):delattr(package,name)
            else:setattr(package,name,attribute)


def select_normalization_profile(p,selection):
    if p.get('cache_lineage_profile') is not None:
        profile=read(p['cache_lineage_profile'])
        require(profile['original_snapshot']==p['old_snapshot'] and profile['current_snapshot']==p['snapshot']
            and profile['original_sha256']==p['source_sha256'],'cache lineage profile detached from selected warning source/code')
        os.environ['HARBOR_SOURCE_CACHE_HISTORY']=json.dumps(p['cache_lineage_profile'],sort_keys=True)
    if p.get('normalization_profile')is None:return
    normal=read(p['normalization_profile'])
    require(normal['prepared']==selection.content.normalization_prepared
        and normal['snapshot']==p['old_snapshot'],'normalization profile detached from original reviewed producer')
    os.environ['HARBOR_FLEET_NORMALIZATION_PROFILE']=json.dumps(p['normalization_profile'],sort_keys=True)

def select_direct_history(p,tp):
    """Select the completed source's original history after a lease takeover."""
    if 'source_history' not in p:return
    prior=read(p['source_history']);selected=tp['selection']
    require(prior['workbook']==p['workbook']==selected['workbook']
        and prior['result']==p['old_result']==selected['checkpoint']
        and prior['descriptor']==read(p['old_result'])['descriptor']
        and prior['source']['sha256']==p['source_sha256'],
        'direct warning history detached from completed takeover')
    select_live_history({**p,'live_renewal':{'plan':p['source_history']}})


def select_live_history(p):
    """Carry the exact original fresh history into every continuation child.

    This selects evidence only. The original admission projection still validates
    its complete inventory and requires the current exclusive central claim.
    """
    prior=live_review(p)if 'completed_semantic'in p['live_renewal']else read(p['live_renewal']['plan']);fresh=read(prior['fresh_plan'])
    require(fresh['workbook']==prior['workbook']==p['workbook'],
        'warning history workbook differs')
    legacy=fresh.get('legacy_research_history')
    if legacy is None:return
    value=read(legacy)
    require(value['workbook']==p['workbook'] and value['original']==prior['source']
        and value['original']['sha256']==p['source_sha256'],
        'warning history detached from original source')
    require(not any(os.environ.get(k) for k in (
        'HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH',
        'HARBOR_FLEET_SOURCE_HISTORY_PATH')), 'foreign warning history projection')
    for key,value in (('HARBOR_FLEET_LEGACY_RESEARCH_PATH',legacy['path']),
                      ('HARBOR_FLEET_LEGACY_RESEARCH_SHA256',legacy['sha256'])):
        require(os.environ.get(key) in (None,value),'warning legacy history changed')
    os.environ['HARBOR_FLEET_LEGACY_RESEARCH_PATH']=legacy['path']
    os.environ['HARBOR_FLEET_LEGACY_RESEARCH_SHA256']=legacy['sha256']

def live_renew_child(row):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated live renewal child required')
    p=read(row);require(p['helper']==binding(__file__) and p['completion'] is None and p['takeover_plan'] is None,'live renewal cannot replace expired ownership')
    select_live_history(p)
    if 'completed_semantic'in p['live_renewal']:return completed_semantic_renew(row,p)
    live=p['live_renewal'];original=module('live_warning_original_context',live['context'])
    prior,fresh,job,D,env,w,selection,lease,recipe,S,L=original.context(live['plan'])
    require(prior['result']==p['old_result'] and prior['owner']==p['owner'] and prior['fence']==p['fence']
        and prior['workbook']==p['workbook'] and prior['source']['sha256']==p['source_sha256'],'original renewal context differs')
    C,I=env[:2];custody_env=(*env[:6],Path(read(p['snapshot'])['root']),env[7])
    def validate(before,old):
        proof=S._failure(w,selection,old,prior['blocked']);require(proof[0]==before,'original completed review tracker changed');return proof[3]
    with(w.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _,completion=L.renew(life=S.life,read=I.read,record=binding,keep=keep,path=prior['tracker']['path'],lease=lease,
            authority_record=row,validation=validate,custody=lambda:active(p,custody_env),directory=Path(p['output'])/'live-renewal')
    return dict(status='LIVE_WARNING_LEASE_RENEWED',completion=completion,models_dispatched=0,automatic_retry=False)

def live_renew_guard(row,p,C,root,out):
    command=[sys.executable,'-I','-B',p['helper']['path'],'live-renew-child','--plan',row['path'],'--sha256',row['sha256']]
    metrics={};rc=None;failure=None;start=time.monotonic()
    try:
        with(out/'renew.log').open('xb')as log:
            rc=C._run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=MEMORY,metrics=metrics,cwd=root,env=os.environ.copy())
    except BaseException as exc:failure=type(exc).__name__
    guard=keep(out/'renew-resource.json',dict(plan=row,command=command,returncode=rc,failure=failure,seconds=3300,memory_bytes=MEMORY,elapsed_seconds=time.monotonic()-start,metrics=metrics,log=binding(out/'renew.log'),automatic_retry=False))
    require(rc==0 and failure is None,'live renewal requires reconciliation; never replay')
    return guard


def read_or_check(row):
 require(binding(row['path'])==row,'retained warning origin changed')

def prepare_direct(row,p,M,prep,env,w,selection,lease,out):
 C,I=env[:2]
 selection,scope=M.scope_context(selection,out/'scope-equivalence',root=w.root)
 w,selected,current,permit=prep['Q'].authorize(w,selection,lease,blocked=prep['old']['checkpoint'],old_snapshot=prep['old_snapshot'],directory=out/'warning',takeover=p['completion'])
 require(selected==selection and current==lease and read(permit)['model_calls']==0 and read(permit)['semantic_budget_reset'] is False,'warning changed content or consumed history')
 keep(out/'preparation.json',dict(plan=row,takeover=p['completion'],permit=permit,scope=scope,models_dispatched=0))
 return w,selection,dict(context=permit,original_permit=permit,takeover=p['completion'])

def active(p,env):
 from scripts.fleet.coordinator import Client
 require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']and int(os.environ.get('HARBOR_FLEET_FENCE','0'))==p['fence'],'selected central warning custody required')
 c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);s=c.call('status');module('selectedcapacity0190',p['capacity_policy']).validate_occupancy(s)
 rows=[x for x in s['workbooks']if x['workbook']==p['workbook']];require(len(rows)==1,'unique warning owner required');v=rows[0]
 require(v['state']=='active'and v['owner']==p['owner']and v['fence']==p['fence']and v['version']==env[6].name and v['source_sha256']==p['source_sha256']and v['expires']>time.time(),'active warning identity differs')
 c.call('heartbeat',workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)

def preparation_active(prep,W,w,selection,lease):
    selected=getattr(prep['Q'],'preparation_active',None)
    if selected is None:return W.active(w,selection,lease)
    return selected(w,selection,lease,prep['old_snapshot'])

def create_output(path,root):
    """Create a fresh continuation namespace even for a first continuation."""
    out=Path(path)
    require(out.is_absolute() and not any(p.is_symlink() for p in (out,*out.parents))
        and not out.exists() and shutil.disk_usage(root).free>=12*1024**3,
        'fresh retained output and original disk reserve required')
    out.parent.mkdir(parents=True,exist_ok=True)
    out.mkdir()
    return out

def run(row):
 p,M,prep,D,env,w,selection,lease,recipe,profile,manager=context(row);C,I,P,W,R,config,root,controls=env
 try:
  require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot execute retained warning')
  active(p,env)
  _,before,_=(preparation_active(prep,W,w,selection,lease) if p['schema_version']==DIRECT_SCHEMA else W.active(w,selection,lease));require(before['leases']['task_agent']['expires_at']-time.time_ns()>(60 if 'live_renewal' in p and not p.get('resume') else 3450)*10**9,'native lease runway insufficient before first write')
  out=create_output(p['output'],root)
  intent=keep(out/'intent.json',dict(plan=row,models=False,Q_authorization_replayed=False,T_replayed=False,automatic_retry=False))
  if 'live_renewal' in p and not p.get('resume'):
   live_renew_guard(row,p,C,root,out)
   lease=live_lease(row,p,C,read(p['old_result']))
   require(preparation_active(prep,W,w,selection,lease)[1]['leases']['task_agent']['expires_at']-time.time_ns()>3450*10**9,'renewed warning runway insufficient')
  with D.fleet_slot(p['workbook'])as lock_fds:
   if p['schema_version']==DIRECT_SCHEMA:
    if p.get('resume'):
     require(profile is not None,'completed warning permit missing')
     keep(out/'preparation.json',dict(plan=row,takeover=p['completion'],permit=profile['context'],
      retained_preparation=binding(Path(read(p['resume']['prior_plan'])['output'])/'preparation.json'),models_dispatched=0))
    else:w,selection,profile=prepare_direct(row,p,M,prep,env,w,selection,lease,out)
    origin=[r for r in [row,intent,p['old_result'],p['snapshot'],profile['context'],profile['takeover'],selection.content.dialogue_scope_receipt] if r is not None]
    if 'live_renewal' in p:
     renewal_home=Path(read(p['resume']['prior_plan'])['output']) if p.get('resume') else out
     origin.append(live_renewal_receipt(renewal_home))
    if p.get('resume'):
     origin.extend([p['resume']['prior_plan'],p['resume']['failed_outcome']])
     if 'takeover_history'in p['resume']:origin.extend(p['takeover_history'].values())
     else:origin.append(p['resume']['runtime_profile'])
   else:origin=[row,intent,p['warning_profile'],profile['context'],profile['original_permit'],profile['takeover'],p['failed_guard'],selection.content.dialogue_scope_receipt]
   handoff=out/'handoff';handoff.mkdir()
   checkpoint=M.handoff(C,I,w,selection,lease,controls,recipe,profile['context'],origin,handoff)
   value=read(checkpoint);I._prior(checkpoint,value['descriptor']);install_child_bootstrap(row,C,I);outcomes=[]
   for index,phase in enumerate(PHASES):
    require(value['next_step']==phase,'retained warning stage skipped or repeated');active(p,env)
    outcome=I.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,out/('step-%02d'%index),models=False,commands=True,containers=True,guardian_lock_fds=lock_fds)
    checkpoint,value=D.successful(outcome,env);outcomes.append(outcome)
    require(value['status']==('PUBLICATION_READY'if phase=='enqueue'else'STEP_COMPLETE'),'retained warning requires reconciliation')
   D.publication_request(env,value)
  return keep(out/'result.json',dict(schema_version=SCHEMA,status='PUBLICATION_READY',workbook=p['workbook'],plan=row,intent=intent,config=p['config'],takeover_profile=p.get('warning_profile',p['takeover_plan']),outcomes=outcomes,outcome=outcomes[-1],checkpoint=checkpoint,models_dispatched=0,Q_authorization_replayed=False,T_replayed=False,publication_performed=False,automatic_retry=False))
 finally:
  manager.__exit__(*sys.exc_info())

def probe(row):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','read-only retained warning probe required')
 p,M,prep,D,env,w,selection,lease,recipe,profile,manager=context(row)
 try:
  if p['schema_version']==DIRECT_SCHEMA:preparation_active(prep,env[3],w,selection,lease)
  return dict(status='RETAINED_WARNING_CONTEXT_VALIDATED',plan=row,profile=p.get('warning_profile',p['takeover_plan']),models_dispatched=0,owning_mutations=0)
 
 finally:
  manager.__exit__(*sys.exc_info())

def validate_bootstrap(sidecar,row,intent,original):
    v=read(sidecar);p=read(row)
    require(v==dict(schema_version='harbor-retained-warning-child-bootstrap/v1',plan=row,helper=p['helper'],
                    intent=intent,original_command=original,models=False,automatic_retry=False),
            'warning child detached from original guarded intent')
    require(Path(sidecar['path'])==Path(intent['path']).parent/'warning-bootstrap.json','foreign warning bootstrap directory')

def install_child_bootstrap(row,C,I):
    original_run=C._run_in_process_group;p=read(row)
    def guarded(command,*args,**kwargs):
        require(len(command)==6 and command[1:4]==['-B',str(Path(I.__file__).resolve()),'_child'],
                'warning bootstrap accepts only original instruction child')
        intent=dict(path=command[4],sha256=command[5]);v=read(intent)
        require(v['schema_version']==I.SCHEMA and v['action']=='step' and v['models'] is False
                and v['code']==binding(I.__file__) and v['base_code']==binding(C.__file__),
                'warning child cannot dispatch models or change owning code')
        sidecar=keep(Path(intent['path']).parent/'warning-bootstrap.json',dict(
            schema_version='harbor-retained-warning-child-bootstrap/v1',plan=row,helper=p['helper'],
            intent=intent,original_command=command,models=False,automatic_retry=False))
        replacement=[command[0],'-I','-B',p['helper']['path'],'instruction-child','--plan',row['path'],
                     '--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],
                     '--bootstrap',sidecar['path'],'--bootstrap-sha256',sidecar['sha256']]
        return original_run(replacement,*args,**kwargs)
    C._run_in_process_group=guarded

def instruction_child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated warning child required')
    p,M,prep,D,env,w,selection,lease,recipe,profile,manager=context(row);C,I=env[:2]
    try:
        original=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']]
        validate_bootstrap(sidecar,row,intent,original)
        value=read(intent)
        require(value['models'] is False and value['action']=='step','warning models forbidden')
        # context() blocks every physical executor entry before the real child runs.
        return I.child(intent)
    finally:
        manager.__exit__(*sys.exc_info())

def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','instruction-child','live-renew-child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True)
    for n in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):a.add_argument('--'+n)
    v=a.parse_args();row=dict(path=str(Path(v.plan).absolute()),sha256=v.sha256)
    if v.action=='live-renew-child':result=live_renew_child(row)
    elif v.action=='instruction-child':result=instruction_child(row,dict(path=v.intent,sha256=v.intent_sha256),dict(path=v.bootstrap,sha256=v.bootstrap_sha256))
    else:result=(run if v.action=='run' else probe)(row)
    print(json.dumps(result))


def live_review(p):
    """Project the actual later review without changing any original artifact."""
    selected=p['live_renewal'];old=read(selected['plan'])
    if 'completed_semantic'not in selected:return old
    review=selected['completed_semantic']
    require(set(selected)=={'context','plan','completed_semantic'}and set(review)-{'runtime_profile'}=={'outcome','result','descriptor','blocked','tracker','renewal_module'}
        and old['helper']==selected['context']and old['workbook']==p['workbook']
        and old['source']['sha256']==p['source_sha256']and p['owner']==old['owner']
        and p['fence']==old['fence']+1 and p['old_result']==review['result'],
        'completed semantic warning changed original authority or custody')
    read_or_check(review['renewal_module'])
    prior=dict(old,**{k:v for k,v in review.items()if k not in {'renewal_module','runtime_profile'}},owner=p['owner'],fence=p['fence'])
    original_review_guard(prior)
    require(read(review['result'])['checkpoint']==review['blocked']and review['tracker']['path']==old['tracker']['path'],
        'second semantic rejection or tracker path changed')
    return prior


def semantic_context_runtime(p):
    selected=p['live_renewal']['completed_semantic']
    if 'runtime_profile'not in selected:return nullcontext()
    row=selected['runtime_profile'];profile=read(row);old=read(p['live_renewal']['plan'])
    require(set(profile)=={'schema_version','runtime','entrypoint','read_call','patches','guard_modules','child_plans','journal_roots','dependencies','symbols'}
        and profile['schema_version']=='fleet-retained-runtime/v1'and profile['entrypoint']==p['helper']
        and profile['read_call']==dict(function='context',args=[],kwargs={})
        and all(profile[k]==[]for k in('patches','guard_modules','child_plans','journal_roots','dependencies'))
        and len(profile['symbols'])==1,'only declared completed semantic reader scope permitted')
    original=read(old['fresh_plan']);symbol=profile['symbols'][0]
    require(old['retained_context']['kind']=='completed_disclosure'and old['retained_context']['plan']==old['fresh_plan']
        and original['schema_version']=='fleet-retained-completed-disclosure/v1'
        and set(symbol)=={'module_name','original','selected','names'}
        and symbol['original']==original['helper']and symbol['names']==['semantic_current']
        and original['semantic_successor']==dict(helper=old['helper'],output=old['output']),
        'completed semantic reader detached from original consumed round')
    for item in (symbol['original'],symbol['selected']):read_or_check(item)
    old_tree=ast.parse(Path(symbol['original']['path']).read_text());new_tree=ast.parse(Path(symbol['selected']['path']).read_text())
    def rest(tree):return ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n,ast.FunctionDef)or n.name not in {'semantic_current','semantic_terminal'}],type_ignores=[]),include_attributes=False)
    require(rest(old_tree)==rest(new_tree)
        and [n.name for n in old_tree.body if isinstance(n,ast.FunctionDef)and n.name in {'semantic_current','semantic_terminal'}]==['semantic_current']
        and [n.name for n in new_tree.body if isinstance(n,ast.FunctionDef)and n.name in {'semantic_current','semantic_terminal'}]==['semantic_current','semantic_terminal'],
        'completed semantic runtime changed unrelated original reader code')
    runtime=module('completed_semantic_history_scope',profile['runtime'])
    return runtime.installed(row)


@contextmanager
def completed_semantic_context(p):
    with semantic_context_runtime(p):
        with _completed_semantic_context(p)as selected:yield selected


@contextmanager
def _completed_semantic_context(p):
    """Original permit and all three actual author/review children remain owning."""
    live=p['live_renewal'];prior=live_review(p);original=module('warning_completed_semantic_original',live['context'])
    loaded=original.context(live['plan'],continued=True)
    old,fresh,job,D,env,w,selection,lease,recipe,S,L=loaded
    manager=original.retained_manager(old,D,L)
    try:
        require(old==read(live['plan'])and old['helper']==live['context'],'original completed semantic loader changed')
        C,I,_,W,_,config,root,controls=env;home=Path(old['output']);current=binding(home/'permit/result.json');value=read(current)
        guard=read(binding(home/'permit-resource.json'));intent=read(binding(home/'intent.json'))
        require(intent==dict(plan=live['plan'],helper=old['helper'],automatic_retry=False)
            and guard['intent']==binding(home/'intent.json')and guard['returncode']==0 and guard['failure']is None
            and guard['models_dispatched']==0 and guard['seconds']==3300 and guard['memory_bytes']==MEMORY
            and guard['automatic_retry']is False and guard['log']==binding(home/'permit.log')
            and guard['command']==[sys.executable,'-I','-B',old['helper']['path'],'permit-child','--plan',live['plan']['path'],'--sha256',live['plan']['sha256']],
            'original semantic permit guard changed')
        I._prior(current,value['descriptor']);actual,observed,_=I.selected(value['descriptor'])
        require(value['status']=='RETRY_READY'and value['next_step']=='dialogue-writer'and value['descriptor']==binding(home/'permit/descriptor.json')
            and observed==controls and actual.run==w.run and actual.root==w.root
            and actual.dialogue_semantic_retry==binding(w.run/'dialogue-semantic-retry-02/complete.json'),
            'original semantic handoff changed')
        permit=read(read(actual.dialogue_semantic_retry)['permit'])
        require(permit['blocked']==old['blocked']and permit['round']==2 and permit['consumed_rounds']==1 and permit['semantic_limit']==2
            and permit['semantic_budget_reset']is False,'semantic warning lost consumed first review')
        ops=Path(config['run_root'])/'dispatcher-v2'/home.name
        require(sorted(x.name for x in ops.iterdir()if x.is_dir())==['000','001'],'exact second writer/paraphraser/review tail required')
        evidence=[live['plan'],current,actual.dialogue_semantic_retry,binding(home/'permit-resource.json')]
        for index,directory in enumerate([home/'first-step',ops/'000',ops/'001']):
            intent=binding(directory/'intent.json');iv=read(intent);side=read(binding(directory/'semantic-bootstrap.json'))
            kind,bound=original.retained_command(side['original_command'],old,D,env)
            require(kind=='instruction'and bound==intent and iv['action']=='step'and iv['arguments']['checkpoint']==current
                and iv['arguments']['descriptor']==value['descriptor']and side==dict(schema_version='fleet-retained-semantic-child/v1',
                    plan=live['plan'],helper=old['helper'],kind=kind,intent=intent,original_command=side['original_command'],
                    seconds=original.retained_seconds(kind,intent,env),memory_bytes=MEMORY,automatic_retry=False),
                'second semantic child lost original predecessor or guard')
            outcome=binding(directory/'outcome.json');evidence.extend([intent,outcome])
            if index<2:
                current,value=D.successful(outcome,env)
                require(value['status']=='STEP_COMPLETE'and value['next_step']==('dialogue-paraphraser','dialogue-review')[index],
                    'second author pair did not complete')
            else:
                require(prior['outcome']==outcome,'warning does not select actual final review')
                value,selection,lease=original.completed_failure(prior,I,controls);current=prior['result']
        actual,_,recipe=I.selected(value['descriptor']);_,before,_=W.active(actual,selection,lease)
        require(binding(prior['tracker']['path'])==prior['tracker']and actual.dialogue_semantic_retry is not None,
            'completed semantic current tracker differs')
        S.validate(actual,before);evidence.extend([current,prior['blocked'],read(actual.dialogue_semantic_retry)['after']])
        yield prior,D,env,actual,selection,lease,recipe,S,evidence
    finally:manager.__exit__(*sys.exc_info())


def completed_semantic_renew(row,p):
    with completed_semantic_context(p)as(prior,D,env,w,selection,lease,recipe,S,evidence):
        C,I=env[:2];root=Path(read(p['snapshot'])['root']);custody_env=(*env[:6],root,env[7])
        module_row=p['live_renewal']['completed_semantic']['renewal_module']
        L=module('warning_completed_semantic_lease',module_row)
        require(L.SCHEMA=='fleet-validated-continuation-lease/v1'and L.DURATION_NS==7200*10**9,
            'original bounded continuation lease scheduler required')
        def validate(before,old):
            _,actual,_=env[3].active(w,selection,old);require(actual==before,'semantic renewal changed current tracker')
            S.validate(w,before);return evidence
        with(w.run/'worker.lock').open('a+b')as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            tracker=Path(prior['tracker']['path']);require(binding(tracker)==prior['tracker'],'semantic live before changed')
            before_copy=Path(p['output'])/'semantic-live-before.json'
            fd=os.open(before_copy,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb')as stream:stream.write(tracker.read_bytes());stream.flush();os.fsync(stream.fileno())
            parent_fd=os.open(before_copy.parent,os.O_RDONLY)
            try:os.fsync(parent_fd)
            finally:os.close(parent_fd)
            copied=binding(before_copy);require(copied['sha256']==prior['tracker']['sha256'],'semantic live before copy changed')
            evidence=[*evidence,copied]
            _,completion=L.renew(life=S.life,read=I.read,record=binding,keep=keep,path=prior['tracker']['path'],lease=lease,
                authority_record=row,validation=validate,custody=lambda:active(p,custody_env),directory=Path(p['output'])/'live-renewal',retain_sufficient=True)
        return dict(status='LIVE_WARNING_LEASE_RENEWED',completion=completion,models_dispatched=0,automatic_retry=False)


if __name__=='__main__':main()
