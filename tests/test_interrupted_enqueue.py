"""Only an exact stopped enqueue before any transaction may get a new attempt."""
from dataclasses import dataclass,asdict
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib,json,sys
import pytest
from scripts.fleet import interrupted_enqueue as E


def bind(p):
    p=Path(p);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(r):
    assert bind(r['path'])==r
    return json.loads(Path(r['path']).read_text())

def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return bind(p)

def require(ok,message):
    if not ok:raise ValueError(message)

@dataclass
class Lease:
    revision:int
    sha256:str

@pytest.fixture
def case(tmp_path):
    d=tmp_path/'run/dispatcher-v2/old/004';d.mkdir(parents=True)
    tracker=tmp_path/'trackers/w.json';tr=put(tracker,dict(revision=17));lease=Lease(17,tr['sha256'])
    step=tmp_path/'worker/steps/enqueue';step.mkdir(parents=True)
    worker=NS(run=tmp_path/'worker',context=NS(tracker_root=tracker.parent))
    initial=put(worker.run/'initial.json',{'initial':1})
    code=put(tmp_path/'code.json',{})
    checkpoint=put(tmp_path/'checkpoint.json',{})
    value=dict(checkpoint=checkpoint,descriptor=code,selection={'selection':1},lease=asdict(lease))
    C=NS(__file__=code['path'],decode=lambda x:Lease(**x),encode=lambda x:x)
    I=NS(__file__=code['path'],SCHEMA='instruction/v1')
    W=NS(step_directory=lambda *a:step,code=lambda w:code)
    env=(C,I,None,W,None,dict(run_root=str(tmp_path/'run')),None,{'limit':1})
    old=dict(helper=code,owner='old',output=str(tmp_path/'old'))
    plan=put(tmp_path/'plan.json',{})
    jpath=tmp_path/'worker-job.json';run=tmp_path/'outer'
    job=put(jpath,dict(driver=code,inputs=[plan],workbook='w',source=code,result=str(Path(old['output'])/'result.json'),run=str(run)))
    wi=put(run/'intent.json',dict(job=job,owner='old'))
    outer=put(run/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',failure_type='TimeoutError',returncode=None,result=None,automatic_retry=False,publication_performed=False,metrics=dict(heartbeat_count=4,elapsed_seconds=20),intent=wi))
    process=put(tmp_path/'process.json',dict(pid=2147483647,job=job))
    intent=put(d/'intent.json',dict(action='step',schema_version=I.SCHEMA,arguments=dict(descriptor=code,checkpoint=checkpoint),code=code,base_code=code,controls=env[-1],memory_limit_bytes=8589934592,models=True,commands=True,containers=True))
    put(d/'semantic-bootstrap.json',dict(schema_version='fleet-retained-semantic-child/v1',plan=plan,helper=code,kind='instruction',intent=intent,original_command=[sys.executable,'-B',str(Path(I.__file__).resolve()),'_child',intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False))
    put(d/'dependency.json',{});(d/'step.log').touch()
    task=put(step/'intent.json',dict(code=code,initial=initial,lease=asdict(lease),previous=checkpoint,selection=value['selection'],step='enqueue'))
    ready=put(step/'readiness.json',dict(publication_ready=True,blockers=[],mutation_performed=False))
    p=dict(workbook='w',source=code,semantic_context={'plan':plan},interrupted_enqueue=dict(worker_job=job,worker_outcome=outer,process=process,failed_intent=intent,task_intent=task,readiness=ready))
    U=NS(require=require,read=read,binding=bind,retained_seconds=lambda *a:3300)
    return U,p,old,env,checkpoint,value,worker


def test_exact_no_transaction_stop_keeps_all_old_files(case):
    U,p,old,env,cp,value,worker=case
    before={str(x):x.read_bytes()for x in worker.run.parent.rglob('*')if x.is_file()}
    assert E.stopped(*case)==worker.run/'steps/enqueue'
    assert before=={str(x):x.read_bytes()for x in worker.run.parent.rglob('*')if x.is_file()}

@pytest.fixture
def commit_case(case):
    U,p,old,env,cp,value,worker=case
    proof=p.pop('interrupted_enqueue');p['interrupted_commit']=proof
    old_guard=Path(proof['failed_intent']['path']).parent;guard=old_guard.with_name('003');old_guard.rename(guard)
    proof['failed_intent']=bind(guard/'intent.json')
    side=json.loads((guard/'semantic-bootstrap.json').read_text());side['intent']=proof['failed_intent'];side['original_command'][-2:]=[proof['failed_intent']['path'],proof['failed_intent']['sha256']]
    put(guard/'semantic-bootstrap.json',side)
    old_step=worker.run/'steps/enqueue';step=old_step.with_name('dialogue-commit');old_step.rename(step)
    (step/'readiness.json').unlink();proof.pop('readiness')
    intent=json.loads((step/'intent.json').read_text());intent['step']='dialogue-commit';proof['task_intent']=put(step/'intent.json',intent)
    outer=read(proof['worker_outcome']);outer['failure_type']='RuntimeError';proof['worker_outcome']=put(Path(proof['worker_outcome']['path']),outer)
    env[3].step_directory=lambda *a:step
    return case

def test_completed_runtime_can_resume_only_unprepared_commit(commit_case):
    U,p,old,env,cp,value,worker=commit_case
    before={str(x):x.read_bytes()for x in worker.run.parent.rglob('*')if x.is_file()}
    assert E.stopped(*commit_case)==worker.run/'steps/dialogue-commit'
    assert before=={str(x):x.read_bytes()for x in worker.run.parent.rglob('*')if x.is_file()}

@pytest.mark.parametrize('effect',['prepared','partial','tracker','completion','exception','readiness','guard','failure'])
def test_commit_recovery_rejects_any_transaction_or_unsealed_effect(commit_case,effect):
    U,p,old,env,cp,value,worker=commit_case;proof=p['interrupted_commit'];step=worker.run/'steps/dialogue-commit'
    if effect in ('prepared','partial'):
        journal=worker.context.tracker_root/'w-notes-transactions/18-dialogue';journal.mkdir(parents=True)
        if effect=='prepared':put(journal/'prepared.json',{})
    elif effect=='tracker':put(worker.context.tracker_root/'w.json',dict(revision=18))
    elif effect in ('completion','exception','readiness'):put(step/(effect+'.json'),{})
    elif effect=='guard':put(Path(proof['failed_intent']['path']).parent/'outcome.json',{})
    else:
        v=read(proof['worker_outcome']);v['failure_type']='TimeoutError';proof['worker_outcome']=put(Path(proof['worker_outcome']['path']),v)
    with pytest.raises(ValueError):E.stopped(*commit_case)

@pytest.mark.parametrize('phase',['dialogue-commit','enqueue','dialogue-writer','dialogue-review'])
def test_commit_tail_has_no_model_capability_and_cannot_return_to_review(tmp_path,phase):
    from contextlib import ExitStack
    from scripts.fleet import instruction_semantic_continue as U
    job=put(tmp_path/'job.json',{'models':True});cp=put(tmp_path/'cp.json',{'next_step':phase})
    calls=[]
    def run(*args,**kwargs):calls.append(kwargs);return 'original guard'
    C=NS(_run_in_process_group=lambda *a,**k:None);I=NS(run=run)
    with ExitStack()as stack:
        D=NS(_semantic_tail_manager=stack)
        env=(C,I,None,None,None,{'run_root':str(tmp_path/'run')})
        U.install_retained_commands({},dict(original_job=job,interrupted_commit={},output=str(tmp_path/'new')),D,env)
        if phase in ('dialogue-commit','enqueue'):
            assert I.run('step',{'checkpoint':cp},models=True,commands=True,containers=True)=='original guard'
            assert calls==[dict(models=False,commands=True,containers=True)]
        else:
            with pytest.raises(ValueError,match='cannot author or review'):I.run('step',{'checkpoint':cp},models=True)
            assert calls==[]
    assert I.run is run

@pytest.mark.parametrize('mutation',['task_complete','task_exception','prepared_transaction','partial_transaction','tracker_advanced','guard_outcome','guard_log','unknown_failure','request'])
def test_uncertain_or_effectful_boundaries_are_rejected(case,mutation):
    U,p,old,env,cp,value,worker=case;proof=p['interrupted_enqueue'];step=worker.run/'steps/enqueue';guard=Path(proof['failed_intent']['path']).parent
    if mutation in ('task_complete','task_exception','request'):put(step/({'task_complete':'complete.json','task_exception':'exception.json','request':'request.json'}[mutation]),{})
    elif mutation in ('prepared_transaction','partial_transaction'):
        journal=worker.context.tracker_root/'w-notes-transactions/18-enqueue';journal.mkdir(parents=True)
        if mutation=='prepared_transaction':put(journal/'prepared.json',{})
    elif mutation=='tracker_advanced':put(worker.context.tracker_root/'w.json',dict(revision=18))
    elif mutation=='guard_outcome':put(guard/'outcome.json',{})
    elif mutation=='guard_log':(guard/'step.log').write_text('unaccounted effect')
    else:
        v=read(proof['worker_outcome']);v['failure_type']='RemoteDisconnected';proof['worker_outcome']=put(Path(proof['worker_outcome']['path']),v)
    with pytest.raises(ValueError):E.stopped(*case)

@pytest.fixture
def completed_selection(tmp_path):
    from scripts.fleet import instruction_semantic_continue as U0
    code=put(tmp_path/'code.json',{})
    old={k:code for k in U0.TAIL_FIELDS}
    old.update(helper=code,schema_version=U0.TAIL_SCHEMA,fence=4,owner='old',output=str(tmp_path/'old'),interrupted_enqueue={},checkpoint_lease={},semantic_context={'helper':code,'plan':code},next_step='enqueue')
    prior=put(tmp_path/'previous.json',old)
    p={k:old[k]for k in U0.TAIL_FIELDS};p.update(helper=code,fence=5,owner='new',output=str(tmp_path/'new'),semantic_context=old['semantic_context'],completed_enqueue={k:code for k in ('reader','guard','process','outer','worker_job','intent','readiness','exception','root_reader')})
    p['completed_enqueue'].update(reader=bind(E.__file__),previous={'helper':code,'plan':prior})
    calls=[]
    def context(*args,**kwargs):calls.append('native');raise RuntimeError('native validation boundary')
    U=NS(read=read,require=require,binding=bind,__file__=code['path'],TAIL_FIELDS=U0.TAIL_FIELDS,TAIL_SCHEMA=U0.TAIL_SCHEMA,module=lambda *args:NS(tail_context=context))
    return U,p,old,calls,tmp_path

@pytest.mark.parametrize('defect',['source','outcome','semantic_context','fence','owner','output','renewal','extra_field'])
def test_completed_enqueue_cannot_detach_renewal_or_replay_prior_identity(completed_selection,defect):
    U,p,old,calls,home=completed_selection
    if defect in ('source','outcome','semantic_context'):p[defect]={'foreign':True}
    elif defect in ('owner','output'):p[defect]=old[defect]
    elif defect=='fence':p['fence']=old['fence']
    elif defect=='extra_field':p['checkpoint_lease']={}
    else:
        old.pop('checkpoint_lease');p['completed_enqueue']['previous']['plan']=put(home/'altered.json',old)
    with pytest.raises(ValueError):E.completed_context(U,put(home/'new.json',p))
    assert calls==[]

@pytest.mark.parametrize('drift',[None,'code','binding','globals'])
def test_completed_commit_composes_declared_route(tmp_path,drift):
    from scripts.fleet import completed_checkpoint_takeover as K
    import types
    source="""def _step(worker,selection,lease,checkpoint,predecessor):
    require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')
    return step_directory(worker,'dialogue-commit')
"""
    file=tmp_path/'selected.py';file.write_text(source)
    old=tmp_path/'original.py';old.write_text(source)
    W=types.ModuleType('worker');W.__file__=str(old);W.require=require;W.codec=NS(encode=lambda x:x);W.asdict=lambda x:x;W.step_directory=lambda *a:'old'
    exec(compile(source,str(file),'exec'),W.__dict__)
    selected=bind(file)
    if drift=='code':exec(compile(source.replace("return step_directory(worker,'dialogue-commit')","return 'different'"),str(file),'exec'),W.__dict__)
    if drift=='binding':selected['sha256']='0'*64
    if drift=='globals':W._step=types.FunctionType(W._step.__code__,dict(W.__dict__))
    plan=put(tmp_path/'plan.json',dict(command_routing={'worker':selected}))
    U=NS(require=require,read=read,binding=bind,module=lambda *a:NS(routed_source=lambda text,kind:text))
    before=W._step;proofs=[]
    def operation():
        with E.routed_step_scope(U,{'context':{'helper':{},'plan':plan}},W,K,lambda *a:proofs.append(a)or True,lambda *a:'new'):
            assert W._step(None,'selected','new',None,{'selection':'selected','lease':'old'})=='new'
        assert W._step is before
    if drift:
        with pytest.raises(ValueError):operation()
    else:operation();assert len(proofs)==1
