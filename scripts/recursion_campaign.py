#!/usr/bin/env python3
"""Resumable generation-pinned Recursion campaign. No credential values persisted."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import recursion_solver_policy as policy

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports/recursion'
CONFIG = json.loads((ROOT / 'configs/recursion-fcp-pass3.json').read_text())
ENVIRONMENT = CONFIG['environmentId']


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def read(path):
    return json.loads(path.read_text())


def cli(*args, payload=None, timeout_seconds=None):
    command = [sys.executable, str(ROOT / 'scripts/recursion_cli.py'), *args]
    if payload is not None:
        command += ['--from-json', str(payload)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f'CLI read timed out after {timeout_seconds} seconds') from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:2000])
    output = result.stdout.strip()
    # The official CLI prints OK for successful HTTP 204 operations.
    return None if output in ('', 'OK') else json.loads(output)


def mutation(folder, name, args, payload=None):
    """Persist intent before dispatch; uncertain requests need reconciliation."""
    destination = folder / (name + '.response.json')
    intent = folder / (name + '.intent.json')
    if destination.exists():
        return read(destination)
    if intent.exists():
        raise RuntimeError(f'Reconcile unfinished mutation before retrying: {intent}')
    payload_file = None
    if payload is not None:
        payload_file = folder / (name + '.request.json')
        save(payload_file, payload)
    save(intent, {'args': args, 'payload': str(payload_file) if payload_file else None,
                  'createdAt': datetime.now(timezone.utc).isoformat()})
    result = cli(*args, payload=payload_file)
    save(destination, result)
    return result


def helper(folder):
    path = folder / 'runner_support.py'
    spec = importlib.util.spec_from_file_location('fcp_runner_support', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def init_campaign():
    from recursion_task_inventory import inventory
    current = inventory(Path(CONFIG['tasksRoot']))
    # The initial preflight proved none of these prompts match any historical
    # problem version. Subsequent campaigns exclude generations already queued.
    known = set()
    for previous in (REPORTS / 'campaigns').glob('*/campaign.json'):
        known.update((x['task_id'], x['generation_id']) for x in read(previous)['tasks'])
    rows = [x for x in current['tasks'] if x['status'] == 'manifest_verified'
            and (x['task_id'], x['generation_id']) not in known]
    if not rows:
        print('No unqueued verified generations.')
        return
    identity = hashlib.sha256(json.dumps([(x['task_id'], x['generation_id']) for x in rows]).encode()).hexdigest()
    folder = REPORTS / 'campaigns' / identity[:16]
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        source = Path(row['path'])
        manifest = read(source / 'generation-manifest.json')
        destination = folder / 'tasks' / (row['task_id'] + '-outputs')
        for relative, item in manifest['artifacts'].items():
            path = (source / relative).resolve()
            if not path.is_relative_to(source.resolve()):
                raise RuntimeError('Task artifact escapes its root')
            contents = path.read_bytes()
            if hashlib.sha256(contents).hexdigest() != item['sha256']:
                raise RuntimeError(f'Task changed during snapshot: {path}')
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(contents)
        save(destination / 'generation-manifest.json', manifest)
        row['snapshot'] = str(destination)
    shutil.copy2(ROOT.parent / 'run_recursion_eval.py', folder / 'runner_support.py')
    support = helper(folder)
    tasks = {x['task_id']: Path(x['snapshot']) for x in rows}
    support.prepare_build_context(folder / 'build', tasks)
    # Sanitize inherited OpenCode configuration for plain (no-MCP) tasks.
    opencode = folder / 'build/fcp-opencode-wrapper'
    code = opencode.read_text().replace('mcp_pid=""',
        'export OPENCODE_CONFIG_CONTENT=\'{"permission":{"*":"allow","external_directory":"allow"},"mcp":{}}\'\nmcp_pid=""', 1)
    opencode.write_text(code)
    payload = {'id': identity, 'tasks': rows, 'environmentId': ENVIRONMENT,
               'createdAt': datetime.now(timezone.utc).isoformat(),
               'metadata': {'schemaVersion': 1, 'attemptsPerProblem': 3, 'successThreshold': 1},
               'status': 'snapshotted', 'supportSha256': hashlib.sha256((folder/'runner_support.py').read_bytes()).hexdigest()}
    save(folder / 'campaign.json', payload)
    print(json.dumps({'campaign': str(folder), 'tasks': len(rows), 'plannedRuns': len(rows)*3}))


def build(folder):
    approval = folder / 'registry-approval.json'
    if approval.exists() and read(approval).get('status') != 'approved':
        raise RuntimeError('Registry publication is awaiting explicit user approval; see registry-approval.json')
    from recursion_scaffold import validate as validate_scaffold
    validate_scaffold(folder / 'build')
    for model in policy.MODELS:
        output = folder / (model + '-image.json')
        if output.exists():
            continue
        versions = read(REPORTS / 'remote' / (model + '-versions.json'))
        parent = max((v for v in versions if v.get('lockedAt') and v.get('verifiedAt')),
                     key=lambda v: v['versionNumber'])
        config = parent['config']
        if model == 'opus':
            policy.validate(config)
        else:
            assert config['envVars']['FCP_OPENCODE_MODEL'] == 'google-vertex/gemini-3.8-flash'
        save(folder / (model + '-parent.json'), parent)
        repository = config['harnessImageUrl'].split('@')[0]
        tag = repository + ':generation-' + folder.name
        subprocess.run(['docker','buildx','build','--platform','linux/amd64',
                        '--build-arg','BASE_IMAGE='+config['harnessImageUrl'],
                        '--tag',tag,'--push',str(folder/'build')], check=True)
        result = subprocess.run(['gcloud','artifacts','docker','images','describe',tag,
                                 '--billing-project=lb-ml-dev',
                                 '--format=value(image_summary.fully_qualified_digest)'],
                                capture_output=True,text=True,check=True)
        digest = result.stdout.strip()
        assert '@sha256:' in digest
        save(output, {'image': digest, 'tag': tag})
        print(model, 'image ready', flush=True)


def grader_command(task):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        tests = task / 'tests'
        paths = [tests/'run_grader.py', tests/'answer_key.json',
                 *sorted((tests/'finance_grader').glob('*.py'))]
        for path in paths:
            data = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(tests).as_posix())
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    encoded = base64.b64encode(gzip.compress(stream.getvalue(), mtime=0)).decode()
    generation = read(task/'generation-manifest.json')['generation_id']
    return (f': FCP_GENERATION_DISCRETE_V1_{generation}; set -e; '
            'mkdir -p /tmp/fcp-grader /tmp/fcp-verifier; '
            f"printf '%s' '{encoded}' | base64 -d | tar -xz -C /tmp/fcp-grader; "
            'python3 /tmp/fcp-grader/run_grader.py --workspace /workspace '
            '--answer-key /tmp/fcp-grader/answer_key.json '
            '--output-dir /tmp/fcp-verifier --mode discrete; '
            'cp /tmp/fcp-verifier/reward.json "$GRADE_OUTPUT"')


def provision(folder):
    campaign = read(folder/'campaign.json')
    for row in campaign['tasks']:
        task_id, generation = row['task_id'], row['generation_id']
        task = Path(row['snapshot'])
        work = folder/'problems'/task_id
        # Generation-specific external IDs prevent imported stale files or
        # draft versions owned by other agents from entering these evaluations.
        created = mutation(work, 'create', ['problems','upsert-with-version',
                            '--environment-id',ENVIRONMENT],
                           {'externalId': f'fcp-generation-{task_id}-{generation}'})
        pid, vid = created['problem']['id'], created['version']['id']
        mutation(work, 'identity', ['problems','update','--problem-id',pid],
                 {'title': f'fcp/{task_id}-outputs [{generation[:12]}]',
                  'description': f'Validated generation {generation}; original source {row["path"]}.'})
        payload = {'prompt': (task/'instruction.md').read_text(), 'timeoutSeconds':10800,
                   'maxTurns':None, 'containerImage':None, 'containerSize':'medium',
                   'solverRunConfigVersionId':None, 'graderRunConfigVersionId':None,
                   'gradingConfig': {'type':'programmatic','command':grader_command(task),
                                     'outputFormat':'json'}}
        mutation(work,'update',['problem-versions','update','--problem-version-id',vid],payload)
        mutation(work,'lock',['problem-versions','lock','--problem-version-id',vid])
        verified = cli('problem-versions','get','--problem-version-id',vid)
        assert verified['lockedAt'] and verified['prompt'] == payload['prompt']
        assert verified['gradingConfig'] == payload['gradingConfig']
        save(work/'verified.json', verified)
        print('Problem ready',task_id,flush=True)


def configure(folder):
    campaign = read(folder/'campaign.json')
    research = [r for r in campaign['tasks'] if (Path(r['snapshot'])/'environment/mcp-server/server.py').exists()]
    plain = [r for r in campaign['tasks'] if r not in research]
    for model in policy.MODELS:
        parent = read(folder/(model+'-parent.json'))
        image = read(folder/(model+'-image.json'))['image']
        work = folder/'configs'/model
        created = mutation(work,'create',['run-configs','create-version',
                           '--run-config-id',parent['runConfigId'],
                           '--parent-run-config-version-id',parent['id']])
        vid = created['id']
        stages = ([('plain',plain[0])] if plain else []) + ([('research',research[0])] if research else [])
        for kind, row in stages:
            task = Path(row['snapshot'])
            workbook = next((task/'environment').glob(row['task_id']+'-inputs.*')).name
            marker = f'FCP_GENERATION_{model.upper()}_{kind.upper()}_READY'
            prompt = f'Confirm {workbook} exists in the working directory. '
            if (task/'environment/additional-assumptions.md').exists():
                prompt += 'Also confirm additional-assumptions.md exists. '
            if kind == 'research':
                tool = 'research_list_sources' if model == 'gemini' else 'research list_sources'
                prompt += f'Call the {tool} MCP tool once with kind empty and limit 1. '
            prompt += f'Then respond exactly {marker}. Do not modify any file.'
            config = copy.deepcopy(parent['config'])
            config.update(harnessImageUrl=image,timeoutSeconds=10800)
            config = policy.enforce(config)
            payload = {'config':config,'probe':{'prompt':prompt,'files':[],
                       'qualityCheck': f'PASS only if the transcript confirms the requested files exist, '
                                       f'{"a successful research MCP tool result, " if kind == "research" else ""}'
                                       f'and ends with {marker}. Fail on authentication, model routing, or runtime errors.'},
                       'notes': f'FCP generation campaign {campaign["id"]}; {model}; probe={kind}; discrete pass@3.'}
            mutation(work,kind+'-update',['run-config-versions','update','--run-config-version-id',vid],payload)
            probe = mutation(work,kind+'-probe',['run-config-versions','probe','--run-config-version-id',vid])
            result = cli('probe-runs','get','--run-config-probe-run-id',probe['probeRunId'])
            save(work/(kind+'-probe-status.json'),result)
            if result['status'] != 'succeeded':
                print(model,kind,'probe',result['status'],flush=True)
                # Leave the next stage untouched while this probe runs.
                break
        else:
            mutation(work,'lock',['run-config-versions','lock','--run-config-version-id',vid])
            result=cli('run-config-versions','get','--run-config-version-id',vid)
            assert result['lockedAt'] and result['verifiedAt']
            policy.validate(result['config'])
            save(work/'verified.json',result)
            print(model,'verified and locked',flush=True)


def launch(folder, models=policy.MODELS):
    policy.validate_models(models)
    if CONFIG.get('concurrencyPerModel', 12) > 75:
        from recursion_parallel import launch as parallel_launch
        return parallel_launch(folder, models)
    campaign = read(folder/'campaign.json')
    models = tuple(m for m in ('opus', 'gemini') if m in models)
    assert models
    receipts = campaign.setdefault('evaluations', [])
    covered = {m for receipt in receipts for m in receipt['models']}
    if covered.intersection(models):
        if set(models) <= covered:
            print('Requested models already submitted; no duplicate launch.')
            return
        raise RuntimeError('Request only the remaining uncovered model to avoid duplicate attempts')
    receipt_name = 'evaluation' if len(models) == 2 else 'evaluation-'+models[0]
    indexed = {r['receipt'] for r in receipts}
    for intent in folder.glob('evaluation*.intent.json'):
        name = intent.name.removesuffix('.intent.json')
        if name not in indexed and name != receipt_name:
            raise RuntimeError(f'Reconcile unindexed evaluation receipt before changing model selection: {intent}')
    solvers=[]
    for model in models:
        config=read(folder/'configs'/model/'verified.json')
        assert config['lockedAt'] and config['verifiedAt']
        assert config['config']['harnessImageUrl'] == read(folder/(model+'-image.json'))['image']
        if model == 'opus':
            policy.validate(config['config'])
        else:
            assert config['config']['envVars']['FCP_OPENCODE_MODEL'] == 'google-vertex/gemini-3.8-flash'
            assert config['config']['envVars']['LLM_MODEL'] == 'vertex_ai/gemini-3.8-flash'
        solvers.append({'displayName':'Opus 5 / Claude Code' if model=='opus' else 'Gemini 3.8 Flash / OpenCode',
                        'runConfigVersionId':config['id'],'concurrency':12})
    problems=[]
    for row in campaign['tasks']:
        version=read(folder/'problems'/row['task_id']/'verified.json')
        assert version['lockedAt'] and row['generation_id'] in version['gradingConfig']['command']
        problems.append({'problemId':version['problemId'],'problemVersionId':version['id'],
                         'environmentId':ENVIRONMENT})
    model_names = ' vs '.join(s['displayName'] for s in solvers)
    payload={'name':f'FCP current generations {folder.name} — {len(problems)} tasks — {model_names} pass@3',
             'description':'Exact task generations; discrete full-answer pass criterion; three attempts per solver.',
             'metadata':campaign['metadata'],'tags':['fcp','generation-campaign','pass-at-3',folder.name],
             'solvers':solvers,'problems':problems}
    result=mutation(folder,receipt_name,['evaluations','bulk-create'],payload)
    assert result['nAttemptsPerProblem']==3 and result['totalSolvers']==len(models)
    receipts.append({'id':result['id'], 'models':list(models), 'receipt':receipt_name})
    campaign.update(status='submitted' if covered.union(models)>=set(policy.MODELS) else 'partially_submitted')
    save(folder/'campaign.json',campaign)
    print(json.dumps({'evaluationId':result['id'],'models':models,'tasks':len(problems),'independentRuns':len(problems)*3*len(models)}))


def summarize_results(results):
    """Only completed, scored triples count toward final empirical pass@3."""
    solvers = {}
    seen = set()
    for cell in results['cells']:
        identity = cell['solverId'], cell['problemVersionId']
        assert identity not in seen, 'Duplicate result cell'
        seen.add(identity)
        attempts = cell['attempts']
        numbers = [a['attemptNumber'] for a in attempts]
        assert len(numbers) == len(set(numbers)) and set(numbers) <= {1, 2, 3}
        assert cell['totalRuns'] == 3
        summary = solvers.setdefault(cell['solverId'], {'taskCount': 0, 'completeTriples': 0,
            'passingTriples': 0, 'failedTriples': 0, 'runStatuses': Counter(),
            'infrastructureRetries': 0, 'erroredRuns': [], 'passAt3': None})
        summary['taskCount'] += 1
        summary['runStatuses'].update(a['status'] for a in attempts)
        summary['infrastructureRetries'] += sum(a.get('infraRetryCount', 0) for a in attempts)
        summary['erroredRuns'].extend({'problemRunId': a['problemRunId'],
            'status': a['status'], 'errorMessage': a.get('errorMessage')}
            for a in attempts if a['status'] in ('failed', 'cancelled'))
        if len(attempts) == 3 and all(a['status'] == 'completed' and a.get('score') is not None for a in attempts):
            assert all(a['score'] in (0, 1) for a in attempts), 'Expected discrete grader scores'
            summary['completeTriples'] += 1
            summary['passingTriples' if any(a['score'] == 1 for a in attempts) else 'failedTriples'] += 1
    for summary in solvers.values():
        if summary['completeTriples'] == summary['taskCount']:
            summary['passAt3'] = summary['passingTriples'] / summary['taskCount']
    return solvers


def monitor(folder):
    campaign = read(folder/'campaign.json')
    expected_versions = {read(folder/'problems'/r['task_id']/'verified.json')['id'] for r in campaign['tasks']}
    batches = []
    reporting_models = {m for receipt in campaign['evaluations'] for m in receipt['models']} or set(policy.MODELS)
    covered = {m: set() for m in reporting_models}
    aggregate = {}
    for receipt in campaign['evaluations']:
        eid = receipt['id']
        evaluation = cli('evaluations', 'get', '--evaluation-id', eid)
        results = cli('evaluations', 'get-results', '--evaluation-id', eid)
        save(folder/(receipt['receipt']+'-status.json'), evaluation)
        save(folder/(receipt['receipt']+'-results.json'), results)
        batch_versions = set(receipt.get('problemVersionIds', expected_versions))
        assert batch_versions <= expected_versions
        assert len(results['cells']) == len(batch_versions) * len(receipt['models'])
        assert {c['problemVersionId'] for c in results['cells']} == batch_versions
        solvers = summarize_results(results)
        assert len(solvers) == len(receipt['models']) and all(s['taskCount'] == len(batch_versions) for s in solvers.values())
        batches.append({'evaluationId': eid, 'models':receipt['models'], 'solvers':solvers,
                        'complete':all(s['passAt3'] is not None for s in solvers.values())})
        for model in receipt['models']:
            assert not covered[model].intersection(batch_versions), 'Overlapping task/model evaluations'
            covered[model].update(batch_versions)
        if len(receipt['models']) == 1:
            model = receipt['models'][0]
            total = aggregate.setdefault(model, {'taskCount': 0, 'completeTriples': 0,
                'passingTriples': 0, 'failedTriples': 0, 'runStatuses': Counter(),
                'infrastructureRetries': 0, 'erroredRuns': [], 'passAt3': None})
            for solver in solvers.values():
                for key in ('taskCount', 'completeTriples', 'passingTriples', 'failedTriples', 'infrastructureRetries'):
                    total[key] += solver[key]
                total['runStatuses'].update(solver['runStatuses'])
                total['erroredRuns'].extend(solver['erroredRuns'])
    for total in aggregate.values():
        if total['taskCount'] == len(expected_versions) and total['completeTriples'] == total['taskCount']:
            total['passAt3'] = total['passingTriples'] / total['taskCount']
    summary = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'expectedRuns':len(expected_versions)*3*len(reporting_models),
               'submittedRuns':3*sum(len(v) for v in covered.values()), 'evaluations':batches,
               'models':aggregate,
               'complete':all(v == expected_versions for v in covered.values()) and all(b['complete'] for b in batches)}
    save(folder/'results-summary.json', summary)
    if summary['complete']:
        campaign['status'] = 'completed'
        save(folder/'campaign.json', campaign)
    print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','build','provision','configure','launch','monitor'])
    parser.add_argument('--campaign',type=Path)
    parser.add_argument('--models',nargs='+',choices=['opus'],default=['opus'])
    args=parser.parse_args()
    REPORTS.mkdir(exist_ok=True)
    with (REPORTS/'campaign.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='init':init_campaign()
        else:
            if args.campaign is None:parser.error('--campaign is required')
            if args.action == 'launch':launch(args.campaign.resolve(), args.models)
            else:globals()[args.action](args.campaign.resolve())
