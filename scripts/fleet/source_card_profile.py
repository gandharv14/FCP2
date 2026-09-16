"""Data-selected source-card repair boundaries; no authorization or task writes.

A source correction retains author generation one, consumes generation two, and
leaves independent review one unused. The selected original modules authenticate
all lease history; this module never renews a lease or authorizes a replacement.
"""
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import re

from scripts.fleet.source_card_authoring import budget_contract, require

SCHEMA = 'fleet-source-card-profile/v1'
KINDS = {'averageif-string-criterion/v1'}


def binding(path):
    path = Path(path)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'absolute nonsymlink source-card evidence required')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def checked(row):
    require(type(row) is dict and set(row) == {'path', 'sha256'} and
            binding(row['path']) == row, 'source-card evidence changed')
    return json.loads(Path(row['path']).read_bytes())


def load(bound):
    p = checked(bound)
    fields = {'schema_version', 'workbook', 'source', 'published_source', 'correction_kind',
              'budget', 'projection', 'original_pair', 'corrected_claims', 'changes',
              'old_descriptor', 'blocked', 'snapshot', 'directory', 'dependencies',
              'lease_boundary'}
    require(set(p) == fields and p['schema_version'] == SCHEMA and
            type(p['workbook']) is str and re.fullmatch(r'[0-9]{4}', p['workbook']) and
            p['correction_kind'] in KINDS, 'closed source-card profile required')
    budget_contract(p['budget'])
    projection, pair = checked(p['projection']), checked(p['original_pair'])
    require(projection['status'] == 'SOURCE_PROJECTION_VERIFIED' and
            projection['source'] == p['source'] and
            projection['published_source'] == p['published_source'] and
            projection['qualification'] is False and
            projection['original_failure_preserved'] is True and
            all(type(projection[k]) is int and projection[k] == 0
                for k in ('models', 'authorizations', 'owning_steps')),
            'source projection does not prove this unchanged source')
    require(pair['status'] == 'ORIGINAL_AUTHOR_PAIR_VERIFIED' and
            pair['original_pair_actual'] is True and pair['old_descriptor'] == p['old_descriptor'] and
            pair['blocked'] == p['blocked'] and
            pair['original_claims'] == projection['original_claims'] and
            pair['old_check'] == projection['original_check'] and
            all(type(pair[k]) is int and pair[k] == v for k, v in
                {'prior_writer_generations': 1, 'prior_paraphraser_generations': 1,
                 'prior_review_attempts': 0, 'models': 0, 'owning_mutations': 0}.items()),
            'original author or unused review boundary differs')
    old, fixed, changes = checked(pair['original_claims']), checked(p['corrected_claims']), checked(p['changes'])
    require(old['workbook'] == fixed['workbook'] == p['workbook'] and
            fixed == projection['corrected_claims'] and changes == projection['changes'] and
            type(projection['changed_cards']) is int and 0 < len(changes) == projection['changed_cards'] and
            type(projection['total_cards']) is int and len(old['claims']) == len(fixed['claims']) == projection['total_cards'],
            'complete source-card projection changed')
    ids = [c['record_id'] for c in old['claims']]
    require(len(set(ids)) == len(ids) and ids == [c['record_id'] for c in fixed['claims']] and
            len({c['record_id'] for c in changes}) == len(changes) and
            set(c['record_id'] for c in changes) <= set(ids), 'source-card claim identity changed')
    require(len(pair['author_calls']) == len(pair['author_requests']) == 2,
            'two original authors required')
    for row in (p['source'], p['published_source'], pair['old_draft'], p['old_descriptor'], p['blocked'], p['snapshot']):
        require(binding(row['path']) == row, 'selected source-card ancestry changed')
    directory = Path(p['directory'])
    require(directory.is_absolute() and not any(q.is_symlink() for q in (directory, *directory.parents)),
            'source-card namespace cannot alias another task')
    deps = p['dependencies']
    require(type(deps) is list and len({r['path'] for r in deps}) == len(deps) and binding(__file__) in deps,
            'complete selected source-card dependency inventory required')
    for row in deps:
        require(binding(row['path']) == row, 'selected source-card implementation changed')
    boundary = p['lease_boundary']
    require(type(boundary) is dict and set(boundary) == {'kind', 'tracker', 'lease', 'takeover'} and
            boundary['kind'] in ('retained-live', 'completed-takeover') and
            (boundary['takeover'] is None) == (boundary['kind'] == 'retained-live'),
            'explicit source-card lease boundary required')
    checked(boundary['tracker'])
    if boundary['takeover'] is not None: checked(boundary['takeover'])
    return p


def validate_lease(p, worker, before, lease, *, renewal, takeover, exact_boundary):
    """Authenticate the selected lease through original validators, without CAS.

    Authorization uses exact_boundary=True. Subsequent owning steps may advance
    that same lease only along the original, fully validated continuation chain.
    """
    require(type(exact_boundary) is bool, 'explicit source-card boundary mode required')
    boundary = p['lease_boundary']; selected = checked(boundary['tracker'])
    require(before['workbook_id'] == selected['workbook_id'] == p['workbook'] and
            before['bindings']['source_file'] == selected['bindings']['source_file'] == p['published_source'] and
            before['bindings']['original_raw_source'] == selected['bindings']['original_raw_source'] == p['source'],
            'source-card lease belongs to another source')
    prior = renewal.validate_historical_integrity(worker, before, p['snapshot'])
    # The R permit starts before its own renewal, whereas normal task commits
    # can subsequently advance the tracker. The actual blocked checkpoint is
    # the selected pre-repair boundary, not that earlier R permit.
    blocked = checked(p['blocked'])
    require(blocked['status']=='DIALOGUE_REPAIR_REQUIRED', 'original blocked source-card boundary required')
    original_record = boundary['tracker']
    if boundary['kind'] == 'completed-takeover':
        _, after, new = takeover.validate_completion(boundary['takeover'])
        receipt = checked(boundary['takeover'])
        require(after == selected and receipt['after'] == boundary['tracker'] and
                asdict(new) == boundary['lease'], 'selected completed takeover changed')
        original_record = checked(receipt['stopped_owner_proof'])['before']
    original = checked(original_record); original_lease=blocked['lease']
    require(original['revision']==original_lease['revision'] and
            original_record['sha256']==original_lease['sha256'] and
            all(original['leases']['task_agent'][k]==original_lease[k] for k in ('owner','token')),
            'blocked checkpoint differs from exact pre-repair tracker')
    takeover.continuation_chain(original_record, original_lease, selected,
        type(lease)(**boundary['lease']), takeover=boundary['takeover'],
        tracker_path=worker.context.tracker_root/(p['workbook']+'.json'))
    # All subsequent transactions must retain this exact owner/token and follow
    # the original renewal history. No future takeover is silently admitted.
    takeover.continuation_chain(boundary['tracker'], boundary['lease'], before, lease,
        tracker_path=worker.context.tracker_root/(p['workbook']+'.json'))
    if exact_boundary:
        require(before == selected and asdict(lease) == boundary['lease'],
                'source-card authorization boundary was already consumed or changed')
    return prior


def feedback(p, authority, draft):
    budget_contract(p['budget'])
    require(type(authority) is dict and set(authority) == {'path', 'sha256'} and
            type(draft) is str and draft.strip(), 'exact authority and original draft required')
    return dict(previous_draft=draft, source_card_feedback=dict(
        schema_version='harbor-source-card-authoring/v1', authority=authority, budget=p['budget'],
        changed_claim_ids=[c['record_id'] for c in checked(p['changes'])],
        reason='The old source cards mislabeled AVERAGEIF string criteria as worksheet row labels. '
               'Use the corrected cards and retain every actual row, literal criterion, and calculation. '
               'The original author pair failed mechanical checks; no independent dialogue review has occurred.'))
