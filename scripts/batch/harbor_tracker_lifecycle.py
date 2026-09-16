"""Root-owned tracker creation and lease operations using the strict schema.

No CLI and no arbitrary tracker mutation API. The root dispatcher constructs
the authority and supplies independent validators; worksheet/model data cannot.
"""
import copy
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import threading
import time
import uuid

from scripts.batch import harbor_tracker as schema


Conflict = schema.Conflict
EVENT_SCHEMA = 'harbor-lease-event/v1'
CREATE_SCHEMA = 'harbor-tracker-created/v1'
PAUSE_EVENT_SCHEMA = 'harbor-coordinator-pause-lease/v1'
MAX_EXPIRED_REPLACEMENTS = 3

# Cache only byte digests, never qualification decisions. Every lookup opens
# the actual regular file and checks its inode, size and nanosecond change
# metadata. Mutable evidence is hashed again after any observed change.
_DIGEST_CACHE = OrderedDict()
_DIGEST_CACHE_PID = os.getpid()
_DIGEST_CACHE_LIMIT = 1024
_DIGEST_CACHE_LOCK = threading.RLock()


def _file_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _checked_digest(path):
    global _DIGEST_CACHE_PID, _DIGEST_CACHE_LOCK
    if _DIGEST_CACHE_PID != os.getpid():
        _DIGEST_CACHE_LOCK = threading.RLock()
        _DIGEST_CACHE.clear()
        _DIGEST_CACHE_PID = os.getpid()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise Conflict('regular evidence file required: ' + str(path))
        identity = _file_identity(before)
        key = (str(path), identity)
        with _DIGEST_CACHE_LOCK:
            digest = _DIGEST_CACHE.get(key)
        if digest is None:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if (_file_identity(os.fstat(stream.fileno())) != identity
                or _file_identity(os.stat(path, follow_symlinks=False)) != identity):
            raise Conflict('evidence changed while hashing: ' + str(path))
    with _DIGEST_CACHE_LOCK:
        _DIGEST_CACHE[key] = digest
        _DIGEST_CACHE.move_to_end(key)
        while len(_DIGEST_CACHE) > _DIGEST_CACHE_LIMIT:
            _DIGEST_CACHE.popitem(last=False)
    return digest


@dataclass(frozen=True)
class RootAuthority:
    """Trusted code configuration, not a serializable bearer permission."""
    actor: str
    max_duration_ns: int
    authorize: object
    max_replacements: int = 1
    allowance: dict | None = None  # Explicit root-selected append-only policy.


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'
    ).encode()


def checked_record(record):
    if not isinstance(record, dict) or set(record) != {'path', 'sha256'}:
        raise Conflict('expected exact path/hash evidence record')
    path = schema.safe_path(record['path'])
    if (
        not isinstance(record['path'], str)
        or not path.is_absolute()
        or not Path(record['path']).is_absolute()
    ):
        raise Conflict('evidence path must be absolute')
    if not isinstance(record['sha256'], str) or not schema.HEX.fullmatch(record['sha256']):
        raise Conflict('invalid evidence digest')
    if not path.is_file():
        raise Conflict('missing evidence: ' + str(path))
    digest = _checked_digest(path)
    if digest != record['sha256']:
        raise Conflict('changed evidence: ' + str(path))
    return copy.deepcopy(record)


def check_records(records):
    for record in records:
        checked_record(record)


def authorize(authority, operation, path, before, request):
    if (
        not isinstance(authority, RootAuthority)
        or authority.actor != 'harbor-orchestrator'
        or not schema.integer(authority.max_duration_ns)
        or not authority.max_duration_ns
    ):
        raise Conflict('root authority configuration required')
    if not callable(authority.authorize) or authority.authorize(
        operation,
        str(path),
        copy.deepcopy(before),
        copy.deepcopy(request)
    ) is not True:
        raise Conflict('root dispatch authorization rejected')


def independent(callback, *args):
    if not callable(callback) or callback(*copy.deepcopy(args)) is not True:
        raise Conflict('independent validator did not pass')


def instant(clock):
    now = clock()
    if not schema.integer(now) or not now:
        raise Conflict('invalid clock')
    return now


@contextmanager
def locked(path):
    path = schema.safe_path(path)
    lockpath = schema.safe_path(str(path) + '.lock')
    fd = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


def commit(path, before_raw, after, *, records, clock, deadline=None, fault_hook=lambda _: None):
    schema.validate_tracker(after)
    payload = canonical(after)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        fault_hook('after_temp_fsync')
        schema.safe_path(path)
        check_records(records)
        if deadline is not None and instant(clock) >= deadline:
            raise Conflict('lease expired during validation')
        if before_raw is None:
            if path.exists():
                raise Conflict('tracker already exists')
            # Publish a complete new file atomically without replacing an existing tracker.
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as e:
                raise Conflict('tracker already exists') from e
        else:
            if path.read_bytes() != before_raw:
                raise Conflict('tracker changed before replacement')
            os.replace(temporary, path)
            temporary = None
        fault_hook('after_publish')
        if temporary is not None:
            os.unlink(temporary)
            temporary = None
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return {
        'revision': after['revision'],
        'sha256': schema.sha(payload),
        'tracker': copy.deepcopy(after)
    }


def create_tracker(
    path,
    *,
    authority,
    workbook_id,
    batch,
    requested_ids,
    original_source,
    dependency_preflight,
    intake_evidence,
    intake_validator,
    raw_source=None,
    lineage_validator=None,
    clock=time.time_ns,
    fault_hook=lambda _: None
):
    """Create revision0 queued/pending; no successful financial lane is inferred."""
    with locked(path) as path:
        if path.exists():
            raise Conflict('tracker already exists')
        if path.name != str(workbook_id) + '.json':
            raise Conflict('tracker filename/workbook identity mismatch')
        original = checked_record(original_source)
        raw = checked_record(raw_source or original)
        dep = checked_record(dependency_preflight)
        evidence = [checked_record(x) for x in intake_evidence]
        if not evidence:
            raise Conflict('initial intake evidence required')
        request = {
            'workbook_id': workbook_id,
            'batch': batch,
            'requested_ids': requested_ids,
            'original_source': original,
            'raw_source': raw,
            'dependency_preflight': dep,
            'intake_evidence': evidence
        }
        authorize(authority, 'create', path, None, request)
        independent(intake_validator, request)
        if raw != original:
            independent(lineage_validator, original, raw, evidence)
        now = instant(clock)
        d = {k: None for k in schema.TOP}
        d.update(
            schema_version=2,
            revision=0,
            batch=batch,
            workbook_id=workbook_id,
            requested_ids=copy.deepcopy(requested_ids),
            raw_source=raw,
            execution_state='queued',
            lane_state={k: {
                'state': 'pending',
                'current_confidence': None,
                'confidence_history': [],
                'attempt': 0
            } for k in schema.LANES},
            bindings={
                'original_raw_source': original,
                'raw_source': raw,
                'dependency_preflight': dep
            },
            generation_ids=dict.fromkeys(('source', 'segmentation', 'task', 'release')),
            artifact_hashes={},
            current_confidence={
                'level': None,
                'reason_codes': [],
                'contributors': [],
                'computed_at_revision': 0
            },
            leases=dict.fromkeys(schema.LEASE_KINDS),
            repairs={'count': 0, 'by_signature': {}, 'history': []},
            scheduler={
                'attempts': 0,
                'resource_exhausted_retries': 0,
                'resource_exhausted_first_at': None
            },
            agent_notes=dict.fromkeys((
                'path',
                'sha256',
                'diagnostic_signature',
                'evidence_set_sha256',
                'updated_at'
            )),
            promotion_ids=dict.fromkeys((
                'task_generation_id',
                'release_id',
                'previous_release_id'
            )),
            updated_at=now
        )
        for name in schema.HISTORY:
            d[name] = []
        d['handoffs'].append({
            'schema_version': CREATE_SCHEMA,
            'revision': 0,
            'root_actor': authority.actor,
            'workbook_id': workbook_id,
            'batch': batch,
            'requested_ids_sha256': schema.sha(canonical(requested_ids)),
            'original_source': original,
            'raw_source': raw,
            'intake_evidence': evidence,
            'created_at': now
        })
        return commit(
            path,
            None,
            d,
            records=[original, raw, dep, *evidence],
            clock=clock,
            fault_hook=fault_hook
        )


def _validate_lifecycle(d):
    """Refuse unreviewed legacy admission and prove the lease history is complete."""
    creations = [
        x for x in d['handoffs']
        if isinstance(x, dict) and x.get('schema_version') == CREATE_SCHEMA
    ]
    if len(creations) != 1:
        raise Conflict('requires exactly one validated creation record; no implicit legacy adoption')
    creation = creations[0]
    if (
        creation['workbook_id'] != d['workbook_id']
        or creation['batch'] != d['batch']
        or creation['requested_ids_sha256'] != schema.sha(canonical(d['requested_ids']))
        or creation['original_source'] != d['bindings'].get('original_raw_source')
        or creation['raw_source'] != d['raw_source']
        or creation['raw_source'] != d['bindings'].get('raw_source')
    ):
        raise Conflict('immutable original/cohort identity changed')

    def same_lease(recorded, live):
        if recorded is None or live is None:
            return recorded is live
        # Normal lane CAS may advance the heartbeat, but cannot change lease identity.
        return (
            set(recorded) == set(live) == schema.LEASE_KEYS
            and all(recorded[k] == live[k] for k in schema.LEASE_KEYS - {'heartbeat_at'})
            and recorded['heartbeat_at'] <= live['heartbeat_at'] < live['expires_at']
        )
    leases = dict.fromkeys(schema.LEASE_KINDS)
    replacement_count = 0
    last_revision = 0
    pause_count = 0
    historical_replacement_limit = MAX_EXPIRED_REPLACEMENTS
    for index, event in enumerate(d['handoffs']):
        if isinstance(event, dict) and event.get('schema_version') == PAUSE_EVENT_SCHEMA:
            from scripts.batch.harbor_pause_recovery import validate_event
            prior = validate_event(event)
            if (pause_count or replacement_count != 1
                    or d['handoffs'][:index] != prior['handoffs']
                    or not last_revision < event['revision'] <= d['revision']
                    or not same_lease(leases['task_agent'], event['before'])):
                raise Conflict('invalid or exhausted coordinator-pause recovery history')
            pause_count += 1
            leases['task_agent'] = event['after']
            last_revision = event['revision']
            continue
        if not isinstance(event, dict) or event.get('schema_version') != EVENT_SCHEMA:
            continue
        expected = {
            'schema_version',
            'operation',
            'lease_kind',
            'revision',
            'root_actor',
            'at',
            'before',
            'after',
            'prior_tracker_sha256'
        }
        if (
            set(event) - expected - {'stopped_owner_proof', 'allowance'}
            or expected - set(event)
            or event['root_actor'] != 'harbor-orchestrator'
            or not schema.integer(event['at'])
            or not isinstance(event['prior_tracker_sha256'], str)
            or not schema.HEX.fullmatch(event['prior_tracker_sha256'])
        ):
            raise Conflict('malformed root lease event')
        kind = event.get('lease_kind')
        operation = event.get('operation')
        rev = event.get('revision')
        if (
            kind not in schema.LEASE_KINDS
            or not schema.integer(rev)
            or not last_revision < rev <= d['revision']
            or not same_lease(leases[kind], event.get('before'))
        ):
            raise Conflict('invalid lease event chain')
        if operation not in {'acquire', 'renew', 'release', 'replace_expired'}:
            raise Conflict('invalid lease operation history')
        old, new = (event['before'], event.get('after'))
        if operation == 'acquire' and old is not None:
            raise Conflict('acquisition history overwrites lease')
        if operation == 'renew' and (
            not old
            or not new
            or any(old[k] != new[k] for k in (
                'owner', 'token', 'scope', 'acquired_at', 'acquisition_revision'
            ))
        ):
            raise Conflict('renewal identity changed')
        if operation == 'release' and (not old or new is not None):
            raise Conflict('invalid release history')
        if operation == 'replace_expired':
            if (
                kind != 'task_agent'
                or not old
                or not new
                or event.get('at', 0) < old['expires_at']
                or not event.get('stopped_owner_proof')
            ):
                raise Conflict('invalid replacement history')
            checked_record(event['stopped_owner_proof'])
            replacement_count += 1
        allowed_replacements = historical_replacement_limit
        if event.get('allowance') is not None:
            from scripts.batch.harbor_continuation_allowance import lease_limits
            allowed_replacements, allowed_duration = lease_limits(event['allowance'], d, at=event['at'])
            if new is not None and new['expires_at'] - event['at'] > allowed_duration:
                raise Conflict('retained lease exceeds selected allowance')
        if operation == 'replace_expired' and replacement_count > MAX_EXPIRED_REPLACEMENTS and event.get('allowance') is None:
            raise Conflict('additional replacement omitted explicit allowance')
        historical_replacement_limit = max(historical_replacement_limit, allowed_replacements)
        if replacement_count > allowed_replacements:
            raise Conflict('expired task-agent replacement budget exhausted')
        if operation == 'release' and event['at'] >= old['expires_at']:
            if not event.get('stopped_owner_proof'):
                raise Conflict('expired release omitted stopped-owner proof')
            checked_record(event['stopped_owner_proof'])
        leases[kind] = new
        last_revision = rev
    for kind, live in d['leases'].items():
        recorded = leases[kind]
        if recorded is None and live is None:
            continue
        if not same_lease(recorded, live):
            raise Conflict('lease not bound to retained operation history')
    return replacement_count


def validate_lifecycle(d):
    try:
        return _validate_lifecycle(d)
    except Conflict:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise Conflict('malformed retained lifecycle history') from exc


def lease_operation(
    path,
    *,
    authority,
    operation,
    lease_kind,
    owner,
    scope,
    expected_revision,
    expected_sha256,
    token=None,
    duration_ns=None,
    prior_lease=None,
    stopped_owner_proof=None,
    prior_owner_stopped_validator=None,
    failure_validator=None,
    clock=time.time_ns,
    fault_hook=lambda _: None
):
    """Fixed root operations: acquire, renew, release, explicit replace_expired.

    There is no mutable field payload. Existing owners are checked exactly;
    acquire/replacement tokens are minted here, never reused from a caller.
    """
    if operation not in {
        'acquire',
        'renew',
        'release',
        'replace_expired'
    } or lease_kind not in schema.LEASE_KINDS:
        raise Conflict('unsupported root lease operation')
    if (
        not schema.integer(expected_revision)
        or not isinstance(expected_sha256, str)
        or not schema.HEX.fullmatch(expected_sha256)
    ):
        raise Conflict('invalid CAS expectation')
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or not isinstance(scope, str)
        or not scope.strip()
    ):
        raise Conflict('owner and scope required')
    with locked(path) as path:
        raw = path.read_bytes()
        before = schema.validate_tracker(schema.strict_json(raw))
        if before['revision'] != expected_revision or schema.sha(raw) != expected_sha256:
            raise Conflict('revision/hash mismatch')
        # Preserve source/cohort bindings; source health remains a warden gate.
        # Missing source bytes must not prevent validated terminal lease cleanup.
        replacements = validate_lifecycle(before)
        now = instant(clock)
        old = before['leases'][lease_kind]
        request = {
            'operation': operation,
            'lease_kind': lease_kind,
            'owner': owner,
            'scope': scope,
            'token': token,
            'duration_ns': duration_ns,
            'prior_lease': prior_lease,
            'stopped_owner_proof': stopped_owner_proof
        }
        authorize(authority, operation, path, before, request)
        allowed_replacements = MAX_EXPIRED_REPLACEMENTS
        if authority.allowance is not None:
            from scripts.batch.harbor_continuation_allowance import lease_limits
            allowed_replacements, allowed_duration = lease_limits(authority.allowance, before, at=now)
            if authority.max_duration_ns > allowed_duration:
                raise Conflict('root lease configuration exceeds selected allowance')
        if lease_kind == 'task_agent' and scope != 'workbook:' + before['workbook_id']:
            raise Conflict('task lease scope mismatch')
        if operation != 'release' and before['execution_state'] in {'STOPPED', 'PROMOTED'}:
            raise Conflict('no new work or extension on terminal tracker')
        records = []
        deadline = None
        proof = None
        if operation in {'acquire', 'replace_expired'}:
            if token is not None:
                raise Conflict('new token must be generated by root operation')
            if operation == 'acquire' and old is not None:
                raise Conflict('lease occupied; expired replacement must be explicit')
            if operation == 'replace_expired':
                if lease_kind != 'task_agent' or old is None or now < old['expires_at']:
                    raise Conflict('cannot steal active lease or replace another lease kind')
                if (type(authority.max_replacements) is not int or not 1 <= authority.max_replacements <= allowed_replacements
                        or replacements >= authority.max_replacements):
                    raise Conflict('expired task-agent replacement budget exhausted')
                if prior_lease != old:
                    raise Conflict('exact prior owner/token/scope lease required')
                proof = checked_record(stopped_owner_proof)
                records.append(proof)
                independent(prior_owner_stopped_validator, before, old, proof)
        else:
            if not old or (old['owner'], old['token'], old['scope']) != (owner, token, scope):
                raise Conflict('exact existing owner/token/scope required')
            if now < old['heartbeat_at']:
                raise Conflict('clock precedes existing heartbeat')
            if operation == 'renew' and now >= old['expires_at']:
                raise Conflict('cannot renew expired lease')
            if operation == 'release' and now >= old['expires_at']:
                terminal_task = lease_kind == 'task_agent' and before['execution_state'] in {'STOPPED', 'PROMOTED'}
                completed_publication = lease_kind == 'publication' and before['execution_state'] == 'PROMOTED'
                if not (terminal_task or completed_publication):
                    raise Conflict('expired lease needs explicit recovery; release cannot hide replacement')
                proof = checked_record(stopped_owner_proof)
                records.append(proof)
                independent(prior_owner_stopped_validator, before, old, proof)
            if now < old['expires_at']:
                deadline = old['expires_at']
        after = copy.deepcopy(before)
        revision = before['revision'] + 1
        if operation == 'release':
            new = None
        else:
            if not schema.integer(duration_ns) or not 0 < duration_ns <= authority.max_duration_ns:
                raise Conflict('duration outside root lease policy')
            if operation == 'renew':
                new = {**old, 'heartbeat_at': now, 'expires_at': now + duration_ns}
                if new['expires_at'] <= old['expires_at']:
                    raise Conflict('renewal must extend expiry')
            else:
                new = {
                    'owner': owner,
                    'token': str(uuid.uuid4()),
                    'scope': scope,
                    'acquired_at': now,
                    'heartbeat_at': now,
                    'expires_at': now + duration_ns,
                    'acquisition_revision': revision
                }
            deadline = min(
                deadline,
                new['expires_at']
            ) if deadline is not None else new['expires_at']
        after['leases'][lease_kind] = new
        after['revision'] = revision
        after['updated_at'] = now
        event = {
            'schema_version': EVENT_SCHEMA,
            'operation': operation,
            'lease_kind': lease_kind,
            'revision': revision,
            'root_actor': authority.actor,
            'at': now,
            'before': copy.deepcopy(old),
            'after': copy.deepcopy(new),
            'prior_tracker_sha256': schema.sha(raw)
        }
        if authority.allowance is not None:
            event['allowance'] = checked_record(authority.allowance)
        if proof:
            event['stopped_owner_proof'] = proof
        after['handoffs'].append(event)
        if before['execution_state'] == 'STOPPED':
            notes = before['agent_notes']
            f = before['promotion_failure']
            if (
                notes['diagnostic_signature'] != f['diagnostic_signature']
                or not notes.get('evidence_set_sha256')
            ):
                raise Conflict('failure notes signature/evidence-set mismatch')
            note = checked_record({'path': notes['path'], 'sha256': notes['sha256']})
            records.extend([note, *f['evidence_paths']])
            check_records(records)
            independent(failure_validator, before, after, frozenset({'stop', 'lease_release'}))
        schema.validate_tracker(after)
        validate_lifecycle(after)
        return commit(
            path,
            raw,
            after,
            records=records,
            clock=clock,
            deadline=deadline,
            fault_hook=fault_hook
        )
