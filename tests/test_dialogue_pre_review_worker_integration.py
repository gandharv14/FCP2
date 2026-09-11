"""Worker routing/serialization tests; authority semantics have their own suite."""
from dataclasses import replace,asdict
from pathlib import Path
from types import ModuleType
import sys
import pytest
from scripts.batch import harbor_instruction_worker as W,harbor_instruction_lease_renewal as L,harbor_transition_codec as codec
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_complete_readiness import SemanticSelection
from test_instruction_lease_renewal import boundary,case,instruction_setup

@pytest.fixture
def repaired(boundary,monkeypatch):
    worker,selection,lease,checkpoint,snapshot,tracker=boundary
    for name in W.STEPS[1:11]:
        result=W.step(worker,selection,lease,checkpoint=checkpoint)
        checkpoint=result.evidence
    step=W.step_directory(worker,'dialogue-paraphraser')
    intent=W.keep(step/'intent.json',dict(initial=record(worker.run/'initial.json'),previous=checkpoint))
    blocked=W.keep(step/'blocked.json',dict(intent=intent,status='DIALOGUE_REPAIR_REQUIRED',selection=codec.encode(selection),lease=asdict(lease)))
    permit=W.keep(worker.run/'pre-review/permit.json',dict(schema_version='harbor-dialogue-pre-review-repair/v1',
        phase='dialogue-review',predecessor=blocked,selection=codec.encode(selection),lease=asdict(lease)))
    module=ModuleType('scripts.batch.harbor_dialogue_pre_review_repair')
    module.validate=lambda worker,before:read(permit)
    monkeypatch.setitem(sys.modules,module.__name__,module)
    return replace(worker,dialogue_pre_review_repair=permit),selection,lease,checkpoint,permit,blocked,tracker


def test_repair_receipt_routes_only_unreviewed_draft_and_preserves_failure(repaired):
    worker,selection,lease,old,permit,blocked,tracker=repaired
    saved=Path(blocked['path']).read_bytes()
    with pytest.raises(ValueError,match='preceding checkpoint'):
        W.step(worker,selection,lease,checkpoint=old)
    result=W.step(worker,selection,lease,checkpoint=permit)
    assert read(result.evidence)['step']=='dialogue-review'
    assert Path(result.evidence['path']).is_relative_to(Path(permit['path']).parent)
    assert read(read(result.evidence)['intent'])['previous']==permit
    assert Path(blocked['path']).read_bytes()==saved
    assert not (W.step_directory(worker,'dialogue-paraphraser')/'complete.json').exists()
    phase,history=L._boundary(worker,selection,lease,result.evidence)
    assert phase=='final-runtime' and permit in history


def test_worker_codec_preserves_exact_repair_and_rejects_extra_fields(repaired):
    from scripts import harbor_instruction_campaign as C
    worker,*_=repaired
    encoded=C.worker_encode(worker)
    assert C.worker_decode(encoded)==worker
    with pytest.raises(ValueError):C.worker_decode({**encoded,'invented_repair':encoded['dialogue_pre_review_repair']})
    with pytest.raises(ValueError):C.worker_decode({**encoded,'dialogue_pre_review_repair':{'path':'a'}})


def test_semantic_codec_preserves_legacy_shape_and_new_evidence():
    value=SemanticSelection(None,{}, {},None,{},())
    encoded=codec.encode(value)
    assert 'dialogue_pre_review_repair' not in encoded['value']
    assert codec.encode(codec.decode(encoded))==encoded
    older={**encoded,'value':{k:v for k,v in encoded['value'].items() if k!='disclosure_revision'}}
    assert codec.decode(older)==value
    proof={'path':'/proof.json','sha256':'a'*64}
    revised=replace(value,dialogue_pre_review_repair=proof)
    assert codec.decode(codec.encode(revised))==revised
    with pytest.raises(ValueError):codec.decode({**encoded,'value':{**encoded['value'],'unknown':{'type':'scalar','value':None}}})


def test_historical_inventory_still_uses_exact_retained_adapter(monkeypatch):
    worker=type('Worker',(),{'lease_renewal':{'path':'/old','sha256':'a'*64}})()
    monkeypatch.setattr(L,'_retained_lease_adapter',lambda w,b:{'validated':'earlier-root'})
    assert L._validate(worker,{},set(),historical_code_inventory={'bound-old-code':'x'})=={'validated':'earlier-root'}


def test_real_second_round_never_skips_paraphraser(repaired,monkeypatch):
    from scripts.batch import harbor_dialogue_semantic_retry as D
    worker,selection,lease,old,pre_review,blocked,tracker=repaired
    proof=dict(initial=record(worker.run/'initial.json'),blocked=blocked,
        selection=codec.encode(selection),lease=asdict(lease))
    permit=W.keep(worker.run/'semantic-two/permit.json',proof)
    semantic=W.keep(worker.run/'semantic-two/complete.json',dict(schema_version=D.SCHEMA,permit=permit))
    worker=replace(worker,dialogue_semantic_retry=semantic)
    monkeypatch.setattr(D,'historical_pre_review',lambda *args:read(pre_review),raising=False)
    monkeypatch.setattr(D,'validate',lambda *args:proof)
    checkpoint=semantic
    for phase in ('dialogue-writer','dialogue-paraphraser','dialogue-review'):
        result=W.step(worker,selection,lease,checkpoint=checkpoint)
        assert read(result.evidence)['step']==phase
        assert Path(result.evidence['path'])==Path(pre_review['path']).parent/'steps'/phase/'semantic-round-02/complete.json'
        checkpoint=result.evidence
    assert not (worker.run/'steps/dialogue-paraphraser/complete.json').exists()
