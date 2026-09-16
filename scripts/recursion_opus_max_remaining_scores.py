"""Export the 60 approved generation triples without mixing historical samples."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import json
import recursion_campaign as c
from recursion_opus_max_remaining import WORK
from recursion_opus_max_scores import summarize


def main():
    approval = c.read(WORK/'remaining-approval.json')
    rows = []
    for name in approval['campaigns']:
        campaign = c.read(c.REPORTS/'campaigns'/name/'campaign.json')
        group = WORK/'groups'/name
        receipt = c.read(group/'evaluation.response.json') if (group/'evaluation.response.json').exists() else None
        if receipt:
            detail = c.cli('evaluations', 'get', '--evaluation-id', receipt['id'])
            c.save(group/'evaluation-status.json', detail)
        for task in campaign['tasks']:
            row = {'task': task['task_id'], 'generationId': task['generation_id'], 'campaign': name,
                   'passAt3': None, 'attempts': [], 'state': 'awaiting_submission'}
            publication = c.REPORTS/'campaigns'/name/'registry-approval.json'
            if not receipt and publication.exists() and c.read(publication)['status'] != 'approved':
                row['state'] = 'awaiting_publication_approval'
            if receipt:
                version = c.read(c.REPORTS/'campaigns'/name/'problems'/task['task_id']/'verified.json')
                row.update(problemId=version['problemId'], problemVersionId=version['id'],
                           evaluationId=receipt['id'], evaluationCreatedAt=receipt['createdAt'],
                           solverVersionId=receipt['solvers'][0]['runConfigVersionId'])
            rows.append(row)

    def fetch(row):
        if 'evaluationId' not in row:
            return row
        try:
            response = c.cli('problem-runs', 'list-for-problem', '--problem-id', row['problemId'])
            runs = [r for r in response['items'] if r['problemVersionId'] == row['problemVersionId']
                    and r.get('solverRunConfigVersionId') == row['solverVersionId']
                    and r.get('source') == 'evaluation' and r['createdAt'] >= row['evaluationCreatedAt']]
            assert all(r.get('apiModelName') in (None, c.policy.MODEL_ID) for r in runs)
            # Keep outcome identity and error evidence; no bulky embedded configs.
            keep = ('id', 'attemptNumber', 'status', 'finalScore', 'errorMessage', 'gradingError',
                    'createdAt', 'startedAt', 'completedAt', 'apiModelName', 'solverRunConfigVersionId')
            return summarize(row, [{k: r[k] for k in keep if k in r} for r in runs])
        except Exception as exc:
            return {**row, 'state': 'read_error', 'error': str(exc)[:500]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, rows))
    assert len(results) == approval['tasks'] == 60
    assert len({(r['task'], r['generationId']) for r in results}) == 60
    states = Counter(r['state'] for r in results)
    summary = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'taskCount': len(results),
               'submittedTasks': sum('evaluationId' in r for r in results),
               'states': dict(states), 'attemptStatuses': dict(Counter(a['status'] for r in results for a in r['attempts'])),
               'completeTriples': states['scored'], 'passingTriples': sum(r['passAt3'] == 1 for r in results),
               'passAt3': None}
    if states['scored'] == len(results):
        summary['passAt3'] = summary['passingTriples'] / len(results)
    c.save(WORK/'remaining-task-scores.json', results)
    c.save(WORK/'remaining-score-summary.json', summary)
    with (WORK/'remaining-task-scores.csv').open('w', newline='') as output:
        fields = ['task', 'generationId', 'campaign', 'problemVersionId', 'solverVersionId', 'evaluationId',
                  'state', 'score1', 'score2', 'score3', 'passAt3']
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in results:
            data = {k: row.get(k) for k in fields}
            for attempt in row['attempts']:
                data['score' + str(attempt['attemptNumber'])] = attempt.get('finalScore')
            writer.writerow(data)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
