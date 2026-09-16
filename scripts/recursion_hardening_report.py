"""Render the pinned audit campaign's authoritative evaluation results."""
from pathlib import Path
import recursion_campaign as c
from recursion_hardening_campaign import WORK, ROOT, summarize, attempt_outcome, apply_failure_adjudications


def render():
    rows = c.read(WORK / 'results/task-runs.json')
    for row in rows:
        for attempts in row['models'].values():
            for attempt in attempts:attempt.pop('failureAdjudication', None)
    ledger = WORK / 'results/failure-adjudications.json'
    if ledger.exists():apply_failure_adjudications(rows, c.read(ledger), ROOT)
    summary = summarize(rows)
    saved = c.read(WORK / 'results/summary.json')
    assert all(saved[k] == v for k, v in summary.items())
    lines = ['# Repaired-task model comparison', '',
             f"Result snapshot updated: {saved['collectedAt']}", '',
             '## Scope and method', '',
             'The audit covered 1,804 historical Gemini 3.8 Flash attempts scoring strictly above 80%. '
             'The comparison selects the latest definitions of their 574 distinct workbooks. '
             'This is a cohort selected for historical Gemini success, not a random benchmark sample.', '',
             'Both models receive the same repaired assets, prompts, numeric tolerances and time budget. '
             'Each receives three independent attempts per task. A pass requires every requested answer '
             'to meet its tolerance; pass@3 is the proportion of tasks with at least one complete pass. '
             'These rates are not directly comparable to the historical continuous closeness scores.', '',
             'Opus uses Claude Code and Gemini uses OpenCode. The pinned configurations have equivalent '
             'task inputs and permissions, the same medium container tier and a 10,800-second time limit. '
             'Gemini also has a 900-second inactivity watchdog, which terminated three current attempts. '
             'Per-response output-token budget parity is not independently established. This measures the models in their respective '
             'agent harnesses rather than isolating model weights from harness behavior.', '',
             '## Results', '',
             '| Model | Scored attempts | Expected attempts | Pass per attempt | Pass@3 |',
             '|---|---:|---:|---:|---:|']
    for model, name in [('opus', 'Opus 5 thinking max'), ('gemini', 'Gemini 3.8 Flash')]:
        m = summary['models'][model]
        percent = lambda x: ('Unresolved' if summary['allAttemptsTerminal'] else 'Pending') if x is None else f'{100*x:.2f}%'
        pass_at_3 = percent(m['passAt3'])
        if summary['allAttemptsTerminal'] and m['passAt3'] is None:
            bounds = m['passAt3Bounds']
            pass_at_3 = f"{100*bounds['lower']:.2f}%–{100*bounds['upper']:.2f}%"
        lines.append(f"| {name} | {m['scoredAttempts']} | {m['expectedAttempts']} | "
                     f"{percent(m['perAttemptPassRate'])} | {pass_at_3} |")
    lines.append('')
    amendment_review = WORK / 'content-amendment-review/status.json'
    amendment_pending = amendment_review.exists() and c.read(amendment_review)['status'] != 'complete'
    if amendment_pending:
        lines += ['**Additional content corrections are awaiting activation.** Follow-up review identified '
                  'calculated results in previously missed text formats. The amendment status below records '
                  'the documentation, repair, and runtime-verification stage. Affected tasks replace all three attempts for both models '
                  'on new immutable versions; these counts are provisional. '
                  '[Amendment review](../content-amendment-review/status.json).', '']
    review_path = WORK / 'task-validity-review.json'
    if review_path.exists() and c.read(review_path)['status'] != 'complete':
        lines += ['Source-validity corrections are still pending activation. These progress counts include '
                  'predecessor task versions; every affected task will replace all three attempts for both models. '
                  'They are not the final corrected-task comparison.', '']
    if amendment_pending:
        lines += ['Final comparison conclusions are withheld pending the documented content amendment and '
                  'its complete replacement evaluation.', '']
    elif summary['complete']:
        gap = summary['opusMinusGeminiPercentagePoints']
        lines += [f'Opus minus Gemini pass@3: **{gap:+.2f} percentage points**. '
                  f"The expected advantage of at least 10 points was {'met' if gap >= 10 else 'not met'}.", '']
        paired = {'both': 0, 'opus_only': 0, 'gemini_only': 0, 'neither': 0}
        for row in rows:
            o, g = (any(attempt_outcome(a) == 1 for a in row['models'][m]) for m in ('opus', 'gemini'))
            paired['both' if o and g else 'opus_only' if o else 'gemini_only' if g else 'neither'] += 1
        lines += [f"Paired task outcomes: both passed {paired['both']}; only Opus passed {paired['opus_only']}; "
                  f"only Gemini passed {paired['gemini_only']}; neither passed {paired['neither']}.", '']
    elif summary['allAttemptsTerminal']:
        lines += ['All three attempts per task and model have ended, but some graded outcomes remain unresolved. '
                  'The following bounds show every possible assignment of those unknown outcomes; '
                  'they are not confidence intervals or invented backend scores.', '']
        for model in ('opus', 'gemini'):
            b = summary['models'][model]['passAt3Bounds']
            lines.append(f"- {model} pass@3: **{100*b['lower']:.2f}%–{100*b['upper']:.2f}%**.")
        b = summary['opusMinusGeminiPercentagePointBounds']
        lines += [f"- Opus minus Gemini: **{b['lower']:+.2f}–{b['upper']:+.2f} percentage points**.", '']
        conclusion = ('met throughout these bounds' if b['lower'] >= 10 else
                      'not met even at the upper bound' if b['upper'] < 10 else
                      'undetermined because the bounds cross 10 points')
        lines += [f'The expected Opus advantage of at least 10 percentage points is {conclusion}.', '']
        raw_possible_opus = sum(any(a.get('finalScore') in (1, None) for a in row['models']['opus']) for row in rows)
        raw_confirmed_gemini = sum(any(a.get('finalScore') == 1 for a in row['models']['gemini']) for row in rows)
        raw_gap_upper = 100 * (raw_possible_opus - raw_confirmed_gemini) / len(rows)
        lines += [f'Sensitivity check: treating every unscored attempt as unknown, including the '
                  f'evidence-backed solver failures, gives a maximum Opus lead of **{raw_gap_upper:.2f} points**. '
                  'This check does not replace the adjudicated results above.', '']
    else:
        lines += ['**The comparison is incomplete. Final results are withheld until the remaining attempts finish; '
                  'unresolved graded outcomes will be reported as bounds.**', '']
    lines += ['## Infrastructure and provenance', '']
    if (WORK / 'results/collection-sources.json').exists():
        lines += ['Completed evaluation matrices can be reused from verified frozen snapshots after a fresh '
                  'unchanged evaluation-detail check. Their original read times and hashes remain explicit in '
                  '[collection provenance](collection-sources.json); cached values are not presented as new reads.', '']
    cap_evidence = 'infra-review/new-opus-failures-1623/output-cap/output-cap-comparison.json'
    if (WORK / 'results' / cap_evidence).exists():
        lines += ['The stock CLI source paths expose 32,000-token default per-response output ceilings. '
                  'Opus output-limit errors were observed; the exact Gemini request cap and recovery behavior '
                  'are not independently captured. This does not demonstrate unequal caps or token-budget parity. '
                  'Opus max thinking effort is a separate verified setting. '
                  f'[Output-limit evidence]({cap_evidence}).', '']
    for model in ('opus', 'gemini'):
        lines.append(f"- {model}: {summary['models'][model]['adjudicatedSolverFailures']} evidenced solver "
                     'resource failures counted as unsuccessful attempts; backend scores remain null.')
        lines.append(f"- {model}: {summary['models'][model]['infrastructureOrUnscoredAttempts']} terminal "
                     'attempts with unresolved outcomes. These are not counted as model failures.')
    lines += ['', 'Numerical scores come exclusively from the new evaluation matrices. Evidence-backed solver '
              'failure outcomes are separately recorded in [the adjudication ledger](failure-adjudications.json), '
              'with the same criteria for both models. Exit code 137 alone does not establish a solver-caused failure. '
              'Historical and unrelated runs '
              'are excluded. Duplicate run IDs, duplicate attempt slots, unexpected tasks, extra attempts, '
              'and continuous scores fail validation.', '',
              'Any task revision supersedes all three attempts on the earlier version for both models. '
              '[Superseded attempts](superseded-task-runs.json) remain inspectable and are excluded from this comparison.', '',
              '[Task-level results](task-runs.json) · [Summary](summary.json) · '
              '[Audit and repair report](../../README.md)', '']
    path = WORK / 'results/README.md'
    path.write_text('\n'.join(lines))
    print(path)


if __name__ == '__main__':
    render()
