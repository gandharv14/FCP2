"""Prospective fleet transport gate; no network replay, no implicit uncertain release."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from scripts.fleet.coordinator import Client, Conflict, canonical


def configured():
    return bool(os.environ.get('HARBOR_FLEET_URL'))


def context():
    names=['URL','TOKEN_FILE','WORKBOOK','OWNER','CLAIM_TOKEN','FENCE','MODEL_JOURNAL']
    values={name:os.environ['HARBOR_FLEET_'+name] for name in names}
    owner=dict(workbook=values['WORKBOOK'],owner=values['OWNER'],token=values['CLAIM_TOKEN'],fence=int(values['FENCE']))
    return Client(values['URL'],values['TOKEN_FILE']),owner,Path(values['MODEL_JOURNAL'])


def retain(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    data=value if isinstance(value,bytes) else canonical(value).encode()
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def evidence(path):
    return canonical(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def terminal_http_rejection(outcome, operation_id, request_sha256):
    """Recognize a sealed one-shot HTTP error, never a successful model review.

    Run without acquiring the executor lock: its caller still owns that lock.
    Partial, modified, retried, or ambiguous journals remain reconciliation holds.
    """
    try:
        from scripts.batch.harbor_tracker import strict_json
        root = outcome.parent
        files = {}
        for path in root.iterdir():
            if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
                return False
            if path.name != 'outcome.json':
                files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        value = strict_json(outcome.read_bytes())
        if (value.get('schema_version') != 'harbor-model-execution/v1'
                or value.get('status') != 'http_rejected'
                or value.get('semantic_approval') is not False
                or value.get('evidence') != files):
            return False
        plan = strict_json((root/'plan.json').read_bytes())
        attempt = plan['attempt_id']
        expected_operation = attempt + ('-physical-repair-01' if plan.get('physical_repair') is not None else '')
        if (operation_id != expected_operation or plan.get('max_transport_retries') != 0
                or files.get('request.json') != request_sha256):
            return False
        # There must be exactly one transport and a completed, non-retrying receipt.
        names = {name for name in files if name.startswith('transport-')}
        if names != {'transport-00.started.json', 'transport-00.body.raw', 'transport-00.http-error.json'}:
            return False
        if {'response.raw.json', 'response.json', 'completed.json', 'parsed.json', 'content.txt'} & files.keys():
            return False  # Contradictory successful-Call artifacts require reconciliation.
        started = strict_json((root/'transport-00.started.json').read_bytes())
        receipt = strict_json((root/'transport-00.http-error.json').read_bytes())
        status = receipt.get('status')
        details = value.get('details')
        if (type(status) is not int or not 400 <= status <= 599
                or details != {'http_status': status, 'automatic_replay': False}
                or receipt.get('retry') is not False or receipt.get('delay_seconds') is not None
                or receipt.get('body') != {'path': str(root/'transport-00.body.raw'),
                                          'sha256': files['transport-00.body.raw']}
                or started.get('index') != 0 or started.get('attempt_id') != attempt
                or started.get('request_sha256') != request_sha256):
            return False
        times = [started.get('started_at_ns'), receipt.get('completed_at_ns'), value.get('sealed_at_ns')]
        if any(type(t) is not int or t <= 0 for t in times) or times != sorted(times):
            return False
        try:
            body = strict_json((root/'transport-00.body.raw').read_bytes())
        except ValueError:
            body = None
        return not (isinstance(body, dict) and body.get('object') == 'chat.completion')
    except (ValueError, TypeError, KeyError, OSError):
        return False


def other_model_hold(status, owner, operation_id):
    """Wait only on authenticated other-workbook holds; never grant a permit."""
    from scripts.fleet.capacity_policy import validate_occupancy
    if status.get('schema_version') != 'fleet-coordinator/v1':
        raise Conflict('unknown fleet schema while waiting')
    try:
        occupied, _ = validate_occupancy(status)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Conflict('unknown fleet limits or operation occupancy while waiting') from exc
    held_operations = [op for op in occupied if op['state'] == 'hold']
    if not held_operations:
        return None  # Only the next transactional begin may grant execution.
    proofs = []
    for held in held_operations:
        if (held.get('workbook') == owner['workbook']
                or held.get('operation_id') == operation_id
                or not isinstance(held.get('operation_id'), str)
                or not held['operation_id']):
            raise Conflict('own or unknown model hold requires reconciliation')
        rows = [w for w in status.get('workbooks', [])
                if w.get('workbook') == held['workbook']]
        if (len(rows) != 1 or rows[0].get('state') != 'hold'
                or rows[0].get('owner') != held.get('owner')
                or rows[0].get('fence') != held.get('fence')):
            raise Conflict('held model owner is not reconciled in the coordinator')
        proofs.append({k: held.get(k) for k in ('operation_id', 'workbook', 'owner', 'fence', 'state')})
    if len({op['operation_id'] for op in proofs}) != len(proofs):
        raise Conflict('duplicate held operation identity')
    proofs.sort(key=lambda op: op['operation_id'])
    selected = dict(proofs[0])
    if len(proofs) > 1:
        selected['held_operations'] = proofs  # Retain the complete observed barrier.
    return selected


@contextmanager
def slot(*, operation_id, request_sha256, outcome_path):
    client,owner,journal=context()
    # Filename independent of endpoint request body or credentials.
    directory=journal/hashlib.sha256(operation_id.encode()).hexdigest()
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    intent=directory/'intent.json'
    if intent.exists():
        raise Conflict('fleet model intent already exists; reconcile without replay')
    started=time.monotonic()
    from scripts.fleet.physical_model_capacity import selected as physical_selection
    physical = physical_selection()
    intent_value = dict(operation_id=operation_id,request_sha256=request_sha256,
                       workbook=owner['workbook'],owner=owner['owner'],fence=owner['fence'],at=time.time())
    if physical is not None:
        intent_value['physical_capacity_policy'] = physical[0]
    retain(intent,intent_value)
    last_heartbeat = None
    while True:
        acquired=client.call('begin',kind='model',operation_id=operation_id,request_sha256=request_sha256,**owner)
        if acquired['execute']:
            break
        if not acquired.get('busy'):
            raise Conflict('existing durable model operation; do not replay')
        if acquired.get('state') == 'hold':
            held = other_model_hold(client.call('status'), owner, operation_id)
            if held is not None:
                waiting = directory / ('waiting-for-' + hashlib.sha256(
                    held['operation_id'].encode()).hexdigest() + '.json')
                if not waiting.exists():
                    retain(waiting, dict(held_operation=held, at=time.time(),
                                         physical_dispatch_started=False))
        now = time.monotonic()
        if last_heartbeat is None or now - last_heartbeat >= 15:
            client.call('heartbeat', lease_seconds=180, **owner)
            last_heartbeat = now
        time.sleep(1)
    retain(directory/'acquired.json',dict(wait_seconds=time.monotonic()-started,at=time.time()))
    try:
        yield
    finally:
        # A normal Python return is insufficient: the executor may catch errors.
        # Only a persisted, terminal response journal proves completion.
        outcome=Path(outcome_path)
        confirmed=False
        if outcome.is_file():
            value=json.loads(outcome.read_text())
            confirmed=value.get('status') in {'completed','response_rejected'} and (outcome.parent/'response.raw.json').is_file()
            if value.get('status') == 'http_rejected':
                confirmed=terminal_http_rejection(outcome, operation_id, request_sha256)
        proof=evidence(outcome) if outcome.is_file() else evidence(intent)
        result=client.call('end',kind='model',operation_id=operation_id,evidence=proof,
                           outcome='confirmed' if confirmed else 'uncertain',**owner)
        retain(directory/'released.json',dict(result=result,at=time.time()))


def read_response(request, *, timeout, local_slot):
    """Fallback transport callers also retain a one-shot response before release."""
    import urllib.request
    operation_id=str(uuid.uuid4())
    _,_,journal=context()
    directory=journal/('transport-'+operation_id)
    body=request.data or b''
    request_hash=hashlib.sha256(body).hexdigest()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with local_slot(), slot(operation_id=operation_id,request_sha256=request_hash,outcome_path=directory/'outcome.json'):
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout) as response:
            raw=response.read()
        retain(directory/'response.raw.json',raw)
        retain(directory/'outcome.json',dict(status='completed',response_sha256=hashlib.sha256(raw).hexdigest()))
        return raw
