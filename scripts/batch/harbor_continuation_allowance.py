"""Explicit root-selected cumulative allowances; never reset execution history.

Authorization files are append-only and hash chained. A binding is selected by
trusted root code, not inferred from workbook/model content. This module issues
no model requests and changes no tracker. Historical validation uses event time;
new admission uses the current clock. A reservation is consumption, including
when its caller loses a response, and has no automatic replay API.
"""
from pathlib import Path
import copy
import fcntl
import json
import os
import time
from scripts.batch import harbor_tracker_lifecycle as life

SCHEMA = 'harbor-continuation-allowance/v1'
LIMITS = {'takeovers', 'lease_duration_ns', 'stage_seconds', 'memory_bytes', 'review_attempts', 'transport_retries'}


def require(ok, message):
    if not ok:
        raise life.Conflict(message)


def read(binding):
    life.checked_record(binding)
    return json.loads(Path(binding['path']).read_bytes())


def record(path):
    return {'path': str(path.absolute()), 'sha256': life.schema.sha(path.read_bytes())}


def retain(path, value):
    raw = life.canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return record(path)


def scope(workbook, source_sha256):
    require(isinstance(workbook, str) and len(workbook) == 4 and workbook.isdigit(), 'workbook scope required')
    require(isinstance(source_sha256, str) and life.schema.HEX.fullmatch(source_sha256), 'original source hash required')
    return {'workbook': workbook, 'original_sha256': source_sha256}


def validate(binding, *, expected_scope=None, at=None, _seen=()):
    require(binding['path'] not in _seen and len(_seen) < 128, 'cyclic or overlong authorization chain')
    value = read(binding)
    require(set(value) == {'schema_version', 'actor', 'sequence', 'previous', 'scope', 'limits',
                          'issued_at_ns', 'expires_at_ns', 'authorization', 'reason'}, 'closed authorization required')
    require(value['schema_version'] == SCHEMA and value['actor'] == 'harbor-orchestrator', 'unknown authorization')
    require(value['scope'] == scope(value['scope']['workbook'], value['scope']['original_sha256']), 'invalid scope')
    if expected_scope is not None:
        require(value['scope'] == expected_scope, 'authorization belongs to another source/workbook')
    require(type(value['sequence']) is int and value['sequence'] >= 1, 'invalid authorization sequence')
    require(Path(binding['path']).name == 'authorization-%06d.json' % value['sequence'], 'canonical append-only authorization sequence required')
    require(set(value['limits']) == LIMITS and all(type(x) is int and x >= 0 for x in value['limits'].values()), 'finite integer allowances required')
    require(all(value['limits'][k] > 0 for k in LIMITS - {'transport_retries'}), 'positive operational allowances required')
    require(type(value['issued_at_ns']) is int and type(value['expires_at_ns']) is int
            and 0 < value['issued_at_ns'] < value['expires_at_ns'], 'bounded authorization window required')
    require(isinstance(value['reason'], str) and value['reason'].strip(), 'explicit authorization reason required')
    life.checked_record(value['authorization'])
    if value['previous'] is None:
        require(value['sequence'] == 1, 'first authorization must have sequence one')
    else:
        require(Path(value['previous']['path']).parent == Path(binding['path']).parent, 'authorization chain moved to another ledger')
        prior = validate(value['previous'], expected_scope=value['scope'], _seen=(*_seen, binding['path']))
        require(value['sequence'] == prior['sequence'] + 1 and value['issued_at_ns'] >= prior['issued_at_ns'], 'authorization history skipped or reordered')
        require(all(value['limits'][k] >= prior['limits'][k] for k in LIMITS), 'allowance history cannot reset or reduce consumed budgets')
    if at is not None:
        require(type(at) is int and value['issued_at_ns'] <= at < value['expires_at_ns'], 'authorization is not currently effective')
    return value


def issue(directory, *, expected_previous, selected_scope, limits, authorization, reason,
          expires_at_ns, approve, clock=time.time_ns):
    """Append only after independent root approval of this exact declaration."""
    directory = life.schema.safe_path(directory)
    require(directory.is_dir(), 'existing root-selected allowance directory required')
    with (directory / 'authorization.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = sorted(directory.glob('authorization-*.json'))
        require([p.name for p in files] == ['authorization-%06d.json' % i for i in range(1, len(files)+1)], 'authorization sequence gap')
        previous = record(files[-1]) if files else None
        require(previous == expected_previous, 'authorization append CAS conflict')
        if previous:
            validate(previous, expected_scope=selected_scope)
        value = dict(schema_version=SCHEMA, actor='harbor-orchestrator', sequence=len(files)+1,
                     previous=previous, scope=selected_scope, limits=limits, issued_at_ns=clock(),
                     expires_at_ns=expires_at_ns, authorization=authorization, reason=reason)
        # Validate before committing, without installing a temporary authority.
        require(callable(approve) and approve(copy.deepcopy(value)) is True, 'explicit root authorization rejected')
        life.checked_record(authorization)
        require(selected_scope == scope(selected_scope['workbook'], selected_scope['original_sha256']), 'invalid scope')
        require(set(limits) == LIMITS and all(type(v) is int and v >= 0 for v in limits.values())
                and all(limits[k] > 0 for k in LIMITS-{'transport_retries'}), 'finite bounded limits required')
        require(type(expires_at_ns) is int and type(value['issued_at_ns']) is int
                and 0 < value['issued_at_ns'] < expires_at_ns and isinstance(reason,str) and reason.strip(), 'invalid authorization window/reason')
        if previous:
            old=validate(previous)
            require(value['issued_at_ns'] >= old['issued_at_ns'] and all(limits[k]>=old['limits'][k] for k in LIMITS), 'authorization regression')
        return retain(directory / ('authorization-%06d.json' % value['sequence']), value)


def lease_limits(binding, tracker, *, at=None):
    value = validate(binding, expected_scope=scope(tracker['workbook_id'], tracker['bindings']['original_raw_source']['sha256']), at=at)
    return value['limits']['takeovers'], value['limits']['lease_duration_ns']


def reserve_review(directory, *, allowance, semantic_scope, prior_records, original_limit,
                   expected_ordinal, prove_closed, clock=time.time_ns, cumulative_limit=None):
    """Reserve the next cumulative review ordinal, preserving all prior evidence.

    The owning reader must prove every supplied prior record terminal and its
    exact ordinal/scope. Unknown or active calls cannot be made terminal here.
    A retained reservation blocks replay even if no transport was observed.
    """
    value = validate(allowance, expected_scope=scope(semantic_scope['workbook_id'], semantic_scope['original_sha256']), at=clock())
    selected_limit=value['limits']['review_attempts'] if cumulative_limit is None else cumulative_limit
    require(type(selected_limit)is int and expected_ordinal<=selected_limit<=value['limits']['review_attempts'], 'selected review ceiling exceeds policy')
    require(set(semantic_scope)=={'workbook_id','original_sha256','lane','budget_id'}
            and all(isinstance(semantic_scope[k],str) and semantic_scope[k] for k in ('lane','budget_id')), 'exact stable semantic scope required')
    require(type(original_limit) is int and original_limit > 0 and type(expected_ordinal) is int, 'original budget required')
    require(isinstance(prior_records, list) and len(prior_records)==expected_ordinal-1
            and expected_ordinal>original_limit and expected_ordinal<=value['limits']['review_attempts'], 'cumulative additional review allowance exhausted')
    require(len({r['path'] for r in prior_records})==len(prior_records), 'duplicate prior review evidence')
    for ordinal, row in enumerate(prior_records, 1):
        life.checked_record(row)
        require(callable(prove_closed) and prove_closed(row, copy.deepcopy(semantic_scope), ordinal) is True,
                'owning reader did not prove terminal prior review')
    directory=life.schema.safe_path(directory)
    require(directory == Path(allowance['path']).parent / 'review-reservations' and directory.is_dir(), 'same policy review ledger required; namespace cannot reset consumption')
    with (directory/'review.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        reserved_at=clock()
        validate(allowance,expected_scope=value['scope'],at=reserved_at)
        # A scope has one durable consumption file per ordinal, independent of
        # policy version. New authorization cannot reset an uncertain attempt.
        key=life.schema.sha(life.canonical(semantic_scope))
        path=directory/('%s-%06d.json'%(key,expected_ordinal))
        require(not path.exists(), 'review ordinal already reserved; reconcile without replay')
        return retain(path,dict(schema_version='harbor-additional-review-reservation/v1',allowance=allowance,
                      scope=semantic_scope,ordinal=expected_ordinal,original_limit=original_limit,
                      cumulative_limit=selected_limit,prior_records=prior_records,
                      automatic_retry=False,created_at_ns=reserved_at))


def validate_review_reservation(binding, semantic_scope, ordinal, limit):
    value=read(binding)
    require(set(value)=={'schema_version','allowance','scope','ordinal','original_limit','cumulative_limit',
                         'prior_records','automatic_retry','created_at_ns'}, 'closed review reservation required')
    require(value['schema_version']=='harbor-additional-review-reservation/v1' and value['automatic_retry'] is False,
            'unknown review consumption record')
    require(value['scope']==semantic_scope and value['ordinal']==ordinal and value['cumulative_limit']==limit,
            'review reservation scope/ordinal/limit changed')
    policy=validate(value['allowance'],expected_scope=scope(semantic_scope['workbook_id'],semantic_scope['original_sha256']),at=value['created_at_ns'])
    require(type(ordinal) is int and type(limit) is int and type(value['original_limit']) is int
            and 0<value['original_limit']<ordinal<=limit<=policy['limits']['review_attempts'], 'additional cumulative review limit differs')
    require(len(value['prior_records'])==ordinal-1 and len({r['path'] for r in value['prior_records']})==ordinal-1,
            'prior review consumption missing or duplicated')
    for row in value['prior_records']:life.checked_record(row)
    key=life.schema.sha(life.canonical(semantic_scope))
    require(Path(binding['path'])==Path(value['allowance']['path']).parent/'review-reservations'/('%s-%06d.json'%(key,ordinal)),
            'review reservation moved to another namespace')
    return value,policy
