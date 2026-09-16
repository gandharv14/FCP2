"""Resume a completed environment prefix using original refresh and step APIs.

A completed expired-lease takeover is read, never repeated. Historical source,
model operations and checkpoints remain immutable. Every actual transition uses
its original bounded cold child; a new namespace never resets task budgets.
"""
from pathlib import Path
from dataclasses import asdict
import argparse,contextlib,hashlib,importlib.util,json,os,sys,time

SCHEMA='fleet-completed-environment-continuation/v1'
FIELDS={'schema_version','entry','stdlib','original_job','original_worker_job','checkpoint','outcome','takeover_runtime','takeover_profile','takeover_completion','source','workbook','home','operations','owner'}

def require(ok,message):
    if not ok:raise ValueError(message)
def binding(path):
    p=Path(path);require(p.is_absolute() and not any(x.is_symlink()for x in(p,*p.parents)),'absolute regular evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def checked(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row,'selected evidence changed')
    return json.loads(Path(row['path']).read_bytes())
def keep(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    return binding(p)
def module(name,row):
    require(binding(row['path'])==row,'selected code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def takeover_context(runtime,profile,completion,*,current=True):
    if completion is None:return runtime.load(profile)
    # The original direct reader's one historical tracker input is read from
    # the immutable pre-CAS archive. All other reads retain their exact paths.
    from types import FunctionType,SimpleNamespace
    p=checked(profile);value=checked(completion);stop=checked(value['stopped_owner_proof'])
    require(p['schema_version']=='harbor-allowance-takeover-direct/v1','completed takeover selection differs')
    old=p['selection']['tracker'];archive=stop['before']
    require(old['sha256']==archive['sha256'],'completed takeover original archive differs')
    def historical(row):
        return runtime.checked(archive if row==old else row)
    f=runtime.direct
    require(f.__globals__ is runtime.__dict__ and f.__closure__ is None,'plain direct reader required')
    observed=stop['observed_at']
    require(type(observed)is int and observed>0,'completed takeover observation time required')
    # This reconstructs an already completed action. Its original allowance is
    # judged at that action's time; validate_completion still proves the event.
    clock=SimpleNamespace(time_ns=lambda:observed)
    selected=FunctionType(f.__code__,{**f.__globals__,'checked':historical,'time':clock},f.__name__,f.__defaults__)(p)
    T=selected[1]
    require(T._selection(stop['selection'])==T._selection(p['selection']),'completed takeover selection differs')
    T.validate_completion(completion)
    if current:require(value['after']['sha256']==binding(old['path'])['sha256'],'current tracker differs from actual completed takeover')
    return selected

def select_history(p,worker_job,*,probe=False):
    """Select the original job's history without rerunning its fresh admission."""
    if 'history' in worker_job:
        history=worker_job['history'];checked(history)
        require(not os.environ.get('HARBOR_FLEET_LEGACY_RESEARCH_PATH'),'conflicting legacy history')
        if probe:
            from scripts.fleet import history as H
            os.environ.update(HARBOR_FLEET_WORKBOOK=p['workbook'],HARBOR_FLEET_HISTORY_PATH=history['path'],HARBOR_FLEET_HISTORY_SHA256=history['sha256'])
            H.projected_survey=lambda roots,ids:H.inspect_projection(roots,ids,p['workbook'],history)
        else:
            require(os.environ.get('HARBOR_FLEET_HISTORY_PATH')==history['path'] and os.environ.get('HARBOR_FLEET_HISTORY_SHA256')==history['sha256'],'original live history authority required')
        return
    inputs=worker_job['inputs'];require(bool(inputs),'original fresh plan required')
    plan_row=inputs[0];fresh=checked(plan_row);root=Path(fresh['code_root'])
    command=worker_job['command']
    require(len(command)==9 and command[1:3]==['-I','-B'] and command[3:]==[
        str(root/'scripts/fleet/fresh.py'),'run','--plan',plan_row['path'],'--plan-sha256',plan_row['sha256']],
        'original fresh invocation differs')
    require(fresh['workbook']==p['workbook']==worker_job['workbook'] and root.name==worker_job['version'],
        'original legacy workbook/version differs')
    original=checked(p['original_job']);config=checked(original['config'])
    require(original['driver']==fresh['dispatcher'] and Path(config['run_root'])==Path(fresh['home'])/'authoring'
        and checked(config['code'])['root']==str(root),'legacy history detached from completed author job')
    legacy=fresh['legacy_research_history'];require(legacy in inputs,'original legacy input missing')
    from scripts.batch import harbor_legacy_research_history as L
    inventory=checked(fresh['inventory'])
    inherited=L.validate(legacy,workbook=p['workbook'],original=p['source'],history_roots=fresh['history_roots'],
        known_ids={i['workbook_id']for i in inventory['sources']})
    F=module('completed_environment_original_fresh',binding(root/'scripts/fleet/fresh.py'))
    source=F._validate(fresh,lambda roots,ids:L.author_survey(inherited))
    require({k:source[k]for k in ('path','sha256')}==p['source']==worker_job['source'],
        'original legacy source differs')
    L.source_handoff(legacy,checked(fresh['reconstruction']['handoff']))
    require(not any(os.environ.get(k)for k in ('HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_CONTINUATION_HISTORY_PATH',
        'HARBOR_FLEET_SOURCE_HISTORY_PATH')),'foreign environment history projection')
    for suffix,value in (('PATH',legacy['path']),('SHA256',legacy['sha256'])):
        key='HARBOR_FLEET_LEGACY_RESEARCH_'+suffix
        require(os.environ.get(key)in (None,value),'original legacy history changed');os.environ[key]=value
    if probe:
        os.environ['HARBOR_FLEET_WORKBOOK']=p['workbook']
        def historical(roots,ids):
            value=L.validate(legacy,workbook=p['workbook'],original=p['source'],history_roots=roots,known_ids=ids)
            return L.author_survey(value)
        L.projected_survey=historical


def takeover_config(selection,job):
    """Retain canonical takeover custody when only admission intake was relocated."""
    if selection.config==job['config']:return
    active=checked(job['config']);canonical=checked(selection.config)
    require(selection.config==binding(Path(active['run_root'])/'config.json')
        and 'intake'in active and set(active)==set(canonical)
        and canonical==dict(active,intake=canonical['intake']),
        'takeover configuration differs beyond exact original intake placement')
    checked(active['intake']);checked(canonical['intake'])


def profile_remaining_identity(p):
    proof=checked(p['profile_remaining'])
    require(set(proof)=={'schema_version','previous','outer','refresh','failed','stopped'} and proof['schema_version']=='fleet-profile-fragment-remaining/v1','closed retained profile boundary required')
    old=checked(proof['previous']);changes={'entry','home','operations','profile_recovery','profile_remaining','profile_derived_remaining'}
    require(set(old)==FIELDS|{'profile_recovery'} and set(p)in(set(old)|{'profile_remaining'},set(old)|{'profile_remaining','profile_derived_remaining'})
        and all(p[k]==old[k]for k in old if k not in changes)
        and Path(p['home']).parent==Path(old['home']).parent and p['home']!=old['home']
        and Path(p['operations']).parent==Path(old['operations']).parent and p['operations']!=old['operations'],
        'remaining profile changed original authority or namespace')
    correction=checked(checked(p['profile_recovery'])['producer_correction'])
    old_policy=checked(old['profile_recovery'])
    require(all(correction[k]==old_policy[k]for k in ('source','phase1','inventory','captures','authorization')),'remaining profile correction detached from original inputs')
    outer=checked(proof['outer']);intent=checked(outer['intent']);job=checked(intent['job']);claim=checked(binding(Path(outer['intent']['path']).parent/'claim.json'))
    require(job['driver']==old['entry'] and job['inputs'][0]==proof['previous'] and job['source']==old['source'] and job['workbook']==old['workbook']
        and job['command'][1:]==['-I','-B',old['entry']['path'],'run','--plan',proof['previous']['path'],'--plan-sha256',proof['previous']['sha256']],
        'stopped profile job differs from original continuation')
    require(intent['owner']==claim['owner']==old['owner'] and intent['claim_id']==claim['claim_id']
        and claim['workbook']==old['workbook'] and claim['source_sha256']==old['source']['sha256'] and claim['version']==job['version'] and type(claim['fence'])is int and claim['fence']>0,
        'stopped profile claim identity differs')
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==0 and outer['failure_type']is None and outer['automatic_retry']is False and outer['publication_performed']is False
        and outer['result']==binding(Path(old['home'])/'result.json') and checked(outer['result'])['status']=='UNRESOLVED','completed unresolved original profile required')
    values=[]
    for key,folder in (('refresh','refresh'),('failed','bridge')):
        row=proof[key];require(row==binding(Path(old['home'])/folder/'outcome.json'),'original profile guard namespace differs')
        value=checked(row);resource=checked(value['resource'])
        require(value['status']=='COMPLETED_OPERATION' and resource['returncode']==0 and resource['failure']is None,'uncertain profile operation cannot resume')
        values.append((value,checked(value['result']),checked(resource['intent'])))
    refresh,imported,refresh_intent=values[0];failed,result,failed_intent=values[1]
    require(imported['status']=='IMPORTED' and imported['step']=='normalization_phase1' and refresh_intent['action']=='refresh'
        and refresh_intent['arguments']['checkpoint']==old['checkpoint'],'original completed phase-one refresh required')
    require(result['status']=='UNRESOLVED' and result['step']=='source_profile' and result['descriptor']==imported['descriptor'] and result['lease']==imported['lease']
        and correction['stop']in result['evidence'] and failed_intent['action']=='step'
        and failed_intent['arguments']==dict(descriptor=imported['descriptor'],checkpoint=refresh['result'],captures=old_policy['captures']),
        'original failed producer detached from completed refresh')
    stopped=checked(proof['stopped']);require(stopped['processes']==[] and stopped['files'],'exact stopped profile evidence required')
    copies={r['native']['path']:r for r in stopped['files']}
    for r in stopped['files']:
        require(binding(r['copy']['path'])==r['copy'] and r['copy']['sha256']==r['native']['sha256'],'stopped profile copy changed')
    for row in (proof['previous'],proof['outer'],proof['refresh'],proof['failed'],outer['intent'],intent['job'],binding(Path(outer['intent']['path']).parent/'claim.json')):
        require(row['path']in copies and copies[row['path']]['native']==row,'stopped profile custody missing original identity')
    if 'profile_derived_remaining'in p:
        extra=checked(p['profile_derived_remaining']);module('selected_profile_derived_remaining',extra['helper']).validate(sys.modules[__name__],p)
    return old,proof,refresh['result'],imported


def profile_remaining_context(bound,*,probe=False):
    p=checked(bound);old,proof,checkpoint,value=profile_remaining_identity(p)
    E=module('profile_remaining_original_context',old['entry'])
    E.module('profile_remaining_stdlib',old['stdlib']).preload_inspect()
    runtime=module('profile_remaining_original_takeover',p['takeover_runtime'])
    _,T,selection,_=takeover_context(runtime,p['takeover_profile'],p['takeover_completion'],current=False)
    _,_,original_lease=T.validate_completion(p['takeover_completion'])
    if probe:
        job=checked(old['original_worker_job']);E.select_history(old,job,probe=True)
    _,D,env=E.cold_context(proof['previous']);C=env[0]
    worker,_,_=C.selected(value['descriptor']);prior,lease=C._prior(checkpoint,value['descriptor'])
    require(prior==value and (lease.owner,lease.token)==(original_lease.owner,original_lease.token) and lease.revision>=original_lease.revision,'retained import left original takeover custody')
    C.worker._active(worker,lease)
    require(not Path(p['home']).exists(),'remaining profile continuation consumed');D.operation_namespace(env[5],p['operations'])
    current=C.content_codec.decode(value['selection'])
    require(current.content.workbook==p['workbook'] and current.content.bindings['original_raw_source']==p['source'],'remaining profile source differs')
    from scripts.batch import harbor_environment_refresh as F
    return p,D,env,worker,current,lease,T,F


def context(bound,*,probe=False):
    p=checked(bound)
    if 'profile_remaining'in p:
        require(set(p)in(FIELDS|{'profile_recovery','profile_remaining'},FIELDS|{'profile_recovery','profile_remaining','profile_derived_remaining'}) and p['schema_version']==SCHEMA and p['entry']==binding(__file__),'closed remaining profile context required')
        return profile_remaining_context(bound,probe=probe)
    require(set(p) in (FIELDS,FIELDS|{'package_recovery'},FIELDS|{'profile_recovery'}) and p['schema_version']==SCHEMA and p['entry']==binding(__file__),'closed completed environment continuation required')
    module('environment_continuation_stdlib',p['stdlib']).preload_inspect()
    runtime=module('environment_continuation_takeover',p['takeover_runtime']);_,T,s,_=takeover_context(runtime,p['takeover_profile'],p['takeover_completion'])
    require(s.workbook==p['workbook'] and s.checkpoint==p['checkpoint'] and s.owner==p['owner'],'takeover detached from retained boundary')
    old=checked(p['original_job']);worker_job=checked(p['original_worker_job']);config=checked(old['config'])
    takeover_config(s,old)
    require(worker_job['workbook']==p['workbook'] and worker_job['source']==p['source']
            and worker_job['version']==Path(checked(config['code'])['root']).name,'original job/source differs')
    require(old['publish']is False and all(old[k]is True for k in ('models','commands','containers')),'original capabilities differ')
    if probe:
        # Retained model plans include the original fleet transport policy.
        # A read-only validator must reconstruct that configuration too.
        os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
    select_history(p,worker_job,probe=probe)
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    D=module('original_environment_dispatcher',old['driver']);env=D.setup(old);C=env[0]
    checkpoint,value=D.successful(p['outcome'],env)
    require(checkpoint==p['checkpoint'] and value['status']=='STEP_COMPLETE' and value['step'] in {'normalization_phase1','source_profile','normalization_phase2'},'completed environment boundary required')
    worker,_,controls=C.selected(value['descriptor']);C._prior(checkpoint,value['descriptor'])
    require(worker.bootstrap.workbook==p['workbook'] and worker.bootstrap.policy.tracker_root==Path(s.tracker['path']).parent,'retained worker differs')
    current=C.content_codec.decode(value['selection'])
    from scripts.batch import harbor_environment_refresh as F
    previous,kind,_=F.predecessor(worker,checkpoint,current=current);F.unchanged(previous,current)
    require(Path(p['home']).parent==Path(config['run_root'])/'continuations' and not Path(p['home']).exists(),'continuation namespace consumed')
    D.operation_namespace(config,p['operations'])
    lease=None
    if p['takeover_completion']is not None:
        before,after,lease=T.validate_completion(p['takeover_completion'])
        completion=checked(p['takeover_completion']);stop=checked(completion['stopped_owner_proof'])
        require(stop['selection']==T._selection_value(s) and completion['after']['sha256']==binding(s.tracker['path'])['sha256'],'completed takeover context changed')
        C.bootstrap._tracker(worker.bootstrap,lease)
        require(after['leases']['task_agent']['expires_at']-time.time_ns()>3400*10**9,'insufficient lease for original first guard')
    elif not probe:raise ValueError('actual completed takeover required before continuation')
    return p,D,env,worker,current,lease,T,F

def package_policy(p,env):
    row=p.get('package_recovery')
    if row is None:return None
    q=checked(row)
    require(set(q)=={'schema_version','helper','runner','release','original_release','policy',
                    'work','selection','command','result','log','failure'}
            and q['schema_version']=='fleet-completed-package-selection/v1','closed retained package selection required')
    for key in ('helper','runner','release','original_release'):
        require(binding(q[key]['path'])==q[key],'package code dependency changed')
    for key in ('policy','selection','command','result','failure'):checked(q[key])
    require(binding(q['log']['path'])==q['log'],'retained package log changed')
    policy=checked(q['policy'])
    require(policy['schema_version']=='passive-web-hyperlinks/v1'
            and policy['code_sha256']==q['release']['sha256']
            and policy['source_modified'] is False and policy['model_calls']==0,
            'selected passive-reference policy differs')
    D=module('package_original_dispatcher_check',checked(p['original_job'])['driver'])
    failed,value=D.successful(q['failure'],env)
    intent=checked(checked(checked(q['failure'])['resource'])['intent'])
    require(value['status']=='UNRESOLVED' and value['step']=='environment'
            and intent['action']=='step' and intent['arguments']['checkpoint']==p['checkpoint']
            and value['descriptor']==checked(p['checkpoint'])['descriptor'],
            'package failure detached from original complete prefix')
    C=env[0];worker,_,_=C.selected(value['descriptor'])
    require(Path(q['work'])==worker.run/'packaging'
            and q['selection']==binding(Path(q['work'])/'selection.json'),
            'retained package location differs')
    original=C.worker.environment.readiness.publication
    require(binding(original.__file__)==q['original_release'],'original publication validator differs')
    return q


@contextlib.contextmanager
def package_runtime(bound,p,D,env):
    if 'profile_recovery' in p:
        with profile_runtime(bound,p,D,env) as selected:yield selected
        return
    q=package_policy(p,env)
    if q is None:
        yield None
        return
    C,I=env[:2]
    runner=module('completed_environment_declared_runner',q['runner'])
    release=module('completed_package_selected_release',q['release'])
    original=C.worker.environment.readiness.publication
    old_function=original._reject_external_package_artifacts
    selected=release._reject_external_package_artifacts
    # Keep original module identity and all other functions. The separately
    # bound selected function carries its own passive-reference dependencies.
    evidence=[p['package_recovery'],*[q[k]for k in ('helper','runner','release','original_release','policy')]]
    original_runner=C._run_in_process_group
    guards={}
    for kind,producer in (('environment',C),('instruction',I),('renewal',D)):
        guards[str(Path(producer.__file__).absolute())]=runner.select_run(producer,base=C,kind=kind,
            reader_plan=bound,child_wrapper=p['entry'],producer_code=binding(producer.__file__),
            reader_evidence=evidence).declared_runner
    def guarded(command,**options):
        require(isinstance(command,list) and len(command)in {6,7},'unsupported retained child launch')
        code=command[3] if command[1:3]==['-I','-B'] else command[2]
        require(code in guards,'undeclared owning child producer')
        return guards[code](command,**options)
    changes=[]
    for m in tuple(sys.modules.values()):
        namespace=getattr(m,'__dict__',None)
        if namespace is None or m is release:continue
        for key,value in tuple(namespace.items()):
            if value is old_function:changes.append((namespace,key,value));namespace[key]=selected
    C._run_in_process_group=guarded
    try:yield q,runner,evidence
    finally:
        C._run_in_process_group=original_runner
        for namespace,key,value in reversed(changes):namespace[key]=value


@contextlib.contextmanager
def profile_runtime(bound,p,D,env):
    q=checked(p['profile_recovery']);helper=module('selected_verified_profile_runtime',q['helper'])
    C,I=env[:2];runner=module('selected_profile_declared_runner',q['runner'])
    profile=checked(q['runtime_profile'])
    evidence=[p['profile_recovery'],*[q[k]for k in ('helper','runner','runtime','runtime_profile','skip_helper','authorization')],*[r[k]for r in profile['dependencies']for k in ('original','selected')]]
    if 'projection_helper'in q:evidence.append(q['projection_helper'])
    if 'producer_correction'in q:evidence.append(q['producer_correction'])
    if 'profile_remaining'in p:evidence.append(p['profile_remaining'])
    if 'profile_derived_remaining'in p:evidence.extend([p['profile_derived_remaining'],checked(p['profile_derived_remaining'])['helper']])
    original_runner=C._run_in_process_group
    guards={str(Path(producer.__file__).absolute()):runner.select_run(producer,base=C,kind=kind,
        reader_plan=bound,child_wrapper=p['entry'],producer_code=binding(producer.__file__),reader_evidence=evidence).declared_runner
        for kind,producer in (('environment',C),('instruction',I),('renewal',D))}
    def guarded(command,**options):
        require(isinstance(command,list)and len(command)in {6,7},'unsupported retained profile child')
        code=command[3]if command[1:3]==['-I','-B']else command[2]
        require(code in guards,'undeclared profile child producer');return guards[code](command,**options)
    try:
        with helper.installed(p['profile_recovery'],env):
            C._run_in_process_group=guarded
            yield q,runner,evidence
    finally:C._run_in_process_group=original_runner


def cold_context(bound):
    p=checked(bound)
    require(set(p) in (FIELDS|{'package_recovery'},FIELDS|{'profile_recovery'},FIELDS|{'profile_recovery','profile_remaining'},FIELDS|{'profile_recovery','profile_remaining','profile_derived_remaining'}) and p['schema_version']==SCHEMA
            and p['entry']==binding(__file__),'closed selected package child required')
    module('environment_continuation_stdlib',p['stdlib']).preload_inspect()
    runtime=module('environment_continuation_takeover',p['takeover_runtime'])
    _,T,s,_=takeover_context(runtime,p['takeover_profile'],p['takeover_completion'],current=False)
    require(s.workbook==p['workbook'] and s.owner==p['owner'] and s.checkpoint==p['checkpoint'],
            'child takeover source boundary differs')
    old=checked(p['original_job']);job=checked(p['original_worker_job'])
    takeover_config(s,old)
    require(job['workbook']==p['workbook']
            and job['source']==p['source'],'child original source differs')
    select_history(p,job)
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    D=module('original_environment_dispatcher',old['driver']);env=D.setup(old)
    require(job['version']==env[6].name,'cold child original version differs')
    return p,D,env


def completed_context(bound,*,probe=False):
    """Read an already consumed ordinary environment continuation.

    The pinned original helper validates its original takeover and history.
    A caller must still validate the later instruction checkpoint and live
    lease with the original instruction APIs before authorizing any work.
    """
    p=checked(bound)
    require(set(p)==FIELDS and p['schema_version']==SCHEMA
            and p['takeover_completion'] is not None,
            'closed completed ordinary environment context required')
    E=module('retained_completed_environment_original',p['entry'])
    require(E.SCHEMA==SCHEMA and E.FIELDS==FIELDS,
            'original environment context schema differs')
    E.module('retained_environment_stdlib',p['stdlib']).preload_inspect()
    runtime=E.module('retained_environment_takeover',p['takeover_runtime'])
    _,T,s,_=E.takeover_context(runtime,p['takeover_profile'],p['takeover_completion'],current=False)
    require(s.workbook==p['workbook'] and s.owner==p['owner'] and s.checkpoint==p['checkpoint'],
            'retained takeover source boundary differs')
    old=E.checked(p['original_job']);job=E.checked(p['original_worker_job'])
    config=E.checked(old['config']);E.takeover_config(s,old)
    require(job['workbook']==p['workbook'] and job['source']==p['source']
            and job['version']==Path(E.checked(config['code'])['root']).name,
            'retained environment source/version differs')
    require(old['publish'] is False and all(old[k] is True for k in ('models','commands','containers')),
            'retained environment capabilities differ')
    require(Path(p['home']).parent==Path(config['run_root'])/'continuations'
            and Path(p['home']).is_dir(), 'completed environment namespace absent or foreign')
    if probe:os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
    E.select_history(p,job,probe=probe)
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    D=E.module('retained_environment_original_dispatcher',old['driver']);env=D.setup(old)
    require(len(env)==8 and env[5]==config and Path(env[6]).name==job['version'],
            'retained environment dispatcher selection differs')
    return p,D,env


def owning_child(bound,kind,intent):
    if 'gate_repair' in checked(bound):return gate_context(bound).owning_child(sys.modules[__name__],bound,kind,intent)
    p,D,env=cold_context(bound);active(p);C,I=env[:2]
    with package_runtime(bound,p,D,env)as selected:
        q,runner,evidence=selected
        require(kind in {'environment','instruction','renewal'},'unsupported package continuation child')
        producer={'environment':C,'instruction':I,'renewal':D}[kind]
        runner.validate_child(producer,base=C,kind=kind,reader_plan=bound,
            child_wrapper=p['entry'],intent=intent,reader_evidence=evidence)
        value=checked(intent)
        if 'package_recovery' in p and kind=='environment' and value['action']=='step' and Path(intent['path']).parent==Path(p['home'])/'bridge':
            args=value['arguments'];descriptor=checked(args['descriptor'])
            imported=checked(args['checkpoint'])
            require(imported['status']=='IMPORTED' and descriptor['refresh']==binding(Path(p['home'])/'refresh/prefix/prepared.json'),
                    'package checkpoint belongs to another refresh')
            helper=module('selected_retained_package_completion',q['helper'])
            from scripts.batch import harbor_environment_refresh as F
            helper.checkpoint(C,F,args['descriptor'],args['checkpoint'],authority=p['package_recovery'],
                directory=Path(descriptor['refresh']['path']).parent/'continuation')
        if kind=='renewal':D.renewal_child(intent)
        else:producer.child(intent)


def active(p):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook'] and os.environ.get('HARBOR_FLEET_OWNER')==p['owner'],'exclusive central owner required')
    value=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',workbook=p['workbook'],owner=p['owner'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],fence=int(os.environ['HARBOR_FLEET_FENCE']),lease_seconds=180)
    require(value['state']=='active' and value['source_sha256']==p['source']['sha256'],'live original source custody differs')

def probe(bound):
    if 'gate_repair' in checked(bound):return gate_context(bound).probe(sys.modules[__name__],bound)
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit read-only probe required')
    p,D,env,worker,current,lease,T,F=context(bound,probe=True);wire=env[0].content_codec.encode(current);tracker=binding(worker.bootstrap.policy.tracker_root/(p['workbook']+'.json'));before=checked(tracker)
    with package_runtime(bound,p,D,env)as selected:
        if 'profile_recovery' in p:current=env[0].content_codec.decode(wire)
        checks=F.checks(worker,before,current,checked(p['checkpoint'])['step'])
        if selected is not None and 'package_recovery' in p:
            q=selected[0];helper=module('selected_package_probe',q['helper'])
            helper.validate(env[0].worker.environment,current.content,current.public_profiles,Path(q['work']),worker.taxonomy)
    require(binding(tracker['path'])==tracker,'probe changed tracker')
    return dict(status='COMPLETED_ENVIRONMENT_PREFIX_VALIDATED',plan=bound,tracker=tracker,checks=[dict(name=x.get('gate'),passed=x['passed'])for x in checks],models=0,tracker_mutations=0)

def _run_selected(bound,loaded):
    p,D,env,worker,current,lease,T,F=loaded;active(p);C=env[0];home=Path(p['home']);home.mkdir(parents=True)
    keep(home/'intent.json',dict(plan=bound,entry=p['entry'],automatic_retry=False))
    C._campaign(worker.campaign)
    arguments=None if 'profile_remaining'in p else dict(legacy=keep(home/'legacy.json',dict(worker=C.worker.bootstrap.serial(asdict(worker)),lease=C.encode(lease))),checkpoint=p['checkpoint'],current=keep(home/'current.json',C.content_codec.encode(current)),lease=keep(home/'lease.json',C.encode(lease)))
    with D.fleet_slot(p['workbook'])as fds:
        active(p)
        if 'profile_remaining'in p:outcome=checked(p['profile_remaining'])['refresh']
        else:outcome=C.run('refresh',arguments,env[7],home/'refresh',guardian_lock_fds=fds)
        raw=checked(outcome);require(raw['status']=='COMPLETED_OPERATION','refresh incomplete; reconcile retained CAS before resume')
        checkpoint=raw['result'];value=checked(checkpoint);require(value['status']=='IMPORTED','refresh not imported')
        active(p);first=C.run('step',dict(descriptor=value['descriptor'],checkpoint=checkpoint,captures=checked(p['profile_recovery'])['captures'] if 'profile_recovery' in p else None),env[7],home/'bridge',dispatch=True,guardian_lock_fds=fds)
        D.successful(first,env)
    job=keep(home/'authoring-job.json',{**checked(p['original_job']),'outcome':first,'operations':p['operations']});D.run(job)
    hold=checked(binding(Path(p['operations'])/'hold.json'));checkpoint,value=D.successful(hold['outcome'],env)
    return keep(home/'result.json',dict(status=value['status'],plan=bound,job=job,config=checked(job)['config'],outcome=hold['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))


def run(bound):
    if 'gate_repair' in checked(bound):return gate_context(bound).run(sys.modules[__name__],bound)
    loaded=context(bound)
    p,D,env=loaded[:3];wire=env[0].content_codec.encode(loaded[4])
    with package_runtime(bound,p,D,env):
        if 'profile_recovery' in p:loaded=(*loaded[:4],env[0].content_codec.decode(wire),*loaded[5:])
        return _run_selected(bound,loaded)

def gate_context(bound):
    return module('selected_environment_gate_context',checked(bound)['gate_repair']['helper'])


def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind');a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();bound=dict(path=str(Path(v.plan).absolute()),sha256=v.plan_sha256)
    if v.action=='owning-child':owning_child(bound,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
    else:print(json.dumps(globals()[v.action](bound)))
if __name__=='__main__':main()
