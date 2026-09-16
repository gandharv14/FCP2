"""Resume the explicitly approved 60-generation Opus-max rollout."""
import argparse
import fcntl
from pathlib import Path
import recursion_campaign as c
import recursion_solver_policy as policy
import recursion_registry_image as registry

WORK = c.REPORTS / 'opus-max-20260915'
NEW = ('2d7bdb608174038c', 'cc2d99a09687fdc4', '071ce1609fb3f255')
TERMINAL = {'completed', 'failed', 'cancelled', 'partial'}


def prepare_new():
    parent = c.read(WORK / 'verified.json')
    for name in NEW:
        folder = c.REPORTS / 'campaigns' / name
        if not (folder / 'opus-image.json').exists():
            registry.main(folder, True)
        if not all((folder / 'problems' / row['task_id'] / 'verified.json').exists()
                   for row in c.read(folder / 'campaign.json')['tasks']):
            c.provision(folder)
        destination = WORK / 'proposals' / (name + '.json')
        if destination.exists():
            continue
        rows = c.read(folder / 'campaign.json')['tasks']
        problems, stages = [], {}
        for row in rows:
            version = c.read(folder / 'problems' / row['task_id'] / 'verified.json')
            task = Path(row['snapshot'])
            workbook = next((task / 'environment').glob(row['task_id'] + '-inputs.*')).name
            problems.append({'task': workbook, 'problemId': version['problemId'],
                             'problemVersionId': version['id'], 'environmentId': c.ENVIRONMENT,
                             'generationId': row['generation_id']})
            kind = 'research' if (task / 'environment/mcp-server/server.py').exists() else 'plain'
            prompt = f'Use Bash to confirm {workbook} exists and print only CLAUDE_CODE_EFFORT_LEVEL and ANTHROPIC_MODEL. '
            if (task / 'environment/additional-assumptions.md').exists():
                prompt += 'Confirm additional-assumptions.md exists. '
            if kind == 'research':
                prompt += 'Call research list_sources once with kind empty and limit 1. '
            stages[kind] = {'prompt': prompt + 'Reply FCP_OPUS_MAX_READY. Do not modify any file.',
                            'files': [], 'qualityCheck': 'PASS only if requested files exist, Bash shows max and claude-opus-5, '
                            + ('research MCP succeeds, ' if kind == 'research' else '')
                            + 'and final answer contains FCP_OPUS_MAX_READY. Fail on authentication or runtime errors.'}
        config = policy.enforce(parent['config'])
        config['harnessImageUrl'] = c.read(folder / 'opus-image.json')['image']
        c.save(destination, {'campaign': name, 'tasks': problems, 'config': config,
                            'credentialParent': parent['id'], 'smokeStages': stages,
                            'attempts': len(rows) * 3, 'concurrency': 3})


def configure(proposal):
    group = WORK / 'groups' / proposal['campaign']
    if (group / 'verified.json').exists():
        return True
    parent = c.read(WORK / 'verified.json')
    policy.validate(proposal['config'])
    assert proposal['credentialParent'] == parent['id']
    draft = c.mutation(group, 'create', ['run-configs', 'create-version', '--run-config-id', parent['runConfigId'],
                                        '--parent-run-config-version-id', parent['id']])
    vid = draft['id']
    for kind, probe in proposal['smokeStages'].items():
        c.mutation(group, kind + '-update', ['run-config-versions', 'update', '--run-config-version-id', vid],
                   {'config': proposal['config'], 'probe': probe,
                    'notes': f'Approved Opus 5 max generation rollout {proposal["campaign"]}; {kind}; discrete pass@3.'})
        receipt = c.mutation(group, kind + '-probe', ['run-config-versions', 'probe', '--run-config-version-id', vid])
        result = c.cli('probe-runs', 'get', '--run-config-probe-run-id', receipt['probeRunId'])
        c.save(group / (kind + '-probe-status.json'), result)
        print(proposal['campaign'], kind, result['status'], flush=True)
        if result['status'] != 'succeeded':
            return False
        assert result['result']['passed'] is True, 'Smoke quality check failed'
    c.mutation(group, 'lock', ['run-config-versions', 'lock', '--run-config-version-id', vid])
    verified = c.cli('run-config-versions', 'get', '--run-config-version-id', vid)
    assert verified['lockedAt'] and verified['verifiedAt']
    policy.validate(verified['config'])
    assert verified['config'] == proposal['config']
    c.save(group / 'verified.json', verified)
    return True


def active_capacity():
    from recursion_parallel import active_capacity as campaign_capacity
    return campaign_capacity('opus')


def launch(proposal):
    group = WORK / 'groups' / proposal['campaign']
    if (group / 'evaluation.response.json').exists():
        return
    verified = c.read(group / 'verified.json')
    live = c.cli('run-config-versions', 'get', '--run-config-version-id', verified['id'])
    policy.validate(live['config'])
    assert live['lockedAt'] and live['verifiedAt'] and live['config'] == proposal['config']
    if active_capacity() + proposal['concurrency'] > 150:
        print(proposal['campaign'], 'waiting for capacity', flush=True)
        return
    problems = [{k: row[k] for k in ('problemId', 'problemVersionId', 'environmentId')} for row in proposal['tasks']]
    assert len({row['problemVersionId'] for row in problems}) == len(problems)
    payload = {'name': f'FCP Opus 5 max remaining {proposal["campaign"]} — pass@3',
               'description': 'Approved generation-pinned Opus max rollout; three independent attempts per task; discrete full-answer grading.',
               'metadata': {'schemaVersion': 1, 'attemptsPerProblem': 3, 'successThreshold': 1},
               'tags': ['fcp', 'opus-max', 'pass-at-3', proposal['campaign']],
               'solvers': [{'displayName': 'Opus 5 max / Claude Code', 'runConfigVersionId': verified['id'],
                            'concurrency': proposal['concurrency']}], 'problems': problems}
    response = c.mutation(group, 'evaluation', ['evaluations', 'bulk-create'], payload)
    assert response['nAttemptsPerProblem'] == 3 and response['totalProblems'] == len(problems)
    assert response['totalSolvers'] == 1 and response['solvers'][0]['runConfigVersionId'] == verified['id']
    print(proposal['campaign'], 'submitted', response['id'], 'attempts', len(problems) * 3, flush=True)


def main(prepare=False):
    approval = c.read(WORK / 'remaining-approval.json')
    assert approval['status'] == 'approved'
    with (c.REPORTS / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if prepare:
            prepare_new()
        def order(path):
            group = WORK / 'groups' / path.stem
            in_progress = (group / 'create.response.json').exists() and not (group / 'verified.json').exists()
            return (not in_progress, path.stem)
        for path in sorted((WORK / 'proposals').glob('*.json'), key=order):
            proposal = c.read(path)
            assert proposal['campaign'] in approval['campaigns']
            if configure(proposal):
                launch(proposal)
            else:
                # Recursion permits only one draft per parent run config.
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-new', action='store_true')
    main(parser.parse_args().prepare_new)
