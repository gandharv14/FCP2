"""Source-scoped deterministic criterion correction before unused review one.

This reader never represents the corrected draft as a model response. Original
writer/paraphraser provenance is validated against the original cards and text;
the reviewer receives a separately authenticated deterministic derivative.
No lease, reservation, request preparation or publication is performed here.
"""
from pathlib import Path
from dataclasses import asdict
import json

from scripts.fleet.criterion_source_cards import corrected_claims, require
from scripts.fleet.criterion_draft_correction import corrected_draft
from scripts.fleet.source_card_profile import binding, checked

SCHEMA = 'fleet-criterion-pre-review/v1'
BUDGET = dict(writer_generations_consumed=1, paraphraser_generations_consumed=1,
              added_author_generations=0, review_attempts_consumed=0,
              next_review_ordinal=1, review_limit=2, semantic_budget_reset=False)
FIELDS = {'schema_version', 'workbook', 'source', 'published_source', 'old_descriptor',
          'blocked', 'snapshot', 'original_pair', 'projection', 'mechanical_proof',
          'corrected_claims', 'corrected_draft', 'changes', 'phrase_proofs',
          'budget', 'directory', 'dependencies', 'lease_boundary'}


def budget(value):
    require(value == BUDGET and set(value) == set(BUDGET) and
            all(type(value[k]) is type(v) for k, v in BUDGET.items()),
            'only unused first review; no additional authors or review reset')
    return value


def load(bound):
    p = checked(bound)
    require(set(p) == FIELDS and p['schema_version'] == SCHEMA,
            'closed criterion pre-review profile required')
    budget(p['budget'])
    projection = checked(p['projection']); pair = checked(p['original_pair'])
    mechanical = checked(p['mechanical_proof'])
    require(projection['status'] == 'SOURCE_PROJECTION_VERIFIED' and
            projection['source'] == p['source'] and
            projection['published_source'] == p['published_source'] and
            projection['qualification'] is False and
            projection['original_failure_preserved'] is True,
            'exact original source projection required')
    require(pair['status'] == 'ORIGINAL_AUTHOR_PAIR_VERIFIED' and
            pair['original_pair_actual'] is True and
            pair['old_descriptor'] == p['old_descriptor'] and pair['blocked'] == p['blocked'] and
            pair['original_claims'] == projection['original_claims'] and
            pair['old_check'] == projection['original_check'],
            'original author pair differs from source projection')
    require(all(type(pair[k]) is int and pair[k] == v for k,v in
                dict(prior_writer_generations=1, prior_paraphraser_generations=1,
                     prior_review_attempts=0, models=0, owning_mutations=0).items()),
            'retained author/review consumption differs')
    claims = checked(p['corrected_claims']); old = checked(pair['original_claims'])
    require(claims == projection['corrected_claims'] and
            claims['workbook'] == old['workbook'] == p['workbook'] and
            checked(p['changes']) == projection['changes'], 'full projected card inventory differs')
    draft = Path(pair['old_draft']['path']).read_text()
    fixed, phrases = corrected_draft(draft, projection['changes'])
    require(binding(p['corrected_draft']['path']) == p['corrected_draft'] and
            Path(p['corrected_draft']['path']).read_text() == fixed and
            checked(p['phrase_proofs']) == phrases, 'deterministic draft derivative differs')
    require(mechanical['status'] == 'DETERMINISTIC_CRITERION_DRAFT_MECHANICS_VERIFIED' and
            mechanical['semantic_approval'] is False and mechanical['qualification'] is False and
            mechanical['source'] == p['source'] and mechanical['published_source'] == p['published_source'] and
            mechanical['original_pair'] == p['original_pair'] and
            mechanical['original_draft'] == pair['old_draft'] and mechanical['corrected_draft'] == fixed and
            mechanical['changed_phrases'] == phrases and mechanical['mechanical']['passed'] is True and
            not mechanical['mechanical']['faults'] and all(type(mechanical[k]) is int and mechanical[k] == 0
                for k in ('models', 'authorizations', 'owning_mutations')),
            'mechanical proof is not semantic approval')
    for key in ('source', 'published_source', 'old_descriptor', 'blocked', 'snapshot'):
        require(binding(p[key]['path']) == p[key], 'original criterion ancestry changed')
    require(binding(pair['old_draft']['path']) == pair['old_draft'], 'original draft changed')
    deps = p['dependencies']
    require(type(deps) is list and len({r['path'] for r in deps}) == len(deps) and
            binding(__file__) in deps, 'complete criterion reader dependency selection required')
    for row in deps: require(binding(row['path']) == row, 'criterion dependency changed')
    directory = Path(p['directory'])
    require(directory.is_absolute() and not any(q.is_symlink() for q in (directory, *directory.parents)),
            'criterion correction namespace is not canonical')
    boundary = p['lease_boundary']
    require(type(boundary) is dict and set(boundary) == {'kind', 'tracker', 'lease', 'takeover'} and
            boundary['kind'] in ('retained-live', 'completed-takeover') and
            (boundary['takeover'] is None) == (boundary['kind'] == 'retained-live'),
            'explicit original validated lease boundary required')
    checked(boundary['tracker'])
    if boundary['takeover'] is not None: checked(boundary['takeover'])
    return p


def project(p, *, old_renderer, new_renderer, extractor, formulas, validator, stage):
    """Repeat source derivation and the actual original mechanical validator."""
    pair = checked(p['original_pair']); original = checked(pair['original_claims'])
    claims, changes = corrected_claims(original, old_renderer, new_renderer, extractor, formulas)
    require(claims == checked(p['corrected_claims']) and changes == checked(p['changes']),
            'actual current source derivation differs from selected projection')
    draft, phrases = corrected_draft(Path(pair['old_draft']['path']).read_text(), changes)
    require(draft == Path(p['corrected_draft']['path']).read_text() and
            phrases == checked(p['phrase_proofs']), 'actual deterministic derivative changed')
    report = validator.check_draft(draft, claims, stage, source_workbook=p['published_source'])
    require(report['passed'] is True and not report['faults'],
            'corrected complete draft fails original mechanical accuracy')
    return claims, draft, phrases, report


def original_pair(p, *, W, P, A, worker):
    """Revalidate real retained executor Calls against their unmodified text."""
    pair = checked(p['original_pair'])
    require(len(pair['author_requests']) == len(pair['author_calls']) == 2,
            'exact original writer and paraphraser required')
    requests = [W.request_saved(Path(row['path'])) for row in pair['author_requests']]
    calls = []
    for row, request, kind in zip(pair['author_requests'], requests,
                                   ('dialogue-writer', 'dialogue-paraphraser')):
        require(binding(row['path']) == row and request.prepared.ordinal == 1 and
                request.prepared.limit == 2 and request.prepared.scope.budget_id == kind,
                'original author request or consumption changed')
        calls.append(P.observed(request, worker.campaign))
    require([asdict(c) for c in calls] == pair['author_calls'],
            'actual executor calls differ from retained author pair')
    checked_authors = A.verify_pair(tuple(calls), policy=requests[0].prepared.policy,
        claims=checked(pair['original_claims']), draft=Path(pair['old_draft']['path']).read_text(),
        round=1, root=worker.root, workbook=p['workbook'], original_sha256=p['source']['sha256'])
    return tuple(calls), checked_authors


def review_body(p, authority, *, claims, draft):
    """Truthful derivative context; it grants no permission to prepare a Call."""
    budget(p['budget'])
    require(checked(p['corrected_claims']) == claims and
            binding(p['corrected_draft']['path']) == p['corrected_draft'] and
            Path(p['corrected_draft']['path']).read_text() == draft,
            'review substituted corrected draft or complete claims')
    fixed, phrases = corrected_draft(Path(checked(p['original_pair'])['old_draft']['path']).read_text(),
                                     checked(p['changes']))
    require(fixed == draft and checked(p['phrase_proofs']) == phrases,
            'review derivative is not its exact original source correction')
    require(type(authority) is dict and set(authority) == {'path', 'sha256'} and
            binding(authority['path']) == authority, 'exact owning correction authority required')
    return dict(claims=claims, draft_markdown=draft, source_proved_correction=dict(
        schema_version=SCHEMA, authority=authority, phrase_proofs=phrases,
        original_pair=p['original_pair'], original_draft=checked(p['original_pair'])['old_draft'],
        corrected_draft=p['corrected_draft'], model_generated_derivative=False,
        semantic_approval=False, budget=p['budget']))


def review_guidance():
    return ('\nIndependently review every original claim identity against the corrected cards and draft. '
            'The disclosed deterministic changes distinguish formula string criteria from worksheet row labels; '
            'they were applied after the two preserved author Calls. The corrected text is not represented as '
            'either model response. Source correction and mechanical validation do not imply semantic approval. '
            'Reject any remaining calculation, sign, scope, coverage, or unsupported-source error normally.')


def unspent(worker, W):
    """Reject prepared or uncertain review work before reserving a correction."""
    original = worker.run/'producer/dialogue'
    require(not any((original/name).exists() for name in
                    ('round-02', 'review-call-01.json', 'review-call-02.json', 'round-01/review.json')) and
            not (worker.run/'producer/requests/dialogue-review').exists(),
            'independent review already prepared, consumed, or uncertain')
    require(not (W.step_directory(worker, 'dialogue-paraphraser')/'complete.json').exists(),
            'completed paraphraser checkpoint cannot be replaced')
    for phase in ('dialogue-review', 'final-runtime', 'dialogue-commit', 'enqueue'):
        require(not W.step_directory(worker, phase).exists(), 'later phase already attempted')
    for phase in ('dialogue-writer', 'dialogue-paraphraser'):
        requests = worker.run/'producer/requests'/phase
        require(requests.is_dir() and {q.name for q in requests.iterdir()} == {'attempt-01'},
                'additional author preparation already consumes another generation')
