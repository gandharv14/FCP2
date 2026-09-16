"""Read-only reconciliation of an uncertain evaluation bulk-create request.

Only evaluations list/get are called. A uniquely matching accepted evaluation can
be recorded as the missing response; this module never submits or retries a job.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import recursion_campaign as c


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'Timestamp must include a timezone')
    return result


def problem_set(items):
    tuples = [(x['problemId'], x['problemVersionId'], x['environmentId']) for x in items]
    require(tuples and len(set(tuples)) == len(tuples), 'Empty or duplicate problem slots')
    require(len({x[0] for x in tuples}) == len(tuples), 'Duplicate problem identity')
    require(len({x[1] for x in tuples}) == len(tuples), 'Duplicate problem version')
    return set(tuples)


def solver_set(items, *, detail=False):
    tuples = []
    for item in items:
        concurrency = item['metadata']['concurrency'] if detail else item['concurrency']
        require(type(concurrency) is int and concurrency > 0, 'Invalid solver concurrency')
        tuples.append((item['runConfigVersionId'], item['displayName'], concurrency))
    require(tuples and len(set(tuples)) == len(tuples), 'Empty or duplicate solver slots')
    require(len({x[0] for x in tuples}) == len(tuples), 'Duplicate solver configuration')
    require(len({x[1] for x in tuples}) == len(tuples), 'Duplicate solver display name')
    return set(tuples)


def validate_request(request):
    require(set(request) == {'name', 'description', 'metadata', 'tags', 'solvers', 'problems'},
            'Unsupported bulk-create payload fields; do not infer omitted behavior')
    require(isinstance(request['name'], str) and request['name'], 'Missing evaluation name')
    metadata = request['metadata']
    require(metadata.get('schemaVersion') == 1 and metadata.get('attemptsPerProblem') == 3,
            'Reconciliation requires the exact pass-at-three request')
    require(metadata.get('successThreshold') == 1, 'Expected full-pass threshold')
    retry = metadata.get('retryPolicy', {})
    require(type(retry.get('maxInfraRetries')) is int and retry['maxInfraRetries'] >= 0,
            'Missing explicit infrastructure retry policy')
    require(len(set(request['tags'])) == len(request['tags']), 'Duplicate request tags')
    problem_set(request['problems'])
    solver_set(request['solvers'])


def exact_match(request, detail, earliest):
    """Return explicit mismatch reasons; never select a candidate by its scores."""
    reasons = []
    try:
        require(detail['kind'] == 'solve_and_grade', 'Evaluation kind differs')
        require(detail['name'] == request['name'], 'Evaluation name differs')
        require(instant(detail['createdAt']) >= earliest, 'Evaluation predates intent')
        require(detail['description'] == request['description'], 'Description differs')
        require(detail['metadata'] == request['metadata'], 'Attempt/threshold/retry metadata differs')
        require(detail['nAttemptsPerProblem'] == 3, 'Attempt count differs')
        require(detail.get('graderRunConfigVersionId') is None, 'Unexpected evaluation grader override')
        require(len(set(detail['tags'])) == len(detail['tags']) and set(detail['tags']) == set(request['tags']), 'Tags differ or duplicate')
        require(problem_set(detail['problems']) == problem_set(request['problems']), 'Problem/version/environment set differs')
        require(solver_set(detail['solvers'], detail=True) == solver_set(request['solvers']), 'Model configuration/display/concurrency set differs')
        require(detail['totalProblems'] == len(request['problems']), 'Problem total differs')
        require(detail['totalSolvers'] == len(request['solvers']), 'Solver total differs')
        require(all(x['evaluationId'] == detail['id'] for x in detail['problems'] + detail['solvers']), 'Child evaluation binding differs')
        for group in ['problems', 'solvers']:
            require(len({x['id'] for x in detail[group]}) == len(detail[group]), 'Duplicate child identity')
    except (ValueError, KeyError, TypeError) as exc:
        reasons.append(str(exc))
    return reasons


def reconcile(work, prefix, *, clock_skew_seconds=0, clock_skew_reason=None):
    work = Path(work).resolve()
    require(re.fullmatch(r'(evaluation|correction)-[A-Za-z0-9][A-Za-z0-9._-]*', prefix), 'Expected exact evaluation-/correction- prefix')
    require(type(clock_skew_seconds) is int and 0 <= clock_skew_seconds <= 5, 'Clock skew must be between zero and five seconds')
    require(not clock_skew_seconds or bool(clock_skew_reason), 'Nonzero clock skew requires an evidence-based explanation')
    intent_path, request_path, response_path = [work / (prefix + suffix) for suffix in ('.intent.json', '.request.json', '.response.json')]
    require(intent_path.is_file() and request_path.is_file(), 'Existing intent and request are required')
    require(not response_path.exists(), 'Response already exists; no reconciliation needed')
    with (work / (prefix + '.reconciliation.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not response_path.exists(), 'Response appeared before reconciliation lock')
        intent, request = c.read(intent_path), c.read(request_path)
        require(intent['args'] == ['evaluations', 'bulk-create'], 'Intent is not an evaluation bulk-create')
        require(Path(intent['payload']).resolve() == request_path, 'Intent does not bind the exact request file')
        validate_request(request)
        earliest = instant(intent['createdAt']) - timedelta(seconds=clock_skew_seconds)
        pinned = {str(intent_path): sha(intent_path), str(request_path): sha(request_path)}
        folder = work / (prefix + '.reconciliation') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        evidence = {'schemaVersion': 1, 'status': 'scanning', 'prefix': prefix,
                    'method': 'Read-only all-page evaluation listing and exact candidate detail comparison; never resubmit.',
                    'requestArtifactHashes': pinned, 'clockSkewSeconds': clock_skew_seconds,
                    'clockSkewReason': clock_skew_reason, 'earliestAcceptedCreatedAt': earliest.isoformat(),
                    'readArtifacts': {}, 'candidates': [], 'matches': [], 'checkedAt': datetime.now(timezone.utc).isoformat()}
        try:
            summaries, cursor, cursors = {}, None, set()
            page_number = 0
            while True:
                args = ['evaluations', 'list', '--limit', '100', '--sort-by', 'createdAt', '--sort-direction', 'desc']
                if cursor: args += ['--cursor', cursor]
                page = c.cli(*args)
                path = folder / f'page-{page_number:04d}.json'; c.save(path, page); evidence['readArtifacts'][str(path)] = sha(path)
                page_number += 1
                for item in page['items']:
                    require(item['id'] not in summaries, 'Duplicate evaluation identity in paginated listing')
                    summaries[item['id']] = item
                cursor = page.get('nextCursor')
                if not cursor: break
                require(cursor not in cursors, 'Repeated pagination cursor')
                cursors.add(cursor)
            evidence['listedEvaluationCount'] = len(summaries)
            matches = []
            for item in summaries.values():
                if item['name'] != request['name'] or instant(item['createdAt']) < earliest:
                    continue
                evaluation_id = item['id']
                detail = c.cli('evaluations', 'get', '--evaluation-id', evaluation_id)
                path = folder / f'candidate-{len(evidence["candidates"]):04d}.json'; c.save(path, detail); evidence['readArtifacts'][str(path)] = sha(path)
                reasons = exact_match(request, detail, earliest)
                if detail.get('id') != evaluation_id: reasons.append('Detail identity differs from listing')
                evidence['candidates'].append({'evaluationId': evaluation_id, 'detailPath': str(path), 'mismatchReasons': reasons})
                if not reasons: matches.append(detail)
            evidence['matches'] = [x['id'] for x in matches]
            require(len(matches) == 1, f'Expected one exact accepted evaluation; found {len(matches)}')
            for path, digest in pinned.items(): require(sha(path) == digest, 'Intent/request changed during read-only reconciliation')
            require(not response_path.exists(), 'Response appeared during reconciliation')
            evidence.update(status='unique_exact_match', recoveredEvaluationId=matches[0]['id'], recoveredResponseSha256=hashlib.sha256((json.dumps(matches[0], indent=2) + '\n').encode()).hexdigest())
            c.save(folder / 'evidence.json', evidence)
            # Link a fully written same-filesystem snapshot atomically. EEXIST
            # prevents replacing another caller's response; no partial JSON can
            # become the authoritative receipt if the process is interrupted.
            prepared_response = folder / 'recovered-response.json'
            c.save(prepared_response, matches[0])
            require(sha(prepared_response) == evidence['recoveredResponseSha256'], 'Prepared response hash differs')
            os.link(prepared_response, response_path)
            return {'status': 'recovered', 'evaluationId': matches[0]['id'], 'responsePath': str(response_path),
                    'evidencePath': str(folder / 'evidence.json'), 'evidenceSha256': sha(folder / 'evidence.json')}
        except Exception as exc:
            evidence.update(status='not_recovered', failure=str(exc))
            c.save(folder / 'evidence.json', evidence)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--clock-skew-seconds', type=int, default=0)
    parser.add_argument('--clock-skew-reason')
    args = parser.parse_args()
    print(json.dumps(reconcile(args.work, args.prefix, clock_skew_seconds=args.clock_skew_seconds,
                               clock_skew_reason=args.clock_skew_reason), indent=2))


if __name__ == '__main__': main()
