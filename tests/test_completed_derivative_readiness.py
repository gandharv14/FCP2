from dataclasses import dataclass,asdict
from pathlib import Path
from types import SimpleNamespace
import hashlib,json
import pytest
from scripts.fleet import completed_derivative_readiness as C,dialogue_revision_adoption as M

@dataclass
class Lease:
    owner:str='owner'
    token:str='fixture'
    revision:int=18
    sha256:str='before'

def fixture(tmp,fault=None):
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
    def encode(lease):return dict(type='Lease',value={k:dict(type='scalar',value=v)for k,v in asdict(lease).items()})
    home=tmp/'current';ops=tmp/'ops';failed=tmp/'failed';run=tmp/'worker';source={'source':'unchanged'};old=Lease();new=Lease(revision=19,sha256='after')
    if fault=='revision':new.revision=20
    if fault=='owner':new.owner='other'
    previous=dict(status='STEP_COMPLETE',selection=source,descriptor={'descriptor':'original'},checkpoint={'checkpoint':'commit'},lease=encode(old))
    prior=put(tmp/'commit/result.json',previous);prior_outcome=put(tmp/'commit/outcome.json',dict(result=prior))
    guard_fields=dict(action='step',code={'original':'I'},base_code={'original':'B'},controls={},models=False,commands=True,containers=True,memory_limit_bytes=8589934592,timeout_seconds=3300)
    failed_intent=put(failed/'guard-intent.json',guard_fields);failed_resource=put(failed/'resource.json',dict(intent=failed_intent));failed_outcome=put(failed/'outcome.json',dict(resource=failed_resource))
    task=failed/'task';error=dict(type='AdmissionError',message_sha256=hashlib.sha256(b'full current readiness did not qualify').hexdigest(),automatic_replay=False)
    put(task/'intent.json',{});put(task/'exception.json',error)
    rejected=put(task/'readiness.json',dict(publication_ready=False,blockers=['numerical_error']if fault=='historical'else['prose_mechanics'],checks=dict(prose_mechanics=dict(checked=False,error_type='AdmissionError'))))
    initial=put(run/'initial.json',{});original_task=dict(step='enqueue',previous=previous['checkpoint'],selection=source,lease=asdict(old),code={'original':'W'},initial=initial)
    if fault=='predecessor':original_task['previous']={}
    task_intent=put(home/'steps/enqueue/intent.json',original_task)
    complete=put(home/'steps/enqueue/complete.json',dict(step='enqueue',status='completed',selection=source,lease=asdict(new),intent=task_intent))
    result=put(ops/'000/result.json',dict(status='STEP_COMPLETE'if fault=='notready'else'PUBLICATION_READY',next_step=None,publication_performed=False,selection=source,descriptor=previous['descriptor'],checkpoint=complete,lease=encode(new)))
    intent=put(ops/'000/intent.json',dict(guard_fields,models=fault=='model',arguments=dict(descriptor=previous['descriptor'],checkpoint=prior)))
    log=ops/'000/step.log';log.write_text('');resource=put(ops/'000/resource.json',dict(returncode=1 if fault=='guard'else 0,failure=None,intent=intent,log=M.binding(log)))
    outcome=put(ops/'000/outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result))
    p=dict(schema_version=M.SCHEMA,operations=str(ops),continuation=dict(output=str(home),failed_readiness=rejected,completed_outcomes=[prior_outcome],failed_outcome=failed_outcome))
    ready=dict(outcome=outcome,checkpoint=result);worker=SimpleNamespace(root=tmp,run=run)
    W=SimpleNamespace(codec=SimpleNamespace(encode=lambda v:v),code=lambda w:{'original':'W'},complete_readiness=lambda *a,**k:dict(publication_ready=fault!='current',blockers=['missing_evidence']if fault=='current'else[]))
    return p,ready,(p['continuation'],task,error,worker,source,old,W,tmp)

def test_completed_guard_supplies_history_without_reusing_old_live_lease(tmp_path):
    p,ready,args=fixture(tmp_path);original=M.stopped_readiness
    with C.installed(M,p,ready):assert M.stopped_readiness(*args)is None
    assert M.stopped_readiness is original

@pytest.mark.parametrize('fault',['revision','owner','predecessor','notready','model','guard','historical','current'])
def test_completed_history_rejects_tampering_and_remaining_quality_failures(tmp_path,fault):
    p,ready,args=fixture(tmp_path,fault);original=M.stopped_readiness
    with pytest.raises(ValueError):
        with C.installed(M,p,ready):M.stopped_readiness(*args)
    assert M.stopped_readiness is original

@pytest.mark.parametrize('value',[{},dict(type='Lease',value={}),dict(type='Other',value={})])
def test_closed_lease_encoding(value):
    with pytest.raises(ValueError):C.lease_value(value)

@pytest.mark.parametrize('fault',[None,'no_scope','cached_class','current'])
def test_completed_facade_failure_keeps_full_quality_gates(tmp_path,fault):
    p,record,args=fixture(tmp_path,'current'if fault=='current'else None)
    c,task,error,worker,selection,lease,W,root=args
    def put(path,value):path.write_text(json.dumps(value));return M.binding(path)
    c['failed_readiness']=put(task/'readiness.json',dict(publication_ready=True,blockers=[]))
    error.update(message_sha256=hashlib.sha256(b'complete current readiness required').hexdigest());put(task/'exception.json',error)
    transition=lambda:None;W.HarborLaneLifecycle=SimpleNamespace(transition=transition)
    facade=SimpleNamespace(ReadyTransitions=SimpleNamespace(transition=transition))
    if fault=='cached_class':W.HarborLaneLifecycle=SimpleNamespace(transition=lambda:None)
    if fault!='no_scope':p['ready_root']={'helper':'authenticated by owning M.context'}
    with C.installed(M,p,record):
        if fault:
            with pytest.raises(ValueError):M.stopped_readiness(*args,ready=facade)
        else:M.stopped_readiness(*args,ready=facade)

@pytest.mark.parametrize('fault',[None,'original_rejects','foreign_env','foreign_worker'])
def test_completed_publication_rebinds_only_declared_namespaces(tmp_path,fault):
    p,record,args=fixture(tmp_path);events=[]
    base=object();publisher=object();instruction=SimpleNamespace();worker_module=SimpleNamespace();repair=SimpleNamespace()
    config={'run_root':str(tmp_path/'run')};root=tmp_path/'code';controls=object()
    original_env=(base,instruction,publisher,worker_module,repair,config,root,controls)
    worker=SimpleNamespace(root=root,run=Path(config['run_root'])/'instruction')
    if fault=='foreign_worker':worker.root=tmp_path/'foreign'
    def original_selected(observed,result):
        assert observed==original_env;events.append(result)
        if fault=='original_rejects':raise ValueError('original selection rejected')
        return worker,'recipe'
    original_D=SimpleNamespace(selected=original_selected)
    old_namespace=M.namespace
    with C.installed(M,p,record)as bind:
        I,W,R,D=[M.namespace(x)for x in(instruction,worker_module,repair,original_D)]
        env=(base,I,publisher,W,R,config,root,controls);bind(D,env)
        if fault:
            with pytest.raises(ValueError):D.selected((*env[:6],tmp_path,controls)if fault=='foreign_env'else env,{'actual':'ready'})
        else:assert D.selected(env,{'actual':'ready'})==(worker,'recipe')
    assert M.namespace is old_namespace
    assert events==([]if fault=='foreign_env'else[{'actual':'ready'}])

@pytest.fixture
def full_tail(tmp_path):
    from scripts.fleet import advance_authoring_v8 as D
    def put(path,v):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return M.binding(path)
    p=dict(schema_version=M.SCHEMA,operations=str(tmp_path/'ops'),helper={'path':'/reader','sha256':'r'},runner={'path':'/runner','sha256':'x'},continuation={'plan':{'path':'/old','sha256':'o'},'failed_outcome':{},'output':'/unused'})
    plan={'path':'/plan','sha256':'p'};prior=put(tmp_path/'adoption/result',dict(status='STEP_COMPLETE',next_step='final-runtime',descriptor={'path':'/descriptor','sha256':'d'}));value=M.read(prior);initial=prior;outcomes=[]
    I=SimpleNamespace(__file__='/I.py',_prior=lambda *a:None);base=SimpleNamespace(__file__='/B.py');deps=[{'path':'/evidence','sha256':'e'}]
    for n,phase in enumerate(('final-runtime','dialogue-commit','enqueue')):
        folder=Path(p['operations'])/('%03d'%n);folder.mkdir(parents=True)
        intent=put(folder/'intent.json',dict(action='step',arguments=dict(descriptor=value['descriptor'],checkpoint=prior),models=False,commands=True,containers=True))
        log=folder/'step.log';log.write_text('original finished')
        resource=put(folder/'resource.json',dict(intent=intent,log=M.binding(log),returncode=0,failure=None))
        value=dict(status='PUBLICATION_READY'if n==2 else'STEP_COMPLETE',next_step=None if n==2 else('dialogue-commit','enqueue')[n],descriptor=value['descriptor'],publication_performed=False)
        prior=put(folder/'result.json',value);outcome=put(folder/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=prior));outcomes.append(outcome)
        command=['/python','original',phase]
        side=dict(schema_version='adapter',adapter=p['runner'],producer_code={'path':'/I.py','sha256':'I'},base_code={'path':'/B.py','sha256':'B'},kind='instruction',reader_plan=plan,child_wrapper=p['helper'],reader_evidence=deps,intent=intent,original_command=command,selected_command=['/python','-I','-B','/reader','owning-child','--plan','/plan','--plan-sha256','p','--kind','instruction','--intent',intent['path'],'--intent-sha256',intent['sha256']],timeout_seconds=1350 if n==0 else 3300,memory_limit_bytes=8589934592,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False)
        put(folder/'declared-runtime-adapter.json',side)
    def bind(path):
        if str(path)in('/I.py','/B.py'):return dict(path=str(path),sha256=Path(path).stem)
        return M.binding(path)
    reader=SimpleNamespace(require=M.require,read=M.read,binding=bind,authorization=lambda *a:(None,initial,M.read(initial)),dependencies=lambda _:deps,module=lambda *a:SimpleNamespace(SCHEMA='adapter',contract=lambda i,b,k,intent,c:1350 if c[-1]=='final-runtime'else 3300))
    return reader,p,dict(plan=plan,operations=outcomes,outcome=outcomes[-1],checkpoint=prior),SimpleNamespace(successful=D.successful),(base,I)

@pytest.mark.parametrize('fault',[None,'missing','order','guard','predecessor','model','reader','lease_archive'])
def test_full_three_phase_completion_retains_every_original_guard(full_tail,fault):
    reader,p,ready,D,env=full_tail;folder=Path(p['operations'])/'001'
    if fault=='missing':ready['operations'].pop(0)
    elif fault=='order':ready['operations'].reverse()
    elif fault in('guard','predecessor','model'):
        name='resource.json'if fault=='guard'else'intent.json';path=folder/name;v=json.loads(path.read_text())
        if fault=='guard':v['returncode']=1
        elif fault=='predecessor':v['arguments']['checkpoint']={}
        else:v['models']=True
        path.write_text(json.dumps(v))
    elif fault in('reader','lease_archive'):
        path=folder/'declared-runtime-adapter.json';v=json.loads(path.read_text());v['child_wrapper']={}if fault=='reader'else v['child_wrapper']
        if fault=='lease_archive':v['reader_evidence']=[]
        path.write_text(json.dumps(v))
    if fault:
        with pytest.raises(ValueError):C.completed_phases(reader,p,ready,D,env)
    else:C.completed_phases(reader,p,ready,D,env)

@pytest.mark.parametrize('kind',['aggregate','disclosure'])
@pytest.mark.parametrize('fault',[None,'root','controls','run','history','environment','original_rejection'])
def test_aggregate_publication_projects_only_authenticated_root(tmp_path,fault,kind):
    from scripts.fleet.advance_authoring_v8 import selected as original_selection
    from scripts.fleet.advance_authoring_v8 import MODEL
    worker=SimpleNamespace(root=tmp_path/'new',run=tmp_path/'run/instruction',campaign=SimpleNamespace(model_slots=1,slot_root=MODEL))
    controls=object();I=SimpleNamespace(selected=lambda _: (worker,controls,None))
    old=(object(),I,object(),object(),object(),{'run_root':str(tmp_path/'run')},tmp_path/'old',controls)
    current=(*old[:6],worker.root,controls);chosen=(*current[:1],SimpleNamespace(selected=I.selected),*current[2:])
    require=M.require;current_env=current;env=old;original_selected=original_selection
    def dispatch(observed,result):
        require(observed==current_env,'aggregate current environment changed');return original_selected(env,result)
    if kind=='disclosure':
        def retained_dcc_selected(observed,result):
            require(observed==old,'dispatcher environment differs from retained context')
            candidate,_,_=old[1].selected(result['descriptor'])
            require(candidate.root==worker.root and candidate.run==worker.run,'foreign DCC worker')
            return original_selection((*observed[:6],worker.root,observed[7]),result)
        original_selected=retained_dcc_selected
        def dispatch(observed,result):
            require(observed==current_env,'retained disclosure current environment changed')
            return original_selected(env,result)
    aggregate=dispatch
    def make_outer():
        env=current;original_selected=aggregate
        def selected(observed,result):
            require(observed==chosen,'derivative dispatcher environment changed')
            return original_selected(env,result)
        return selected
    outer=make_outer();D=SimpleNamespace(selected=outer,declared_namespace={})
    helper=M.binding(__file__);p={'helper':helper};ready={'checkpoint':{'actual':'ready'}}
    reader=SimpleNamespace(require=M.require,binding=M.binding,read=lambda _:dict(descriptor={}),dependencies=lambda _:[helper],assign=M.assign)
    if fault=='root':worker.root=tmp_path/'other'
    elif fault=='controls':I.selected=lambda _:(worker,object(),None);chosen[1].selected=I.selected
    elif fault=='run':worker.run=tmp_path/'other'
    elif fault=='history':reader.dependencies=lambda _:[]
    elif fault=='environment':env=(*old[:5],{'run_root':'/foreign'},*old[6:])
    elif fault=='original_rejection':worker.campaign.model_slots=9
    if fault:
        with pytest.raises(ValueError):
            C.selected_aggregate_root(reader,p,ready,D,chosen);D.selected(chosen,dict(descriptor={}))
    else:
        if kind=='aggregate':
            with pytest.raises(ValueError,match='instruction selection differs'):outer(chosen,dict(descriptor={}))
        else:assert outer(chosen,dict(descriptor={}))==(worker,None)
        C.selected_aggregate_root(reader,p,ready,D,chosen)
        assert D.selected(chosen,dict(descriptor={}))==(worker,None)
        with pytest.raises(ValueError):D.selected(old,dict(descriptor={}))
        if kind=='aggregate':
            with pytest.raises(ValueError):outer(chosen,dict(descriptor={}))

@pytest.mark.parametrize('fault',[None,'source','helper','route','tamper'])
def test_nested_aggregate_binding_uses_only_exact_typed_history(tmp_path,fault):
    helper={'path':'/actual.py','sha256':'actual'};source={'path':'/source','sha256':'source'}
    current=dict(schema_version='fleet-retained-completed-aggregate/v1',workbook='0171',source=source,helper=helper)
    def put(n,v):
        f=tmp_path/str(n);f.write_text(json.dumps(v));return M.binding(f)
    end=put(0,current);next_row=end
    route=[('fleet-instruction-semantic-continuation/v1','retained_context'),('fleet-completed-instruction-continuation/v1','semantic_context'),('fleet-ordinary-mechanical-dialogue/v1','semantic_context'),('fleet-retained-completed-review-tail/v1','context'),('fleet-dialogue-source-revision-review/v1','semantic_context')]
    for n,(schema,key)in enumerate(route,1):
        current=dict(schema_version=schema,workbook='foreign'if fault=='source'and n==2 else'0171',source=source,helper=helper)
        current[key]=dict(plan=next_row,helper=helper if fault!='helper' or n!=3 else{})
        if fault=='route'and n==4:current['schema_version']='unlisted'
        next_row=put(n,current)
    if fault=='tamper':Path(end['path']).write_text('{}')
    p=dict(review_plan=next_row,source=source,workbook='0171')
    if fault:
        with pytest.raises(ValueError):C.aggregate_history_binding(M,p)
    else:assert C.aggregate_history_binding(M,p)==helper

@pytest.mark.parametrize('fault',[None,'missing','order','guard','predecessor','model','reader','lease_archive'])
def test_direct_adoption_retains_all_three_original_guards(full_tail,fault):
    reader,p,ready,D,env=full_tail
    p.pop('continuation')
    original=reader.authorization;seen=[]
    def authority(row,observed):
        assert row==ready['plan'] and observed==env
        seen.append(row);return original(row,observed)
    reader.authorization=authority
    test_full_three_phase_completion_retains_every_original_guard(full_tail,fault)
    assert seen==[ready['plan']]

@pytest.mark.parametrize('fault',[None,'foreign_output','invented_retained','missing_phase','wrong_phase','models','failed_guard','changed_result'])
def test_direct_ready_projection_requires_original_complete_output(tmp_path,fault):
    from scripts.fleet import fresh_publication as F
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
    author=put('author.json',{'config':{'original':'config'}})
    selected={'schema_version':M.SCHEMA,'workbook':'0102','output':str(tmp_path/'output'),'operations':str(tmp_path/'ops'),'authoring_job':author}
    planrow=put('plan.json',selected)
    cp={'path':'/actual-ready','sha256':'a'}
    outcomes=[put('ops/%03d/outcome.json'%n,dict(status='COMPLETED_OPERATION',result=cp))for n in range(3)]
    ready=dict(schema_version=M.SCHEMA,status='PUBLICATION_READY',plan=planrow,operations=outcomes,models_dispatched=0,publication_performed=False,automatic_retry=False,checkpoint=cp)
    if fault=='invented_retained':ready['retained_adoption']={}
    if fault=='missing_phase':ready['operations']=outcomes[1:]
    if fault=='wrong_phase':ready['operations']=list(reversed(outcomes))
    if fault=='models':ready['models_dispatched']=1
    if fault=='changed_result':ready['checkpoint']={}
    if fault=='failed_guard':outcomes[-1]=put('ops/002/outcome.json',dict(status='RECONCILIATION_REQUIRED',result=cp))
    record=put('foreign/result.json'if fault=='foreign_output'else'output/result.json',ready)
    plan=dict(ready=record,source_revision=dict(plan=planrow,outcome=outcomes[-1],completed_reader={'exact':'reader'}))
    pub=SimpleNamespace(checked=M.read,require=M.require,binding=M.binding)
    if fault:
        with pytest.raises(ValueError):F.ready_value(pub,plan)
    else:
        result=F.ready_value(pub,plan)
        assert result['job']==author and result['checkpoint']==cp and M.read(record)==ready
