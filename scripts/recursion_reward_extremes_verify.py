"""Verify and freeze the completed audit; never changes evaluation inputs or scores."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from recursion_reward_extremes_audit import AUDIT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify():
    ledger = json.loads((AUDIT / 'task-audit-ledger.json').read_text())
    if ledger.get('metric') == 'arithmetic_mean_of_three_continuous_numerical_closeness_rewards':
        from recursion_continuous_audit_report import verify as verify_continuous
        return verify_continuous()
    selection = json.loads((AUDIT / 'selection.json').read_text())
    assert sha(AUDIT.parent / 'task-runs.json') == selection['sourceResultsSha256']
    assert len(ledger['tasks']) == 536
    assert {t['taskId'] for t in ledger['tasks']} == set(selection['taskIds'])
    checked, issues = {}, []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str):
                path = value['path']
                if path not in checked:
                    checked[path] = sha(path) if Path(path).is_file() else None
                if checked[path] != value['sha256']:
                    issues.append({'path': path, 'expected': value['sha256'], 'actual': checked[path]})
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for n in (1, 2, 3):
        path = AUDIT / f'shard-{n}-audit.json'
        assert sha(path) == ledger['reviewReportHashes'][str(path)]
        report = json.loads(path.read_text())
        walk(report)
        for record in report.get('taskRecords', []):
            walk(json.loads(Path(record['path']).read_text()))
    assert not issues, issues[:5]
    (AUDIT / 'evidence-pin-verification.json').write_text(json.dumps({
        'verifiedAt': datetime.now(timezone.utc).isoformat(),
        'pinsChecked': len(checked), 'issues': issues}, indent=2) + '\n')
    pages = [AUDIT / 'README.md', AUDIT / 'TASK-AUDIT.md',
             *sorted((AUDIT / 'tasks').glob('*.md'))]
    assert len(pages) == 538
    links = 0
    for page in pages:
        for raw in re.findall(r'\]\((<[^>]+>|[^)]+)\)', page.read_text()):
            value = raw.strip('<>')
            if value.startswith(('https:', 'http:', '#')):
                continue
            target = Path(value) if value.startswith('/') else page.parent / value
            assert target.exists() or target == AUDIT / 'completion-receipt.json', (page, value)
            assert not value.endswith('.gz') and '.private.' not in value, (page, value)
            links += 1
    pins = [AUDIT / f for f in ('README.md', 'TASK-AUDIT.md', 'task-audit-ledger.json',
            'selection.json', 'threshold-update-20260916.json', 'collection-verification.json',
            'structural-screen.json', 'evidence-pin-verification.json')]
    pins += [AUDIT / f'shard-{n}-audit.{ext}' for n in (1, 2, 3) for ext in ('json', 'md')]
    pins += sorted((AUDIT / 'root-review').glob('*.json'))
    receipt = {
        'completedAt': datetime.now(timezone.utc).isoformat(),
        'status': 'complete_bounded_trajectory_audit',
        'tasks': 536, 'rollouts': 3216, 'eventsScreened': ledger['eventsScreened'],
        'excludedOpus100Gemini0Tasks': selection['excludedOpus100Gemini0'],
        'assessmentCounts': ledger['assessmentCounts'], 'evidencePinsVerified': len(checked),
        'markdownPagesVerified': len(pages), 'markdownLinksVerified': links,
        'originalResultsSha256Unchanged': selection['sourceResultsSha256'],
        'remoteMutations': 0, 'newModelAttempts': 0, 'taskDefinitionChanges': 0,
        'backendRewardChanges': 0,
        'limitations': [
            'All events structurally screened with targeted semantic/source analysis; not every token manually read.',
            'No full numerical reconstruction or exhaustive source-completeness proof for every task.',
            'Primary exposure assessments do not imply that exposure caused or suffices for success.',
            'Observed complete answers are diagnostic only; missing/null rewards are not imputed.'
        ],
        'files': {str(path): sha(path) for path in pins},
    }
    (AUDIT / 'completion-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('status', 'tasks', 'rollouts',
          'assessmentCounts', 'evidencePinsVerified', 'markdownPagesVerified', 'markdownLinksVerified')}))


if __name__ == '__main__':
    verify()
