"""Conservative remaining-slot bounds for explicitly reviewed campaign evaluations."""
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def reviewed_evaluations(work, known_configs):
    path = Path(work) / 'content-amendments/0002/reviewed-tail-capacity-evaluations.json'
    if not path.exists():
        return {}, None
    receipt = read(path)
    require(receipt.get('schemaVersion') == 1 and receipt.get('status') == 'reviewed'
            and receipt.get('scope') == 'campaign_tail_capacity_only', 'Invalid tail capacity review')
    require(receipt.get('evidenceHashes'), 'Missing tail capacity evidence')
    for source, digest in receipt['evidenceHashes'].items():
        require(sha(source) == digest, 'Tail capacity evidence changed')
    indexed = {}
    for item in receipt['evaluations']:
        for key in ('request', 'response'):
            require(receipt['evidenceHashes'].get(item[key+'Path']) == item[key+'Sha256']
                    and sha(item[key+'Path']) == item[key+'Sha256'], 'Unpinned tail capacity request/response')
        request, response = read(item['requestPath']), read(item['responsePath'])
        eid = item['evaluationId']
        require(eid == response.get('id') and eid not in indexed, 'Wrong/duplicate reviewed evaluation')
        require('fcp-audit-hardening-20260915' in request.get('tags', []), 'Not a reviewed campaign request')
        require(request.get('metadata', {}).get('attemptsPerProblem') == 3
                and response.get('nAttemptsPerProblem') == 3, 'Tail bound requires three logical attempts')
        solvers = request['solvers']
        require(len(solvers) == 2 and {known_configs.get(x['runConfigVersionId']) for x in solvers} == {'opus', 'gemini'},
                'Tail bound requires exact known paired campaign solvers')
        indexed[eid] = {'request': request, 'response': response}
    require(indexed, 'Empty tail capacity review')
    return indexed, {'path': str(path.resolve()), 'sha256': sha(path)}


def remaining_slots(detail, matrix, reviewed, terminal):
    """All nonterminal logical slots can run, including pending and grading slots.

    This bounds concurrent solver attempts only under the existing serial-retry
    contract: retries replace a logical slot, never add overlapping solver slots.
    No scores, artifact availability, or inferred process health release capacity.
    """
    request, response = reviewed['request'], reviewed['response']
    eid = response['id']
    require(detail.get('id') == eid and isinstance(matrix, dict) and matrix.get('evaluationId') == eid,
            'Missing/foreign tail matrix')
    require(detail.get('nAttemptsPerProblem') == response.get('nAttemptsPerProblem') == request['metadata']['attemptsPerProblem'] == 3,
            'Tail attempt count changed')
    def problems(items):
        rows = [(x['problemId'], x['problemVersionId'], x['environmentId']) for x in items]
        require(rows and len(set(rows)) == len(rows) and len({x[1] for x in rows}) == len(rows),
                'Duplicate/empty tail problem membership')
        return set(rows)
    expected = problems(request['problems'])
    require(problems(response['problems']) == expected == problems(detail['problems']), 'Tail problem membership changed')
    requested = {x['runConfigVersionId']: x['concurrency'] for x in request['solvers']}
    require(len(requested) == len(request['solvers']), 'Duplicate requested tail solver')
    def solvers(items):
        rows = [(x['id'], x['runConfigVersionId'], x['metadata']['concurrency']) for x in items]
        require(len(rows) == len(requested) and len({x[0] for x in rows}) == len(rows)
                and len({x[1] for x in rows}) == len(rows), 'Duplicate/missing tail solver')
        require({x[1]: x[2] for x in rows} == requested, 'Tail solver/config/concurrency changed')
        require(all(x.get('evaluationId') == eid for x in items), 'Foreign tail solver')
        return set(rows)
    expected_solvers = solvers(response['solvers'])
    require(solvers(detail['solvers']) == expected_solvers, 'Tail solver identity changed')
    solver_ids = {x[0] for x in expected_solvers}; version_ids = {x[1] for x in expected}
    expected_pairs = {(s, v) for s in solver_ids for v in version_ids}
    cells = matrix.get('cells')
    require(isinstance(cells, list) and len(cells) == len(expected_pairs), 'Incomplete tail matrix')
    pairs = [(c.get('solverId'), c.get('problemVersionId')) for c in cells]
    require(len(set(pairs)) == len(pairs) and set(pairs) == expected_pairs, 'Unexpected/duplicate tail matrix member')
    remaining = dict.fromkeys(solver_ids, 0); all_run_ids = set()
    for cell in cells:
        attempts = cell.get('attempts')
        require(isinstance(attempts, list) and len(attempts) == 3
                and {a.get('attemptNumber') for a in attempts} == {1, 2, 3}
                and all(type(a.get('attemptNumber')) is int for a in attempts), 'Incomplete/duplicate tail attempt slots')
        for attempt in attempts:
            run = attempt.get('problemRunId'); status = attempt.get('status')
            require(isinstance(run, str) and run and run not in all_run_ids, 'Missing/duplicate tail run identity')
            require(isinstance(status, str) and status, 'Missing tail attempt status')
            all_run_ids.add(run)
            if status not in terminal:
                remaining[cell['solverId']] += 1
    return remaining
