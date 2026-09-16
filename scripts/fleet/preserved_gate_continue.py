"""Resume an exact recorded owning gate failure through original I/W/D APIs."""
import argparse,json,os,sys,hashlib,importlib.util
from contextlib import contextmanager,ExitStack,nullcontext
from dataclasses import replace
from pathlib import Path

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    path=Path(path).resolve();return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'preserved gate input changed');return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'preserved gate implementation changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value

def function_sha(fn):
    import ast,inspect,textwrap
    return hashlib.sha256(ast.dump(ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0],include_attributes=False).encode()).hexdigest()

SCHEMA='fleet-preserved-owning-gate-continuation/v1'
FIELDS={'schema_version','helper','previous','core','profile','owner','fence','output','workbook','source','utilities','capacity_policy','semantic_archive','witness'}

def load(row):
    p=read(row);require(set(p)-{'settled_repair','parent_context','unstarted_preparation'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed preserved gate continuation required')
    old=read(p['previous']);require(old['workbook']==p['workbook']and old['source']==p['source']and ('settled_repair'in p or 'unstarted_preparation'in p or p['fence']==old['fence']+1)
        and p['owner']!=old['owner']and p['output']!=old['output'],'repair changes source or repeats old owner')
    S=module('preserved_original_adoption',old['helper']);K=module('preserved_owning_gate',p['core']);profile=read(p['profile'])
    require(profile['implementation']==p['core']and profile['source']==p['source']and profile['workbook']==p['workbook'],
            'repair profile source or implementation differs')
    U=module('preserved_gate_utilities',p['utilities'])
    if 'settled_repair'in p:settled(p,allow_progress=True)
    if 'unstarted_preparation'in p and 'settled_repair'not in p:unstarted_preparation(p)
    return p,old,S,K,profile,U

def unstarted_preparation(p):
    """Reconcile only the stopped nested-admission failure before any mutation."""
    require('settled_repair'not in p and 'parent_context'in p,'unstarted dialogue preparation required')
    v=read(p['unstarted_preparation'])
    require(set(v)=={'schema_version','plan','worker_outcome','process','census'}
        and v['schema_version']=='fleet-preserved-gate-unstarted-preparation/v1','closed stopped preparation required')
    old=read(v['plan']);changed={'helper','owner','fence','output','unstarted_preparation'}
    require('unstarted_preparation'not in old and {k:x for k,x in p.items()if k not in changed}=={k:x for k,x in old.items()if k not in changed}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'stopped preparation changed original authority')
    home=Path(old['output']);out=read(v['worker_outcome']);wi=read(out['intent']);job=read(wi['job']);process=read(v['process'])
    require(out['status']=='RECONCILIATION_REQUIRED'and out['returncode']==1 and out['failure_type']is None
        and out['result']is None and out['automatic_retry']is False and wi['owner']==old['owner']
        and job['driver']==old['helper']and job['source']==old['source']and job['inputs'][0]==v['plan']
        and job['result']==str(home/'result.json')and v['worker_outcome']==binding(Path(job['run'])/'outcome.json')
        and process['job']==wi['job']and process['command'][process['command'].index('--owner')+1]==old['owner'],
        'stopped preparation worker differs')
    log=Path(out['log']['path']);require(binding(log)==out['log']and log.read_text().rstrip().endswith('ValueError: original owning predicate changed')
        and 'with initial_active_scope(W,before,worker)'in log.read_text()and 'K.prepare'in log.read_text(),
        'not the exact nested admission failure')
    profile=read(old['profile'])
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False)
        and read(binding(home/'repair/intent.json'))==dict(schema_version='harbor-preserved-owning-gate-repair/v1',profile=old['profile'],tracker=profile['tracker'],automatic_retry=False)
        and {x.name for x in home.iterdir()}=={'intent.json','repair'}
        and {x.name for x in (home/'repair').iterdir()}=={'intent.json'}
        and binding(profile['custody_after']['path'])==profile['custody_after']
        and profile['custody_after']['sha256']==profile['tracker']['sha256'], 'stopped preparation has additional effects')
    census=read(v['census']);require(census['workbook']==p['workbook']and census['read_only']is True
        and read(census['local'])['matches']==[]and len(census['hosts'])==8,'complete stopped census required')
    require(all(read(host)['status']=='OBSERVED'and read(host)['matches']==[]for host in census['hosts']), 'prior task remains active or uncertain')
    central=read(census['central']);require(len(central['workbooks'])==1 and central['workbooks'][0]['state']=='hold'
        and central['workbooks'][0]['owner']==old['owner']and central['workbooks'][0]['fence']==old['fence']
        and all(op['state']=='confirmed'for op in central['operations']), 'prior ownership or model operation remains uncertain')
    require(type(process['pid'])is int and process['pid']>1 and not Path('/proc',str(process['pid'])).exists(), 'prior process remains active')
    return home

def instruction_profile(old,profile,I):
    """Select only an already declared relocated original checkpoint reader."""
    selected=dict(profile['owning'])
    if 'checkpoint_reader' not in old:return selected
    import ast
    witness=old['checkpoint_reader']
    require(set(witness)=={'module','prior','decode','history'}and binding(witness['module']['path'])==witness['module'],
            'declared relocated instruction reader changed')
    tree=ast.parse(Path(witness['module']['path']).read_text())
    definitions=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n.name=='_prior']
    require(len(definitions)==1 and hashlib.sha256(ast.dump(definitions[0],include_attributes=False).encode()).hexdigest()==witness['prior']
        and function_sha(I._prior)==witness['prior'], 'current instruction reader is not the declared original relocation')
    # The retained helper's owning_runtime independently authenticates decoder,
    # history and globals before this consumer. The immutable repair profile
    # and its original source-reader declaration remain unchanged.
    return dict(selected,prior=witness['prior'])

@contextmanager
def archived_prefix_factory(S,old,C,evidence):
    """Build a retained prefix clone from its exact original evidence function.

    The semantic archive wrapper stays installed during validation. Only the
    synchronous original prefix factory sees its original function, so its
    own closed archive checker is attached to the actual recursive reader.
    """
    if not hasattr(S,'archived_prefix_reader'):
        yield;return
    original=S.archived_prefix_reader
    require(original.__globals__.get('__file__')==S.__file__ and binding(S.__file__)==old['helper'],
            'historical prefix factory is not the declared original helper')
    def factory(*args,**kwargs):
        current=C.evidence_records;C.evidence_records=evidence
        try:return original(*args,**kwargs)
        finally:C.evidence_records=current
    S.archived_prefix_reader=factory
    try:yield
    finally:S.archived_prefix_reader=original

def settled(p,*,allow_progress=False):
    v=read(p['settled_repair']);prechild=v.get('schema_version')=='fleet-preserved-gate-settled-preparation/v1'
    prelaunch=v.get('schema_version')=='fleet-preserved-gate-settled-prelaunch/v1'
    require(set(v)==({'schema_version','plan','worker_outcome','process','observation'}if prechild else {'schema_version','plan','worker_outcome','failed_outcome','process','observation'})
        and (prechild or prelaunch or v['schema_version']=='fleet-preserved-gate-settled-repair/v1'),'closed settled original repair required')
    old=read(v['plan']);changes={'helper','owner','fence','output','settled_repair'}
    require('settled_repair'not in old and {k:x for k,x in p.items()if k not in changes}=={k:x for k,x in old.items()if k not in changes}
        and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],
        'settled repair changed completed authority')
    home=Path(old['output']);outer=read(v['worker_outcome']);wi=read(outer['intent']);job=read(wi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None and outer['automatic_retry']is False
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==old['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256'],'settled repair worker differs')
    if prechild:
        require(outer.get('result')is None and not(home/'first-step').exists(), 'completed preparation has a first child or result')
        log=Path(outer['log']['path']);require(binding(log)==outer['log']and log.read_text().rstrip().endswith('ValueError: original preserved-repair instruction witnesses changed')
            and 'K.instruction_scope' in log.read_text()and 'in run' in log.read_text(), 'not the original pre-child instruction witness failure')
    elif prelaunch:
        stopped_legacy_guard(home,v)
    else:
        failed=read(v['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent']);side=read(binding(home/'first-step/preserved-gate-bootstrap.json'))
        require(v['failed_outcome']==binding(home/'first-step/outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
            and failed.get('result')is None and guard['returncode']==1 and guard.get('failure')is None
            and intent['action']=='step'and intent['arguments']==dict(descriptor=binding(home/'repair/descriptor.json'),checkpoint=binding(home/'repair/result.json'))
            and side['plan']==v['plan']and side['helper']==old['helper']and side['intent']==guard['intent'],
            'settled repair has a different child or completed effects')
        log=Path(guard['log']['path']);require(binding(log)==guard['log']and log.read_text().rstrip().endswith('ValueError: original preserved-repair instruction witnesses changed')
            and 'K.instruction_scope' in log.read_text(),'not the exact pre-worker cold witness failure')
    process=read(v['process']);obs=read(v['observation']);receipt=read(binding(home/'repair/permit.json'))
    expected_tracker=dict(path=read(old['profile'])['tracker']['path'],sha256=receipt['after_repair']['sha256'])
    expected_obs=dict(schema_version='fleet-stopped-preserved-repair/v1',process=v['process'],worker_outcome=v['worker_outcome'],processes=[],tracker=expected_tracker,observed_at_ns=obs['observed_at_ns'])
    if prechild:expected_obs['unstarted_first_step']=True
    if prelaunch:expected_obs['unstarted_child']=True
    require(obs==expected_obs
        and type(obs['observed_at_ns'])is int and process['job']==wi['job']
        and process['command'][process['command'].index('--owner')+1]==old['owner']
        and not(home/'authoring-job.json').exists(),
        'settled repair lacks stopped custody or already advanced')
    require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=v['plan'],automatic_retry=False),
            'settled repair original parent changed')
    if not allow_progress:require(not(home/'repair/steps').exists(),'settled repair already advanced; use exact current checkpoint')
    return home

def stopped_legacy_guard(home,v):
    """The original runner sealed an AttributeError before invoking a child."""
    failed=read(v['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent'])
    error="'types.SimpleNamespace' object has no attribute 'guard_seconds'"
    require(v['failed_outcome']==binding(home/'first-step/outcome.json')and failed['status']=='RECONCILIATION_REQUIRED'
        and failed.get('result')is None and failed['automatic_retry']is False and guard['returncode']is None
        and guard['metrics']=={}and guard['failure']==dict(type='AttributeError',message_sha256=hashlib.sha256(error.encode()).hexdigest())
        and intent['action']=='step'and intent['arguments']==dict(descriptor=binding(home/'repair/descriptor.json'),checkpoint=binding(home/'repair/result.json'))
        and intent['models']is False and intent['commands']is False and intent['containers']is False
        and intent['timeout_seconds']==3300 and intent['memory_limit_bytes']==8589934592,
        'not the exact stopped legacy guard before child launch')
    require(guard['log']==binding(home/'first-step/step.log')and Path(guard['log']['path']).read_bytes()==b''
        and {x.name for x in (home/'first-step').iterdir()}=={'intent.json','resource.json','outcome.json','step.log'},
        'legacy guard has child or additional effects')

def repair_home(p):return settled(p,allow_progress=True)if 'settled_repair'in p else Path(p['output'])

def completed_parent(p,old,S,profile):
    """The actual failed guard and its original predecessor stay authoritative."""
    failed=read(profile['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent']);blocked=read(profile['failed_step'])
    require(failed['status']=='COMPLETED_OPERATION'and failed['result']is not None
        and guard['returncode']==0 and guard.get('failure')is None and intent['action']=='step',
        'not a settled original owning gate failure')
    result=read(failed['result']);prior=read(intent['arguments']['checkpoint']);descriptor=intent['arguments']['descriptor']
    require(result['status']=='OWNING_GATE_FAILED'and result['checkpoint']==profile['failed_step']
        and result['descriptor']==descriptor==profile['original_descriptor']and prior['status']=='STEP_COMPLETE'
        and prior['next_step']==blocked['step']and result['selection']==blocked['selection'],
        'owning failure detached from original completed predecessor')
    side=read(profile['failed_sidecar'])
    require(side['schema_version']==S.SCHEMA and side['plan']==p['previous']and side['helper']==old['helper']
        and side['intent']==guard['intent']and side['kind']=='instruction'and side['automatic_retry']is False,
        'failed cold child detached from original parent')
    outer=read(profile['worker_outcome']);oi=read(outer['intent']);job=read(oi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and oi['owner']==old['owner']and job['driver']==old['helper']
        and job['source']==p['source']and job['command'][job['command'].index('--plan')+1]==p['previous']['path']
        and job['command'][job['command'].index('--sha256')+1]==p['previous']['sha256'],
        'failed worker source or original launch differs')
    require(read(binding(Path(old['output'])/'intent.json'))==dict(schema_version=S.SCHEMA,plan=p['previous'],automatic_retry=False),
            'original parent intent changed')
    return intent['arguments']['checkpoint']

@contextmanager
def context(row,*,continued=False,invocation=None,probe=False):
    p,old,S,K,profile,U=load(row)
    if 'parent_context'in p:
        with dialogue_context(row,p,old,S,K,profile,U,continued=continued,invocation=invocation,probe=probe)as args:yield args
        return
    prior=completed_parent(p,old,S,profile);home=Path(p['output'])
    require(home.is_absolute()and not home.is_relative_to(Path(old['output'])),'repair output overlaps old operation')
    if continued:require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=row,automatic_retry=False),'repair parent intent differs')
    else:
        require(not home.exists(),'repair already started; use exact retained completion')
        if 'settled_repair'in p:settled(p)
    original_current=S.current_checkpoint;original_runtime=S.owning_runtime
    require(p['witness']==dict(current_checkpoint=function_sha(original_current),owning_runtime=function_sha(original_runtime)),
            'original historical/current boundary witness changed')
    original_home=repair_home(p)
    repair=binding(original_home/'repair/permit.json')if(original_home/'repair/permit.json').exists()else None
    require(not continued or repair is not None,'repair cold child requires completed snapshot and handoff')
    @contextmanager
    def runtime(A,selected,*,receipt=None):
        require(selected==old or selected==dict(old,_binding=p['previous']),'foreign historical adoption plan')
        if 'semantic_archive'in old:
            with original_runtime(A,dict(selected,semantic_archive=p['semantic_archive']),receipt=receipt):yield
        else:
            reader=module('preserved_completed_semantic_archive',p['semantic_archive']['helper'])
            from scripts.batch import harbor_complete_readiness as complete
            evidence=complete.evidence_records
            require(function_sha(evidence)==p['semantic_archive']['profile']['functions']['evidence'],
                    'historical evidence factory must start from the declared original')
            with archived_prefix_factory(S,old,complete,evidence),original_runtime(A,selected,receipt=receipt),reader.installed(A,p['semantic_archive']['profile'],receipt):yield
    def current(selected,env,worker,renewed,A,unused,*,installed=False):
        C,I,_,W=env[:4]
        with ExitStack()as stack:
            if not installed:
                stack.enter_context(runtime(A,old,receipt=binding(S.adoption_home(old)/'permit/permit.json')))
                if 'interrupted_step'in old:stack.enter_context(S.interrupted_runtime(old,W,A,continued=True))
            if repair is not None:stack.enter_context(K.instruction_scope(W,I,receipt=repair,profile=instruction_profile(old,profile,I)))
            if invocation is None:
                w,controls,_=I.selected(profile['original_descriptor']);_,s,previous=I._prior(prior,profile['original_descriptor'])
                blocked=read(profile['failed_step']);require(I.codec.encode(s)==blocked['selection'],'repair predecessor selection differs')
                if repair is None:
                    value=read(profile['custody_after']);lease=value['leases']['task_agent'];l=W.Lease(lease['owner'],lease['token'],value['revision'],profile['custody_after']['sha256'])
                    _,_,failed,_=K.validate_failure(__import__('scripts.batch.harbor_lane_notes',fromlist=['read']),profile)
                    K.validate_custody(W,profile,failed,l)
                    require(binding(profile['tracker']['path'])==profile['tracker'],'live failed custody drifted')
                    stack.enter_context(K.initial_active_scope(W,value,w))
                else:
                    _,s,l=I._prior(binding(original_home/'repair/result.json'),binding(original_home/'repair/descriptor.json'))
            else:
                intent,sidecar=invocation;side=read(sidecar);D=module('preserved_actual_dispatcher',read(old['original_job'])['driver'])
                kind,actual=U.retained_command(side['original_command'],p,D,env)
                require(actual==intent and side['plan']==row and side['helper']==p['helper']and side['schema_version']==SCHEMA
                    and Path(sidecar['path'])==Path(intent['path']).parent/'preserved-gate-bootstrap.json',
                    'repair child detached from exact parent')
                if kind=='instruction':
                    args=read(intent)['arguments'];w,controls,_=I.selected(args['descriptor']);_,s,l=I._prior(args['checkpoint'],args['descriptor'])
                else:
                    result,w,_,s,l=D.renewal_context(env,read(intent)['checkpoint']);_,controls,_=I.selected(result['descriptor'])
            require(profile['trusted_public_key']==w.context.trusted_public_key and controls==env[7]and w.run==worker.run and w.root==worker.root and w.context==worker.context
                and w.campaign==worker.campaign and s.content.bindings['original_raw_source']==p['source'],
                'repair changed original worker, source or live controls')
            W.active(w,s,l)
            return w,s,l
    S.current_checkpoint=current;S.owning_runtime=runtime
    try:
        # The original reader validates its completed parent and true archived
        # review bytes. The callback above alone owns current checkpoint checks.
        with S.context(p['previous'],continued=True,invocation=('current-checkpoint',None))as(_,M,_,D,env,w,s,l,A,raw,job):
            with ExitStack()as stack:
                if repair is not None:stack.enter_context(K.instruction_scope(env[3],env[1],receipt=repair,profile=instruction_profile(old,profile,env[1])))
                yield p,S,K,profile,U,D,env,w,s,l,A,raw,job
    finally:S.current_checkpoint=original_current;S.owning_runtime=original_runtime


def custody(p,U,w):
    root=w.root
    if 'parent_context'in p:
        old=read(p['previous']);runtime=read(old['retained_context']['fleet_runtime'])
        for row in runtime['files'].values():require(binding(row['path'])==row,'declared fleet runtime changed')
        root=Path(runtime['root'])
    return U.custody(p,root)

@contextmanager
def source_repair_activity(W,K,profile,receipt):
    """Recheck original source prefix when begin_repair invalidated its lanes.

    This changes no tracker state. The original owning commit and canonical
    clear transition remain responsible for durable gate results.
    """
    from types import FunctionType
    if 'source_context'not in profile:
        yield;return
    value,base=K.validate(W,receipt)
    if value.get('failure_result',{}).get('gate')!='source':
        yield;return
    require(W.read(value['profile'])==profile,'source activity repair profile changed')
    original=W.active;guard=original.__globals__['require']
    def active(worker,selection,lease):
        def checked(ok,message):
            if not ok and message=='environment lane must pass before instructions':
                current,snapshot=K.validate(W,receipt)
                require(current==value and snapshot==base,'source repair permit changed')
                tracker=Path(profile['tracker']['path']);before=W.read(binding(tracker))
                require(str(worker.root)==value['worker_root']and str(worker.run)==value['worker_run']
                    and binding(tracker)['sha256']==lease.sha256 and before['revision']==lease.revision
                    and before['promotion_failure']==base['promotion_failure']
                    and selection.content.bindings['original_raw_source']==value['source']
                    and before['bindings']['original_raw_source']==value['source'],
                    'source prefix admission changed live selection or custody')
                from scripts.batch import harbor_lane_notes as notes,harbor_completed_lanes as completed
                kind=K.intended_gate(notes,profile,value['failure_result'])
                prefix=profile['source_context']['prefix']
                require(prefix==W.record(Path(worker.root)/'scripts/batch/harbor_instruction_repair.py'),
                    'source activity prefix is not the original worker implementation')
                reader=K._module(W,prefix,'source_activity_original_prefix')
                reader.check_completed_gate=W.check_completed_gate
                rows=reader.prefix(before,selection,worker.context,stop=kind)
                expected=completed.STEPS[:[x[0]for x in completed.STEPS].index(kind)]
                require([r['kind']for r in rows]==[r[0]for r in expected]
                    and all(r['result']['passed']is True and r['result']['lane']==lane for r,(_,lane)in zip(rows,expected))
                    and any(lane=='harbor-environment-packager'for _,lane in expected),
                    'original source prefix omitted or failed an upstream gate')
                return guard(True,message)
            return guard(ok,message)
        scope=dict(original.__globals__,require=checked)
        selected=FunctionType(original.__code__,scope,original.__name__,original.__defaults__,original.__closure__)
        selected.__kwdefaults__=original.__kwdefaults__
        return selected(worker,selection,lease)
    W.active=active
    try:yield
    finally:W.active=original


@contextmanager
def repair_scope(p,S,K,profile,env,receipt):
    W,I=env[3],env[1]
    with K.instruction_scope(W,I,receipt=receipt,profile=instruction_profile(read(p['previous']),profile,I)),source_repair_activity(W,K,profile,receipt):
        if 'parent_context'not in p:
            yield;return
        # The retained dialogue parent uses private original-function namespaces.
        # Keep only these authenticated repair aliases synchronized in that scope.
        saved=[]
        for target,names in ((W,('active','step_directory','_step','_operation','check_completed_gate')),(I,('_prior',))):
            for name in names:
                saved.append((target,name,target.declared_namespace.get(name)))
                target.declared_namespace[name]=getattr(target,name)
        try:yield
        finally:
            for target,name,value in saved:target.declared_namespace[name]=value

def dialogue_parent(p,old,S,profile):
    """Authenticate the original declared cold child and its actual failed guard."""
    selected=p['parent_context']
    require(set(selected)=={'kind','claims_reader','context','semantic_review'}
        and selected['kind']=='dialogue_source_revision'
        and old['schema_version']==S.SCHEMA=='fleet-dialogue-source-revision-adoption/v1'
        and 'retained_context'in old and function_sha(S.context)==selected['context'],
        'closed retained dialogue parent required')
    failed=read(profile['failed_outcome']);guard=read(failed['resource']);intent=read(guard['intent'])
    result=read(failed['result']);blocked=read(profile['failed_step']);prior=read(intent['arguments']['checkpoint'])
    require(failed['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard['failure']is None
        and intent['action']=='step'and intent['models']is False
        and intent['commands']is intent['containers']is True
        and result['status']==blocked['status']=='OWNING_GATE_FAILED'
        and result['checkpoint']==profile['failed_step']
        and result['descriptor']==intent['arguments']['descriptor']==profile['original_descriptor']
        and prior['status']=='STEP_COMPLETE'and prior['next_step']==blocked['step']=='dialogue-commit'
        and result['selection']==blocked['selection']==prior['selection'],
        'retained dialogue failure lost original completed runtime')
    side=read(profile['failed_sidecar']);runner=module('preserved_dialogue_original_runner',old['runner'])
    require(set(side)=={'schema_version','adapter','producer_code','base_code','kind','reader_plan','child_wrapper',
        'reader_evidence','intent','original_command','selected_command','timeout_seconds','memory_limit_bytes',
        'original_snapshot_unchanged','semantic_budget_reset','automatic_retry'}
        and side['schema_version']==runner.SCHEMA and side['adapter']==old['runner']
        and side['reader_plan']==p['previous']and side['child_wrapper']==old['helper']
        and side['reader_evidence']==S.dependencies(old)and side['intent']==guard['intent']
        and side['kind']=='instruction'and side['producer_code']==intent['code']
        and side['base_code']==intent['base_code']and side['original_snapshot_unchanged']is True
        and side['semantic_budget_reset']is side['automatic_retry']is False
        and side['timeout_seconds']==intent['timeout_seconds']
        and side['memory_limit_bytes']==intent['memory_limit_bytes']==8589934592,
        'retained dialogue cold adapter differs')
    expected=[side['original_command'][0],'-I','-B',old['helper']['path'],'owning-child','--plan',p['previous']['path'],
        '--plan-sha256',p['previous']['sha256'],'--kind','instruction','--intent',guard['intent']['path'],'--intent-sha256',guard['intent']['sha256']]
    require(side['original_command']==[side['original_command'][0],'-B',side['producer_code']['path'],'_child',guard['intent']['path'],guard['intent']['sha256']]
        and side['selected_command']==expected and Path(profile['failed_sidecar']['path'])==Path(guard['intent']['path']).parent/'declared-runtime-adapter.json',
        'retained dialogue actual cold invocation differs')
    outer=read(profile['worker_outcome']);oi=read(outer['intent']);job=read(oi['job'])
    require(outer['status']=='RECONCILIATION_REQUIRED'and outer['returncode']==1 and outer['failure_type']is None
        and outer['automatic_retry']is False and oi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==p['previous']['path']
        and job['command'][job['command'].index('--sha256')+1]==p['previous']['sha256'],
        'retained dialogue failed worker differs')
    return intent['arguments']['checkpoint'],guard['intent']

@contextmanager
def dialogue_context(row,p,old,S,K,profile,U,*,continued=False,invocation=None,probe=False):
    from types import FunctionType,SimpleNamespace
    prior,original_intent=dialogue_parent(p,old,S,profile);home=Path(p['output'])
    require(home.is_absolute()and not home.is_relative_to(Path(old['output']))
        and home!=Path(old.get('continuation',{}).get('output',old['output'])),'repair overlaps original dialogue operation')
    if continued:require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=row,automatic_retry=False),'repair parent intent differs')
    else:require(not home.exists(),'repair already started; reconcile original transactions')
    args=S.context(p['previous'],probe=True,owning_intent=original_intent)
    _,q,_,_,env,D,worker,original_selection,_,validate,_=args
    original_home=repair_home(p)
    C,I,_,W=env[:4];repair=binding(original_home/'repair/permit.json')if(original_home/'repair/permit.json').exists()else None
    try:
        require(not continued or repair is not None,'repair child requires completed snapshot and handoff')
        S.no_models()
        from scripts.batch import harbor_complete_readiness as complete
        original_review=complete.dialogue_review;selected=p['parent_context']
        require(function_sha(original_review)==selected['semantic_review']and original_review.__code__.co_filename==old['helper']['path'],
            'retained dialogue semantic reader differs')
        claims=module('preserved_dialogue_claims_copy',selected['claims_reader'])
        def reviewed(call,**options):
            claims.semantic_claims(options,q,original_selection,retained=True)
            return original_review(call,**dict(options,claims=q['claims']))
        complete.dialogue_review=reviewed
        private_readiness=W.complete_readiness.__globals__;private_review=private_readiness['dialogue_review']
        require(private_review is original_review,'private dialogue reader differs')
        private_readiness['dialogue_review']=reviewed
        # Reuse original run bytecode and its declared instruction namespace;
        # the existing preserved-child adapter supplies the guarded launch.
        original_run=I.run
        require(hasattr(original_run,'declared_runner')and original_run.__name__=='run', 'declared original instruction runner required')
        scope=dict(original_run.__globals__);scope['base']=C
        run=FunctionType(original_run.__code__,scope,original_run.__name__,original_run.__defaults__,original_run.__closure__)
        run.__kwdefaults__=original_run.__kwdefaults__
        def instruction_run(*args,guardian_lock_fds=(),**kwargs):
            return S.instruction_step(SimpleNamespace(run=run),*args,guardian_lock_fds=guardian_lock_fds,**kwargs)
        S.assign(I,run=instruction_run)
        raw=C._run_in_process_group
        job=dict(read(old['authoring_job']),maximum_steps=2,commands=False,containers=False)
        with ExitStack()as stack:
            if repair is not None:stack.enter_context(repair_scope(p,S,K,profile,env,repair))
            if invocation is None:
                w,controls,_=I.selected(profile['original_descriptor']);_,s,_=I._prior(prior,profile['original_descriptor'])
                if repair is None:
                    before=read(profile['custody_after']);lease=before['leases']['task_agent'];l=W.Lease(lease['owner'],lease['token'],before['revision'],profile['custody_after']['sha256'])
                    from scripts.batch import harbor_lane_notes as notes
                    _,_,failed,_=K.validate_failure(notes,profile);K.validate_custody(W,profile,failed,l)
                    require(binding(profile['tracker']['path'])==profile['tracker'],'failed dialogue custody changed')
                else:_,s,l=I._prior(binding(original_home/'repair/result.json'),binding(original_home/'repair/descriptor.json'))
            else:
                intent,sidecar=invocation;side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
                require(kind=='instruction'and actual==intent and side['plan']==row and side['helper']==p['helper']
                    and side['schema_version']==SCHEMA and Path(sidecar['path'])==Path(intent['path']).parent/'preserved-gate-bootstrap.json',
                    'dialogue repair child detached from exact parent')
                task=read(intent)['arguments'];w,controls,_=I.selected(task['descriptor']);_,s,l=I._prior(task['checkpoint'],task['descriptor'])
            require(w==worker and controls==env[7]and s.content.bindings['original_raw_source']==p['source']
                and w.context.trusted_public_key==profile['trusted_public_key'], 'dialogue repair changed original worker or source')
            if probe:require(not continued and invocation is None and (repair is None or 'settled_repair'in p),'read-only admission requires an unstarted or reconciled repair')
            if not probe or profile['custody_transition']is not None:
                check_dialogue_activity(K,W,w,s,l,before if repair is None and invocation is None else None)
            yield p,S,K,profile,U,D,env,w,s,l,None,raw,job
    finally:
        if 'private_review'in locals():private_readiness['dialogue_review']=private_review
        if 'original_review'in locals():complete.dialogue_review=original_review
        D._derivative_context_manager.close()


def check_dialogue_activity(K,W,worker,selection,lease,failed):
    """End admission's temporary predicate before prepare owns its own scope."""
    with K.initial_active_scope(W,failed,worker) if failed is not None else nullcontext():
        return W.active(worker,selection,lease)


def operations(p,D,config):
    if 'parent_context'in p:
        return Path(config['run_root'])/'dispatcher-v2'/Path(p['output']).name
    namespace=module('preserved_operation_namespace',read(p['previous'])['namespace_module'])
    return namespace.selected_operation_namespace(config,str(Path(config['run_root'])/'dispatcher-v2'/Path(p['output']).name))

def instruction_guard_seconds(I,C,intent):
    value=read(intent)
    require(value['code']==binding(I.__file__)and value['base_code']==binding(C.__file__)
        and value['action']=='step'and value['memory_limit_bytes']==8589934592,'original instruction guard identity changed')
    expected=I.guard_seconds('step',value['arguments'])if hasattr(I,'guard_seconds')else C.SECONDS
    require(type(expected)is int and expected in (1350,3300)and value['timeout_seconds']==expected,
        'original instruction timeout changed')
    return expected


def install(row,p,U,D,env,raw,job):
    C,I=env[:2];ops=operations(p,D,env[5])
    def setup(current):
        require(current==dict(job,outcome=current['outcome'],operations=str(ops)),'original job changed beyond selected repair');return env
    if 'parent_context'in p:
        old=read(p['previous']);S=module('preserved_dialogue_namespace',old['helper']);S.assign(D,setup=setup)
    else:D.setup=setup
    def guarded(command,*args,**options):
        kind,intent=U.retained_command(command,p,D,env);seconds=instruction_guard_seconds(I,C,intent)if kind=='instruction'else read(intent)['seconds']
        require(options['timeout_seconds']==seconds and options['memory_limit_bytes']==8589934592,'original owning guard changed')
        side=U.keep(Path(intent['path']).parent/'preserved-gate-bootstrap.json',dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=command,seconds=seconds,memory_bytes=8589934592,automatic_retry=False))
        return raw([command[0],'-I','-B',p['helper']['path'],'child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']],*args,**options)
    C._run_in_process_group=guarded

def child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated original child required')
    with context(row,continued=True,invocation=(intent,sidecar))as(p,S,K,profile,U,D,env,w,s,l,A,raw,job):
        custody(p,U,w);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
        seconds=instruction_guard_seconds(env[1],env[0],intent)if kind=='instruction'else read(intent)['seconds']
        require(actual==intent and side==dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],seconds=seconds,memory_bytes=8589934592,automatic_retry=False),'original repair guard changed')
        install(row,p,U,D,env,raw,job)
        return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)

def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot repair')
    with context(row)as(p,S,K,profile,U,D,env,w,s,l,A,raw,job):
        C,I,_,W=env[:4];home=Path(p['output']);custody(p,U,w);home.mkdir(parents=True)
        U.keep(home/'intent.json',dict(schema_version=SCHEMA,plan=row,automatic_retry=False))
        if 'settled_repair'in p:
            oldhome=repair_home(p);receipt=binding(oldhome/'repair/permit.json');K.validate(W,receipt)
            descriptor=binding(oldhome/'repair/descriptor.json');checkpoint=binding(oldhome/'repair/result.json')
        else:
            L=module('preserved_gate_renewal',profile['renewal_module'])
            receipt,descriptor,checkpoint,l=K.prepare(W,I,w,s,l,profile=p['profile'],directory=home/'repair',renewal_module=L,custody=lambda:custody(p,U,w))
        with nullcontext()if 'settled_repair'in p else repair_scope(p,S,K,profile,env,receipt):
            install(row,p,U,D,env,raw,job);I.selected(descriptor);I._prior(checkpoint,descriptor)
            outcome=I.run('step',dict(descriptor=descriptor,checkpoint=checkpoint),env[7],home/'first-step',models=False,commands=False,containers=False)
            D.successful(outcome,env);current=dict(job,outcome=outcome,operations=str(operations(p,D,env[5])));bound=U.keep(home/'authoring-job.json',current)
            D.run(bound);hold=read(binding(Path(current['operations'])/'hold.json'));last,result=D.successful(hold['outcome'],env)
            return U.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],continuation=row,job=bound,config=job['config'],outcome=hold['outcome'],checkpoint=last,publication_performed=False,automatic_retry=False))

def probe(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','read-only probe requires explicit no-effect environment')
    with context(row,probe=True)as(p,S,K,profile,U,D,env,w,s,l,A,raw,job):
        require('parent_context'in p,'selected original dialogue probe required')
        if 'settled_repair'in p:
            certificate=read(p['settled_repair']);receipt=binding(repair_home(p)/'repair/permit.json')
            K.validate(env[3],receipt)
            guard=read(read(certificate['failed_outcome'])['resource'])
            require(certificate['schema_version']=='fleet-preserved-gate-settled-prelaunch/v1', 'exact prelaunch probe required')
            try:env[1].guard_seconds('step',read(guard['intent'])['arguments'])
            except AttributeError as exc:
                require(guard['failure']==dict(type=type(exc).__name__,message_sha256=hashlib.sha256(str(exc).encode()).hexdigest()),'retained guard failure cannot be reproduced')
            else:raise ValueError('selected predecessor does not reproduce the legacy failure')
            seconds=instruction_guard_seconds(env[1],env[0],guard['intent'])
            return dict(status='PRESERVED_SOURCE_REPAIR_VALIDATED',plan=row,completed_repair=receipt,original_failure_reproduced=True,guard_seconds=seconds,models_dispatched=0,task_mutations=0)
        from scripts.batch import harbor_lane_notes as notes
        _,_,failed,result=K.validate_failure(notes,profile)
        before=read(profile['custody_after']);K.validate_custody(env[3],profile,failed,l)
        gate=K.corrected_gate(env[3],notes,profile,result,before,s,w)
        require(gate['passed']is True,'original corrected gate still rejects')
        require(binding(profile['tracker']['path'])==profile['tracker'],'read-only repair probe changed tracker')
        return dict(status='PRESERVED_SOURCE_REPAIR_VALIDATED_CUSTODY_PENDING'if profile['custody_transition']is None else'PRESERVED_SOURCE_REPAIR_VALIDATED',plan=row,phase=K.PHASES[K.intended_gate(notes,profile,result)],gate=gate,models_dispatched=0,task_mutations=0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('run','child','probe'));parser.add_argument('--plan',required=True);parser.add_argument('--sha256',required=True)
    for name in ('intent','intent-sha256','bootstrap','bootstrap-sha256'):parser.add_argument('--'+name)
    args=parser.parse_args();row=dict(path=args.plan,sha256=args.sha256)
    result=probe(row)if args.action=='probe'else run(row)if args.action=='run'else child(row,dict(path=args.intent,sha256=args.intent_sha256),dict(path=args.bootstrap,sha256=args.bootstrap_sha256))
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
