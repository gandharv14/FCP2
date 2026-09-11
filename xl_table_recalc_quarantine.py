"""ISOLATED PROPOSAL: domain-separated table recalculation quarantine evidence.

No runner execution, signing, source acceptance, publication or lineage approval.
The caller's producer digest and public key must come from protected deployment
configuration, never from this request/result. All returned claims are unsigned.
"""
from pathlib import Path
import base64
import hashlib
import json
import re
import time
import uuid

import xl_source_health as health
import xl_source_recalc as recalc
from scripts.recalc import excel_runner as observer

PROFILE = 'excel-table-quarantine/v1'
REQUEST = 'table-recalc-quarantine-request/v1'
CLAIMS = 'excel-table-quarantine-claims/v1'
RECEIPT = 'excel-table-quarantine-receipt/v1'
RESULT = 'table-recalc-quarantine-result/v1'
MAX_LIFETIME_NS = 86400 * 10**9

class QuarantineError(ValueError):
    pass

def canonical(value):
    return recalc._canonical_bytes(value)

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def file_hash(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise QuarantineError('evidence must be a regular, nonlinked file')
    return hashlib.sha256(p.read_bytes()).hexdigest()

def sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise QuarantineError('invalid SHA256')
    return value

def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise QuarantineError('unexpected or missing contract fields')

def same(actual, expected, label):
    # Canonical bytes deliberately distinguish bool from 0/1.
    if canonical(actual) != canonical(expected):
        raise QuarantineError(label + ' mismatch')

def code_identity(producer_sha256):
    return {
        'trusted_producer_sha256': sha(producer_sha256),
        'profile_sha256': file_hash(__file__),
        'observation_sha256': file_hash(observer.__file__),
        'snapshot_receipt_sha256': file_hash(recalc.__file__),
        'health_sha256': file_hash(health.__file__),
    }

def inspect(path):
    source_hash = file_hash(path)
    report = health.inspect_workbook(path)
    if report.get('route') != 'unsupported' or report.get('reason_codes') != ['data_tables_present']:
        raise QuarantineError('only the otherwise unsupported data-table route is quarantinable')
    observation = observer.observe_data_tables(Path(path))
    if not observation.get('tables'):
        raise QuarantineError('table profile requires complete nonempty table observations')
    snapshot = recalc.semantic_workbook_snapshot(path)
    same(observation['source_sha256'], source_hash, 'observation source')
    same(snapshot['source_sha256'], source_hash, 'snapshot source')
    same(file_hash(path), source_hash, 'source changed during inspection')
    return {'source_sha256': source_hash, 'observation': observation,
            'health_sha256': digest(report), 'snapshot': snapshot}

def make_request(source, *, workbook, output, producer_sha256, public_key,
                 engine_version, created_at_ns=None, lifetime_ns=MAX_LIFETIME_NS, request_id=None):
    if not isinstance(workbook, str) or re.fullmatch('[A-Za-z0-9_-]+', workbook) is None:
        raise QuarantineError('invalid original workbook identity')
    if not isinstance(engine_version, str) or not engine_version.strip():
        raise QuarantineError('explicit engine version required')
    created = time.time_ns() if created_at_ns is None else created_at_ns
    if type(created) is not int or created <= 0 or type(lifetime_ns) is not int or not 0 < lifetime_ns <= MAX_LIFETIME_NS:
        raise QuarantineError('invalid request lifetime')
    src = Path(source).absolute(); dest = Path(output).absolute()
    if src == dest or dest.suffix.lower() != '.xlsx' or dest.exists() or dest.is_symlink():
        raise QuarantineError('quarantine output must be a fresh distinct xlsx path')
    key = recalc._trusted_public_key(public_key)
    evidence = inspect(src)
    identity = str(uuid.uuid4()) if request_id is None else request_id
    if str(uuid.UUID(identity)) != identity:
        raise QuarantineError('invalid request ID')
    request = {'schema_version': REQUEST, 'profile': PROFILE, 'request_id': identity,
               'workbook': workbook, 'created_at_ns': created, 'expires_at_ns': created+lifetime_ns,
               'source_path': str(src), 'output_path': str(dest),
               'source_sha256': evidence['source_sha256'], 'source_size_bytes': src.stat().st_size,
               'original_observation': evidence['observation'], 'original_health_sha256': evidence['health_sha256'],
               'original_snapshot_sha256': digest(evidence['snapshot']),
               'code_identity': code_identity(producer_sha256), 'public_key_sha256': file_hash(key),
               'engine': 'excel-macos', 'engine_version': engine_version,
               'scope': 'quarantine_only', 'source_approval': False, 'task_approval': False}
    request['request_sha256'] = digest(request)
    return request

def validate_request(request, *, source, output, producer_sha256, public_key,
                     for_execution=False, now_ns=None):
    keys(request, ('schema_version','profile','request_id','workbook','created_at_ns','expires_at_ns',
                  'source_path','output_path','source_sha256','source_size_bytes','original_observation',
                  'original_health_sha256','original_snapshot_sha256','code_identity','public_key_sha256',
                  'engine','engine_version','scope','source_approval','task_approval','request_sha256'))
    same(request['request_sha256'], digest({k:v for k,v in request.items() if k!='request_sha256'}), 'request digest')
    for field, expected in {'schema_version':REQUEST,'profile':PROFILE,'scope':'quarantine_only',
                            'source_approval':False,'task_approval':False,'engine':'excel-macos'}.items():
        same(request[field], expected, field)
    try:
        if str(uuid.UUID(request['request_id'])) != request['request_id']: raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise QuarantineError('invalid request ID') from exc
    if not isinstance(request['workbook'], str) or not re.fullmatch('[A-Za-z0-9_-]+', request['workbook']):
        raise QuarantineError('invalid workbook identity')
    if not isinstance(request['engine_version'], str) or not request['engine_version'].strip():
        raise QuarantineError('engine version missing')
    start,end = request['created_at_ns'],request['expires_at_ns']
    now = time.time_ns() if now_ns is None else now_ns
    if type(start) is not int or type(end) is not int or not 0 < start < end or end-start > MAX_LIFETIME_NS or start > now+60*10**9:
        raise QuarantineError('invalid request time interval')
    if for_execution and not start <= now <= end:
        raise QuarantineError('request is not valid for execution now')
    src = Path(source).absolute(); dest = Path(output).absolute()
    same(request['source_path'], str(src), 'caller source path')
    same(request['output_path'], str(dest), 'caller output path')
    if src.resolve() == dest.resolve() or dest.is_symlink() or dest.suffix.lower() != '.xlsx':
        raise QuarantineError('unsafe output path')
    if for_execution and dest.exists():
        raise QuarantineError('execution cannot overwrite an existing output')
    same(request['code_identity'], code_identity(producer_sha256), 'reviewed producer/code')
    same(request['public_key_sha256'], file_hash(recalc._trusted_public_key(public_key)), 'protected key')
    evidence = inspect(src)
    same(request['source_sha256'], evidence['source_sha256'], 'original source')
    same(request['source_size_bytes'], src.stat().st_size, 'original size')
    same(request['original_observation'], evidence['observation'], 'complete original observation')
    same(request['original_health_sha256'], evidence['health_sha256'], 'current original health')
    same(request['original_snapshot_sha256'], digest(evidence['snapshot']), 'original semantic snapshot')
    return evidence

def _validated_evidence(request, *, source, staged, output, producer_sha256,
                            public_key, engine_version, started_at_ns, completed_at_ns,
                            isolation_controls):
    """Inspect actual retained files; return unsigned claims for a future trusted producer.

This function cannot establish that Excel produced `staged`. Only the distinct
trusted signature and reviewed producer implementation can establish that fact.
"""
    original = validate_request(request, source=source, output=output,
                                producer_sha256=producer_sha256, public_key=public_key)
    if len({Path(p).resolve() for p in (source,staged,output)}) != 3:
        raise QuarantineError('original, observed staging, and output must be separate files')
    if type(started_at_ns) is not int or type(completed_at_ns) is not int or not request['created_at_ns'] <= started_at_ns <= completed_at_ns <= request['expires_at_ns'] or completed_at_ns > time.time_ns()+60*10**9:
        raise QuarantineError('execution timestamps outside request interval')
    same(engine_version, request['engine_version'], 'engine version')
    keys(isolation_controls, observer.REQUIRED_CONTROLS)
    if any(v is not True for v in isolation_controls.values()):
        raise QuarantineError('six actual true isolation controls required')
    stage = inspect(staged); after = inspect(output)
    for key in ('geometry_sha256','primitive_inputs_sha256','formula_identity_sha256'):
        same(stage['observation'][key], original['observation'][key], 'staging '+key)
        same(after['observation'][key], original['observation'][key], 'output '+key)
    same(after['observation']['results_sha256'], stage['observation']['results_sha256'], 'complete observed table results')
    same(stage['snapshot']['semantics'], original['snapshot']['semantics'], 'staging financial semantics/settings/formats')
    same(after['snapshot']['semantics'], original['snapshot']['semantics'], 'original financial semantics/settings/formats')
    # First profile deliberately rejects cache-encoding ambiguities rather than
    # inferring a value match. Every normal formula and table body cache is bound.
    same(after['snapshot']['formula_caches'], stage['snapshot']['formula_caches'], 'all staging formula caches')
    for path, evidence in ((source, original), (staged, stage), (output, after)):
        same(file_hash(path), evidence['source_sha256'], 'evidence changed during joint validation')
    return {'schema_version':CLAIMS, 'profile':PROFILE, 'scope':'quarantine_only',
            'source_approval':False, 'task_approval':False, 'request_sha256':request['request_sha256'],
            'request_id':request['request_id'], 'workbook':request['workbook'],
            'source_sha256':original['source_sha256'], 'staging_sha256':stage['source_sha256'],
            'output_sha256':after['source_sha256'], 'code_identity':code_identity(producer_sha256),
            'original_observation_sha256':digest(original['observation']),
            'staging_observation_sha256':digest(stage['observation']),
            'output_observation_sha256':digest(after['observation']),
            'original_snapshot_sha256':digest(original['snapshot']),
            'staging_snapshot_sha256':digest(stage['snapshot']), 'output_snapshot_sha256':digest(after['snapshot']),
            'health_sha256':{k:v['health_sha256'] for k,v in [('original',original),('staging',stage),('output',after)]},
            'engine':'excel-macos', 'engine_version':engine_version,
            'started_at_ns':started_at_ns, 'completed_at_ns':completed_at_ns,
            'calculation_complete':True, 'isolation_enforced':True,
            'isolation_controls':isolation_controls,
            'network_isolation_mechanism':'macos-pf-anchor',
            'network_isolation_rules_sha256':recalc.PF_NETWORK_RULES_SHA256,
            'public_key_sha256':request['public_key_sha256']}


_DERIVED_FIELDS = ('original_snapshot_sha256', 'staging_snapshot_sha256',
                   'output_snapshot_sha256', 'health_sha256')

def _split_evidence(evidence, *, source, staged, output):
    derived = {'schema_version':'table-quarantine-derived-evidence/v1',
               'authority':'pipeline_derived_not_runner_attested',
               **{k:evidence[k] for k in _DERIVED_FIELDS}}
    signed = {k:v for k,v in evidence.items() if k not in _DERIVED_FIELDS and k!='code_identity'}
    signed['selected_policy_code'] = evidence['code_identity']
    signed.update(source_path=str(Path(source).absolute()),
                  staging_path=str(Path(staged).absolute()), output_path=str(Path(output).absolute()))
    return signed, derived

def prepare_unsigned_claims(request, **bindings):
    """Pipeline preview only: the protected producer reconstructs its own facts.

Only observable facts enter this receipt domain. Snapshot/health hashes remain
separately derived and are never represented as trusted runner attestations.
"""
    evidence = _validated_evidence(request, **bindings)
    return _split_evidence(evidence, **{k:bindings[k] for k in ('source','staged','output')})[0]

def validate_result(result, request, *, source, staged, output, producer_sha256, public_key):
    """Return quarantine evidence only; never a source/recalc-policy pass."""
    keys(result, ('schema_version','request_sha256','receipt','derived_evidence','result_sha256'))
    same(result['schema_version'], RESULT, 'result schema')
    same(result['request_sha256'], request['request_sha256'], 'result request')
    same(result['result_sha256'], digest({k:v for k,v in result.items() if k!='result_sha256'}), 'result digest')
    receipt=result['receipt']
    keys(receipt, ('schema_version','signature_algorithm','signed_payload','signature_base64'))
    same(receipt['schema_version'], RECEIPT, 'receipt domain')
    same(receipt['signature_algorithm'], 'ed25519', 'signature algorithm')
    payload=receipt['signed_payload']
    if not isinstance(payload, dict): raise QuarantineError('missing signed claims')
    evidence=_validated_evidence(request, source=source, staged=staged, output=output,
                 producer_sha256=producer_sha256, public_key=public_key,
                 engine_version=payload.get('engine_version'), started_at_ns=payload.get('started_at_ns'),
                 completed_at_ns=payload.get('completed_at_ns'), isolation_controls=payload.get('isolation_controls'))
    expected, derived = _split_evidence(evidence, source=source, staged=staged, output=output)
    same(payload, expected, 'independently reconstructed signed observations')
    same(result['derived_evidence'], derived, 'independently reconstructed unsigned derived evidence')
    try:
        signature=base64.b64decode(receipt['signature_base64'], validate=True)
    except (ValueError, TypeError) as exc:
        raise QuarantineError('invalid signature encoding') from exc
    key=recalc._ed25519_public_bytes(recalc._trusted_public_key(public_key))
    if key is None or not recalc._verify_ed25519(key,signature,canonical(payload)):
        raise QuarantineError('untrusted quarantine signature')
    return {'schema_version':'validated-table-quarantine-evidence/v1','scope':'quarantine_only',
            'source_approval':False,'task_approval':False,'request_sha256':request['request_sha256'],
            'result_sha256':result['result_sha256'],'claims_sha256':digest(payload),
            'original_sha256':payload['source_sha256'],'intermediate_sha256':payload['output_sha256']}

def assemble_result(request, receipt, **bindings):
    """Package only a valid independently signed receipt; no signing capability."""
    payload=receipt.get('signed_payload', {})
    evidence=_validated_evidence(request, **bindings, engine_version=payload.get('engine_version'),
        started_at_ns=payload.get('started_at_ns'),completed_at_ns=payload.get('completed_at_ns'),
        isolation_controls=payload.get('isolation_controls'))
    _,derived=_split_evidence(evidence, **{k:bindings[k] for k in ('source','staged','output')})
    result = {'schema_version': RESULT, 'request_sha256': request['request_sha256'],
              'receipt': receipt, 'derived_evidence':derived}
    result['result_sha256'] = digest(result)
    validate_result(result, request, **bindings)
    return result
