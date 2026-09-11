"""Explicit, hash-bound scratch cleanup after private-release custody verification.

Planning is read-only. Execution accepts only the verification package's closed
custody authorization, never a passed flag or user-provided verification callback.
No directory tree is deleted. Candidates are atomically quarantined and checked
again immediately before unlink. Writers must cooperate with the workbook lock;
this is not protection against a hostile same-user process between syscalls.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

SCHEMA = 'harbor-retention-plan/v1'
JOURNAL_SCHEMA = 'harbor-retention-journal/v1'


class RetentionError(ValueError):
    pass


def _require(value, message):
    if not value:
        raise RetentionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def plan_sha256(plan):
    return hashlib.sha256(canonical(plan)).hexdigest()


def _path(value):
    _require(type(value) is str and value and '\x00' not in value, 'invalid path')
    p = Path(value)
    _require(p.is_absolute() and '..' not in p.parts and str(p) == value,
             'canonical absolute path required')
    return p


def _inside(path, root):
    return path == root or root in path.parents


def _overlap(a, b):
    return _inside(a, b) or _inside(b, a)


@contextmanager
def _directory(path):
    """Walk every component without following any symlink."""
    path = _path(str(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _identity(st):
    return dict(device=st.st_dev, inode=st.st_ino, size=st.st_size,
                mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns)


def _record_at(parent_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode), 'regular file required')
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        _require(_identity(before) == _identity(after), 'file changed during hashing')
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _require(_identity(entry) == _identity(after), 'path changed during hashing')
        return dict(identity=_identity(after), sha256=digest.hexdigest(),
                    allocated_bytes=after.st_blocks * 512)
    finally:
        os.close(fd)


def _record(path):
    with _directory(path.parent) as parent_fd:
        return _record_at(parent_fd, path.name)


def plan_cleanup(candidates, *, disposable_roots, protected_roots, retained_roots):
    """Freeze explicit duplicate mappings; never discover files for deletion.

    The authority verifier must independently check these selected roots against
    the publication's source, code, curation, final-task and private-bundle roots.
    """
    roots = [_path(x) for x in disposable_roots]
    protected = [_path(x) for x in protected_roots]
    retained = [_path(x) for x in retained_roots]
    _require(roots and protected and retained, 'all root classes are required')
    _require(len(set(roots)) == len(roots), 'duplicate disposable root')
    for index, root in enumerate(roots):
        _require(root != Path('/'), 'filesystem root is forbidden')
        _require(not any(_overlap(root, p) for p in protected + retained + roots[:index]),
                 'disposable/protected/retained roots overlap')
    for root in roots + protected + retained:
        with _directory(root):
            pass
    _require(type(candidates) is list and candidates, 'explicit nonempty candidate list required')
    rows, seen = [], set()
    for candidate in candidates:
        _require(type(candidate) is dict and set(candidate) == {'path', 'retained_path'},
                 'closed candidate mapping required')
        path, copy = _path(candidate['path']), _path(candidate['retained_path'])
        _require(path not in seen, 'duplicate candidate')
        seen.add(path)
        _require(any(root in path.parents for root in roots), 'candidate outside disposable roots')
        _require(any(root in copy.parents for root in retained), 'copy outside retained roots')
        _require(not any(_inside(path, root) for root in protected + retained), 'protected candidate')
        _require(not any(part.startswith('.harbor-retention-') or part.endswith('.lock')
                         for part in path.parts), 'retention metadata/locks are protected')
        source, kept = _record(path), _record(copy)
        _require(source['sha256'] == kept['sha256'] and
                 source['identity']['size'] == kept['identity']['size'], 'retained copy differs')
        rows.append(dict(path=str(path), retained_path=str(copy), source=source, retained=kept))
    return dict(schema_version=SCHEMA, disposable_roots=[str(x) for x in roots],
                protected_roots=[str(x) for x in protected], retained_roots=[str(x) for x in retained],
                files=rows, logical_bytes=sum(x['source']['identity']['size'] for x in rows),
                allocated_bytes=sum(x['source']['allocated_bytes'] for x in rows),
                physical_space_estimate=False)


def _validate_plan(plan):
    _require(type(plan) is dict and set(plan) == {
        'schema_version', 'disposable_roots', 'protected_roots', 'retained_roots', 'files',
        'logical_bytes', 'allocated_bytes', 'physical_space_estimate'}, 'closed plan required')
    _require(plan['schema_version'] == SCHEMA and plan['physical_space_estimate'] is False,
             'unknown plan schema')
    _require(type(plan['files']) is list and plan['files'], 'empty plan')
    roots = [_path(x) for x in plan['disposable_roots']]
    protected = [_path(x) for x in plan['protected_roots']]
    retained = [_path(x) for x in plan['retained_roots']]
    _require(roots and protected and retained and len(set(roots)) == len(roots), 'invalid roots')
    for index, root in enumerate(roots):
        _require(root != Path('/') and not any(_overlap(root, p) for p in protected + retained + roots[:index]),
                 'overlapping roots')
    seen = set()
    for row in plan['files']:
        _require(type(row) is dict and set(row) == {'path', 'retained_path', 'source', 'retained'},
                 'closed file record required')
        path, copy = _path(row['path']), _path(row['retained_path'])
        _require(path not in seen and any(r in path.parents for r in roots), 'duplicate/foreign candidate')
        seen.add(path)
        _require(any(r in copy.parents for r in retained), 'foreign retained copy')
        _require(not any(part.startswith('.harbor-retention-') or part.endswith('.lock')
                         for part in path.parts), 'metadata/lock candidate')
        for record in (row['source'], row['retained']):
            _require(type(record) is dict and set(record) == {'identity', 'sha256', 'allocated_bytes'},
                     'closed byte record required')
            _require(type(record['identity']) is dict and set(record['identity']) == {
                'device', 'inode', 'size', 'mtime_ns', 'ctime_ns'}, 'closed identity required')
            _require(all(type(x) is int and x >= 0 for x in record['identity'].values()) and
                     type(record['allocated_bytes']) is int and record['allocated_bytes'] >= 0 and
                     type(record['sha256']) is str and len(record['sha256']) == 64 and
                     all(x in '0123456789abcdef' for x in record['sha256']), 'invalid byte record')
        _require(row['source']['sha256'] == row['retained']['sha256'] and
                 row['source']['identity']['size'] == row['retained']['identity']['size'], 'copy not equivalent')
    _require(plan['logical_bytes'] == sum(x['source']['identity']['size'] for x in plan['files']) and
             plan['allocated_bytes'] == sum(x['source']['allocated_bytes'] for x in plan['files']),
             'plan totals differ')


def _authorize(authorization, plan):
    # Fixed import and exact type: no plugin callback, truthy flag or force mode.
    from scripts import harbor_verification_package as custody
    _require(type(authorization) is custody.CleanupAuthorization, 'typed custody authorization required')
    custody.validate_cleanup_authorization(authorization, plan)


def _atomic_json(parent_fd, name, value):
    temporary = name + '.' + uuid.uuid4().hex + '.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    try:
        payload = canonical(value) + b'\n'
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(payload)
            stream.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)


def _read_json(parent_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode), 'journal regular file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return json.load(stream)
    finally:
        os.close(fd)


def _exists(parent_fd, name):
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _space(fd):
    s = os.fstatvfs(fd)
    return dict(available_bytes=s.f_bavail * s.f_frsize, free_bytes=s.f_bfree * s.f_frsize,
                measured_at_ns=time.time_ns())


def _same_after_rename(record, expected):
    # Rename can change ctime. Inode, bytes and mtime must still match exactly.
    return record['sha256'] == expected['sha256'] and all(
        record['identity'][k] == expected['identity'][k]
        for k in ('device', 'inode', 'size', 'mtime_ns'))


def execute_cleanup(plan, *, authorization, journal_dir):
    """Delete explicit duplicates only after exact independent custody proof.

    A crash leaves at most one quarantined candidate. Resuming revalidates all
    retained bytes and authorization. Changes detected before the final unlink
    stay quarantined for inspection. Cooperative writers must respect the owning
    workbook lock; a hostile same-user process can still race final syscalls.
    Physical free-space deltas include other
    filesystem activity and cannot be promised from APFS allocation totals.
    """
    _validate_plan(plan)
    _authorize(authorization, plan)
    directory = _path(str(journal_dir))
    _require(not any(_overlap(directory, _path(root)) for root in plan['disposable_roots']),
             'journal overlaps disposable roots')
    # Caller creates a durable journal namespace; do not recursively create paths.
    digest = plan_sha256(plan)
    with _directory(directory) as journal_fd:
        lock_fd = os.open('cleanup.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=journal_fd)
        try:
            _require(stat.S_ISREG(os.fstat(lock_fd).st_mode), 'regular cleanup lock required')
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _execute_locked(plan, authorization, journal_fd, digest)
        finally:
            os.close(lock_fd)


def _execute_locked(plan, authorization, journal_fd, digest):
    _require(all(row['source']['identity']['device'] == os.fstat(journal_fd).st_dev
                 for row in plan['files']), 'one filesystem per cleanup journal required')
    name = digest + '.json'
    if _exists(journal_fd, name):
        state = _read_json(journal_fd, name)
        _require(state['schema_version'] == JOURNAL_SCHEMA and state['plan_sha256'] == digest and
                 len(state['files']) == len(plan['files']), 'journal differs from plan')
    else:
        state = dict(schema_version=JOURNAL_SCHEMA, plan_sha256=digest,
                     files=['pending'] * len(plan['files']), before=_space(journal_fd), after=None)
        _atomic_json(journal_fd, name, state)
    for row in plan['files']:
        _require(_record(_path(row['retained_path'])) == row['retained'], 'retained evidence changed')
    for index, row in enumerate(plan['files']):
        _authorize(authorization, plan)
        path = _path(row['path'])
        stage_dir = '.harbor-retention-' + digest
        staged_name = str(index)
        with _directory(path.parent) as parent_fd:
            status = state['files'][index]
            _require(status in ('pending', 'intent', 'staged', 'deleted'), 'invalid journal state')
            if status == 'deleted':
                _require(not _exists(parent_fd, path.name), 'deleted path unexpectedly recreated')
                continue
            if status == 'pending':
                _require(_record_at(parent_fd, path.name) == row['source'], 'candidate changed')
                state['files'][index] = 'intent'
                _atomic_json(journal_fd, name, state)
            try:
                os.mkdir(stage_dir, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            quarantine_fd = os.open(stage_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                qs = os.fstat(quarantine_fd)
                _require(qs.st_uid == os.geteuid() and stat.S_IMODE(qs.st_mode) == 0o700,
                         'unsafe quarantine directory')
                original_exists = _exists(parent_fd, path.name)
                staged_exists = _exists(quarantine_fd, staged_name)
                _require(not (original_exists and staged_exists), 'both candidate and quarantine exist')
                if not staged_exists and original_exists:
                    _require(state['files'][index] == 'intent', 'staged candidate moved unexpectedly')
                    _require(_record_at(parent_fd, path.name) == row['source'], 'candidate changed before rename')
                    os.rename(path.name, staged_name, src_dir_fd=parent_fd, dst_dir_fd=quarantine_fd)
                    os.fsync(parent_fd)
                    os.fsync(quarantine_fd)
                    staged_exists = True
                if not staged_exists:
                    _require(state['files'][index] == 'staged', 'candidate missing before custody transfer')
                else:
                    _require(_same_after_rename(_record_at(quarantine_fd, staged_name), row['source']),
                             'quarantined replacement preserved; identity differs')
                    _require(_record(_path(row['retained_path'])) == row['retained'], 'retained copy changed')
                    state['files'][index] = 'staged'
                    _atomic_json(journal_fd, name, state)
                    # Retained hashing and durable journal writes can be slow.
                    # Recheck both after that work, with the candidate check last,
                    # so modifications during either cannot delete changed bytes.
                    _require(_record(_path(row['retained_path'])) == row['retained'],
                             'retained copy changed before unlink')
                    _require(_same_after_rename(_record_at(quarantine_fd, staged_name), row['source']),
                             'quarantined replacement preserved; changed before unlink')
                    os.unlink(staged_name, dir_fd=quarantine_fd)
                    os.fsync(quarantine_fd)
                state['files'][index] = 'deleted'
                _atomic_json(journal_fd, name, state)
            finally:
                os.close(quarantine_fd)
    state['after'] = _space(journal_fd)
    state['observed_available_bytes_delta'] = state['after']['available_bytes'] - state['before']['available_bytes']
    state['physical_space_attribution'] = 'Observed filesystem delta; APFS sharing and concurrent activity apply.'
    _atomic_json(journal_fd, name, state)
    return state
