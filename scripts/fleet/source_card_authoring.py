"""Prospective source-card correction primitives; no lease, dispatch or approval.

The owning continuation must validate its root permit before using these
primitives. A source correction consumes author generation two while retaining
the unused independent review ordinal one. It is not a failed-review retry.
"""
from copy import deepcopy
import json
import re

SCHEMA = 'harbor-source-card-authoring/v1'
AUTHOR_GENERATION = 2
REVIEW_ORDINAL = 1
LIMIT = 2
IDS = ('003', '004', '021', '022', '023', '024', '025', '026', '027', '028',
       '029', '030', '031', '032', '033', '034', '035', '036', '037', '038',
       '039', '040', '041', '042', '043')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def corrected_claims(original, old_renderer, new_renderer, formulas):
    """Derive the 25 source operand corrections, checking both renderers.

    The caller must separately bind the complete original source and renderer
    inventories. This pure projection never certifies a model-generated draft.
    """
    require(original.get('workbook') == '0200' and len(original['claims']) == 43,
            'closed original workbook and full claim inventory required')
    claims = original['claims']
    require(len({c['record_id'] for c in claims}) == len(claims), 'duplicate claim')
    keep = ('record_id', 'sheet', 'row_label', 'junior_scope', 'question',
            'must_say', 'alternatives', 'chosen_alternative', 'spoken')
    projected = [{k: c[k] for k in keep if k in c} for c in claims]
    require(projected == original['writer_pack']['claims'], 'writer pack detached from claims')
    corrected = deepcopy(original)
    body = original['disclosure_body']
    require(original['writer_pack']['disclosure_body'] == body, 'public disclosure copies differ')
    changed = []
    for index, old in enumerate(claims):
        if old['record_id'].rsplit('::', 1)[-1] not in IDS:
            continue
        source = old['reviewer_only']
        require(source['source'] == 'custom_method_detector' and old['sheet'] == 'Model',
                'correction requires the original formula detector')
        cell = source['cells'][0]
        require(formulas.get(cell) == source['evidence'], 'actual source formula changed')
        args = (source['steps'], cell, source['evidence'], old['sheet'],
                old['row_label'], 'only within the specified columns')
        before, after = old_renderer.speak_steps(*args), new_renderer.speak_steps(*args)
        require(before == old['spoken'], 'old card is not the exact original renderer output')
        formula = source['evidence']
        partial = re.fullmatch(r'=IF\(\$C([0-9]+)<F\$3,\$D\1,IF\(\$C\1<G\$3,\(G\$3-\$C\1\)/30\*\$D\1,0\)\)', formula)
        quotient = re.fullmatch(r'=IF\(AND\(\$C([0-9]+)<=F\$3,\$D\1>F\$3\),\$B\1/\$E\1,0\)', formula)
        require(bool(partial) != bool(quotient), 'unsupported source formula shape')
        require(cell == 'Model!F' + (partial or quotient).group(1), 'formula source row changed')
        wrong, right = (('minus the result locked input', 'minus the first locked input')
                        if partial else ('the result locked input', 'the third locked input'))
        require(before.count(wrong) == 1 and after == before.replace(wrong, right, 1),
                'renderer delta exceeds the source operand correction')
        if partial:
            require('multiply by the second locked input' in after and
                    'use the result locked input' in after, 'other operand roles changed')
        else:
            # The original extractor lists this role only because its renderer
            # mislabeled the quotient's numerator as the entire branch result.
            # Remove exactly that false atom; all other requirements survive.
            require(old['must_say'].count('result locked input') == 1,
                    'quotient must_say role is not the original false atom')
            atoms = [atom for atom in old['must_say'] if atom != 'result locked input']
            corrected['claims'][index]['must_say'] = atoms
            corrected['writer_pack']['claims'][index]['must_say'] = deepcopy(atoms)
        corrected['claims'][index]['spoken'] = after
        corrected['writer_pack']['claims'][index]['spoken'] = after
        require(body.count(before) == 1, 'ambiguous public disclosure card copy')
        body = body.replace(before, after, 1)
        changed.append(dict(record_id=old['record_id'], source_cell=cell,
                            source_formula=source['evidence'], before=before, after=after))
    require(tuple(c['record_id'].rsplit('::', 1)[-1] for c in changed) == IDS,
            'exactly the 25 source-proved cards must change')
    corrected['disclosure_body'] = body
    corrected['writer_pack']['disclosure_body'] = body
    return corrected, changed


def budget_contract(value):
    expected = dict(author_generation=2, author_limit=2, prior_writer_generations=1,
                    prior_paraphraser_generations=1, review_ordinal=1, review_limit=2,
                    prior_review_attempts=0, semantic_budget_reset=False)
    require(value == expected and all(type(value[k]) is int for k in expected
            if k != 'semantic_budget_reset') and value['semantic_budget_reset'] is False,
            'source-card author/review budget changed')
    return deepcopy(expected)


def feedback(authority, previous_draft, changes, budget):
    """Truthful writer context: source correction, never a fabricated review."""
    budget_contract(budget)
    require(isinstance(previous_draft, str) and previous_draft.strip(), 'old draft required')
    require(set(authority) == {'path', 'sha256'}, 'exact source authority binding required')
    require(len(changes) == len(IDS), 'complete source card changes required')
    # Reviewer-only source formulas stay in the sealed certificate, not in the
    # prose writer prompt. The new public cards contain the corrected meaning.
    return dict(previous_draft=previous_draft, source_card_feedback=dict(
        schema_version=SCHEMA, authority=authority, budget=deepcopy(budget),
        changed_claim_ids=[c['record_id'] for c in changes],
        reason='The prior card renderer assigned a whole-branch result role to '
               'a subtraction operand or quotient numerator. Rewrite under the corrected source cards. '
               'The previous draft failed mechanical checks; no independent '
               'dialogue review has been attempted.'))


def original_pair(author, calls, *, policy, claims, draft, root, workbook, source_sha256):
    """Revalidate actual generation-one calls without requiring they passed mechanics."""
    return author.verify_pair(tuple(calls), policy=policy, claims=claims, draft=draft,
                              round=1, root=root, workbook=workbook,
                              original_sha256=source_sha256)


def authority_evidence(authority, evidence):
    """Executor budget snapshots retain exact bytes at new immutable paths."""
    from pathlib import Path
    from scripts.batch.harbor_tracker_lifecycle import checked_record
    checked_record(authority)
    original = Path(authority['path']).read_bytes()
    matches = []
    for binding in evidence:
        checked_record(binding)
        if binding['sha256'] == authority['sha256']:
            require(Path(binding['path']).read_bytes() == original, 'authority copy changed')
            matches.append(binding)
    require(len(matches) == 1, 'exactly one source authority budget witness required')


def generated_pair(author, calls, *, policy, claims, draft, previous, authority,
                   prior_checked, root, workbook, source_sha256):
    """Validate two real generation-two Calls independently of review ordinal.

    The caller supplies prior_checked only after original_pair and the sealed
    source certificate validate. No semantic first-round review is invented.
    """
    from scripts.batch.harbor_admission import read
    from scripts.batch.harbor_model_review_provenance import require_independent
    require(len(calls) == len(prior_checked) == 2, 'both original and new authors required')
    require(previous['source_card_feedback']['authority'] == authority,
            'writer feedback detached from source authority')
    budget_contract(previous['source_card_feedback']['budget'])
    composer = author.skill_module(root, 'additional-assumptions-dialogue', 'compose_draft')
    template, slots = composer.compose(claims, claims['writer_pack'])
    for call, kind in zip(calls, ('writer', 'paraphraser')):
        submitted = read(call.submitted)
        expected = dict(workbook_id=workbook, original_sha256=source_sha256,
                        lane='harbor-dialogue-author', budget_id='dialogue-' + kind)
        require(submitted['scope'] == expected and
                type(submitted['semantic_ordinal']) is int and submitted['semantic_ordinal'] == 2 and
                type(submitted['semantic_limit']) is int and submitted['semantic_limit'] == 2,
                'remaining author original, role or generation changed')
        evidence = submitted['budget_evidence']
        authority_evidence(authority, evidence)
    writer, incoming, faults, old = author.verify_author(calls[0], policy=policy,
        kind='writer', round=2, template=template, slots=slots, pack=claims['writer_pack'],
        previous=previous, root=root)
    require(not faults, 'remaining writer structure invalid')
    para, filled, faults, new = author.verify_author(calls[1], policy=policy,
        kind='paraphraser', round=2, template=template, slots=slots,
        pack=claims['writer_pack'], incoming=incoming, root=root)
    require(not faults, 'remaining paraphraser structure invalid')
    require_independent(writer, prior_checked)
    require_independent(para, [*prior_checked, writer])
    for sid in slots['slot_order']:
        if slots['slots'][sid]['kind'] == 'junior':
            require(old['slot_prose_sha256'][sid] == new['slot_prose_sha256'][sid],
                    'remaining paraphraser changed junior prose')
    require(composer.strip_slot_scaffold(filled) == draft, 'review draft is not new author output')
    return [writer, para]


def review_body(claims, draft, authority, budget):
    budget_contract(budget)
    return {'claims.json': claims, 'draft.md': draft, 'round': 1,
            'source_card_authoring': dict(schema_version=SCHEMA, authority=authority,
                                         author_generation=2, review_ordinal=1)}


def review_identity(call, *, author, claims, draft, authority, budget, policy,
                    authors_checked, workbook, source_sha256, prompt):
    """Validate real review ordinal one and independent completed authors."""
    from scripts.batch.harbor_admission import read
    from scripts.batch.harbor_model_review_provenance import validate_call, require_independent
    budget_contract(budget)
    request, submitted = read(call.request), read(call.submitted)
    require(json.loads(request['messages'][1]['content']) == review_body(claims, draft, authority, budget)
            and request['messages'][0]['content'] == prompt, 'review context changed')
    require(submitted['scope'] == dict(workbook_id=workbook, original_sha256=source_sha256,
            lane='harbor-dialogue-author', budget_id='dialogue-review') and
            type(submitted['semantic_ordinal']) is int and submitted['semantic_ordinal'] == 1 and
            type(submitted['semantic_limit']) is int and submitted['semantic_limit'] == 2,
            'unused first review original or budget changed')
    authority_evidence(authority, submitted['budget_evidence'])
    require(len(authors_checked) == 2, 'review requires actual new author pair')
    checked = validate_call(call, request, policy=policy)
    require_independent(checked, authors_checked)
    require(type(checked['review'].get('round')) is int and checked['review']['round'] == 1,
            'review response ordinal changed')
    return checked
