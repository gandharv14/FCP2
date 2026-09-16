"""Bounded continuation of a settled first naturalization rejection.

The original retry transaction retains the first draft/review and consumes its
existing second round. No model operation is replayed and no snapshot changes.
"""
from pathlib import Path
import inspect
import argparse, fcntl, hashlib, importlib.util, json, os, sys, time

SCHEMA = 'fleet-naturalization-continuation/v1'
FIELDS = {'schema_version','entry','original_job','outcome','result','source',
          'tracker','owner','fence','output','capacity_policy','renewal','adapter',
          'authorization','workbook'}
AUTHORIZATION = 'acffe75c8ed401f66484a5bb6c39a15a0a3e209fc976967da0bddd6125126fd0'

def require(value, message):
    if not value: raise ValueError(message)

def binding(path):
    p=Path(path)
    require(p.is_absolute() and p.is_file() and not any(x.is_symlink() for x in (p,*p.parents)), 'canonical evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(set(row)=={'path','sha256'} and binding(row['path'])==row, 'selected evidence changed')
    return json.loads(Path(row['path']).read_bytes())

def keep(path,value):
    p=Path(path);require(not any(x.is_symlink() for x in (p,*p.parents)), 'unsafe journal')
    with p.open('x') as f:
        json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(p.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(p)

def module(name,row):
    require(binding(row['path'])==row,'selected code changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

def completed_rejection(p,D,env):
    C,I,_,W,_,config,root,controls=env
    # D.successful with an environment additionally treats a rejection as a
    # resumable checkpoint. Validate its actual guard, then select the original
    # rejected worker explicitly instead of claiming that rejection is READY.
    checkpoint,value=D.successful(p['outcome'])
    require(checkpoint==p['result'] and value['status']=='NAT_REPAIR_REQUIRED'
            and value['schema_version']==I.SCHEMA and value['next_step'] in ('nat-writer','nat-review')
            and value['publication_performed'] is False,'settled original NAT rejection required')
    directory=Path(p['outcome']['path']).parent;intent=read(binding(directory/'intent.json'))
    require(intent['code']==binding(I.__file__) and intent['base_code']==binding(C.__file__)
            and intent['action']=='step' and intent['arguments']['descriptor']==value['descriptor']
            and value['evidence'][0]==intent['arguments']['checkpoint']
            and intent['controls']==C.encode(controls) and intent['memory_limit_bytes']==C.MEMORY
            and intent['timeout_seconds']==I.guard_seconds('step',intent['arguments'])
            and binding(intent['executable']['path'])==intent['executable'],'original NAT guard changed')
    worker,expected,recipe=I.selected(value['descriptor'])
    require(expected==controls and worker.root==root and worker.run==Path(config['run_root'])/'instruction',
            'original owning worker changed')
    selected=I.codec.decode(value['selection']);lease=C.decode(value['lease'])
    require(selected.content.workbook==p['workbook'] and selected.content.bindings['original_raw_source']==p['source']
            and binding(p['source']['path'])==p['source'] and worker.retry is None,'source or consumed retry changed')
    return value,worker,selected,lease

def select_history(p,job,*,probe=False):
    context=p.get('history_context')
    if context is None:return
    require(set(context)=={'helper','original_worker_job'},'closed original NAT history context required')
    original=read(context['original_worker_job']);config=read(job['config']);snapshot=read(config['code'])
    require(original['workbook']==p['workbook'] and original['source']==p['source']
        and original['version']==Path(snapshot['root']).name,'NAT history source/version differs')
    sys.path.insert(0,snapshot['root'])
    helper=module('nat_original_history_selection',context['helper'])
    helper.select_history(p,original,probe=probe)


def select_completed_environment(p,job,D,env):
    """Carry an authenticated completed package policy into later cold stages."""
    context=p.get('completed_environment_context')
    if context is None:return
    require(set(context)=={'plan','outcome'},'closed completed environment context required')
    prior=read(context['plan']);history=p.get('history_context',{})
    require(prior['entry']==history.get('helper') and prior['original_worker_job']==history.get('original_worker_job')
        and prior['original_job']==p['original_job'] and prior['source']==p['source']
        and prior['workbook']==p['workbook'] and 'package_recovery' in prior
        and 'profile_recovery' not in prior,'completed package source/history differs')
    home=Path(prior['home'])
    require(read(binding(home/'intent.json'))==dict(plan=context['plan'],entry=prior['entry'],automatic_retry=False)
        and context['outcome']['path']==str(home/'bridge/outcome.json'),
        'completed package belongs to another continuation')
    helper=module('nat_completed_environment_policy',prior['entry'])
    q=helper.package_policy(prior,env)
    require(q is not None,'completed passive package policy required')
    release=module('nat_completed_package_release',q['release'])
    original=env[0].worker.environment.readiness.publication
    old=original._reject_external_package_artifacts
    replacements=[]
    for m in tuple(sys.modules.values()):
        namespace=getattr(m,'__dict__',None)
        if namespace is None or m is release:continue
        for key,value in tuple(namespace.items()):
            if value is old:replacements.append((namespace,key,value))
    try:
        for namespace,key,_ in replacements:namespace[key]=release._reject_external_package_artifacts
        checkpoint,value=D.successful(context['outcome'],env)
        require(checkpoint==binding(home/'bridge/result.json') and value['status']=='STEP_COMPLETE'
            and value['step']=='environment','original environment did not complete')
    except BaseException:
        for namespace,key,value in reversed(replacements):namespace[key]=value
        raise


def load(row,*,probe=False):
    p=read(row);require(set(p)-{'resume','takeover_context','history_context','completed_environment_context','lease_policy','prepermit_recovery'}==FIELDS and p['schema_version']==SCHEMA and p['entry']==binding(__file__),'closed NAT continuation required')
    require(p['authorization']['sha256']==AUTHORIZATION and binding(p['authorization']['path'])==p['authorization'],'explicit root authorization required')
    require(type(p['fence']) is int and p['fence']>0 and isinstance(p['owner'],str) and p['owner'],'explicit central identity required')
    job=read(p['original_job']);select_history(p,job,probe=probe)
    D=module('nat_original_dispatcher',job['driver']);env=D.setup(job)
    select_completed_environment(p,job,D,env)
    C,I,_,W,_,config,root,controls=env
    require(all(job[k] is True for k in ('models','commands','containers')) and job['publish'] is False
            and config['workbook']==p['workbook'] and C.SECONDS==3300 and C.MEMORY==8*1024**3,'unchanged owning capabilities required')
    output=Path(p['output']);require(output.is_absolute() and not output.is_relative_to(root),'isolated continuation namespace required')
    A=module('nat_declared_child_adapter',p['adapter']);L=module('nat_continuation_lease',p['renewal'])
    A.selected_operation_namespace(config,str(Path(config['run_root'])/'dispatcher-v2'/output.name))
    require(L.DURATION_NS==7200*10**9 and A.SCHEMA=='fleet-declared-owning-child-adapter/v1','bounded original continuation dependencies required')
    lease_options(p,L)
    from scripts.batch import harbor_nat_retry as N
    if 'takeover_context' in p:
        require('resume' not in p, 'completed import and new takeover cannot be mixed')
        D._nat_takeover_lease,D._nat_takeover_context=completed_takeover(p)
        from scripts.batch import harbor_nat_retry as N
    return p,job,D,env,A,L,N

def completed_takeover(p):
    """Select sealed original custody and its declared cold dependencies."""
    row=p['takeover_context']
    require(set(row)=={'helper','fresh_plan','completed_takeover'}, 'closed NAT takeover context required')
    U=module('nat_completed_custody_reader',row['helper'])
    selected={key:p[key] for key in ('workbook','original_job','outcome','result','source','tracker','owner')}
    selected.update(helper=row['helper'],fresh_plan=row['fresh_plan'],completed_takeover=row['completed_takeover'])
    # The existing reader independently validates the original fresh job,
    # stopped rejection, complete T transaction and exact dependency profile.
    lease,context=U.retained_lease(selected)
    try:
        from scripts.batch import harbor_nat_retry as N
        profile=read(row['completed_takeover']['dependency_profile'])
        rows=[item for item in profile['dependencies'] if item['module_name']=='scripts.batch.harbor_nat_retry']
        require(len(rows)==1 and rows[0]['selected']==binding(N.__file__), 'selected NAT custody reader is not declared')
        return lease,context
    except BaseException:
        context.manager.__exit__(*sys.exc_info());raise

def rejection_lease(p,D,original):
    return D._nat_takeover_lease if 'takeover_context' in p else original

def takeover_arguments(p):
    return dict(takeover=p['takeover_context']['completed_takeover']['completion']) if 'takeover_context' in p else {}

def custody(p,root):
    from scripts.fleet.coordinator import Client
    capacity=module('nat_capacity_policy',p['capacity_policy']);c=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']);status=c.call('status')
    capacity.validate_occupancy(status);rows=[w for w in status['workbooks'] if w['workbook']==p['workbook']]
    require(len(rows)==1 and rows[0]['state']=='active' and rows[0]['owner']==p['owner'] and rows[0]['fence']==p['fence']
            and rows[0]['version']==root.name and rows[0]['source_sha256']==p['source']['sha256'] and rows[0]['expires']>status['at']
            and abs(time.time()-status['at'])<60,'exclusive live claim required')
    require(os.environ.get('HARBOR_FLEET_OWNER')==p['owner'] and os.environ.get('HARBOR_FLEET_FENCE')==str(p['fence']), 'claim environment differs')
    require(not capacity.model_admission_barriers(status,p['workbook'],p['owner'])
            and not any(o['workbook']==p['workbook'] and o['state'] in ('active','hold') for o in status['operations']), 'target or uncertain model requires reconciliation')

def probe(row):
    p,job,D,env,A,L,N=load(row,probe=True)
    require(binding(p['tracker']['path'])==p['tracker'] and not Path(p['output']).exists(),'tracker changed or continuation consumed')
    reconcile_prepermit(p)
    D.operation_namespace(env[5],str(Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name))
    if 'resume'in p:
        outcome,guard=settled_import(p,D,env)
        checkpoint,value=D.successful(outcome,env)
        return dict(status='PASS',result=dict(workbook=p['workbook'],tracker=p['tracker'],remaining_rounds=1,limit=2,completed_import=checkpoint),models_dispatched=0,task_mutations=0)
    value,w,s,lease=completed_rejection(p,D,env)
    lease=rejection_lease(p,D,lease)
    proof=N._failure(w,s,lease,value['checkpoint'],**takeover_arguments(p))
    require(proof[1]==p['tracker'],'blocked task differs from selected tracker')
    gates=N._fresh_prefix(proof[0],s,w.context)
    require(binding(p['tracker']['path'])==p['tracker'],'tracker changed during prefix proof')
    return dict(status='PASS',result=dict(workbook=p['workbook'],tracker=p['tracker'],remaining_rounds=1,limit=2,prefix_evidence=len(gates)),models_dispatched=0,task_mutations=0)

def evidence(p):
    rows=[p[k] for k in ('original_job','outcome','result','source','capacity_policy','renewal','adapter','authorization')]+([p['resume']]if 'resume'in p else [])
    if 'takeover_context' in p:
        selected=p['takeover_context'];rows += [selected['helper'],selected['fresh_plan'],*selected['completed_takeover'].values()]
    if 'history_context' in p:rows += list(p['history_context'].values())
    if 'completed_environment_context' in p:
        context=p['completed_environment_context'];prior=read(context['plan']);policy=read(prior['package_recovery'])
        rows += list(context.values())+[prior['package_recovery']]
        rows += [v for v in policy.values() if isinstance(v,dict) and set(v)=={'path','sha256'}]
    if 'prepermit_recovery' in p:rows += list(p['prepermit_recovery'].values())
    return rows

def lease_options(p,L):
    if 'lease_policy' not in p:return {}
    require(p['lease_policy']==dict(mode='retain-sufficient-or-renew',minimum_seconds=7200)
        and 'retain_sufficient' in inspect.signature(L.renew).parameters,
        'unsupported bounded continuation lease policy')
    return dict(retain_sufficient=True)

def reconcile_prepermit(p):
    """An exact rejected non-extending renewal is not an uncertain model call."""
    context=p.get('prepermit_recovery')
    if context is None:return
    require(set(context)=={'prior_plan','guard','renewal_intent'},'closed prepermit reconciliation required')
    prior=read(context['prior_plan']);home=Path(prior['output'])
    require('prepermit_recovery' not in prior and 'resume' not in p and 'takeover_context' not in p
        and prior=={**{k:v for k,v in p.items()if k not in ('prepermit_recovery','lease_policy')},
            **{k:prior[k]for k in ('entry','owner','fence','output','renewal')}}
        and p['fence']>prior['fence'] and p['output']!=prior['output'],
        'prepermit recovery changed task history')
    require(binding(prior['entry']['path'])==prior['entry']
        and binding(p['tracker']['path'])==prior['tracker'],'prepermit tracker or original code changed')
    guard=read(context['guard'])
    require(context['guard']==binding(home/'permit-resource.json')
        and guard['returncode']==1 and guard['failure'] is None and guard['models']==0
        and guard['seconds']==3300 and guard['memory_bytes']==8*1024**3
        and read(guard['intent'])==dict(plan=context['prior_plan'],entry=prior['entry'],automatic_retry=False),
        'prepermit failure is not the exact bounded guard')
    require(binding(guard['log']['path'])==guard['log'] and
        Path(guard['log']['path']).read_text().endswith('scripts.batch.harbor_tracker.Conflict: renewal must extend expiry\n'),
        'different renewal failure needs separate reconciliation')
    intent=read(context['renewal_intent'])
    require(context['renewal_intent']==binding(home/'renewal/intent.json')
        and intent['authority']==context['prior_plan'] and intent['tracker']==p['tracker']
        and intent['duration_ns']==7200*10**9 and intent['automatic_retry'] is False,
        'failed renewal belongs to another attempt')
    require(not (home/'renewal/complete.json').exists() and not(home/'permit-complete.json').exists()
        and not(home/'import').exists(),'renewal or permit already completed; do not repeat')

def settled_import(p,D,env):
    """Retain the completed retry CAS/import after a pre-owning loader failure."""
    r=read(p['resume']);require(set(r)=={'schema_version','prior_plan','permit','guard','import_outcome','failed_outcome'}
        and r['schema_version']=='fleet-naturalization-settled-import/v1','closed settled NAT import required')
    prior=read(r['prior_plan']);require(prior['schema_version']==SCHEMA and 'resume'not in prior
        and all(prior[k]==p[k]for k in ('original_job','outcome','result','source','workbook','authorization','renewal','capacity_policy'))
        and prior['output']!=p['output'],'original retry lineage differs')
    require(binding(prior['entry']['path'])==prior['entry'],'original retry implementation changed')
    home=Path(prior['output']);permit=read(r['permit']);guard=read(r['guard'])
    require(r['permit']==binding(home/'permit-complete.json') and r['guard']==binding(home/'permit-resource.json')
        and permit['plan']==r['prior_plan'] and permit['models']==0 and permit['semantic_budget_reset']is False
        and permit['automatic_retry']is False and guard['returncode']==0 and guard['failure']is None
        and guard['seconds']==3300 and guard['memory_bytes']==8*1024**3 and guard['models']==0,
        'actual guarded retry authorization required')
    require(read(guard['intent'])==dict(plan=r['prior_plan'],entry=prior['entry'],automatic_retry=False),'retry guard identity differs')
    require(binding(guard['log']['path'])==guard['log'] and binding(permit['completion']['path'])==permit['completion'],'retry evidence changed')
    checkpoint,value=D.successful(r['import_outcome'],env)
    require(Path(r['import_outcome']['path']).parent==home/'import' and value['status']=='RETRY_READY'
        and value['next_step']=='nat-writer','exact remaining NAT writer boundary required')
    import_intent=read(binding(home/'import/intent.json'))
    require(import_intent['action']=='import-retry' and import_intent['arguments']['retry_completion']==permit['completion']
        and import_intent['arguments']['checkpoint']==p['result'],'retry import detached from completion')
    failed=read(r['failed_outcome']);directory=Path(r['failed_outcome']['path']).parent
    require(directory==Path(env[5]['run_root'])/'dispatcher-v2'/home.name/'000'
        and failed['status']=='RECONCILIATION_REQUIRED' and failed.get('result')is None
        and failed['automatic_retry']is False and failed['resource']==binding(directory/'resource.json'),'exact stopped first child required')
    resource=read(failed['resource']);intent=read(resource['intent'])
    require(resource['returncode']==1 and resource['failure']is None and resource['intent']==binding(directory/'intent.json')
        and resource['log']==binding(directory/'step.log') and not(directory/'result.json').exists()
        and intent['action']=='step' and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint),
        'failed child differs from completed retry import')
    log=Path(resource['log']['path']).read_text()
    require(log.endswith('ValueError: dispatcher namespace exists; no replay\n') and 'in load\n'in log
        and prior['entry']['path']in log,'failure was not the retained pre-owning namespace check')
    return r['import_outcome'],r['guard']

def install(row,p,D,env,A):
    C,I=env[:2]
    I.run=A.select_run(I,base=C,kind='instruction',reader_plan=row,child_wrapper=p['entry'],producer_code=binding(I.__file__),reader_evidence=evidence(p))
    D.renew=A.select_run(D,base=C,kind='renewal',reader_plan=row,child_wrapper=p['entry'],producer_code=binding(D.__file__),reader_evidence=evidence(p))
    original=D.setup;job=read(p['original_job']);ops=str(Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name)
    def setup(current):
        require(current==dict(job,outcome=current['outcome'],operations=ops),'ordinary continuation job changed')
        return env
    D.setup=setup

def owning_child(row,kind,intent):
    p,job,D,env,A,L,N=load(row);custody(p,env[6]);C,I=env[:2];producer=I if kind=='instruction' else D
    require(kind in ('instruction','renewal'),'unsupported NAT child')
    A.validate_child(producer,base=C,kind=kind,reader_plan=row,child_wrapper=p['entry'],intent=intent,reader_evidence=evidence(p))
    return I.child(intent) if kind=='instruction' else D.renewal_child(intent)

def permit_child(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot authorize retry')
    p,job,D,env,A,L,N=load(row);require('resume'not in p,'settled retry import cannot authorize another retry')
    C,I=env[:2];value,w,s,lease=completed_rejection(p,D,env);out=Path(p['output']);custody(p,env[6])
    require(read(binding(out/'intent.json'))==dict(plan=row,entry=p['entry'],automatic_retry=False),'outer continuation intent changed')
    def validation(before,current):
        proof=N._failure(w,s,current,value['checkpoint']);require(proof[0]==before,'rejection tracker changed');return [proof[1],value['checkpoint']]
    if 'takeover_context' in p:
        lease=rejection_lease(p,D,lease);renewal=None
        require(binding(p['tracker']['path'])==p['tracker'], 'takeover tracker changed before NAT authorization')
    else:
        with (w.run/'worker.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            lease,renewal=L.renew(life=N.lifecycle,read=read,record=binding,keep=keep,path=p['tracker']['path'],lease=lease,
                authority_record=row,validation=validation,custody=lambda:custody(p,env[6]),directory=out/'renewal',**lease_options(p,L))
    custody(p,env[6]);w,s,lease,completion=N.authorize(w,s,lease,blocked=value['checkpoint'],**takeover_arguments(p))
    return keep(out/'permit-complete.json',dict(plan=row,completion=completion,renewal=renewal,models=0,semantic_budget_reset=False,automatic_retry=False))

def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot run')
    p,job,D,env,A,L,N=load(row);C,I=env[:2];out=Path(p['output']);custody(p,env[6])
    require(not out.exists() and binding(p['tracker']['path'])==p['tracker'],'consumed continuation or changed tracker')
    reconcile_prepermit(p)
    if 'resume'in p:
        outcome,guard=settled_import(p,D,env)
        out.mkdir(parents=True);keep(out/'intent.json',dict(plan=row,entry=p['entry'],completed_import=outcome,automatic_retry=False))
        return remaining(row,p,job,D,env,A,outcome,guard)
    value,w,s,lease=completed_rejection(p,D,env)
    out.mkdir(parents=True);intent=keep(out/'intent.json',dict(plan=row,entry=p['entry'],automatic_retry=False));command=[sys.executable,'-I','-B',p['entry']['path'],'permit-child','--plan',row['path'],'--plan-sha256',row['sha256']]
    metrics={};rc=None;failure=None;start=time.monotonic()
    try:
        with (out/'permit.log').open('xb') as log:
            rc=C._run_in_process_group(command,stdout=log,timeout_seconds=3300,grace_seconds=3,memory_limit_bytes=C.MEMORY,metrics=metrics,cwd=env[6],env=os.environ.copy())
    except BaseException as exc:failure=type(exc).__name__
    guard=keep(out/'permit-resource.json',dict(intent=intent,command=command,returncode=rc,failure=failure,elapsed_seconds=time.monotonic()-start,metrics=metrics,log=binding(out/'permit.log'),seconds=3300,memory_bytes=C.MEMORY,models=0,automatic_retry=False))
    require(rc==0 and failure is None,'NAT permit requires reconciliation; never replay')
    receipt=read(binding(out/'permit-complete.json'));require(receipt['plan']==row,'retry completion detached')
    install(row,p,D,env,A);custody(p,env[6])
    outcome=I.run('import-retry',dict(descriptor=value['descriptor'],checkpoint=p['result'],retry_completion=receipt['completion']),env[7],out/'import')
    return remaining(row,p,job,D,env,A,outcome,guard,installed=True)

def remaining(row,p,job,D,env,A,outcome,guard,*,installed=False):
    if not installed:install(row,p,D,env,A)
    custody(p,env[6]);out=Path(p['output'])
    D.successful(outcome,env);ops=str(Path(env[5]['run_root'])/'dispatcher-v2'/out.name)
    current=keep(out/'authoring-job.json',dict(job,outcome=outcome,operations=ops));D.run(current)
    held=read(binding(Path(ops)/'hold.json'));last,result=D.successful(held['outcome'])
    require(result['status']=='PUBLICATION_READY','remaining NAT authoring reached a diagnosed hold')
    return keep(out/'result.json',dict(status='PUBLICATION_READY',workbook=p['workbook'],job=current,config=job['config'],outcome=held['outcome'],checkpoint=last,continuation=row,permit_guard=guard,publication_performed=False,automatic_retry=False))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=('probe','run','permit-child','owning-child'));a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True);a.add_argument('--kind');a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=v.plan,sha256=v.plan_sha256)
    print(json.dumps(owning_child(row,v.kind,dict(path=v.intent,sha256=v.intent_sha256)) if v.action=='owning-child' else {'probe':probe,'run':run,'permit-child':permit_child}[v.action](row)))
