"""Consolidate reviewed tasks without changing task definitions or rewards."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from recursion_reward_extremes_audit import AUDIT


LABELS = {
    'confirmed_excess_information': 'Excess information / shortcut',
    'confirmed_missing_information': 'Missing or ambiguous information',
    'confirmed_source_or_grading_defect': 'Source / grading defect',
    'model_or_execution_error_supported': 'Model / execution error supported',
    'capability_without_confirmed_shortcut': 'Capability; no confirmed shortcut',
    'unresolved_or_mixed': 'Unresolved / mixed evidence',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percent(value):
    return 'Unscored attempts' if value is None else f'{100 * value:.2f}%'


def plain(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def evidence_links(value):
    """Only link local evidence explicitly identified by the reviewer."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and ('path' in key.lower()) and Path(item).is_file():
                if '.private.' not in item and not item.endswith('.gz'):
                    found.add(item)
            elif isinstance(item, (dict, list)):
                found.update(evidence_links(item))
    elif isinstance(value, list):
        for item in value:
            found.update(evidence_links(item))
    return found


def render():
    selection = json.loads((AUDIT / 'selection.json').read_text())
    verified = json.loads((AUDIT / 'collection-verification.json').read_text())
    assert verified['uniqueRollouts'] == 3216 and not verified['sequenceIssues']
    expected = {r['taskId']: r for n in (1, 2, 3)
                for r in json.loads((AUDIT / f'shard-{n}.json').read_text())}
    tasks = []
    seen_tasks, seen_runs = set(), set()
    report_pins = {}
    for n in (1, 2, 3):
        path = AUDIT / f'shard-{n}-audit.json'
        report = json.loads(path.read_text())
        report_pins[str(path)] = sha(path)
        if isinstance(report.get('tasks'), list):
            reviewed_tasks = report['tasks']
        else:
            reviewed_tasks = []
            for record in report['taskRecords']:
                assert sha(record['path']) == record['sha256'], record['path']
                reviewed_tasks.append({**json.loads(Path(record['path']).read_text()),
                                       'taskEvidencePath': record['path']})
        for task in reviewed_tasks:
            tid = task['taskId']
            assert tid not in seen_tasks and tid in expected
            seen_tasks.add(tid)
            original = expected[tid]
            version = task.get('activeProblemVersionId', task.get('problemVersionId', task.get('activeVersionId')))
            assert version == original['problemVersionId'], ('version', tid)
            assert task['meanBackendReward'] == original['meanBackendReward'], ('reward', tid)
            actual_runs = {a['problemRunId']: (model, a)
                           for model, attempts in original['models'].items() for a in attempts}
            coverage = task['rolloutCoverage']
            assert len(coverage) == 6
            task_runs = set()
            for run in coverage:
                run_id = run.get('problemRunId', run.get('runId'))
                assert run_id in actual_runs and run_id not in seen_runs
                model, actual = actual_runs[run_id]
                assert run['model'] == model and run['attemptNumber'] == actual['attemptNumber']
                seen_runs.add(run_id)
                task_runs.add(run_id)
            assert task_runs == set(actual_runs)
            assert task.get('narrative') and isinstance(task['findings'], list)
            tasks.append({**task, 'reviewShard': n})
    assert seen_tasks == set(selection['taskIds']) and len(seen_runs) == 3216
    assert not seen_tasks.intersection(selection['excludedOpus100Gemini0'])
    tasks.sort(key=lambda r: r['taskId'])
    counts = Counter(r['assessment'] for r in tasks)
    out = {'createdAt': datetime.now(timezone.utc).isoformat(),
           'status': 'complete_bounded_trajectory_audit',
           'tasksAudited': 536, 'rolloutsAudited': 3216, 'eventsScreened': verified['eventCount'],
           'excludedOpus100Gemini0': 15, 'assessmentCounts': dict(counts),
           'reviewReportHashes': report_pins, 'tasks': tasks}
    (AUDIT / 'task-audit-ledger.json').write_text(json.dumps(out, indent=2) + '\n')
    folder = AUDIT / 'tasks'
    folder.mkdir(exist_ok=True)
    matrix = ['# Task-by-task trajectory audit', '',
              'Selected when either fully scored mean reward is at least 90% or exactly 0%; '
              'Opus 100% / Gemini 0% tasks are excluded. Unscored means are not imputed. '
              'Each task links to its six-rollout coverage and evidence.', '',
              'Assessments are bounded by the evidence described in each task. '
              '“No confirmed shortcut” is not a proof of complete task validity.', '',
              '[Audit summary](README.md) · [Structured ledger](task-audit-ledger.json)', '',
              '| Task | Opus mean reward | Gemini mean reward | Assessment | Audit finding |',
              '|---|---:|---:|---|---|']
    for task in tasks:
        tid = task['taskId']
        label = LABELS.get(task['assessment'], task['assessment'].replace('_', ' '))
        mean = task['meanBackendReward']
        matrix.append(f"| [{plain(tid)}](tasks/{tid}.md) | {percent(mean['opus'])} | "
                      f"{percent(mean['gemini'])} | {plain(label)} | {plain(task['narrative'])} |")
        lines = [f'# {tid}', '', f'**Assessment: {label}.**', '',
                 f"Opus mean reward: **{percent(mean['opus'])}**. "
                 f"Gemini mean reward: **{percent(mean['gemini'])}**.", '',
                 task['narrative'], '', '## Findings', '']
        if not task['findings']:
            lines += ['No task defect was confirmed in the reviewed evidence. See the limitations below.', '']
        for i, finding in enumerate(task['findings'], 1):
            description = finding.get('description', finding.get('summary', finding.get('finding', 'See linked reviewer evidence.')))
            lines += [f'### {i}. {finding.get("type", finding.get("findingType", "Finding")).replace("_", " ")}', '', str(description), '']
            if finding.get('confidence'):
                lines += [f"Confidence: {finding['confidence']}.", '']
            recommendation = finding.get('recommendation', finding.get('actionableRecommendation'))
            if recommendation:
                lines += ['**Recommended action:** ' + str(recommendation), '']
            links = sorted(evidence_links(finding))
            if links:
                lines += ['Evidence: ' + ' · '.join(f'[{Path(f).name}](<{f}>)' for f in links), '']
            for limit in finding.get('limitations', []):
                lines.append('- ' + str(limit))
            lines.append('')
        lines += ['## Rollout coverage', '',
                  '| Model | Attempt | Backend reward | Run ID |', '|---|---:|---:|---|']
        for model, attempts in expected[tid]['models'].items():
            for attempt in sorted(attempts, key=lambda a: a['attemptNumber']):
                reward = attempt['finalScore']
                lines.append(f"| {model} | {attempt['attemptNumber']} | "
                             f"{'Unscored' if reward is None else f'{100 * reward:.0f}%'} | {attempt['problemRunId']} |")
        lines += ['', '## Limits and provenance', '']
        for limit in task.get('limitations', []):
            lines.append('- ' + str(limit))
        if task.get('taskEvidencePath'):
            lines += ['', f"[Detailed task evidence](<{task['taskEvidencePath']}>)", '']
        lines += ['', 'All retrieved events received structured screening, with targeted semantic and source review. '
                  'Diagnostic comparisons are not new backend grades.', '',
                  f"[Full shard evidence](../shard-{task['reviewShard']}-audit.json) · "
                  '[Task index](../TASK-AUDIT.md) · [Audit summary](../README.md)', '']
        (folder / (tid + '.md')).write_text('\n'.join(lines))
    (AUDIT / 'TASK-AUDIT.md').write_text('\n'.join(matrix) + '\n')
    print(json.dumps({'tasks': len(tasks), 'rollouts': len(seen_runs), 'assessmentCounts': dict(counts)}))


if __name__ == '__main__':
    # The binary renderer is retained for provenance; current reports use continuous rewards.
    from recursion_continuous_audit_report import render as render_continuous
    render_continuous()
