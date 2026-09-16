"""Bounded exact-ID observations when the evaluation matrix endpoint is down.

The cached matrix proves historical evaluation membership, not current retry
membership. This module never queries global problem histories, never launches
runs, and never labels observations as a completed pass@3 comparison. Reconcile
with a recovered authoritative matrix before final scoring. V2 job pagination
cannot supply scores; the legacy list-for-job endpoint does not accept v2 IDs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


class MembershipError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MembershipError(message)


def matrix_index(detail, matrix):
    """Bind every run ID to one exact solver/version/attempt evaluation slot."""
    require(matrix['evaluationId'] == detail['id'], 'Evaluation identity mismatch')
    require(detail['nAttemptsPerProblem'] == 3, 'Expected three attempts')
    solvers = {s['id']: s for s in detail['solvers']}
    problems = {p['problemVersionId']: p for p in detail['problems']}
    require(len(solvers) == len(detail['solvers']) == detail['totalSolvers'], 'Duplicate/missing solver')
    require(len(problems) == len(detail['problems']) == detail['totalProblems'], 'Duplicate/missing version')
    require(all(s['evaluationId'] == detail['id'] for s in solvers.values()), 'Foreign solver')
    require(all(p['evaluationId'] == detail['id'] for p in problems.values()), 'Foreign problem')
    expected = {(s, v) for s in solvers for v in problems}
    cells = [(c['solverId'], c['problemVersionId']) for c in matrix['cells']]
    require(len(cells) == len(set(cells)) and set(cells) == expected, 'Matrix coverage mismatch')
    result = {}
    for cell in matrix['cells']:
        sid, version = cell['solverId'], cell['problemVersionId']
        numbers = [a['attemptNumber'] for a in cell['attempts']]
        require(cell['totalRuns'] == 3 and sorted(numbers) == [1, 2, 3], 'Duplicate/missing attempt slot')
        for a in cell['attempts']:
            rid = a['problemRunId']
            require(isinstance(rid, str) and bool(rid) and rid not in result, 'Duplicate/missing run ID')
            require(type(a['attemptNumber']) is int, 'Invalid attempt number')
            result[rid] = {'evaluationId': detail['id'], 'solverId': sid,
                'solverRunConfigVersionId': solvers[sid]['runConfigVersionId'],
                'problemId': problems[version]['problemId'], 'problemVersionId': version,
                'attemptNumber': a['attemptNumber'], 'problemRunId': rid,
                'cachedStatus': a['status'], 'cachedScore': a.get('score'),
                'cachedJobV2Id': a.get('jobV2Id'), 'infraRetryCount': a.get('infraRetryCount', 0)}
    return result


def compare_membership(detail, previous_matrix, current_matrix):
    old = matrix_index(detail, previous_matrix)
    new = matrix_index(detail, current_matrix)
    slot = lambda a: (a['solverId'], a['problemVersionId'], a['attemptNumber'])
    before = {slot(a): a for a in old.values()}
    after = {slot(a): a for a in new.values()}
    require(set(before) == set(after), 'Logical attempt membership changed')
    return [{'solverId': s[0], 'problemVersionId': s[1], 'attemptNumber': s[2],
             'oldProblemRunId': before[s]['problemRunId'], 'newProblemRunId': after[s]['problemRunId'],
             'infraRetryEvidence': after[s]['infraRetryCount'] > before[s]['infraRetryCount']}
            for s in sorted(before) if before[s]['problemRunId'] != after[s]['problemRunId']]


def validate_run(slot, run):
    for field, expected in [('id', slot['problemRunId']), ('problemId', slot['problemId']),
            ('problemVersionId', slot['problemVersionId']), ('attemptNumber', slot['attemptNumber']),
            ('solverRunConfigVersionId', slot['solverRunConfigVersionId']), ('source', 'evaluation')]:
        require(run.get(field) == expected, 'Run identity mismatch: ' + field)
    require(type(run['attemptNumber']) is int, 'Invalid run attempt number')
    require(run['status'] in ('pending', 'running', 'grading', 'completed', 'failed', 'cancelled'),
            'Unknown run lifecycle')
    score = run.get('finalScore')
    require(score is None or (type(score) in (int, float) and score in (0, 1)), 'Non-discrete score')
    error = bool(run.get('errorMessage') or run.get('gradingError'))
    return {**slot, 'readSucceeded': True, 'status': run['status'],
        'score': score if run['status'] == 'completed' and not error else None,
        'hasRunError': bool(run.get('errorMessage')), 'hasGradingError': bool(run.get('gradingError')),
        'jobV2Id': run.get('jobV2Id'), 'topJobId': run.get('topJobId'),
        'jobChangedSinceCachedMatrix': bool(slot['cachedJobV2Id'] and
            slot['cachedJobV2Id'] != run.get('jobV2Id')),
        'startedAt': run.get('startedAt'), 'completedAt': run.get('completedAt'),
        'regradeCount': run.get('regradeCount', 0)}


def collect_exact(detail, matrix, run_ids, getter, *, max_reads=12, workers=2):
    index = matrix_index(detail, matrix)
    ids = list(run_ids)
    require(0 < max_reads <= 24 and 1 <= workers <= 4, 'Excessive fallback read budget')
    require(len(ids) <= max_reads, 'Fallback read budget exceeded; retry the matrix endpoint')
    require(len(ids) == len(set(ids)) and set(ids) <= set(index), 'Unproven/duplicate requested run ID')
    def one(rid):
        try:
            run = getter(rid)
        except Exception as error:
            # API/transport failure is an observation failure, never a model zero.
            return {**index[rid], 'readSucceeded': False, 'collectorErrorType': type(error).__name__}
        return validate_run(index[rid], run)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        observations = list(pool.map(one, ids))
    return {'evaluationId': detail['id'], 'collectedAt': datetime.now(timezone.utc).isoformat(),
        'source': 'cached-evaluation-matrix-exact-run-ids', 'expectedAttempts': len(index),
        'requestedReads': len(ids), 'successfulReads': sum(x['readSucceeded'] for x in observations),
        'requiresCurrentMatrixReconciliation': True, 'passAt3': None, 'observations': observations}


def main():
    import recursion_campaign as c
    parser = argparse.ArgumentParser()
    parser.add_argument('--detail', type=Path, required=True)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--run-id', action='append', default=[])
    parser.add_argument('--max-reads', type=int, default=12)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = collect_exact(c.read(args.detail), c.read(args.matrix), args.run_id,
        lambda rid: c.cli('problem-runs', 'get', '--problem-run-id', rid), max_reads=args.max_reads)
    result['detailSha256'] = hashlib.sha256(args.detail.read_bytes()).hexdigest()
    result['matrixSha256'] = hashlib.sha256(args.matrix.read_bytes()).hexdigest()
    c.save(args.output, result)
    print(json.dumps({k:v for k,v in result.items() if k != 'observations'}))


if __name__ == '__main__':
    main()
