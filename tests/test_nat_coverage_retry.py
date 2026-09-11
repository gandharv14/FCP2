import json
from dataclasses import asdict,replace
from types import SimpleNamespace

import pytest

from test_harbor_instruction_producer import setup, nat_writer, ROOT
from scripts.batch import harbor_instruction_producer as p
from scripts.batch import harbor_instruction_worker as w
from scripts.batch import harbor_nat_coverage_retry as retry


@pytest.mark.parametrize('successful_round', [2,3,None])
def test_coverage_retries_are_bounded_and_keep_exact_feedback(setup,monkeypatch,successful_round):
    task,campaign,respond,calls=setup
    writer=nat_writer(setup); original=p.prepare_naturalization_review(task,root=ROOT)
    seen=[]
    def model(worker,current,request,models,credential):
        seen.append(request)
        payload=p.read(request.prepared.request)
        body=json.loads(payload['messages'][1]['content'])
        lines=p.editable_claims(ROOT,body['source'])
        checks=[dict(source_claim=line,candidate_claim=line,preserved=True) for line in lines]
        if len(seen)!=successful_round:checks=checks[:-1]
        if len(seen)>1:
            assert 'missing_source_lines' in payload['messages'][0]['content']
            assert 'Exact previous review' in payload['messages'][0]['content']
        respond(request,dict(valid=True,blocking_findings=[],clause_checks=checks))
        return None
    monkeypatch.setattr(w,'_model',model)
    worker=SimpleNamespace(root=ROOT,campaign=campaign)
    result,pending=retry.run(worker,task,original,writer,task.run/'worker-step',models=True,credential='fixture')
    assert pending is None
    assert len(seen)==(successful_round or 3)
    assert result['passed'] is (successful_round is not None)
    state=p.read(p.record(task.run/'naturalization/state.json'))
    assert len(state['attempts'])==1
    assert len(calls)==1+len(seen)
    assert state['status']==('applied' if successful_round else 'retry_ready')
    assert not (task.run/'naturalization/coverage-exception.json').exists()
    if successful_round is None:
        assert state['semantic_reviews'][-1]['accepted'] is False
        assert result['coverage_exhausted'] is True
    assert seen[1].prepared.ordinal==1 and seen[1].prepared.limit==2
    if len(seen)==3:assert seen[2].prepared.ordinal==2


def test_semantic_mismatch_does_not_enter_coverage_retry(setup,monkeypatch):
    task,campaign,respond,calls=setup;writer=nat_writer(setup)
    original=p.prepare_naturalization_review(task,root=ROOT)
    def model(worker,current,request,models,credential):
        respond(request,dict(valid=False,blocking_findings=['Meaning changed'],clause_checks=[]))
    monkeypatch.setattr(w,'_model',model)
    result,pending=retry.run(SimpleNamespace(root=ROOT,campaign=campaign),task,original,writer,
                             task.run/'worker-step',models=True,credential='fixture')
    assert not result['passed'] and pending is None and len(calls)==2
    assert not list(task.run.glob('coverage-review-feedback*'))


def test_retained_call_allows_journal_copy_and_json_tuple_roundtrip(setup):
    from scripts.batch import harbor_instruction_repair as repair
    task,campaign,respond,calls=setup;writer=nat_writer(setup)
    call=p.observed(writer,campaign)
    assert call.request['path']!=writer.prepared.request['path']
    receipt=p.put(task.run.parent/'repair/permit.json',dict(schema_version=repair.SCHEMA,
        phase='nat-review',worker_run=str(task.run.parent),original=p.read(writer.context)['original'],
        retained_requests=[dict(request=asdict(writer),call=asdict(call))]))
    repaired=replace(task,code_repair=receipt)
    assert repair.validate_retained_request(repaired,writer)['kind']=='naturalization-writer'
    changed=replace(writer,prepared=replace(writer.prepared,ordinal=2))
    with pytest.raises(ValueError,match='not a retained'):
        repair.validate_retained_request(repaired,changed)
    assert len(calls)==1


def test_coverage_cannot_hide_invented_candidate_evidence(setup,monkeypatch):
    task,campaign,respond,calls=setup;writer=nat_writer(setup)
    original=p.prepare_naturalization_review(task,root=ROOT)
    def model(worker,current,request,models,credential):
        body=json.loads(p.read(request.prepared.request)['messages'][1]['content'])
        lines=p.editable_claims(ROOT,body['source'])
        respond(request,dict(valid=True,blocking_findings=[],clause_checks=[
            dict(source_claim=line,candidate_claim='Invented evidence',preserved=True) for line in lines]))
    monkeypatch.setattr(w,'_model',model)
    result,pending=retry.run(SimpleNamespace(root=ROOT,campaign=campaign),task,original,writer,
                             task.run/'worker-step',models=True,credential='fixture')
    assert not result['passed'] and pending is None and len(calls)==2
    assert not list(task.run.glob('coverage-review-feedback*'))


def test_legacy_incomplete_coverage_receipt_cannot_authorize_acceptance(tmp_path):
    (tmp_path/'coverage-exception.json').write_text('{}')
    with pytest.raises(ValueError,match='cannot authorize'):
        retry.validate_exception(tmp_path,None,writer_call=None,writer_request=None,
                                 writer_bodies=None,policy=None,root=ROOT)
