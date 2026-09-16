import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from scripts.fleet import coordinator_control as C
from scripts.fleet.pool import binding


@pytest.fixture
def authority(tmp_path, monkeypatch):
    control = tmp_path / 'repo/diagnostics/fleet/control'
    control.mkdir(parents=True)
    code = control / 'coordinator-v1.py'
    code.write_text('# immutable fixture\n')
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    for name in ('worker', 'admin'):
        path = private / (name + '.token')
        path.write_text(name * 32)
        path.chmod(0o600)
    database = control / 'fleet.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE workbooks(state TEXT)')
        db.execute('CREATE TABLE operations(state TEXT)')
        db.execute("INSERT INTO workbooks VALUES('hold')")
        db.execute("INSERT INTO operations VALUES('hold')")
    legacy = control.parents[1] / 'authoring-resume-20260910/root'
    locks = [legacy / 'authoring-fleet-slots' / f'{n}.lock' for n in range(2)] + [legacy / 'model-slots/slot-0.lock']
    for path in locks:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original inode')
    command = [sys.executable, '-B', str(code), 'serve', '--database', str(database), '--port', '8765',
               '--worker-token-file', str(private / 'worker.token'), '--admin-token-file', str(private / 'admin.token')]
    for path in locks:
        command.extend(['--legacy-lock', str(path)])
    receipt = control / 'coordinator-process.json'
    receipt.write_text(json.dumps(dict(pid=99999999, command=command, private_directory=str(private),
        code_sha256=C.hashlib.sha256(code.read_bytes()).hexdigest(), legacy_locks=list(map(str, locks)),
        url='http://127.0.0.1:8765', database=str(database))))
    monkeypatch.setattr(C, 'pid_exists', lambda pid: False)
    monkeypatch.setattr(C, 'port_open', lambda: False)
    return binding(receipt), control, private, locks


def test_read_only_preserves_uncertain_operations_and_lock_inodes(authority):
    row, control, _, locks = authority
    before = {str(p): (p.stat().st_ino, p.read_bytes()) for p in [*locks, control / 'fleet.sqlite3']}
    result = C.restart(row, None)
    assert result['restartable'] and result['operation_counts'] == {'hold': 1}
    assert result['claims_reconciled'] is False
    assert before == {str(p): (p.stat().st_ino, p.read_bytes()) for p in [*locks, control / 'fleet.sqlite3']}


@pytest.mark.parametrize('alive', ['process', 'listener'])
def test_refuses_live_authority_before_intent(authority, monkeypatch, alive):
    row, control, _, _ = authority
    monkeypatch.setattr(C, 'pid_exists' if alive == 'process' else 'port_open', lambda *args: True)
    with pytest.raises(ValueError, match='existing process or listener'):
        C.restart(row, control / 'restart', execute=True)
    assert not (control / 'restart').exists()


def test_changed_code_refused(authority):
    row, control, _, _ = authority
    (control / 'coordinator-v1.py').write_text('changed')
    with pytest.raises(ValueError, match='frozen authority changed'):
        C.inspect(row)


def test_exposed_credential_refused_without_printing_secret(authority):
    row, _, private, _ = authority
    (private / 'worker.token').chmod(0o644)
    with pytest.raises(ValueError, match='credential file custody differs'):
        C.inspect(row)


def test_existing_restart_intent_never_replayed(authority, monkeypatch):
    row, control, _, _ = authority
    journal = control / 'restart'
    journal.mkdir()
    monkeypatch.setattr(C.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not start'))
    with pytest.raises(ValueError, match='journal already exists'):
        C.restart(row, journal, execute=True)


def test_start_failure_retains_intent_and_original_receipt(authority, monkeypatch):
    row, control, _, _ = authority
    original = Path(row['path']).read_bytes()
    def fail(*args, **kwargs):
        raise OSError('fixture failure')
    monkeypatch.setattr(C.subprocess, 'Popen', fail)
    with pytest.raises(OSError):
        C.restart(row, control / 'restart', execute=True)
    assert (control / 'restart/intent.json').exists()
    assert Path(row['path']).read_bytes() == original
    with pytest.raises(ValueError, match='journal already exists'):
        C.restart(row, control / 'restart', execute=True)
