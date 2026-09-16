"""Freeze completed evaluation matrices; keep their actual read time visible."""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_terminal(detail, matrix, request, receipt):
    assert detail['id'] == matrix['evaluationId'] == receipt['id']
    assert detail['status'] == 'completed'
    solvers = detail['solvers']
    assert solvers and all(s['jobStatus'] == 'completed' for s in solvers)
    assert {s['runConfigVersionId'] for s in solvers} == {s['runConfigVersionId'] for s in request['solvers']}
    assert request['metadata']['attemptsPerProblem'] == 3
    assert request['metadata']['successThreshold'] == 1
    expected = {(s['id'], p['problemVersionId']) for s in solvers for p in request['problems']}
    cells = matrix['cells']
    assert len(cells) == len(expected)
    assert {(x['solverId'], x['problemVersionId']) for x in cells} == expected
    ids = []
    for cell in cells:
        assert len(cell['attempts']) == 3
        assert {a['attemptNumber'] for a in cell['attempts']} == {1, 2, 3}
        for a in cell['attempts']:
            assert a['status'] in ('completed', 'failed', 'cancelled')
            assert a.get('score') in (None, 0, 1)
            ids.append(a['problemRunId'])
    assert len(set(ids)) == len(ids)


def freeze(work, prefix, matrix_retrieved_at):
    work = Path(work)
    paths = {
        'detail': work / 'results' / (prefix + '-detail.json'),
        'matrix': work / 'results' / (prefix + '-matrix.json'),
        'request': work / (prefix + '.request.json'),
        'receipt': work / (prefix + '.response.json'),
    }
    data = {k: json.loads(p.read_text()) for k, p in paths.items()}
    validate_terminal(**data)
    destination = work / 'results/terminal-matrix-cache' / prefix
    assert not destination.exists(), 'Terminal snapshot already exists'
    destination.mkdir(parents=True)
    hashes = {}
    for name, path in paths.items():
        target = destination / (name + '.json')
        target.write_bytes(path.read_bytes())
        hashes[name] = digest(target)
        target.chmod(0o444)
    manifest = {'schemaVersion': 1, 'evaluationId': data['detail']['id'],
                'matrixRetrievedAt': matrix_retrieved_at, 'sha256': hashes,
                'detailCanonicalSha256': canonical(data['detail'])}
    target = destination / 'manifest.json'
    target.write_text(json.dumps(manifest, indent=2) + '\n')
    target.chmod(0o444)
    return manifest


def load(work, prefix, fresh_detail):
    work = Path(work)
    directory = work / 'results/terminal-matrix-cache' / prefix
    if not directory.exists():
        return None
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['schemaVersion'] == 1
    for name, expected in manifest['sha256'].items():
        assert digest(directory / (name + '.json')) == expected, 'Terminal cache evidence changed'
    assert digest(work / (prefix + '.request.json')) == manifest['sha256']['request']
    assert digest(work / (prefix + '.response.json')) == manifest['sha256']['receipt']
    if canonical(fresh_detail) != manifest['detailCanonicalSha256']:
        return None
    matrix = json.loads((directory / 'matrix.json').read_text())
    validate_terminal(fresh_detail, matrix,
                      json.loads((directory / 'request.json').read_text()),
                      json.loads((directory / 'receipt.json').read_text()))
    return matrix, {'kind': 'frozen_terminal_matrix', 'matrixRetrievedAt': manifest['matrixRetrievedAt'],
                    'manifestPath': str(directory / 'manifest.json'),
                    'manifestSha256': digest(directory / 'manifest.json')}
