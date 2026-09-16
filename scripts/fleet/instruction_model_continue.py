"""Original I/W/D continuation for an exactly sealed unacquired model request.

The original semantic attempt stays spent. Only its fresh physical journal and
failed step location change; completed preparation and every later owning gate
remain original. This route has no automatic transport retry.
"""
import argparse,ast,hashlib,importlib.util,json,os,sys,time
from contextlib import contextmanager,ExitStack
from dataclasses import replace
from pathlib import Path

SCHEMA='fleet-instruction-unacquired-model-continuation/v1'
FIELDS={'schema_version','helper','previous','context_adapter','context_witness','certificate','executor_recovery','payload_reader','payload_original','failed_outcome','failed_sidecar','owner','fence','output','workbook','source','allowance','renewal_witness','phase','capacity_policy'}

def require(ok,message):
    if not ok:raise ValueError(message)

def binding(path):
    p=Path(path).resolve();return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
    require(binding(row['path'])==row,'model continuation evidence changed');return json.loads(Path(row['path']).read_bytes())

def module(name,row):
    require(binding(row['path'])==row,'model continuation module changed')
    spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def load(row):
    p=read(row);old=read(p['previous'])
    predecessor=read(read(p['completed_tail'])['plan'])if 'completed_tail'in p else(read(read(p['settled_renewal'])['plan'])if 'settled_renewal'in p else old)
    require(set(p)-{'settled_renewal','completed_tail','notes_archive','historical_runtime','reserved_prefix','renewal_before_work','semantic_archive'}==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__)
        and p['source']==old['source']and p['workbook']==old['workbook']and p['fence']==predecessor['fence']+1
        and p['owner']!=old['owner']and p['output']!=old['output']and p['phase']=='nat-writer'and p['capacity_policy']==old['capacity_policy'], 'closed original model continuation required')
    parent=module('original_preserved_model_parent',old['helper']);adapter=module('model_current_checkpoint',p['context_adapter'])
    return p,old,parent,adapter


def renewal_admission(I,arguments,before,*,now=None):
    """The original I step must return renewal-required before any task work."""
    seconds=I.guard_seconds('step',arguments)
    require(seconds==3300,'renewal-only admission requires original 3300-second instruction guard')
    remaining=before['leases']['task_agent']['expires_at']-(time.time_ns()if now is None else now)
    require(1500*10**9<remaining<=(seconds+30)*10**9,
            'renewal-before-work requires live bounded original renewal-required window')
    return remaining


def completed_tail(p):
    v=read(p['completed_tail']);old=read(v['plan']);obs=read(v['observation'])
    require(set(v)=={'schema_version','plan','outcomes','failure','sidecar','outer','observation'}
        and v['schema_version']=='fleet-model-completed-tail/v1'and 'completed_tail'not in old
        and old.get('settled_renewal')==p.get('settled_renewal')
        and all(old[k]==p[k]for k in FIELDS-{'helper','owner','fence','output'})
        and type(v['outcomes'])is list and 2<=len(v['outcomes'])<=20,
        'completed model tail changed original request or authority')
    initial=read(v['outcomes'][0]);previous=initial['result'];ig=read(initial['resource'])
    iside=read(binding(Path(ig['intent']['path']).parent/'model-continuation-bootstrap.json'))
    require(v['outcomes'][0]==binding(Path(old['output'])/'first-step/outcome.json')
        and initial['status']=='COMPLETED_OPERATION'and ig['returncode']==0 and ig.get('failure')is None
        and iside['plan']==v['plan']and iside['helper']==old['helper']and iside['intent']==ig['intent']
        and read(previous)['status']=='MODEL_REQUIRED','completed model tail lacks original request boundary')
    for outcome in v['outcomes'][1:]:
        value=read(outcome);guard=read(value['resource']);intent=read(guard['intent']);result=read(value['result'])
        side=read(binding(Path(guard['intent']['path']).parent/'model-continuation-bootstrap.json'))
        require(value['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard.get('failure')is None
            and side['plan']==v['plan']and side['helper']==old['helper']and side['intent']==guard['intent']
            and side['kind']=='instruction'and side['automatic_retry']is False
            and intent['arguments']['checkpoint']==previous and result['status']=='STEP_COMPLETE'
            and result['evidence'][0]==previous,'completed model tail lost an original guarded predecessor')
        previous=value['result']
    failed=read(v['failure']);guard=read(failed['resource']);intent=read(guard['intent']);side=read(v['sidecar'])
    last=read(previous);outer=read(v['outer']);inventory={x['path']:x['sha256']for x in obs['files']}
    wi=read(outer['intent']);job=read(wi['job'])
    author_job=read(read(read(p['previous'])['previous'])['original_job'])
    require(failed['status']=='RECONCILIATION_REQUIRED'and guard['returncode']==1 and guard.get('failure')is None
        and intent['arguments']==dict(checkpoint=previous,descriptor=last['descriptor'])
        and type(intent['models'])is bool and intent['models']==author_job['models']
        and side['plan']==v['plan']and side['helper']==old['helper']and side['intent']==guard['intent']
        and obs['processes']==[]and outer['returncode']==1 and outer.get('failure_type')is None and outer.get('result')is None
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256']
        and all(inventory.get(r['path'])==r['sha256']for r in [*v['outcomes'],v['failure'],v['sidecar'],v['outer']]),
        'completed model tail is not a stopped pre-child failure')
    log=Path(guard['log']['path']).read_bytes();require(binding(guard['log']['path'])==guard['log']
        and b'validate_notes_pair'in log and b'changed evidence: 'in log,
        'completed model tail failure is not original historical notes validation')
    return intent['arguments'],Path(old['output'])


@contextmanager
def notes_history(parent,p,old):
    if 'notes_archive'not in p:
        yield;return
    helper=module('model_preserved_notes_archive',p['notes_archive']);original_module=parent.module
    source=read(old['previous']);S=original_module('model_notes_adoption_context',source['helper'])
    receipt=binding(S.adoption_home(source)/'permit/permit.json')
    archive=original_module('model_notes_submitted_archives',old['semantic_archive']['helper'])
    semantic=None
    if 'semantic_archive'in p:
        selection=p['semantic_archive'];require(set(selection)=={'original','selected'}and selection['original']==old['semantic_archive']['helper'],'exact original semantic archive reader required')
        semantic=module('model_completed_semantic_archive',selection['selected']);require(semantic.SCHEMA==archive.SCHEMA=='fleet-disclosure-semantic-archive/v1','unsupported completed semantic archive')
    replacement=None
    if 'historical_runtime'in p:
        selected=p['historical_runtime'];require(set(selected)=={'helper','original','selected'},'closed historical prefix function selection required')
        replacement=original_module('model_updated_historical_prefix',selected['helper'])
        require(parent.function_sha(replacement.historical_runtime)==selected['selected'],'selected historical prefix changed')
    with ExitStack()as stack:
        def selected(name,row):
            value=semantic if semantic is not None and row==p['semantic_archive']['original']else original_module(name,row)
            if row==source['helper']and semantic is not None:
                read_module=value.module
                def semantic_module(name,bound):
                    if bound==p['semantic_archive']['original']:return semantic
                    return read_module(name,bound)
                value.module=semantic_module;stack.callback(setattr,value,'module',read_module)
            if row==old['core']:stack.enter_context(helper.installed(value,archive,receipt))
            if row==source['helper']and replacement is not None:
                require(parent.function_sha(value.historical_runtime)==p['historical_runtime']['original'],'original historical prefix changed')
                original=value.historical_runtime;value.historical_runtime=replacement.historical_runtime
                stack.callback(setattr,value,'historical_runtime',original)
            return value
        parent.module=selected
        try:yield
        finally:parent.module=original_module


def settled_renewal(p,*,continued):
    """Retain the actual guarded renewal after a pre-operation path refusal."""
    v=read(p['settled_renewal']);old=read(v['plan']);observation=read(v['observation'])
    require(set(v)=={'schema_version','plan','renewal_outcome','failure_outcome','failure_sidecar','worker_outcome','observation'}
        and v['schema_version']=='fleet-model-completed-renewal/v1'
        and 'settled_renewal'not in old and old['schema_version']==SCHEMA
        and all(p[k]==old[k]for k in FIELDS-{'helper','owner','fence','output'})
        and p['owner']!=old['owner']and p['output']!=old['output'], 'completed renewal changes original model authority')
    done=read(v['renewal_outcome']);dg=read(done['resource']);di=read(dg['intent']);result=read(done['result'])
    failed=read(v['failure_outcome']);fg=read(failed['resource']);fi=read(fg['intent']);fr=read(failed['result']);side=read(v['failure_sidecar'])
    require(done['status']==failed['status']=='COMPLETED_OPERATION'
        and dg['returncode']==fg['returncode']==0 and dg.get('failure')is fg.get('failure')is None
        and result['status']=='RETRY_READY'and result['next_step']==p['phase']
        and fi['arguments']==dict(checkpoint=done['result'],descriptor=result['descriptor'])
        and fr['status']=='RECONCILIATION_REQUIRED'and fr['next_step']==p['phase']
        and fr['descriptor']==result['descriptor']and fr['lease']==result['lease']
        and side['plan']==v['plan']and side['helper']==old['helper']and side['intent']==fg['intent']
        and side['kind']=='instruction'and side['automatic_retry']is False,
        'completed renewal or exact pre-operation refusal differs')
    original_args,blocked=failure(p,read(p['previous']),None)
    require(fr['checkpoint']==read(blocked)['intent'],'refusal is not the original preserved writer intent')
    outer=read(v['worker_outcome']);inventory={x['path']:x['sha256']for x in observation['files']}
    wi=read(outer['intent']);job=read(wi['job'])
    require(observation['processes']==[]and outer['returncode']==1 and outer.get('failure_type')is None and outer.get('result')is None
        and wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==v['plan']['path']
        and job['command'][job['command'].index('--sha256')+1]==v['plan']['sha256']
        and inventory.get(v['worker_outcome']['path'])==v['worker_outcome']['sha256']
        and all(inventory.get(r['path'])==r['sha256']for r in(v['renewal_outcome'],v['failure_outcome'],v['failure_sidecar']))
        and not any(Path(x).is_relative_to(Path(old['output'])/'model-step')for x in inventory)
        and not(Path(old['output'])/'model-step').exists(), 'stopped model continuation has new operation effects')
    return fi['arguments']


@contextmanager
def preserved_callbacks(W,core,directory,operation):
    """Follow the same callbacks inside the exact preserved-step clone."""
    import inspect
    step=W._step
    require(binding(inspect.getsourcefile(step))==core and step.__name__=='step',
        'model continuation requires the selected preserved-step wrapper')
    closure=inspect.getclosurevars(step).nonlocals;scope=closure.get('scope')
    require(isinstance(scope,dict)and closure.get('W')is W
        and scope.get('step_directory')is W.step_directory and scope.get('_operation')is W._operation,
        'preserved step callback identities changed')
    old_directory,old_operation=scope['step_directory'],scope['_operation']
    scope['step_directory']=directory;scope['_operation']=operation
    try:yield
    finally:scope['step_directory']=old_directory;scope['_operation']=old_operation


def failure(p,old,parent):
    outcome=read(p['failed_outcome']);guard=read(outcome['resource']);intent=read(guard['intent']);result=read(outcome['result']);side=read(p['failed_sidecar'])
    require(outcome['status']=='COMPLETED_OPERATION'and guard['returncode']==0 and guard.get('failure')is None
        and intent['action']=='step'and intent['models']is True and result['status']=='MODEL_RECONCILIATION_REQUIRED'
        and result['next_step']==p['phase']and side['plan']==p['previous']and side['helper']==old['helper']
        and side['intent']==guard['intent']and side['kind']=='instruction'and side['automatic_retry']is False,
        'not the exact original sealed model boundary')
    prior=read(intent['arguments']['checkpoint']);blocked=read(result['checkpoint'])
    encoded=prior['lease'];require(set(encoded)=={'type','value'}and encoded['type']=='Lease'and set(encoded['value'])=={'owner','token','revision','sha256'}
        and all(set(v)=={'type','value'}and v['type']=='scalar'for v in encoded['value'].values()),'original typed lease encoding differs')
    lease={k:v['value']for k,v in encoded['value'].items()}
    require(prior['status']=='STEP_COMPLETE'and prior['next_step']==p['phase']and result['descriptor']==intent['arguments']['descriptor']
        and blocked['status']=='MODEL_RECONCILIATION_REQUIRED'and blocked['step']==p['phase']
        and blocked['selection']==prior['selection']and blocked['lease']==lease and result['lease']==prior['lease']
        and read(blocked['intent'])['previous']==prior['checkpoint'], 'model failure changed the completed predecessor')
    wi=read(read(read(p['certificate'])['proof'])['worker_intent']);job=read(wi['job'])
    require(wi['owner']==old['owner']and job['driver']==old['helper']and job['source']==p['source']
        and job['command'][job['command'].index('--plan')+1]==p['previous']['path']
        and job['command'][job['command'].index('--sha256')+1]==p['previous']['sha256'], 'model failure worker is not the original parent')
    return intent['arguments'],result['checkpoint']


def adoption_history(A,R,worker,before,*,historical_evidence=None):
    """An aggregate receipt keeps its original prior lease after a later renewal."""
    receipt=A.read(worker.disclosure_repair)
    require(receipt['implementation']==A.record(A.__file__)and A.selected(worker),'foreign aggregate history adapter')
    selected=worker
    semantic=getattr(worker,'dialogue_semantic_retry',None)
    if semantic!=receipt['prior_continuations'].get('dialogue_semantic_retry'):
        from scripts.batch import harbor_dialogue_semantic_retry as S
        require(semantic is not None and receipt['prior_continuations'].get('dialogue_semantic_retry') is None,
            'aggregate semantic predecessor removed or replaced')
        permission=A.read(A.read(semantic)['permit'])
        require(permission['prior_continuations'].get('disclosure_repair')==worker.disclosure_repair,
            'semantic continuation detached from adopted actual reviews')
        # The complete original S validator owns its CAS, budgets and archives.
        # Its nested history sees the predecessor that existed before this S CAS;
        # current W/I custody continues using the untouched current worker.
        prior_worker=replace(worker,dialogue_semantic_retry=None)
        original_history=S._history
        def predecessor_history(observed,current):
            require(observed==worker and current==before,'foreign semantic history projection')
            return original_history(prior_worker,current)
        S._history=predecessor_history
        try:S.validate(worker,before)
        finally:S._history=original_history
        selected=prior_worker
    prior=receipt['prior_continuations'].get('lease_renewal')
    if worker.lease_renewal!=prior:
        require(worker.lease_renewal is not None,'aggregate prior renewal removed')
        renewed=R.validate(worker,before)
        require(renewed['prior_continuations'].get('disclosure_repair')==worker.disclosure_repair,
                'later renewal is detached from adopted actual reviews')
        selected=replace(selected,lease_renewal=prior)
    validated=A.validate(selected,before)
    records=[worker.disclosure_repair,*[validated[k]for k in('implementation','history_reader','round_plan','round_result','review_plan','aggregate','initial')],
        *[item['archive']for item in validated['historical_records']]]
    if historical_evidence is not None:
        require(callable(historical_evidence),'declared historical evidence reader required')
        records=historical_evidence(A,worker.disclosure_repair,validated)
    for record in records:A.E.checked_bytes(record)
    return validated,records


def payload_scope(E,p):
    """Install only the two existing pretransport schema dispatch clauses."""
    from scripts.batch import harbor_disclosure_payload_repair as original
    require(binding(original.__file__)==p['payload_original'],'original payload dependency differs')
    before=ast.parse(Path(original.__file__).read_text());after=ast.parse(Path(p['payload_reader']['path']).read_text())
    require(binding(p['payload_reader']['path'])==p['payload_reader'],'selected payload dependency changed')
    changes=0
    for node in after.body:
        if isinstance(node,ast.FunctionDef)and node.name in('original_identity','validate_prepared'):
            arguments='permit'if node.name=='original_identity'else 'prepared,campaign'
            expected=ast.parse("if value.get('schema_version')=='harbor-model-pretransport-recovery/v1':\n from scripts.batch.harbor_model_pretransport_recovery import "+node.name+" as validated\n return validated("+arguments+")").body[0]
            matches=[i for i,child in enumerate(node.body)if ast.dump(child,include_attributes=False)==ast.dump(expected,include_attributes=False)]
            require(len(matches)==1,'closed pretransport dispatch required')
            node.body.pop(matches[0]);changes+=1
    require(changes==2 and ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False),'payload changed beyond exact pretransport dispatch')
    names=('original_identity','validate_prepared');saved={name:getattr(original,name)for name in names}
    selected=ast.parse(Path(p['payload_reader']['path']).read_text());scope=dict(original.__dict__)
    for node in selected.body:
        if isinstance(node,ast.FunctionDef)and node.name in names:
            exec(compile(ast.Module(body=[node],type_ignores=[]),p['payload_reader']['path'],'exec'),scope)
            setattr(original,node.name,scope[node.name])
    return original,saved



@contextmanager
def context(row,*,continued=False,invocation=None):
    p,old,parent,adapter=load(row);arguments,blocked=failure(p,old,parent);home=Path(p['output'])
    if 'settled_renewal'in p:arguments=settled_renewal(p,continued=continued)
    model_home=home
    if 'completed_tail'in p:arguments,model_home=completed_tail(p)
    if continued:require(read(binding(home/'intent.json'))==dict(schema_version=SCHEMA,plan=row,automatic_retry=False),'current model parent intent differs')
    else:require(not home.exists(),'model continuation exists; no replay')
    recovery=None
    @contextmanager
    def runtime(W,I,A):
        nonlocal recovery
        E=W.executor;recovery=module('scripts.batch.harbor_model_pretransport_recovery',p['executor_recovery'])
        require(recovery.E is E and read(p['certificate'])['implementation']==p['executor_recovery'],'executor identity differs')
        recovery.validate_original(p['certificate']);payload,saved=payload_scope(E,p)
        original_directory=W.step_directory;producer=W.producer
        from scripts.batch import harbor_disclosure_review_resume as disclosure
        original_context=disclosure.validate_request_context
        original_history=disclosure.validate_historical_integrity;original_validate=disclosure.validate
        original_renewal=sys.modules['scripts.batch.harbor_instruction_lease_renewal'];renew=original_renewal.authorize
        require(adapter.function_sha(renew)==p['renewal_witness'],'original renewal function changed')
        # Bind the exact actual failed worker step, rather than infer a run root.
        original_step=Path(blocked['path']).parent
        def directory(worker,phase):
            actual=original_directory(worker,phase)
            if phase==p['phase']and actual==original_step:return model_home/'model-step'
            return actual
        def request_context(task,request):
            if request.prepared.physical_repair!=p['certificate']:return original_context(task,request)
            _,prepared,campaign=recovery.validate_original(p['certificate']);recovery.validate_prepared(request.prepared,campaign)
            original=replace(request,prepared=prepared)
            require(read(read(p['certificate'])['proof'])['prepared']==binding(original_step/'request.json'),'original prepared step moved')
            retained=W.request_saved(original_step/'request.json')
            require(original==retained,'physical continuation changed original producer context')
            return producer.validate_request(task,original)
        def bounded_renew(*args,**kwargs):
            require('duration_ns'not in kwargs and 'allowance'not in kwargs,'conflicting renewal policy')
            return renew(*args,**kwargs,duration_ns=7200*10**9,allowance=p['allowance'])
        original_operation=W._operation
        def operation(worker,selection,lease,phase,path,**options):
            if phase==p['phase']and path==model_home/'model-step':
                original=W.request_saved(original_step/'request.json')
                selected=replace(original,prepared=replace(original.prepared,physical_repair=p['certificate']))
                recovery.validate_prepared(selected.prepared,worker.campaign)
                target=path/'request.json'
                if target.exists():require(W.request_saved(target)==selected,'physical prepared request changed')
                else:W.request_saved(target,selected)
            return original_operation(worker,selection,lease,phase,path,**options)
        def validate_adoption(worker,before):
            if not A.selected(worker):return original_validate(worker,before)
            return adoption_history(A,original_renewal,worker,before)[0]
        def historical_adoption(worker,before):
            if not A.selected(worker):return original_history(worker,before)
            return adoption_history(A,original_renewal,worker,before)
        with ExitStack()as callbacks:
            callbacks.enter_context(preserved_callbacks(W,old['core'],directory,operation))
            if 'reserved_prefix'in p:
                source=read(old['previous']);review=read(source['context']['plan'])
                require('reserved_round'in review,'reserved prefix requires the actual retained round')
                prefix=module('model_reserved_prefix',p['reserved_prefix'])
                callbacks.enter_context(prefix.prefix_callback(sys.modules['scripts.batch.harbor_disclosure_review_round'],review['round_reader']['runtime']))
            W.step_directory=directory;W._operation=operation;disclosure.validate_request_context=request_context;original_renewal.authorize=bounded_renew
            disclosure.validate=validate_adoption;disclosure.validate_historical_integrity=historical_adoption
            try:yield
            finally:
                W.step_directory=original_directory;W._operation=original_operation;disclosure.validate_request_context=original_context;original_renewal.authorize=renew
                disclosure.validate=original_validate;disclosure.validate_historical_integrity=original_history
                for name,function in saved.items():setattr(payload,name,function)
    def current(env,worker):
        I=env[1]
        if invocation is None:args=arguments
        else:
            intent,sidecar=invocation;side=read(sidecar);D=module('model_original_dispatcher',read(read(old['previous'])['original_job'])['driver'])
            U=module('model_original_utilities',old['utilities']);kind,actual=U.retained_command(side['original_command'],p,D,env)
            require(actual==intent and side['schema_version']==SCHEMA and side['plan']==row and side['helper']==p['helper']
                and Path(sidecar['path'])==Path(intent['path']).parent/'model-continuation-bootstrap.json','model child detached from parent')
            if kind=='instruction':args=read(intent)['arguments']
            else:
                held,w,_,s,l=D.renewal_context(env,read(intent)['checkpoint']);_,controls,_=I.selected(held['descriptor']);return w,controls,s,l
        w,controls,_=I.selected(args['descriptor']);_,s,l=I._prior(args['checkpoint'],args['descriptor']);return w,controls,s,l
    with notes_history(parent,p,old),adapter.installed(parent,p['previous'],witness=p['context_witness'],checkpoint=current,runtime=runtime)as(_,S,K,profile,U,D,env,w,s,l,A,raw,job):
        require(env[3].step_directory(w,p['phase'])==model_home/'model-step','model step was not narrowly relocated')
        yield p,parent,U,D,env,w,s,l,raw,job,recovery,arguments


def install(row,p,U,D,env,raw,job):
    C,I=env[:2];ops=Path(env[5]['run_root'])/'dispatcher-v2'/Path(p['output']).name
    def setup(current):
        require(current==dict(job,outcome=current['outcome'],operations=str(ops)),'model dispatcher source changed');return env
    D.setup=setup
    def guarded(command,*args,**options):
        kind,intent=U.retained_command(command,p,D,env)
        side=U.keep(Path(intent['path']).parent/'model-continuation-bootstrap.json',dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=command,automatic_retry=False))
        return raw([command[0],'-I','-B',p['helper']['path'],'child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',side['path'],'--bootstrap-sha256',side['sha256']],*args,**options)
    C._run_in_process_group=guarded
    return ops


def child(row,intent,sidecar):
    require(sys.flags.isolated==1 and sys.dont_write_bytecode,'isolated model child required')
    with context(row,continued=True,invocation=(intent,sidecar))as(p,parent,U,D,env,w,s,l,raw,job,recovery,args):
        U.custody(p,w.root);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],p,D,env)
        require(actual==intent and side==dict(schema_version=SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],automatic_retry=False),'model child scope differs')
        install(row,p,U,D,env,raw,job)
        return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)


def run(row):
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only cannot continue model')
    with context(row)as(p,parent,U,D,env,w,s,l,raw,job,recovery,args):
        C,I,_,W=env[:4];home=Path(p['output']);U.custody(p,w.root)
        if 'renewal_before_work'in p:
            require(p['renewal_before_work']is True and 'completed_tail'in p,'explicit completed-tail renewal admission required')
            renewal_admission(I,args,W.active(w,s,l)[1])
        home.mkdir();U.keep(home/'intent.json',dict(schema_version=SCHEMA,plan=row,automatic_retry=False))
        ops=install(row,p,U,D,env,raw,job);require(not ops.exists(),'dispatcher already used')
        first=I.run('step',args,env[7],home/'first-step',models=False,commands=False,containers=False)
        if p.get('renewal_before_work'):
            require(read(read(first)['result'])['status']=='LEASE_RENEWAL_REQUIRED',
                    'original instruction did not emit the required renewal boundary')
        D.successful(first,env)
        selected=U.keep(home/'authoring-job.json',dict(job,outcome=first,operations=str(ops)))
        D.run(selected);hold=read(binding(ops/'hold.json'));last,result=D.successful(hold['outcome'],env)
        return U.keep(home/'result.json',dict(status=result['status'],workbook=p['workbook'],continuation=row,job=selected,config=job['config'],outcome=hold['outcome'],checkpoint=last,publication_performed=False,automatic_retry=False))


def main():
    q=argparse.ArgumentParser();q.add_argument('action',choices=('run','child'));q.add_argument('--plan',required=True);q.add_argument('--sha256',required=True)
    for name in('intent','intent-sha256','bootstrap','bootstrap-sha256'):q.add_argument('--'+name)
    a=q.parse_args();row=dict(path=a.plan,sha256=a.sha256)
    value=run(row)if a.action=='run'else child(row,dict(path=a.intent,sha256=a.intent_sha256),dict(path=a.bootstrap,sha256=a.bootstrap_sha256));print(json.dumps(value))
if __name__=='__main__':main()
