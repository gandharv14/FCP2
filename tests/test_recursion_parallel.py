"""Non-overlapping reruns preserve pass@3 and never duplicate paid submissions."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_parallel as p


def plan():
    entries = []
    problems = [{'problemId': str(i), 'problemVersionId': str(i), 'environmentId': 'env'} for i in range(45)]
    for model in ('opus',):
        for i, (subset, slots) in enumerate(p.partitions(problems, 150)):
            entries.append({'model': model, 'name': f'evaluation-{model}-{i}', 'payload': {
                'metadata': {'attemptsPerProblem': 3, 'successThreshold': 1},
                'problems': subset, 'solvers': [{'runConfigVersionId': model, 'concurrency': slots}]}})
    return {'requests': entries, 'problemVersionIds': [str(i) for i in range(45)],
            'concurrencyPerModel': 150, 'phase': 'restart_ready'}


def test_two_partitions_preserve_all_135_attempts_per_model():
    value = plan()
    p.validate_plan(value)
    for model in ('opus',):
        entries = [e for e in value['requests'] if e['model'] == model]
        assert [len(e['payload']['problems']) for e in entries] == [23, 22]
        assert sum(e['payload']['solvers'][0]['concurrency'] for e in entries) == 150
        assert sum(len(e['payload']['problems']) * 3 for e in entries) == 135


@pytest.mark.parametrize('damage', ['overlap', 'missing', 'too_many_slots', 'extra_attempt'])
def test_invalid_partition_cannot_be_submitted(damage):
    value = copy.deepcopy(plan())
    payload = value['requests'][1]['payload']
    if damage == 'overlap': payload['problems'][0] = value['requests'][0]['payload']['problems'][0]
    if damage == 'missing': payload['problems'].pop()
    if damage == 'too_many_slots': payload['solvers'][0]['concurrency'] = 150
    if damage == 'extra_attempt': payload['metadata']['attemptsPerProblem'] = 4
    with pytest.raises(AssertionError): p.validate_plan(value)


def test_restart_resumes_without_duplicate_evaluations(tmp_path, monkeypatch):
    work = tmp_path/'rounds'/'rerun'
    value = plan()
    p.c.save(tmp_path/'campaign.json', {'evaluations': []})
    p.c.save(work/'plan.json', value)
    calls = []
    monkeypatch.setattr(p, 'active_capacity', lambda model: 0)
    def api(*args, payload=None):
        if args[:2] == ('run-config-versions', 'get'):
            return {'config': p.c.policy.enforce({}), 'lockedAt': 'now', 'verifiedAt': 'now'}
        data = p.c.read(payload)
        calls.append(data)
        return {'id': f'new-{len(calls)}', 'nAttemptsPerProblem': 3,
                'totalProblems': len(data['problems']), 'totalSolvers': 1,
                'solvers': [{'runConfigVersionId': data['solvers'][0]['runConfigVersionId'],
                             'metadata': {'concurrency': 75}}]}
    monkeypatch.setattr(p.c, 'cli', api)
    p.submit(tmp_path, work, value, ['opus'])
    p.submit(tmp_path, work, value)
    p.submit(tmp_path, work, value)
    assert len(calls) == 2
    assert len(p.c.read(tmp_path/'campaign.json')['evaluations']) == 2


def test_new_batch_waits_if_model_budget_is_already_full(tmp_path, monkeypatch):
    work = tmp_path/'rounds'/'rerun'
    value = plan()
    p.c.save(tmp_path/'campaign.json', {'evaluations': []})
    monkeypatch.setattr(p, 'active_capacity', lambda model: 150)
    monkeypatch.setattr(p.c, 'cli', lambda *a, **k: pytest.fail('must not exceed model concurrency budget'))
    p.submit(tmp_path, work, value)
    assert not p.c.read(tmp_path/'campaign.json')['evaluations']


def test_monitor_aggregates_disjoint_batches_and_excludes_superseded(tmp_path, monkeypatch):
    rows = [{'task_id': str(i)} for i in range(4)]
    receipts = []
    for model in ('opus', 'gemini'):
        for n, versions in enumerate([['0', '2'], ['1', '3']]):
            receipts.append({'id': model+str(n), 'models': [model],
                             'receipt': model+str(n), 'problemVersionIds': versions})
    p.c.save(tmp_path/'campaign.json', {'tasks': rows, 'evaluations': receipts,
                                      'supersededEvaluations': [{'id': 'old-do-not-count'}]})
    for row in rows: p.c.save(tmp_path/'problems'/row['task_id']/'verified.json', {'id': row['task_id']})
    def api(*args):
        receipt = next(r for r in receipts if r['id'] == args[-1])
        if args[1] == 'get': return {'status': 'completed'}
        return {'cells': [{'solverId': receipt['id'], 'problemVersionId': v, 'totalRuns': 3,
                          'attempts': [{'problemRunId': v+str(i), 'attemptNumber': i,
                                        'status': 'completed', 'score': int(v == '0' and i == 1)}
                                       for i in (1, 2, 3)]} for v in receipt['problemVersionIds']]}
    monkeypatch.setattr(p.c, 'cli', api)
    p.c.monitor(tmp_path)
    summary = p.c.read(tmp_path/'results-summary.json')
    assert summary['complete'] and summary['submittedRuns'] == 24
    assert all(m['passAt3'] == .25 for m in summary['models'].values())


def test_opus_only_monitor_completes_three_attempts_per_task(tmp_path, monkeypatch):
    p.c.save(tmp_path/'campaign.json',{'tasks':[{'task_id':'a'}],
        'evaluations':[{'id':'new','models':['opus'],'receipt':'evaluation-opus'}]})
    p.c.save(tmp_path/'problems/a/verified.json',{'id':'a'})
    def api(*args):
        if args[1]=='get':return {'status':'completed'}
        return {'cells':[{'solverId':'opus','problemVersionId':'a','totalRuns':3,
            'attempts':[{'problemRunId':str(i),'attemptNumber':i,'status':'completed','score':int(i==2)} for i in [1,2,3]]}]}
    monkeypatch.setattr(p.c,'cli',api)
    p.c.monitor(tmp_path)
    result=p.c.read(tmp_path/'results-summary.json')
    assert result['complete'] and result['expectedRuns']==3
    assert result['models']['opus']['passAt3']==1


def test_capacity_includes_separate_max_rollout_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(p.c, 'REPORTS', tmp_path)
    p.c.save(tmp_path/'campaigns/a/campaign.json', {'evaluations': [{'id': 'current', 'models': ['opus']}]})
    p.c.save(tmp_path/'opus-max-20260915/groups/a/evaluation.response.json', {'id': 'current'})
    p.c.save(tmp_path/'opus-max-20260915/groups/b/evaluation.response.json', {'id': 'second'})
    p.c.save(tmp_path/'opus-max-20260915/base-1.response.json', {'id': 'finished'})
    def api(*args):
        return {'status': 'completed' if args[-1] == 'finished' else 'running',
                'solvers': [{'displayName': 'Opus 5 max', 'metadata': {'concurrency': 50}}]}
    monkeypatch.setattr(p.c, 'cli', api)
    assert p.active_capacity('opus') == 100
    assert p.active_capacity('gemini') == 0
