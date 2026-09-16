"""Separate, read-only recovery of already-known evaluation attempt slots.

Seed snapshots and recovery receipts are immutable and private. Only documented
problem-runs list-for-problem/get reads are used; collector files are never written.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import recursion_campaign as c
import recursion_reconcile_evaluation as match

STATES = {'pending', 'running', 'grading', 'completed', 'failed', 'cancelled'}
TERMINAL = {'completed', 'failed', 'cancelled'}


def require(value, code):
    if not value: raise ValueError(code)


def now(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_call(*args):
    require(args[:2] in (('problem-runs','list-for-problem'),('problem-runs','get')),'non_read_route_rejected')
    # Keep credentials in the existing wrapper's environment, never in argv.
    result=subprocess.run([sys.executable,str(c.ROOT/'scripts/recursion_cli.py'),*args],
                          cwd=c.ROOT,capture_output=True,text=True,timeout=60)
    if result.returncode: raise RuntimeError('documented_read_failed')
    return json.loads(result.stdout)


def immutable_bytes(path, data):
    """Publish complete bytes atomically without replacing an existing receipt."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == data, 'immutable_receipt_differs')
        return
    temporary = path.parent / ('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600); stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try: os.link(temporary, path)
        except FileExistsError: require(path.read_bytes() == data, 'immutable_receipt_race')
    finally:
        if temporary.exists(): temporary.unlink()


def immutable_json(path, value):
    immutable_bytes(path, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())


def clean_outcome(status, score, started=None, completed=None, execution=None, has_error=False):
    require(status in STATES, 'unknown_run_status')
    require(score is None or (type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1), 'invalid_run_score')
    for stamp in (started, completed):
        if stamp is not None: match.instant(stamp)
    require(execution is None or (type(execution) is int and execution >= 0), 'invalid_execution_time')
    return {'status': status, 'score': score, 'startedAt': started, 'completedAt': completed,
            'executionTimeMs': execution, 'hasError': bool(has_error)}


def membership(request, detail, matrix):
    match.validate_request(request)
    require(not match.exact_match(request, detail, match.instant(detail['createdAt'])), 'seed_detail_request_mismatch')
    require(matrix['evaluationId'] == detail['id'], 'seed_evaluation_mismatch')
    solvers = {s['id']: s for s in detail['solvers']}
    problems = {p['problemVersionId']: p for p in detail['problems']}
    expected = {(s, p) for s in solvers for p in problems}; seen = set(); ids = set(); slots = []
    for cell in matrix['cells']:
        identity = (cell['solverId'], cell['problemVersionId'])
        require(identity in expected and identity not in seen, 'duplicate_or_foreign_seed_cell'); seen.add(identity)
        require(len(cell['attempts']) == 3 and {x['attemptNumber'] for x in cell['attempts']} == {1,2,3}, 'missing_or_duplicate_seed_attempt')
        problem = problems[cell['problemVersionId']]; solver = solvers[cell['solverId']]
        for attempt in cell['attempts']:
            rid = attempt['problemRunId']; require(isinstance(rid, str) and rid and rid not in ids, 'missing_or_duplicate_seed_run'); ids.add(rid)
            require(type(attempt['attemptNumber']) is int, 'invalid_seed_attempt_number')
            slots.append({'evaluationId': detail['id'], 'solverId': solver['id'], 'problemId': problem['problemId'],
                          'problemVersionId': problem['problemVersionId'], 'environmentId': problem['environmentId'],
                          'solverRunConfigVersionId': solver['runConfigVersionId'], 'attemptNumber': attempt['attemptNumber'],
                          'problemRunId': rid, 'seedOutcome': clean_outcome(attempt['status'], attempt['score'], attempt.get('startedAt'),
                            attempt.get('completedAt'), attempt.get('executionTimeMs'), bool(attempt.get('errorMessage')))})
    require(seen == expected, 'incomplete_seed_cells')
    return sorted(slots, key=lambda x: (x['problemId'], x['solverId'], x['attemptNumber']))


def prepare_seed(directory, request_path, detail_path, matrix_path):
    """Local-only exact snapshot. Original collector files may later advance."""
    directory = Path(directory).resolve(); seed_path = directory / 'seed.json'
    if seed_path.exists():
        seed = validate_seed(directory)
        require(all(Path(path).resolve() == Path(seed['sources'][kind]['originalPath']) for kind,path in
                    [('request',request_path),('detail',detail_path),('matrix',matrix_path)]), 'existing_seed_uses_other_sources')
        return seed
    captured = {}
    for kind, source in [('request',request_path),('detail',detail_path),('matrix',matrix_path)]:
        source = Path(source).resolve(); data = source.read_bytes(); destination = directory/'seed-artifacts'/f'{kind}.json'
        immutable_bytes(destination, data)
        captured[kind] = {'originalPath':str(source), 'snapshotPath':str(destination), 'sha256':sha(destination),
                          'originalFileModifiedAt':datetime.fromtimestamp(source.stat().st_mtime,timezone.utc).isoformat()}
    request, detail, matrix = [read(captured[k]['snapshotPath']) for k in ('request','detail','matrix')]
    slots = membership(request,detail,matrix)
    seed = {'schemaVersion':1,'kind':'known_evaluation_slot_seed','createdAt':now(),'evaluationId':detail['id'],
            'sources':captured,'slots':slots,'problemCount':len(detail['problems']),'solverCount':len(detail['solvers']),
            'cellCount':len(matrix['cells']),'runCount':len(slots),
            'membershipSha256':hashlib.sha256(canonical(slots)).hexdigest(),
            'note':'Seed matrix membership is authoritative. Source-file modified time is not an API fetch timestamp.'}
    immutable_json(seed_path,seed)
    return validate_seed(directory)


def validate_seed(directory):
    directory=Path(directory); seed=read(directory/'seed.json')
    require(seed['kind']=='known_evaluation_slot_seed','wrong_seed_kind')
    for source in seed['sources'].values(): require(sha(source['snapshotPath'])==source['sha256'],'seed_snapshot_changed')
    slots=membership(*(read(seed['sources'][k]['snapshotPath']) for k in ('request','detail','matrix')))
    require(slots==seed['slots'] and hashlib.sha256(canonical(slots)).hexdigest()==seed['membershipSha256'],'seed_membership_changed')
    require(len(slots)==seed['runCount'],'seed_count_changed')
    return seed


def seed_from_prefix(work, prefix, directory):
    require(re.fullmatch(r'(evaluation|correction)-[A-Za-z0-9][A-Za-z0-9._-]*',prefix),'invalid_evaluation_prefix')
    work=Path(work)
    return prepare_seed(directory,work/(prefix+'.request.json'),work/'results'/(prefix+'-detail.json'),
                        work/'results'/(prefix+'-matrix.json'))


def clean_run(run, slot):
    """Project only public result fields; discard prompt/transcript/grader archives."""
    bindings={'id':'problemRunId','problemId':'problemId','problemVersionId':'problemVersionId',
              'attemptNumber':'attemptNumber','solverRunConfigVersionId':'solverRunConfigVersionId'}
    require(all(run.get(field)==slot[expected] for field,expected in bindings.items()),'run_binding_mismatch')
    require(type(run['attemptNumber']) is int,'invalid_returned_attempt_number')
    require(run.get('source')=='evaluation','run_source_mismatch')
    return clean_outcome(run['status'],run['finalScore'],run.get('startedAt'),run.get('completedAt'),
                         run.get('executionTimeMs'),bool(run.get('errorMessage') or run.get('gradingError')))


def _validate_problem_receipt(receipt, slots, seed_hash):
    require(receipt['seedSha256']==seed_hash,'receipt_seed_mismatch')
    expected={s['problemRunId']:s for s in slots}; result=receipt['runs']
    require(len(result)==len(expected) and {x['problemRunId'] for x in result}==set(expected),'receipt_slots_incomplete')
    for run in result:
        slot=expected[run['problemRunId']]
        require(run['slot']=={k:v for k,v in slot.items() if k!='seedOutcome'},'receipt_slot_binding_changed')
        require(run['outcome']==clean_outcome(**{'status':run['outcome']['status'],'score':run['outcome']['score'],
                    'started':run['outcome']['startedAt'],'completed':run['outcome']['completedAt'],
                    'execution':run['outcome']['executionTimeMs'],'has_error':run['outcome']['hasError']}),'receipt_outcome_invalid')
        provenance=run['provenance'];require(provenance['kind'] in ('fresh_read','cached_seed_terminal'),'unknown_freshness')
        if provenance['kind']=='cached_seed_terminal':
            require(slot['seedOutcome']['status'] in TERMINAL and run['outcome']==slot['seedOutcome'],'cached_nonterminal_or_changed_outcome')
            require(provenance.get('readAt') is None,'cached_seed_claims_fresh_read')
        else:match.instant(provenance['readAt'])


def recover_problem(directory, problem_id, slots, seed_hash, cache_seed_terminal):
    directory=Path(directory); destination=directory/'problems'/f'{problem_id}.json'
    if destination.exists():
        require(read(destination.with_suffix('.seal.json'))=={'receiptSha256':sha(destination),'seedSha256':seed_hash},'receipt_seal_mismatch')
        receipt=read(destination);_validate_problem_receipt(receipt,slots,seed_hash)
        return {'problemId':problem_id,'receiptPath':str(destination),'sha256':sha(destination),'resumed':True}
    wanted={s['problemRunId']:s for s in slots}; fresh={}; routes=[]
    needed={rid:s for rid,s in wanted.items() if not(cache_seed_terminal and s['seedOutcome']['status'] in TERMINAL)}
    try:
        if needed:
            history=read_call('problem-runs','list-for-problem','--problem-id',problem_id)
            read_at=now();require(type(history.get('truncated')) is bool,'missing_history_truncation_flag')
            rows=history['items'];require(isinstance(rows,list),'invalid_history_items')
            history_ids=[x['id'] for x in rows];require(len(history_ids)==len(set(history_ids)),'duplicate_history_run_id')
            present={}
            for run in rows:
                # Other historical runs are irrelevant, not inferred into slots.
                if run['id'] in wanted:present[run['id']]=clean_run(run,wanted[run['id']])
            routes.append({'operation':'problem-runs.list-for-problem','problemId':problem_id,'readAt':read_at,
                           'truncated':history['truncated'],'returnedCount':len(rows),'matchedKnownCount':len(present)})
            for rid,slot in needed.items():
                if not history['truncated'] and rid in present:
                    fresh[rid]=(present[rid],read_at,'problem-runs.list-for-problem')
                else:
                    run=read_call('problem-runs','get','--problem-run-id',rid); fetched=now()
                    fresh[rid]=(clean_run(run,slot),fetched,'problem-runs.get')
                    routes.append({'operation':'problem-runs.get','problemRunId':rid,'readAt':fetched})
        result=[]
        for slot in slots:
            rid=slot['problemRunId']
            if rid in fresh:
                outcome,read_at,route=fresh[rid];provenance={'kind':'fresh_read','readAt':read_at,'route':route}
            else:
                require(cache_seed_terminal and slot['seedOutcome']['status'] in TERMINAL,'missing_known_run')
                seed=read(directory/'seed.json')
                outcome=slot['seedOutcome'];provenance={'kind':'cached_seed_terminal','readAt':None,'route':None,
                    'seedSnapshotCreatedAt':seed['createdAt'],
                    'sourceMatrixFileModifiedAt':seed['sources']['matrix']['originalFileModifiedAt'],
                    'sourceMatrixSha256':seed['sources']['matrix']['sha256']}
            result.append({'problemRunId':rid,'slot':{k:v for k,v in slot.items() if k!='seedOutcome'},'outcome':outcome,'provenance':provenance})
        receipt={'schemaVersion':1,'seedSha256':seed_hash,'problemId':problem_id,'createdAt':now(),'routes':routes,'runs':result}
        _validate_problem_receipt(receipt,slots,seed_hash);immutable_json(destination,receipt)
        immutable_json(destination.with_suffix('.seal.json'),{'receiptSha256':sha(destination),'seedSha256':seed_hash})
        return {'problemId':problem_id,'receiptPath':str(destination),'sha256':sha(destination),'resumed':False}
    except Exception as exc:
        # Raw exception bodies / returned DTOs can contain confidential content.
        failure={'problemId':problem_id,'seedSha256':seed_hash,'failedAt':now(),'exceptionType':type(exc).__name__,
                 'code':str(exc) if isinstance(exc,ValueError) and re_safe_code(str(exc)) else 'read_or_validation_failed','completedReadRoutes':routes}
        immutable_json(directory/'failures'/f'{problem_id}-{uuid.uuid4().hex}.json',failure)
        raise ValueError('problem_recovery_failed:'+problem_id) from None


def re_safe_code(value): return value.replace('_','').isalnum() and len(value)<100


def recover(directory, *, reads_authorized=False, max_workers=4, cache_seed_terminal=False):
    require(reads_authorized,'read_execution_not_authorized')
    require(type(max_workers) is int and 1<=max_workers<=4,'parallelism_exceeds_four')
    directory=Path(directory).resolve();seed=validate_seed(directory);seed_hash=sha(directory/'seed.json')
    with (directory/'recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        policy={'seedSha256':seed_hash,'cacheSeedTerminal':cache_seed_terminal,'maxWorkers':max_workers}
        immutable_json(directory/'policy.json',policy)
        grouped={}
        for slot in seed['slots']:grouped.setdefault(slot['problemId'],[]).append(slot)
        done=[];failures=[]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures={pool.submit(recover_problem,directory,p,slots,seed_hash,cache_seed_terminal):p for p,slots in grouped.items()}
            for future in as_completed(futures):
                try:done.append(future.result())
                except Exception:
                    failures.append(futures[future])
                    # Stop queued work after the first failure; at most the four
                    # already-running problem workers finish. Resume is explicit.
                    for pending in futures:pending.cancel()
        if failures:
            immutable_json(directory/'cycles'/f'{uuid.uuid4().hex}.json',{'status':'incomplete','seedSha256':seed_hash,'checkedAt':now(),
                           'successfulProblems':len(done),'failedProblemIds':sorted(failures),'note':'No complete recovery artifact emitted. Resume reads explicitly; successful receipts retain original fetch timestamps.'})
            raise ValueError('recovery_incomplete:'+str(len(failures)))
        return assemble(directory)


def assemble(directory):
    directory=Path(directory);seed=validate_seed(directory);seed_hash=sha(directory/'seed.json');grouped={}
    for slot in seed['slots']:grouped.setdefault(slot['problemId'],[]).append(slot)
    all_runs=[];receipts={}
    for problem,slots in grouped.items():
        path=directory/'problems'/f'{problem}.json';receipt=read(path);_validate_problem_receipt(receipt,slots,seed_hash)
        require(read(path.with_suffix('.seal.json'))=={'receiptSha256':sha(path),'seedSha256':seed_hash},'receipt_seal_mismatch')
        receipts[str(path.resolve())]=sha(path);all_runs.extend(receipt['runs'])
    require(len(all_runs)==seed['runCount'] and len({x['problemRunId'] for x in all_runs})==len(all_runs),'assembly_missing_or_duplicate_run')
    cells={}
    for run in all_runs:
        slot=run['slot'];identity=(slot['solverId'],slot['problemVersionId'])
        cell=cells.setdefault(identity,{'solverId':identity[0],'problemVersionId':identity[1],'attempts':[]})
        cell['attempts'].append({'problemRunId':run['problemRunId'],'attemptNumber':slot['attemptNumber'],**run['outcome'],'provenance':run['provenance']})
    matrix=[]
    for _,cell in sorted(cells.items()):
        cell['attempts'].sort(key=lambda x:x['attemptNumber']);require([x['attemptNumber'] for x in cell['attempts']]==[1,2,3],'assembly_slot_mismatch');matrix.append(cell)
    output={'schemaVersion':1,'kind':'sanitized_known_slot_recovery','status':'read_cycle_complete','evaluationId':seed['evaluationId'],
            'seedSha256':seed_hash,'receiptHashes':receipts,'cells':matrix,'runCount':len(all_runs),
            'freshReadRunCount':sum(x['provenance']['kind']=='fresh_read' for x in all_runs),
            'cachedSeedTerminalRunCount':sum(x['provenance']['kind']=='cached_seed_terminal' for x in all_runs),
            'earliestFreshReadAt':min((x['provenance']['readAt'] for x in all_runs if x['provenance']['kind']=='fresh_read'),default=None),
            'latestFreshReadAt':max((x['provenance']['readAt'] for x in all_runs if x['provenance']['kind']=='fresh_read'),default=None),
            'notRefreshedFields':['infraRetryCount','costUsd','token metrics'],
            'note':'Separate recovery artifact; not a replacement collector snapshot. Fresh readAt values remain attached to each slot, including resumed receipts. Null scores remain null.'}
    immutable_json(directory/'recovered-slots.json',output)
    return {'status':output['status'],'evaluationId':seed['evaluationId'],'cellCount':len(matrix),'runCount':len(all_runs),
            'freshReadRunCount':output['freshReadRunCount'],'cachedSeedTerminalRunCount':output['cachedSeedTerminalRunCount'],
            'artifactPath':str(directory/'recovered-slots.json'),'artifactSha256':sha(directory/'recovered-slots.json')}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['seed','read','assemble']);p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--request',type=Path);p.add_argument('--detail',type=Path);p.add_argument('--matrix',type=Path)
    p.add_argument('--work',type=Path);p.add_argument('--prefix')
    p.add_argument('--authorize-reads',action='store_true');p.add_argument('--cache-seed-terminal',action='store_true');p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if a.action=='seed':
        if a.prefix:
            require(a.work is not None and not any([a.request,a.detail,a.matrix]),'prefix_requires_work_without_explicit_sources')
            s=seed_from_prefix(a.work,a.prefix,a.directory)
        else:s=prepare_seed(a.directory,a.request,a.detail,a.matrix)
        result={k:s[k] for k in ('evaluationId','problemCount','solverCount','cellCount','runCount')}
    elif a.action=='read':result=recover(a.directory,reads_authorized=a.authorize_reads,max_workers=a.workers,cache_seed_terminal=a.cache_seed_terminal)
    else:result=assemble(a.directory)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
