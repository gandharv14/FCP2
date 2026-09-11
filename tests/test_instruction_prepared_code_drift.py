"""Prepared instruction requests must fail closed across a live code change.

No source files or network are touched: current code observations are replaced
only inside the test so parallel source edits are unnecessary for reproduction.
"""
from dataclasses import replace
import copy
import pytest
from scripts.batch import harbor_instruction_producer as P, harbor_model_executor as E
from scripts.batch.harbor_admission import AdmissionError
from test_harbor_instruction_producer import setup, dialogue_fixture, ROOT
from test_dialogue_slot_producer import values

@pytest.fixture
def paraphraser(setup):
    task,campaign,respond,calls=dialogue_fixture(setup)
    writer=P.prepare_dialogue_writer(task,root=ROOT)
    respond(writer,values(task));P.consume_dialogue_writer(task,writer,campaign,root=ROOT)
    request=P.prepare_dialogue_paraphraser(task,root=ROOT)
    return task,request,campaign,respond,calls

def test_current_paraphraser_code_validates_without_historical_exception(paraphraser):
    task,request,*_=paraphraser
    context=P.validate_request(task,request,'dialogue-paraphraser')
    assert context['code']==P.code_evidence(task)

@pytest.mark.parametrize('kind',['dialogue_skill','executor','producer','missing_record'])
def test_prepared_paraphraser_current_code_drift_fails_before_dispatch(paraphraser,monkeypatch,kind):
    task,request,campaign,respond,calls=paraphraser
    context_bytes=P.Path(request.context['path']).read_bytes()
    request_bytes=P.Path(request.prepared.request['path']).read_bytes()
    current=copy.deepcopy(P.code_evidence(task))
    if kind=='missing_record':current.pop()
    else:
        suffix={'dialogue_skill':'additional-assumptions-dialogue/SKILL.md','executor':'harbor_model_executor.py','producer':'harbor_instruction_producer.py'}[kind]
        rows=[row for row in current if row['path'].endswith(suffix)]
        assert len(rows)==1
        rows[0]['sha256']='0'*64
    monkeypatch.setattr(P,'code_evidence',lambda _:current)
    before=len(calls)
    with pytest.raises(AdmissionError,match='remaining structured dialogue writer only'):
        P.dispatch(task,request,campaign,credential='synthetic-token')
    assert len(calls)==before
    assert P.Path(request.context['path']).read_bytes()==context_bytes
    assert P.Path(request.prepared.request['path']).read_bytes()==request_bytes
    assert not E.location(request.prepared,campaign).exists()

def test_context_code_alias_or_omission_is_not_a_current_profile(paraphraser,tmp_path):
    task,request,*_=paraphraser
    context=P.read(request.context)
    context['code']=context['code'][:-1]
    changed=P.put(tmp_path/'altered-context.json',context)
    with pytest.raises(AdmissionError,match='remaining structured dialogue writer only'):
        P.validate_request(task,replace(request,context=changed),'dialogue-paraphraser')
