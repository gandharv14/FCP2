"""Write an inspectable, active-version-only score table for the frozen audit cohort."""
import json
from pathlib import Path

from recursion_hardening_campaign import WORK, ROOT, attempt_outcome, summarize, apply_failure_adjudications


def render(work=WORK):
    work = Path(work)
    rows = json.loads((work / 'results/task-runs.json').read_text())
    for row in rows:
        for attempts in row['models'].values():
            for attempt in attempts:
                attempt.pop('failureAdjudication', None)
    ledger = work / 'results/failure-adjudications.json'
    if ledger.exists():
        apply_failure_adjudications(rows, json.loads(ledger.read_text()), ROOT)
    saved = json.loads((work / 'results/summary.json').read_text())
    index = json.loads((work / 'content-successor/REPAIR-INDEX.json').read_text())
    calculated = summarize(rows)
    assert all(saved[k] == v for k, v in calculated.items()), 'Stale summary'
    repairs = {r['taskId']: r for r in index['tasks']}
    assert len(repairs) == len(rows) == 574
    assert set(repairs) == {r['taskId'] for r in rows}
    lines = [
        '# Active task reward percentages', '',
        f"Result snapshot updated: {saved['collectedAt']}", '',
        'Each model has exactly three planned independent attempts per task. '
        'Only the active task version is included; replaced attempts are excluded.', '',
        '`100%` = canonical full-answer pass; `0%` = canonical full-answer failure; '
        '`F` = separately evidenced solver resource failure (backend score remains null); '
        '`?` = terminal outcome unresolved; `…` = pending/running; `—` = not yet dispatched. '
        'Attempt reward columns are ordered 1 / 2 / 3 and shown as percentages.', '',
        'Mean reward is 100 × the sum of the three backend rewards ÷ 3. '
        'The current grader records a reward of 0 or 1, so this is the average strict reward, '
        'not the historical continuous closeness score or pass@3. '
        'Where a backend reward is missing (including `F`), the mean is a possible range '
        'obtained by assigning missing rewards 0 or 1; missing rewards are never silently dropped. '
        'These ranges are not confidence intervals.', '',
        'Repair flags: `W` numeric workbook results masked; `F` formatted results masked; '
        '`D` deeper targets; `P` source clarification; `R` research corpus isolation. '
        '`A` additional source-reviewed result masking. '
        'All tasks use strict complete-answer grading.', '',
        '[Comparison and limitations](README.md) · '
        '[Repair evidence](../content-successor/REPAIR-INDEX.md) · '
        '[Exact run IDs and task versions](task-runs.json)', '',
        '| Task | Repairs | Opus attempt rewards | Opus mean reward | Gemini attempt rewards | Gemini mean reward |',
        '|---|---|---|---|---|---|',
    ]

    def model_cell(attempts):
        slots = {a['attemptNumber']: a for a in attempts}
        marks = []
        for n in (1, 2, 3):
            a = slots.get(n)
            if a is None:
                marks.append('—')
            elif a.get('failureAdjudication') and attempt_outcome(a) == 0:
                marks.append('F')
            elif attempt_outcome(a) is not None:
                marks.append(f"{100 * a['finalScore']:.0f}%")
            elif a['status'] in ('completed', 'failed', 'cancelled'):
                marks.append('?')
            else:
                marks.append('…')
        known = [a['finalScore'] for a in attempts if a.get('finalScore') is not None]
        assert all(value in (0, 1) for value in known)
        low = 100 * sum(known) / 3
        high = low + 100 * (3 - len(known)) / 3
        reward = (f'{low:.2f}%' if len(known) == 3 else
                  f'{low:.2f}–{high:.2f}% ({len(known)}/3 scored)')
        return ' / '.join(marks), reward

    for row in rows:
        repair = repairs[row['taskId']]
        assert row['problemVersionId'] == repair['activeComparisonProblemVersionId']
        flags = []
        if repair['initialWorkbookRepair']:
            flags.append('W')
        if repair['formattedWorkbookCorrection']:
            flags.append('F')
        if repair.get('amendmentWorkbookCorrection'):
            flags.append('A')
        if repair['depthReview'] and repair['depthReview'].get('status') == 'fixed':
            flags.append('D')
        if repair['promptCorrection']:
            flags.append('P')
        if any('research' in name for name in repair['initialRepairs']):
            flags.append('R')
        o, os = model_cell(row['models']['opus'])
        g, gs = model_cell(row['models']['gemini'])
        lines.append(f"| {row['taskId']} | {', '.join(flags) or '—'} | {o} | {os} | {g} | {gs} |")
    path = work / 'results/TASK-SCORES.md'
    path.write_text('\n'.join(lines) + '\n')
    print(path)


if __name__ == '__main__':
    render()
