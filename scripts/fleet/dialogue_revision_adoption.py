"""Declared zero-model adoption and original instruction tail for source derivatives.

The source correction, old author pairs and every review remain separately named.
Original instruction guards own runtime, lane commit, and enqueue. No author or
review transport is available through this entrypoint.
"""
from __future__ import annotations
import inspect,ast,argparse,fcntl,hashlib,importlib.util,json,os,sys,time
from dataclasses import asdict,replace
from pathlib import Path
from types import FunctionType,SimpleNamespace
from contextlib import contextmanager,ExitStack
SCHEMA='fleet-dialogue-source-revision-adoption/v1'

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(p):
    p=Path(p);require(p.is_absolute()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical adoption evidence required')
    return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(b):
    require(binding(b['path'])==b,'adoption evidence changed');return json.loads(Path(b['path']).read_bytes())

def semantic_claims(args,q,selection,*,retained):
    """Retain the original worker's authenticated copy of reviewed claims."""
    require(args['reviewed_claims']==q['claims'],'derivative reviewed claims changed')
    if args['claims']==q['claims']:return True
    require(retained and args['claims']==selection.content.dialogue_claims
        and args['claims']['sha256']==q['claims']['sha256'], 'derivative current claims identity changed')
    require(binding(args['claims']['path'])==args['claims']and binding(q['claims']['path'])==q['claims']
        and Path(args['claims']['path']).read_bytes()==Path(q['claims']['path']).read_bytes(),
        'original current and reviewed claims bytes differ')
    return True

def module(name,b):
    require(binding(b['path'])==b,'adoption dependency changed');s=importlib.util.spec_from_file_location(name,b['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def keep(p,v):
    with Path(p).open('x')as f:json.dump(v,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(Path(p).parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return binding(p)

def namespace(original):
    values=dict(original.__dict__)
    prior_scope=getattr(original,'declared_namespace',original.__dict__)
    require(isinstance(prior_scope,dict),'original declared namespace is not a mapping')
    for name,value in list(values.items()):
        if isinstance(value,FunctionType)and (value.__globals__ is original.__dict__ or value.__globals__ is prior_scope):
            f=FunctionType(value.__code__,values,value.__name__,value.__defaults__,value.__closure__);f.__kwdefaults__=value.__kwdefaults__;f.__annotations__=value.__annotations__;values[name]=f
    result=SimpleNamespace(**values);result.declared_namespace=values;return result

def assign(m,**values):m.__dict__.update(values);m.declared_namespace.update(values)

def warning_function(original,bound):
    read_bytes=Path(bound['path']).read_bytes();require(hashlib.sha256(read_bytes).hexdigest()==bound['sha256'],'warning policy changed')
    tree=ast.parse(read_bytes);nodes=[x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='review_warnings'];require(len(nodes)==1,'one declared warning function required')
    values=dict(original.__dict__);exec(compile(ast.Module(body=nodes,type_ignores=[]),bound['path'],'exec'),values)
    return values['review_warnings']

def selected_warning(original,bound):
    """Only the reviewed warning classifier is supplied as a declared dependency."""
    original.review_warnings=warning_function(original,bound)
    return original

def selected_source_warning(original,bound,reader_bound):
    """Select four declared warning/apply functions, preserving old mechanics.

    The original module's root and every other function remain unchanged. The
    old mechanical API receives its original positional arguments; the selected
    source itself is still authenticated and checked by derivative history.
    """
    require(binding(bound['path'])==bound,'selected warning/apply code changed')
    reader=module('scripts.batch.harbor_dialogue_source_revision_warnings',reader_bound)
    require(reader.SCHEMA=='harbor-dialogue-source-revision-warning/v1','foreign source-warning reader')
    import scripts.batch as package
    package.harbor_dialogue_source_revision_warnings=reader
    names=('review_warnings','review_faults','review_passed','apply')
    old_tree=ast.parse(Path(original.__file__).read_bytes());new_tree=ast.parse(Path(bound['path']).read_bytes())
    def functions(tree):
        selected=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in names]
        require(len(selected)==len(names)and {n.name for n in selected}==set(names),'closed four-function validator selection required')
        return selected
    before,after=functions(old_tree),functions(new_tree)
    evidence=dict(original=binding(original.__file__),selected=bound,reader=reader_bound,
        symbols=list(names),original_ast_sha256=hashlib.sha256(ast.dump(ast.Module(body=before,type_ignores=[])).encode()).hexdigest(),
        selected_ast_sha256=hashlib.sha256(ast.dump(ast.Module(body=after,type_ignores=[])).encode()).hexdigest())
    prior=getattr(original,'_derivative_source_warning_selection',None)
    if prior is not None:
        require(prior==evidence,'source-warning scope changed within owning context');return evidence
    values=dict(original.__dict__);mechanics=original.check_draft
    if 'source_workbook'not in inspect.signature(mechanics).parameters:
        def check_draft(text,claims,task_dir,*,source_workbook=None):
            require(source_workbook is not None and binding(source_workbook['path'])==source_workbook,
                'old mechanical adapter requires authenticated source')
            return mechanics(text,claims,task_dir)
        values['check_draft']=check_draft
    exec(compile(ast.Module(body=after,type_ignores=[]),bound['path'],'exec'),values)
    for name in names:setattr(original,name,values[name])
    original._derivative_source_warning_selection=evidence
    return evidence

@contextmanager
def historical_warnings(validator, bound):
    """Validate recorded findings with their original policy, then restore current policy."""
    require(Path(validator.__file__).resolve()==Path(bound['path']).resolve(),
            'historical warning policy belongs to another validator')
    previous=validator.review_warnings
    validator.review_warnings=warning_function(validator,bound)
    try:yield
    finally:validator.review_warnings=previous

def renewed_preparation(p):
    """Reconcile a stopped parent that completed only its lease renewal."""
    r=p['renewed_preparation'];require(set(r)=={'plan','job','outcome','process'},'closed lease-only recovery required')
    old=read(r['plan']);job=read(r['job']);outcome=read(r['outcome']);process=read(r['process'])
    require('renewed_preparation'not in old and 'continuation'not in old
        and {k:v for k,v in p.items()if k not in {'renewed_preparation','helper','owner','fence','operations','output'}}==
            {k:v for k,v in old.items()if k not in {'helper','owner','fence','operations','output'}}
        and p['owner']!=old['owner']and p['fence']==old['fence']+1 and p['operations']!=old['operations']and p['output']!=old['output'],
        'lease-only recovery changed source, review, or policy')
    require(job['driver']==old['helper']and job['inputs'][0]==r['plan']and job['result']==old['output']+'/result.json'
        and job['workbook']==p['workbook']and job['source']==p['source']and process['job']==r['job']
        and r['outcome']==binding(Path(job['run'])/'outcome.json')and outcome['intent']==binding(Path(job['run'])/'intent.json')
        and read(outcome['intent'])['job']==r['job'],'lease-only recovery belongs to another worker')
    require(outcome['status']=='RECONCILIATION_REQUIRED'and outcome['returncode']==1 and outcome['failure_type']is None
        and outcome['result']is None and outcome['automatic_retry']is False and outcome['publication_performed']is False
        and outcome['log']==binding(Path(job['run'])/'worker.log')
        and Path(outcome['log']['path']).read_text().rstrip().endswith('ValueError: cached foreign implementation'),
        'only diagnosed warm reconstruction failure is supported')
    require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(),'prior adoption worker may still run')
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        if int(path.parent.name)==os.getpid():continue
        try:args=path.read_bytes().split(b'\0')
        except(FileNotFoundError,PermissionError):continue
        require(not any(x.encode()in args for x in(r['job']['path'],old['helper']['path'])),'prior adoption child may still run')
    home=Path(old['output'])
    require(not Path(old['operations']).exists(),'original adoption dispatcher has owning effects')
    require({str(x.relative_to(home))for x in home.rglob('*')if x.is_file()}==
        {'lease-before.json','lease-after.json','lease/intent.json','lease/complete.json'}
        ,'adoption has owning effects beyond the completed renewal')
    return r['plan']

def lease_home(p):
    return Path(read(p['renewed_preparation']['plan'])['output']if 'renewed_preparation'in p else p['output'])


def lease_completion(p):
    home=lease_home(p)/'lease';renewed=home/'complete.json';retained=home/'retained-live.json'
    require(not(renewed.exists()and retained.exists()),'mixed adoption lease receipts')
    return retained if retained.exists()else renewed

def lease_records(p):
    completion=lease_completion(p)
    if not completion.exists():return []
    home=lease_home(p)
    names=['lease/retained-live.json']if completion.name=='retained-live.json'else['lease/complete.json','lease/intent.json']
    return [binding(home/x)for x in names+['lease-before.json','lease-after.json']]

def validate_adoption_lease(p,authority,path,previous):
    from scripts.batch import harbor_tracker_lifecycle as life
    home=lease_home(p);completion=lease_completion(p);before_binding=binding(home/'lease-before.json');after_binding=binding(home/'lease-after.json')
    if completion.name=='complete.json':
        L=module('_adoption_selected_lease',p['renewal_module'])
        return L.validate_completed(life=life,read=read,record=binding,path=Path(path),previous=previous,
            completion=binding(completion),before_binding=before_binding,after_binding=after_binding,authority_record=authority)
    v=read(binding(completion));before=read(before_binding);lease=before['leases']['task_agent']
    require(set(v)=={'schema_version','authority','lease','tracker','validated_evidence','minimum_remaining_ns',
        'observed_at_ns','original_expired_replacements','lease_operations','task_guards_changed','semantic_budget_reset','automatic_retry'}
        and v['schema_version']=='fleet-retained-live-continuation-lease/v1'and v['authority']==authority
        and v['lease']==asdict(previous) and v['tracker']==dict(path=str(path),sha256=previous.sha256)
        and before_binding['sha256']==after_binding['sha256']==previous.sha256 and read(after_binding)==before
        and before['revision']==previous.revision and (lease['owner'],lease['token'])==(previous.owner,previous.token)
        and v['minimum_remaining_ns']==7200*10**9 and type(v['observed_at_ns'])is int
        and lease['heartbeat_at']<=v['observed_at_ns'] and lease['expires_at']-v['observed_at_ns']>=v['minimum_remaining_ns']
        and v['original_expired_replacements']==life.validate_lifecycle(before) and v['lease_operations']==0
        and v['task_guards_changed']is False and v['semantic_budget_reset']is False and v['automatic_retry']is False,
        'retained adoption lease differs from original sufficient live custody')
    for row in v['validated_evidence']:
        if row['path']==str(path):require(row==v['tracker'],'retained adoption tracker witness differs')
        else:require(binding(row['path'])==row,'retained adoption evidence changed')
    return previous


def stopped_readiness(c,task,error,worker,selection,lease,W,root,*,ready=None):
    """Retain the failed pure check and require every current readiness gate."""
    require(c['failed_readiness']==binding(task/'readiness.json')
        and {x.name for x in task.iterdir()}=={'intent.json','exception.json','readiness.json'},
        'failed readiness has unknown owning effects')
    failed=read(c['failed_readiness'])
    prose=(error==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'full current readiness did not qualify').hexdigest(),automatic_replay=False)
        and failed['publication_ready']is False and failed['blockers']==['prose_mechanics']
        and failed['checks']['prose_mechanics']==dict(checked=False,error_type='AdmissionError'))
    facade=(ready is not None and error==dict(type='AdmissionError',message_sha256=hashlib.sha256(b'complete current readiness required').hexdigest(),automatic_replay=False)
        and failed['publication_ready']is True and failed['blockers']==[])
    require(prose or facade,'only diagnosed pure readiness failure is supported')
    _,before,tracker=W.active(worker,selection,lease)
    path=Path(tracker['path']);journal=path.parent/(path.stem+'-notes-transactions')
    require(all(int(x.name.split('-')[0])<=lease.revision for x in(journal.iterdir()if journal.exists()else[])),
        'prepared enqueue exists; reconcile original transaction')
    current=W.complete_readiness(selection,root=root)
    require(current['publication_ready']is True and current['blockers']==[],
        'current full readiness still fails')
    if ready is not None:
        require(W.HarborLaneLifecycle.transition is ready.ReadyTransitions.transition,
            'actual owning readiness facade differs')
        ready._proof(before,selection)

def dependencies(p):
    rows=[p[k]for k in('helper','history','warning_validator','runner','review_plan','review_result','renewal_module')]
    q=read(p['review_plan']);remaining=q['schema_version']=='fleet-dialogue-remaining-derivative-review/v1'
    retained=q['schema_version']=='fleet-rejected-reader-review/v1'
    if retained:
        context=p['retained_context'];q=retained_review(q)
        rows.extend(q[k]for k in('helper','context_helper','reader_selection','candidate','proof','executor','allowance','allowance_module','claims','draft','golden','descriptor','original_intent','failed_resource'))
        rows.extend(context[k]for k in('config','dispatcher','stdlib','fleet_runtime','takeover_helper','takeover_profile','warning_reader'))
        if 'allowance_history'in context:rows.append(context['allowance_history'])
        if 'lifecycle_runtime'in context:rows.extend(context['lifecycle_runtime'].values())
        if context['takeover_complete']is not None:rows.append(context['takeover_complete'])
        rows.extend([context['archive']['helper'],context['archive']['plan'],*context['archive'].get('receipt',{}).values()])
    else:rows.extend(q[k]for k in('helper','stdlib','revision','claims','draft','candidate','proof','selection'))
    if remaining:
        require(p.get('review_allowance_reader')is None,'ordinary remaining review has no extra allowance')
        rows.extend(q['checker'].values());rows.extend(q['semantic_context'].values())
    elif not retained:rows.extend(q[k]for k in('allowance_module','allowance'))
    if p.get('review_allowance_reader')is not None:rows.append(p['review_allowance_reader'])
    if p.get('ready_root')is not None:rows.extend(p['ready_root'].values())
    if p.get('continuation')is not None:
        rows.extend(p['continuation'][k]for k in('plan','failed_outcome'))
        if p['continuation'].get('previous_plan'):
            rows.append(p['continuation']['previous_plan'])
            if p['continuation'].get('completed_outcomes'):rows.extend([p['continuation'][k]for k in('failed_intent','failed_exception')]+p['continuation']['completed_outcomes'])
        if p['continuation'].get('failed_readiness'):rows.append(p['continuation']['failed_readiness'])
    if p.get('renewed_preparation')is not None:rows.extend(p['renewed_preparation'].values())
    if p.get('retained_preparation')is not None:rows.append(p['retained_preparation'])
    rows.extend(lease_records(p))
    for row in rows:require(binding(row['path'])==row,'declared adoption dependency changed')
    return list({r['path']:r for r in rows}.values())


def retained_review(q):
    """Expose exact retained inputs without inventing a blocked checkpoint."""
    require(q['schema_version']=='fleet-rejected-reader-review/v1','retained failed consumer required')
    selected=read(q['reader_selection']);rejected=read(selected['rejection']);revision=read(selected['revision'])
    require(q['proof']==selected['revision']and q['candidate']==revision['corrected_draft']
        and q['source']==selected['source']and q['workbook']==selected['workbook']
        and binding(revision['claims']['path'])==revision['claims']
        and binding(rejected['claims']['path'])==rejected['claims']
        and revision['claims']['sha256']==rejected['claims']['sha256'],'retained derivative source lineage differs')
    intent=read(rejected['original_intent']);directory=Path(rejected['original_intent']['path']).parent
    require(intent['step']=='dialogue-review'and not any((directory/name).exists()for name in('complete.json','blocked.json')),
        'original failed consumer must remain without a canonical checkpoint')
    return dict(q,claims=rejected['claims'],draft=rejected['draft'],golden=revision['source'],
        descriptor=rejected['descriptor'],original_intent=rejected['original_intent'],failed_resource=rejected['failed_resource'])


def predecessor(q):
    if q['schema_version']=='fleet-rejected-reader-review/v1':
        return q['original_intent'],[q['original_intent'],q['failed_resource']]
    return read(q['blocked'])['intent'],[q['blocked']]


def guard_seconds(p):
    context=p.get('retained_context')
    if context is None:return 180
    require(context['guard_seconds']==1800,'explicit bounded historical adoption guard required')
    return 1800


@contextmanager
def zero_model_controls(base,selected,job):
    """Keep original locks and descriptors without requiring unused credentials."""
    require(job['models']is False and job['publish']is False,
        'credential-free controls require a zero-model nonpublication tail')
    original=base._controls;executor=base.executor
    prior={name:getattr(executor,name)for name in ('execute','_open')}
    def forbidden(*args,**kwargs):raise ValueError('retained zero-model controls forbid model dispatch')
    def checked(value):
        require(value==selected and type(value)is base.Controls,'original zero-model controls changed')
        return original(replace(value,env_file=None))
    base._controls=checked
    for name in prior:setattr(executor,name,forbidden)
    try:yield
    finally:
        base._controls=original
        for name,value in prior.items():setattr(executor,name,value)


@contextmanager
def retained_lifecycle(p,root):
    """Use the completed takeover's declared lifecycle for current custody."""
    c=p['retained_context'];selected=c.get('lifecycle_runtime')
    if selected is None:
        yield;return
    require(set(selected)=={'helper','profile'},'closed retained lifecycle selection required')
    runtime=module('_adoption_lifecycle_runtime',selected['helper'])
    takeover=read(c['takeover_profile']);original=binding(Path(root)/'scripts/batch/harbor_tracker_lifecycle.py')
    chosen=takeover['shared']['lifecycle']
    entry=read(p['continuation']['plan'])['helper']if 'continuation'in p else p['helper']
    expected=dict(schema_version=runtime.SCHEMA,runtime=selected['helper'],entrypoint=entry,
        read_call=dict(function='context',args=[],kwargs={}),patches=[],guard_modules=[],child_plans=[],journal_roots=[],
        dependencies=[dict(module_name='scripts.batch.harbor_tracker_lifecycle',original=original,selected=chosen)],
        symbols=[dict(module_name='scripts.batch.harbor_tracker_lifecycle',original=original,selected=chosen,names=['validate_lifecycle'])])
    require(read(selected['profile'])==expected,'only the selected takeover lifecycle may change')
    with runtime.installed(selected['profile']):yield


def retained_environment(p,q,stack):
    """Load the original configured worker, with explicit fleet-only imports."""
    c=p['retained_context']
    require(set(c)-{'allowance_history','lifecycle_runtime'}=={'config','dispatcher','stdlib','fleet_runtime','takeover_helper','takeover_profile','takeover_complete','warning_reader','archive','guard_seconds'},
        'closed retained dialogue environment required')
    guard_seconds(p)
    selected=read(q['reader_selection']);module('_retained_adoption_stdlib',c['stdlib']).preload_inspect()
    reader=module('_retained_adoption_original_reader',selected['reader'])
    root,B,I,W,producer,Q,codec,worker,selection,old_lease=reader.setup()
    stack.enter_context(retained_lifecycle(p,root))
    config=read(c['config']);rejected=read(selected['rejection'])
    job=read(p['authoring_job'])
    require(job['schema_version']=='fleet-retained-dialogue-tail/v1'
        and job['config']==c['config'],'original zero-model job required')
    stack.enter_context(zero_model_controls(B,B.decode(config['controls']),job))
    require(reader.DESCRIPTOR==q['descriptor']and reader.RESOURCE==q['failed_resource']
        and Path(config['run_root'])/'instruction'==worker.run and config['workbook']==p['workbook']
        and B.decode(config['controls'])==I.selected(q['descriptor'])[1],
        'retained configured worker differs from actual failed consumer')
    Treader=module('_retained_adoption_takeover_reader',c['takeover_helper'])
    if c['takeover_complete']is None:
        profile,T,chosen,_=Treader.load(c['takeover_profile'])
    else:
        history=module('_retained_adoption_allowance_history',c['allowance_history'])
        profile,T,_,_=history.load(c['takeover_helper'],c['takeover_profile'],c['takeover_complete'])
        chosen=T._selection(profile['selection'])
    archive_reader=module('_retained_adoption_archive',c['archive']['helper'])
    archive_plan,archive=archive_reader.load(c['archive']['plan'])
    require(archive_plan['selection']==q['reader_selection']and archive_plan['context_helper']==q['context_helper']
        and chosen.workbook==p['workbook']and chosen.guard==reader.RESOURCE and chosen.checkpoint==reader.ORIGINAL
        and chosen.config==c['config']and chosen.allowance==q['allowance']
        and chosen.tracker==archive['tracker']['original'],
        'retained lease recovery detached from original archived consumer')
    preparation=read(read(reader.ORIGINAL)['checkpoint'])
    require(preparation['lease']==asdict(old_lease)
        and preparation['tracker']['sha256']==old_lease.sha256,
        'original preparation lease archive differs')
    archived=read(archive['tracker']['archive'])
    retained_lease=type(old_lease)(old_lease.owner,old_lease.token,archived['revision'],chosen.tracker['sha256'])
    T.continuation_chain(preparation['tracker'],asdict(old_lease),archived,retained_lease)
    lease=retained_lease
    if c['takeover_complete']is not None:
        before,after,lease=T.validate_completion(c['takeover_complete'])
        stopped=read(read(c['takeover_complete'])['stopped_owner_proof'])
        require(stopped['selection']==T._selection_value(chosen)
            and before==read(archive['tracker']['archive']), 'completed takeover changed selected original custody')
        T.continuation_chain(preparation['tracker'],asdict(old_lease),after,lease,
            takeover=c['takeover_complete'],tracker_path=Path(chosen.tracker['path']))
    else:Treader.probe(c['takeover_profile'])
    runtime=read(c['fleet_runtime']);runtime_root=Path(runtime['root'])
    for row in runtime['files'].values():require(binding(row['path'])==row,'declared fleet-only dependency changed')
    import scripts
    require(list(scripts.__path__)==[str(root/'scripts')], 'original task package already has foreign imports')
    require(not(root/'scripts/fleet').exists()and(runtime_root/'scripts/fleet').is_dir(),'fleet-only extension is not isolated from task implementation')
    scripts.__path__.append(str(runtime_root/'scripts'))
    import scripts.fleet
    require(all(Path(x)==runtime_root/'scripts/fleet'for x in scripts.fleet.__path__), 'foreign fleet-only package')
    D=module('_retained_adoption_dispatcher',c['dispatcher'])
    from scripts import harbor_publish as P
    from scripts.batch import harbor_instruction_lease_renewal as R
    controls=B.decode(config['controls']);job=read(p['authoring_job'])
    original_operations=read(p['continuation']['plan'])['operations']if 'continuation'in p else p['operations']
    require(set(job)=={'schema_version','driver','config','operations','models','commands','containers','publish'}
        and job['schema_version']=='fleet-retained-dialogue-tail/v1'and job['driver']==c['dispatcher']
        and job['config']==c['config']and job['operations']==original_operations
        and job['models']is job['publish']is False and job['commands']is job['containers']is True,
        'closed original zero-model tail job required')
    require(worker.campaign.model_slots==1 and worker.campaign.slot_root==D.MODEL,'original retained model capacity differs')
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    env=(B,I,P,W,R,config,root,controls)
    return job,D,env,worker,selection,lease,dict(path=chosen.tracker['path'],sha256=lease.sha256)


def completed_review_failure(original,cp,q,I,controls,p,authority):
    """Decode T-backed review custody from its authenticated pre-renewal archive.

    The original takeover validator still runs. Only its final historical tracker
    read uses the original L archive; full L validation follows before returning.
    Current owning transitions continue through W.active and I._prior.
    """
    home=lease_home(p);complete=lease_completion(p)
    if cp.get('completed_takeover')is None or not complete.exists():
        return original.completed_failure(q,I,controls)
    require(cp['schema_version']=='fleet-retained-completed-review-tail/v1'
        and q['semantic_context']['helper']==cp['helper'] and read(q['semantic_context']['plan'])==cp,
        'historical takeover review context differs')
    archive=binding(home/'lease-before.json');target=q['tracker']
    require(archive['sha256']==target['sha256'],'historical takeover tracker archive differs')
    takeover=cp['completed_takeover'];loader=original.module;scoped=[]
    def selected(row):
        reader=loader(row)
        if row!=takeover['helper']:return reader
        original_binding,original_read=reader.binding,reader.read
        def historical_binding(path):
            if str(Path(path))==target['path']:
                require(original_binding(archive['path'])==archive,'historical takeover archive changed')
                return target
            return original_binding(path)
        def historical_read(bound):
            if bound==target:return original_read(archive)
            return original_read(bound)
        reader.binding,reader.read=historical_binding,historical_read
        scoped.append((reader,original_binding,original_read))
        return reader
    original.module=selected
    try:result=original.completed_failure(q,I,controls)
    finally:
        original.module=loader
        for reader,old_binding,old_read in reversed(scoped):reader.binding,reader.read=old_binding,old_read
    validate_adoption_lease(p,authority,target['path'],result[2])
    return result


def context(bound,*,probe=False,owning_intent=None):
    stack=ExitStack()
    try:result=_context(bound,probe=probe,owning_intent=owning_intent,_stack=stack)
    except BaseException:
        stack.__exit__(*sys.exc_info());raise
    result[5]._derivative_context_manager=stack
    return result


def _context(bound,*,probe=False,owning_intent=None,_stack):
    p=read(bound);require(set(p)-{'continuation','review_allowance_reader','renewed_preparation','ready_root','retained_context'}=={'schema_version','workbook','source','helper','review_plan','review_result','history','warning_validator','runner','owner','fence','output','operations','authoring_job','retained_preparation','renewal_module'} and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed derivative adoption plan required')
    authority=p.get('continuation',{}).get('plan',bound)
    renewal_authority=p.get('renewed_preparation',{}).get('plan',authority)
    if 'renewed_preparation'in p:
        preparation=read(authority)if authority!=bound else p
        require(preparation['renewed_preparation']==p['renewed_preparation'],'completed adoption lost original renewal preparation')
        renewed_preparation(preparation)
    if authority!=bound:
        old=read(authority);c=p['continuation']
        require(set(c) in ({'plan','failed_outcome','output'},{'plan','failed_outcome','output','pre_tail_directory_failure'},{'plan','failed_outcome','output','previous_plan'},{'plan','failed_outcome','output','previous_plan','completed_outcomes','failed_intent','failed_exception'},{'plan','failed_outcome','output','previous_plan','completed_outcomes','failed_intent','failed_exception','failed_readiness'}) and all(p[k]==v for k,v in old.items() if k not in{'helper','owner','fence','operations'}),'continuation changed adopted source or authority')
        previous=read(c['previous_plan'])if c.get('previous_plan')else old
        require(not c.get('previous_plan') or c['previous_plan']==authority or previous.get('continuation',{}).get('plan')==authority,'completed tail lost original adoption authority')
        require(p['owner']!=previous['owner'] and p['fence']==previous['fence']+1 and p['operations']!=old['operations'] and c['output']!=old['output'],'fresh continuation custody required')
    dependencies(p);q=read(p['review_plan']);require(q['source']==p['source']and q['workbook']==p['workbook'],'adoption source differs')
    retained=q['schema_version']=='fleet-rejected-reader-review/v1'
    require(retained==('retained_context'in p),'retained environment belongs only to original failed consumers')
    if retained:
        q=retained_review(q)
        oldjob,D,env,worker,selection,lease,retained_tracker=retained_environment(p,q,_stack)
        q=dict(q,tracker=retained_tracker)
    else:
        M=module('_adoption_review_context',q['helper']);cp=read(q['semantic_context']['plan']);original=module('_adoption_semantic_history',q['semantic_context']['helper'])
        module('_adoption_stdlib',q['stdlib']).preload_inspect();os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH','')
    remaining=q['schema_version']=='fleet-dialogue-remaining-derivative-review/v1'
    if remaining:
        fresh,oldjob,D,env,worker,selection,lease,retained_manager=M.authoring_context(q)
        D._remaining_derivative_manager=retained_manager
    elif not retained:
        fresh,oldjob,D,env=original.validated_authoring(cp)
        if cp.get('schema_version')=='fleet-retained-completed-review-tail/v1':_stack.callback(original.close,D)
    base,I,P,W,R,config,root,controls=env
    ready=None
    if p.get('ready_root')is not None:
        require(set(p['ready_root'])=={'helper','lifecycle'},'closed original readiness root scope required')
        ready_scope=module('_adoption_ready_root',p['ready_root']['helper']).installed(W,root,module('_adoption_ready_lifecycle',p['ready_root']['lifecycle']))
        ready=_stack.enter_context(ready_scope)
    job=read(p['authoring_job']);require(job['config']==oldjob['config']and job['driver']==oldjob['driver']and job['publish']is False,'selected original authoring job changed')
    if not remaining and not retained:
        _,selection,lease=completed_review_failure(original,cp,q,I,controls,p,renewal_authority);worker,observed,recipe=I.selected(q['descriptor']);require(observed==controls,'original instruction controls differ')
    tracker=binding(worker.context.tracker_root/(p['workbook']+'.json'))
    certificate=Path(p['output'])/'adoption/certificate.json'
    renewed=lease_completion(p);expected_tracker=q['tracker']
    previous_lease=lease
    def refresh_lease():
        nonlocal lease
        require(lease_completion(p).exists(),'completed original renewal or retained-live receipt required')
        lease=validate_adoption_lease(p,renewal_authority,tracker['path'],previous_lease)
        return lease
    if renewed.exists():
        refresh_lease()
        expected_tracker=dict(path=tracker['path'],sha256=lease.sha256)
    if not certificate.exists():require(tracker==expected_tracker,'current pre-adoption tracker differs')
    # Once applied, original I._prior and W.active validate each current lease;
    # a historical rejected checkpoint never supplies live post-commit authority.
    if not remaining:
        A=module('scripts.batch.harbor_continuation_allowance',q['allowance_module']);import scripts.batch as batch;batch.harbor_continuation_allowance=A
    history=module('_adoption_derivative_history',p['history']);V=W.producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
    if retained:selected_source_warning(V,p['warning_validator'],p['retained_context']['warning_reader'])
    else:selected_warning(V,p['warning_validator'])
    archive_cache={}
    # Apply and later readiness must use the same authenticated coverage checker.
    if remaining:V.clause_covered=module('_adoption_selected_coverage',q['checker']['selected']).clause_covered
    def validated(current_selection):
        s=current_selection.content
        arguments=dict(root=root,claims=s.dialogue_claims,draft=q['candidate'],workbook=s.workbook,original_source=s.bindings['original_raw_source'],golden=s.bindings['source_file'],policy=s.model_policy,task_dir=s.stage)
        if remaining:
            # The independent result retains its original faults and passed:false.
            # Only the subsequent adoption decision uses the declared new policy.
            with historical_warnings(V,binding(root/'.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py')):
                result=history.validate(p['review_plan'],p['review_result'],**arguments)
        else:
            if retained:arguments.update(warning_reader=p['retained_context']['warning_reader'],archive=p['retained_context']['archive'],archive_cache=archive_cache)
            result=history.validate(p['review_plan'],p['review_result'],**arguments)
        extra=dict(source_warning_proof=result['source_warning_proof'])if retained else {}
        review=result['provenance']['review'];warnings=V.review_warnings(review,read(q['claims']),draft_text=Path(q['candidate']['path']).read_text(),**extra)
        allowed={'dialogue_style_only','frozen_junior_prefix','hidden_frozen_scaffold'}if retained else {'dialogue_style_only'}
        require(V.review_passed(review,read(q['claims']),draft_text=Path(q['candidate']['path']).read_text(),**extra)and all(x['code']in allowed for x in warnings),'substantive or unknown review finding remains')
        return result,warnings
    proof,warnings=validated(selection)
    validated.refresh_lease=refresh_lease
    from scripts.batch import harbor_semantic_readiness as semantic,harbor_complete_readiness as complete
    S=namespace(semantic);K=namespace(complete);U=namespace(W);J=namespace(I);RR=namespace(R);DD=namespace(D)
    original_review=semantic.dialogue_review
    def reviewed(call,**args):
        body=json.loads(read(call.request)['messages'][1]['content'])
        if 'source_revision'not in body:return original_review(call,**args)
        s=selection.content;require(args['root']==root and args['workbook']==p['workbook']and args['original_sha256']==p['source']['sha256']and args['source']==q['golden']and args['current_source_sha256']==q['golden']['sha256']and args['historical']is None and args['pre_review_repair']is None,'foreign derivative semantic context')
        require(semantic_claims(args,q,selection,retained=retained)and args['task_dir']==s.stage and args['answer_key']==binding(s.stage/'tests/answer_key.json'),'derivative semantic selection changed')
        actual,flags=validated(selection);require(call==__import__('scripts.batch.harbor_model_review_provenance',fromlist=['Call']).Call(**read(p['review_result'])['call'])and tuple(args['writer_calls'])==actual['writer_calls'],'derivative author/reviewer identity changed')
        require(Path(args['notes']['path']).read_text()==V.strip_claim_comments(Path(q['candidate']['path']).read_text()),'applied notes differ from independent review')
        return dict(claims=actual['claims'],round=actual['round'],provenance=actual['provenance'],source_revision_history=dict(actual,writer_calls=[asdict(c)for c in actual['writer_calls']]),warnings=flags,evidence=actual['evidence'])
    assign(S,dialogue_review=reviewed);assign(K,dialogue_review=reviewed);assign(U,complete_readiness=K.complete_readiness)
    # The unchanged lane facade dynamically imports the original K.check_dialogue.
    # Its sole semantic dependency must be the same exact declared reader as W.
    complete.dialogue_review=reviewed
    if p.get('review_allowance_reader')is not None:
        from scripts.batch import harbor_ready_transitions as ready
        allowance_reader=module('_derivative_ready_allowance',p['review_allowance_reader'])
        def review_witness(before,chosen,full):
            actual,flags=validated(chosen);reported=full['checks']['dialogue_review_provenance']['result']
            require(reported['source_revision_history']==dict(actual,writer_calls=[asdict(c)for c in actual['writer_calls']]),'readiness omitted actual review history')
            directory=Path(q['claims']['path']).parent
            calls=[read(binding(directory/('review-call-%02d.json'%i)))for i in range(1,actual['round'])]+[read(p['review_result'])['call']]
            reservation=read(actual['reservation']);requests=[c['request']for c in calls]
            return dict(scope=reservation['scope'],reservation=actual['reservation'],ordinal=actual['round'],limit=reservation['cumulative_limit'],requests=requests,outcomes=[*actual['original_reviews'],read(p['review_result'])['outcome']])
        allowance_reader.install(ready,expected_code=binding(root/'scripts/batch/harbor_ready_transitions.py'),validate=review_witness)
    directory=Path(p['output'])/'adoption';old_directory=W.step_directory(worker,'dialogue-paraphraser'if remaining else'dialogue-review')
    def certificate_value():
        from scripts.batch.harbor_model_review_provenance import Call
        call=Call(**read(p['review_result'])['call'])
        expected=U.refreshed(replace(selection,content=replace(selection.content,dialogue_review=call.parsed,dialogue_reviewed_draft=q['candidate']),semantic=replace(selection.semantic,dialogue_call=call,dialogue_reviewed_claims=q['claims'],dialogue_writer_calls=proof['writer_calls'])))
        before=binding(directory/'tracker-before.json')
        require(before['sha256']==lease.sha256,'derivative predecessor tracker differs')
        value=read(binding(directory/'certificate.json'))
        require(value==dict(schema_version=SCHEMA,plan=authority,review_result=p['review_result'],old_directory=str(old_directory),initial=binding(worker.run/'initial.json'),selection=I.codec.encode(expected),lease=asdict(lease),intent=predecessor(q)[0],tracker_before=before,history=proof['evidence'],lease_history=lease_records(p),warnings=warnings,author_generation=2,review_ordinal=proof['round'],models_dispatched=0),'derivative certificate differs from revalidated exact adoption')
        require(Path(expected.content.stage/'environment/additional-assumptions.md').read_text()==V.strip_claim_comments(Path(q['candidate']['path']).read_text()),'actual adopted notes differ')
        return value
    if p['retained_preparation']is not None:retained_preparation(p['retained_preparation'],q,W,worker)
    if certificate.exists():certificate_value()
    def step_directory(w,phase):
        if remaining and w.run==worker.run and phase=='dialogue-paraphraser' and (directory/'complete.json').exists():
            cert=certificate_value();author_directory=directory/'author-generation-02'
            value=read(binding(author_directory/'complete.json'))
            require(value==dict(step='dialogue-paraphraser',status='completed',selection=cert['selection'],lease=cert['lease'],intent=cert['intent'],source_revision=binding(directory/'certificate.json')),'derivative author completion differs')
            return author_directory
        if w.run==worker.run and phase=='dialogue-review' and (directory/'complete.json').exists():
            cert=certificate_value();completed=read(binding(directory/'complete.json'))
            require(cert['plan']==authority and cert['review_result']==p['review_result']and cert['old_directory']==str(old_directory)and cert['initial']==binding(w.run/'initial.json'),'derivative projection detached from worker')
            require(completed==dict(step='dialogue-review',status='completed',selection=cert['selection'],lease=cert['lease'],intent=cert['intent'],source_revision= binding(directory/'certificate.json')),'derivative completed checkpoint changed')
            return directory
        if w.run==worker.run and phase in('final-runtime','dialogue-commit','enqueue'):
            require((directory/'certificate.json').exists(),'derivative certificate required before remaining phase')
            certificate_value();base=Path(p['continuation']['output'])if phase=='enqueue' and p.get('continuation',{}).get('completed_outcomes')else Path(p['output']);return base/'steps'/phase
        return W.step_directory(w,phase)
    assign(U,step_directory=step_directory);retained_worker_scope(W,U)
    assign(J,worker=U);assign(RR,_worker=lambda:U)
    # Original I uses a module global named w. Bind all exact object aliases,
    # including warm function globals, without altering any original function.
    for facade in(J,RR,DD):
        for name,value in list(facade.declared_namespace.items()):
            if value is W:assign(facade,**{name:U})
            elif value is I:assign(facade,**{name:J})
            elif value is R:assign(facade,**{name:RR})
    chosen=(base,J,P,U,RR,config,root,controls)
    original_selected=D.selected
    def selected(observed,result):
        require(observed==chosen,'derivative dispatcher environment changed')
        worker_value,recipe=original_selected(env,result)
        require(worker_value.root==root and worker_value.run==Path(config['run_root'])/'instruction',
            'original derivative selected worker differs')
        return worker_value,recipe
    assign(DD,selected=selected)
    runner=module('_adoption_declared_runner',p['runner'])
    validated.refresh_runner=runner_refresher(runner,J,base=base,bound=bound,p=p,producer_code=binding(I.__file__))
    validated.refresh_runner()
    def setup(observed):require(observed==job or observed==dict(job,operations=p['operations'],models=False),'unselected derivative tail job');return chosen
    assign(DD,setup=setup)
    operation_namespace(DD,config,p['operations'],owning_intent=owning_intent,plan=bound)
    if authority!=bound:
        _,checkpoint,value=authorization(authority,chosen)
        if c.get('completed_outcomes'):
            require(len(c['completed_outcomes'])==2,'exact runtime and commit required')
            for ordinal,outcome in enumerate(c['completed_outcomes']):
                require(Path(outcome['path'])==Path(old['operations'])/('%03d'%ordinal)/'outcome.json','foreign completed phase')
                checkpoint,value=DD.successful(outcome,chosen)
                require(value['status']=='STEP_COMPLETE' and value['next_step']==('dialogue-commit','enqueue')[ordinal],'completed phases changed order')
            old_task=Path(previous.get('continuation',{}).get('output',previous['output']))/'steps/enqueue';failed_task=read(c['failed_intent']);error=read(c['failed_exception'])
            require(c['failed_intent']==binding(old_task/'intent.json') and c['failed_exception']==binding(old_task/'exception.json'),'failed enqueue evidence differs')
            prior,selected,current=J._prior(checkpoint,value['descriptor'])
            require(failed_task['step']=='enqueue' and failed_task['previous']==prior['checkpoint'] and failed_task['selection']==I.codec.encode(selected) and failed_task['lease']==asdict(current) and failed_task['initial']==binding(worker.run/'initial.json') and failed_task['code']==U.code(worker),'failed enqueue lost exact committed predecessor')
            if c.get('failed_readiness'):stopped_readiness(c,old_task,error,worker,selected,current,U,root,ready=ready)
            else:
                require({x.name for x in old_task.iterdir()}=={'intent.json','exception.json'},'failed enqueue has unknown owning work')
                require(error['type']=='TypeError' and error['automatic_replay']is False and error['message_sha256']==hashlib.sha256(b'Object of type Call is not JSON serializable').hexdigest(),'unselected enqueue failure')
        if c.get('pre_tail_directory_failure')is not None:
            stopped_before_tail(c,previous)
        else:
            failed=read(c['failed_outcome']);resource=read(failed['resource']);intent=read(resource['intent']);index='002'if c.get('completed_outcomes')and not previous.get('continuation',{}).get('completed_outcomes')else'000'
            require(Path(c['failed_outcome']['path'])==Path(previous['operations'])/index/'outcome.json' and failed['status']=='RECONCILIATION_REQUIRED' and resource['returncode']==1 and resource['failure']is None and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=checkpoint) and intent['action']=='step' and intent['models']is False,'continuation is detached from failed remaining guard')
    return p,q,proof,warnings,chosen,DD,worker,selection,lease,validated,runner


def stopped_before_tail(c,previous):
    """Recognize a sealed worker stop after adoption but before any tail guard."""
    require(c['pre_tail_directory_failure']is True,'explicit pre-tail directory failure required')
    failed=read(c['failed_outcome']);intent=read(failed['intent']);job=read(intent['job'])
    require(failed['schema_version']=='harbor-fleet-worker-outcome/v1'
        and failed['status']=='RECONCILIATION_REQUIRED'and failed['returncode']==1
        and failed['failure_type']is None and failed['result']is None and failed['automatic_retry']is False
        and intent['owner']==previous['owner']and job['driver']==previous['helper']
        and job['inputs'][0]==c['plan']and job['source']==previous['source']
        and job['result']==str(Path(previous['output'])/'result.json'),
        'pre-tail failure detached from original stopped worker')
    log=failed['log'];require(binding(log['path'])==log,'pre-tail worker log changed')
    expected="FileNotFoundError: [Errno 2] No such file or directory: "+repr(previous['operations'])
    require(Path(log['path']).read_text().rstrip().endswith(expected)
        and not Path(previous['operations']).exists()and not Path(job['result']).exists(),
        'pre-tail worker has another failure or owning work')


def create_operations(path):
    path=Path(path)
    require(path.is_absolute()and not any(x.is_symlink()for x in(path,*path.parents)),
        'canonical fresh tail namespace required')
    require(not path.exists(),'tail namespace already used')
    path.mkdir(parents=True)
    return path


def retained_worker_scope(original,selected):
    """Compose the exact retained worker step scope with declared aliases."""
    step=selected._step
    if step.__globals__ is selected.declared_namespace:return
    old=original._step;scope=old.__globals__
    require(step is old and old.__name__=='_step'
        and binding(inspect.getsourcefile(old))==binding(original.__file__)
        and scope.get('step_directory')is original.step_directory,
        'retained original worker step scope differs')
    values=dict(scope)
    for name,value in original.__dict__.items():
        if name in values and values[name]is value and name in selected.__dict__:
            values[name]=selected.__dict__[name]
    require(values['step_directory']is selected.step_directory,
        'retained worker lost selected checkpoint directory')
    current=FunctionType(old.__code__,values,old.__name__,old.__defaults__,old.__closure__)
    current.__kwdefaults__=old.__kwdefaults__;current.__annotations__=old.__annotations__
    assign(selected,_step=current)


def runner_refresher(runner,producer,*,base,bound,p,producer_code):
    """Freeze current authenticated inputs after renewal, from the original run."""
    original=producer.run
    def refresh():
        previous=producer.run;assign(producer,run=original)
        try:selected=runner.select_run(producer,base=base,kind='instruction',reader_plan=bound,
            child_wrapper=p['helper'],producer_code=producer_code,reader_evidence=dependencies(p))
        except BaseException:
            assign(producer,run=previous);raise
        assign(producer,run=selected)
    return refresh


def operation_namespace(dispatcher,config,value,*,owning_intent=None,plan=None):
    if owning_intent is None:return dispatcher.operation_namespace(config,value)
    path=Path(value);base=Path(config['run_root'])/'dispatcher-v2';intent_path=Path(owning_intent['path'])
    require(path.is_absolute() and path.parent==base and path.resolve()==path and base.resolve()==base and path.is_dir(),'owning dispatcher namespace escaped')
    require(intent_path.name=='intent.json' and intent_path.parent.parent==path and intent_path.parent.name in{'000','001','002'},'owning intent outside selected tail namespace')
    value=read(owning_intent)
    require(value['action']=='step' and value['models']is False and value['commands']is True and value['containers']is True,'unselected cold instruction capability')
    sidecar=read(binding(intent_path.parent/'declared-runtime-adapter.json'))
    require(sidecar['reader_plan']==plan,'cold namespace not bound by selected parent adapter')
    return path


def retained_preparation(bound,q,W,worker):
    """Preserve a diagnosed pre-runtime fixture journal; never replay its intent."""
    v=read(bound)
    require(set(v)=={'schema_version','review_plan','probe','fixture_plan','fixture_result','intent','exception','tracker','stage_sha256'} and v['schema_version']=='fleet-derivative-diagnostic-preparation/v1','closed diagnostic preparation required')
    require(read(v['review_plan'])==q and v['tracker']==q['tracker'],'foreign diagnostic source/history')
    for key in('probe','fixture_plan','fixture_result','intent','exception'):require(binding(v[key]['path'])==v[key],'retained diagnostic preparation changed')
    directory=W.step_directory(worker,'final-runtime')
    require(v['intent']==binding(directory/'intent.json')and v['exception']==binding(directory/'exception.json')and {str(f.relative_to(directory))for f in directory.rglob('*')if f.is_file()}=={'intent.json','exception.json'},'diagnostic runtime namespace has unknown work')
    error=read(v['exception']);require(error==dict(type='BeforeRuntimeWrite',message_sha256=hashlib.sha256(b'').hexdigest(),automatic_replay=False),'diagnostic failure differs')
    fixture=read(v['fixture_plan']);result=read(v['fixture_result']);intent=read(v['intent']);completed=read(result['checkpoint'])
    require(fixture['review_plan']==v['review_plan']and result['status']=='STEP_COMPLETE'and result['next_step']=='final-runtime'and result['publication_performed']is False,'diagnostic derivative history differs')
    require(intent['step']=='final-runtime'and intent['previous']==result['checkpoint']and intent['selection']==completed['selection']==result['selection']and intent['lease']==completed['lease']and intent['initial']==binding(worker.run/'initial.json')and intent['code']==W.code(worker),'diagnostic preparation changed original worker authority')
    selected=W.codec.decode(intent['selection'])
    require(selected.content.stage.is_relative_to(Path(fixture['output']).parent)and selected.content.bindings['original_raw_source']==q['source'],'diagnostic was not isolated fixture input')
    return v


def live(p):
    from scripts.fleet import model_gate
    client,owner,_=model_gate.context()
    require(owner['workbook']==p['workbook'] and owner['owner']==p['owner'] and owner['fence']==p['fence'],'foreign derivative custody')
    value=client.call('heartbeat',**owner)
    require(value['source_sha256']==p['source']['sha256'],'current fleet source differs')
    return value

def no_models():
    from scripts.batch import harbor_model_executor as E
    def forbidden(*a,**kw):raise ValueError('source derivative adoption has no model authority')
    E.execute=E._open=forbidden


def instruction_step(instruction,*args,guardian_lock_fds=(),**kwargs):
    """Retain external fleet guardians for the original pre-keyword runner."""
    fn=instruction.run
    if 'guardian_lock_fds'in inspect.signature(fn).parameters:
        return fn(*args,guardian_lock_fds=guardian_lock_fds,**kwargs)
    require(list(inspect.signature(fn).parameters)==['action','arguments','controls','directory','models','commands','containers']
        and kwargs.get('models')is False,'only original zero-model instruction runner supported')
    require(isinstance(fn,FunctionType)and isinstance(guardian_lock_fds,tuple)
        and all(type(fd)is int and fd>=3 for fd in guardian_lock_fds),'explicit fleet guardian descriptors required')
    for fd in guardian_lock_fds:os.fstat(fd)
    scope=dict(fn.__globals__);base=scope['base'];proxy=SimpleNamespace(**base.__dict__)
    original_guard=base._run_in_process_group
    def guarded(command,**options):
        native=options.get('guardian_lock_fds')
        require(isinstance(native,tuple)and native,'original workbook lock guardian required')
        return original_guard(command,**dict(options,guardian_lock_fds=tuple(dict.fromkeys((*native,*guardian_lock_fds)))))
    proxy._run_in_process_group=guarded;scope['base']=proxy
    selected=FunctionType(fn.__code__,scope,fn.__name__,fn.__defaults__,fn.__closure__)
    selected.__kwdefaults__=fn.__kwdefaults__
    return selected(*args,**kwargs)


def authorize(bound,intent):
    p,q,proof,warnings,env,D,w,s,l,validate,runner=context(bound)
    B,I,_,W,_,_,root,controls=env
    directory=Path(p['output'])/'adoption'
    seconds=guard_seconds(p)
    require(intent==binding(directory/'guard-intent.json') and read(intent)==dict(schema_version=SCHEMA,plan=bound,seconds=seconds,memory_bytes=8589934592,models=False),'exact derivative guard required')
    require(sys.argv==[__file__,'authorize-child','--plan',bound['path'],'--sha256',bound['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']],'actual derivative child argv differs')
    no_models();live(p)
    with(w.run/'worker.lock').open('a+b')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=W.active(w,s,l)[2];require(before['path']==q['tracker']['path']and before['sha256']==l.sha256,'pre-apply lease changed')
        proof,warnings=validate(s)
        # Preserve the exact original current tracker before any later lane CAS.
        tracker_copy=directory/'tracker-before.json'
        with tracker_copy.open('xb')as f:f.write(Path(before['path']).read_bytes());f.flush();os.fsync(f.fileno())
        require(binding(tracker_copy)['sha256']==before['sha256'],'historical tracker copy changed')
        V=W.producer.skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
        original_coverage=V.clause_covered
        try:
            if q['schema_version']=='fleet-dialogue-remaining-derivative-review/v1':V.clause_covered=module('_adoption_remaining_checker',q['checker']['selected']).clause_covered
            extra=dict(source_warning_proof=proof['source_warning_proof'])if 'retained_context'in p else {}
            V.apply(s.content.stage,Path(q['candidate']['path']),read(q['claims']),read(p['review_result'])['review'],require_review_pass=True,skip_smoke=True,source_workbook=q['golden'],**extra)
        finally:V.clause_covered=original_coverage
        from scripts.batch.harbor_model_review_provenance import Call
        reviewed=Call(**read(p['review_result'])['call'])
        new=W.refreshed(replace(s,content=replace(s.content,dialogue_review=reviewed.parsed,dialogue_reviewed_draft=q['candidate']),semantic=replace(s.semantic,dialogue_call=reviewed,dialogue_reviewed_claims=q['claims'],dialogue_writer_calls=proof['writer_calls'])))
        old_intent,old_evidence=predecessor(q)
        old_directory=Path(old_intent['path']).parent if 'retained_context'in p else Path(q['blocked']['path']).parent
        cert=keep(directory/'certificate.json',dict(schema_version=SCHEMA,plan=bound,review_result=p['review_result'],old_directory=str(old_directory),initial=binding(w.run/'initial.json'),selection=I.codec.encode(new),lease=asdict(l),intent=old_intent,tracker_before=binding(tracker_copy),history=proof['evidence'],lease_history=lease_records(p),warnings=warnings,author_generation=2,review_ordinal=proof['round'],models_dispatched=0))
        if q['schema_version']=='fleet-dialogue-remaining-derivative-review/v1':
            author_directory=directory/'author-generation-02';author_directory.mkdir()
            keep(author_directory/'complete.json',dict(step='dialogue-paraphraser',status='completed',selection=I.codec.encode(new),lease=asdict(l),intent=old_intent,source_revision=cert))
        completed=keep(directory/'complete.json',dict(step='dialogue-review',status='completed',selection=I.codec.encode(new),lease=asdict(l),intent=old_intent,source_revision=cert))
        old=read(q['descriptor'])
        descriptor=keep(directory/'descriptor.json',dict(old,initial=dict(selection=I.codec.encode(new),lease=B.encode(l)),origin=[*old['origin'],q['descriptor'],*old_evidence,bound,p['review_result'],cert]))
        result=keep(directory/'result.json',I._result('STEP_COMPLETE',descriptor,new,l,checkpoint=completed,next_step='final-runtime',evidence=[*old_evidence,bound,p['review_result'],completed]))
        I.selected(descriptor);I._prior(result,descriptor)


def authorization(bound,env):
    p=read(bound);B,I=env[:2];directory=Path(p['output'])/'adoption'
    outcome=binding(directory/'outcome.json');o=read(outcome);resource=read(o['resource'])
    require(o['status']=='COMPLETED_OPERATION' and o['result']==binding(directory/'result.json') and o['automatic_retry']is False,'derivative apply guard did not complete')
    seconds=guard_seconds(p)
    require(resource['returncode']==0 and resource['failure']is None and resource['seconds']==seconds and resource['memory_bytes']==8589934592 and resource['intent']==binding(directory/'guard-intent.json') and resource['log']==binding(directory/'step.log'),'derivative guard changed')
    require(read(resource['intent'])==dict(schema_version=SCHEMA,plan=bound,seconds=seconds,memory_bytes=8589934592,models=False),'derivative authorization differs')
    require(resource['command']==[sys.executable,'-I','-B',p['helper']['path'],'authorize-child','--plan',bound['path'],'--sha256',bound['sha256'],'--intent',resource['intent']['path'],'--intent-sha256',resource['intent']['sha256']],'derivative actual guard argv differs')
    value=read(o['result']);I.selected(value['descriptor']);I._prior(o['result'],value['descriptor'])
    require(value['status']=='STEP_COMPLETE'and value['next_step']=='final-runtime','derivative handoff differs')
    return outcome,o['result'],value


def run(bound):
    p,q,proof,warnings,env,D,w,s,l,validate,runner=context(bound)
    B,I,_,W,_,config,root,controls=env
    if p.get('continuation')is not None:return continued_run(bound,(p,q,proof,warnings,env,D,w,s,l,validate,runner))
    if 'retained_context'in p:require(p['retained_context']['takeover_complete']is not None,'original failed consumer needs completed authorized custody before adoption')
    seconds=guard_seconds(p)
    no_models();live(p);before=W.active(w,s,l)[1]
    require(before['leases']['task_agent']['expires_at']-time.time_ns()>60*10**9,'live lease runway required before supported same-owner renewal')
    out=Path(p['output'])
    if 'renewed_preparation'in p:renewed_preparation(p)
    require(not out.exists(),'derivative transition already attempted; no replay')
    D.operation_namespace(config,p['operations'])
    with D.fleet_slot(p['workbook'])as fleet_fds:
        fd=os.open(B._controls(controls),os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            out.mkdir()
            from scripts.batch import harbor_tracker_lifecycle as life
            L=module('_adoption_selected_lease',p['renewal_module'])
            require(L.DURATION_NS==7200*10**9 and L.SCHEMA=='fleet-validated-continuation-lease/v1','selected original continuation lease differs')
            tracker=w.context.tracker_root/(p['workbook']+'.json')
            if 'renewed_preparation'not in p:
                with(w.run/'worker.lock').open('a+b')as native_lock:
                    fcntl.flock(native_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    before=W.active(w,s,l)[2]
                    with(out/'lease-before.json').open('xb')as f:f.write(tracker.read_bytes());f.flush();os.fsync(f.fileno())
                    require(binding(out/'lease-before.json')['sha256']==before['sha256'],'pre-renewal archive differs')
                    l,renewal=L.renew(life=life,read=read,record=binding,keep=keep,path=tracker,lease=l,authority_record=bound,validation=lambda current,selected:validate(s)[0]['evidence'],custody=lambda:live(p),directory=out/'lease',retain_sufficient=True)
                    with(out/'lease-after.json').open('xb')as f:f.write(tracker.read_bytes());f.flush();os.fsync(f.fileno())
                    require(binding(out/'lease-after.json')['sha256']==l.sha256,'completed renewal archive differs')
            # Refresh only authenticated lease state; rebuilding retained module
            # stacks in this warm interpreter contaminates historical imports.
            require(validate.refresh_lease()==l,'renewed typed lease differs')
            validate.refresh_runner()
            tracker=w.context.tracker_root/(p['workbook']+'.json')
            require(read(binding(tracker))['leases']['task_agent']['expires_at']-time.time_ns()>(seconds+3330+60)*10**9,'renewed tail runway insufficient')
            directory=out/'adoption';directory.mkdir()
            intent=keep(directory/'guard-intent.json',dict(schema_version=SCHEMA,plan=bound,seconds=seconds,memory_bytes=8589934592,models=False))
            command=[sys.executable,'-I','-B',p['helper']['path'],'authorize-child','--plan',bound['path'],'--sha256',bound['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
            rc=None;failure=None;metrics={}
            try:
                with(directory/'step.log').open('xb')as log:
                    rc=B._run_in_process_group(command,stdout=log,timeout_seconds=seconds,memory_limit_bytes=8589934592,grace_seconds=3,metrics=metrics,cwd=root,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},guardian_lock_fds=(fd,*fleet_fds))
                    log.flush();os.fsync(log.fileno())
            except Exception as exc:failure=dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
            resource=keep(directory/'resource.json',dict(intent=intent,returncode=rc,failure=failure,metrics=metrics,log=binding(directory/'step.log'),command=command,seconds=seconds,memory_bytes=8589934592))
            result=binding(directory/'result.json')if rc==0 and failure is None and(directory/'result.json').exists()else None
            keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION'if result else'RECONCILIATION_REQUIRED',resource=resource,result=result,automatic_retry=False))
        finally:os.close(fd)
        _,checkpoint,value=authorization(bound,env)
        # Exactly the three original post-review phases, with their original guards.
        # Model capability stays false in both original intent and actual cold child.
        operations=create_operations(p['operations'])
        completed=[]
        for ordinal,phase in enumerate(('final-runtime','dialogue-commit','enqueue')):
            require(value['next_step']==phase,'unexpected remaining instruction phase')
            live(p)
            outcome=instruction_step(I,'step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,operations/('%03d'%ordinal),models=False,commands=True,containers=True,guardian_lock_fds=fleet_fds)
            completed.append(outcome);o=read(outcome)
            if o['status']!='COMPLETED_OPERATION':break
            checkpoint=o['result'];value=read(checkpoint)
            if value['status']!='PUBLICATION_READY':I._prior(checkpoint,value['descriptor'])
            if value['status'] not in('STEP_COMPLETE','PUBLICATION_READY'):break
        ready=value['status']=='PUBLICATION_READY' and len(completed)==3 and read(completed[-1])['status']=='COMPLETED_OPERATION'
        keep(out/'result.json',dict(schema_version=SCHEMA,plan=bound,status='PUBLICATION_READY'if ready else'RECONCILIATION_REQUIRED',checkpoint=checkpoint,operations=completed,models_dispatched=0,review_consumed=proof['round'],publication_performed=False,automatic_retry=False))


def continued_run(bound,loaded):
    p,q,proof,warnings,env,D,w,s,l,validate,runner=loaded;B,I,_,W,_,config,root,controls=env
    no_models();live(p);_,checkpoint,value=authorization(p['continuation']['plan'],env)
    if p['continuation'].get('completed_outcomes'):
        for outcome in p['continuation']['completed_outcomes']:checkpoint,value=D.successful(outcome,env)
    _,selected,lease=I._prior(checkpoint,value['descriptor']);current=W.active(w,selected,lease)[1]
    require(current['leases']['task_agent']['expires_at']-time.time_ns()>3330*10**9,'remaining original tail runway insufficient')
    phases=('enqueue',)if p['continuation'].get('completed_outcomes')else('final-runtime','dialogue-commit','enqueue')
    require(not W.step_directory(w,phases[0]).exists(),'remaining phase has owning work; reconcile before continuation')
    out=Path(p['continuation']['output']);require(not out.exists(),'completion continuation already attempted');D.operation_namespace(config,p['operations'])
    out.mkdir();operations=create_operations(p['operations']);completed=[]
    with D.fleet_slot(p['workbook'])as fleet_fds:
        for ordinal,phase in enumerate(phases):
            require(value['next_step']==phase,'unexpected completion phase');live(p)
            outcome=instruction_step(I,'step',dict(descriptor=value['descriptor'],checkpoint=checkpoint),controls,operations/('%03d'%ordinal),models=False,commands=True,containers=True,guardian_lock_fds=fleet_fds)
            completed.append(outcome);o=read(outcome)
            if o['status']!='COMPLETED_OPERATION':break
            checkpoint=o['result'];value=read(checkpoint)
            if value['status']!='PUBLICATION_READY':I._prior(checkpoint,value['descriptor'])
            if value['status']not in('STEP_COMPLETE','PUBLICATION_READY'):break
    ready=value['status']=='PUBLICATION_READY' and len(completed)==len(phases) and read(completed[-1])['status']=='COMPLETED_OPERATION'
    keep(out/'result.json',dict(schema_version=SCHEMA,plan=bound,status='PUBLICATION_READY'if ready else'RECONCILIATION_REQUIRED',checkpoint=checkpoint,operations=completed,retained_adoption=p['continuation']['plan'],models_dispatched=0,review_consumed=proof['round'],publication_performed=False,automatic_retry=False))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('run','probe','authorize-child','owning-child'));parser.add_argument('--plan',required=True);parser.add_argument('--sha256');parser.add_argument('--plan-sha256');parser.add_argument('--intent');parser.add_argument('--intent-sha256');parser.add_argument('--kind');args=parser.parse_args()
    bound=dict(path=args.plan,sha256=args.sha256 or args.plan_sha256)
    if args.action=='run':run(bound)
    elif args.action=='authorize-child':authorize(bound,dict(path=args.intent,sha256=args.intent_sha256))
    else:
        p,q,proof,warnings,env,D,w,s,l,validate,runner=context(bound,probe=True,owning_intent=dict(path=args.intent,sha256=args.intent_sha256)if args.action=='owning-child'else None)
        no_models()
        if args.action=='probe':
            if p.get('continuation')is not None:
                _,checkpoint,value=authorization(p['continuation']['plan'],env)
                for outcome in p['continuation'].get('completed_outcomes',[]):checkpoint,value=D.successful(outcome,env)
                _,s,l=env[1]._prior(checkpoint,value['descriptor'])
            pending='retained_context'in p and p['retained_context']['takeover_complete']is None
            if not pending:env[3].active(w,s,l)
            print(json.dumps(dict(status='DERIVATIVE_HISTORY_VALIDATED_CUSTODY_PENDING'if pending else'DERIVATIVE_ADOPTION_VALIDATED_NOT_STARTED',plan=bound,claims=proof['claims'],review_ordinal=proof['round'],models_dispatched=0,task_mutations=0,qualified=False)))
        else:
            require(args.kind=='instruction','only original instruction tail authorized')
            authorization(p.get('continuation',{}).get('plan',bound),env)
            intent=dict(path=args.intent,sha256=args.intent_sha256);v=read(intent)
            require(v['models']is False and v['commands']is True and v['containers']is True and v['action']=='step','tail capability differs')
            prior=read(v['arguments']['checkpoint']);require(prior['next_step']in('final-runtime','dialogue-commit','enqueue'),'unselected owning phase')
            runner.validate_child(env[1],base=env[0],kind='instruction',reader_plan=bound,child_wrapper=p['helper'],intent=intent,reader_evidence=dependencies(p))
            live(p);env[1].child(intent)
    return 0

if __name__=='__main__':raise SystemExit(main())
