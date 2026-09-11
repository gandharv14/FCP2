"""General cohorts retain the legacy bootstrap's identity and evidence gates."""
import dataclasses
from pathlib import Path

import pytest

from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import (
    selected as legacy_selected, write, complete, baseline,
    test_frozen_identity_and_proofs_reject_before_admission as check_frozen,
    test_exact_prior_audit_batches_preserve_remaining_budget as check_budget,
)
from scripts.batch import harbor_admission as a
from scripts.batch import harbor_workbook_bootstrap as b


@pytest.fixture
def selected(setup, monkeypatch):
    s, history, tmp = legacy_selected.__wrapped__(setup, monkeypatch)
    value = a.read(s.candidate_manifest)
    value.update(schema_version='harbor-qualified-task-candidate-proposal/v1',
                 requested_workbook_ids=['0001', '0002'], target_new_qualified_tasks=2,
                 selected_candidates=value['selected_candidates'][:2])
    binding = write(tmp/'general-candidate.json', value)
    p = policy(binding, s.policy)
    return dataclasses.replace(s, candidate_manifest=binding, policy=p), history, tmp


def policy(binding, p, **changes):
    args = dict(batch=p.batch, tracker_root=p.tracker_root,
                original_inventory=p.original_inventory, dependency=p.dependency,
                dependencies=p.dependencies, history_roots=p.history_roots,
                expected_original_count=p.expected_original_count)
    return a.policy_from_candidate_manifest(binding, **{**args, **changes})


def test_arbitrary_cohort_bootstraps_real_generations(selected):
    s, _, _ = selected
    prepared = b.prepare(s)
    _, result = complete(s, prepared)
    t = result['tracker']
    assert t['requested_ids'] == ['0001', '0002']
    assert t['lane_state']['harbor-source-warden']['state'] == 'passed'
    assert t['lane_state']['harbor-segmentation-analyst']['state'] == 'passed'
    assert all(row['attempt'] == 0 for row in t['lane_state'].values())
    assert t['promotion_ids']['task_generation_id'] is None
    assert b.read(prepared)['validated']['targets'] == a.read(s.candidate_manifest)['selected_candidates'][0]['retained_targets']


@pytest.mark.parametrize('change', [
    'missing_ids', 'empty', 'duplicate', 'unknown', 'reorder', 'count', 'bool_count',
    'denominator', 'actual_denominator', 'few_selected', 'duplicate_selected',
    'original', 'excluded', 'unmapped', 'missing_unmapped', 'history_scope',
    'inventory', 'prior_audit', 'missing_prior', 'old_schema',
])
def test_general_contract_rejects_changed_cohort_or_evidence(selected, change):
    s, _, tmp = selected
    value = a.read(s.candidate_manifest)
    kwargs = {}
    if change == 'missing_ids': value.pop('requested_workbook_ids')
    elif change == 'empty': value['requested_workbook_ids'] = []
    elif change == 'duplicate': value['requested_workbook_ids'][1] = '0001'
    elif change == 'unknown': value['requested_workbook_ids'][1] = '9999'
    elif change == 'reorder': value['requested_workbook_ids'].reverse()
    elif change == 'count': value['target_new_qualified_tasks'] = 3
    elif change == 'bool_count': value['target_new_qualified_tasks'] = True
    elif change == 'denominator': value['denominator_for_original_rebuild'] = 49
    elif change == 'actual_denominator':
        value['denominator_for_original_rebuild'] = 49
        kwargs['expected_original_count'] = 49
    elif change == 'few_selected': value['selected_candidates'].pop()
    elif change == 'duplicate_selected': value['selected_candidates'][1] = value['selected_candidates'][0]
    elif change == 'original': value['selected_candidates'][0]['original']['sha256'] = '0'*64
    elif change == 'excluded': value['selected_candidates'][0]['exclusion_evidence'] = ['prior task']
    elif change == 'prior_audit': value['selected_candidates'][0]['prior_audit_metadata'] = [{'path': '/missing/audit.json', 'sha256': '0'*64}]
    elif change == 'missing_prior': value['selected_candidates'][0].pop('prior_audit_metadata')
    elif change == 'old_schema': value['schema_version'] = 'new50-qualified-task-candidate-proposal/v1'
    else:
        survey = a.read(value['survey'])
        if change == 'unmapped': survey['unmapped_prior_artifacts'] = ['prior task']
        elif change == 'missing_unmapped': survey.pop('unmapped_prior_artifacts')
        elif change == 'history_scope': survey['scanned_roots'] = []
        else: survey['inventory']['sha256'] = '0'*64
        value['survey'] = write(tmp/'bad-survey.json', survey)
    binding = write(tmp/'bad-candidate.json', value)
    with pytest.raises((ValueError, OSError)):
        policy(binding, s.policy, **kwargs)
    assert not (s.policy.tracker_root/'0001.json').exists()


@pytest.mark.parametrize('change', ['ledger_changed', 'targets', 'curation', 'source_manifest', 'foreign_id', 'unproved_normalization'])
def test_general_bootstrap_retains_frozen_evidence_gates(selected, change):
    check_frozen(selected, change)


@pytest.mark.parametrize('spent', [1, 2])
def test_general_bootstrap_retains_prior_audit_budget(selected, monkeypatch, spent):
    check_budget(selected, monkeypatch, spent)


def test_new_history_and_changed_manifest_block_general_admission(selected):
    s, history, _ = selected
    prepared = b.prepare(s)
    prior = history/'0001-old-task'
    prior.mkdir()
    (prior/'instruction.md').write_text('Existing task')
    with pytest.raises(ValueError, match='prior task'):
        b.admit(s, prepared)
    assert not (s.policy.tracker_root/'0001.json').exists()
    Path(s.candidate_manifest['path']).write_text('{}')
    with pytest.raises(ValueError):
        b.validate(s)
