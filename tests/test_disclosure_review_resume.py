from dataclasses import replace
from pathlib import Path
import json
import pytest
from scripts.batch import harbor_disclosure_review_resume as R
from scripts.batch import harbor_disclosure_payload_repair as P
from scripts.batch import harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as C
from test_harbor_model_executor import setup,Response,response_bytes
from test_disclosure_payload_repair import original,reject
from test_instruction_worker import case,instruction_setup


def test_completed_physical_review_retains_full_request_and_rejects_detached_call(setup,monkeypatch,tmp_path):
    c,p=original(setup);reject(p,c,monkeypatch);repaired=P.prepare(p,c,tmp_path/'physical')
    monkeypatch.setattr(R.E,'_open',lambda *a:Response(response_bytes(p.policy)))
    result=R.E.execute(repaired,c,credential='synthetic-token')
    assert R.original_review_request(result.call)==P.read(p.request)
    assert len(json.loads(R.original_review_request(result.call)['messages'][1]['content'])['disclosure']['method_assessments'])==3
    foreign=tmp_path/'foreign.json';foreign.write_bytes(R.E.canonical(P.read(result.call.request)))
    with pytest.raises(ValueError,match='completed physical repair'):
        R.original_review_request(replace(result.call,request=R.E.record(foreign)))


def test_worker_codec_closed_optional_history(case,tmp_path):
    worker,*_=case
    assert 'disclosure_repair' not in C.worker_encode(worker)
    receipt={'path':str(tmp_path/'permit.json'),'sha256':'a'*64}
    selected=replace(worker,disclosure_repair=receipt)
    assert C.worker_decode(C.worker_encode(selected))==selected
    with pytest.raises((ValueError,FileNotFoundError)):
        C.worker_encode(replace(selected,retry=receipt))
    value=C.worker_encode(selected);value['disclosure_repiar']=receipt
    with pytest.raises(ValueError,match='closed'):C.worker_decode(value)


def test_worker_fresh_namespace_keeps_original_block_and_requires_permit(case,monkeypatch,tmp_path):
    worker,s,lease,*_=case
    monkeypatch.setattr(W,'_operation',lambda *a,**k:(s,lease,None))
    build=W.step(worker,s,lease,phase='disclosure-build',checkpoint=None)
    monkeypatch.setattr(W,'_operation',lambda *a,**k:(s,lease,'MODEL_RECONCILIATION_REQUIRED'))
    blocked=W.step(worker,s,lease,phase='disclosure-review',checkpoint=build.evidence)
    oldbytes=Path(blocked.evidence['path']).read_bytes()
    permit=W.keep(tmp_path/'resume/permit.json',dict(blocked=blocked.evidence,selection=W.codec.encode(s),lease=C.asdict(lease)))
    selected=replace(worker,disclosure_repair=permit)
    monkeypatch.setattr(R,'validate',lambda *a:W.read(permit))
    with pytest.raises(ValueError,match='exact root-selected'):
        W.step(selected,s,lease,phase='disclosure-review',checkpoint=build.evidence)
    seen=[]
    def operation(w,s,l,phase,directory,**kw):seen.append(directory);return s,l,None
    monkeypatch.setattr(W,'_operation',operation)
    result=W.step(selected,s,lease,phase='disclosure-review',checkpoint=permit)
    assert result.next_step=='disclosure-commit'
    assert seen==[tmp_path/'resume/steps/disclosure-review']
    assert Path(blocked.evidence['path']).read_bytes()==oldbytes
    W.step(selected,s,lease,phase='disclosure-commit',checkpoint=result.evidence)
    assert seen[-1]==tmp_path/'resume/steps/disclosure-commit'


def test_context_rebind_preserves_original_budget_and_rejects_substitution(setup,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from scripts.batch import harbor_instruction_producer as producer
    c,p=original(setup);reject(p,c,monkeypatch);repaired=P.prepare(p,c,tmp_path/'physical')
    old={'kind':'disclosure-review','request':p.request,'budget_evidence':list(p.budget_evidence),
         'code':[],'evidence':[],'ordinal':1,'limit':1}
    oldbinding=W.keep(tmp_path/'requests/disclosure-review/attempt-01/context.json',old)
    current=[R.E.record(R.__file__)]
    monkeypatch.setattr(producer,'code_evidence',lambda t:current)
    value={**old,'request':repaired.request,'code':current,'original_context':oldbinding,'physical_repair':repaired.physical_repair}
    context=W.keep(tmp_path/'new-context.json',value)
    request=producer.Request(repaired,context)
    assert R.validate_request_context(SimpleNamespace(run=tmp_path),request)==value
    altered={**value,'limit':2};binding=W.keep(tmp_path/'altered-context.json',altered)
    with pytest.raises(ValueError,match='beyond explicit'):
        R.validate_request_context(SimpleNamespace(run=tmp_path),replace(request,context=binding))


def test_authorize_preserves_failed_history_and_refuses_second_namespace_after_dispatch(case,setup,monkeypatch,tmp_path):
    from scripts.batch import harbor_instruction_repair as history,harbor_lease_takeover as takeover
    from scripts.batch import harbor_instruction_producer as producer
    worker,s,lease,*_=case;c,p=original(setup);reject(p,c,monkeypatch)
    worker=replace(worker,campaign=c)
    monkeypatch.setattr(W,'_operation',lambda *a,**k:(s,lease,None))
    build=W.step(worker,s,lease,phase='disclosure-build',checkpoint=None)
    oldstep=worker.run/'steps/disclosure-review'
    context=W.keep(worker.run/'producer/requests/disclosure-review/attempt-01/context.json',
        {'stage_tree':W.static.tree(s.content.stage),'budget_evidence':list(p.budget_evidence)})
    W.request_saved(oldstep/'request.json',producer.Request(p,context))
    W.keep(oldstep/'manifest.json',{'fixture':'full manifest'})
    W.keep(oldstep/'instruction.snapshot.md',b'full original instruction')
    monkeypatch.setattr(W,'_operation',lambda *a,**k:(s,lease,'MODEL_RECONCILIATION_REQUIRED'))
    blocked=W.step(worker,s,lease,phase='disclosure-review',checkpoint=build.evidence)
    before={f.name:f.read_bytes() for f in oldstep.iterdir()}
    snapshot=W.keep(tmp_path/'old-code.json',{'root':str(worker.root),'files':[]})
    monkeypatch.setattr(R,'changes',lambda *a,**k:['reviewed fixture boundary'])
    monkeypatch.setattr(history,'prefix',lambda *a,**k:[])
    monkeypatch.setattr(takeover,'continuation_chain',lambda *a,**k:[])
    monkeypatch.setattr(producer,'validate_request',lambda *a:None)
    with pytest.raises(ValueError,match='selected financial content'):
        R.authorize(worker,replace(s,content=replace(s.content,stage_sha256='f'*64)),lease,blocked=blocked.evidence,old_snapshot=snapshot,directory=tmp_path/'wrong-content')
    assert not (tmp_path/'wrong-content').exists()
    permit=R.authorize(worker,s,lease,blocked=blocked.evidence,old_snapshot=snapshot,directory=tmp_path/'resume')
    assert W.read(permit)['semantic_budget_reset'] is False
    assert {f.name:f.read_bytes() for f in oldstep.iterdir()}==before
    with pytest.raises(ValueError,match='already authorized'):
        R.authorize(worker,s,lease,blocked=blocked.evidence,old_snapshot=snapshot,directory=tmp_path/'resume')
    selected=replace(worker,disclosure_repair=permit)
    assert R.validate(selected,W.active(worker,s,lease)[1])['original_context']==context
    repaired=W.request_saved(tmp_path/'resume/steps/disclosure-review/request.json')
    monkeypatch.setattr(R.E,'_open',lambda *a:Response(response_bytes(p.policy)))
    R.E.execute(repaired.prepared,c,credential='synthetic-token')
    with pytest.raises(ValueError,match='already authorized'):
        R.authorize(worker,s,lease,blocked=blocked.evidence,old_snapshot=snapshot,directory=tmp_path/'another')
    assert not (tmp_path/'another').exists()
    # Downstream NAT and dialogue continuations retain the actual completed
    # physical call and original rejection, not a replacement PASS assertion.
    from scripts.batch import harbor_nat_retry as nat,harbor_dialogue_semantic_retry as semantic
    before_tracker=W.active(selected,s,lease)[1]
    journal_before={f:f.read_bytes() for prep in (p,repaired.prepared)
                    for f in R.E.location(prep,c).iterdir() if f.is_file()}
    retained=nat._disclosure_history(selected,before_tracker)
    actual=R.E.inspect(repaired.prepared,c).call
    assert actual.response in retained and actual.submitted in retained
    assert R.E.record(R.E.location(p,c)/'outcome.json') in retained
    assert semantic._continuations(selected)['disclosure_repair']==permit
    assert nat._prior_continuations(selected)=={'disclosure_repair':permit}
    assert actual.response in semantic._history(selected,before_tracker)
    assert (repaired.prepared.attempt_id,repaired.prepared.ordinal,repaired.prepared.limit)==(p.attempt_id,1,1)
    assert all(path.read_bytes()==raw for path,raw in journal_before.items())
    historical,proof=R.validate_historical_integrity(selected,before_tracker)
    assert historical['physical_count']==1 and actual.response in proof
    from scripts.batch import harbor_instruction_repair as extraction
    code=W.code(selected)
    successor=W.keep(tmp_path/'parser/permit.json',{'current_code':code,
        'prior_continuations':{'disclosure_repair':permit}})
    repaired_worker=replace(selected,code_repair=successor)
    assert extraction.preserved_disclosure(repaired_worker,before_tracker,W.read(successor))==proof
    with pytest.raises(ValueError,match='history changed'):
        extraction.preserved_disclosure(repaired_worker,before_tracker,{'prior_continuations':{}})
    # A relocated successor may inspect the exact completed physical review;
    # its original UUID, response and rejected transport remain untouched.
    monkeypatch.setattr(W,'code',lambda worker:{'successor':'new code'})
    successor2=W.keep(tmp_path/'parser2/permit.json',{'current_code':W.code(selected),
        'prior_continuations':{'disclosure_repair':permit}})
    assert R.validate(replace(selected,code_repair=successor2),before_tracker)==historical
    assert all(path.read_bytes()==raw for path,raw in journal_before.items())



def test_later_retry_routes_preserve_disclosure_namespace_and_require_link(case,tmp_path):
    worker,*_=case
    disclosure=W.keep(tmp_path/'disclosure/permit.json',{'schema_version':R.SCHEMA})
    permit=W.keep(tmp_path/'nat/permit.json',{'prior_continuations':{'disclosure_repair':disclosure}})
    retry=W.keep(tmp_path/'nat/complete.json',{'permit':permit})
    selected=replace(worker,disclosure_repair=disclosure,retry=retry)
    assert C.worker_decode(C.worker_encode(selected))==selected
    assert W.step_directory(selected,'nat-writer')==tmp_path/'disclosure/steps/nat-writer/round-02'
    assert W.step_directory(selected,'disclosure-review')==tmp_path/'disclosure/steps/disclosure-review'
    semantic=replace(worker,disclosure_repair=disclosure,dialogue_semantic_retry=retry)
    assert W.step_directory(semantic,'dialogue-review')==tmp_path/'disclosure/steps/dialogue-review/semantic-round-02'
    bad=W.keep(tmp_path/'bad.json',{'permit':W.keep(tmp_path/'unlinked.json',{})})
    with pytest.raises(ValueError,match='detached'):
        C.worker_encode(replace(selected,retry=bad))


def test_historical_physical_context_requires_exact_completed_request(setup,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from scripts.batch import harbor_instruction_producer as producer
    c,p=original(setup);reject(p,c,monkeypatch);repaired=P.prepare(p,c,tmp_path/'physical')
    old={'kind':'disclosure-review','request':p.request,'budget_evidence':list(p.budget_evidence),
         'code':[],'evidence':[],'ordinal':1,'limit':1}
    root=tmp_path/'worker';run=root/'producer'
    original_context=W.keep(run/'requests/disclosure-review/attempt-01/context.json',old)
    oldcode=W.keep(tmp_path/'old-code.json',{'preserved':'bytes'})
    value={**old,'request':repaired.request,'code':[oldcode],
           'original_context':original_context,'physical_repair':repaired.physical_repair}
    context=W.keep(tmp_path/'physical-context.json',value)
    request=producer.Request(repaired,context)
    saved=W.request_saved(tmp_path/'physical-request.json',request)
    prior=W.keep(tmp_path/'disclosure.json',{'worker_run':str(root),'request':W.record(tmp_path/'physical-request.json')})
    current=[W.record(R.__file__)]
    monkeypatch.setattr(producer,'code_evidence',lambda task:current)
    monkeypatch.setattr(W,'code',lambda task:{'current':'implementation'})
    repair=W.keep(tmp_path/'repair.json',{'prior_continuations':{'disclosure_repair':prior},'current_code':{'current':'implementation'}})
    task=SimpleNamespace(run=run,root=tmp_path,code_repair=repair)
    with pytest.raises(ValueError,match='not complete|not been submitted'):
        R.validate_request_context(task,request)
    monkeypatch.setattr(R.E,'_open',lambda *a:Response(response_bytes(p.policy)))
    actual=R.E.execute(repaired,c,credential='synthetic-token')
    assert R.validate_request_context(task,request)==value
    assert (actual.call.attempt_id if hasattr(actual.call,'attempt_id') else repaired.attempt_id)==p.attempt_id
    assert repaired.ordinal==repaired.limit==1
    missing=W.keep(tmp_path/'missing-repair.json',{'current_code':{'current':'implementation'}})
    with pytest.raises(ValueError,match='lacks disclosure'):
        R.validate_request_context(SimpleNamespace(run=run,root=tmp_path,code_repair=missing),request)
