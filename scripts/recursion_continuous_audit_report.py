"""Present existing audits using continuous rewards, without inventing audit coverage."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import re

from recursion_reward_extremes_audit import AUDIT
from recursion_reward_extremes_report import LABELS, evidence_links, plain


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pct(value):
    if value is None:
        return 'Unavailable'
    if 0 < value < 0.00005:
        return '<0.01%'
    if 0.99995 <= value < 1:
        return '>99.99%'
    return f'{100 * value:.2f}%'


def mean(model):
    if model['meanAll'] is not None:
        return pct(model['meanAll'])
    return f"{pct(model['lower'])}–{pct(model['upper'])} ({model['recovered']}/3 recovered)"


def render():
    archive = AUDIT / 'superseded-binary-report'
    if not archive.exists():
        archive.mkdir()
        files = ['README.md', 'TASK-AUDIT.md', 'task-audit-ledger.json', 'completion-receipt.json']
        for name in files:
            shutil.copy2(AUDIT / name, archive / name)
        shutil.copytree(AUDIT / 'tasks', archive / 'tasks')
        (archive / 'ARCHIVE-NOTE.md').write_text(
            '# Superseded binary-reward report\n\nHistorical snapshot, not the current continuous-reward audit. '
            'Original absolute paths in historical receipts refer to the pre-update files. '
            'Original relative links were authored from the parent audit directory.\n')
    prior = json.loads((archive / 'task-audit-ledger.json').read_text())
    old = {t['taskId']: t for t in prior['tasks']}
    deep_index_path = AUDIT / 'deep-review/resolution-index.json'
    deep_reviews = {}
    if deep_index_path.exists():
        deep_index = json.loads(deep_index_path.read_text())
        for rec in deep_index['tasks']:
            assert sha(rec['path']) == rec['sha256']
            deep_reviews[rec['taskId']] = json.loads(Path(rec['path']).read_text())
    source = AUDIT.parent / 'continuous-rewards/task-rewards.json'
    source_rows = json.loads(source.read_text())
    backend = {t['taskId']: t for t in json.loads((AUDIT.parent / 'task-runs.json').read_text())}
    selected, excluded, uncertain, outside = [], [], [], []
    all_rows = []
    for row in source_rows:
        tid = row['taskId']
        assert row['problemVersionId'] == backend[tid]['problemVersionId']
        for model in ('opus', 'gemini'):
            attempts = row['models'][model]['attempts']
            expected = {a['problemRunId']: a for a in backend[tid]['models'][model]}
            assert len(attempts) == 3 and {a['problemRunId'] for a in attempts} == set(expected)
            for a in attempts:
                assert a['attemptNumber'] == expected[a['problemRunId']]['attemptNumber']
                assert a['backendDiscreteScore'] == expected[a['problemRunId']]['finalScore']
            values = [a['continuousReward'] for a in attempts]
            assert all(v is None or 0 <= v <= 1 for v in values)
            actual = sum(values) / 3 if all(v is not None for v in values) else None
            stored = row['models'][model]['meanAll']
            assert (actual is None and stored is None) or (
                actual is not None and stored is not None and math.isclose(actual, stored, rel_tol=0, abs_tol=1e-14))
        o, g = [row['models'][m]['meanAll'] for m in ('opus', 'gemini')]
        if o == 1 and g == 0:
            group, status = excluded, 'excluded_opus_100_gemini_0'
        elif any(v is not None and (v >= .9 or v == 0) for v in (o, g)):
            group, status = selected, 'selected'
        elif (o is None or g is None) and not all(
                m['lower'] > 0 and m['upper'] < .9 for m in row['models'].values()):
            group, status = uncertain, 'indeterminate_missing_rewards'
        else:
            group, status = outside, 'outside_threshold'
        group.append(tid)
        audit = old.get(tid)
        deep_review = deep_reviews.get(tid)
        if audit:
            version = audit.get('activeProblemVersionId', audit.get('problemVersionId'))
            assert version == row['problemVersionId']
        if deep_review:
            assert deep_review['problemVersionId'] == row['problemVersionId']
        all_rows.append({**row, 'selectionStatus': status,
                         'auditCoverageStatus': 'existing_six_rollout_audit' if audit else 'not_covered_by_previous_audit',
                         'assessment': deep_review['assessment'] if deep_review else audit['assessment'] if audit else 'audit_pending',
                         'deepReviewEvidence': deep_review,
                         'priorAuditEvidence': audit})
    rows = [r for r in all_rows if r['selectionStatus'] == 'selected']
    pending = [r['taskId'] for r in rows if r['priorAuditEvidence'] is None]
    counts = Counter(r['assessment'] for r in rows)
    manifest = {'updatedAt': datetime.now(timezone.utc).isoformat(),
                'metric': 'arithmetic_mean_of_three_continuous_numerical_closeness_rewards',
                'rule': 'Either complete three-attempt mean >=0.9 or ==0; exclude exact Opus1/Gemini0. Missing means are not imputed.',
                'sourcePath': str(source), 'sourceSha256': sha(source),
                'taskIds': selected, 'excludedOpus100Gemini0': excluded,
                'indeterminateMissingRewards': uncertain, 'outsideThreshold': outside,
                'qualifyingTasksWithoutPreviousAudit': pending,
                'previouslyAuditedTasksOutsideCurrentSelection': sorted(set(old) - set(selected))}
    (AUDIT / 'continuous-selection.json').write_text(json.dumps(manifest, indent=2) + '\n')
    ledger = {'updatedAt': manifest['updatedAt'], 'status': 'continuous_report_updated_audit_coverage_incomplete' if pending else 'continuous_report_updated',
              'metric': manifest['metric'], 'sourceSha256': manifest['sourceSha256'],
              'selectedTasks': len(rows), 'selectedTasksWithExistingAudit': len(rows) - len(pending),
              'selectedTasksPendingAudit': len(pending), 'assessmentCounts': dict(counts),
              'deepReviewStatusCounts': dict(Counter(d['resolutionStatus'] for d in deep_reviews.values())),
              'tasks': rows, 'nonselectedTasks': [r for r in all_rows if r['selectionStatus'] != 'selected']}
    (AUDIT / 'task-audit-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    matrix = ['# Continuous-reward task audit', '',
              f'**{len(rows)} qualifying tasks: {len(rows)-len(pending)} have an existing six-rollout audit; {len(pending)} need audit coverage.**', '',
              'Selected using the unrounded continuous mean of three attempts: either model ≥90% or exactly 0%; '
              'exclude exact Opus 100% / Gemini 0%. Missing attempts produce bounds, not an imputed mean. '
              'There are no fully observed exclusion pairs in this snapshot.', '',
              'Historical findings are preserved. Their references to zero reward or a pass concern the strict binary backend grade, '
              'not the continuous rewards displayed below. Pending rows are not audited findings.', '',
              '[Summary](README.md) · [All-task selection status](SELECTION-STATUS.md) · [Structured evidence](task-audit-ledger.json)', '',
              '| Task | Opus continuous mean | Gemini continuous mean | Audit status / assessment |',
              '|---|---:|---:|---|']
    status_lines = ['# Continuous-reward selection status — all 574 tasks', '',
                    'Filtering uses complete, full-precision three-attempt means. Incomplete means are shown as bounds and are not treated as zero. '
                    'A task is definitively outside when both models’ bounds lie strictly between 0% and 90%. '
                    'A task can qualify through one complete model mean while the other remains unavailable.', '',
                    '[Qualifying-task audit](TASK-AUDIT.md) · [Selection data](continuous-selection.json)', '',
                    '| Task | Opus continuous mean | Gemini continuous mean | Selection | Existing audit |',
                    '|---|---:|---:|---|---|']
    for row in all_rows:
        tid, audit = row['taskId'], row['priorAuditEvidence']
        deep_review = row['deepReviewEvidence']
        label = LABELS.get(row['assessment'], row['assessment'].replace('_', ' '))
        om, gm = (mean(row['models'][m]) for m in ('opus', 'gemini'))
        status_lines.append(f"| [{tid}](tasks/{tid}.md) | {om} | {gm} | {row['selectionStatus']} | {'Yes' if audit else 'No'} |")
        if row['selectionStatus'] == 'selected':
            matrix.append(f'| [{tid}](tasks/{tid}.md) | {om} | {gm} | {label} |')
        lines = [f'# {tid}', '', f'Opus continuous mean: **{om}**. Gemini continuous mean: **{gm}**.', '',
                 f"Selection: **{row['selectionStatus']}**. Audit assessment: **{label}**.", '',
                 'Continuous rewards are locally recalculated numerical-closeness rewards for the existing submissions. '
                 'They do not replace backend grades. Missing values remain unavailable.', '', '## Existing audit findings', '']
        if audit:
            if deep_review:
                audit = {**audit, 'narrative': deep_review['summary'], 'findings': deep_review['findings'],
                         'limitations': deep_review.get('limitations', [])}
                lines += [f"**Deep review: {deep_review['resolutionStatus']}.**", '',
                          f"[Deep-review evidence](../deep-review/{deep_index['taskPaths'][tid]})", '']
            lines += [('The findings below incorporate the deeper source and trajectory review. ' if deep_review else
                       'The findings below were produced by the previous audit. ') + 'Any zero/full-pass descriptions refer to '
                      'the strict binary backend grade; they must not be read as continuous means.', '', audit['narrative'], '']
            for finding in audit['findings']:
                text = finding.get('description', finding.get('summary', finding.get('cause', finding.get('finding', 'See detailed evidence.'))))
                lines += [f"### {finding.get('type', 'Finding').replace('_', ' ')}", '', str(text), '']
                if finding.get('counterfactual'):
                    lines += ['Counterfactual check: ' + str(finding['counterfactual']), '']
                recommendation = finding.get('recommendation', finding.get('actionableRecommendation'))
                if recommendation:
                    lines += ['Recommended action: ' + str(recommendation), '']
                for link in sorted(evidence_links(finding)):
                    lines += [f'- [{Path(link).name}](<{link}>)']
                lines += ['', *['- ' + str(v) for v in finding.get('limitations', [])], '']
            if deep_review:
                recommendations = deep_review.get('recommendations', [])
                if deep_review.get('recommendation'):
                    recommendations = [*recommendations, deep_review['recommendation']]
                if recommendations:
                    lines += ['## Recommended action', '', *['- ' + str(v) for v in recommendations], '']
                for link in sorted(evidence_links(deep_review.get('evidence', []))):
                    lines += [f'- [{Path(link).name}](<{link}>)']
                lines.append('')
            lines += ['## Audit limitations', '', *['- ' + str(v) for v in audit.get('limitations', [])], '',
                      f"[Original shard evidence](../shard-{audit['reviewShard']}-audit.json)", '']
        else:
            lines += ['No task-specific audit from the previous 536-task cohort exists for this task. '
                      'Reward evidence below is not a substitute for trajectory review.', '']
        lines += ['## Attempt rewards and provenance', '',
                  '| Model | Attempt | Continuous reward | Strict binary grade | Run ID |', '|---|---:|---:|---:|---|']
        for model in ('opus', 'gemini'):
            for a in row['models'][model]['attempts']:
                lines.append(f"| {model} | {a['attemptNumber']} | {pct(a['continuousReward'])} | "
                             f"{pct(a['backendDiscreteScore'])} | [{a['problemRunId']}](<{a['evidencePath']}>) |")
        lines += ['', '[Qualifying-task index](../TASK-AUDIT.md) · [All-task selection](../SELECTION-STATUS.md)', '']
        (AUDIT / 'tasks' / (tid + '.md')).write_text('\n'.join(lines))
    (AUDIT / 'TASK-AUDIT.md').write_text('\n'.join(matrix) + '\n')
    (AUDIT / 'SELECTION-STATUS.md').write_text('\n'.join(status_lines) + '\n')
    by_id = {r['taskId']: r for r in all_rows}
    summary = ['# Continuous-reward trajectory audit', '',
               '**Corrected metric: continuous numerical-closeness reward, averaged across three attempts.** '
               'The earlier report incorrectly selected and displayed mean binary backend grades.', '',
               f'**{len(rows)} tasks qualify. {len(rows)-len(pending)} have prior six-rollout audit evidence; {len(pending)} are pending audit.** '
               f'{len(uncertain)} other tasks have incomplete rewards and indeterminate selection; {len(outside)} are outside the threshold. '
               f'{len(excluded)} fully observed tasks meet the Opus 100% / Gemini 0% exclusion.', '',
               '[Qualifying tasks and corrected mean rewards](TASK-AUDIT.md) · [Selection status for all 574 tasks](SELECTION-STATUS.md) · '
               '[Full reward report](../TASK-SCORES.md)', '', '## Metric and missing data', '',
               'Each task uses its historical continuous numerical-closeness formula and weights, applied to the active repaired targets '
               'and existing model submissions. Mean reward is the sum of three continuous attempt rewards divided by three. '
               'It is neither pass@3 nor the fraction of fully correct attempts. Selection uses full precision; values just below 100% '
               'are displayed as >99.99%, rather than rounded to exact 100%.', '',
               'When an attempt is unavailable, the report shows the possible three-attempt mean range and recovered count. '
               'It does not silently drop the missing attempt. A range overlapping 90% alone does not establish eligibility. '
               'The other model can establish eligibility with a complete mean. If both models’ bounds lie strictly between 0% and 90%, '
               'the task is definitively outside the filter despite any missing rewards.', '',
               '[Recalculation method and limitations](../continuous-rewards/README.md) · [Frozen selection](continuous-selection.json)', '',
               '## Existing findings with corrected rewards', '',
               'These are prior source/trajectory findings, not newly performed audits. Strict binary failure can coexist with a high '
               'continuous reward when one answer is slightly wrong.', '',
               '| Task | Opus continuous mean | Gemini continuous mean | Existing finding |', '|---|---:|---:|---|']
    notes = {'0227': 'Research exposes solved funding amounts that sum to the sole target.',
             '0560': 'Research exposes solved DCF sensitivity endpoints; subtraction gives both targets.',
             '0072': 'Native expected EBIT mixes a percentage into currency. Both continuous means are near 100%, despite strict binary failures.',
             'Import_Toys_30SKU_Model': 'Aggregate packing convention is masked and not linked to the appropriate research inputs.',
             'VELTARA_TECHNOLOGIES_v2': 'The first-year versus later-year ARR rule is not specified clearly.',
             'Fin_Deriv_RK_Credit_Default_Structural_1974_Merton_v1.2': 'Fraction-versus-percent ambiguity. At 85.86% for both models, this task is outside the corrected threshold; its finding is retained as supplementary evidence.'}
    for tid, note in notes.items():
        row = by_id[tid]
        summary.append(f"| [{tid}](tasks/{tid}.md) | {mean(row['models']['opus'])} | {mean(row['models']['gemini'])} | {note} |")
    summary += ['', '## Assessment counts — qualifying cohort only', '', '| Assessment | Tasks |', '|---|---:|']
    for key, count in sorted(counts.items()):
        summary.append(f"| {LABELS.get(key, key.replace('_', ' '))} | {count} |")
    if deep_reviews:
        summary += ['', '## Deeper review of formerly unresolved cases', '',
                    f"{len(deep_reviews)} task-specific deep reviews are documented. Resolution statuses: " +
                    ', '.join(f'{k}: {v}' for k, v in ledger['deepReviewStatusCounts'].items()) + '.', '',
                    '[Deep-review findings and remaining evidence requirements](deep-review/README.md)', '']
    summary += ['', 'Counts describe existing primary assessments, not proof that every exposure caused success. '
                'Unresolved cases retain their evidence limitations. The original 536-task counts do not describe this corrected cohort.', '',
                '## Coverage still needed', '', ', '.join(f'[{t}](tasks/{t}.md)' for t in pending), '',
                'This update corrects reporting and selection; it does not claim these newly qualifying tasks have been audited. '
                'No tasks, model attempts, or backend grades were changed.', '',
                '## Provenance', '',
                'The original binary selection and shard audit evidence are retained unchanged. The superseded presentation and '
                'its completion receipt are preserved under `superseded-binary-report/`. Historical completion claims apply only '
                'to that earlier cohort. The current ledger explicitly records incomplete coverage.', '',
                '[Current update receipt](completion-receipt.json) · [Structured task evidence](task-audit-ledger.json)', '']
    (AUDIT / 'README.md').write_text('\n'.join(summary))
    receipt = {k: v for k, v in ledger.items() if k not in ('tasks', 'nonselectedTasks')}
    receipt.update({'reportUpdateComplete': True, 'continuousCohortAuditComplete': not pending,
                    'newModelAttempts': 0, 'backendGradeChanges': 0,
                    'preservedBinaryReceiptSha256': sha(archive / 'completion-receipt.json'),
                    'files': {str(AUDIT / name): sha(AUDIT / name) for name in
                              ('README.md', 'TASK-AUDIT.md', 'SELECTION-STATUS.md', 'continuous-selection.json', 'task-audit-ledger.json')}})
    (AUDIT / 'completion-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'files'}))


def verify():
    ledger = json.loads((AUDIT / 'task-audit-ledger.json').read_text())
    selection = json.loads((AUDIT / 'continuous-selection.json').read_text())
    receipt = json.loads((AUDIT / 'completion-receipt.json').read_text())
    source = json.loads(Path(selection['sourcePath']).read_text())
    assert sha(selection['sourcePath']) == selection['sourceSha256']
    assert all(sha(path) == pin for path, pin in receipt['files'].items())
    combined = ledger['tasks'] + ledger['nonselectedTasks']
    assert len(combined) == len({r['taskId'] for r in combined}) == len(source) == 574
    by_id = {r['taskId']: r for r in source}
    assert {r['taskId'] for r in ledger['tasks']} == set(selection['taskIds'])
    pins = 0
    for row in combined:
        assert row['models'] == by_id[row['taskId']]['models']
        for model in row['models'].values():
            for attempt in model['attempts']:
                assert sha(attempt['evidencePath']) == attempt['evidenceSha256']
                pins += 1
    pages = [AUDIT / n for n in ('README.md', 'TASK-AUDIT.md', 'SELECTION-STATUS.md')]
    pages += sorted((AUDIT / 'tasks').glob('*.md'))
    assert len(pages) == 577
    links = 0
    for page in pages:
        for raw in re.findall(r'\]\((<[^>]+>|[^)]+)\)', page.read_text()):
            value = raw.strip('<>')
            if value.startswith(('https:', 'http:', '#')):
                continue
            target = Path(value) if value.startswith('/') else page.parent / value
            assert target.exists(), (page, value)
            assert '.private.' not in value and not value.endswith('.gz'), (page, value)
            links += 1
    table = (AUDIT / 'TASK-AUDIT.md').read_text()
    assert sum(line.startswith('| [') for line in table.splitlines()) == ledger['selectedTasks']
    assert sum(line.startswith('| [') for line in (AUDIT / 'SELECTION-STATUS.md').read_text().splitlines()) == 574
    result = {'status': 'passed', 'rewardEvidenceHashesChecked': pins, 'pagesChecked': len(pages),
              'linksChecked': links, 'selectedTasks': ledger['selectedTasks'],
              'coveredByPriorAudit': ledger['selectedTasksWithExistingAudit'],
              'pendingAudit': ledger['selectedTasksPendingAudit'], 'allSourceRewardsPreserved': True}
    (AUDIT / 'continuous-report-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    render()
    verify()
