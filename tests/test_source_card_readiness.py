"""Real producer/Call fixtures plus the original independent readiness gate."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_producer as P, harbor_semantic_readiness as S
from scripts.fleet import source_card_readiness as adapter, source_card_authoring as core
from scripts.batch.harbor_admission import record
from test_source_card_producer import fixture, setup, values, review_response, ROOT
from test_source_card_worker import prepared, case, instruction_setup


@pytest.mark.parametrize('passed', [False, True])
def test_actual_independent_readiness_consumes_author2_and_review1(setup, monkeypatch, passed):
    task, campaign, producer, respond, sent, directory, old_calls = fixture(setup, monkeypatch)
    writer = producer.prepare_dialogue_writer(task); respond(writer, values(task))
    producer.consume_dialogue_writer(task, writer, campaign)
    para = producer.prepare_dialogue_paraphraser(task); respond(para, values(task))
    producer.consume_dialogue_paraphraser(task, para, writer, campaign)
    review = producer.prepare_dialogue_review(task); respond(review, review_response(task, passed))
    reader = producer._source_cards
    reader.authority = task.dialogue_pre_review_repair
    reader.review_pair = lambda bound, calls, **kw: reader.verify_pair(task, kw['draft'], calls=calls)
    reader.review_projection = lambda bound, claims, draft: core.review_body(claims, draft, bound, reader.budget())['source_card_authoring']
    original = dict(S.__dict__)
    selected = adapter.semantic(S, reader)
    assert S.__dict__ == original
    draft = P.text(record(directory/'round-02/draft.md'))
    validator = P.skill_module(ROOT, 'additional-assumptions-dialogue', 'validate_dialogue')
    notes = P.put(directory/'fixture-current-notes.md', validator.strip_claim_comments(draft).encode())
    identity = P.task_context(task)[1]
    options = dict(claims=record(directory/'claims.json'), notes=notes,
        reviewed_claims=record(directory/'claims.json'), policy=task.policy,
        writer_calls=tuple(P.observed(r, campaign) for r in (writer, para)),
        workbook=identity['workbook'], current_source_sha256=identity['original']['sha256'],
        root=ROOT, original_sha256=identity['original']['sha256'], pre_review_repair=reader.authority)
    call = P.observed(review, campaign)
    if passed:
        result = selected.dialogue_review(call, **options)
        assert result['round'] == 1 and result['claims'] == len(P.read(options['claims'])['claims'])
    else:
        with pytest.raises(ValueError, match='semantic coverage failed'):
            selected.dialogue_review(call, **options)
    with pytest.raises(ValueError, match='unselected source-card'):
        selected.dialogue_review(call, **{**options, 'pre_review_repair': {'path': 'foreign', 'sha256': '0'*64}})
    # This is a declared successor, not a retrospective ordinary-review pass.
    with pytest.raises(ValueError): S.dialogue_review(call, **options)
    assert len(sent) == 5  # Two original authors, two corrected authors, one review.


def test_campaign_accepts_only_exact_selected_source_authority(case, monkeypatch):
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_worker as W
    worker, selection, lease, old, authority, _, reader, validated = prepared(case, monkeypatch)
    # The shared owning fixture uses 0245; this isolated reader fixture is
    # deliberately substituted at the closed 0200 path, never on a real task.
    (worker.context.tracker_root/'0200.json').write_bytes((worker.context.tracker_root/'0245.json').read_bytes())
    reader.authority = authority
    descriptor = W.keep(worker.run/'fixture-source-descriptor.json', dict(worker=C.worker_encode(worker)))
    checkpoint = W.keep(worker.run/'fixture-source-checkpoint.json', C._result('RETRY_READY', descriptor,
        selection, lease, checkpoint=authority, next_step='dialogue-writer'))
    original = dict(C.__dict__)
    selected = adapter.campaign(C, reader, W)
    assert C.__dict__ == original
    result, s, current = selected._prior(checkpoint, descriptor)
    assert current == lease and s == selection and validated
    with pytest.raises((ValueError, KeyError)): C._prior(checkpoint, descriptor)
    reader.authority = {**authority, 'sha256': '0'*64}
    with pytest.raises(ValueError, match='detached'):
        selected._prior(checkpoint, descriptor)


def test_other_readiness_functions_are_original_bytecode():
    from scripts.batch import harbor_readiness as R
    for module, factory, changed in ((R, adapter.static, 'prose_content'), (S, adapter.semantic, 'dialogue_review')):
        selected = factory(module, SimpleNamespace())
        for name, value in module.__dict__.items():
            if callable(value) and hasattr(value, '__code__') and name not in ({changed,'context_bindings'} if factory is adapter.static else {changed}):
                assert getattr(selected, name).__code__ is value.__code__


def test_renewal_boundary_cannot_invent_source_paraphraser_completion(case, monkeypatch):
    import sys
    import scripts.batch
    from scripts.batch import harbor_instruction_worker as W, harbor_instruction_lease_renewal as R
    from scripts.fleet import source_card_worker
    worker, selection, lease, _, authority, _, reader, _ = prepared(case, monkeypatch)
    reader.authority = authority
    monkeypatch.setattr(W, '_operation', lambda worker, selected, lease, phase, directory, **kw: (selected, lease, None))
    selected = source_card_worker.install(W, reader, W.producer)
    original = dict(R.__dict__)
    renewal = adapter.renewal(R, reader)
    assert R.__dict__ == original
    for name in ('_validate', 'recover', '_renewal_chain'):
        assert getattr(renewal, name).__code__ is getattr(R, name).__code__
    done = selected.step(worker, selection, lease, checkpoint=authority)
    assert done.next_step == 'dialogue-paraphraser'
    monkeypatch.setitem(sys.modules, 'scripts.batch.harbor_instruction_worker', selected)
    monkeypatch.setattr(scripts.batch, 'harbor_instruction_worker', selected)
    phase, records = renewal._boundary(worker, done.selection, done.lease, done.evidence)
    assert phase == 'dialogue-paraphraser' and done.evidence in records
    assert not selected.step_directory(worker, phase).exists()
    # The original one-sentence reader cannot consume this separate authority.
    with pytest.raises((KeyError, ValueError)): R._boundary(worker, done.selection, done.lease, done.evidence)

def test_source_scope_keeps_original_discovery_and_reproves_corrected_claims(tmp_path,monkeypatch):
 from dataclasses import dataclass
 from scripts.batch.harbor_admission import read
 from scripts.batch import harbor_transition_codec as codec
 @dataclass
 class Content:
  dialogue_claims:dict
  dialogue_scope_receipt:dict
  scope_evidence:dict
  bindings:dict
  pre_dialogue_witness:object
 def save(name,value):
  import json
  path=tmp_path/name;path.write_text(json.dumps(value));return record(path)
 old=save('old.json',{'claims':['original']});new=save('new.json',{'claims':['corrected']});source=save('source.json',{})
 scope=save('scope.json',{});evidence=save('evidence.json',{});authority=save('permit.json',{});proof=save('proof.json',{})
 selected=Content(new,scope,evidence,{'source_file':source},SimpleNamespace(stage=tmp_path))
 prior=Content(old,scope,evidence,selected.bindings,selected.pre_dialogue_witness)
 monkeypatch.setattr(codec,'decode',lambda value:SimpleNamespace(content=prior))
 p={'old_selection':{},'source':source,'original_claims':old,'claims':new,'source_proof':proof}
 seen=[];owned=[]
 def original(value,**kw):
  seen.append(value);assert value.dialogue_claims==old and value.dialogue_scope_receipt==scope
  return [scope,evidence]
 def permit():owned.append(True);return p
 reader=SimpleNamespace(plan={'corrected_claims':new},authority=authority,owned=permit,extractor=lambda:SimpleNamespace(extract=lambda stage:read(new)))
 result=adapter.source_scope(selected,original,reader,root=tmp_path)
 assert seen==[prior] and owned and result==[scope,evidence,authority,proof,old,new]
 reader.extractor=lambda:SimpleNamespace(extract=lambda stage:{'claims':['foreign']})
 with pytest.raises(ValueError,match='does not reproduce'):adapter.source_scope(selected,original,reader,root=tmp_path)
 # A failed original discovery cannot be relabeled by corrected extraction.
 def reject(*a,**k):raise ValueError('original scope failed')
 with pytest.raises(ValueError,match='original scope failed'):adapter.source_scope(selected,reject,reader,root=tmp_path)
 selected.dialogue_scope_receipt={}
 with pytest.raises(ValueError,match='authority differs'):adapter.source_scope(selected,original,reader,root=tmp_path)
