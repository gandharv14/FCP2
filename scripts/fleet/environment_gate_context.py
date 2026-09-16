"""Reusable original environment-gate repair on a stopped fleet workbook.

The existing environment continuation entrypoint, original guarded children and
atomic fleet launcher own execution. Historical failed outcomes are never edited.
"""
from contextlib import contextmanager,ExitStack
from pathlib import Path
from types import SimpleNamespace,ModuleType,FunctionType
import os,sys

FIELDS={'schema_version','entry','gate_repair','original_job','original_worker_job','outcome','outer','stopped',
        'tracker','home','operations','owner','fence','workbook','source'}
REPAIR={'helper','core','qualification_helper','qualification','policy','adapter','history','dispatcher','dispatcher_check'}


def load(E,row,*,probe=False,continued=False):
    p=E.checked(row);E.require(set(p)==FIELDS and p['schema_version']==E.SCHEMA and p['entry']==E.binding(E.__file__)
        and set(p['gate_repair']) in (REPAIR,REPAIR|{'takeover_policy'}) and p['gate_repair']['helper']==E.binding(__file__),'closed environment gate continuation required')
    q=p['gate_repair'];job=E.checked(p['original_job']);old=E.checked(p['original_worker_job']);outer=E.checked(p['outer'])
    intent=E.checked(outer['intent']);claim=E.checked(E.binding(Path(old['run'])/'claim.json'))
    E.require(old['workbook']==p['workbook'] and old['source']==p['source'] and intent['job']==p['original_worker_job']
        and p['outer']==E.binding(Path(old['run'])/'outcome.json') and outer['status']=='RECONCILIATION_REQUIRED'
        and outer['returncode']==1 and outer['failure_type'] is None and outer['automatic_retry'] is False
        and outer['publication_performed'] is False and p['fence']==claim['fence']+1 and p['owner']!=claim['owner']
        and intent['claim_id']==claim['claim_id'],'environment repair lost stopped original claim')
    config=E.checked(job['config']);root=Path(E.checked(config['code'])['root']);sys.path.insert(0,str(root))
    H=E.module('environment_gate_original_history',q['history']);H.select_history(p,old,probe=probe)
    check=E.module('environment_gate_dispatcher_check',q['dispatcher_check']);check.renewal_dispatcher(job['driver'],q['dispatcher'])
    D=E.module('environment_gate_original_dispatcher',q['dispatcher']);selected=dict(job,driver=q['dispatcher'])
    env=D.setup(selected);C,I,_,W=env[:4]
    checkpoint,value=D.successful(p['outcome'],env)
    operations=Path(job['operations']);directories=sorted(x for x in operations.iterdir() if x.is_dir())
    E.require(directories and [x.name for x in directories]==['%03d'%n for n in range(len(directories))]
        and p['outcome']==E.binding(directories[-1]/'outcome.json')
        and E.checked(E.binding(operations/'hold.json'))['outcome']==p['outcome']
        and E.checked(E.binding(operations/'job.json'))==job,'original failed dispatcher advanced or changed')
    previous,_=D.successful(job['outcome'],env)
    for directory in directories:
        completed=E.checked(E.binding(directory/'outcome.json'));guard=E.checked(completed['resource']);step=E.checked(guard['intent'])
        E.require(step['action']=='step' and step['arguments']['checkpoint']==previous,'environment prefix lost actual predecessor')
        previous,_=D.successful(E.binding(directory/'outcome.json'),env)
    E.require(value['status']=='OWNING_GATE_FAILED' and value['step']=='normalization_phase1'
        and not job['publish'] and all(job[k] is True for k in ('models','commands','containers')),
        'settled original phase1 gate failure required')
    stop=E.checked(p['stopped']);E.require(stop['processes']==[],'original environment author is not stopped')
    copies={r['native']['path']:r for r in stop['files']}
    for item in (p['outer'],outer['intent'],p['original_worker_job'],p['original_job'],p['outcome'],checkpoint,p['tracker']):
        E.require(item['path'] in copies and copies[item['path']]['native']==item
            and E.binding(copies[item['path']]['copy']['path'])==copies[item['path']]['copy']
            and copies[item['path']]['copy']['sha256']==item['sha256'],'stopped environment custody incomplete')
    E.require(Path(p['home']).parent==Path(config['run_root'])/'continuations'
        and Path(p['operations']).parent==Path(config['run_root'])/'dispatcher-v2'
        and (continued or (not Path(p['home']).exists() and not Path(p['operations']).exists())),
        'environment repair namespace consumed or outside original task')
    worker,_,_=C.selected(value['descriptor']);E.require(worker.bootstrap.workbook==p['workbook'],'foreign environment worker')
    G=E.module('selected_environment_grouped_qualification',q['qualification_helper'])
    K=E.module('selected_original_environment_repair',q['core']);A=E.module('selected_original_environment_runner',q['adapter'])
    return p,q,D,env,selected,checkpoint,worker,G,K,A


def active(E,p):
    from scripts.fleet.coordinator import Client
    E.require(os.environ['HARBOR_FLEET_WORKBOOK']==p['workbook'] and os.environ['HARBOR_FLEET_OWNER']==p['owner']
        and int(os.environ['HARBOR_FLEET_FENCE'])==p['fence'],'exclusive current environment repair claim required')
    state=Client(os.environ['HARBOR_FLEET_URL'],os.environ['HARBOR_FLEET_TOKEN_FILE']).call('heartbeat',
        workbook=p['workbook'],owner=p['owner'],fence=p['fence'],token=os.environ['HARBOR_FLEET_CLAIM_TOKEN'],lease_seconds=180)
    E.require(state['state']=='active' and state['source_sha256']==p['source']['sha256'],'environment repair source custody changed')


def dependencies(p):
    return [p[k] for k in ('original_job','original_worker_job','outcome','outer','stopped')]+list(p['gate_repair'].values())


def takeover_selection(E,p,q,job,checkpoint):
    if 'takeover_policy'not in q:return None
    from scripts.batch import harbor_lease_takeover as T
    policy=E.checked(q['takeover_policy']);config=E.checked(job['config']);guard=E.checked(p['outcome'])['resource']
    E.require(set(policy)=={'schema_version','code','authorization','workbook','source','owner','maximum_replacements',
        'duration_ns','directory'} and policy['schema_version']=='fleet-environment-gate-first-takeover/v1'
        and policy['code']==E.binding(T.__file__) and policy['workbook']==p['workbook'] and policy['source']==p['source']
        and policy['owner']==p['owner'] and policy['maximum_replacements']==1 and policy['duration_ns']==7200*10**9
        and Path(policy['directory']).parent==Path(config['run_root'])/'lease-takeovers',
        'explicit bounded original environment takeover required')
    E.checked(policy['authorization']) if policy['authorization']['path'].endswith('.json') else E.require(E.binding(policy['authorization']['path'])==policy['authorization'],'takeover authorization changed')
    chosen=T.Selection(campaign_root=Path(config['run_root']),workbook=p['workbook'],tracker=p['tracker'],
        worker_kind='environment',guard=guard,checkpoint=checkpoint,owner=p['owner'],duration_ns=policy['duration_ns'],config=job['config'])
    before,_,_,_=T._recipe(chosen);T._prior_chain(chosen,before)
    E.require(T.life.validate_lifecycle(before)==0,'first takeover policy cannot reset historical consumption')
    with T._stopped_locks(chosen):E.require(E.binding(p['tracker']['path'])==p['tracker'],'environment custody changed')
    E.require(not Path(policy['directory']).exists(),'takeover already attempted; reconcile retained journal')
    return T,chosen,Path(policy['directory']),q['takeover_policy']


@contextmanager
def context(E,row,*,probe=False,continued=False):
    loaded=load(E,row,probe=probe,continued=continued);p,q,D,env,job,checkpoint,worker,G,K,A=loaded
    with ExitStack()as stack:
        stack.enter_context(G.qualification_scope(q['qualification'],q['policy']))
        receipt=Path(p['home'])/'repair/receipt.json'
        if receipt.exists():stack.enter_context(K.scope(env[0],env[1],env[3],E.binding(receipt)))
        elif continued:raise ValueError('cold environment child lacks completed repair receipt')
        yield loaded


def probe(E,row):
    with context(E,row,probe=True)as(p,q,D,env,job,checkpoint,worker,G,K,A):
        takeover=takeover_selection(E,p,q,job,checkpoint)
        C=env[0];value,before,worker,selection,_=K.failure(C,checkpoint,p['tracker'],permit_expired=takeover is not None)
        from scripts.batch import harbor_completed_lanes as L
        gate=L.check_completed_gate(before,L.CompletionEvidence('normalization_phase1',selection),C.worker._context(worker))
        E.require(gate['passed'] is True and E.binding(p['tracker']['path'])==p['tracker'],'owning repair probe failed or changed tracker')
        return dict(status='ORIGINAL_ENVIRONMENT_GATE_REPAIR_VALIDATED',plan=row,gate=gate,models=0,tracker_mutations=0)


def dispatcher(E,p,D,env,A,row,job):
    C,I=env[:2];common=dict(base=C,reader_plan=row,child_wrapper=p['entry'],reader_evidence=dependencies(p))
    b=SimpleNamespace(**C.__dict__);b.run=A.select_run(C,kind='environment',producer_code=E.binding(C.__file__),**common)
    i=SimpleNamespace(**I.__dict__);i.run=A.select_run(I,kind='instruction',producer_code=E.binding(I.__file__),**common)
    b._run_in_process_group=A.select_run(D,kind='renewal',producer_code=E.binding(D.__file__),**common).declared_runner
    clone=ModuleType(D.__name__);clone.__dict__.update(D.__dict__)
    for name,value in D.__dict__.items():
        if isinstance(value,FunctionType) and value.__globals__ is D.__dict__:
            function=FunctionType(value.__code__,clone.__dict__,value.__name__,value.__defaults__,value.__closure__)
            function.__kwdefaults__=value.__kwdefaults__;clone.__dict__[name]=function
    def setup(current):
        E.require(current==dict(job,outcome=current['outcome'],operations=p['operations']),'environment dispatcher changed authority')
        return b,i,*env[2:]
    clone.setup=setup
    return clone,(b,i,*env[2:])


def run(E,row):
    with context(E,row)as(p,q,D,env,job,checkpoint,worker,G,K,A):
        active(E,p);takeover=takeover_selection(E,p,q,job,checkpoint);home=Path(p['home']);home.mkdir(parents=True)
        E.keep(home/'intent.json',dict(plan=row,entry=p['entry'],automatic_retry=False))
        with D.fleet_slot(p['workbook'])as fds:
            receipt=K.prepare(env[0],checkpoint,p['tracker'],home/'repair',custody=lambda:active(E,p),takeover=takeover)
            with K.scope(env[0],env[1],env[3],receipt):
                DD,selected=dispatcher(E,p,D,env,A,row,job)
                first=selected[0].run('step',dict(descriptor=E.checked(checkpoint)['descriptor'],checkpoint=receipt,captures=None),
                    env[7],home/'first-step',dispatch=True,guardian_lock_fds=fds)
                cp,value=DD.successful(first,selected)
                E.require(value['status']=='STEP_COMPLETE' and value['step']=='normalization_phase1','original repaired phase1 did not advance')
        with K.scope(env[0],env[1],env[3],receipt):
            DD,selected=dispatcher(E,p,D,env,A,row,job)
            bound=E.keep(home/'authoring-job.json',dict(job,outcome=first,operations=p['operations']))
            DD.run(bound);hold=E.checked(E.binding(Path(p['operations'])/'hold.json'));cp,value=DD.successful(hold['outcome'],selected)
            return E.keep(home/'result.json',dict(status=value['status'],workbook=p['workbook'],plan=row,job=bound,
                outcome=hold['outcome'],checkpoint=cp,publication_performed=False,automatic_retry=False))


def owning_child(E,row,kind,intent):
    with context(E,row,continued=True)as(p,q,D,env,job,checkpoint,worker,G,K,A):
        active(E,p);producer={'environment':env[0],'instruction':env[1],'renewal':D}[kind]
        A.validate_child(producer,base=env[0],kind=kind,reader_plan=row,child_wrapper=p['entry'],intent=intent,reader_evidence=dependencies(p))
        return producer.renewal_child(intent) if kind=='renewal' else producer.child(intent)


@contextmanager
def completed_review_context(row,*,continued=True):
    """Read actual environment repair and its first rejected disclosure Call."""
    import hashlib,json,importlib.util
    def binding(path):
        path=Path(path)
        if not path.is_absolute()or not path.is_file()or any(x.is_symlink()for x in(path,*path.parents)):raise ValueError('canonical completed review evidence required')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    def checked(bound):
        if binding(bound['path'])!=bound:raise ValueError('completed review evidence changed')
        return json.loads(Path(bound['path']).read_bytes())
    p=checked(row)
    fields={'schema_version','helper','original','entry','plan','workbook','source','build','failed_review','rejection'}
    if set(p)!=fields or p['schema_version']!='fleet-completed-environment-review/v1' or p['helper']!=binding(__file__) or continued is not True:
        raise ValueError('closed completed environment review context required')
    def load(name,bound):
        if binding(bound['path'])!=bound:raise ValueError('completed review reader changed')
        spec=importlib.util.spec_from_file_location(name,bound['path']);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
    E=load('completed_review_environment_entry',p['entry']);old=E.checked(p['plan'])
    E.require(old['entry']==p['entry']and old['gate_repair']['helper']==p['original']
        and old['workbook']==p['workbook']and old['source']==p['source'],'completed environment review changed source')
    original=load('completed_review_environment_original',p['original'])
    with original.context(E,p['plan'],probe=True,continued=True)as loaded:
        prior,q,D,env,job,checkpoint,worker,G,K,A=loaded
        completed_review_prefix(E,p,prior,original,D,env,job,A)
        built=E.checked(p['build']['result']);value,selection,lease=env[1]._prior(p['build']['result'],p['build']['descriptor'])
        E.require(value==built and selection.content.workbook==p['workbook']
            and selection.content.bindings['original_raw_source']==p['source'],'completed build source differs')
        yield env,lease


def completed_review_prefix(E,p,old,original,D,env,job,A):
    """Validate the closed contiguous original guard chain, including failure."""
    root=Path(old['operations']);own=E.binding(Path(old['home'])/'authoring-job.json');actual=E.checked(own)
    first=E.binding(Path(old['home'])/'first-step/outcome.json')
    E.require(actual==dict(job,outcome=first,operations=str(root))
        and E.checked(E.binding(root/'job.json'))==actual,'completed repair dispatcher job differs')
    directories=sorted(x for x in root.iterdir()if x.is_dir())
    E.require(len(directories)>=3 and [x.name for x in directories]==['%03d'%i for i in range(len(directories))]
        and {x.name for x in root.iterdir()}=={'job.json','hold.json',*[x.name for x in directories]},
        'completed review guards are not contiguous')
    cp,value=D.successful(first,env)
    for i,directory in enumerate(directories):
        outcome=E.binding(directory/'outcome.json');result=E.checked(outcome);guard=E.checked(result['resource']);intent=E.checked(guard['intent'])
        E.require(intent['arguments']['checkpoint']==cp,'completed review skipped original checkpoint')
        kind='environment'if intent['code']==E.binding(env[0].__file__)else'instruction'
        producer=env[0]if kind=='environment'else env[1]
        side=E.checked(E.binding(directory/'declared-runtime-adapter.json'))
        seconds=A.contract(producer,env[0],kind,guard['intent'],side['original_command'])
        E.require(side==dict(schema_version=A.SCHEMA,adapter=E.binding(A.__file__),producer_code=E.binding(producer.__file__),base_code=E.binding(env[0].__file__),kind=kind,reader_plan=p['plan'],child_wrapper=p['entry'],reader_evidence=original.dependencies(old),intent=guard['intent'],original_command=side['original_command'],selected_command=[sys.executable,'-I','-B',p['entry']['path'],'owning-child','--plan',p['plan']['path'],'--plan-sha256',p['plan']['sha256'],'--kind',kind,'--intent',guard['intent']['path'],'--intent-sha256',guard['intent']['sha256']],timeout_seconds=seconds,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False),
            'completed review lost actual declared child lineage')
        if i==len(directories)-1:
            E.require(outcome==p['failed_review']and cp==p['build']['result']and kind=='instruction'
                and intent['action']=='step'and intent['arguments']==dict(descriptor=p['build']['descriptor'],checkpoint=cp)
                and result==dict(status='RECONCILIATION_REQUIRED',resource=E.binding(directory/'resource.json'),automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None)
                and guard['returncode']==1 and guard['failure']is None and guard['log']==E.binding(directory/'step.log')
                and Path(guard['log']['path']).read_text().rstrip().endswith('scripts.batch.harbor_admission.AdmissionError: review has blocking findings')
                and not(directory/'result.json').exists(),'only the sealed original semantic review refusal is supported')
        else:
            cp,value=D.successful(outcome,env)
            if i==len(directories)-2:
                E.require(outcome==p['build']['outcome']and cp==p['build']['result']and value['status']=='STEP_COMPLETE'
                    and value['next_step']=='disclosure-review'and value['descriptor']==p['build']['descriptor'],
                    'completed build is not immediately before rejected review')
    E.require(E.checked(E.binding(root/'hold.json'))['outcome']==p['failed_review'],'original held review differs')
    rejection=E.checked(p['rejection']);reader=E.module('completed_environment_semantic_rejection',rejection['implementation'])
    from scripts.batch import harbor_instruction_worker as W
    worker,_,_=env[1].selected(p['build']['descriptor']);request=W.request_saved(worker.run/'steps/disclosure-review/request.json')
    E.require(rejection['execution']==E.binding(W.executor.location(request.prepared,worker.campaign)/'plan.json'),
        'rejected Call belongs to another prepared review')
    from dataclasses import asdict
    reader.closed_rejection(p['rejection'],asdict(request.prepared.scope),1)
