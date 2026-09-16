from pathlib import Path
import json,sys
from types import SimpleNamespace
import pytest
from scripts.fleet import instruction_model_continue as M


@pytest.mark.parametrize('fault',[None,'source','owner','result','lease','selection','previous','sidecar'])
def test_sealed_model_boundary_retains_actual_predecessor(tmp_path,fault):
    def put(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value,sort_keys=True));return M.binding(p)
    source=put('source',{});helper=put('helper',{});previous=put('previous',{})
    p=dict(previous=previous,phase='nat-writer',source=source)
    old=dict(owner='old',helper=helper)
    descriptor=put('descriptor',{});lease=dict(owner='native',token='fixture',revision=2,sha256='b'*64)
    encoded=dict(type='Lease',value={k:dict(type='scalar',value=v)for k,v in lease.items()})
    last=put('last',{});prior=put('prior',dict(status='STEP_COMPLETE',next_step='nat-writer',selection={'source':'same'},lease=encoded,checkpoint=last))
    intent=put('intent',dict(action='step',models=True,arguments=dict(descriptor=descriptor,checkpoint=prior)))
    guard=put('guard',dict(returncode=0,failure=None,intent=intent))
    step_intent=put('step-intent',dict(previous=last))
    blocked=dict(status='MODEL_RECONCILIATION_REQUIRED',step='nat-writer',selection={'source':'same'},lease=lease,intent=step_intent)
    if fault=='lease':blocked['lease']=dict(lease,revision=3)
    if fault=='selection':blocked['selection']={'source':'foreign'}
    if fault=='previous':blocked['intent']=put('other-step',dict(previous=put('other-checkpoint',{})))
    blocked_row=put('blocked',blocked)
    result=put('result',dict(status='MODEL_RECONCILIATION_REQUIRED'if fault!='result'else'STEP_COMPLETE',next_step='nat-writer',descriptor=descriptor,checkpoint=blocked_row,lease=encoded))
    p['failed_outcome']=put('outcome',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
    p['failed_sidecar']=put('sidecar',dict(plan=previous,helper=helper,intent=intent,kind='instruction',automatic_retry=fault=='sidecar'))
    job=put('job',dict(driver=helper,source=source if fault!='source'else put('foreign-source',{}),command=['python','--plan',previous['path'],'--sha256',previous['sha256']]))
    wi=put('worker-intent',dict(owner='old'if fault!='owner'else'foreign',job=job))
    proof=put('proof',dict(worker_intent=wi));p['certificate']=put('certificate',dict(proof=proof))
    if fault:
        with pytest.raises(ValueError):M.failure(p,old,None)
    else:assert M.failure(p,old,None)==(dict(descriptor=descriptor,checkpoint=prior),blocked_row)


def test_payload_dispatch_delta_is_exact(tmp_path,monkeypatch):
    from scripts.batch import harbor_disclosure_payload_repair as actual
    from scripts import batch
    import ast
    text=Path(actual.__file__).read_text();tree=ast.parse(text)
    for node in tree.body:
        if isinstance(node,ast.FunctionDef)and node.name in('original_identity','validate_prepared'):
            node.body=[c for c in node.body if not(isinstance(c,ast.If)and 'harbor-model-pretransport-recovery/v1'in ast.unparse(c))]
    old=tmp_path/'old.py';old.write_text(ast.unparse(tree))
    original=M.module('original_payload_fixture',M.binding(old))
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_payload_repair',original);monkeypatch.setattr(batch,'harbor_disclosure_payload_repair',original)
    p=dict(payload_original=M.binding(old),payload_reader=M.binding(actual.__file__))
    package,saved=M.payload_scope(None,p)
    assert package is original and set(saved)=={'original_identity','validate_prepared'}
    for name,function in saved.items():setattr(package,name,function)
    altered=tmp_path/'altered.py';altered.write_text(text+'\nUNRELATED_CHANGE=True\n')
    with pytest.raises(ValueError):M.payload_scope(None,dict(p,payload_reader=M.binding(altered)))


def test_aggregate_history_keeps_prior_lease_only_after_full_new_renewal_validation(tmp_path):
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class Worker:
        disclosure_repair:dict
        lease_renewal:dict|None
    receipt=dict(implementation='impl',history_reader='history',round_plan='run',round_result='result',review_plan='review',aggregate='aggregate',initial='initial',prior_continuations={},historical_records=[dict(archive='archive')])
    seen=[];A=SimpleNamespace(__file__='implementation.py',read=lambda _:receipt,record=lambda _: 'impl',selected=lambda _:True,E=SimpleNamespace(checked_bytes=lambda r:seen.append(r)))
    A.validate=lambda w,b:(seen.append(('validated',w.lease_renewal,b))or receipt)
    worker=Worker({'path':'actual-adoption'}, {'path':'actual-renewal'})
    R=SimpleNamespace(validate=lambda w,b:(seen.append(('renewal',w,b))or dict(prior_continuations={'disclosure_repair':w.disclosure_repair})))
    value,records=M.adoption_history(A,R,worker,{'current':'tracker'})
    assert value is receipt and seen[0][0]=='renewal'
    assert ('validated',None,{'current':'tracker'})in seen
    assert worker.lease_renewal=={'path':'actual-renewal'}and 'archive'in records
    R.validate=lambda w,b:dict(prior_continuations={'disclosure_repair':{'path':'foreign'}})
    with pytest.raises(ValueError):M.adoption_history(A,R,worker,{})


def test_preserved_clone_uses_selected_model_callbacks_and_restores(tmp_path):
    module=tmp_path/'preserved.py';module.write_text('''def make(W):
    scope={'step_directory':W.step_directory,'_operation':W._operation}
    def step():
        assert W is not None
        return scope['step_directory'](),scope['_operation']()
    return step
''')
    K=M.module('actual_preserved_callback_fixture',M.binding(module))
    W=SimpleNamespace(step_directory=lambda:'old-directory',_operation=lambda:'old-operation')
    W._step=K.make(W)
    assert W._step()==('old-directory','old-operation')
    with M.preserved_callbacks(W,M.binding(module),lambda:'new-directory',lambda:'new-operation'):
        assert W._step()==('new-directory','new-operation')
    assert W._step()==('old-directory','old-operation')
    old=W.step_directory;W.step_directory=lambda:'foreign'
    with pytest.raises(ValueError):
        with M.preserved_callbacks(W,M.binding(module),old,W._operation):pass


@pytest.mark.parametrize('fault',[None,'guard','descriptor','lease','side','worker','effects','process'])
def test_completed_renewal_retains_guarded_handoff_without_new_model_effects(tmp_path,monkeypatch,fault):
    def put(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value,sort_keys=True));return M.binding(p)
    source=put('source',{});helper=put('helper',{});previous=put('previous',{})
    p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,source=source,previous=previous,phase='nat-writer',owner='new',fence=3,output=str(tmp_path/'new'),helper=helper)
    old=dict(p,owner='old',fence=2,output=str(tmp_path/'old'));oldrow=put('plan',old)
    descriptor=put('descriptor',{});renewed=put('renewed',dict(status='RETRY_READY',next_step='nat-writer',descriptor=descriptor,lease={'exact':'new'}))
    renewal_intent=put('renewal-intent',{});rg=put('renewal-guard',dict(returncode=0,failure=None,intent=renewal_intent));ro=put('renewal-outcome',dict(status='COMPLETED_OPERATION',resource=rg,result=renewed))
    original_intent=put('original-intent',{});blocked=put('blocked',dict(intent=original_intent))
    monkeypatch.setattr(M,'failure',lambda *args:({},blocked))
    args=dict(checkpoint=renewed,descriptor=descriptor if fault!='descriptor'else put('foreign-descriptor',{}))
    intent=put('failed-intent',dict(arguments=args));fg=put('failed-guard',dict(returncode=1 if fault=='guard'else 0,failure=None,intent=intent))
    fr=put('failed-result',dict(status='RECONCILIATION_REQUIRED',next_step='nat-writer',descriptor=descriptor,lease={'exact':'old'if fault=='lease'else'new'},checkpoint=original_intent))
    fo=put('failed-outcome',dict(status='COMPLETED_OPERATION',resource=fg,result=fr));side=put('side',dict(plan=oldrow,helper=helper,intent=intent,kind='instruction',automatic_retry=fault=='side'))
    job=put('job',dict(driver=helper,source=source,command=['python','--plan',oldrow['path'],'--sha256',oldrow['sha256']]))
    wi=put('worker-intent',dict(owner='foreign'if fault=='worker'else'old',job=job));outer=put('outer',dict(returncode=1,failure_type=None,result=None,intent=wi))
    rows=[outer,ro,fo,side]
    if fault=='effects':rows.append(dict(path=str(Path(old['output'])/'model-step/intent.json'),sha256='a'*64))
    observation=put('observation',dict(processes=[123]if fault=='process'else[],files=rows))
    p['settled_renewal']=put('settled',dict(schema_version='fleet-model-completed-renewal/v1',plan=oldrow,renewal_outcome=ro,failure_outcome=fo,failure_sidecar=side,worker_outcome=outer,observation=observation))
    if fault:
        with pytest.raises(ValueError):M.settled_renewal(p,continued=False)
    else:assert M.settled_renewal(p,continued=False)==args


@pytest.mark.parametrize('fault',[None,'predecessor','models','source','owner','inventory','log'])
def test_completed_model_tail_preserves_actual_guarded_calls(tmp_path,fault):
    def put(name,value):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,sort_keys=True));return M.binding(path)
    source=put('source',{});helper=put('helper',{});ordinary=put('author-job',dict(models=True));s=put('S',dict(original_job=ordinary));parent=put('P',dict(previous=s))
    p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,source=source,previous=parent,phase='nat-writer',owner='new',fence=4,output=str(tmp_path/'new'),helper=helper)
    old=dict(p,owner='old',fence=3,output=str(tmp_path/'old'));oldrow=put('plan',old);descriptor=put('descriptor',{})
    outcomes=[];previous=put('renewed',{})
    for index,status,next_phase in [(0,'MODEL_REQUIRED','nat-writer'),(1,'STEP_COMPLETE','nat-review'),(2,'STEP_COMPLETE','nat-commit')]:
        directory='old/first-step'if index==0 else 'ops/%03d'%(index-1)
        intent=put(directory+'/intent.json',dict(arguments=dict(checkpoint=previous,descriptor=descriptor)))
        guard=put(directory+'/resource.json',dict(returncode=0,failure=None,intent=intent))
        put(directory+'/model-continuation-bootstrap.json',dict(plan=oldrow,helper=helper,intent=intent,kind='instruction',automatic_retry=False))
        result=put(directory+'/result.json',dict(status=status,next_step=next_phase,descriptor=descriptor,evidence=[previous]))
        outcomes.append(put(directory+'/outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result)));previous=result
    args=dict(checkpoint=previous if fault!='predecessor'else put('foreign',{}),descriptor=descriptor)
    fi=put('ops/002/intent.json',dict(arguments=args,models=False if fault=='models'else True))
    log=tmp_path/'failure.log';log.write_text('foreign failure'if fault=='log'else'validate_notes_pair\nchanged evidence: instruction.md')
    fg=put('ops/002/resource.json',dict(returncode=1,failure=None,intent=fi,log=M.binding(log)))
    failed=put('ops/002/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=fg))
    side=put('ops/002/model-continuation-bootstrap.json',dict(plan=oldrow,helper=helper,intent=fi))
    job=put('worker-job',dict(driver=helper,source=source if fault!='source'else put('foreign-source',{}),command=['python','--plan',oldrow['path'],'--sha256',oldrow['sha256']]))
    wi=put('worker-intent',dict(owner='foreign'if fault=='owner'else'old',job=job));outer=put('outer',dict(returncode=1,failure_type=None,result=None,intent=wi))
    rows=[*outcomes,failed,side,outer];obs=put('observation',dict(processes=[],files=rows[1:]if fault=='inventory'else rows))
    p['completed_tail']=put('tail',dict(schema_version='fleet-model-completed-tail/v1',plan=oldrow,outcomes=outcomes,failure=failed,sidecar=side,outer=outer,observation=obs))
    if fault:
        with pytest.raises(ValueError):M.completed_tail(p)
    else:assert M.completed_tail(p)==(args,Path(old['output']))

@pytest.mark.parametrize('remaining',[1501,2000,3330])
def test_original_renewal_required_window_keeps_bounded_runway(remaining):
 I=SimpleNamespace(guard_seconds=lambda action,args:3300)
 before={'leases':{'task_agent':{'expires_at':remaining*10**9}}}
 assert M.renewal_admission(I,{},before,now=0)==remaining*10**9

@pytest.mark.parametrize('remaining',[-1,0,1500,3331,7200])
def test_renewal_admission_never_forces_an_ineligible_or_expired_step(remaining):
 I=SimpleNamespace(guard_seconds=lambda action,args:3300)
 with pytest.raises(ValueError):M.renewal_admission(I,{}, {'leases':{'task_agent':{'expires_at':remaining*10**9}}},now=0)


def test_optional_historical_evidence_follows_full_original_adoption_validation():
 from types import SimpleNamespace
 from scripts.fleet import instruction_model_continue as M
 events=[];receipt={'implementation':'core','prior_continuations':{}}
 def validate(w,b):events.append('validate');return receipt
 A=SimpleNamespace(__file__='/core',read=lambda row:receipt,record=lambda p:'core',selected=lambda w:True,validate=validate,E=SimpleNamespace(checked_bytes=lambda row:events.append(('checked',row))))
 worker=SimpleNamespace(disclosure_repair='receipt',lease_renewal=None)
 # The exact evidence field inventory is present even before projection.
 receipt.update({k:k for k in('history_reader','round_plan','round_result','review_plan','aggregate','initial')});receipt['historical_records']=[{'archive':'old-instruction'}]
 def archive(a,row,validated):
  assert events==['validate']and a is A and row=='receipt'and validated is receipt
  events.append('archive');return ['real-submitted-archive']
 assert M.adoption_history(A,None,worker,{},historical_evidence=archive)==(receipt,['real-submitted-archive'])
 assert events==['validate','archive',('checked','real-submitted-archive')]
 events.clear()
 def reject(w,b):raise ValueError('original adoption rejected')
 A.validate=reject
 import pytest
 with pytest.raises(ValueError,match='original adoption rejected'):M.adoption_history(A,None,worker,{},historical_evidence=archive)
 assert events==[]
