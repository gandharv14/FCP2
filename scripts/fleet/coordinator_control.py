"""Recover the existing local authority without reassigning any workbook.

The original process receipt, code, database, credentials and lock inodes remain
unchanged. A new journal is mandatory; uncertain starts are never replayed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import time

from scripts.fleet.pool import binding, lock, persist, read, safe


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def port_open():
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(('127.0.0.1', 8765)) == 0


def inspect(receipt_binding):
    receipt = read(receipt_binding)
    command = receipt['command']
    control = Path(receipt_binding['path']).parent
    database = safe(receipt['database'])
    frozen = control / 'coordinator-v1.py'
    require(database == control / 'fleet.sqlite3', 'original database differs')
    require(receipt['url'] == 'http://127.0.0.1:8765', 'local authority URL differs')
    require(hashlib.sha256(frozen.read_bytes()).hexdigest() == receipt['code_sha256'], 'frozen authority changed')
    private = safe(receipt['private_directory'])
    require(stat.S_IMODE(private.stat().st_mode) == 0o700, 'private directory mode differs')
    tokens = [private / (name + '.token') for name in ('worker', 'admin')]
    for token in tokens:
        safe(token)
        require(stat.S_IMODE(token.stat().st_mode) == 0o600 and token.stat().st_uid == os.getuid(), 'credential file custody differs')
        require(len(token.read_text().strip()) >= 32, 'retained credential is unavailable')
    require(tokens[0].read_bytes() != tokens[1].read_bytes(), 'authority credentials must remain distinct')
    expected = [command[0], '-B', str(frozen), 'serve', '--database', str(database),
                '--port', '8765', '--worker-token-file', str(tokens[0]), '--admin-token-file', str(tokens[1])]
    legacy = control.parents[1] / 'authoring-resume-20260910' / 'root'
    locks = [legacy / 'authoring-fleet-slots' / f'{n}.lock' for n in range(2)] + [legacy / 'model-slots' / 'slot-0.lock']
    require(receipt['legacy_locks'] == list(map(str, locks)), 'original exclusion locks differ')
    for path in locks:
        expected.extend(['--legacy-lock', str(path)])
    interpreter = Path(command[0])
    require(command == expected and interpreter.is_absolute() and '..' not in interpreter.parts
            and safe(interpreter.resolve()).is_file() and os.access(interpreter, os.X_OK),
            'original server command differs')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'authority database integrity failed')
        counts = dict(db.execute('SELECT state, count(*) FROM workbooks GROUP BY state'))
        operations = dict(db.execute('SELECT state, count(*) FROM operations GROUP BY state'))
    return dict(receipt=receipt_binding, original_process_present=pid_exists(receipt['pid']),
                local_port_open=port_open(), queue_counts=counts, operation_counts=operations,
                database_integrity='ok', automatic_retry=False, claims_reconciled=False)


def restart(receipt_binding, journal, *, execute=False):
    receipt = read(receipt_binding)
    control = Path(receipt_binding['path']).parent
    with lock(control / 'coordinator-recovery.lock', blocking=False):
        observed = inspect(receipt_binding)
        require(not observed['original_process_present'] and not observed['local_port_open'],
                'existing process or listener present; restart refused')
        # Fail before a durable restart intent if a local authoring worker has
        # taken custody while the authority was offline. Do not replace locks.
        descriptors = []
        try:
            for path in receipt['legacy_locks']:
                fd = os.open(safe(path), os.O_RDWR | os.O_NOFOLLOW)
                descriptors.append(fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            for fd in descriptors:
                os.close(fd)
        if not execute:
            return dict(observed, execute=False, restartable=True)
        journal = safe(journal)
        require(not journal.exists(), 'restart journal already exists; reconcile without retry')
        journal.mkdir(parents=True, mode=0o700)
        intent = persist(journal / 'intent.json', dict(observed, created_at=time.time(),
                         command=receipt['command'], driver=binding(__file__)), immutable=True)
        with (journal / 'coordinator.log').open('xb') as log:
            child = subprocess.Popen(receipt['command'], stdout=log, stderr=subprocess.STDOUT,
                                     cwd=control.parents[2], start_new_session=True)
        launched = persist(journal / 'launched.json', dict(intent=intent, pid=child.pid,
                            command=receipt['command'], automatic_retry=False), immutable=True)
        # A failed startup is retained. Never terminate/retry an uncertain child.
        for _ in range(50):
            if child.poll() is not None:
                raise RuntimeError('restarted authority exited; inspect retained log and intent')
            if port_open():
                from scripts.fleet.coordinator import Client
                current = Client(receipt['url'], Path(receipt['private_directory']) / 'worker.token').call('status')
                require(current['model_limit'] == 1 and current['publication_limit'] == 1, 'authority limits differ')
                return persist(journal / 'ready.json', dict(launched=launched, ready_at=time.time(),
                               model_limit=1, publication_limit=1, claims_reconciled=False), immutable=True)
            time.sleep(0.1)
        raise RuntimeError('authority startup response uncertain; reconcile without retry')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'restart'))
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--journal')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    row = dict(path=str(Path(args.receipt).absolute()), sha256=args.receipt_sha256)
    require(not args.execute or args.action == 'restart' and args.journal, 'explicit restart journal required')
    value = inspect(row) if args.action == 'inspect' else restart(row, args.journal, execute=args.execute)
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
