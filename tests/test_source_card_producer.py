"""Real executor/owning producer fixtures; no live network or root authorization."""
from dataclasses import replace
import io
import json
from pathlib import Path
import time

import pytest

from scripts.batch import harbor_instruction_producer as P, harbor_dialogue_authoring as A
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import record
from scripts.fleet import source_card_authoring as core, source_card_producer as adapter
from test_harbor_instruction_producer import setup, dialogue_fixture
from test_dialogue_slot_producer import values
from test_source_card_authoring import BUDGET

ROOT = Path(__file__).resolve().parents[1]


def fixture(setup, monkeypatch):
    old_task, campaign, old_respond, sent = dialogue_fixture(setup)
    writer = P.prepare_dialogue_writer(old_task)
    old_respond(writer, values(old_task)); P.consume_dialogue_writer(old_task, writer, campaign)
    para = P.prepare_dialogue_paraphraser(old_task)
    old_respond(para, values(old_task))
    old_result = P.consume_dialogue_paraphraser(old_task, para, writer, campaign)
    old_calls = tuple(P.observed(r, campaign) for r in (writer, para))
    old_draft = P.text(old_result['draft'])
    original_directory, claims, template, slots = P._dialogue(old_task, 1, ROOT)
    directory = old_task.run/'fixture-source-correction'
    for name in ('claims.json', 'writer_pack.json', 'draft_template.md', 'slots.json'):
        P.put(directory/name, (original_directory/name).read_bytes())
    authority = P.put(directory/'permit.json', dict(schema_version=core.SCHEMA,
        fixture_only=True, budget=BUDGET, original_pair=[c.submitted for c in old_calls]))
    task = replace(old_task, dialogue_pre_review_repair=authority)
    identity = P.task_context(old_task)[1]

    class Reader:
        def is_card(self, value): return value.dialogue_pre_review_repair == authority
        def dependencies(self): return [record(adapter.__file__), record(core.__file__)]
        def author(self): return A
        def budget(self): return BUDGET
        def owns_directory(self, value): return Path(value) == directory
        def review_guidance(self): return '\nVerify the corrected source cards independently; author generation two is reviewed at ordinal one.'
        def validate_task(self, value):
            assert self.is_card(value) and P.read(authority)['fixture_only'] is True
            core.original_pair(A, old_calls, policy=task.policy, claims=claims, draft=old_draft,
                root=ROOT, workbook=identity['workbook'], source_sha256=identity['original']['sha256'])
            return authority
        def context(self, value, *, root):
            self.validate_task(value)
            return directory, claims, template, slots
        def feedback(self):
            return core.feedback(authority, old_draft,
                                 [dict(record_id=n) for n in core.IDS], BUDGET)
        def verify_pair(self, value, draft, calls=None):
            self.validate_task(value)
            if calls is None:
                from scripts.batch.harbor_model_review_provenance import Call
                calls = tuple(Call(**P.read(record(directory/(name+'-call-02.json'))))
                              for name in ('writer', 'paraphraser'))
            prior = core.original_pair(A, old_calls, policy=task.policy, claims=claims,
                draft=old_draft, root=ROOT, workbook=identity['workbook'],
                source_sha256=identity['original']['sha256'])
            return core.generated_pair(A, calls, policy=task.policy, claims=claims, draft=draft,
                previous=self.feedback(), authority=authority, prior_checked=prior, root=ROOT,
                workbook=identity['workbook'], source_sha256=identity['original']['sha256'])

    selected = adapter.install(P, Reader())

    def respond(request, value):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        raw = dict(model=task.policy.model, id='source-fixture-'+str(len(sent)),
                   object='chat.completion', created=time.time_ns()//10**9,
                   choices=[dict(finish_reason='stop', message=dict(role='assistant', content=json.dumps(value)))])
        def opened(*args):
            sent.append(args)
            return Response(json.dumps(raw).encode())
        monkeypatch.setattr(E, '_open', opened)
        return selected.dispatch(task, request, campaign, credential='synthetic-fixture-only')

    return task, campaign, selected, respond, sent, directory, old_calls


def review_response(task, passed):
    claims = P.read(record(task.run/'dialogue/claims.json'))
    return dict(agent_model='gpt-5.6-sol-high', round=1, passed=passed,
        accuracy=dict(verdict='pass', claims=[dict(record_id=c['record_id'], must_say='entailed', findings=[])
                     for c in claims['claims']], extras=[], cell_refs_in_senior_turns=[]),
        naturalness=dict(verdict='pass' if passed else 'fail', findings=[] if passed else ['Repeated phrase']))


@pytest.mark.parametrize('passed', [False, True])
def test_owning_prepare_consume_generation2_then_original_first_review(setup, monkeypatch, passed):
    task, campaign, selected, respond, sent, directory, old_calls = fixture(setup, monkeypatch)
    writer = selected.prepare_dialogue_writer(task)
    assert writer.prepared.ordinal == 2
    respond(writer, values(task))
    assert selected.consume_dialogue_writer(task, writer, campaign)['passed']
    para = selected.prepare_dialogue_paraphraser(task)
    assert para.prepared.ordinal == 2
    respond(para, values(task))
    result = selected.consume_dialogue_paraphraser(task, para, writer, campaign)
    assert result['passed'] and result['draft_report']['passed']
    assert (directory/'round-02/writer-accepted.json').exists()
    assert (directory/'round-02/paraphraser-accepted.json').exists()
    assert not (directory/'round-02/prior-round.json').exists()
    assert not (directory/'review-call-01.json').exists()
    review = selected.prepare_dialogue_review(task)
    assert review.prepared.ordinal == 1 and review.prepared.limit == 2
    respond(review, review_response(task, passed))
    result = selected.consume_dialogue_review(task, review, writer, para, campaign)
    assert result['passed'] is passed and result['round'] == 1 and result['author_generation'] == 2
    assert len(sent) == 5 and result['publication_ready'] is False
    for call in old_calls:
        for binding in (call.request, call.submitted, call.response, call.completed):
            assert record(binding['path']) == binding
    if not passed:
        with pytest.raises(ValueError, match='still fails'):
            selected.consume_dialogue_review(task, review, writer, para, campaign, apply=True)
    assert not (task.stage/'tests/dialogue-applied.json').exists()


def test_facade_keeps_ordinary_function_namespace_unchanged(setup, monkeypatch):
    before = P._request
    _, _, selected, _, _, _, _ = fixture(setup, monkeypatch)
    assert P._request is before
    assert selected._consume_author.__code__ is P._consume_author.__code__
    assert selected.validate_request.__code__ is P.validate_request.__code__
    assert selected.dispatch.__code__ is P.dispatch.__code__
    assert selected._request is not P._request


def test_source_pair_cannot_prepare_second_review_or_structural_budget(setup, monkeypatch):
    task, _, selected, _, _, _, _ = fixture(setup, monkeypatch)
    with pytest.raises(ValueError): selected.prepare_dialogue_review(task, round=2)
    with pytest.raises(ValueError):
        selected._request(task, 'dialogue-writer', P.DIALOGUE, 1, 1, {}, 'fixture', [], structural_round=2)
