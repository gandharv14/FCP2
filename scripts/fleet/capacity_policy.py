"""Truthful validation of the explicitly authorized fleet model policy.

This does not change task campaigns, review caps, model identities, or resource
guards. Historical model-one status remains readable; the new policy is accepted
only with the retained authorization binding. No status fields are rewritten.
"""
from pathlib import PurePosixPath

AUTHORIZATION_SHA256 = '4cf7d589371b8b7805b34658f801884ac769ea8bc69009631610975e32c94263'
CHANGE_ID = 'remove-global-model-limit-20260913-001'
SCHEMA = 'fleet-concurrency-policy/v1'


def validate_limits(status):
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    require(type(status.get('publication_limit')) is int and status['publication_limit'] == 1,
            'publication must remain serialized')
    limit = status.get('model_limit', 'missing')
    policy = status.get('model_policy')
    if type(limit) is int and limit == 1 and policy is None:
        return 1  # Original immutable coordinator response.
    require(isinstance(policy, dict) and set(policy) == {
        'schema_version', 'revision', 'model_limit', 'authorization', 'change_id',
        'per_workbook_model_limit', 'unknown_model_hold_barrier', 'publication_limit'},
        'explicit complete fleet concurrency policy required')
    require(policy['schema_version'] == SCHEMA
            and type(policy['publication_limit']) is int and policy['publication_limit'] == 1
            and type(policy['per_workbook_model_limit']) is int and policy['per_workbook_model_limit'] == 1
            and policy['unknown_model_hold_barrier'] == 'global', 'unknown concurrency safeguards')
    if type(limit) is int and limit == 1 and policy['revision'] == 0:
        require(type(policy['revision']) is int and type(policy['model_limit']) is int and policy['model_limit'] == 1
                and policy['authorization'] is None and policy['change_id'] is None,
                'legacy policy metadata differs')
        return 1
    authorization = policy['authorization']
    require(limit is None and policy['model_limit'] is None
            and type(policy['revision']) is int and policy['revision'] == 1
            and policy['change_id'] == CHANGE_ID, 'unapproved model concurrency policy')
    require(isinstance(authorization, dict) and set(authorization) == {'path', 'sha256'}
            and authorization['sha256'] == AUTHORIZATION_SHA256
            and isinstance(authorization['path'], str)
            and PurePosixPath(authorization['path']).is_absolute()
            and '..' not in PurePosixPath(authorization['path']).parts,
            'model concurrency authorization differs')
    return None


def validate_occupancy(status):
    """Check actual occupancy without treating an uncertain operation as free."""
    limit = validate_limits(status)
    operations = status.get('operations')
    if not isinstance(operations, list):
        raise ValueError('durable operation inventory required')
    occupied = []
    for operation in operations:
        if (not isinstance(operation, dict) or operation.get('kind') not in {'model', 'publication'}
                or operation.get('state') not in {'active', 'hold', 'confirmed', 'abandoned'}):
            raise ValueError('unknown durable operation')
        if operation['state'] in {'active', 'hold'}:
            occupied.append(operation)
    models = [op for op in occupied if op['kind'] == 'model']
    publications = [op for op in occupied if op['kind'] == 'publication']
    workbooks = [op.get('workbook') for op in models]
    if (len(publications) > 1 or limit is not None and len(models) > limit
            or any(not isinstance(w, str) or not w for w in workbooks)
            or len(set(workbooks)) != len(workbooks)):
        raise ValueError('durable operation occupancy violates authorized policy')
    return models, publications


def validate_quarantines(status):
    """Validate optional append-only isolation; held operations remain occupied."""
    import hashlib
    import json
    import math
    models, publications = validate_occupancy(status)
    policy = status.get('quarantine_policy')
    rows = status.get('quarantines')
    if policy is None and rows is None:
        return []
    expected = {'schema_version', 'revision', 'default_unknown_model_hold_barrier',
                'maximum_exception_seconds', 'per_workbook_model_limit', 'publication_limit'}
    if (not isinstance(policy, dict) or set(policy) != expected or not isinstance(rows, list)
            or policy != dict(schema_version='fleet-model-quarantine-policy/v1', revision=len(rows),
                              default_unknown_model_hold_barrier='global', maximum_exception_seconds=86400,
                              per_workbook_model_limit=1, publication_limit=1)
            or type(policy['revision']) is not int):
        raise ValueError('unknown quarantine policy')
    now = status.get('at')
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError('finite coordinator observation required')
    fields = {'operation_id', 'workbook', 'owner', 'fence', 'source_sha256', 'version', 'host',
              'operation_sha256', 'authorization', 'evidence', 'change_id', 'expires_at',
              'schema_version', 'revision', 'created_at', 'state', 'provider_completion',
              'consumed_operation_retained', 'workbook_blocked', 'host_blocked'}
    seen = set(); workbooks = set(); applicable = []
    for revision, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or set(row) != fields
                or row['schema_version'] != 'fleet-model-quarantine/v1'
                or type(row['revision']) is not int or row['revision'] != revision
                or row['state'] != 'quarantined_unknown' or row['provider_completion'] != 'unknown'
                or any(row[k] is not True for k in ('consumed_operation_retained', 'workbook_blocked', 'host_blocked'))
                or type(row['fence']) is not int or row['fence'] < 1):
            raise ValueError('unknown quarantine record')
        for key in ('created_at', 'expires_at'):
            if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
                raise ValueError('invalid quarantine clock')
        if not 0 < row['expires_at'] - row['created_at'] <= 86400 or row['created_at'] > now:
            raise ValueError('quarantine duration differs')
        for key in ('operation_id', 'workbook', 'owner', 'version', 'change_id'):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError('quarantine identity missing')
        if row['host'] not in {'hhu-server', *('hhu-server-' + str(i) for i in range(2, 9))} or quarantine_host(row['owner']) != row['host']:
            raise ValueError('quarantine host identity differs')
        for key in ('source_sha256', 'operation_sha256'):
            if len(row[key]) != 64 or any(c not in '0123456789abcdef' for c in row[key]):
                raise ValueError('quarantine digest invalid')
        for key in ('authorization', 'evidence'):
            value = row[key]
            if (not isinstance(value, dict) or set(value) != {'path', 'sha256'}
                    or not isinstance(value['path'], str) or not PurePosixPath(value['path']).is_absolute()
                    or '..' in PurePosixPath(value['path']).parts or not isinstance(value['sha256'], str)
                    or len(value['sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['sha256'])):
                raise ValueError('quarantine retained authority missing')
        if row['operation_id'] in seen or row['workbook'] in workbooks:
            raise ValueError('duplicate quarantine identity')
        seen.add(row['operation_id']); workbooks.add(row['workbook'])
        operations = [op for op in status['operations'] if op['kind'] == 'model' and op['operation_id'] == row['operation_id']]
        claims = [wb for wb in status.get('workbooks', []) if wb['workbook'] == row['workbook']]
        if len(operations) != 1 or len(claims) != 1:
            raise ValueError('quarantined operation or workbook missing')
        operation, claim = operations[0], claims[0]
        # A later explicit terminal resolution remains visible. It does not
        # erase this record or automatically unblock the quarantined host.
        if operation['state'] in {'confirmed', 'abandoned'}:
            continue
        actual = hashlib.sha256(json.dumps(operation, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (operation['state'] != 'hold' or actual != row['operation_sha256']
                or claim['state'] != 'hold'
                or any(operation.get(k) != row[k] for k in ('workbook', 'owner', 'fence'))
                or any(claim.get(k) != row[k] for k in ('workbook', 'owner', 'fence', 'source_sha256', 'version'))):
            raise ValueError('quarantined held identity changed')
        if now < row['expires_at']:
            applicable.append(row)
    return applicable


def model_admission_barriers(status, workbook, owner):
    """Read-only admission hint; transactional begin remains the only permit."""
    models, _ = validate_occupancy(status)
    applicable = validate_quarantines(status)
    quarantines = status.get('quarantines', [])
    exceptions = validate_host_capacities(status,applicable)
    extensions = validate_physical_extensions(status)
    host = quarantine_host(owner) if quarantines else None
    if quarantines:
        if host not in {'hhu-server', *('hhu-server-' + str(i) for i in range(2, 9))}:
            raise ValueError('explicit host required under quarantine')
        allowed_host=any(row['host']==host for row in exceptions)
        if any(q['workbook'] == workbook or (q['host'] == host and not allowed_host) for q in quarantines):
            raise ValueError('quarantined workbook or host remains blocked')
    selected=next((row for row in exceptions if row['host']==host),None)
    if selected is not None:
        occupied=[op for op in models if quarantine_host(op['owner'])==host]
        limit=next((r['physical_model_limit']for r in extensions if r['host']==host),selected['physical_model_limit'])
        if len(occupied)>=limit:return occupied
    allowed = {q['operation_id'] for q in applicable}
    return [op for op in models if op['state'] == 'hold' and op['operation_id'] not in allowed
            or op['workbook'] == workbook]


def quarantine_host(owner):
    """Existing pool slash and one-off colon identities share an exact VM host."""
    import re
    match = re.fullmatch(r'(hhu-server(?:-[2-8])?)([:/])[^\s]+', owner) if isinstance(owner, str) else None
    if not match:
        raise ValueError('explicit authorized VM host required')
    return match.group(1)


def validate_host_capacities(status, applicable_quarantines=None):
    """Validate append-only host exceptions without releasing uncertain effects."""
    import hashlib,json,math
    from pathlib import PurePosixPath
    applicable_quarantines = validate_quarantines(status) if applicable_quarantines is None else applicable_quarantines
    rows=status.get('quarantine_host_capacity',[])
    if not isinstance(rows,list):raise ValueError('host capacity records required')
    active=[];seen=set();now=status['at']
    fields={'host','quarantine_change_id','quarantine_sha256','physical_model_limit','authorization','evidence',
            'change_id','expires_at','schema_version','revision','created_at','uncertain_operation_retained','workbook_blocked'}
    for revision,row in enumerate(rows,1):
        if (not isinstance(row,dict)or set(row)!=fields or row['schema_version']!='fleet-quarantine-host-capacity/v1'
                or type(row['revision'])is not int or row['revision']!=revision
                or type(row['physical_model_limit'])is not int or row['physical_model_limit']!=2
                or row['uncertain_operation_retained']is not True or row['workbook_blocked']is not True
                or not isinstance(row['change_id'],str)or not row['change_id'] or row['host']in seen):
            raise ValueError('unknown host capacity exception')
        seen.add(row['host'])
        for key in ('created_at','expires_at'):
            if type(row[key])not in(int,float)or not math.isfinite(row[key]):raise ValueError('finite host capacity clock required')
        q=[q for q in status.get('quarantines',[])if q['change_id']==row['quarantine_change_id']]
        if len(q)!=1:raise ValueError('host exception missing quarantine')
        q=q[0];sha=hashlib.sha256(json.dumps(q,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if (q['host']!=row['host'] or sha!=row['quarantine_sha256']
                or not q['created_at']<=row['created_at']<=now
                or not row['created_at']<row['expires_at']<=q['expires_at']):raise ValueError('host exception detached or extended')
        for key in ('authorization','evidence'):
            value=row[key]
            if (not isinstance(value,dict)or set(value)!={'path','sha256'}or not isinstance(value['path'],str)
                    or not PurePosixPath(value['path']).is_absolute()or '..'in PurePosixPath(value['path']).parts
                    or not isinstance(value['sha256'],str)or len(value['sha256'])!=64
                    or any(x not in '0123456789abcdef'for x in value['sha256'])):raise ValueError('host capacity authority missing')
        if now<row['expires_at'] and q in applicable_quarantines:active.append(row)
    return active


def validate_physical_extensions(status):
    """Authenticate separate finite policy; the original v1 records stay intact."""
    import hashlib,json,math
    def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    rows=status.get('physical_capacity_extensions',[]);active=[];seen=set();now=status['at']
    if not isinstance(rows,list):raise ValueError('physical extension ledger required')
    qs=validate_quarantines(status);capacities=validate_host_capacities(status,qs);policy=status.get('author_capacity_policy')
    fields={'host','prior_capacity_sha256','quarantines','physical_model_limit','maximum_fresh_models','authorization','evidence','change_id','expires_at','schema_version','revision','identity','created_at','author_policy_sha256','unknown_slots_consumed','unknown_operations_released','author_limit_changed'}
    for revision,row in enumerate(rows,1):
        if (set(row)!=fields or row['schema_version']!='fleet-physical-capacity-extension/v1' or type(row['revision'])is not int or row['revision']!=revision or row['host']in seen
                or type(row['physical_model_limit'])is not int or row['physical_model_limit']!=3 or type(row['maximum_fresh_models'])is not int or row['maximum_fresh_models']!=1
                or type(row['unknown_slots_consumed'])is not int or row['unknown_slots_consumed']!=2 or row['unknown_operations_released']is not False or row['author_limit_changed']is not False):raise ValueError('unsupported bounded physical extension')
        seen.add(row['host'])
        if any(type(row[k])not in(int,float)or not math.isfinite(row[k])for k in ('created_at','expires_at'))or not row['created_at']<=now or not 0<row['expires_at']-row['created_at']<=14400:raise ValueError('physical extension clock differs')
        for k in ('authorization','evidence'):
            v=row[k]
            if not isinstance(v,dict)or set(v)!={'path','sha256'}or not isinstance(v['path'],str)or not v['path'].startswith('/')or '..'in v['path'].split('/')or not isinstance(v['sha256'],str)or len(v['sha256'])!=64 or any(c not in '0123456789abcdef'for c in v['sha256']):raise ValueError('physical extension authority missing')
        args={k:row[k]for k in ('host','prior_capacity_sha256','quarantines','physical_model_limit','maximum_fresh_models','authorization','evidence','change_id','expires_at')};args['expected_revision']=revision-1
        if digest(args)!=row['identity']or digest(policy)!=row['author_policy_sha256']:raise ValueError('physical extension identity or author policy differs')
        prior=[c for c in status.get('quarantine_host_capacity',[])if c['host']==row['host']]
        allq=[q for q in status.get('quarantines',[])if q['host']==row['host']]
        if len(prior)!=1 or digest(prior[0])!=row['prior_capacity_sha256']or len(allq)!=2 or {q['change_id']:digest(q)for q in allq}!=row['quarantines']or row['expires_at']>min(prior[0]['expires_at'],*(q['expires_at']for q in allq)):raise ValueError('physical extension detached from retained uncertainty')
        if now<row['expires_at']and prior[0]in capacities and all(q in qs for q in allq):active.append(row)
    return active
