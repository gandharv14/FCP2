import ast
import json
from dataclasses import dataclass, asdict
from types import SimpleNamespace

import pytest

from scripts.fleet import completed_checkpoint_takeover as M


SOURCE = '''def step(worker, selection, lease, checkpoint, predecessor):
    require(checkpoint == worker.checkpoint, 'original checkpoint differs')
    require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')
    return worker.operation(selection, lease, checkpoint)
'''


@dataclass
class Lease:
    owner: str
    token: str
    revision: int
    sha256: str


def fixture():
    scope = dict(require=M.require, asdict=asdict, codec=SimpleNamespace(encode=lambda s: s))
    exec(compile(SOURCE, '/original/worker.py', 'exec'), scope)
    old = Lease('old', 'a', 1, 'one'); new = Lease('new', 'b', 2, 'two')
    calls = []
    worker = SimpleNamespace(checkpoint='exact', operation=lambda *a: calls.append(a))
    predecessor = dict(selection='source', lease=asdict(old))
    return scope['step'], old, new, calls, worker, predecessor


def test_only_lease_assertion_changes_and_operation_gets_real_current_lease():
    original, old, new, calls, worker, prior = fixture()
    proofs = []
    def allowed(w, s, l, c, p):
        assert (w, s, l, c, p) == (worker, 'source', new, 'exact', prior)
        proofs.append(True)
        return True
    selected = M.lease_predicate_function(original, SOURCE, allowed)
    selected(worker, 'source', new, 'exact', prior)
    assert proofs == [True] and calls == [('source', new, 'exact')]
    with pytest.raises(ValueError, match='previous worker checkpoint'):
        original(worker, 'source', new, 'exact', prior)


def test_original_checkpoint_and_selection_checks_still_reject():
    original, old, new, calls, worker, prior = fixture()
    proofs = []
    selected = M.lease_predicate_function(original, SOURCE, lambda *a: proofs.append(a) or True)
    with pytest.raises(ValueError, match='original checkpoint'):
        selected(worker, 'source', new, 'foreign', prior)
    with pytest.raises(ValueError, match='previous worker checkpoint'):
        selected(worker, 'foreign', new, 'exact', prior)
    assert not proofs and not calls


def test_unchanged_lease_uses_original_path_without_takeover_override():
    original, old, new, calls, worker, prior = fixture()
    selected = M.lease_predicate_function(original, SOURCE, lambda *a: pytest.fail('unexpected projection'))
    selected(worker, 'source', old, 'exact', prior)
    assert calls == [('source', old, 'exact')]


@pytest.mark.parametrize('source', [
    SOURCE.replace("require(checkpoint == worker.checkpoint, 'original checkpoint differs')", 'pass'),
    SOURCE.replace('worker.operation(selection, lease, checkpoint)', 'worker.operation(selection, lease, None)'),
    SOURCE.replace("predecessor['selection']", "predecessor['foreign']"),
])
def test_cannot_discard_existing_installed_worker_checks(source):
    original, *_ = fixture()
    with pytest.raises(ValueError, match='actual installed worker'):
        M.lease_predicate_function(original, source, lambda *a: True)


def test_existing_scoped_transformation_must_be_retained():
    original, old, new, calls, worker, prior = fixture()
    amended = SOURCE.replace("require(checkpoint == worker.checkpoint, 'original checkpoint differs')",
        "require(checkpoint == worker.checkpoint and worker.preserved, 'original checkpoint differs')")
    scope = dict(original.__globals__)
    exec(compile(amended, '/original/worker.py', 'exec'), scope)
    with pytest.raises(ValueError, match='actual installed worker'):
        M.lease_predicate_function(scope['step'], SOURCE, lambda *a: True)
    selected = M.lease_predicate_function(scope['step'], amended, lambda *a: True)
    worker.preserved = False
    with pytest.raises(ValueError, match='original checkpoint'):
        selected(worker, 'source', new, 'exact', prior)
    assert calls == []


def test_imported_module_compiler_context_preserved_without_module_execution():
    original,old,new,calls,worker,prior=fixture()
    module_source="import json as codec\nexecutions.append('module')\n"+SOURCE
    scope=dict(original.__globals__,executions=[])
    exec(compile(module_source,'/original/imported-worker.py','exec'),scope)
    scope['codec']=original.__globals__['codec']
    selected=M.lease_predicate_function(scope['step'],SOURCE,lambda *a:True,module_source=module_source)
    selected(worker,'source',new,'exact',prior)
    assert scope['executions']==['module'] and calls==[('source',new,'exact')]
    with pytest.raises(ValueError,match='module function differs'):
        M.lease_predicate_function(scope['step'],SOURCE,lambda *a:True,
            module_source=module_source.replace('worker.operation(selection, lease, checkpoint)','None'))


def test_exact_current_lease_predicate_rejects_foreign_predecessor(monkeypatch):
    original, old, new, calls, worker, prior = fixture()
    value = dict(selection='source', checkpoint='exact')
    monkeypatch.setattr(M, 'validate', lambda *a, **k: ({}, value, worker, 'source', old, new))
    monkeypatch.setattr(M, 'read', lambda row: prior)
    I = SimpleNamespace(codec=SimpleNamespace(encode=lambda x: x))
    allowed = M.lease_predicate('profile', I, None, None, None)
    assert allowed(worker, 'source', new, 'exact', prior)
    with pytest.raises(ValueError, match='exact original completed predecessor'):
        allowed(worker, 'source', new, 'foreign', prior)
    with pytest.raises(ValueError, match='exact original completed predecessor'):
        allowed(worker, 'source', old, 'exact', prior)


def test_full_custody_validation_failure_cannot_reach_predicate(monkeypatch):
    def rejected(*a, **k):
        raise ValueError('changed current tracker')
    monkeypatch.setattr(M, 'validate', rejected)
    with pytest.raises(ValueError, match='changed current tracker'):
        M.lease_predicate('profile', None, None, None, None)(None, None, None, None, None)


def handoff_fixture(tmp_path):
    def keep(path, value):
        path.write_text(json.dumps(value, sort_keys=True))
        return M.binding(path)
    old = Lease('old', 'a', 40, 'old-sha'); new = Lease('new', 'b', 41, 'new-sha')
    source = keep(tmp_path / 'source.json', {'source': True})
    selection = SimpleNamespace(content=SimpleNamespace(workbook='0001', bindings={'original_raw_source': source}))
    worker = SimpleNamespace(run=tmp_path / 'worker', root=tmp_path / 'code',
        context=SimpleNamespace(tracker_root=tmp_path))
    before = dict(revision=40, workbook_id='0001', bindings={'original_raw_source': source},
        leases={'task_agent': dict(owner='old', token='a')})
    after = dict(before, revision=41, leases={'task_agent': dict(owner='new', token='b')})
    tracker = keep(tmp_path / '0001.json', after)
    new.sha256 = tracker['sha256']
    descriptor = keep(tmp_path / 'original-descriptor.json', {'origin': [], 'initial': {'retained': True}})
    checkpoint = keep(tmp_path / 'completed.json', dict(selection={'content': 'same'}, lease=asdict(old)))
    value = dict(status='STEP_COMPLETE', descriptor=descriptor, checkpoint=checkpoint,
        next_step='enqueue', publication_performed=False, selection={'content': 'same'}, lease=asdict(old))
    result = keep(tmp_path / 'original-result.json', value)
    guard = keep(tmp_path / 'resource.json', {'returncode': 0})
    outcome = keep(tmp_path / 'outcome.json', {'resource': guard, 'result': result})
    spec = dict(checkpoint=result, guard=guard, tracker=dict(path=str(tmp_path/'0001.json'), sha256=old.sha256))
    stop = keep(tmp_path / 'stop.json', dict(selection=spec, before=dict(path='/archive/before', sha256=old.sha256)))
    completion = keep(tmp_path / 'completion.json', dict(stopped_owner_proof=stop, after=tracker))
    T = SimpleNamespace(__file__=M.__file__, life=SimpleNamespace(__file__=M.__file__),
        _selection=lambda v: v, validate_completion=lambda b: (before, after, new))
    tp = keep(tmp_path / 'takeover-plan.json', dict(schema_version='harbor-allowance-takeover-direct/v1',
        selection=spec, shared=dict(takeover=M.binding(M.__file__), lifecycle=M.binding(M.__file__))))
    p = dict(schema_version=M.SCHEMA, implementation=M.binding(M.__file__), takeover_module=M.binding(M.__file__),
        takeover_plan=tp, completion=completion, outcome=outcome, result=result, descriptor=descriptor, guard=guard,
        worker_run=str(worker.run), worker_root=str(worker.root), workbook='0001', source=source,
        tracker=tracker, directory=str(tmp_path/'handoff'))
    profile = keep(tmp_path/'profile.json', p)
    live = []
    W = SimpleNamespace(STEPS=('enqueue',), active=lambda w,s,l: live.append(l))
    I = SimpleNamespace(_prior=lambda r,d: (value, selection, old), selected=lambda d: (worker, None, None),
        base=SimpleNamespace(encode=asdict), codec=SimpleNamespace(encode=lambda s: {'content':'same'}))
    I._result=lambda status,descriptor,selection,lease,**kw: dict(schema_version='original-instruction',
        status=status,descriptor=descriptor,selection={'content':'same'},lease=asdict(lease),**kw)
    return profile,I,W,T,lambda b: (result,value),keep,p,old,new,live


def test_named_handoff_preserves_original_checkpoint_and_delegates_later_results(tmp_path):
    profile,I,W,T,successful,keep,p,old,new,live = handoff_fixture(tmp_path)
    original = I._prior
    row = M.handoff(profile,I,W,T,successful,keep)
    saved = M.read(row)
    with M.prior_scope(row,profile,I,W,T,successful):
        actual, selection, lease = I._prior(saved['result'],saved['descriptor'])
        assert lease == new and actual['checkpoint'] == M.read(p['result'])['checkpoint']
        assert M.completed(row,I)==(saved['result'],actual)
        assert I._prior(p['result'],p['descriptor'])[2] == old
    assert I._prior is original and all(l == new for l in live)
    assert M.read(p['result'])['lease'] == asdict(old)


def test_handoff_rejects_changed_live_tracker_and_restores_decoder(tmp_path):
    profile,I,W,T,successful,keep,p,old,new,live = handoff_fixture(tmp_path)
    row = M.handoff(profile,I,W,T,successful,keep)
    saved = M.read(row); original = I._prior
    with M.prior_scope(row,profile,I,W,T,successful):
        keep(__import__('pathlib').Path(p['tracker']['path']), {'foreign': True})
        with pytest.raises(ValueError, match='current takeover tracker changed'):
            I._prior(saved['result'],saved['descriptor'])
    assert I._prior is original


def test_changed_handoff_cannot_relabel_old_checkpoint(tmp_path):
    profile,I,W,T,successful,keep,p,old,new,live = handoff_fixture(tmp_path)
    row = M.handoff(profile,I,W,T,successful,keep)
    saved = M.read(row)
    changed = keep(__import__('pathlib').Path(row['path']), dict(saved, checkpoint=p['source']))
    with M.prior_scope(changed,profile,I,W,T,successful):
        with pytest.raises(ValueError, match='handoff changed'):
            I._prior(saved['result'],saved['descriptor'])


@pytest.mark.parametrize('fault',[None,'tracker_lease','extra_effect','different_exception','failed_readiness'])
def test_enqueue_scope_requires_exact_unchanged_custody_and_failed_tree(tmp_path,monkeypatch,fault):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    _,value,worker,selection,_,_=M.validate(profile,I,W,T,successful)
    I.__file__=M.__file__;I.guard_seconds=lambda *a:90
    directory=tmp_path/'old-enqueue';directory.mkdir()
    W.step_directory=lambda w,phase:directory if phase=='enqueue'else tmp_path/phase
    W.code=lambda w:{'original':'unchanged'}
    worker.run.mkdir();initial=keep(worker.run/'initial.json',{'original':True})
    wi=keep(directory/'intent.json',dict(code=W.code(worker),initial=initial,lease=asdict(old),
        previous=value['checkpoint'],selection=value['selection'],step='enqueue'))
    exception=keep(directory/'exception.json',dict(type='AdmissionError',automatic_replay=False,
        message_sha256=M.hashlib.sha256(b'complete current readiness required').hexdigest()))
    report=keep(directory/'readiness.json',dict(publication_ready=True,blockers=[],checks=['all']))
    gi=keep(tmp_path/'guard-intent.json',dict(action='step',arguments=dict(descriptor=p['descriptor'],checkpoint=p['result']),
        code=M.binding(M.__file__),timeout_seconds=90))
    guard=keep(tmp_path/'failure-resource.json',dict(intent=gi,returncode=1,failure=None))
    # Original I.run omits the result field when the guarded child failed.
    outcome=keep(tmp_path/'failed-outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=guard))
    proof=keep(tmp_path/'failure.json',dict(schema_version='fleet-enqueue-readiness-failure/v1',profile=profile,
        outcome=outcome,directory=str(directory),tree={n.name:M.binding(n)['sha256']for n in directory.iterdir()},
        worker_intent=wi,exception=exception))
    selected=dict(p,guard=guard)
    if fault=='tracker_lease':old=Lease('foreign','foreign',40,'foreign')
    monkeypatch.setattr(M,'validate',lambda *a,**k:(selected,value,worker,selection,old,new))
    if fault=='extra_effect':keep(directory/'complete.json',{'unexpected':'effect'})
    if fault=='different_exception':keep(directory/'exception.json',{'different':'failure'})
    if fault=='failed_readiness':keep(directory/'readiness.json',dict(publication_ready=False,blockers=['fault'],checks=['all']))
    before=W.step_directory
    if fault:
        with pytest.raises(ValueError):
            with M.enqueue_directory_scope(proof,profile,I,W,T,successful):pass
        assert W.step_directory is before

    else:
        with M.enqueue_directory_scope(proof,profile,I,W,T,successful)as chosen:
            assert chosen(worker,'enqueue')==tmp_path/'handoff/steps/enqueue'
            assert chosen(worker,'dialogue-commit')==tmp_path/'dialogue-commit'
            assert M.read(wi)['lease']==asdict(old)
        assert W.step_directory is before


@pytest.mark.parametrize('fault',[None,'foreign_handoff','foreign_predecessor','foreign_phase','unsealed'])
def test_completed_successor_allows_historical_handoff_after_real_tracker_advance(tmp_path,fault):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    row=M.handoff(profile,I,W,T,successful,keep);handoff=M.read(row)
    I.__file__=M.__file__;I.guard_seconds=lambda *a:90;I.base.decode=lambda value:Lease(**value)
    worker=I.selected(p['descriptor'])[0];worker.run.mkdir();initial=keep(worker.run/'initial.json',{})
    step=tmp_path/'next-step';step.mkdir();W.step_directory=lambda *a:step
    lease=Lease(new.owner,new.token,new.revision+1,'after-owning-step')
    wi=keep(step/'intent.json',dict(previous=handoff['checkpoint'],lease=asdict(new),initial=initial))
    complete=keep(step/'complete.json',dict(status='completed',step='enqueue',intent=wi,
        selection=handoff['selection'],lease=asdict(lease)))
    gi=keep(tmp_path/'next-guard-intent.json',dict(action='step',code=M.binding(M.__file__),timeout_seconds=90,
        arguments=dict(descriptor=handoff['descriptor'],checkpoint=handoff['result'])))
    guard=keep(tmp_path/'next-resource.json',dict(intent=gi,returncode=0,failure=None))
    value=dict(status='PUBLICATION_READY',descriptor=handoff['descriptor'],checkpoint=complete,
        selection=handoff['selection'],lease=asdict(lease),evidence=[handoff['result']])
    result=keep(tmp_path/'next-result.json',value)
    outcome=keep(tmp_path/'next-outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
    next_successful=lambda bound: (result,value)if bound==outcome else successful(bound)
    # The original live tracker has really changed; history must use T archives.
    keep(__import__('pathlib').Path(p['tracker']['path']),{'revision':42,'actual':'later owning CAS'})
    if fault=='foreign_handoff':keep(__import__('pathlib').Path(gi['path']),dict(M.read(gi),arguments=dict(descriptor=handoff['descriptor'],checkpoint=p['result'])))
    if fault=='foreign_predecessor':keep(__import__('pathlib').Path(wi['path']),dict(M.read(wi),previous=p['source']))
    if fault=='foreign_phase':keep(__import__('pathlib').Path(complete['path']),dict(M.read(complete),step='dialogue-review'))
    if fault=='unsealed':keep(__import__('pathlib').Path(outcome['path']),dict(M.read(outcome),status='RECONCILIATION_REQUIRED'))
    with M.prior_scope(row,profile,I,W,T,next_successful,completed_successor=outcome):
        if fault:
            with pytest.raises(ValueError):I._prior(handoff['result'],handoff['descriptor'])
        else:
            value,selection,lease=I._prior(handoff['result'],handoff['descriptor'])
            assert lease==new and value['checkpoint']==handoff['checkpoint']
    with M.prior_scope(row,profile,I,W,T,successful):
        with pytest.raises(ValueError,match='current takeover tracker changed'):
            I._prior(handoff['result'],handoff['descriptor'])


def test_pending_handoff_retains_model_required_status(tmp_path,monkeypatch):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    checked=M.validate(profile,I,W,T,successful);value=dict(checked[1],status='MODEL_REQUIRED')
    monkeypatch.setattr(M,'validate',lambda *a,**k:(p,value,*checked[2:]))
    row=M.handoff(profile,I,W,T,successful,keep);saved=M.read(row)
    assert M.read(saved['result'])['status']=='MODEL_REQUIRED'
    assert M.read(saved['result'])['checkpoint']==value['checkpoint']
    assert M.read(p['result'])['lease']==asdict(old)


@pytest.mark.parametrize('fault',[None,'wrong_lease','wrong_source','changed_pending','exception'])
def test_pending_scope_changes_only_two_lease_checks_and_restores(tmp_path,monkeypatch,fault):
    from scripts.fleet import retained_pending_instruction as H
    source='''from dataclasses import asdict
from types import SimpleNamespace
codec=SimpleNamespace(encode=lambda s:s)
def require(ok,message):
 if not ok:raise ValueError(message)
def _continued_step(worker,selection,lease,*,phase,checkpoint):
 require(phase=='disclosure-review','phase changed')
 predecessor=worker.prior
 require(codec.encode(selection)==predecessor['selection'] and asdict(lease)==predecessor['lease'],'caller did not select exact previous worker checkpoint')
 observed=worker.pending
 require(observed['selection']==codec.encode(selection) and observed['lease']==asdict(lease),'pending context changed')
 return worker.operation(lease)
def route(worker,selection,lease,*,phase,checkpoint):
 return _continued_step(worker,selection,lease,phase=phase,checkpoint=checkpoint)
'''
    file=tmp_path/'pending.py';file.write_text(source);module=H.module('actual_pending_scope',H.binding(file))
    old=Lease('old','a',7,'before');new=Lease('new','b',8,'after');selection={'source':'original'}
    prior=dict(selection=selection,lease=asdict(old));pending=dict(prior,status='MODEL_REQUIRED')
    prior_row=keep_simple(tmp_path/'prior.json',prior);pending_row=keep_simple(tmp_path/'pending.json',pending)
    calls=[]
    def operation(lease):
        calls.append(lease)
        if fault=='exception':raise RuntimeError('original effect failed')
        return lease
    worker=SimpleNamespace(prior=prior,pending=pending,operation=operation)
    value=dict(checkpoint=pending_row,selection=selection);q=dict(predecessor=prior_row,pending=pending_row)
    monkeypatch.setattr(M,'validate',lambda *a,**k:({},value,worker,selection,old,new))
    monkeypatch.setattr(M,'pending_preparation',lambda *a,**k:(H,q,pending,{}))
    W=SimpleNamespace(_step=module.route);original=module._continued_step
    with M.pending_step_scope({},None,W,None,None,wrapper=H.binding(file)):
        if fault=='changed_pending':worker.pending=dict(pending,status='FOREIGN')
        lease=old if fault=='wrong_lease'else new
        # An unrelated lease never gains the T2 projection.
        if fault=='wrong_lease':lease=Lease('unrelated','x',8,'after')
        selected={'source':'foreign'}if fault=='wrong_source'else selection
        if fault:
            with pytest.raises((ValueError,RuntimeError)):W._step(worker,selected,lease,phase='disclosure-review',checkpoint=pending_row)
        else:assert W._step(worker,selected,lease,phase='disclosure-review',checkpoint=pending_row)==new
    assert module._continued_step is original and W._step is module.route
    assert M.read(prior_row)==prior and M.read(pending_row)==pending
    assert calls==([new]if fault in(None,'exception')else [])


def keep_simple(path,value):
    path.write_text(json.dumps(value));return M.binding(path)


def test_declared_global_route_preserves_original_checks_and_restores(tmp_path):
    import importlib.util
    text=SOURCE.replace('def step(', 'def _continued_step(')+'''\ndef route(worker,selection,lease,checkpoint,predecessor):
    return _continued_step(worker,selection,lease,checkpoint,predecessor)
'''
    path=tmp_path/'declared.py';path.write_text(text)
    spec=importlib.util.spec_from_file_location('declared_step_fixture',path);mod=importlib.util.module_from_spec(spec)
    mod.require=M.require;mod.asdict=asdict;mod.codec=SimpleNamespace(encode=lambda s:s);spec.loader.exec_module(mod)
    _,old,new,calls,worker,prior=fixture();W=SimpleNamespace(_step=mod.route);original=mod._continued_step
    allowed=lambda w,s,l,c,p:(w,s,l,c,p)==(worker,'source',new,'exact',prior)
    with pytest.raises(RuntimeError):
        with M.declared_step_scope(W,allowed,wrapper=M.binding(path)):
            W._step(worker,'source',new,'exact',prior)
            with pytest.raises(ValueError):W._step(worker,'foreign',new,'exact',prior)
            raise RuntimeError('caller failed')
    assert mod._continued_step is original and calls==[('source',new,'exact')]
    with pytest.raises(ValueError):W._step(worker,'source',new,'exact',prior)
    path.write_text(text+'\n# changed\n')
    with pytest.raises(ValueError):
        with M.declared_step_scope(W,allowed,wrapper={'path':str(path),'sha256':'0'*64}):pass


def test_explicit_prior_takeover_chain_keeps_actual_authorization_guard(tmp_path):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    original=keep(tmp_path/'original-authority.json',dict(revision=old.revision,workbook_id=p['workbook'],bindings={'original_raw_source':p['source']},leases={'task_agent':{'owner':old.owner,'token':old.token}}))
    old.sha256=original['sha256'];prior=keep(tmp_path/'prior-T.json',{'completed':True});guard=keep(tmp_path/'original-root-guard.json',{'original':True})
    tp=M.read(p['takeover_plan']);tp['selection'].update(checkpoint=prior,prior_completion=prior,guard=guard)
    p['prior_takeover_chain']=dict(original_tracker=original,prior_completion=prior,guard=guard)
    calls=[];T.continuation_chain=lambda *a,**k:calls.append((a,k))
    assert M.checkpoint_custody(p,tp,T,old,{}, {'current':True},new,{})
    assert calls==[((original,asdict(old),{'current':True},new),dict(takeover=p['completion'],tracker_path=p['tracker']['path']))]
    tp['selection']['checkpoint']=p['result']
    with pytest.raises(ValueError,match='prior takeover checkpoint'):M.checkpoint_custody(p,tp,T,old,{}, {},new,{})
    assert len(calls)==1


def test_prior_chain_delegates_rejection_without_forging_new_lease(tmp_path):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    original=keep(tmp_path/'original-authority.json',dict(revision=old.revision,workbook_id=p['workbook'],bindings={'original_raw_source':p['source']},leases={'task_agent':{'owner':old.owner,'token':old.token}}));old.sha256=original['sha256']
    prior=keep(tmp_path/'prior-T.json',{});tp=M.read(p['takeover_plan']);tp['selection'].update(checkpoint=prior,prior_completion=prior)
    p['prior_takeover_chain']=dict(original_tracker=original,prior_completion=prior,guard=p['guard'])
    def failed(*a,**k):raise ValueError('original lifecycle rejected changed lane or source')
    T.continuation_chain=failed
    with pytest.raises(ValueError,match='original lifecycle rejected'):M.checkpoint_custody(p,tp,T,old,{}, {},new,{})
    p['prior_takeover_chain']['original_tracker']=keep(tmp_path/'foreign.json',dict(M.read(original),workbook_id='foreign'))
    with pytest.raises(ValueError,match='actual instruction lease'):M.checkpoint_custody(p,tp,T,old,{}, {},new,{})


def test_initial_start_preserves_absent_predecessor_and_live_unused_directory(tmp_path,monkeypatch):
    value=dict(status='INSTRUCTION_READY',checkpoint=None,next_step='disclosure-build',selection={'source':'exact'},lease={'old':'exact'})
    p=dict(initial_start=True,descriptor='descriptor')
    monkeypatch.setattr(M,'read',lambda row:{'initial':dict(selection=value['selection'],lease=value['lease'])})
    worker=SimpleNamespace(run=tmp_path/'instruction');W=SimpleNamespace(STEPS=('disclosure-build','dialogue'))
    M.initial_start(p,value,None,W,worker,live=True)
    worker.run.mkdir()
    with pytest.raises(ValueError,match='already started'):M.initial_start(p,value,None,W,worker,live=True)
    # Actual completed-successor guard must separately prove later history.
    M.initial_start(p,value,None,W,worker,live=False)


@pytest.mark.parametrize('fault',['checkpoint','step','descriptor','discriminator'])
def test_initial_takeover_rejects_invented_or_foreign_start(tmp_path,monkeypatch,fault):
    value=dict(checkpoint=None,next_step='disclosure-build',selection={'source':'exact'},lease={'old':'exact'})
    p=dict(initial_start=True,descriptor='descriptor');initial=dict(selection=value['selection'],lease=value['lease'])
    if fault=='checkpoint':value['checkpoint']={'invented':True}
    elif fault=='step':value['next_step']='enqueue'
    elif fault=='descriptor':initial=dict(initial,selection={'source':'foreign'})
    else:p['initial_start']=1
    monkeypatch.setattr(M,'read',lambda row:{'initial':initial})
    with pytest.raises(ValueError,match='cannot invent'):
        M.initial_start(p,value,None,SimpleNamespace(STEPS=('disclosure-build','enqueue')),SimpleNamespace(run=tmp_path/'instruction'),live=True)

@pytest.mark.parametrize('fault',[None,'input','output','descriptor','lease','source','guard'])
def test_completed_guard_input_retains_exact_completed_output(tmp_path,fault):
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    value=M.read(p['result']);descriptor=p['descriptor'];original_result=p['result']
    prior=dict(value,next_step='final-runtime')
    input_row=keep(tmp_path/'guard-input.json',prior)
    value=dict(value,evidence=[input_row]);p['result']=keep(tmp_path/'guard-output.json',value)
    intent=keep(tmp_path/'guard-intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=input_row),
        code=M.binding(M.__file__),base_code=M.binding(M.__file__)))
    p['guard']=keep(tmp_path/'complete-resource.json',dict(intent=intent,returncode=0,failure=None))
    p['outcome']=keep(tmp_path/'complete-outcome.json',dict(resource=p['guard'],result=p['result']))
    p['completed_guard_input']=input_row
    tp=dict(selection=dict(checkpoint=input_row,guard=p['guard']))
    selection=SimpleNamespace(content=SimpleNamespace(workbook=p['workbook'],bindings={'original_raw_source':p['source']}))
    actual_lease=old
    if fault=='input':p['completed_guard_input']=original_result
    elif fault=='output':p['result']=original_result
    elif fault=='descriptor':prior['descriptor']={'foreign':True}
    elif fault=='lease':actual_lease=Lease('foreign',old.token,old.revision,old.sha256)
    elif fault=='source':selection.content.bindings['original_raw_source']={'foreign':True}
    elif fault=='guard':tp['selection']['guard']=input_row
    I.__file__=M.__file__;I.base.__file__=M.__file__
    I._prior=lambda r,d:(prior,selection,actual_lease)
    if fault:
        with pytest.raises(ValueError):M.completed_guard_input(p,tp,I,value,old)
    else:
        assert M.completed_guard_input(p,tp,I,value,old)is None
        assert M.read(input_row)==prior and M.read(p['result'])==value


def failed_successor_fixture(tmp_path):
    from pathlib import Path
    profile,I,W,T,successful,keep,p,old,new,live=handoff_fixture(tmp_path)
    row=M.handoff(profile,I,W,T,successful,keep);handoff=M.read(row)
    I.__file__=M.__file__;I.base.__file__=M.__file__;I.guard_seconds=lambda *a:3300
    worker=I.selected(p['descriptor'])[0];worker.run.mkdir();initial=keep(worker.run/'initial.json',{})
    step=worker.run/'steps/enqueue';step.mkdir(parents=True);W.step_directory=lambda *a:step;W.code=lambda w:{'original':'code'}
    W.refreshed=lambda selection:selection
    wi=keep(step/'intent.json',dict(code=W.code(worker),initial=initial,lease=asdict(new),
        previous=handoff['checkpoint'],selection=handoff['selection'],step=handoff['next_step']))
    exception=keep(step/'exception.json',dict(type='AdmissionError',automatic_replay=False,message_sha256='exact-failure'))
    command=step/'command';command.mkdir();keep(command/'intent.json',{'original':'command'});keep(command/'complete.json',{'returncode':1})
    directory=tmp_path/'first-step';directory.mkdir();(directory/'step.log').write_text('original failed command')
    intent=keep(directory/'intent.json',dict(action='step',arguments=dict(descriptor=handoff['descriptor'],checkpoint=handoff['result']),
        code=M.binding(I.__file__),base_code=M.binding(I.base.__file__),timeout_seconds=3300,memory_limit_bytes=8589934592))
    resource=keep(directory/'resource.json',dict(returncode=1,failure=None,intent=intent,log=M.binding(directory/'step.log')))
    outcome=keep(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource,
        automatic_retry=False,source_invalidity_claimed=False,terminal_verdict=None))
    failure=keep(tmp_path/'failed.json',dict(schema_version='fleet-failed-checkpoint-successor/v1',handoff=row,outcome=outcome,
        directory=str(step),tree={str(x.relative_to(step)):M.binding(x)['sha256']for x in step.rglob('*')if x.is_file()},worker_intent=wi,exception=exception))
    return profile,I,W,T,successful,keep,p,new,live,row,failure,worker


def test_failed_successor_keeps_actual_failure_and_current_live_custody(tmp_path):
    profile,I,W,T,successful,keep,p,new,live,row,failure,worker=failed_successor_fixture(tmp_path)
    before=I._prior;h=M.read(row);old_bytes=__import__('pathlib').Path(M.read(failure)['outcome']['path']).read_bytes()
    # The selected old module remains the validator of its own immutable profile.
    original=SimpleNamespace(__file__=M.__file__,validate=M.validate)
    with pytest.raises(RuntimeError,match='downstream'):
        with M.prior_scope(row,profile,I,W,T,successful,failed_successor=failure,original_reader=original):
            value,s,lease=I._prior(h['result'],h['descriptor']);assert lease==new
            assert value['checkpoint']==h['checkpoint']and live[-1]==new
            assert I._prior(p['result'],p['descriptor'])[2]!=new
            raise RuntimeError('downstream')
    assert I._prior is before
    assert __import__('pathlib').Path(M.read(failure)['outcome']['path']).read_bytes()==old_bytes


@pytest.mark.parametrize('reject_current',[False,True])
def test_failed_successor_refreshes_only_live_stage_check(tmp_path,reject_current):
    profile,I,W,T,successful,keep,p,new,live,row,failure,worker=failed_successor_fixture(tmp_path)
    original=SimpleNamespace(__file__=M.__file__,validate=M.validate)
    historical=I._prior(p['result'],p['descriptor'])[1];current=object();seen=[]
    def refresh(selection):
        assert selection is historical
        return current
    def active(w,s,lease):
        assert w is worker and s is current and lease==new
        seen.append(True)
        if reject_current:raise ValueError('current source or owner rejected')
    W.refreshed=refresh;W.active=active;h=M.read(row);before=I._prior
    with M.prior_scope(row,profile,I,W,T,successful,failed_successor=failure,original_reader=original):
        if reject_current:
            with pytest.raises(ValueError,match='current source or owner rejected'):
                I._prior(h['result'],h['descriptor'])
        else:
            value,selection,lease=I._prior(h['result'],h['descriptor'])
            assert selection is historical and value==M.read(h['result']) and lease==new
    assert seen==[True]and I._prior is before


@pytest.mark.parametrize('fault',['tracker','result','completed','uncertain','checkpoint','code','base','budget','tree','initial','lease','exception','foreign_reader'])
def test_failed_successor_rejects_effects_or_changed_custody(tmp_path,fault):
    from pathlib import Path
    profile,I,W,T,successful,keep,p,new,live,row,failure,worker=failed_successor_fixture(tmp_path)
    f=M.read(failure);out=M.read(f['outcome']);g=M.read(out['resource']);i=M.read(g['intent']);step=Path(f['directory'])
    original=SimpleNamespace(__file__=M.__file__,validate=M.validate)
    if fault=='tracker':keep(Path(p['tracker']['path']),{'actual':'changed'})
    elif fault=='result':keep(Path(f['outcome']['path']).parent/'result.json',{'unexpected':True})
    elif fault=='completed':keep(step/'complete.json',{'completed':True})
    elif fault=='uncertain':g['returncode']=None;g['failure']={'type':'TimeoutError'}
    elif fault=='checkpoint':i['arguments']['checkpoint']=p['result']
    elif fault=='code':i['code']=p['source']
    elif fault=='base':i['base_code']=p['source']
    elif fault=='budget':i['timeout_seconds']=4000
    elif fault=='tree':keep(step/'command/another.json',{'new':True})
    elif fault=='initial':keep(worker.run/'initial.json',{'foreign':True})
    elif fault=='lease':
        wi=M.read(f['worker_intent']);wi['lease']['revision']+=1;f['worker_intent']=keep(step/'intent.json',wi)
    elif fault=='exception':f['exception']=keep(step/'exception.json',dict(type='TimeoutError',automatic_replay=False))
    else:original.__file__=str(tmp_path/'source.json')
    if fault in {'uncertain','checkpoint','code','base','budget'}:
        g['intent']=keep(Path(g['intent']['path']),i);out['resource']=keep(Path(out['resource']['path']),g);f['outcome']=keep(Path(f['outcome']['path']),out)
    if fault in {'lease','exception'}:f['tree']={str(x.relative_to(step)):M.binding(x)['sha256']for x in step.rglob('*')if x.is_file()}
    failure=keep(Path(failure['path']),f);h=M.read(row);before=I._prior
    with pytest.raises(ValueError):
        with M.prior_scope(row,profile,I,W,T,successful,failed_successor=failure,original_reader=original):
            I._prior(h['result'],h['descriptor'])
    assert I._prior is before
