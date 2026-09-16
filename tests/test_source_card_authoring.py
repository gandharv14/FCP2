import copy
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.fleet import source_card_authoring as repair
from scripts.batch import harbor_instruction_producer as p, harbor_dialogue_authoring as author
from scripts.batch.harbor_admission import record
from test_harbor_instruction_producer import setup, dialogue_fixture
from test_dialogue_slot_producer import values

ROOT = Path(__file__).resolve().parents[1]
H = ROOT / 'diagnostics/vm-fleet-20260912/control/0200-spoken-role-001'
BUDGET = dict(author_generation=2, author_limit=2, prior_writer_generations=1,
              prior_paraphraser_generations=1, review_ordinal=1, review_limit=2,
              prior_review_attempts=0, semantic_budget_reset=False)


def module(path):
    spec = importlib.util.spec_from_file_location('renderer_' + str(abs(hash(str(path)))), path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def projection():
    original = json.loads((H/'retained-claims.json').read_text())
    lineage = json.loads((H/'lineage-frozen-002.json').read_text())
    old = module(lineage['original']['path'])
    new = module(H/'spoken_formula.py')
    formulas = {c['reviewer_only']['cells'][0]: c['reviewer_only']['evidence'] for c in original['claims']}
    return original, old, new, formulas


def test_exact_actual_cards_preserve_source_and_every_genuine_clause(projection):
    original, old, new, formulas = projection
    fixed, changes = repair.corrected_claims(original, old, new, formulas)
    restored = copy.deepcopy(fixed)
    for group in ('claims',):
        for index, c in enumerate(original[group]):
            restored[group][index]['spoken'] = c['spoken']
            restored['writer_pack']['claims'][index]['spoken'] = c['spoken']
            restored[group][index]['must_say'] = c['must_say']
            restored['writer_pack']['claims'][index]['must_say'] = c['must_say']
    restored['disclosure_body'] = original['disclosure_body']
    restored['writer_pack']['disclosure_body'] = original['writer_pack']['disclosure_body']
    assert restored == original
    assert len(changes) == 25
    assert len(fixed['claims']) == 43
    for a, b in zip(original['claims'], fixed['claims']):
        assert a['reviewer_only'] == b['reviewer_only']
        removed = set(a['must_say']) - set(b['must_say'])
        assert not removed or removed == {'result locked input'}


@pytest.mark.parametrize('change', ['formula', 'pack', 'card', 'duplicate', 'missing', 'renderer'])
def test_source_projection_rejects_unproved_or_broader_change(projection, change):
    original, old, new, formulas = projection
    if change == 'formula': formulas['Model!F102'] = '=0'
    if change == 'pack': original['writer_pack']['claims'][0]['spoken'] += 'changed'
    if change == 'card':
        original['claims'][22]['spoken'] += 'changed'
        original['writer_pack']['claims'][22]['spoken'] += 'changed'
    if change == 'duplicate': original['claims'][0]['record_id'] = original['claims'][1]['record_id']
    if change == 'missing': original['claims'].pop()
    if change == 'renderer': new = old
    with pytest.raises(ValueError): repair.corrected_claims(original, old, new, formulas)


@pytest.mark.parametrize('key,value', [('author_generation', 1), ('author_generation', 3),
    ('author_limit', 3), ('prior_writer_generations', 0), ('prior_paraphraser_generations', 2),
    ('review_ordinal', 2), ('review_limit', 3), ('prior_review_attempts', 1),
    ('semantic_budget_reset', True), ('prior_review_attempts', False)])
def test_separate_original_budgets_cannot_reset_or_skip_review(key, value):
    budget = {**BUDGET, key: value}
    with pytest.raises(ValueError): repair.budget_contract(budget)


def completed_fixture(setup):
    # This is protocol integration against the real local executor with its
    # transport replaced by the existing explicit synthetic response fixture.
    # Source qualification is separately tested against the actual 43 cards.
    task, campaign, respond, calls = dialogue_fixture(setup)
    first = p.prepare_dialogue_writer(task)
    respond(first, values(task)); p.consume_dialogue_writer(task, first, campaign)
    first_para = p.prepare_dialogue_paraphraser(task)
    respond(first_para, values(task))
    previous_result = p.consume_dialogue_paraphraser(task, first_para, first, campaign)
    original_calls = tuple(p.observed(r, campaign) for r in (first, first_para))
    directory, claims, template, slots = p._dialogue(task, 1, ROOT)
    old_draft = p.text(previous_result['draft'])
    identity = p.task_context(task)[1]
    prior = repair.original_pair(author, original_calls, policy=task.policy, claims=claims,
        draft=old_draft, root=ROOT, workbook=identity['workbook'],
        source_sha256=identity['original']['sha256'])
    cert = p.put(task.run/'fixture-source-certificate.json',
                 dict(schema_version=repair.SCHEMA, fixture_only=True, budget=BUDGET))
    changes = [dict(record_id='fixture-' + n) for n in repair.IDS]
    feedback = repair.feedback(cert, old_draft, changes, BUDGET)
    body = author.author_body(template, slots, claims['writer_pack'], 'writer', previous=feedback)
    writer = p._request(task, 'dialogue-writer', p.DIALOGUE, 2, 2, body,
                        author.prompt(ROOT, 'writer'), [cert], mode='json_object')
    respond(writer, values(task))
    writer_call = p.observed(writer, campaign)
    _, filled, faults, _ = author.verify_author(writer_call, policy=task.policy,
        kind='writer', round=2, template=template, slots=slots,
        pack=claims['writer_pack'], previous=feedback, root=ROOT)
    assert not faults
    body = author.author_body(template, slots, claims['writer_pack'], 'paraphraser', incoming=filled)
    para = p._request(task, 'dialogue-paraphraser', p.DIALOGUE, 2, 2, body,
                      author.prompt(ROOT, 'paraphraser'), [cert], mode='json_object')
    respond(para, values(task))
    pair = (writer_call, p.observed(para, campaign))
    draft = author.skill_module(ROOT, 'additional-assumptions-dialogue', 'compose_draft').strip_slot_scaffold(filled)
    args = dict(policy=task.policy, claims=claims, draft=draft, previous=feedback, authority=cert,
                prior_checked=prior, root=ROOT, workbook=identity['workbook'],
                source_sha256=identity['original']['sha256'])
    return task, campaign, respond, calls, pair, args


def test_actual_executor_generation_two_gets_unused_review_one_without_fake_review(setup):
    task, campaign, respond, calls, pair, args = completed_fixture(setup)
    checked = repair.generated_pair(author, pair, **args)
    assert len(checked) == 2 and len(calls) == 4
    assert not (task.run/'dialogue/review-call-01.json').exists()
    writer_body = json.loads(p.read(pair[0].request)['messages'][1]['content'])
    assert 'previous_review' not in writer_body
    assert writer_body['source_card_feedback']['budget']['prior_review_attempts'] == 0
    body = repair.review_body(args['claims'], args['draft'], args['authority'], BUDGET)
    prompt = p.skill_prompt(ROOT, 'additional-assumptions-dialogue', 'You are reviewing notes from a deal-team conversation')
    request = p._request(task, 'dialogue-review', p.DIALOGUE, 1, 2, body, prompt, [args['authority']])
    respond(request, dict(round=1, passed=False))
    result = repair.review_identity(p.observed(request, campaign), author=author,
        claims=args['claims'], draft=args['draft'], authority=args['authority'], budget=BUDGET,
        policy=task.policy, authors_checked=checked, workbook=args['workbook'],
        source_sha256=args['source_sha256'], prompt=prompt)
    assert result['review']['passed'] is False  # transport identity is not qualification
    assert request.prepared.ordinal == 1 and request.prepared.limit == 2
    assert len(calls) == 5
    # Existing historical reader stays strict; only the explicit new contract
    # may interpret these generation-two authors without a prior review.
    with pytest.raises(ValueError, match='prior semantic'):
        author.verify_pair(pair, policy=task.policy, claims=args['claims'], draft=args['draft'],
            round=2, root=ROOT, workbook=args['workbook'], original_sha256=args['source_sha256'])


@pytest.mark.parametrize('change', ['draft', 'workbook', 'source', 'prior', 'feedback'])
def test_generation_pair_rejects_detached_context(setup, change):
    _, _, _, _, pair, args = completed_fixture(setup)
    if change == 'draft': args['draft'] += 'changed'
    if change == 'workbook': args['workbook'] = '9999'
    if change == 'source': args['source_sha256'] = '0' * 64
    if change == 'prior': args['prior_checked'] = []
    if change == 'feedback': args['previous']['source_card_feedback']['budget']['review_ordinal'] = 2
    with pytest.raises(ValueError): repair.generated_pair(author, pair, **args)
