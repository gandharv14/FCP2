"""Validate every deeper audit resolution before exposing it in the main report."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from recursion_reward_extremes_audit import AUDIT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root = AUDIT / 'deep-review'
    selection = json.loads((root / 'selection.json').read_text())
    rows = {t['taskId']: (n, t) for n in (1, 2, 3)
            for t in json.loads((root / f'batch-{n}.json').read_text())}
    assert set(rows) == set(selection['taskIds'])
    records, paths, statuses, assessments = [], {}, Counter(), Counter()
    pin_cache, pin_issues = {}, []

    def check_pins(value):
        if isinstance(value, dict):
            if isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str):
                path, expected = value['path'], value['sha256']
                if path not in pin_cache:
                    pin_cache[path] = sha(path) if Path(path).is_file() else None
                if pin_cache[path] != expected:
                    pin_issues.append(path)
            for item in value.values():
                check_pins(item)
        elif isinstance(value, list):
            for item in value:
                check_pins(item)

    for tid, (n, original) in sorted(rows.items()):
        path = root / f'batch-{n}' / tid / 'resolution.json'
        assert path.is_file(), ('Resolution missing', tid)
        review = json.loads(path.read_text())
        assert review['taskId'] == tid and review['problemVersionId'] == original['problemVersionId']
        assert review['resolutionStatus'] in ('resolved', 'evidence_blocker'), tid
        assert review.get('summary') and review.get('findings'), tid
        assert review.get('evidence'), ('No evidence pins', tid)
        if review['resolutionStatus'] == 'evidence_blocker':
            assert review.get('specificRemainingBlocker'), tid
        else:
            assert review['assessment'] != 'unresolved_or_mixed', tid
        expected = {a['problemRunId']: (model, a['attemptNumber'])
                    for model, data in original['models'].items() for a in data['attempts']}
        actual = {}
        for run in review['rolloutCoverage']:
            rid = run.get('problemRunId', run.get('runId'))
            assert rid not in actual
            actual[rid] = (run['model'], run.get('attemptNumber', run.get('attempt')))
            trajectory = AUDIT / 'trajectories' / tid / run['model'] / (rid + '.json.gz')
            if run.get('trajectorySha256'):
                check_pins({'path': str(trajectory), 'sha256': run['trajectorySha256']})
            if run.get('rewardEvidencePath') and run.get('rewardEvidenceSha256'):
                check_pins({'path': run['rewardEvidencePath'], 'sha256': run['rewardEvidenceSha256']})
        assert actual == expected and len(actual) == 6, tid
        check_pins(review)
        records.append({'taskId': tid, 'path': str(path), 'sha256': sha(path),
                        'resolutionStatus': review['resolutionStatus'], 'assessment': review['assessment'],
                        'summary': review['summary']})
        paths[tid] = str(path.relative_to(root))
        statuses[review['resolutionStatus']] += 1
        assessments[review['assessment']] += 1
    assert not pin_issues, sorted(set(pin_issues))
    index = {'completedAt': datetime.now(timezone.utc).isoformat(), 'tasksReviewed': len(records),
             'rolloutsCovered': 6 * len(records), 'resolutionStatusCounts': dict(statuses),
             'assessmentCounts': dict(assessments), 'evidencePinsVerified': len(pin_cache),
             'allSelectedCasesAdjudicated': len(records) == len(rows),
             'casesWithExternalEvidenceBlockers': statuses.get('evidence_blocker', 0),
             'sourceSelectionSha256': sha(root / 'selection.json'),
             'taskPaths': paths, 'tasks': records, 'newModelAttempts': 0, 'taskDefinitionChanges': 0}
    (root / 'resolution-index.json').write_text(json.dumps(index, indent=2) + '\n')
    lines = ['# Deeper review of unresolved task audits', '',
             f"**{len(records)} task reviews, {6*len(records)} rollouts.** " +
             ', '.join(f'{v} {k.replace("_", " ")}' for k, v in sorted(statuses.items())) + '.', '',
             'Scope: all unresolved/mixed cases in the corrected continuous-reward cohort at the start of this review. '
             'Prior findings and continuous model rewards are preserved. Local counterfactual calculations are audit diagnostics, '
             'not new model attempts or replacement grades.', '',
             'A resolved assessment identifies an evidence-supported cause within the scope stated in the task record. '
             'Any remaining evidence requirement is stated explicitly; an evidence blocker is not a finding that the task is valid.', '',
             '[Current task report](../TASK-AUDIT.md) · [Full resolution index](resolution-index.json)', '',
             '## Assessment counts', '', '| Assessment | Tasks |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in sorted(assessments.items())]
    lines += ['', '## Findings by task', '', '| Task | Status | Assessment | Conclusion |', '|---|---|---|---|']
    for rec in records:
        summary = rec['summary'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| [{rec['taskId']}]({paths[rec['taskId']]}) | {rec['resolutionStatus']} | "
                     f"{rec['assessment']} | {summary} |")
    lines += ['', '## Independent checks', '',
              '- [Target-level reward-loss census](target-loss-census.json).',
              '- [Synthetic all-zero baseline](all-zero-baseline-diagnostic.json): no selected case reaches 90% using all-zero answers.',
              '- [Original legacy cache bindings](legacy-original-cache-bindings.json) and [recalculated cache bindings](legacy-recalculated-cache-bindings.json).',
              '- [Duplicate-ID lookup counterfactual](root-review/0266-duplicate-lookup.json).',
              '- [Conflicting output contract](root-review/0628-conflicting-output-contract.json).',
              '- [Computed narrative disclosure](root-review/0069-computed-text.json).',
              '- [Cash adjustment](root-review/0034-cash-plug.json).',
              '- [Output-path violations](root-review/submission-path-contract.json).', '']
    lines += ['### Additional independent replays', '']
    for proof in sorted((root / 'root-review').glob('*.json')):
        if proof.name not in {'0266-duplicate-lookup.json','0628-conflicting-output-contract.json','0069-computed-text.json','0034-cash-plug.json','submission-path-contract.json'}:
            lines.append(f'- [{proof.stem.replace("-", " ")}]({proof.relative_to(root)}).')
    lines.append('')
    (root / 'README.md').write_text('\n'.join(lines))
    print(json.dumps({k: v for k, v in index.items() if k not in ('tasks', 'taskPaths')}))


if __name__ == '__main__':
    main()
