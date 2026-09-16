"""Prevent duplicate external operations across interrupted campaign runs."""
from pathlib import Path
import importlib.util

import pytest

spec = importlib.util.spec_from_file_location('recursion_campaign', Path(__file__).resolve().parents[1] / 'scripts/recursion_campaign.py')
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


@pytest.fixture(autouse=True)
def legacy_launch_policy(monkeypatch):
    monkeypatch.setitem(campaign.CONFIG, 'concurrencyPerModel', 12)


def test_successful_mutation_is_not_resubmitted(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(campaign, 'cli', lambda *args, **kwargs: calls.append(args) or {'id': 'submitted'})
    first = campaign.mutation(tmp_path, 'evaluation', ['evaluations', 'bulk-create'], {'name': 'test'})
    second = campaign.mutation(tmp_path, 'evaluation', ['evaluations', 'bulk-create'], {'name': 'test'})
    assert first == second == {'id': 'submitted'}
    assert len(calls) == 1


def test_unknown_submission_is_not_retried(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError('response lost')
    monkeypatch.setattr(campaign, 'cli', timeout)
    with pytest.raises(TimeoutError):
        campaign.mutation(tmp_path, 'evaluation', ['evaluations', 'bulk-create'])
    monkeypatch.setattr(campaign, 'cli', lambda *args, **kwargs: pytest.fail('duplicate submission'))
    with pytest.raises(RuntimeError, match='Reconcile unfinished mutation'):
        campaign.mutation(tmp_path, 'evaluation', ['evaluations', 'bulk-create'])


def test_registry_approval_block_is_respected(tmp_path, monkeypatch):
    campaign.save(tmp_path/'registry-approval.json', {'status':'required'})
    monkeypatch.setattr(campaign.subprocess, 'run', lambda *args, **kwargs: pytest.fail('publication without approval'))
    with pytest.raises(RuntimeError, match='awaiting explicit user approval'):
        campaign.build(tmp_path)


def test_pass_at_three_excludes_infrastructure_failures_until_scored():
    def cell(version, scores):
        return {'solverId': 'model', 'problemVersionId': version, 'totalRuns': 3,
                'attempts': [{'problemRunId': version+str(i), 'attemptNumber': i,
                              'status': 'failed' if score is None else 'completed',
                              'score': score, 'infraRetryCount': 1 if score is None else 0}
                             for i, score in enumerate(scores, 1)]}
    results = {'cells': [cell('pass', [0, 1, 0]), cell('fail', [0, 0, 0]), cell('error', [0, None, 0])]}
    summary = campaign.summarize_results(results)['model']
    assert summary['passingTriples'] == 1 and summary['failedTriples'] == 1
    assert summary['completeTriples'] == 2 and summary['passAt3'] is None
    assert summary['infrastructureRetries'] == 1 and len(summary['erroredRuns']) == 1
    results['cells'][2] = cell('error', [0, 0, 1])
    assert campaign.summarize_results(results)['model']['passAt3'] == 2/3


def test_continuous_scores_cannot_be_reported_as_discrete_pass_at_three():
    result = {'cells': [{'solverId': 'model', 'problemVersionId': 'task', 'totalRuns': 3,
        'attempts': [{'problemRunId': str(i), 'attemptNumber': i, 'status': 'completed', 'score': .9}
                     for i in (1, 2, 3)]}]}
    with pytest.raises(AssertionError, match='discrete'):
        campaign.summarize_results(result)


def test_partial_campaign_cannot_rerun_covered_model(tmp_path, monkeypatch):
    campaign.save(tmp_path/'campaign.json', {'evaluations':[{'id':'existing','models':['opus'],'receipt':'evaluation-opus'}]})
    monkeypatch.setattr(campaign, 'cli', lambda *args, **kwargs: pytest.fail('duplicate submission'))
    campaign.launch(tmp_path, ['opus'])
    with pytest.raises(ValueError, match='Only Opus'):
        campaign.launch(tmp_path, ['opus','gemini'])


def test_unindexed_partial_submission_must_be_reconciled(tmp_path):
    campaign.save(tmp_path/'campaign.json', {'evaluations':[]})
    campaign.save(tmp_path/'evaluation-gemini.intent.json', {'args':['evaluations','bulk-create']})
    with pytest.raises(RuntimeError, match='Reconcile unindexed evaluation'):
        campaign.launch(tmp_path, ['opus'])


def test_cli_accepts_official_empty_success_marker(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(campaign.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout='OK\n', stderr=''))
    assert campaign.cli('run-config-versions', 'discard', '--run-config-version-id', 'draft') is None
