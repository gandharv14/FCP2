"""Read-only, compressed trajectory collection for the reward-extremes audit."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import recursion_campaign as c

WORK = c.ROOT / 'reports/recursion/gemini-high-score-audit-20260915/refactor'
AUDIT = WORK / 'results/reward-extremes-audit'


def fetch(task_id, model, attempt):
    run_id = attempt['problemRunId']
    folder = AUDIT / 'trajectories' / task_id / model
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (run_id + '.json.gz')
    receipt = folder / (run_id + '.receipt.json')
    if receipt.exists():
        prior = c.read(receipt)
        if prior.get('status') == 'retrieved':
            assert hashlib.sha256(path.read_bytes()).hexdigest() == prior['compressedSha256']
            return prior
    record = {'taskId': task_id, 'model': model, 'problemRunId': run_id,
              'attemptNumber': attempt['attemptNumber'],
              'problemVersionId': attempt['problemVersionId'],
              'evaluationId': attempt['evaluationId'],
              'retrievedAt': datetime.now(timezone.utc).isoformat(),
              'source': 'problem-runs get-transcript', 'remoteMutations': 0}
    for retry in range(3):
        try:
            if shutil.disk_usage(AUDIT).free < 180 * 1024 * 1024:
                raise RuntimeError('Audit collection disk reserve reached')
            value = c.cli('problem-runs', 'get-transcript', '--problem-run-id', run_id,
                          timeout_seconds=120)
            raw = json.dumps(value, ensure_ascii=False).encode()
            packed = gzip.compress(raw, compresslevel=6, mtime=0)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(packed)
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            record.update(status='retrieved', path=str(path), bytes=len(raw),
                          compressedBytes=len(packed),
                          rawSha256=hashlib.sha256(raw).hexdigest(),
                          compressedSha256=hashlib.sha256(packed).hexdigest())
            break
        except RuntimeError as error:
            # Read errors may be retried; never dispatch a replacement solver run.
            message = str(error)[:300]
            transient = any(token in message.lower() for token in
                            ('timed out', 'gateway', 'fetch failed', 'econnreset',
                             'internal server error', 'service unavailable', '429'))
            if transient and retry < 2:
                time.sleep(2 ** retry)
                continue
            record.update(status='unavailable', error=message)
            break
    c.save(receipt, record)
    os.chmod(receipt, 0o600)
    return record


def load_transcript(path):
    value = json.loads(gzip.decompress(Path(path).read_bytes()))
    for _ in range(4):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            break
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--workers', type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    rows = c.read(AUDIT / f'shard-{args.shard}.json')
    jobs = [(r['taskId'], model, a) for r in rows for model, runs in r['models'].items()
            for a in runs]
    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, *job) for job in jobs]
        for future in as_completed(futures):
            records.append(future.result())
            if len(records) % 50 == 0 or len(records) == len(jobs):
                print(json.dumps({'shard': args.shard, 'processed': len(records),
                                  'total': len(jobs),
                                  'retrieved': sum(r['status'] == 'retrieved' for r in records)}),
                      flush=True)
    c.save(AUDIT / f'shard-{args.shard}-collection.json', records)


if __name__ == '__main__':
    main()
