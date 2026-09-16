"""Resume a stopped emission after completed partition reviews.

The original partition producer, environment refresh and authoring dispatcher
remain the owning implementations. A declared generator function correction
is carried through every guarded child; completed model calls are never run.
"""
from pathlib import Path
from contextlib import contextmanager
from dataclasses import asdict, replace
from types import FunctionType, ModuleType, SimpleNamespace
import argparse, ast, hashlib, importlib.util, json, os, sys

SCHEMA='fleet-normalization-remaining/v1'
FIELDS={'schema_version','helper','previous','previous_helper','prepared','failed_guard',
        'prior_outcome','stopped','runtime','runtime_profile','campaign_adapter',
        'takeover_plan','completion','workbook','source','owner','fence','home','tracker'}
MEMORY=8*1024**3
SECONDS=3300
ORDINARY_FIELDS=(FIELDS-{'previous','previous_helper','failed_guard'})|{'ordinary_author'}
PARTIAL_FIELDS=ORDINARY_FIELDS|{'partial_emission','partial_helper'}
COMPLETED_FIELDS=PARTIAL_FIELDS|{'preexecution','completed_emission','completed_helper'}
SINGLE_FIELDS=(ORDINARY_FIELDS-{'ordinary_author'})|{'single_author','single_helper','memory_policy'}


def require(ok,message):
    if not ok:raise ValueError(message)


def binding(path):
    p=Path(path);require(p.is_absolute() and p.is_file() and not any(x.is_symlink()for x in(p,*p.parents)),
                       'canonical normalization continuation file required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def read(row):
    require(set(row)=={'path','sha256'}and binding(row['path'])==row,'normalization continuation bytes changed')
    return json.loads(Path(row['path']).read_bytes())


def module(name,row):
    require(binding(row['path'])==row,'normalization continuation implementation changed')
    s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s)
    sys.modules[name]=m;s.loader.exec_module(m);return m


def keep(path,value):
    p=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    require(not any(x.is_symlink()for x in(p,*p.parents)),'unsafe continuation receipt')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return binding(p)


def generator_delta(profile,root,helper):
    p=read(profile)
    require(p['runtime']==helper and not any(p.get(k)for k in
        ('patches','dependencies','factories','historical_imports','guard_modules','child_plans','journal_roots'))
        and len(p['symbols']) in (1,2,3),'only explicit emission function corrections are supported')
    allowed=[('gen_normalizer','coalesce_same_atom_aliases','gen_normalizer.py'),
             ('mcp_env.build','source_id','mcp_env/build.py'),
             ('scripts.batch.harbor_environment_refresh','unchanged','scripts/batch/harbor_environment_refresh.py')]
    for s,(name,function,path) in zip(p['symbols'],allowed):
        require(s['module_name']==name and s['names']==[function]
            and s['original']==binding(Path(root)/path),'selected generator differs')
        trees=[]
        for row in (s['original'],s['selected']):
            require(binding(row['path'])==row,'generator correction bytes changed')
            t=ast.parse(Path(row['path']).read_text());found=[n for n in t.body if isinstance(n,ast.FunctionDef)and n.name==function]
            require(len(found)==1,'one generator function required');t.body.remove(found[0]);trees.append(ast.dump(t))
        require(trees[0]==trees[1],'generator changed beyond the declared alias function or source identity function')
    return p['symbols'][0]


def stopped_emission(p,old,M,P):
    g=read(p['failed_guard']);i=read(g['intent']);outer=read(p['prior_outcome'])
    wi=read(outer['intent']);job=read(wi['job']);stop=read(p['stopped'])
    require(p['failed_guard']==binding(Path(old['home'])/'stages/emit/resource.json')
        and g['returncode']==1 and g['failure_type']is None and g['log']==binding(Path(g['intent']['path']).parent/'step.log')
        and i['action']=='emit'and i['arguments']==dict(prepared=p['prepared'])
        and i['plan']==p['previous']and i['driver']==p['previous_helper']
        and i['seconds']==SECONDS and i['memory']==MEMORY,'exact original failed emission required')
    result=read(outer['result'])
    require(outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and job['driver']==p['previous_helper']
        and job['source']==p['source']and job['workbook']==p['workbook']
        and job['result']==outer['result']['path']==str(Path(old['home'])/'result.json')
        and result==dict(status='RECONCILIATION_REQUIRED',workbook=p['workbook'],plan=p['previous'],
            failure_type='ValueError',publication_performed=False,automatic_retry=False),'stopped owning emission differs')
    require(set(stop)=={'at','claims','files','models','processes'}and stop['processes']==[]
        and stop['claims']==stop['models']==0,'old normalization worker remains active')
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(all(inventory.get(r['path'])==r['sha256']for r in(p['failed_guard'],p['prior_outcome'])),
            'stopped survey omits actual failed operation')
    for item in stop['files']:
        require(binding(item['native']['path'])==item['native']and binding(item['copy']['path'])==item['copy']
            and item['native']['sha256']==item['copy']['sha256'],'stopped normalization evidence changed')
    d=Path(old['home']);s=P.selection(p['prepared'])
    require(not any((d/n).exists()for n in('refresh','bridge-step','authoring-job.json'))
        and not any((s.run/n).exists()for n in('phase1-result.json','normalized.phase1.json',
             'normalized.json','normalization_report.json','exclusions.json',f'normalize_{p["workbook"]}.py')),
        'emission or later task effects already exist; reconcile before continuation')
    preparation=read(binding(d/'stages/prepare/result.json'))
    require(preparation['value']==p['prepared'],'completed preparation changed')
    # Full original aggregate verifies every actual model response and complete
    # partition coverage. No dispatch or attempt allocator is used here.
    proof=P.aggregate(p['prepared'])
    require(read(binding(s.run/'normalization-review.json'))==proof['review'],
            'retained review differs from completed original calls')
    return proof


def ordinary_partition_prefix(D,env,job,o,research):
    """Authenticate each original checkpoint as partition permits advance the lease."""
    base=env[0];cursor=o['checkpoint'];chain=[];seen=set()
    while cursor!=o['research']:
        require(cursor['path'] not in seen and len(chain)<job['maximum_steps'],
            'ordinary partition checkpoint chain repeats or exceeds original bound')
        seen.add(cursor['path']);directory=Path(cursor['path']).parent
        require(directory.parent==Path(job['operations']) and directory.name.isdigit(),
            'ordinary partition checkpoint left its original dispatcher')
        outcome=binding(directory/'outcome.json');cp,result=D.successful(outcome,env)
        require(cp==cursor and result['descriptor']==research['descriptor']
            and result['step']=='normalization_phase1'
            and result['status']==('UNRESOLVED' if not chain else 'PENDING_MODEL'),
            'ordinary partition chain contains another stage or outcome')
        value=read(outcome);resource=read(value['resource']);intent=read(resource['intent'])
        require(intent['action']=='step' and intent['arguments']['descriptor']==research['descriptor'],
            'ordinary partition chain contains another operation')
        prior=intent['arguments']['checkpoint'];read(prior)
        require(Path(prior['path']).parent.parent==directory.parent
            and Path(prior['path']).parent.name.isdigit()
            and int(Path(prior['path']).parent.name)+1==int(directory.name),
            'ordinary partition checkpoints are not consecutive')
        chain.append(result);cursor=prior
    prior=base.decode(research['lease'])
    for result in reversed(chain):
        current=base.decode(result['lease'])
        require((current.owner,current.token)==(prior.owner,prior.token)
            and current.revision>=prior.revision,'ordinary partition lease identity or revision regressed')
        prior=current
    return prior


def ordinary_author(p):
    """Read a completed ordinary dispatcher failure without inventing a prior repair."""
    o=read(p['ordinary_author'])
    require(set(o)=={'schema_version','job','outcome','checkpoint','research','unresolved'}
        and o['schema_version']=='fleet-normalization-ordinary-author/v1','closed ordinary author context required')
    job=read(o['job']);D=module('remaining_ordinary_dispatcher',job['driver']);env=D.setup(job)
    base=env[0];cfg=env[5];root=Path(env[6])
    checkpoint,result=D.successful(o['outcome'],env)
    require(checkpoint==o['checkpoint'] and result['status']=='UNRESOLVED'
        and result['step']=='normalization_phase1' and result['publication_ready']is False
        and o['unresolved'] in result['evidence'],'ordinary author did not stop at normalization emission')
    error=read(o['unresolved'])
    require(error==dict(status='UNRESOLVED',step='normalization_phase1',error_type='ValueError',
        error_sha256=hashlib.sha256(b'same physical atom has conflicting semantic dimensions/value/question/notes; diagnosis required').hexdigest(),
        automatic_retry=False,source_invalidity_claimed=False),'different ordinary normalization failure')
    research,previous=base._prior(o['research'],result['descriptor'])
    require(research['status']=='STEP_COMPLETE' and research['step']=='research'
        and cfg['workbook']==p['workbook'],'ordinary research predecessor differs')
    w,_,controls=base.selected(result['descriptor'])
    require(controls==env[-1] and w.bootstrap.workbook==p['workbook'],
        'normalization controls or workbook changed')
    previous=ordinary_partition_prefix(D,env,job,o,research)
    from scripts.batch import harbor_normalization_partitioned as P
    require(Path(P.__file__).resolve().is_relative_to(root),'ordinary partition validator is foreign')
    selected=P.selection(p['prepared'])
    require(selected.workbook==p['workbook'] and selected.bindings['original_raw_source']==p['source']
        and selected.run.parent==w.run/'normalization','ordinary partition source or run differs')
    original=base.content_codec.decode(research['selection']).content
    require(all(selected.bindings.get(k)==v for k,v in original.bindings.items()),
        'ordinary partitions detached from original research')
    require(Path(o['checkpoint']['path']).parent.parent==Path(job['operations'])
        and Path(p['home']).is_absolute() and Path(p['home']).parent==Path(job['operations']).parent
        and Path(p['home'])!=Path(job['operations']) and not Path(p['home']).is_relative_to(w.run)
        and not w.run.is_relative_to(Path(p['home'])),'ordinary continuation namespace overlaps original task')
    return o,job,D,env,P,w,base.decode(result['lease'])


def load(p):
    """Closed read-only entry for the declared generator runtime profile."""
    if 'single_author'in p:
        require(set(p)=={'single_author','single_helper','workbook','source','prepared','home'},'closed single review read context required')
        S=module('remaining_single_reader',p['single_helper']);return S.original(SimpleNamespace(**globals()),p)
    require(set(p)=={'ordinary_author','workbook','source','prepared','home'},
        'closed ordinary normalization read context required')
    return ordinary_author(p)


def ordinary_stopped(p,o,job,P):
    outer=read(p['prior_outcome']);intent=read(outer['intent']);worker=read(intent['job'])
    result=read(outer['result']);stop=read(p['stopped'])
    require(outer['status']=='RECONCILIATION_REQUIRED' and outer['returncode']==0
        and outer['failure_type']is None and outer['automatic_retry']is False
        and outer['publication_performed']is False and worker['source']==p['source']
        and worker['workbook']==p['workbook'] and worker['result']==outer['result']['path']
        and result['status']=='UNRESOLVED' and result['workbook']==p['workbook']
        and result['job']==o['job'] and result['outcome']==o['outcome']
        and result['checkpoint']==o['checkpoint'] and result['automatic_retry']is False
        and result['publication_performed']is False,'ordinary outer outcome detached from completed failed step')
    require(set(stop)=={'at','claims','files','models','processes'} and stop['processes']==[]
        and stop['claims']==stop['models']==0,'ordinary author is not stopped')
    inventory={x['native']['path']:x['native']['sha256']for x in stop['files']}
    require(len(inventory)==len(stop['files']) and all(inventory.get(r['path'])==r['sha256']for r in
        (p['prior_outcome'],outer['intent'],intent['job'],outer['result'],o['job'],o['outcome'],o['checkpoint'],o['unresolved'])),
        'ordinary stopped survey omits owning evidence')
    for item in stop['files']:
        require(binding(item['native']['path'])==item['native'] and binding(item['copy']['path'])==item['copy']
            and item['native']['sha256']==item['copy']['sha256'],'ordinary stopped custody changed')
    run=P.selection(p['prepared']).run
    require('partial_emission'in p or not any((run/n).exists()for n in ('phase1-result.json','normalized.phase1.json','normalized.json',
        'normalization_report.json','exclusions.json',f'normalize_{p["workbook"]}.py','source_profiles.json')),
        'ordinary emission or downstream effect already exists')
    proof=P.aggregate(p['prepared'])
    require(read(binding(run/'normalization-review.json'))==proof['review'],
        'ordinary retained review differs from actual completed calls')
    return proof


@contextmanager
def ordinary_context(p,*,initial):
    o,job,D,env,P,w,previous=ordinary_author(p)
    root=str(env[6]);RT=module('remaining_generator_runtime',p['runtime'])
    generator_delta(p['runtime_profile'],root,p['runtime'])
    if initial and 'completed_emission'not in p:ordinary_stopped(p,o,job,P)
    tp=read(p['takeover_plan']);T=module('remaining_completed_takeover',tp['shared']['takeover'])
    T.life=module('remaining_completed_lifecycle',tp['shared']['lifecycle'])
    before,after,lease=T.validate_completion(p['completion']);done=read(p['completion']);stop=read(done['stopped_owner_proof'])
    ordinary_takeover_config(job,tp)
    require(T._selection(tp['selection'])==T._selection(stop['selection'])
        and tp['selection']['checkpoint']==o['checkpoint']
        and tp['selection']['worker_kind']=='environment' and tp['selection']['workbook']==p['workbook']
        and tp['original_snapshot']==read(job['config'])['code']
        and stop['before']['sha256']==previous.sha256 and before['revision']==previous.revision
        and (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(previous.owner,previous.token)
        and p['tracker']==dict(path=tp['selection']['tracker']['path'],sha256=done['after']['sha256'])
        and after['bindings']['original_raw_source']==p['source'] and lease.owner==p['owner'],
        'ordinary takeover detached from stopped source, checkpoint or owner')
    old=dict(code_root=root,research=o['research'],original_job=o['job'])
    A=module('remaining_campaign_adapter',p['campaign_adapter'])
    with RT.installed(p['runtime_profile']):
        if initial and 'completed_emission'in p:
            completed=module('remaining_completed_emission',p['completed_helper']);completed.validate(p,P,env[0].encode(lease))
        elif initial and 'partial_emission'in p:
            partial=module('remaining_partial_emission',p['partial_helper']);partial.validate(p,P,env[0].encode(lease))
        yield p,old,None,D,env,P,w,lease,A


def ordinary_takeover_config(job,tp):
    """The original lease recipe uses canonical custody, not intake placement."""
    current=read(job['config']);selected=tp['selection']['config'];original=read(selected)
    if selected==job['config']:return
    require(selected==binding(Path(current['run_root'])/'config.json')
        and original==dict(current,intake=original.get('intake'))
        and set(original)==set(current) and 'intake'in original,
        'canonical takeover config changed beyond original intake placement')
    # The full original D.setup already authenticated the active intake. Retain
    # the canonical intake bytes too; neither is substituted for the other.
    read(original['intake']);read(current['intake'])


@contextmanager
def context(row,*,initial=False):
    p=read(row);require(set(p) in (FIELDS,ORDINARY_FIELDS,PARTIAL_FIELDS,PARTIAL_FIELDS|{'preexecution'},COMPLETED_FIELDS,SINGLE_FIELDS,SINGLE_FIELDS|{'completed_emission','completed_helper'},SINGLE_FIELDS|{'completed_emission','completed_helper','prepared_refresh','prepared_helper'}) and p['schema_version']==SCHEMA and p['helper']==binding(__file__),
                       'closed remaining normalization continuation required')
    if 'single_author' in p:
        S=module('remaining_single_review',p['single_helper'])
        with S.context(SimpleNamespace(**globals()),p,initial=initial) as values:yield values
        return
    if 'ordinary_author' in p:
        with ordinary_context(p,initial=initial) as values:yield values
        return
    old=read(p['previous']);M=module('remaining_original_normalization',p['previous_helper'])
    D,env,P,selected,w=M.load(old)
    require(old['workbook']==p['workbook']and selected.bindings['original_raw_source']==p['source']
        and Path(p['home']).parent==Path(old['home']).parent and p['home']!=old['home']
        and type(p['fence'])is int and p['fence']>0,'remaining normalization identity differs')
    RT=module('remaining_generator_runtime',p['runtime']);generator_delta(p['runtime_profile'],old['code_root'],p['runtime'])
    if initial:stopped_emission(p,old,M,P)
    tp=read(p['takeover_plan']);T=module('remaining_completed_takeover',tp['shared']['takeover'])
    T.life=module('remaining_completed_lifecycle',tp['shared']['lifecycle'])
    before,after,lease=T.validate_completion(p['completion']);done=read(p['completion'])
    stop=read(done['stopped_owner_proof'])
    previous=M.initial_lease(old,env[0])
    require(T._selection(tp['selection'])==T._selection(stop['selection'])and tp['selection']['workbook']==p['workbook']
        and tp['selection']['prior_completion']==old['lease_continuation']['completion']
        and stop['before']['sha256']==previous.sha256 and before['revision']==previous.revision
        and (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(previous.owner,previous.token)
        and p['tracker']==dict(path=tp['selection']['tracker']['path'],sha256=done['after']['sha256'])
        and after['bindings']['original_raw_source']==p['source'],'completed takeover detached from retained emission')
    A=module('remaining_campaign_adapter',p['campaign_adapter'])
    with RT.installed(p['runtime_profile']):
        yield p,old,M,D,env,P,w,lease,A


def active(p):
    from scripts.fleet.coordinator import Client
    require(os.environ.get('HARBOR_FLEET_WORKBOOK')==p['workbook']
        and os.environ.get('HARBOR_FLEET_OWNER')==p['owner']
        and int(os.environ['HARBOR_FLEET_FENCE'])==p['fence'],'live remaining normalization claim required')
    return Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',
        workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)


def evidence(p):
    original=('single_author','single_helper','memory_policy')if 'single_author'in p else ('ordinary_author',) if 'ordinary_author'in p else ('previous','previous_helper')
    if 'partial_emission'in p:original+=('partial_emission','partial_helper')
    if 'preexecution'in p:original+=('preexecution',)
    if 'completed_emission'in p:original+=('completed_emission','completed_helper')
    if 'prepared_refresh'in p:original+=('prepared_refresh','prepared_helper')
    return [p[k]for k in (*original,'runtime','runtime_profile','campaign_adapter','takeover_plan','completion','stopped')]


def selected_dispatcher(D,env,A,row,p):
    base,I=env[:2];common=dict(base=base,reader_plan=row,child_wrapper=p['helper'],reader_evidence=evidence(p))
    b=SimpleNamespace(**base.__dict__);b.run=A.select_run(base,kind='environment',producer_code=binding(base.__file__),**common)
    i=SimpleNamespace(**I.__dict__);i.run=A.select_run(I,kind='instruction',producer_code=binding(I.__file__),**common)
    b._run_in_process_group=A.select_run(D,kind='renewal',producer_code=binding(D.__file__),**common).declared_runner
    clone=ModuleType(D.__name__);clone.__dict__.update(D.__dict__)
    for name,v in D.__dict__.items():
        if isinstance(v,FunctionType)and v.__globals__ is D.__dict__:
            f=FunctionType(v.__code__,clone.__dict__,v.__name__,v.__defaults__,v.__closure__);f.__kwdefaults__=v.__kwdefaults__;clone.__dict__[name]=f
    def setup(job):
        current=D.setup(job);require(current[:2]==env[:2],'owning dispatcher modules changed')
        return b,i,*current[2:]
    clone.setup=setup
    return clone,(b,i,*env[2:])


def dispatcher_directory(config,home):
    """Keep the continuation journal separate from the original dispatcher."""
    home=Path(home);base=Path(config['run_root'])/'dispatcher-v2'
    selected=base/(home.name+'-authoring'if home.parent==base else home.name)
    require(selected!=home and not selected.exists(),
        'fresh distinct original dispatcher namespace required')
    return selected


def owning_child(row,kind,intent):
    with context(row)as(p,old,M,D,env,P,w,lease,A):
        active(p);producer={'environment':env[0],'instruction':env[1],'renewal':D}[kind]
        A.validate_child(producer,base=env[0],kind=kind,reader_plan=row,child_wrapper=p['helper'],intent=intent,reader_evidence=evidence(p))
        return producer.renewal_child(intent)if kind=='renewal'else producer.child(intent)


def emission_memory(p):
    return 12*1024**3 if 'single_author'in p else MEMORY


def emit_child(row,intent):
    with context(row,initial=True)as(p,old,M,D,env,P,w,lease,A):
        active(p);v=read(intent);directory=Path(p['home'])/'emit'
        require(v==dict(plan=row,helper=p['helper'],prepared=p['prepared'],lease=env[0].encode(lease),seconds=SECONDS,memory=emission_memory(p))
            and intent==binding(directory/'intent.json'),'emission guard changed')
        env[0].worker._active(w,lease)
        emitted=P.emit_phase1(p['prepared'])
        return keep(directory/'result.json',dict(emitted=emitted,lease=env[0].encode(lease),runtime=p['runtime_profile'],models_dispatched=0))


def run(row):
    with context(row,initial=True)as(p,old,M,D,env,P,w,lease,A):
        active(p);require(binding(p['tracker']['path'])==p['tracker']and not Path(p['home']).exists(),'initial custody changed or continuation consumed')
        operations=dispatcher_directory(env[5],p['home'])
        D.operation_namespace(env[5],str(operations))
        env[0].worker._active(w,lease)
        import shutil
        require(shutil.disk_usage(old['code_root']).free>=MEMORY,'8-GiB native reserve required')
        home=Path(p['home']);home.mkdir(mode=0o700);keep(home/'intent.json',dict(plan=row,automatic_retry=False))
        if 'completed_emission'in p:
            completed=module('remaining_completed_emission',p['completed_helper']);emitted=completed.result(p)
        else:
            d=home/'emit';d.mkdir();intent=keep(d/'intent.json',dict(plan=row,helper=p['helper'],prepared=p['prepared'],lease=env[0].encode(lease),seconds=SECONDS,memory=emission_memory(p)))
            metrics={};rc=None;failure=None
            from scripts.batch.hardened_runner import _run_in_process_group
            try:
                with (d/'step.log').open('xb')as log:
                    rc=_run_in_process_group([sys.executable,'-I','-B',__file__,'emit-child','--plan',row['path'],'--plan-sha256',row['sha256'],
                        '--intent',intent['path'],'--intent-sha256',intent['sha256']],stdout=log,timeout_seconds=SECONDS,grace_seconds=3,
                        memory_limit_bytes=emission_memory(p),metrics=metrics,cwd=Path(old['code_root']),env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            except Exception as exc:failure=type(exc).__name__
            keep(d/'resource.json',dict(intent=intent,returncode=rc,failure_type=failure,metrics=metrics,log=binding(d/'step.log'),automatic_retry=False))
            require(rc==0 and failure is None,'remaining emission stopped; reconcile without replay')
            emitted=read(read(binding(d/'result.json'))['emitted']);require(emitted['status']=='phase1_evidence_ready','emission did not qualify')
        D,selected_env=selected_dispatcher(D,env,A,row,p);base=selected_env[0]
        prior=base.content_codec.decode(read(old['research'])['selection']);content=prior.content;r=P.selection(p['prepared']).run
        b={**content.bindings,
            'normalization_draft':binding(r/'draft.json'),'normalizer':binding(r/f'normalize_{p["workbook"]}.py'),
            'normalization_prepared_manifest':p['prepared'],'normalized_phase1':emitted['phase1'],
            'normalized_final':binding(r/'normalized.json'),'normalization_report':binding(r/'normalization_report.json'),
            'normalization_exclusions':binding(r/'exclusions.json')}
        if 'single_author'in p:b['normalization_prepared_request']=read(p['prepared'])['request']
        else:b['normalization_partitioned_review']=emitted['normalization_partitioned_review']
        current=replace(prior,content=replace(content,bindings=b,normalization_run=r,normalization_prepared=p['prepared'],normalization_call=P.call if 'single_author'in p else None))
        encoded=base.encode(lease);legacy=keep(home/'legacy.json',dict(worker=base.worker.bootstrap.serial(asdict(w)),lease=encoded))
        args=dict(legacy=legacy,checkpoint=old['research'],current=keep(home/'current.json',base.content_codec.encode(current)),lease=keep(home/'lease.json',encoded))
        outcome=base.run('refresh',args,env[-1],home/'refresh');value=read(outcome)
        require(value['status']=='COMPLETED_OPERATION','refresh requires reconciliation')
        cp=value['result'];v=read(cp);require(v['status']=='IMPORTED','refresh did not import')
        outcome=base.run('step',dict(descriptor=v['descriptor'],checkpoint=cp,captures=None),env[-1],home/'bridge-step',dispatch=True)
        D.successful(outcome,selected_env);job=M.successor(old,read(old['original_job']))if 'terminal_rejection'in old else read(old['original_job'])
        job=dict(job,outcome=outcome,operations=str(operations))
        jb=keep(home/'authoring-job.json',job);D.run(jb);hold=read(binding(Path(job['operations'])/'hold.json'))
        cp,result=D.successful(hold['outcome'],selected_env)
        return keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],plan=row,job=jb,config=job['config'],
            outcome=hold['outcome'],checkpoint=cp,runtime=p['runtime_profile'],publication_performed=False,automatic_retry=False))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['run','emit-child','owning-child']);a.add_argument('--plan',required=True);a.add_argument('--plan-sha256',required=True)
    a.add_argument('--kind',choices=['environment','instruction','renewal']);a.add_argument('--intent');a.add_argument('--intent-sha256');v=a.parse_args();row=dict(path=v.plan,sha256=v.plan_sha256)
    if v.action=='run':result=run(row)
    elif v.action=='emit-child':result=emit_child(row,dict(path=v.intent,sha256=v.intent_sha256))
    else:result=owning_child(row,v.kind,dict(path=v.intent,sha256=v.intent_sha256))
    print(json.dumps(result))
