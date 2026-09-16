"""Prospective per-host transport capacity, separate from historical campaigns.

Old campaign capacities and model plans remain immutable. A selected deployment
policy supplies a common physical gate to all newly migrated workers on a host.
The fleet coordinator still enforces workbook exclusion and uncertain holds.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import time

SCHEMA = 'fleet-physical-model-capacity/v1'


def binding(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('canonical physical capacity evidence required')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def selected():
    path = os.environ.get('HARBOR_PHYSICAL_MODEL_POLICY')
    digest = os.environ.get('HARBOR_PHYSICAL_MODEL_POLICY_SHA256')
    if path is None and digest is None:
        return None
    if not path or not digest or not os.environ.get('HARBOR_FLEET_URL'):
        raise ValueError('complete fleet physical model policy required')
    return validate({'path': path, 'sha256': digest})


def validate(row):
    if set(row) != {'path', 'sha256'} or binding(row['path']) != row:
        raise ValueError('physical model policy changed')
    value = json.loads(Path(row['path']).read_bytes())
    if set(value) != {'schema_version', 'authorization', 'hosts', 'slots_per_host',
                      'slot_root', 'publication_limit', 'per_workbook_limit',
                      'unknown_response_policy', 'max_wait_seconds', 'revision'}:
        raise ValueError('closed physical model policy required')
    if (value['schema_version'] != SCHEMA or type(value['revision']) is not int
            or value['revision'] < 1 or type(value['slots_per_host']) is not int
            or not 1 <= value['slots_per_host'] <= 8
            or type(value['publication_limit']) is not int or value['publication_limit'] != 1
            or type(value['per_workbook_limit']) is not int or value['per_workbook_limit'] != 1
            or value['unknown_response_policy'] != 'hold-never-replay'
            or type(value['max_wait_seconds']) is not int
            or not 1 <= value['max_wait_seconds'] <= 3600):
        raise ValueError('unbounded or unsupported physical policy')
    if (not isinstance(value['hosts'], list) or socket.gethostname() not in value['hosts']
            or len(set(value['hosts'])) != len(value['hosts'])):
        raise ValueError('host outside selected capacity policy')
    if binding(value['authorization']['path']) != value['authorization']:
        raise ValueError('physical capacity authorization changed')
    root = Path(value['slot_root'])
    if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('canonical shared host gate required')
    return row, value


@contextmanager
def slot(selection, *, clock=time.monotonic, pause=time.sleep):
    """Bounded process-wide gate; capacity changes require a new policy root."""
    row, policy = selection
    if selected() != selection:
        raise ValueError('physical model selection changed')
    root = Path(policy['slot_root'])
    root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    config = os.open(root/'policy.json', flags, 0o600)
    try:
        fcntl.flock(config, fcntl.LOCK_EX)
        expected = (json.dumps(row, sort_keys=True) + '\n').encode()
        current = os.read(config, 4096)
        if current and current != expected:
            raise ValueError('mixed physical capacity policies on one host')
        if not current:
            os.write(config, expected)
            os.fsync(config)
    finally:
        os.close(config)
    acquired = None
    started = clock()
    try:
        while acquired is None:
            if selected() != selection:
                raise ValueError('physical policy changed during admission')
            for number in range(policy['slots_per_host']):
                fd = os.open(root/f'slot-{number}.lock', flags, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                except BaseException:
                    os.close(fd)
                    raise
                else:
                    acquired = fd
                    break
            if acquired is None:
                if clock() - started >= policy['max_wait_seconds']:
                    raise TimeoutError('physical model capacity wait exhausted before dispatch')
                pause(0.1)
        yield {'policy': row, 'host': socket.gethostname(),
               'wait_seconds': clock() - started, 'slots_per_host': policy['slots_per_host']}
    finally:
        if acquired is not None:
            os.close(acquired)
