"""Explicit code successor and bounded dialogue workflow authority.

Historical code is checked as data. Real shard Calls retain their identities;
the global round reservation never resets a writer or reviewer budget.
"""
from pathlib import Path
from dataclasses import asdict, replace
import json
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_tracker_lifecycle import checked_record

SCHEMA = 'harbor-dialogue-partition-continuation/v1'
SELF = 'scripts/batch/harbor_dialogue_partition_continuation.py'
ALLOWED = {SELF, 'scripts/batch/harbor_dialogue_partition_plan.py',
    'scripts/batch/harbor_dialogue_partition_review.py',
    'scripts/batch/harbor_instruction_worker.py',
    'scripts/batch/harbor_instruction_lease_renewal.py',
    'scripts/batch/harbor_disclosure_partition_continuation.py',
    'scripts/batch/harbor_instruction_local_command.py',
    'scripts/batch/harbor_complete_readiness.py',
    'scripts/batch/harbor_transition_codec.py',
    'scripts/batch/harbor_grouped_plain_qualification.py',
    'scripts/harbor_instruction_campaign.py'}
PERMIT_KEYS = {'schema_version','worker_run','root','implementation','old_snapshot',
    'old_root','old_files','current_files','initial','workbook','original_source','source',
    'disclosure_permit','predecessor','selected_ready','global_limit','campaign','context',
    'selection','lease','tracker','failed_step','failed_step_files','prefix','evidence'}


def marker(worker):
    return worker.run / 'dialogue-partition-continuation.json'


def exists(worker):
    return marker(worker).is_file()


def home(worker):
    return worker.run / 'dialogue-partition'


def profile(old_snapshot, root):
    from scripts.batch.harbor_instruction_local_restart import snapshot
    from scripts.batch.harbor_disclosure_partition_continuation import inventory
    old, files = snapshot(old_snapshot)
    now = inventory(root)
    require(set(files) <= set(now), 'dialogue successor removed historical code')
    require({k for k, v in now.items() if files.get(k) != v} <= ALLOWED,
            'dialogue successor changed unrelated code')
    require(record(Path(root) / SELF) == record(__file__),
            'dialogue authority differs from installed code')
    return old, files, now


def permit(worker):
    """Nonrecursive current-code anchor, also usable by historical readers."""
    path = marker(worker)
    require(path.is_file() and not path.is_symlink(), 'dialogue marker missing or unsafe')
    m = read(record(path))
    require(set(m) == {'schema_version', 'permit'} and m['schema_version'] == SCHEMA,
            'closed dialogue marker required')
    b = m['permit']; p = read(b)
    require(set(p) == PERMIT_KEYS and b == record(home(worker) / 'permit.json') and p['schema_version'] == SCHEMA,
            'foreign dialogue permit')
    require(p['worker_run'] == str(worker.run) and p['root'] == str(worker.root)
            and p['implementation'] == record(__file__), 'dialogue identity or code changed')
    require(p['initial'] == record(worker.run / 'initial.json'), 'dialogue initial changed')
    old, files, now = profile(p['old_snapshot'], worker.root)
    require(p['old_root'] == str(old) and p['old_files'] == files
            and p['current_files'] == now, 'dialogue frozen inventory changed')
    require(p['global_limit'] == 2, 'dialogue round ceiling changed')
    from scripts.batch import harbor_transition_codec as K
    selected = K.decode(p['selection']).content
    require(selected.workbook == p['workbook']
            and selected.bindings['original_raw_source'] == p['original_source']
            and selected.bindings['source_file'] == p['source'], 'dialogue selected source changed')
    for item in p['evidence']:
        checked_record(item)
    from scripts import harbor_instruction_campaign as C
    encoded = C.worker_encode(worker)
    require(encoded['campaign'] == p['campaign'] and encoded['context'] == p['context'],
            'dialogue worker scope changed')
    return b, p


def validate_disclosure_successor(worker, prior):
    """Authenticate the complete previous partition implementation as bytes."""
    _, p = permit(worker)
    require(read(p['disclosure_permit']) == prior, 'dialogue detached disclosure authority')
    require(prior['root'] == p['old_root'] and prior['current_files'] == p['old_files'],
            'dialogue prior partition inventory changed')
    name = 'scripts/batch/harbor_disclosure_partition_continuation.py'
    require(prior['implementation'] == record(Path(p['old_root']) / name),
            'dialogue historical disclosure implementation changed')
    return p


def validate(worker, before=None):
    _, p = permit(worker)
    from scripts.batch import harbor_disclosure_partition_continuation as P
    prior = P.validate(worker, before)
    require(read(p['disclosure_permit']) == prior, 'dialogue disclosure link changed')
    if before is not None:
        require(before['workbook_id'] == p['workbook']
                and before['bindings']['original_raw_source'] == p['original_source']
                and before['bindings']['source_file'] == p['source'],
                'dialogue original or selected source changed')
    return p


def authorize(worker, selection, lease, *, old_snapshot, ready, failed_operation, dry_run=False):
    """Repair only a model-free aggregate consume after all shards sealed."""
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_transition_codec as K
    from scripts.batch import harbor_disclosure_partition_review as R
    from scripts.batch import harbor_instruction_local_command as M
    from scripts.batch import harbor_tracker_lifecycle as life
    from scripts.batch.harbor_nat_retry import _renewal_chain
    from scripts.batch.harbor_instruction_repair import prefix
    from scripts.batch.harbor_instruction_local_restart import exclusive, tree
    require(not exists(worker) and not home(worker).exists(),
            'dialogue continuation already reserved; reconcile retained evidence')
    old, files, now = profile(old_snapshot, worker.root)
    r = read(ready); descriptor = read(r['descriptor'])
    require(r['status'] == 'RETRY_READY' and r['next_step'] == 'disclosure-review'
            and r['selection'] == K.encode(selection) and C.base.decode(r['lease']) == lease,
            'exact final partition ready boundary required')
    old_worker = C.worker_decode(descriptor['worker'])
    require(old_worker.root == old and
            {**C.worker_encode(worker), 'root': str(old)} == C.worker_encode(old_worker),
            'dialogue upgrade changed worker continuations or scope')
    result = read(failed_operation); directory = Path(failed_operation['path']).parent
    intent_binding = record(directory / 'intent.json'); intent = read(intent_binding)
    require(result['status'] != 'COMPLETED_OPERATION'
            and intent['action'] == 'step' and intent['arguments'] ==
            {'descriptor': r['descriptor'], 'checkpoint': ready}
            and all(intent[k] is False for k in ('models', 'commands', 'containers')),
            'only the exact model-free consume failure can be repaired here')
    resource_binding = record(directory / 'resource.json'); resource = read(resource_binding)
    require(resource['returncode'] not in (None, 0) and resource['failure'] is None,
            'uncertain guard needs separate reconciliation')
    require(intent['code'] == record(old / 'scripts/harbor_instruction_campaign.py'),
            'failed consumer implementation differs')
    prior_marker = record(worker.run / 'disclosure-partition-continuation.json')
    prior_binding = read(prior_marker)['permit']; prior = read(prior_binding)
    require(prior['root'] == str(old) and prior['current_files'] == files
            and prior['implementation'] == record(old / 'scripts/batch/harbor_disclosure_partition_continuation.py'),
            'complete historical disclosure code required')
    completion = read(r['checkpoint'])
    require(completion['permit'] == prior_binding and completion['phase'] == 'disclosure-review'
            and completion['selection'] == r['selection'] and completion['lease'] == asdict(lease),
            'ready boundary detached from completed aggregate')
    plan = read(prior['review_plan'])
    R.validate_aggregate(completion['aggregate'], plan['manifest'],
        instruction=plan['instruction'], disclosure=plan['disclosure'], golden=plan['golden'],
        delivered=plan['delivered'], policy=selection.content.model_policy, root=worker.root)
    _, before, tracker = W.active(worker, selection, lease)
    M.validate_historical(worker, before, prior['old_snapshot'])
    require(all(before['lane_state'][lane]['attempt'] == 0 for lane in
                (W.producer.DISCLOSURE, W.producer.NAT, W.producer.DIALOGUE)),
            'code-only continuation cannot reset an instruction attempt')
    require(not (worker.run / 'producer/dialogue').exists()
            and not (worker.run / 'producer/naturalization').exists(),
            'later authoring has already started')
    old_step = worker.run / 'steps/disclosure-review'
    require((old_step / 'exception.json').is_file() and not (old_step / 'complete.json').exists()
            and not (old_step / 'request.json').exists(), 'exact uncommitted aggregate consume required')
    exception = read(record(old_step / 'exception.json'))
    require(exception['type'] == 'TypeError', 'unrelated consumer failure')
    expected = "SemanticSelection.__init__() got an unexpected keyword argument 'disclosure_partition_aggregate'"
    require(exception['message_sha256'] == W.executor.sha(expected.encode()),
            'only the missing typed aggregate field is repaired')
    proof = prefix(before, selection, worker.context, stop='disclosure')
    if dry_run:
        return dict(status='DIALOGUE_PARTITION_PREFLIGHT_PASSED', old_snapshot=old_snapshot,
            ready=ready, failed_operation=failed_operation, source=before['bindings']['source_file'],
            prefix=proof, aggregate=completion['aggregate'], current_files=now,
            current_lease=asdict(lease), models_prepared=0, task_mutated=False)
    target = home(worker); target.mkdir()
    before_copy = W.snapshot(target / 'tracker-before.json', tracker)
    historical_step = tree(old_step)
    p = dict(schema_version=SCHEMA, worker_run=str(worker.run), root=str(worker.root),
        implementation=record(__file__), old_snapshot=old_snapshot, old_root=str(old),
        old_files=files, current_files=now, initial=record(worker.run / 'initial.json'),
        workbook=before['workbook_id'], original_source=before['bindings']['original_raw_source'],
        source=before['bindings']['source_file'], disclosure_permit=prior_binding,
        predecessor=r['checkpoint'], selected_ready=ready, global_limit=2,
        campaign=C.worker_encode(worker)['campaign'], context=C.worker_encode(worker)['context'],
        selection=K.encode(selection), lease=asdict(lease), tracker=before_copy,
        failed_step=str(old_step), failed_step_files=historical_step, prefix=proof,
        evidence=[old_snapshot, ready, r['descriptor'], r['checkpoint'], failed_operation,
            intent_binding, resource_binding, resource['log'], prior_marker, prior_binding,
            prior['review_plan'], completion['aggregate'], before_copy, record(old_step / 'exception.json')])
    bound = W.keep(target / 'permit.json', p)
    require(W.active(worker, selection, lease)[1] == before, 'tracker changed during authorization')
    exclusive(marker(worker), dict(schema_version=SCHEMA, permit=bound))
    validate(worker, before)
    path = Path(tracker['path'])
    renewed = life.lease_operation(path,
        authority=C.base._authorize(path, lease.owner, lease.revision, lease.token),
        operation='renew', lease_kind='task_agent', owner=lease.owner,
        scope='workbook:' + p['workbook'], token=lease.token, duration_ns=C.base.DURATION,
        expected_revision=lease.revision, expected_sha256=lease.sha256)
    current = replace(lease, revision=renewed['revision'], sha256=renewed['sha256'])
    _, after, after_binding = W.active(worker, selection, current)
    _renewal_chain(before_copy, asdict(lease), after, current)
    activation = W.keep(target / 'activation.json', dict(schema_version=SCHEMA,
        permit=bound, phase='disclosure-review', previous=r['checkpoint'],
        selection=K.encode(selection), lease=asdict(current), before=before_copy,
        after=W.snapshot(target / 'tracker-after.json', after_binding), renewal=renewed))
    return activation, current


def activation(worker):
    from scripts.batch.harbor_nat_retry import _renewal_chain
    from scripts.batch.harbor_workbook_bootstrap import Lease
    from scripts.batch.harbor_instruction_local_restart import tree
    bound, p = permit(worker); b = record(home(worker) / 'activation.json'); a = read(b)
    require(set(a) == {'schema_version','permit','phase','previous','selection','lease',
                      'before','after','renewal'}
            and a['schema_version'] == SCHEMA and a['permit'] == bound
            and a['phase'] == 'disclosure-review' and a['previous'] == p['predecessor']
            and a['selection'] == p['selection'] and a['before'] == p['tracker'],
            'dialogue activation changed its exact boundary')
    require(tree(p['failed_step']) == p['failed_step_files'], 'failed consume history changed')
    _renewal_chain(a['before'], p['lease'], read(a['after']), Lease(**a['lease']))
    require(a['lease']['revision'] == p['lease']['revision'] + 1
            and a['renewal']['revision'] == a['lease']['revision']
            and a['renewal']['sha256'] == a['lease']['sha256'], 'activation renewal changed')
    return b, a


def disclosure_recovery(worker, selection, lease):
    from scripts.batch import harbor_transition_codec as K
    _, a = activation(worker)
    require(a['selection'] == K.encode(selection) and a['lease'] == asdict(lease),
            'aggregate recovery selection or lease changed')
    return True


def begin_round(worker, selection, lease, checkpoint):
    """Reserve the first global dialogue round after canonical initialization."""
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_transition_codec as K
    from scripts.batch import harbor_dialogue_partition_plan as D
    from scripts.batch import harbor_dialogue_partition_review as R
    _, before, tracker = W.active(worker, selection, lease)
    p = validate(worker, before); saved = read(checkpoint)
    require(checkpoint == record(W.step_directory(worker, 'dialogue-init') / 'complete.json')
            and saved['status'] == 'completed' and saved['step'] == 'dialogue-init'
            and saved['selection'] == K.encode(selection) and saved['lease'] == asdict(lease),
            'exact completed dialogue initialization required')
    require(before['lane_state'][W.producer.DIALOGUE]['attempt'] == 0,
            'first partition round cannot reset a dialogue attempt')
    directory = home(worker) / 'round-01'
    require(not directory.exists(), 'dialogue round already reserved; no replay')
    task = W.task(worker, selection, tracker)
    require(not list((task.run / 'requests').glob('dialogue*')),
            'ordinary dialogue work already prepared')
    source_dir, claims, template, slots = W.producer._dialogue(task, 1, worker.root)
    projection = D.build(claims, root=worker.root)
    require(0 < len(projection['partitions']) <= 128, 'unbounded dialogue plan')
    directory.mkdir()
    plan = W.keep(directory / 'plan.json', dict(claims=claims, projection=projection))
    s = selection.content
    authority = dict(schema_version=R.AUTHORITY, plan=plan, global_round=1, global_limit=2,
        workbook=p['workbook'], original_source=p['original_source'], policy=asdict(s.model_policy),
        initial=p['initial'], previous_round=None, continuation=record(home(worker) / 'permit.json'),
        source=p['source'], answer_key=record(s.stage / 'tests/answer_key.json'),
        instruction=record(s.pre_dialogue_witness.stage / 'instruction.md'),
        disclosure=record(s.stage / 'tests/disclosure.json'), claims=s.dialogue_claims,
        scope_evidence=s.scope_evidence, scope_receipt=s.dialogue_scope_receipt,
        task_dir=str(s.stage), pre_dialogue_stage=str(s.pre_dialogue_witness.stage),
        predecessor=checkpoint, selection=K.encode(selection), lease=asdict(lease),
        tracker=W.snapshot(directory / 'tracker-before.json', tracker), implementation=record(__file__))
    bound = W.keep(directory / 'authority.json', authority)
    R.selected(bound, s.model_policy, root=worker.root)
    return bound


def round_context(worker):
    from scripts.batch import harbor_dialogue_partition_review as R
    from scripts.batch import harbor_transition_codec as K
    p = validate(worker); binding = record(home(worker) / 'round-01/authority.json')
    a = read(binding); selection = K.decode(a['selection'])
    require(a['continuation'] == record(home(worker) / 'permit.json')
            and a['implementation'] == record(__file__) and a['global_round'] == 1
            and a['global_limit'] == p['global_limit'] == 2 and a['previous_round'] is None,
            'dialogue first-round reservation changed')
    require(a['workbook'] == p['workbook'] and a['original_source'] == p['original_source']
            and a['source'] == p['source'] and a['initial'] == p['initial'],
            'dialogue round detached from current worker')
    R.selected(binding, selection.content.model_policy, root=worker.root)
    return binding, a, selection


def calls(worker):
    """Return a fully validated sealed prefix; never replay an unsealed call."""
    from scripts.batch import harbor_dialogue_partition_review as R
    from scripts.batch.harbor_model_review_provenance import Call, require_independent
    binding, a, selection = round_context(worker); policy = selection.content.model_policy
    validated = R.selected(binding, policy, root=worker.root); plan = validated[1]
    count = len(plan['projection']['partitions']); result = {}; proofs = []
    previous_complete = True
    for kind in ('writer', 'paraphraser', 'review'):
        directory = home(worker) / 'round-01' / kind
        names = sorted(x.name for x in directory.iterdir()) if directory.exists() else []
        require(names == ['%04d' % i for i in range(len(names))] and len(names) <= count,
                'dialogue call directories are not a contiguous bounded prefix')
        require(previous_complete or not names, 'later dialogue role started before complete prior role')
        full = _checked_draft(worker, plan, result['paraphraser'], selection) if kind == 'review' and names else None
        values = []
        for index, name in enumerate(names):
            b = record(directory / name / 'complete.json'); value = read(b)
            require(value['authority'] == binding and value['kind'] == kind and value['index'] == index,
                    'dialogue call completion identity changed')
            call = Call(**value['call'])
            if kind == 'review':
                checked = R.validate_review(call, binding, index, policy, full,
                    root=worker.root, _validated=validated)
            else:
                incoming = result['writer'][index]['checked']['filled'] if kind == 'paraphraser' else None
                checked = R.validate_author(call, binding, index, kind, policy,
                    root=worker.root, incoming=incoming, _validated=validated)
            require(checked == value['checked'], 'dialogue sealed validation differs')
            require_independent(checked['provenance'], proofs); proofs.append(checked['provenance'])
            values.append(dict(call=call, checked=checked, completion=b))
        result[kind] = values; previous_complete = previous_complete and len(values) == count
    return binding, a, selection, plan, result


def _checked_draft(worker, plan, paraphrasers, selection):
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_dialogue_partition_plan as D
    require(len(paraphrasers) == len(plan['projection']['partitions']), 'draft lacks actual author coverage')
    composer = W.producer.skill_module(worker.root,'additional-assumptions-dialogue','compose_draft')
    full = composer.strip_slot_scaffold(D.assemble(plan['projection'],
        [v['checked']['slots'] for v in paraphrasers], root=worker.root))
    target = home(worker) / 'round-01'
    require(W.executor.checked_bytes(record(target / 'draft.md')) == full.encode(),
            'review draft differs from actual paraphraser assembly')
    validator = W.producer.skill_module(worker.root,'additional-assumptions-dialogue','validate_dialogue')
    report = validator.check_draft(full,plan['claims'],selection.content.stage,source_workbook=selection.content.bindings['source_file'])
    require(report['passed'] is True and read(record(target / 'draft-mechanics.json')) == report,
            'complete dialogue mechanics require repair before reviewing')
    return full


def execute_next(worker, lease, *, credential, directory):
    """One real call inside root's workbook/process/model guards."""
    import time, uuid
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_dialogue_partition_review as R
    from scripts.batch import harbor_tracker_lifecycle as life
    from scripts.batch.harbor_nat_retry import _renewal_chain
    binding, a, selection, plan, completed = calls(worker)
    n = len(plan['projection']['partitions'])
    kind = next((k for k in ('writer','paraphraser','review') if len(completed[k]) < n), None)
    require(kind is not None, 'all dialogue calls already completed')
    full = _checked_draft(worker,plan,completed['paraphraser'],selection) if kind == 'review' else None
    index = len(completed[kind]); target = home(worker) / 'round-01' / kind / ('%04d' % index)
    require(not target.exists(), 'dialogue call already prepared; reconcile without replay')
    _, before, tracker = W.active(worker, selection, lease)
    _renewal_chain(a['tracker'], a['lease'], before, lease)
    before_binding = W.snapshot(Path(directory) / 'lease-before.json', tracker)
    path = Path(tracker['path'])
    renewed = life.lease_operation(path,
        authority=C.base._authorize(path, lease.owner, lease.revision, lease.token),
        operation='renew', lease_kind='task_agent', owner=lease.owner,
        scope='workbook:' + a['workbook'], token=lease.token, duration_ns=C.base.DURATION,
        expected_revision=lease.revision, expected_sha256=lease.sha256)
    current = replace(lease, revision=renewed['revision'], sha256=renewed['sha256'])
    _, after, tracker = W.active(worker, selection, current)
    _renewal_chain(before_binding, asdict(lease), after, current)
    renewal = W.keep(Path(directory) / 'lease-renewal.json', dict(authority=binding,
        kind=kind, index=index, before=before_binding, after=W.snapshot(Path(directory)/'lease-after.json',tracker),
        previous=asdict(lease), lease=asdict(current), result=renewed))
    require(after['leases']['task_agent']['expires_at'] > time.time_ns() + 3330 * 10**9,
            'dialogue lease does not cover guard')
    policy = selection.content.model_policy
    if kind == 'review':
        payload = R.review_payload(a, plan, index, policy, full, root=worker.root)
    else:
        incoming = completed['writer'][index]['checked']['filled'] if kind == 'paraphraser' else None
        payload = R.author_payload(a, plan, index, kind, policy, root=worker.root, incoming=incoming)
    target.mkdir(parents=True)
    request = W.keep(target / 'request.json', payload)
    budget = tuple(W.snapshot(target / ('budget-%02d.snapshot' % i), b)
                   for i,b in enumerate((binding, a['plan'], tracker, renewal)))
    attempt = str(uuid.uuid4()); W.keep(target / 'attempt-id.json', dict(attempt_id=attempt))
    prepared = W.executor.PreparedCall(request, policy, R.scope(a,kind,index), 1, 1, attempt, budget,
        response_mode='json_object')
    W.keep(target / 'prepared.json', asdict(prepared))
    outcome = W.executor.execute(prepared, worker.campaign, credential=credential)
    require(outcome.status == 'completed' and outcome.call is not None,
            'dialogue call incomplete or uncertain; no automatic replay')
    if kind == 'review':
        require(_checked_draft(worker,plan,completed['paraphraser'],selection) == full,
                'dialogue draft changed during independent review')
        checked = R.validate_review(outcome.call,binding,index,policy,full,root=worker.root)
    else:
        checked = R.validate_author(outcome.call,binding,index,kind,policy,root=worker.root,incoming=incoming)
    result = W.keep(target / 'complete.json', dict(authority=binding,kind=kind,index=index,
        call=asdict(outcome.call),checked=checked,lease=asdict(current),renewal=renewal))
    if kind == 'paraphraser' and index + 1 == n:
        assemble_draft(worker)
    return result, current


def assemble_draft(worker):
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_dialogue_partition_plan as D
    binding, a, selection, plan, completed = calls(worker)
    n = len(plan['projection']['partitions'])
    require(len(completed['writer']) == len(completed['paraphraser']) == n,
            'cannot assemble incomplete actual author coverage')
    composer = W.producer.skill_module(worker.root, 'additional-assumptions-dialogue', 'compose_draft')
    filled = D.assemble(plan['projection'], [v['checked']['slots'] for v in completed['paraphraser']], root=worker.root)
    full = composer.strip_slot_scaffold(filled)
    draft = W.keep(home(worker) / 'round-01/draft.md', full.encode())
    validator = W.producer.skill_module(worker.root, 'additional-assumptions-dialogue', 'validate_dialogue')
    report = validator.check_draft(full, plan['claims'], selection.content.stage, source_workbook=a['source'])
    W.keep(home(worker) / 'round-01/draft-mechanics.json', report)
    require(report['passed'] is True, 'complete dialogue mechanics require repair before reviewing')
    return draft


def finalize(worker, lease):
    from scripts import harbor_instruction_campaign as C
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_dialogue_partition_review as R
    from scripts.batch import harbor_tracker_lifecycle as life
    from scripts.batch.harbor_nat_retry import _renewal_chain
    binding, a, selection, plan, completed = calls(worker)
    n = len(plan['projection']['partitions'])
    require(all(len(completed[k]) == n for k in completed), 'dialogue round lacks complete coverage')
    target = home(worker) / 'round-01'
    require(not (target / 'complete.json').exists() and not (target / 'aggregate.json').exists(),
            'dialogue aggregate already finalized; no replay')
    draft = record(target / 'draft.md')
    value = R.aggregate(binding, *[[v['call'] for v in completed[k]]
        for k in ('writer','paraphraser','review')], draft,
        selection.content.model_policy, root=worker.root, task_dir=selection.content.stage)
    aggregate = W.keep(target / 'aggregate.json', value)
    report = W.keep(target / 'review.json', value['review'])
    _, before, tracker = W.active(worker, selection, lease)
    _renewal_chain(a['tracker'], a['lease'], before, lease)
    before_binding = W.snapshot(target / 'final-lease-before.json', tracker)
    path = Path(tracker['path'])
    renewed = life.lease_operation(path,
        authority=C.base._authorize(path, lease.owner, lease.revision, lease.token),
        operation='renew', lease_kind='task_agent', owner=lease.owner,
        scope='workbook:' + a['workbook'], token=lease.token, duration_ns=C.base.DURATION,
        expected_revision=lease.revision, expected_sha256=lease.sha256)
    current = replace(lease, revision=renewed['revision'], sha256=renewed['sha256'])
    _, after, after_binding = W.active(worker, selection, current)
    _renewal_chain(before_binding, asdict(lease), after, current)
    completion = W.keep(target / 'complete.json', dict(schema_version=SCHEMA,
        authority=binding, phase='dialogue-writer', previous=a['predecessor'],
        selection=a['selection'], lease=asdict(current), aggregate=aggregate, review=report,
        before=before_binding, after=W.snapshot(target / 'final-lease-after.json', after_binding),
        previous_lease=asdict(lease), renewal=renewed, passed=value['passed']))
    return completion, current


def completion(worker):
    from scripts.batch import harbor_dialogue_partition_review as R
    from scripts.batch.harbor_nat_retry import _renewal_chain
    from scripts.batch.harbor_workbook_bootstrap import Lease
    binding, a, selection = round_context(worker)
    b = record(home(worker) / 'round-01/complete.json'); c = read(b)
    require(set(c) == {'schema_version','authority','phase','previous','selection','lease',
        'aggregate','review','before','after','previous_lease','renewal','passed'}
        and c['schema_version'] == SCHEMA and c['authority'] == binding
        and c['phase'] == 'dialogue-writer' and c['previous'] == a['predecessor']
        and c['selection'] == a['selection'], 'dialogue completion boundary changed')
    value = read(c['aggregate'])
    from scripts.batch.harbor_model_review_provenance import Call
    expected = R.aggregate(binding, *[tuple(Call(**v) for v in value[k])
        for k in ('writer_calls','paraphraser_calls','review_calls')], value['draft'],
        selection.content.model_policy, root=worker.root, task_dir=selection.content.stage)
    require(value == expected and read(c['review']) == value['review']
            and c['passed'] == value['passed'], 'dialogue aggregate result changed')
    _renewal_chain(c['before'], c['previous_lease'], read(c['after']), Lease(**c['lease']))
    _renewal_chain(a['tracker'], a['lease'], read(c['after']), Lease(**c['lease']))
    require(c['lease']['revision'] == c['previous_lease']['revision'] + 1
            and c['renewal']['revision'] == c['lease']['revision']
            and c['renewal']['sha256'] == c['lease']['sha256'], 'dialogue final renewal changed')
    return b, c


def checkpoint_predecessor(worker, binding):
    if binding == record(home(worker) / 'activation.json'):
        return activation(worker)[1]['previous']
    require(binding == record(home(worker) / 'round-01/complete.json'),
            'unsupported dialogue continuation checkpoint')
    return completion(worker)[1]['previous']


def consume(worker, selection, lease, phase):
    from scripts.batch import harbor_instruction_worker as W
    from scripts.batch import harbor_transition_codec as K
    from scripts.batch.harbor_nat_retry import _renewal_chain
    bound, c = completion(worker); a = read(c['authority'])
    require(c['passed'] is True, 'rejected dialogue round requires explicit remaining revision')
    require(phase in ('dialogue-writer','dialogue-paraphraser','dialogue-review'),
            'dialogue continuation phase changed')
    _, before, _ = W.active(worker, selection, lease)
    _renewal_chain(c['after'], c['lease'], before, lease)
    require(K.encode(selection) == c['selection'], 'dialogue input selection changed before application')
    if phase != 'dialogue-review':
        return selection, lease, None
    value = read(c['aggregate']); plan = read(a['plan']); s = selection.content
    validator = W.producer.skill_module(worker.root, 'additional-assumptions-dialogue', 'validate_dialogue')
    validator.apply(s.stage, Path(value['draft']['path']), plan['claims'], value['review'],
        require_review_pass=True, skip_smoke=True)
    selected = replace(s, dialogue_review=c['review'], dialogue_reviewed_draft=value['draft'])
    semantic = replace(selection.semantic, dialogue_call=None, dialogue_writer_calls=(),
        dialogue_reviewed_claims=s.dialogue_claims, dialogue_partition_aggregate=c['aggregate'])
    return W.refreshed(replace(selection,content=selected,semantic=semantic)), lease, None
