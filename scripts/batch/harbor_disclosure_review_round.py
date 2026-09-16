"""Append-only disclosure rounds after a completed build and sealed rejection.

This is review authority, not a replacement Call or an authoring checkpoint.
Original work, request bytes, executor outcomes and consumed rounds stay intact.
"""
from dataclasses import asdict
from pathlib import Path
import fcntl
import importlib.util
import json
import sys
import time
from scripts.batch.harbor_admission import read, record, require
from scripts.batch import harbor_continuation_allowance as A
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_tracker_lifecycle as life

SCHEMA = 'harbor-disclosure-review-round/v1'
FIRST = 'harbor-disclosure-first-review-reservation/v1'
KEYS = {'schema_version', 'implementation', 'allowance', 'reservation', 'round',
        'limit', 'prior_rejections', 'build', 'inputs', 'producer_run', 'created_at_ns',
        'prefix', 'prefix_evidence', 'prefix_directories'}


def semantic_scope(p):
    return dict(workbook_id=p['workbook'], original_sha256=p['original_source']['sha256'],
                lane='harbor-disclosure-writer', budget_id='disclosure-review')


def closed_rejection(binding, scope, ordinal):
    """Prove one real HTTP400 transport terminal, without acquiring or replaying it."""
    selected=read(binding)
    if selected.get('schema_version')in {'harbor-completed-disclosure-partition-rejection/v1',
                                        'harbor-completed-disclosure-review-rejection/v1'}:
        reader=bound_module(selected['implementation'])
        require(reader.SCHEMA==selected['schema_version'],'selected partition rejection reader differs')
        return reader.closed_rejection(binding,scope,ordinal)
    from scripts.fleet.model_gate import terminal_http_rejection
    life.checked_record(binding)
    root = Path(binding['path']).parent
    require(Path(binding['path']).name == 'outcome.json', 'exact executor outcome required')
    plan_binding = record(root/'plan.json'); plan = read(plan_binding)
    outcome = read(binding)
    require(plan['scope'] == scope and plan['ordinal'] == ordinal
            and plan.get('physical_repair') is None and plan.get('max_transport_retries') == 0,
            'terminal disclosure scope, ordinal or transport policy changed')
    require(root == Path(plan['campaign_root'])/E.sha(E.canonical(scope))/('attempt-%02d'%ordinal),
            'terminal executor journal moved or scope changed')
    require(outcome.get('details') == {'http_status': 400, 'automatic_replay': False},
            'only a sealed HTTP400 supports this distinct new round')
    require(terminal_http_rejection(Path(binding['path']), plan['attempt_id'],
                                   plan['request']['sha256']), 'incomplete or contradictory transport remains held')
    require(record(root/'request.json')['sha256'] == plan['request']['sha256'], 'old request bytes changed')
    inspected = E.inspect_locked(root, plan, read(plan['request']))
    require(inspected.status == 'http_rejected' and inspected.call is None,
            'terminal request is not an unreviewed HTTP rejection')
    return True


def completed_build(build, *, worker=None, selection=None, campaign=None):
    """Validate the actual original guarded STEP_COMPLETE, never a copied pass flag."""
    from scripts import harbor_instruction_campaign as C
    require(set(build) == {'outcome', 'result', 'descriptor', 'dispatcher', 'instruction'}, 'closed completed-build receipt required')
    require(record(C.__file__)==build['instruction'],'original instruction reader changed')
    D=bound_module(build['dispatcher'])
    result_binding, value = D.successful(build['outcome'])
    require(result_binding == build['result'] and value['descriptor'] == build['descriptor']
            and value['status'] == 'STEP_COMPLETE' and value['next_step'] == 'disclosure-review',
            'original completed disclosure-build boundary required')
    configured, controls, recipe = C.selected(build['descriptor'])
    prior, selected, lease = C._prior(build['result'], build['descriptor'])
    require(prior == value, 'build checkpoint reader differs')
    if worker is not None:
        require(C.worker_encode(configured) == C.worker_encode(worker), 'build belongs to another worker or code')
    if selection is not None:
        require(value['selection'] == C.codec.encode(selection), 'completed build selection changed')
    if campaign is not None:
        require(C.base.encode(configured.campaign) == C.base.encode(campaign), 'build campaign changed')
    out = read(build['outcome']); resource = read(out['resource']); intent = read(resource['intent'])
    require(intent['code'] == record(C.__file__) and intent['controls'] == C.base.encode(controls)
            and intent['action'] == 'step' and intent['arguments']['descriptor'] == build['descriptor'],
            'completed build owning guard detached from original descriptor')
    require(intent['timeout_seconds'] == C.guard_seconds('step', intent['arguments'])
            and intent['memory_limit_bytes'] == C.base.MEMORY, 'completed build guard changed')
    checkpoint = read(value['checkpoint']); child_intent = read(checkpoint['intent'])
    require(checkpoint['status'] == 'completed' and checkpoint['step'] == 'disclosure-build'
            and child_intent['initial'] == record(configured.run/'initial.json'),
            'original completed disclosure command evidence required')
    for path, digest in child_intent['code'].items():
        life.checked_record(dict(path=path, sha256=digest))
    return configured, selected, lease


def bound_module(binding):
    """Resolve a retained dispatcher by exact file, including historical aliases."""
    life.checked_record(binding)
    for module in tuple(sys.modules.values()):
        if module is not None and getattr(module,'__file__',None)==binding['path']:
            return module
    name='retained_disclosure_dispatcher_'+binding['sha256']
    spec=importlib.util.spec_from_file_location(name,binding['path'])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def _first_reservation(allowance, scope, *, limit, now):
    """Explicitly select the unused first round in the same stable policy ledger."""
    ledger = Path(allowance['path']).parent/'review-reservations'
    require(ledger.is_dir(), 'existing policy review ledger required')
    with (ledger/'review.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = A.validate(allowance, expected_scope=A.scope(scope['workbook_id'], scope['original_sha256']), at=now)
        require(0 < limit <= policy['limits']['review_attempts'], 'first round exceeds explicit allowance')
        path = ledger/(life.schema.sha(life.canonical(scope))+'-000001.json')
        return A.retain(path, dict(schema_version=FIRST, allowance=allowance, scope=scope,
                                  ordinal=1, cumulative_limit=limit, prior_records=[],
                                  automatic_retry=False, created_at_ns=now))


def validate_reservation(binding, scope, ordinal, limit):
    r = read(binding)
    if ordinal != 1:
        value, _ = A.validate_review_reservation(binding, scope, ordinal, limit)
        require(value['original_limit'] == 1, 'original disclosure limit changed')
        for i, old in enumerate(value['prior_records'], 1): closed_rejection(old, scope, i)
        return value
    require(set(r) == {'schema_version','allowance','scope','ordinal','cumulative_limit',
                       'prior_records','automatic_retry','created_at_ns'} and r['schema_version'] == FIRST,
            'closed explicit first-round reservation required')
    require(r['scope'] == scope and r['ordinal'] == 1 and r['cumulative_limit'] == limit
            and r['prior_records'] == [] and r['automatic_retry'] is False, 'first-round consumption changed')
    policy = A.validate(r['allowance'], expected_scope=A.scope(scope['workbook_id'],scope['original_sha256']), at=r['created_at_ns'])
    require(type(limit) is int and 1 <= limit <= policy['limits']['review_attempts'], 'first round ceiling changed')
    expected = Path(r['allowance']['path']).parent/'review-reservations'/(life.schema.sha(life.canonical(scope))+'-000001.json')
    require(Path(binding['path']) == expected, 'first-round ledger moved')
    return r


def prefix_records(prefix):
    """Preserve file evidence and explicitly typed directory membership."""
    import copy
    from scripts.batch import harbor_readiness as static
    from scripts.batch.harbor_complete_readiness import evidence_records
    clean=copy.deepcopy(prefix);directories=[];directory_files=[]
    for row in clean:
        bindings=row['result']['bindings']
        for name in ('source_generation','segmentation_generation','mcp_build_a','mcp_build_b','mcp_bundle','staged_bundle'):
            if name not in bindings:continue
            value=bindings[name];path=Path(value['path'])
            require(path.is_absolute() and path.is_dir() and not any(x.is_symlink()for x in(path,*path.parents)),
                    'canonical typed prefix directory required')
            digest,inventory=static.publication._tree_hash(path)
            if name not in ('source_generation','segmentation_generation'):
                require(set(value)=={'path','sha256'} and digest==value['sha256'],'typed prefix directory changed')
            else:
                require(set(value)=={'path','id','manifest_sha256'} and path.name==value['id'],
                        'generation directory identity changed')
                require(record(path/'generation-manifest.json')['sha256']==value['manifest_sha256'],
                        'generation directory manifest changed')
            files=[record(path/relative)for relative in sorted(inventory)]
            require(static.publication._tree_hash(path)==(digest,inventory),'prefix directory changed during enumeration')
            directories.append(dict(kind=row['kind'],name=name,binding=value,tree_sha256=digest,files=files))
            directory_files.extend(files);del bindings[name]
    return evidence_records([clean,directory_files]),directories


def validate(binding, inputs, *, check_build=True):
    p = read(binding)
    require(set(p)-{'source_derivative'} == KEYS and p['schema_version'] == SCHEMA and p['implementation'] == record(__file__),
            'closed selected disclosure round required')
    require(p['inputs'] == inputs, 'round belongs to different review inputs')
    require(type(p['round']) is int and type(p['limit']) is int and 1 <= p['round'] <= p['limit'],
            'finite cumulative review round required')
    scope = semantic_scope(inputs)
    reservation = validate_reservation(p['reservation'], scope, p['round'], p['limit'])
    require(reservation['allowance'] == p['allowance'] and reservation['prior_records'] == p['prior_rejections'],
            'round lost prior consumption or authorization')
    policy = A.validate(p['allowance'], expected_scope=A.scope(inputs['workbook'], inputs['original_source']['sha256']), at=p['created_at_ns'])
    require(policy['limits']['transport_retries'] == 0, 'no automatic transport replay is permitted')
    require(p['producer_run'] == inputs['producer_run'], 'round producer changed')
    for name in ('full_body','projection','manifest','instruction','disclosure','golden','delivered','original_source','initial'):
        life.checked_record(inputs[name])
    projected=read(inputs['projection'])
    if 'empty_review_contract'in projected:
        require(read(projected['empty_review_contract'])['build_result']==p['build']['result'],
                'empty review contract detached from reserved build')
    prefix=read(p['prefix'])
    require([v['kind']for v in prefix]==['source','segmentation','research','normalization_phase1','source_profile','normalization_phase2','environment']
            and all(v['result']['passed']is True for v in prefix),'retained full source prefix differs')
    actual_files,actual_directories=prefix_records(prefix)
    require(actual_directories==p['prefix_directories'] and all(row in p['prefix_evidence']for row in actual_files),
            'retained prefix file or directory inventory differs')
    for evidence in p['prefix_evidence']:life.checked_record(evidence)
    if check_build:
        from scripts.batch import harbor_instruction_worker as W
        worker, selection, _ = completed_build(p['build'])
        require(str(worker.run) == inputs['worker_run'] and record(worker.run/'initial.json') == inputs['initial'],
                'round detached from original completed worker')
        reviewed_content(p.get('source_derivative'),inputs,worker,selection,p['build'],p['prior_rejections'])
        require(Path(inputs['producer_run']) == W.task(worker,selection,record(worker.context.tracker_root/(inputs['workbook']+'.json'))).run,
                'review producer differs from retained worker')
        _primary_history(worker.campaign,scope,p['round'],p['prior_rejections'])
    directory = Path(inputs['worker_run'])/'disclosure-review-rounds'/('%04d'%p['round'])
    require(Path(binding['path']) == directory/'authority.json', 'round authority moved or duplicated')
    return p


def _primary_history(campaign,scope,ordinal,prior):
    primary=Path(campaign.root)/E.sha(E.canonical(scope))
    if ordinal==1:
        require(not primary.exists(),'first primary review has executor history; retain it explicitly')
    else:
        require(isinstance(prior,list) and len(prior)==ordinal-1,'complete prior round history required')
        direct=[]
        for index,row in enumerate(prior,1):
            if read(row).get('schema_version')=='harbor-completed-disclosure-partition-rejection/v1':
                require(closed_rejection(row,scope,index) is True,'completed partition history differs')
            elif read(row).get('schema_version')=='harbor-completed-disclosure-review-rejection/v1':
                require(closed_rejection(row,scope,index) is True,'completed original review differs')
                direct.append((index,read(row)['outcome']))
            else:direct.append((index,row))
        require(primary.is_dir() and {x.name for x in primary.iterdir()} == {'scope.lock',*('attempt-%02d'%i for i,_ in direct)},
                'unlisted or uncertain primary review history remains')
        require(all(row==record(primary/('attempt-%02d'%i)/'outcome.json')for i,row in direct),
                'prior consumption detached from original campaign')


def reviewed_content(derivative,inputs,worker,selection,build,prior_rejections):
    if derivative is None:
        require(record(selection.content.stage/'instruction.md')['sha256']==inputs['instruction']['sha256']
                and record(selection.content.stage/'tests/disclosure.json')['sha256']==inputs['disclosure']['sha256'],
                'completed disclosure differs from review')
        return None
    value=read(derivative);module=bound_module(value['implementation'])
    require(module.SCHEMA=='harbor-disclosure-partition-source-derivative/v1',
            'unknown source derivative reader')
    return module.validate(derivative,worker=worker,selection=selection,build=build,
                           prior_rejections=prior_rejections,inputs=inputs)


def reserve(inputs, *, worker, selection, lease, environment, allowance, build,
            prior_rejections, ordinal, limit, clock=time.time_ns, source_derivative=None):
    """Reserve a distinct review after full live custody/source/build validation.

    The caller holds the original book/process guards. This method makes no model
    request; an incomplete reservation remains consumed and blocks a second one.
    """
    from scripts.batch import harbor_disclosure_partition_review as R
    from scripts.batch import harbor_disclosure_partition_evidence as projection
    W = environment[3]
    from scripts.batch.harbor_instruction_repair import prefix as validate_prefix
    require(set(inputs) == R.PLAN_KEYS|{'producer_run'}, 'closed explicit-round review inputs required')
    require(type(ordinal) is int and type(limit) is int and 1 <= ordinal <= limit,
            'finite selected round and ceiling required')
    require(isinstance(prior_rejections,list) and len(prior_rejections) == ordinal-1,
            'every consumed review must be retained')
    completed_build(build, worker=worker, selection=selection)
    _, before, tracker = W.active(worker, selection, lease)
    require(inputs['workbook'] == before['workbook_id'] and inputs['original_source'] == before['bindings']['original_raw_source']
            and inputs['worker_run'] == str(worker.run) and inputs['initial'] == record(worker.run/'initial.json'),
            'review source or current worker changed')
    require(inputs['policy'] == asdict(selection.content.model_policy), 'selected review policy differs')
    require(Path(inputs['producer_run']) == W.task(worker,selection,tracker).run,
            'review producer differs from original owning task context')
    reviewed_content(source_derivative,inputs,worker,selection,build,prior_rejections)
    import xl_mcp_oracle as oracle
    oracle_binding=record(Path(oracle.__file__))
    prefix=validate_prefix(before, selection, worker.context, stop='disclosure')
    require(record(Path(oracle.__file__))==oracle_binding,'prefix oracle changed during validation')
    prefix_evidence,prefix_directories=prefix_records(prefix)
    if oracle_binding not in prefix_evidence:prefix_evidence.append(oracle_binding)
    body = R.full_body(inputs['manifest'],inputs['instruction'],inputs['disclosure'],inputs['golden'],inputs['delivered'],root=worker.root)
    require(body == read(inputs['full_body']), 'complete current source body differs')
    projected=read(inputs['projection'])
    if 'empty_review_contract'in projected:
        require(read(projected['empty_review_contract'])['build_result']==build['result'],
                'empty review contract requires the actual guarded build')
    projection.validate_plan(read(inputs['projection']),body,inputs['golden']['path'],inputs['delivered']['path'],root=worker.root)
    scope = semantic_scope(inputs)
    for i, old in enumerate(prior_rejections,1): closed_rejection(old,scope,i)
    # A new first round cannot sidestep an existing primary executor journal.
    _primary_history(worker.campaign,scope,ordinal,prior_rejections)
    now=clock();directory=worker.run/'disclosure-review-rounds'/('%04d'%ordinal)
    require(not directory.exists(), 'round already prepared; no replay')
    ledger=Path(allowance['path']).parent/'review-reservations'
    ledger.mkdir(exist_ok=True)
    if ordinal == 1: reservation=_first_reservation(allowance,scope,limit=limit,now=now)
    else:
        reservation=A.reserve_review(ledger,allowance=allowance,
            semantic_scope=scope,prior_records=prior_rejections,original_limit=1,expected_ordinal=ordinal,
            cumulative_limit=limit,prove_closed=closed_rejection,clock=clock)
    directory.mkdir(parents=True)
    prefix_binding=A.retain(directory/'prefix.json',prefix)
    _, latest, _=W.active(worker,selection,lease);require(latest == before, 'custody changed during reservation')
    authority=A.retain(directory/'authority.json',dict(schema_version=SCHEMA,implementation=record(__file__),
        allowance=allowance,reservation=reservation,round=ordinal,limit=limit,prior_rejections=prior_rejections,
        build=build,inputs=inputs,producer_run=inputs['producer_run'],created_at_ns=now,
        prefix=prefix_binding,prefix_evidence=prefix_evidence,prefix_directories=prefix_directories,
        **({'source_derivative':source_derivative}if source_derivative is not None else {})))
    validate(authority, inputs)
    return A.retain(directory/'review-plan.json',{**inputs,'review_round':authority})
