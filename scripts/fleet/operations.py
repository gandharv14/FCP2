"""Inspect or interrupt an explicitly frozen set of worker process identities.

This delegates signals to the reviewed pidfd controller. It cannot release
claims, settle model calls, stop the coordinator, or resume authoring.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

SCHEMA = 'fleet-selected-worker-controls/v1'
RUNNER_SHA = '9c4b8e73098da382292632e1fe7cf013b85cb5c5006a6cb0dba7e81215934520'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def checked(row):
    require(set(row) == {'path', 'sha256'}, 'exact file binding required')
    path = Path(row['path'])
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'canonical control file required')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'control file changed')
    return raw


def load(row):
    value = json.loads(checked(row))
    require(set(value) == {'schema_version', 'runner', 'workers'}, 'closed control manifest required')
    require(value['schema_version'] == SCHEMA and value['runner']['sha256'] == RUNNER_SHA,
            'unreviewed process controller')
    checked(value['runner'])
    require(isinstance(value['workers'], list) and 0 < len(value['workers']) <= 16,
            'explicit set of at most sixteen workers required')
    seen = set()
    for worker in value['workers']:
        require(set(worker) == {'workbook', 'plan'} and isinstance(worker['workbook'], str)
                and re.fullmatch(r'[0-9]{4}', worker['workbook']) is not None and worker['workbook'] not in seen,
                'duplicate or unknown worker selection')
        plan = json.loads(checked(worker['plan']))
        require(plan['workbook'] == worker['workbook'], 'worker plan identity differs')
        seen.add(worker['workbook'])
    return value


def retain(path, value):
    with path.open('x') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    handle = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def invoke(runner, worker, action):
    checked(runner)
    checked(worker['plan'])
    command = [sys.executable, '-I', '-B', runner['path'], action,
               '--plan', worker['plan']['path'], '--plan-sha256', worker['plan']['sha256']]
    result = subprocess.run(command, capture_output=True, timeout=270)
    if result.returncode:
        # Keep the underlying diagnostic in the caller's stderr log. There is
        # no second subprocess invocation after a failure or uncertain reply.
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
    require(result.returncode == 0, 'worker control failed; inspect its retained journal before retry')
    return json.loads(result.stdout)


def run(row, action, journal=None, *, caller=invoke):
    require(action in {'status', 'interrupt'}, 'only status or interrupt is supported')
    value = load(row)
    require((action == 'interrupt') == (journal is not None), 'interrupt requires a fresh journal')
    directory = None
    if journal is not None:
        directory = Path(journal).absolute()
        require(not any(p.is_symlink() for p in (directory, *directory.parents)), 'canonical journal required')
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        handle = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
        retain(directory / 'intent.json', dict(manifest=row, action=action, automatic_retry=False,
                                              automatic_resume=False))
    results = []
    for worker in value['workers']:
        prefix = directory / worker['workbook'] if directory else None
        try:
            observed = caller(value['runner'], worker, 'status')
            if prefix:
                retain(prefix.with_suffix('.status.json'), observed)
            state = observed['process']['state']
            require(state in {'RUNNING', 'ABSENT', 'EXITED_UNREAPED'}, 'process identity requires reconciliation')
            if action == 'interrupt' and state == 'RUNNING':
                require(observed['interrupt_intent_exists'] is False, 'prior interruption requires reconciliation')
                # A lost response cannot cause a second signal. The underlying
                # controller independently reserves its own durable intent.
                retain(prefix.with_suffix('.signal-intent.json'), dict(plan=worker['plan'], automatic_retry=False))
                response = caller(value['runner'], worker, 'interrupt')
                retain(prefix.with_suffix('.signal-response.json'), response)
                state = 'INTERRUPT_RECORDED_RECONCILIATION_REQUIRED'
            result = dict(workbook=worker['workbook'], state=state)
            results.append(result)
            print(json.dumps(result), flush=True)
        except BaseException as exc:
            if prefix:
                retain(prefix.with_suffix('.uncertain.json'), dict(failure_type=type(exc).__name__,
                       automatic_retry=False, automatic_resume=False))
            raise
    if directory:
        retain(directory / 'complete.json', dict(results=results, claims_released=False,
               model_operations_settled=False, automatic_resume=False))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'interrupt'))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--journal')
    args = parser.parse_args()
    run(dict(path=str(Path(args.manifest).absolute()), sha256=args.manifest_sha256),
        args.action, args.journal)


if __name__ == '__main__':
    main()
