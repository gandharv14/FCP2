"""Independent 20-task Gemini research-isolation before/after pilot.

Never writes the parent audit campaign. Reuses its verified isolated runtime,
preserves each historical grader byte-for-byte, and freezes selection before runs.
"""
from __future__ import annotations
import argparse
import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import time

import recursion_campaign as c
import recursion_hardening_campaign as h
import recursion_hardening_revisions as revisions
from recursion_hardening_results_fallback import matrix_index

ROOT = Path(__file__).resolve().parents[1]
AUDIT = h.AUDIT
PARENT = h.WORK
WORK = ROOT / 'reports/recursion/research-isolation-20-20260915'
SEED = 'research-isolation-20-20260915-v1'
CONFIG_PATH = PARENT / 'content-successor/images/configs/gemini/verified.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalized_prompt(prompt):
    return prompt.replace('http://mcp-server:8000/mcp', 'http://127.0.0.1:8000/mcp').replace('/app/answers.json', '/workspace/answers.json')


def source_version(row):
    return next(v for v in c.read(AUDIT / 'versions' / (row['problemId'] + '.json'))
                if v['id'] == row['problemVersionId'])


def prepare():
    if (WORK / 'plan.json').exists():
        return validate()
    WORK.mkdir(parents=True, mode=0o700)
    first = c.read(AUDIT / 'audit-1/per-run-evidence.json')
    second = c.read(AUDIT / 'shard-2-run-audit.json')
    third = c.read(AUDIT / 'shard-3-audit.json')['runs']
    bypass = {r['id'] for r in first if any(f['code'] == 'RUNTIME_CORPUS_BYPASS' for f in r['findings'])}
    bypass |= {r['runId'] for r in second if r.get('confirmedLocalResearchReads')}
    bypass |= {r['run_id'] for r in third if r.get('source_or_corpus_shortcut_observed')}
    index = {r['taskId']: r for r in c.read(PARENT / 'content-successor/REPAIR-INDEX.json')['tasks']}
    final = {r['taskId']: r for r in c.read(PARENT / 'final-assets.json')}
    candidates = []
    for asset in c.read(PARENT / 'selected-assets.json'):
        repair = index[asset['taskId']]
        if (not asset['researchRoot'] or repair['initialWorkbookRepair'] or
                repair['depthReview'] or repair['successorRequired']):
            continue
        history = c.read(AUDIT / 'runs' / (asset['problemId'] + '.json'))
        assert not history['truncated']
        runs = [r for r in history['items'] if r.get('problemVersionId') == asset['problemVersionId']
                and (r.get('apiModelName') or '').endswith('gemini-3.8-flash')]
        if len(runs) != 3 or not all(r['status'] == 'completed' and r.get('finalScore') is not None for r in runs):
            continue
        if not any(r['id'] in bypass for r in runs):
            continue
        source = source_version(asset)
        assert source['timeoutSeconds'] == 10800 and source['containerSize'] == 'medium'
        assert source['privileged'] is False
        assert '--mode continuous' in source['gradingConfig']['command']
        assert '--workspace /workspace' in source['gradingConfig']['command']
        assert asset['workbookSha256'] == final[asset['taskId']]['workbookSha256']
        candidates.append({
            'taskId': asset['taskId'], 'problemId': asset['problemId'],
            'problemVersionId': asset['problemVersionId'],
            'sourceVersionSha256': canonical(source),
            'gradingConfigSha256': canonical(source['gradingConfig']),
            'workbookPath': asset['workbookPath'], 'workbookSha256': asset['workbookSha256'],
            'researchFiles': final[asset['taskId']]['researchFiles'],
            'baseline': [{k: r[k] for k in ('id', 'problemVersionId', 'solverRunConfigVersionId',
                         'apiModelName', 'finalScore', 'status', 'createdAt')} for r in sorted(runs, key=lambda r: (r['createdAt'], r['id']))],
            'documentedBypassRunIds': [r['id'] for r in runs if r['id'] in bypass],
            'selectionHash': hashlib.sha256((SEED + ':' + asset['taskId']).encode()).hexdigest(),
        })
    assert len(candidates) >= 20
    chosen = sorted(candidates, key=lambda r: r['selectionHash'])[:20]
    cfg = c.read(CONFIG_PATH)
    assert cfg['lockedAt'] and cfg['verifiedAt']
    assert cfg['config']['envVars']['FCP_OPENCODE_MODEL'] == 'google-vertex/gemini-3.8-flash'
    pinned = [PARENT / 'runtime/overlay-manifest.json', CONFIG_PATH,
              PARENT / 'content-successor/images/runtime-manifest.json',
              PARENT / 'content-successor/images/payload-proof.json']
    overlay = c.read(pinned[0])
    pinned.extend(Path(f['source']) for f in overlay['files'])
    plan = {'schemaVersion': 1, 'createdAt': datetime.now(timezone.utc).isoformat(),
            'selectionSeed': SEED, 'eligibleTasks': len(candidates), 'taskCount': 20,
            'attemptsPerTask': 3, 'model': 'Gemini 3.8 Flash', 'solverRunConfigVersionId': cfg['id'],
            'image': cfg['config']['harnessImageUrl'], 'sourceArtifacts': {str(p): digest(p) for p in pinned},
            'selectionRule': 'Lowest SHA256(seed:taskId), from tasks with documented research bypass, exactly three completed same-version Gemini attempts, and no workbook, depth or prompt repairs.',
            'primaryMetric': 'Mean continuous score, averaged across three attempts per task, paired over 20 tasks.',
            'limitations': ['Historical before/after comparison, not randomized concurrent control; selection requires historical high-score/bypass evidence.',
                           'Runtime includes privilege, credential, network and model-pinning hardening; the result cannot isolate filesystem access from every associated runtime change.',
                           'No workbook, answer key, tolerance or grader changes. Prompt changes only the local research endpoint and answer output path.',
                           'Backend failures remain unscored. No selective extra attempts or task substitutions.'],
            'tasks': chosen}
    c.save(WORK / 'eligible-task-ids.json', sorted(r['taskId'] for r in candidates))
    c.save(WORK / 'plan.json', plan)
    (WORK / 'plan.sha256').write_text(digest(WORK / 'plan.json') + '\n')
    write_report(plan, None)
    print(json.dumps({'eligibleTasks': len(candidates), 'selectedTasks': [r['taskId'] for r in chosen],
                      'baselineMeanScore': statistics.mean(r['finalScore'] for t in chosen for r in t['baseline'])}))
    return plan


def validate():
    assert digest(WORK / 'plan.json') == (WORK / 'plan.sha256').read_text().strip()
    plan = c.read(WORK / 'plan.json')
    assert len(plan['tasks']) == len({r['taskId'] for r in plan['tasks']}) == 20
    for path, expected in plan['sourceArtifacts'].items():
        assert digest(path) == expected, 'Pinned runtime changed: ' + path
    for row in plan['tasks']:
        assert digest(row['workbookPath']) == row['workbookSha256']
        for path, expected in row['researchFiles'].items():
            assert digest(path) == expected
        assert canonical(source_version(row)) == row['sourceVersionSha256']
    return plan


def provision():
    plan = validate()
    cfg = c.cli('run-config-versions', 'get', '--run-config-version-id', plan['solverRunConfigVersionId'])
    assert cfg == c.read(CONFIG_PATH), 'Verified runtime configuration changed'
    def one(row):
        folder = WORK / 'versions' / row['taskId']
        source = source_version(row)
        created = c.mutation(folder, 'create', ['problem-versions', 'create', '--problem-id', row['problemId'],
                                             '--copy-from-version-id', row['problemVersionId']])
        vid = created['id']
        payload = {'prompt': normalized_prompt(source['prompt']),
                   'solverRunConfigVersionId': plan['solverRunConfigVersionId']}
        c.mutation(folder, 'update', ['problem-versions', 'update', '--problem-version-id', vid], payload)
        c.mutation(folder, 'lock', ['problem-versions', 'lock', '--problem-version-id', vid])
        actual = c.cli('problem-versions', 'get', '--problem-version-id', vid)
        allowed_changes = {'id', 'version', 'createdAt', 'updatedAt', 'lockedAt', 'prompt', 'solverRunConfigVersionId'}
        assert actual['lockedAt'] and actual['prompt'] == payload['prompt']
        assert actual['solverRunConfigVersionId'] == plan['solverRunConfigVersionId']
        assert {k: v for k, v in actual.items() if k not in allowed_changes} == {k: v for k, v in source.items() if k not in allowed_changes}
        assert canonical(actual['gradingConfig']) == row['gradingConfigSha256']
        c.save(folder / 'verified.json', actual)
        print(json.dumps({'taskId': row['taskId'], 'versionId': vid, 'originalGraderPreserved': True}), flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, plan['tasks']))


def capacity():
    items, cursor, seen = [], None, set()
    while True:
        args = ['evaluations', 'list', '--limit', '100', '--sort-by', 'createdAt', '--sort-direction', 'desc']
        if cursor:
            args += ['--cursor', cursor]
        page = h.read_evaluation(*args)
        items.extend(page['items'])
        cursor = page.get('nextCursor')
        if not cursor:
            break
        assert cursor not in seen
        seen.add(cursor)
    assert len({x['id'] for x in items}) == len(items)
    active = [x for x in items if x['status'] not in ('completed', 'failed', 'cancelled', 'canceled')]
    def one(item):
        detail = h.read_evaluation('evaluations', 'get', '--evaluation-id', item['id'])
        if any(s['jobStatus'] == 'partial' for s in detail['solvers']) and not detail.get('completedAt'):
            detail['_terminalEvidenceMatrix'] = h.read_evaluation('evaluations', 'get-results', '--evaluation-id', item['id'])
        return detail
    with ThreadPoolExecutor(max_workers=4) as pool:
        details = list(pool.map(one, active))
    result = h.capacity_summary(details, revisions.known_configs(), c.CONFIG['concurrencyPerModel'], 0)
    result['checkedAt'] = datetime.now(timezone.utc).isoformat()
    c.save(WORK / 'capacity.json', result)
    return result


def launch():
    plan = validate()
    if (WORK / 'evaluation-1.response.json').exists():
        return c.read(WORK / 'evaluation-1.response.json')
    assert not (WORK / 'evaluation-1.intent.json').exists(), 'Reconcile uncertain submission; never blindly retry'
    snapshot = capacity()
    available = snapshot['availableConcurrencyByModel']['gemini']
    if available < 10:
        print(json.dumps({'status': 'waiting_for_capacity', 'availableGeminiConcurrency': available}), flush=True)
        return None
    concurrency = min(20, available)
    versions = [c.read(WORK / 'versions' / r['taskId'] / 'verified.json') for r in plan['tasks']]
    for row, version in zip(plan['tasks'], versions):
        assert version['lockedAt'] and version['problemId'] == row['problemId']
        assert canonical(version['gradingConfig']) == row['gradingConfigSha256']
        assert version['prompt'] == normalized_prompt(source_version(row)['prompt'])
        assert version['solverRunConfigVersionId'] == plan['solverRunConfigVersionId']
    request = {'name': 'Research isolation pilot — 20 matched tasks — Gemini 3.8 Flash',
               'description': 'User-requested research-scaffolding check. Original continuous graders and task content preserved; isolated research runtime; three fresh attempts per task. Historical paired baseline is frozen before dispatch.',
               'metadata': {'schemaVersion': 1, 'attemptsPerProblem': 3, 'successThreshold': 1, 'retryPolicy': {'maxInfraRetries': 2}},
               'tags': ['research-isolation-20-20260915'],
               'solvers': [{'displayName': 'Gemini 3.8 Flash — isolated research',
                            'runConfigVersionId': plan['solverRunConfigVersionId'], 'concurrency': concurrency}],
               'problems': [{'problemId': v['problemId'], 'problemVersionId': v['id'], 'environmentId': v['environmentId']} for v in versions]}
    result = c.mutation(WORK, 'evaluation-1', ['evaluations', 'bulk-create'], request)
    print(json.dumps({'evaluationId': result['id'], 'tasks': 20, 'attempts': 60, 'concurrency': concurrency}), flush=True)
    return result


def paired_statistics(differences):
    assert len(differences) == 20
    rng = random.Random(SEED)
    bootstrap = sorted(statistics.mean(rng.choices(differences, k=20)) for _ in range(10000))
    target = abs(sum(differences))
    count, total, previous_gray = 0, -sum(differences), 0
    for i in range(1 << 20):
        if i:
            gray = i ^ (i >> 1)
            bit = (gray ^ previous_gray).bit_length() - 1
            total += (2 if gray & (1 << bit) else -2) * differences[bit]
            previous_gray = gray
        count += abs(total) >= target - 1e-10
    return {'meanDifference': statistics.mean(differences),
            'pairedBootstrap95Interval': [bootstrap[249], bootstrap[9749]],
            'twoSidedPairedSignFlipP': count / (1 << 20),
            'method': 'Task is the paired unit (n=20). Percentile bootstrap 10000 draws; exact 2^20 sign flips under symmetric paired differences. Exploratory historical comparison; not a causal randomized trial.'}


def perfect_at_three(rows):
    """Secondary metric using the evaluation's frozen score-1 success threshold.

    An unscored attempt cannot change a task that already has a score of 1.
    It remains unknown when the other attempts do not establish a pass.
    """
    paired = []
    for row in rows:
        valid = [a for a in row['attempts'] if a['status'] == 'completed' and a.get('score') is not None
                 and not a.get('errorMessage') and not a.get('gradingError')]
        before = any(s == 1 for s in row['baselineScores'])
        after = True if any(a['score'] == 1 for a in valid) else False if len(valid) == 3 else None
        paired.append({'taskId': row['taskId'], 'before': before, 'after': after})
    passed = sum(r['after'] is True for r in paired)
    unknown = sum(r['after'] is None for r in paired)
    result = {'metric': 'Secondary: at least one of three attempts scores 1.0 under the unchanged original grader.',
              'tasks': len(rows), 'baselinePassed': sum(r['before'] for r in paired),
              'newPassed': passed, 'unknownTaskOutcomes': unknown,
              'newRateBounds': [passed / len(rows), (passed + unknown) / len(rows)], 'pairedTasks': paired,
              'complete': unknown == 0, 'statistics': None}
    if not unknown:
        result['statistics'] = paired_statistics([int(r['after']) - int(r['before']) for r in paired])
    return result


def collect():
    plan = validate()
    receipt = c.read(WORK / 'evaluation-1.response.json')
    detail = h.read_evaluation('evaluations', 'get', '--evaluation-id', receipt['id'])
    matrix = h.read_evaluation('evaluations', 'get-results', '--evaluation-id', receipt['id'])
    slots = matrix_index(detail, matrix)
    assert len(slots) == 60 and len(detail['solvers']) == 1
    assert detail['solvers'][0]['runConfigVersionId'] == plan['solverRunConfigVersionId']
    byversion = {c.read(WORK / 'versions' / r['taskId'] / 'verified.json')['id']: r for r in plan['tasks']}
    assert {x['problemVersionId'] for x in matrix['cells']} == set(byversion)
    rows = []
    for cell in matrix['cells']:
        row = byversion[cell['problemVersionId']]
        attempts = sorted(cell['attempts'], key=lambda a: a['attemptNumber'])
        for a in attempts:
            assert a.get('score') is None or 0 <= a['score'] <= 1
        valid = [a for a in attempts if a['status'] == 'completed' and a.get('score') is not None and not a.get('errorMessage') and not a.get('gradingError')]
        rows.append({'taskId': row['taskId'], 'baselineMean': statistics.mean(a['finalScore'] for a in row['baseline']),
                     'baselineScores': [a['finalScore'] for a in row['baseline']],
                     'newMean': statistics.mean(a['score'] for a in valid) if len(valid) == 3 else None,
                     'newMeanBounds': [sum(a['score'] for a in valid) / 3,
                                       (sum(a['score'] for a in valid) + 3 - len(valid)) / 3],
                     'validAttempts': len(valid), 'attempts': attempts})
    rows.sort(key=lambda r: r['taskId'])
    complete = all(r['newMean'] is not None for r in rows)
    terminal = all(a['status'] in ('completed', 'failed', 'cancelled') for r in rows for a in r['attempts'])
    summary = {'collectedAt': datetime.now(timezone.utc).isoformat(), 'evaluationId': receipt['id'],
               'tasks': 20, 'expectedAttempts': 60, 'scoredAttempts': sum(r['validAttempts'] for r in rows),
               'terminalAttempts': sum(a['status'] in ('completed', 'failed', 'cancelled') for r in rows for a in r['attempts']),
               'complete': complete, 'allTerminal': terminal, 'baselineMean': statistics.mean(r['baselineMean'] for r in rows),
               'newMean': statistics.mean(r['newMean'] for r in rows) if complete else None,
               'statistics': paired_statistics([r['newMean'] - r['baselineMean'] for r in rows]) if complete else None}
    summary['newMeanBounds'] = [statistics.mean(r['newMeanBounds'][i] for r in rows) for i in (0, 1)]
    summary['meanDifferenceBounds'] = [v - summary['baselineMean'] for v in summary['newMeanBounds']]
    summary['perfectScoreAt3'] = perfect_at_three(rows)
    for filename, value in [('detail.json', detail), ('matrix.json', matrix), ('task-results.json', rows), ('summary.json', summary)]:
        c.save(WORK / filename, value)
    write_report(plan, summary, rows)
    print(json.dumps(summary), flush=True)
    return summary


def monitor():
    """Foreground-job supervisor; never touches another campaign's files or jobs."""
    with (WORK / 'monitor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for _ in range(120):
            try:
                if not (WORK / 'evaluation-1.response.json').exists():
                    # The launch function fails closed if a prior request is uncertain.
                    launch()
                if (WORK / 'evaluation-1.response.json').exists():
                    summary = collect()
                    if summary['allTerminal']:
                        return summary
            except RuntimeError as error:
                # Network failures after mutation dispatch are ambiguous. Never
                # retry a submission just because its response was not received.
                uncertain = (WORK / 'evaluation-1.intent.json').exists() and not (WORK / 'evaluation-1.response.json').exists()
                transient = any(s in str(error) for s in ('fetch failed', 'Internal server error',
                    'Service Unavailable', 'Gateway', 'timed out', 'ECONNRESET', 'ETIMEDOUT'))
                if uncertain or not transient:
                    raise
                print(json.dumps({'event': 'transient_read_failure',
                                  'evaluationId': c.read(WORK / 'evaluation-1.response.json')['id'] if (WORK / 'evaluation-1.response.json').exists() else None,
                                  'action': 'retry_read_on_next_monitor_cycle'}), flush=True)
            for _ in range(36):
                time.sleep(5)
        return {'status': 'monitor_window_ended'}


def write_report(plan, summary, rows=None):
    lines = ['# Research isolation: 20-task Gemini pilot', '',
             'The verified scaffold keeps research data and server code private to the trusted service and runs model tools under an unprivileged UID. Research remains available through MCP.', '',
             '## Frozen comparison', '',
             f"Selected 20 of {plan['eligibleTasks']} eligible tasks by a fixed hash ranking. Each has three completed historical Gemini 3.8 Flash attempts and documented research-file bypass. Each receives three new attempts.", '',
             f"Direct-file bypass was documented in {sum(len(r['documentedBypassRunIds']) for r in plan['tasks'])} of the 60 historical attempts. All three historical attempts per task are retained in the baseline, including attempts without observed bypass.", '',
             'Workbooks, research content, answer keys, tolerances, resource budgets and original continuous graders are preserved. Only the research endpoint/output path are normalized for the existing isolated runtime. No change is selected based on new scores.', '',
             '## Interpretation limits', '']
    lines += ['- ' + limit for limit in plan['limitations']]
    lines += ['', 'The requested reduction is a hypothesis. Statistical comparisons use 20 paired tasks, not 60 independent tasks. '
              'Selecting historically high-scoring tasks can produce regression toward the mean on rerun, even without a capability change.', '', '## Results', '']
    if summary is None:
        lines += ['Prepared; fresh evaluation results pending.', '']
    else:
        lines += [f"Retrieved {summary['collectedAt']}; {summary['scoredAttempts']}/60 scored attempts; {summary['terminalAttempts']}/60 terminal.", '']
        if summary['complete']:
            stats = summary['statistics']
            lines += [f"Historical mean score: **{100*summary['baselineMean']:.2f}%**. New mean: **{100*summary['newMean']:.2f}%**.", '',
                      f"New minus historical: **{100*stats['meanDifference']:+.2f} percentage points**; paired bootstrap 95% interval **{100*stats['pairedBootstrap95Interval'][0]:+.2f} to {100*stats['pairedBootstrap95Interval'][1]:+.2f} points**; exploratory two-sided sign-flip p = **{stats['twoSidedPairedSignFlipP']:.5g}**.", '']
        elif summary['allTerminal'] and 'newMeanBounds' in summary:
            lo, hi = summary['newMeanBounds']
            dlo, dhi = summary['meanDifferenceBounds']
            lines += [f"All attempts ended; {summary['scoredAttempts']}/60 returned valid scores. Historical mean: **{100*summary['baselineMean']:.2f}%**. The possible new mean is **{100*lo:.2f}%–{100*hi:.2f}%**; the change from baseline is **{100*dlo:+.2f} to {100*dhi:+.2f} percentage points**.", '',
                      'These are worst-case outcome bounds, not confidence intervals. A statistical significance claim is withheld while outcomes remain unknown.', '']
        else:
            lines += ['Final comparison pending. Unscored attempts are not silently treated as zero.', '']
        secondary = summary.get('perfectScoreAt3')
        if secondary and secondary['complete']:
            lines += ['### Secondary: pass@3 at the original score-1 threshold', '',
                      f"Historical: **{secondary['baselinePassed']}/20 ({5*secondary['baselinePassed']:.1f}%)**. New: **{secondary['newPassed']}/20 ({5*secondary['newPassed']:.1f}%)**. Exploratory exact paired sign-flip p = **{secondary['statistics']['twoSidedPairedSignFlipP']:.5g}**.", '',
                      'This task-level metric can be fully determined despite an unscored attempt when another attempt on that task already scored 1.0. It does not fill in the missing continuous score or replace the primary mean-score comparison.', '']
    lines += ['| Task | Historical mean | New mean |', '|---|---:|---:|']
    results = {r['taskId']: r for r in (rows or [])}
    for row in sorted(plan['tasks'], key=lambda r: r['taskId']):
        baseline = statistics.mean(a['finalScore'] for a in row['baseline'])
        result = results.get(row['taskId'], {})
        new = result.get('newMean')
        displayed = 'Pending' if new is None else f'{100*new:.2f}%'
        if new is None and result.get('attempts') and all(a['status'] in ('completed', 'failed', 'cancelled') for a in result['attempts']):
            lo, hi = result['newMeanBounds']
            displayed = f'{100*lo:.2f}%–{100*hi:.2f}% (unscored attempt)'
        lines.append(f"| {row['taskId']} | {100*baseline:.2f}% | {displayed} |")
    diagnoses = sorted((WORK / 'failure-review').glob('*/diagnosis.json'))
    if diagnoses:
        lines += ['', '## Unscored-attempt evidence', '']
        for path in diagnoses:
            diagnosis = c.read(path)
            lines.append(f"- Task {diagnosis['taskId']}, attempt {diagnosis['attemptNumber']}: [{diagnosis['classification']}]({path.relative_to(WORK).as_posix()}). No backend score was imputed.")
    lines += ['', '[Scaffolding fix and verification](scaffolding.md) · [Frozen selection and provenance](plan.json) · [Task results](task-results.json) · [Summary](summary.json)', '']
    (WORK / 'README.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'provision', 'launch', 'collect', 'capacity', 'monitor'])
    args = parser.parse_args()
    result = globals()[args.action]()
    if args.action == 'capacity':
        print(json.dumps(result))
