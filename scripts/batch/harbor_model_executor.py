"""Root-prepared, immutable model transport journals; never a semantic validator.

No credentials are read by this module. The authorized root dispatcher supplies
one runtime token, exact prepared request/evidence records, and its lane budget.
Complete explicit transient HTTP errors and typed transient open failures may
retry within the original live call. Observed response bodies and sealed unknown
completion never replay, including on a later execute() invocation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid

import xl_transport
from scripts.batch.harbor_model_review_provenance import Call, Policy, validate_call
from scripts.batch.harbor_tracker import strict_json
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA = 'harbor-model-execution/v1'
RETRY_CODES = frozenset({408, 429, 500, 502, 503, 504})
MAX_RETRIES = 3
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class ExecutorError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticScope:
    workbook_id: str
    original_sha256: str
    lane: str
    budget_id: str  # Stable root-owned logical invocation/partition, not an output path.


@dataclass(frozen=True)
class PreparedCall:
    request: dict  # Exact root-prepared path/hash; body is not generated here.
    policy: Policy
    scope: SemanticScope  # Root-selected original workbook/lane/logical budget.
    ordinal: int
    limit: int
    attempt_id: str  # Root-selected canonical UUID, reused on inspection.
    budget_evidence: tuple  # Existing owning-lane state/authorization records.
    response_mode: str = "json_object"  # Root-selected protocol; never inferred from output.
    audit_context: object = None  # Only the independently projected owning-auditor protocol.
    physical_repair: dict | None = None  # Owning terminal-400 disclosure projection, at most once.
    review_reservation: dict | None = None  # Explicit cumulative allowance; prior limits stay unchanged.


@dataclass(frozen=True)
class Campaign:
    root: Path  # Durable execution journal namespace; never delete to retry.
    slot_root: Path  # Shared with every model consumer through xl_transport.
    model_slots: int = 2  # Root may clamp the whole campaign to one; never above two.


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    outcome: dict  # Sealed path/hash, or explicit read-only observation after hard exit.
    call: Call | None
    reused: bool = False


def require(ok, message):
    if not ok:
        raise ExecutorError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def safe(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'journal paths must not contain symlinks')
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def retain(path, raw):
    """Publish exact bytes atomically without replacing any retained artifact."""
    path = safe(path)
    temporary = path.with_name('.' + path.name + '.' + str(uuid.uuid4()) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save(path, value):
    retain(path, canonical(value))


def record(path):
    path = safe(path)
    return checked_record({'path': str(path), 'sha256': sha(path.read_bytes())})


def checked_bytes(binding):
    checked_record(binding)
    raw = safe(binding['path']).read_bytes()
    require(sha(raw) == binding['sha256'], 'prepared evidence changed while reading')
    return raw


def prepared_plan(prepared, campaign, *, original_renderer_code=None):
    require(isinstance(prepared, PreparedCall) and isinstance(prepared.policy, Policy), 'root prepared call and policy required')
    policy = prepared.policy
    require(prepared.response_mode in {"json_object", "text"}, "unsupported response mode")
    require(type(campaign.model_slots) is int and campaign.model_slots in {1, 2}, "campaign model capacity must be one or two")
    require(policy.endpoint == 'https://litellm.labelbox.com/chat/completions', 'unsupported model destination')
    require(policy.model == 'openai/gpt-5.6-sol' and policy.reasoning_effort == ('high' if prepared.audit_context is None else None), 'unsupported model configuration')
    require(isinstance(policy.project_id, str) and bool(policy.project_id.strip()), 'project identity required')
    scope = prepared.scope
    require(isinstance(scope,SemanticScope), 'structured root semantic scope required')
    require(isinstance(scope.workbook_id,str) and re.fullmatch(r'[0-9]{4}',scope.workbook_id), 'original workbook identity required')
    require(isinstance(scope.original_sha256,str) and re.fullmatch(r'[0-9a-f]{64}',scope.original_sha256), 'original workbook hash required')
    require(all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}',value)
                for value in (scope.lane,scope.budget_id)), 'owning lane and stable logical budget required')
    if prepared.review_reservation is None:
        require(type(prepared.ordinal) is int and type(prepared.limit) is int and 1 <= prepared.ordinal <= prepared.limit <= 2, 'original one/two-attempt lane budget required')
    else:
        from scripts.batch.harbor_continuation_allowance import validate_review_reservation
        reservation,allowance=validate_review_reservation(prepared.review_reservation,asdict(scope),prepared.ordinal,prepared.limit)
        require(prepared.physical_repair is None and prepared.audit_context is None,'additional review cannot combine physical/audit recovery')
    require(str(uuid.UUID(prepared.attempt_id)) == prepared.attempt_id, 'canonical attempt UUID required')
    require(isinstance(prepared.budget_evidence, tuple) and bool(prepared.budget_evidence), 'owning lane budget evidence required')
    raw = checked_bytes(prepared.request)
    request = strict_json(raw)
    if prepared.audit_context is not None:
        from scripts.batch.harbor_audit_protocol import validate
        audit = validate(prepared.audit_context,request,policy,original_renderer_code=original_renderer_code)
        require(prepared.response_mode == 'text' and scope.workbook_id == audit['workbook']
                and scope.original_sha256 == audit['original_sha256']
                and scope.lane == 'harbor-research-auditor'
                and scope.budget_id == 'audit-'+audit['scope_hash']+'-'+str(audit['batch'])
                and (prepared.ordinal,prepared.limit)==(audit['ordinal'],audit['limit']), 'owning audit semantic scope differs')
    else:
        require(original_renderer_code is None,'renderer recovery is audit-only')
        require(set(request) == {'model', 'reasoning_effort', 'messages'}, 'unsupported persistent request fields')
        require(request['model'] == policy.model and request['reasoning_effort'] == policy.reasoning_effort, 'request configuration differs')
        messages = request['messages']
        require(isinstance(messages, list) and len(messages) == 2 and [m.get('role') for m in messages] == ['system', 'user'], 'stateless system/user request required')
        require(all(set(m) == {'role', 'content'} and isinstance(m['content'], str) and m['content'].strip() for m in messages), 'invalid message shape/content')
    require(len({x['path'] for x in prepared.budget_evidence}) == len(prepared.budget_evidence), 'duplicate budget evidence')
    evidence = [checked_bytes(x) for x in prepared.budget_evidence]
    plan = dict(schema_version=SCHEMA, request=prepared.request, policy=asdict(policy), scope=asdict(prepared.scope),
                ordinal=prepared.ordinal, limit=prepared.limit, attempt_id=prepared.attempt_id,
                response_mode=prepared.response_mode, model_slots=campaign.model_slots,
                budget_evidence=list(prepared.budget_evidence), semantic_budget_reset=False,
                campaign_root=str(safe(campaign.root)), slot_root=str(safe(campaign.slot_root)),
                max_transport_retries=MAX_RETRIES, uncertain_completion_policy='retain_incomplete_never_replay')
    if prepared.review_reservation is not None:
        plan['review_reservation']=prepared.review_reservation
        plan['max_transport_retries']=min(MAX_RETRIES,allowance['limits']['transport_retries'])
    if prepared.audit_context is not None:plan['audit_context']=asdict(prepared.audit_context)
    if prepared.physical_repair is not None:
        from scripts.batch.harbor_disclosure_payload_repair import validate_prepared
        validate_prepared(prepared,campaign)
        plan['physical_repair']=prepared.physical_repair;plan['max_transport_retries']=0
    if os.environ.get('HARBOR_FLEET_URL'):
        require(campaign.model_slots == 1, 'fleet global model limit must remain one')
        plan['max_transport_retries']=0
        plan['fleet_transport_policy']='single-dispatch-durable-global-gate/v1'
    return plan, raw, request, evidence


def location(prepared, campaign):
    return safe(campaign.root) / sha(canonical(asdict(prepared.scope))) / ('physical-repair-01' if prepared.physical_repair is not None else 'attempt-%02d' % prepared.ordinal)


@contextmanager
def scope_lock(root, *, create=True):
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    else:
        require(root.is_dir() and (root/'scope.lock').is_file(), 'existing logical scope lock required')
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_CREAT if create else 0)
    fd = os.open(root / 'scope.lock', flags, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutorError('logical model scope is already executing') from exc
        yield
    finally:
        os.close(fd)


def all_files(root):
    result = {}
    for path in sorted(root.iterdir()):
        safe(path)
        require(path.is_file(), 'unexpected non-file in retained call journal')
        if path.name != 'outcome.json':
            result[path.name] = sha(path.read_bytes())
    return result


def seal(root, status, *, details=None):
    value = dict(schema_version=SCHEMA, status=status, semantic_approval=False,
                 evidence=all_files(root), details=details or {}, sealed_at_ns=time.time_ns())
    save(root / 'outcome.json', value)
    return value


def call_from(root, response_mode):
    return Call(record(root/'request.json'), record(root/'submitted.json'),
                record(root/'response.raw.json'), record(root/'completed.json'),
                record(root/('parsed.json' if response_mode == 'json_object' else 'content.txt')))


def _audit_context(plan):
    from scripts.batch.harbor_audit_protocol import AuditRequest
    value=plan.get('audit_context')
    return AuditRequest(**{**value,'previous':tuple(value['previous'])}) if value else None


def inspect_locked(root, plan, expected_request, *, reused=True, original_renderer_code=None):
    if plan.get('review_reservation') is not None:
        from scripts.batch.harbor_continuation_allowance import validate_review_reservation
        validate_review_reservation(plan['review_reservation'],plan['scope'],plan['ordinal'],plan['limit'])
    require((root/'plan.json').read_bytes() == canonical(plan), 'logical call identity/configuration/budget changed')
    if (root/'identity.json').exists():
        identity = strict_json((root/'identity.json').read_bytes())
        expected_identity={'attempt_id':plan['attempt_id'],
                'logical_path':str(root), 'plan_sha256':sha(canonical(plan))}
        if plan.get('physical_repair') is not None:
            from scripts.batch.harbor_disclosure_payload_repair import original_identity
            expected_identity=original_identity(plan['physical_repair'])
        require(strict_json(checked_bytes(identity)) == expected_identity, 'attempt identity reservation changed')
    if not (root/'outcome.json').exists():
        # A durable start/partial response may precede abrupt process death. Do
        # not write over it or guess whether the provider completed the call.
        return ExecutionResult('incomplete', dict(schema_version=SCHEMA, status='incomplete',
            semantic_approval=False, evidence=all_files(root),
            details={'reason':'interrupted_without_sealed_outcome','automatic_replay':False}), None, reused)
    outcome = strict_json((root/'outcome.json').read_bytes())
    require(outcome.get('schema_version') == SCHEMA and outcome.get('semantic_approval') is False, 'invalid execution outcome')
    require(outcome.get('evidence') == all_files(root), 'retained execution evidence changed')
    call = None
    if outcome['status'] == 'completed':
        call = call_from(root, plan['response_mode'])
        validate_call(call, expected_request, policy=Policy(**plan['policy']),
                      json_response=plan['response_mode'] == 'json_object', audit_context=_audit_context(plan), original_renderer_code=original_renderer_code)
    return ExecutionResult(outcome['status'], record(root/'outcome.json'), call, reused)


def inspect(prepared, campaign, *, original_renderer_code=None):
    """Read-only recovery, retaining unknown completion; never invokes transport."""
    plan, _, request, _ = prepared_plan(prepared, campaign,original_renderer_code=original_renderer_code)
    root = location(prepared, campaign)
    require(root.is_dir(), 'logical call has not been submitted')
    with scope_lock(root.parent, create=False):
        return inspect_locked(root, plan, request,original_renderer_code=original_renderer_code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request, timeout):
    # Authenticated financial context must never follow an alternate destination.
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def read_body(response, path):
    """Retain partial bytes even if a response read raises or process dies."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    sync_dir(path.parent)
    size = 0
    with os.fdopen(fd, 'wb', buffering=0) as stream:
        try:
            while True:
                try:
                    chunk = response.read(65536)
                except http.client.IncompleteRead as exc:
                    if isinstance(exc.partial, bytes):
                        stream.write(exc.partial); os.fsync(stream.fileno())
                    raise
                if not chunk:
                    break
                size += len(chunk)
                stream.write(chunk)
                os.fsync(stream.fileno())
                require(size <= MAX_RESPONSE_BYTES, 'response exceeds retained size bound')
        finally:
            os.fsync(stream.fileno())
    return path.read_bytes()


def retry_delay(index, value):
    delay = 2 ** (index + 1)
    if value:
        try:
            parsed = float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                parsed = delay
        if math.isfinite(parsed):
            delay = parsed
    return min(60, max(0, delay))


def transient_open_failure(exc):
    """Classify typed causes only; exception messages are never evidence.

    A timeout/reset before response headers does not prove server non-delivery.
    This is bounded prospective transport retry, not an exactly-once claim.
    """
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(cause, (ssl.SSLError, http.client.HTTPException)):
        return None
    if isinstance(cause, socket.gaierror):
        return 'temporary_dns' if cause.errno == socket.EAI_AGAIN else None
    if isinstance(cause, TimeoutError):
        return 'timeout'
    if isinstance(cause, (ConnectionRefusedError, ConnectionResetError,
                          ConnectionAbortedError, BrokenPipeError)):
        return 'connection'
    return None


def execute(prepared, campaign, *, credential, timeout=600, _failpoint=None):
    """Run only an unused logical call. Returned completion is NOT semantic PASS.

    `_failpoint` is a test-only interruption hook, never a validator callback.
    The owning root adapter must validate its original lane state and budget
    before constructing PreparedCall. This journal enforces those selected
    identities and prevents repeated use of a scope/ordinal inside a campaign.
    """
    require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')!='1','read-only continuation cannot dispatch a model')
    plan, body, expected_request, budget_raw = prepared_plan(prepared, campaign)
    require(isinstance(credential, str) and credential.strip(), 'runtime credential required')
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 600, 'bounded transport timeout required')
    require(os.environ.get('HARBOR_MODEL_SLOTS') == str(campaign.model_slots), 'shared root-selected model capacity must be configured')
    require(os.environ.get('HARBOR_MODEL_SLOT_ROOT') == str(safe(campaign.slot_root)), 'shared campaign model slot root differs')
    root = location(prepared, campaign)
    def failpoint(name):
        if _failpoint:
            _failpoint(name)
    with scope_lock(root.parent):
        delegation=root.parent/'scope-delegation.json'
        require(not delegation.exists() and not delegation.is_symlink(), 'original semantic scope is delegated; obsolete request cannot execute')
        if prepared.physical_repair is not None:
            marker=root.parent/'physical-repair.json'
            if marker.exists():require(strict_json(marker.read_bytes())==prepared.physical_repair,'physical repair already reserved')
            else:save(marker,prepared.physical_repair)
        if root.exists():
            return inspect_locked(root, plan, expected_request)
        reservation=None
        if prepared.review_reservation is not None:
            from scripts.batch.harbor_continuation_allowance import validate_review_reservation,validate
            reservation,_=validate_review_reservation(prepared.review_reservation,asdict(prepared.scope),prepared.ordinal,prepared.limit)
            validate(reservation['allowance'],at=time.time_ns())
        previous = []
        for number in range(1, prepared.ordinal):
            prior = root.parent / ('attempt-%02d' % number)
            require((prior/'outcome.json').is_file(), 'prior semantic attempt missing or incomplete')
            prior_plan = strict_json((prior/'plan.json').read_bytes())
            require(prior_plan['scope'] == asdict(prepared.scope) and (prior_plan['limit'] == prepared.limit or (reservation is not None and number<=reservation['original_limit'] and prior_plan['limit']==reservation['original_limit']))
                    and prior_plan['policy'] == asdict(prepared.policy) and prior_plan['ordinal'] == number
                    and (prior_plan['response_mode'] == prepared.response_mode or (
                        prepared.scope.lane == 'harbor-dialogue-author'
                        and prepared.scope.budget_id in {'dialogue-writer', 'dialogue-paraphraser'}
                        and number == 1 and prepared.ordinal == prepared.limit == 2
                        and prior_plan['response_mode'] == 'text' and prepared.response_mode == 'json_object'))
                    and bool(prior_plan.get('audit_context')) == bool(prepared.audit_context),
                    'prior semantic scope/limit differs')
            prior_request = strict_json((prior/'request.json').read_bytes())
            checked = inspect_locked(prior, prior_plan, prior_request)
            require(checked.status in {'completed', 'response_rejected'}, 'unresolved prior transport attempt cannot be reset')
            if prior_plan['response_mode'] != prepared.response_mode:
                # Structured dialogue authoring changes only the response parser.
                # Revalidate the actual first call with its original text mode;
                # its semantic ordinal and immutable journal remain consumed.
                require(checked.status == 'completed' and checked.call is not None,
                        'dialogue response protocol transition requires a complete prior call')
            previous.append(record(prior/'outcome.json'))
        if reservation is not None:require(previous==reservation['prior_records'],'reserved prior review outcomes differ from actual journal')
        root.mkdir(mode=0o700);sync_dir(root.parent)
        save(root/'plan.json', plan)
        retain(root/'request.json', body)
        for index, raw in enumerate(budget_raw):retain(root/('budget-%02d.snapshot' % index), raw)
        save(root/'prior-attempts.json', previous)
        identities = safe(campaign.root) / 'attempt-identities'
        identities.mkdir(exist_ok=True, mode=0o700)
        identity = identities / (prepared.attempt_id + '.json')
        if prepared.physical_repair is None:
            require(not identity.exists(), 'attempt UUID already reserved; no cross-scope reuse')
            save(identity, {'attempt_id':prepared.attempt_id, 'logical_path':str(root),
                            'plan_sha256':sha(canonical(plan))})
        else:
            from scripts.batch.harbor_disclosure_payload_repair import original_identity
            require(strict_json(identity.read_bytes())==original_identity(prepared.physical_repair),'original UUID reservation changed')
        save(root/'identity.json', record(identity))
        # Submission and dispatch-start are intentionally separate: a crash while
        # waiting for a slot remains retained, never interpreted as a new budget.
        submitted = dict(**asdict(prepared.policy), attempt_id=prepared.attempt_id,
                         submitted_at_ns=time.time_ns(), request=record(root/'request.json'),
                         semantic_budget_reset=False, scope=asdict(prepared.scope),
                         semantic_ordinal=prepared.ordinal, semantic_limit=prepared.limit,
                         response_mode=prepared.response_mode, model_slots=campaign.model_slots,
                         budget_evidence=list(prepared.budget_evidence))
        if prepared.review_reservation is not None:submitted['review_reservation']=prepared.review_reservation
        if prepared.physical_repair is not None:submitted['physical_repair']=prepared.physical_repair
        save(root/'submitted.json', submitted)
        try:
            failpoint('after_submitted')
            slot_options = (dict(operation_id=prepared.attempt_id + ('-physical-repair-01' if prepared.physical_repair is not None else ''),
                                 request_sha256=sha(body), outcome_path=root/'outcome.json')
                            if os.environ.get('HARBOR_FLEET_URL') else {})
            with xl_transport.model_slot(**slot_options):
                for index in range(plan['max_transport_retries'] + 1):
                    save(root/('transport-%02d.started.json' % index), dict(index=index,
                        started_at_ns=time.time_ns(), request_sha256=sha(body), attempt_id=prepared.attempt_id))
                    failpoint('after_dispatch_started')
                    request = urllib.request.Request(prepared.policy.endpoint, data=body, method='POST', headers={
                        'Authorization':'Bearer '+credential, 'Content-Type':'application/json',
                        'x-labelbox-context':json.dumps({'project_id':prepared.policy.project_id})})
                    body_path = root/('transport-%02d.body.raw' % index)
                    transport_stage = 'open'
                    try:
                        with _open(request, timeout) as response:
                            transport_stage = 'read'
                            raw = read_body(response, body_path)
                    except urllib.error.HTTPError as exc:
                        try:
                            raw = read_body(exc, body_path)
                            try:
                                observed = strict_json(raw)
                            except ValueError:
                                observed = None
                            if isinstance(observed,dict) and observed.get('object') == 'chat.completion':
                                seal(root,'incomplete',details={'reason':'completion_body_under_error_status',
                                     'http_status':exc.code,'automatic_replay':False})
                                return inspect_locked(root,plan,expected_request,reused=False)
                            retry_after = exc.headers.get('Retry-After') if exc.headers else None
                            retry = exc.code in RETRY_CODES and index < plan['max_transport_retries']
                            delay = retry_delay(index, retry_after) if retry else None
                            save(root/('transport-%02d.http-error.json' % index),dict(status=exc.code,
                                body=record(body_path), retry_after=retry_after, retry=retry,
                                delay_seconds=delay, completed_at_ns=time.time_ns()))
                        finally:
                            exc.close()
                        if not retry:
                            status='http_rejected';seal(root,status,details={'http_status':exc.code,'automatic_replay':False})
                            return inspect_locked(root, plan, expected_request, reused=False)
                        time.sleep(delay)
                        continue
                    except (OSError, urllib.error.URLError) as exc:
                        category = transient_open_failure(exc)
                        retry = (category is not None and transport_stage == 'open'
                                 and not body_path.exists() and index < plan['max_transport_retries'])
                        delay = retry_delay(index, None) if retry else None
                        save(root/('transport-%02d.connection-error.json' % index), dict(
                            category=category or 'unclassified', stage=transport_stage,
                            response_body_present=body_path.exists(), retry=retry,
                            delay_seconds=delay, completed_at_ns=time.time_ns()))
                        if not retry:
                            raise
                        time.sleep(delay)
                        continue
                    retain(root/'response.raw.json', raw)
                    failpoint('after_response_retained')
                    completed = dict(response=record(root/'response.raw.json'),completed_at_ns=time.time_ns(),
                                     response_mode=prepared.response_mode)
                    try:
                        response = strict_json(raw)
                        content = response['choices'][0]['message']['content']
                        require(isinstance(content,str),'response content must be text')
                        retain(root/'content.txt',content.encode())
                        if prepared.response_mode == 'json_object':
                            parsed = strict_json(content.encode())
                            require(isinstance(parsed,dict),'parsed model content must be an object')
                            save(root/'parsed.json',parsed)
                        else:
                            require(bool(content.strip()),'model text must be nonempty')
                        parsed_path = root/('parsed.json' if prepared.response_mode == 'json_object' else 'content.txt')
                        completed['review_sha256']=record(parsed_path)['sha256']
                        save(root/'completed.json',completed)
                        validate_call(call_from(root,prepared.response_mode),expected_request,policy=prepared.policy,
                                      json_response=prepared.response_mode == 'json_object', audit_context=prepared.audit_context)
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        if not (root/'completed.json').exists():save(root/'completed.json',completed)
                        seal(root,'response_rejected',details={'reason_type':type(exc).__name__,'automatic_replay':False})
                        return inspect_locked(root,plan,expected_request,reused=False)
                    failpoint('before_outcome')
                    seal(root,'completed')
                    return inspect_locked(root,plan,expected_request,reused=False)
        except BaseException as exc:
            # Do not serialize exception text/headers/locals containing runtime
            # credentials. Actual provider bytes are retained separately above.
            if not (root/'outcome.json').exists():
                seal(root,'incomplete',details={'reason_type':type(exc).__name__,'automatic_replay':False})
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):
                raise
            return inspect_locked(root,plan,expected_request,reused=False)
    raise ExecutorError('unreachable transport state')
