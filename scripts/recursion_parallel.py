#!/usr/bin/env python3
"""Partition task sets across evaluations; preserve three samples per task/model."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
from pathlib import Path

import recursion_campaign as c

PER_EVALUATION_LIMIT = 75
TERMINAL = {'completed', 'failed', 'cancelled'}


def partitions(problems, concurrency):
    if not problems or not isinstance(concurrency, int) or not 1 <= concurrency <= 150:
        raise ValueError('A nonempty problem set and concurrency between 1 and 150 are required')
    count = min(len(problems), (concurrency + PER_EVALUATION_LIMIT - 1) // PER_EVALUATION_LIMIT)
    return [(problems[i::count], min(PER_EVALUATION_LIMIT, concurrency - i * PER_EVALUATION_LIMIT))
            for i in range(count)]


def requests(folder, concurrency=150):
    campaign = c.read(folder/'campaign.json')
    problems = []
    for row in campaign['tasks']:
        version = c.read(folder/'problems'/row['task_id']/'verified.json')
        assert version['lockedAt'] and row['generation_id'] in version['gradingConfig']['command']
        problems.append({'problemId': version['problemId'], 'problemVersionId': version['id'],
                         'environmentId': campaign['environmentId']})
    assert len({p['problemVersionId'] for p in problems}) == len(problems)
    plans = []
    for model in c.policy.MODELS:
        config = c.read(folder/'configs'/model/'verified.json')
        assert config['lockedAt'] and config['verifiedAt']
        assert config['config']['harnessImageUrl'] == c.read(folder/(model+'-image.json'))['image']
        if model == 'opus':
            c.policy.validate(config['config'])
        else:
            assert config['config']['envVars']['FCP_OPENCODE_MODEL'] == 'google-vertex/gemini-3.8-flash'
            assert config['config']['envVars']['LLM_MODEL'] == 'vertex_ai/gemini-3.8-flash'
        display = 'Opus 5 max / Claude Code' if model == 'opus' else 'Gemini 3.8 Flash / OpenCode'
        parts = partitions(problems, concurrency)
        for index, (subset, limit) in enumerate(parts, 1):
            payload = {'name': f'FCP {folder.name} — {display} — batch {index}/{len(parts)} — pass@3',
                'description': 'Disjoint task partition; three fresh independent samples; prior run history excluded.',
                'metadata': copy.deepcopy(campaign['metadata']),
                'tags': ['fcp', 'generation-campaign', 'pass-at-3', folder.name, f'concurrency-{concurrency}'],
                'solvers': [{'displayName': display, 'runConfigVersionId': config['id'], 'concurrency': limit}],
                'problems': subset}
            assert payload['metadata']['attemptsPerProblem'] == 3
            assert payload['metadata']['successThreshold'] == 1
            plans.append({'name': f'evaluation-{model}-batch-{index}', 'model': model, 'payload': payload})
    return plans


def validate_plan(plan):
    expected = set(plan['problemVersionIds'])
    coverage = {e['model']: set() for e in plan['requests']}
    capacity = {m: 0 for m in coverage}
    for entry in plan['requests']:
        payload = entry['payload']
        model = entry['model']
        versions = [p['problemVersionId'] for p in payload['problems']]
        assert versions and len(versions) == len(set(versions))
        assert not coverage[model].intersection(versions), 'Partitions overlap'
        coverage[model].update(versions)
        limit = payload['solvers'][0]['concurrency']
        assert len(payload['solvers']) == 1 and 1 <= limit <= PER_EVALUATION_LIMIT
        capacity[model] += limit
        assert payload['metadata']['attemptsPerProblem'] == 3
        assert payload['metadata']['successThreshold'] == 1
    assert all(v == expected for v in coverage.values()), 'Partition coverage differs from original task set'
    assert all(v <= plan['concurrencyPerModel'] for v in capacity.values())


def prepare(folder, round_name, restart):
    work = folder/'rounds'/round_name
    if (work/'plan.json').exists():
        plan = c.read(work/'plan.json')
        validate_plan(plan)
        return work, plan
    campaign = c.read(folder/'campaign.json')
    entries = requests(folder)
    plan = {'concurrencyPerModel': 150, 'sourceEvaluations': copy.deepcopy(campaign.get('evaluations', [])) if restart else [],
            'problemVersionIds': sorted({p['problemVersionId'] for e in entries for p in e['payload']['problems']}),
            'requests': entries, 'phase': 'prepared', 'restart': restart}
    validate_plan(plan)
    if restart:
        assert plan['sourceEvaluations'], 'No existing evaluations to replace'
        for receipt in plan['sourceEvaluations']:
            detail = c.cli('evaluations', 'get', '--evaluation-id', receipt['id'])
            result = c.cli('evaluations', 'get-results', '--evaluation-id', receipt['id'])
            assert {p['problemVersionId'] for p in detail['problems']} == set(plan['problemVersionIds'])
            c.save(work/(receipt['id']+'-before-status.json'), detail)
            c.save(work/(receipt['id']+'-before-results.json'), result)
    c.save(work/'plan.json', plan)
    return work, plan


def active_capacity(model):
    """Respect the per-model budget across this campaign family's live batches."""
    capacity = 0
    seen = set()
    receipt_sets = [c.read(path).get('evaluations', [])
                    for path in (c.REPORTS/'campaigns').glob('*/campaign.json')]
    max_work = c.REPORTS/'opus-max-20260915'
    # These approved max reruns deliberately retain separate historical journals.
    # Include them here so later generation campaigns share the same budget.
    for path in list(max_work.glob('base-*.response.json')) + list((max_work/'groups').glob('*/evaluation.response.json')):
        response = c.read(path)
        receipt_sets.append([{'id': response['id'], 'models': ['opus']}])
    for receipts in receipt_sets:
        for receipt in receipts:
            if model not in receipt['models'] or receipt['id'] in seen:
                continue
            seen.add(receipt['id'])
            detail = c.cli('evaluations', 'get', '--evaluation-id', receipt['id'])
            if detail['status'] in TERMINAL | {'partial'}:
                continue
            for solver in detail['solvers']:
                if len(receipt['models']) == 1 or model in solver['displayName'].lower():
                    capacity += solver['metadata']['concurrency']
    return capacity


def submit(folder, work, plan, models=c.policy.MODELS):
    c.policy.validate_models(models)
    if any(e['model'] not in c.policy.MODELS for e in plan['requests']):
        raise ValueError('Stale mixed-model plan: prepare a new Opus-max round')
    validate_plan(plan)
    campaign = c.read(folder/'campaign.json')
    receipts = campaign.setdefault('evaluations', [])
    for entry in plan['requests']:
        if entry['model'] not in models:
            continue
        relative = str((work/entry['name']).relative_to(folder))
        expected = {p['problemVersionId'] for p in entry['payload']['problems']}
        previous = next((r for r in receipts if r['receipt'] == relative), None)
        if previous:
            assert set(previous['problemVersionIds']) == expected
            continue
        for r in receipts:
            if entry['model'] in r['models']:
                covered = set(r.get('problemVersionIds', plan['problemVersionIds']))
                assert not covered.intersection(expected), 'Task/model already covered by another evaluation'
        # A response already on disk must be indexed even if its slots are full.
        if not (work/(entry['name']+'.response.json')).exists():
            required = entry['payload']['solvers'][0]['concurrency']
            if active_capacity(entry['model']) + required > plan['concurrencyPerModel']:
                print(json.dumps({'waitingForCapacity': entry['model'], 'batch': entry['name']}), flush=True)
                continue
        live = c.cli('run-config-versions', 'get', '--run-config-version-id', entry['payload']['solvers'][0]['runConfigVersionId'])
        c.policy.validate(live['config'])
        if not live.get('lockedAt') or not live.get('verifiedAt'):
            raise ValueError('Solver must be smoke verified and locked')
        response = c.mutation(work, entry['name'], ['evaluations', 'bulk-create'], entry['payload'])
        assert response['nAttemptsPerProblem'] == 3 and response['totalProblems'] == len(expected)
        assert response['totalSolvers'] == 1
        assert response['solvers'][0]['runConfigVersionId'] == entry['payload']['solvers'][0]['runConfigVersionId']
        assert response['solvers'][0]['metadata']['concurrency'] == entry['payload']['solvers'][0]['concurrency']
        receipts.append({'id': response['id'], 'models': [entry['model']], 'receipt': relative,
                         'problemVersionIds': sorted(expected), 'concurrency': entry['payload']['solvers'][0]['concurrency']})
        campaign['status'] = 'partially_submitted'
        c.save(folder/'campaign.json', campaign)
        print(json.dumps({'evaluationId': response['id'], 'model': entry['model'],
                          'taskCount': len(expected), 'concurrency': receipts[-1]['concurrency']}), flush=True)
    coverage = {m: set() for m in c.policy.MODELS}
    for r in receipts:
        for model in r['models']:
            if model not in coverage:
                continue
            versions = set(r.get('problemVersionIds', plan['problemVersionIds']))
            assert not coverage[model].intersection(versions)
            coverage[model].update(versions)
    if all(v == set(plan['problemVersionIds']) for v in coverage.values()):
        campaign['status'] = 'submitted'
        plan['phase'] = 'submitted'
        c.save(folder/'campaign.json', campaign)
        c.save(work/'plan.json', plan)


def restart(folder, round_name, execute=False):
    work, plan = prepare(folder, round_name, restart=True)
    if not execute:
        print(json.dumps({'plan': str(work/'plan.json'), 'phase': plan['phase'],
                          'evaluations': len(plan['requests']), 'attempts': len(plan['problemVersionIds'])*6}))
        return
    c.policy.validate_models(models)
    if any(e['model'] not in c.policy.MODELS for e in plan['requests']):
        raise ValueError('Stale mixed-model plan: prepare a new Opus-max round')
    validate_plan(plan)
    campaign = c.read(folder/'campaign.json')
    if plan['phase'] == 'cancelling' and campaign.get('parallelRound') == round_name:
        # Recover a crash after the campaign switch but before the plan save.
        superseded = {r['id'] for r in campaign.get('supersededEvaluations', [])}
        assert {r['id'] for r in plan['sourceEvaluations']} <= superseded
        plan['phase'] = 'restart_ready'
        c.save(work/'plan.json', plan)
    if plan['phase'] in ('prepared', 'cancelling'):
        assert campaign['evaluations'] == plan['sourceEvaluations'], 'Campaign changed since restart preparation'
        campaign['status'] = 'restarting'
        campaign['restartRound'] = round_name
        c.save(folder/'campaign.json', campaign)
        plan['phase'] = 'cancelling'
        c.save(work/'plan.json', plan)
        waiting = []
        for receipt in plan['sourceEvaluations']:
            eid = receipt['id']
            status = c.cli('evaluations', 'get', '--evaluation-id', eid)
            if status['status'] not in TERMINAL | {'partial'}:
                c.mutation(work, 'cancel-'+eid, ['evaluations', 'cancel', '--evaluation-id', eid])
            status = c.cli('evaluations', 'get', '--evaluation-id', eid)
            results = c.cli('evaluations', 'get-results', '--evaluation-id', eid)
            c.save(work/(eid+'-final-status.json'), status)
            c.save(work/(eid+'-final-results.json'), results)
            counts = c.Counter(a['status'] for cell in results['cells'] for a in cell['attempts'])
            print(json.dumps({'oldEvaluationId': eid, 'status': status['status'], 'attemptStatuses': counts}), flush=True)
            if status['status'] not in TERMINAL | {'partial'} or any(s not in TERMINAL for s in counts):
                waiting.append(eid)
        if waiting:
            print(json.dumps({'waitingForCancellation': waiting}), flush=True)
            return
        campaign.setdefault('supersededEvaluations', []).extend(plan['sourceEvaluations'])
        campaign['evaluations'] = []
        campaign['parallelRound'] = round_name
        campaign['status'] = 'restart_ready'
        c.save(folder/'campaign.json', campaign)
        plan['phase'] = 'restart_ready'
        c.save(work/'plan.json', plan)
    submit(folder, work, plan)


def launch(folder, models=c.policy.MODELS):
    campaign = c.read(folder/'campaign.json')
    if campaign.get('status') == 'restarting':
        raise RuntimeError('Resume the recorded restart before launching another batch')
    name = campaign.get('parallelRound', 'concurrency150-initial')
    if campaign.get('evaluations') and not (folder/'rounds'/name/'plan.json').exists():
        print('Existing evaluations retained; use an explicit restart to replace them.')
        return
    work, plan = prepare(folder, name, restart=False)
    submit(folder, work, plan, models)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--round', default='concurrency150-rerun-20260914')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    with (c.REPORTS/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        restart(args.campaign.resolve(), args.round, args.execute)
