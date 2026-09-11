"""Root-only continuation after one mechanical or semantic NAT rejection.

No CLI, model call, financial repair or failure clearing. This one-use permit
retains the consumed attempt and revalidates the canonical prefix under current
code. Existing terminal/classified failures require their separate lifecycle.
"""
from dataclasses import asdict, replace
import copy
import fcntl
from pathlib import Path
import os

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_lane_notes as notes
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_completed_lanes import STEPS, CompletionEvidence, check_completed_gate
from scripts.batch import harbor_transition_codec as codec

SCHEMA = 'harbor-nat-writer-retry/v1'
PREFIX = ('disclosure-build', 'disclosure-review', 'disclosure-commit', 'nat-init')


def _worker():
    from scripts.batch import harbor_instruction_worker
    return harbor_instruction_worker


def _renewal_chain(original_record, original_lease, current, current_lease):
    """Accept only exact hash-chained root renewals since the blocked call."""
    raw = Path(lifecycle.checked_record(original_record)['path']).read_bytes()
    old = schema.strict_json(raw)
    require(old['revision'] == original_lease['revision'] and schema.sha(raw) == original_lease['sha256'], 'blocked tracker snapshot differs from original lease')
    require((original_lease['owner'], original_lease['token']) == (current_lease.owner, current_lease.token), 'renewal cannot replace owner or token')
    require(current['handoffs'][:len(old['handoffs'])] == old['handoffs'], 'lease history changed')
    for event in current['handoffs'][len(old['handoffs']):]:
        require(event.get('schema_version') == lifecycle.EVENT_SCHEMA and event.get('operation') == 'renew' and event.get('lease_kind') == 'task_agent', 'only root task lease renewal may follow blocked tracker')
        require(event['prior_tracker_sha256'] == schema.sha(raw) and event['revision'] == old['revision'] + 1 and event['root_actor'] == 'harbor-orchestrator', 'lease renewal hash/revision chain differs')
        prior = old['leases']['task_agent']; new = event['after']
        require(event['before'] == prior and prior['heartbeat_at'] <= event['at'] < prior['expires_at'], 'expired or substituted predecessor lease')
        require(all(new[k] == prior[k] for k in schema.LEASE_KEYS - {'heartbeat_at', 'expires_at'}) and new['heartbeat_at'] == event['at'] < new['expires_at'], 'renewal identity or chronology differs')
        old['handoffs'].append(event); old['leases']['task_agent'] = new
        old['revision'] = event['revision']; old['updated_at'] = event['at']
        raw = lifecycle.canonical(old)
    require(old == current and old['revision'] == current_lease.revision and schema.sha(raw) == current_lease.sha256, 'tracker changed beyond exact lease renewals')


def _fresh_prefix(before, selection, context):
    """Current fixed owning gates only; no cached passed flags or callbacks."""
    view = copy.deepcopy(before)
    for row in view['lane_state'].values():
        row.update(state='pending', phase=None)
    evidence = []
    for kind, lane in STEPS:
        if kind == 'naturalization':
            break
        if kind in {'source', 'segmentation'}:
            generation = selection.content.bindings[kind + '_generation']
            packet = notes.GateEvidence(kind, record(Path(generation['path']) / 'generation-manifest.json'), selection.content.normalization_records)
            result = notes.check_gate(view, packet, context)
        else:
            result = check_completed_gate(view, CompletionEvidence(kind, selection), context)
        require(result['passed'] and result.get('confidence', 'high') == 'high', 'current prefix gate failed: ' + kind)
        evidence.extend(result['evidence'])
        view['bindings'].update(result['bindings'])
        view['lane_state'][lane].update(state='running' if kind == 'normalization_phase1' else 'passed', phase=result.get('phase'))
    return notes.evidence_list(evidence)


def _prior_continuations(worker):
    result={k:getattr(worker,k) for k in ('disclosure_repair','lease_renewal','disclosure_revision') if getattr(worker,k,None) is not None}
    from scripts.batch.harbor_instruction_lease_renewal import retry_continuations
    return retry_continuations(worker,'retry',result)


def _disclosure_history(worker,before):
    revision_records=[]
    if worker.disclosure_revision is not None:
        from scripts.batch.harbor_disclosure_revision import validate
        validate(worker,before);revision_records.append(worker.disclosure_revision)
    if worker.disclosure_repair is None:return revision_records
    from scripts.batch import harbor_disclosure_review_resume as resume,harbor_disclosure_payload_repair as payload
    from scripts.batch import harbor_model_executor as executor
    from scripts.batch.harbor_complete_readiness import evidence_records
    receipt=resume.validate(worker,before)
    request=_worker().request_saved(__import__('pathlib').Path(receipt['request']['path']))
    observed=executor.inspect(request.prepared,worker.campaign)
    require(observed.status=='completed' and observed.call is not None,'downstream retry requires completed disclosure repair')
    records=[*revision_records,worker.disclosure_repair,*[receipt[k] for k in ('initial','blocked','predecessor','tracker','old_snapshot','request','original_request','original_context','physical_repair')],
             *receipt['retained_prefix'],*receipt['takeover_records'],
             *evidence_records(payload.read(receipt['physical_repair'])),observed.outcome]
    for prepared in (payload.original(receipt['physical_repair']),request.prepared):
        directory=executor.location(prepared,worker.campaign)
        records.extend(record(path) for path in sorted(directory.iterdir()) if path.is_file() and not path.name.endswith('.lock'))
    records.extend(evidence_records(observed.call))
    return records


def _failure(worker, selected, lease, blocked):
    w = _worker()
    require(worker.retry is None, 'a retry cannot authorize another retry')
    facade, before, tracker = w.active(worker, selected, lease)
    failed = read(blocked)
    phase=failed['step']
    require(phase in ('nat-writer','nat-review') and blocked == record(w.step_directory(worker,phase)/'blocked.json'), 'exact root-selected blocked NAT record required')
    semantic=phase=='nat-review'
    require(failed['status'] == 'NAT_REPAIR_REQUIRED', 'only rejected NAT is supported')
    require(failed['selection'] == codec.encode(selected), 'blocked selection changed')
    request = w.request_saved(w.step_directory(worker,'nat-writer')/'request.json')
    request_context = read(request.context)
    blocked_request=w.request_saved(w.step_directory(worker,phase)/'request.json')
    _renewal_chain(read(blocked_request.context)['budget_evidence'][0], failed['lease'], before, lease)
    require(not any(h.get('schema_version') == SCHEMA for h in before['handoffs']), 'round2 already authorized')
    require(not (w.step_directory(worker,phase)/'exception.json').exists(), 'unknown interrupted writer is not a semantic retry')
    for later_phase in w.STEPS[w.STEPS.index(phase)+1:]:
        require(not w.step_directory(worker,later_phase).exists(), 'later work already attempted')
    initial = record(worker.run / 'initial.json')
    previous = None
    prefix = []
    for prefix_phase in (*PREFIX, *(('nat-writer',) if semantic else ())):
        binding = record(w.step_directory(worker,prefix_phase)/'complete.json')
        value = read(binding)
        intent = read(value['intent'])
        require(value['status'] == 'completed' and value['step'] == prefix_phase, 'prefix completion changed')
        require(intent['initial'] == initial, 'prefix initial identity changed')
        # Pending capability records are legitimate intermediate checkpoints.
        current = intent['previous']; seen = set()
        while current != previous:
            require(current is not None and current['path'] not in seen, 'broken prefix checkpoint chain')
            seen.add(current['path']); pending = read(current)
            if worker.lease_renewal is not None and pending.get('schema_version')=='harbor-instruction-lease-renewal/v1':
                from scripts.batch.harbor_instruction_lease_renewal import checkpoint_predecessor
                current=checkpoint_predecessor(worker,current);continue
            if worker.disclosure_revision is not None and current==worker.disclosure_revision:
                current=pending['predecessor'];continue
            if worker.disclosure_repair is not None and current==worker.disclosure_repair:
                current=pending['blocked'];continue
            if worker.disclosure_repair is not None and current==read(worker.disclosure_repair)['blocked']:
                require(pending['step']=='disclosure-review' and pending['status']=='MODEL_RECONCILIATION_REQUIRED','original disclosure rejection changed')
                prior_intent=read(pending['intent']);require(prior_intent['initial']==initial,'disclosure initial changed')
                current=prior_intent['previous'];continue
            require(pending['status'] in {'COMMANDS_REQUIRED', 'MODEL_REQUIRED', 'RUNTIME_REQUIRED'}, 'unexpected prior checkpoint')
            current = read(pending['intent'])['previous']
        prefix.append(binding); previous = binding
    intent = read(failed['intent'])
    require(intent['initial'] == initial and intent['step'] == phase, 'blocked initial differs')
    current = intent['previous']; seen = set()
    while current != previous:
        require(current is not None and current['path'] not in seen, 'broken rejected-writer chain')
        seen.add(current['path']); pending = read(current)
        if worker.lease_renewal is not None and pending.get('schema_version')=='harbor-instruction-lease-renewal/v1':
            from scripts.batch.harbor_instruction_lease_renewal import checkpoint_predecessor
            current=checkpoint_predecessor(worker,current);continue
        require(pending['status'] in {'MODEL_REQUIRED'}, 'unknown writer checkpoint')
        current = read(pending['intent'])['previous']
    t = w.task(worker, selected, tracker)
    r, n, state = producer._nat(t, worker.root)
    require(state['status'] == 'retry_ready' and state['next_attempt'] == 2 and r._attempts_used(state) == 1 and state['attempt_limit'] == 2, 'exact remaining one-of-two budget required')
    require(len(state['attempts']) == 1 and bool(state.get('semantic_reviews')) == semantic and not state.get('staged_attempt'), 'only complete first rejection is supported')
    item = state['attempts'][0]
    require(item['attempt'] == 1 and item['valid'] is semantic and item.get('submission_mode') in ('frozen', 'frozen-boundary-v1'), 'first frozen writer rejection required')
    require((n / 'source.snapshot.md').read_bytes() == (selected.content.stage / 'instruction.md').read_bytes(), 'original staged instruction changed before retry')
    require(selected.content.naturalization_root == n, 'foreign NAT state selected')
    validator = r._load_validator()
    if semantic:
        validator.validate((n/'source.snapshot.md').read_bytes(),(n/'attempt-01/candidate.md').read_bytes(),r._verified_answer_key(n,state))
        rejected=read(record(n/'attempt-01/semantic-review.json'))
        require(rejected in state['semantic_reviews'] and rejected['accepted'] is False,
                'exact retained semantic rejection required')
        # Exact completed response replay verifies both reviewer provenance and
        # the owning rejection. No request or writer attempt is dispatched.
        review=w.request_saved(w.step_directory(worker,'nat-review')/'request.json')
        corrections=sorted(w.step_directory(worker,'nat-review').glob('coverage-review-*.json'))
        if corrections:review=w.request_saved(corrections[-1])
        result=producer.consume_naturalization_review(t,review,request,worker.campaign,root=worker.root)
        require(result['passed'] is False and selected.content.naturalization_call==result['call'] and
                selected.content.naturalization_request==review.prepared.request,'selected rejected reviewer provenance differs')
    else:
        try:
            validator.validate((n / 'source.snapshot.md').read_bytes(), (n / 'attempt-01/candidate.md').read_bytes(), r._verified_answer_key(n, state))
        except validator.RewriteValidationError as exc:
            require(item['reason_codes'] == exc.reason_codes, 'current mechanical failure differs')
        else:
            require(False, 'candidate no longer fails; not a remaining-round authorization')
    # Count the historical failed invocation without claiming that its producer
    # ran current code. Independently prove the exact current ordinal1 request
    # and complete executor protocol; no old code is executed or hash rewritten.
    actual_request=read(request.prepared.request)
    supported=[]
    for legacy in (True,False):
        supported.append({'model':t.policy.model,'reasoning_effort':t.policy.reasoning_effort,
            'messages':[{'role':'system','content':producer.naturalization_writer_prompt(worker.root,legacy=legacy)},
                {'role':'user','content':__import__('json').dumps({'writer_source':producer.text(record(n/'writer_source.md'))},ensure_ascii=False,allow_nan=False)}]})
    require(actual_request in supported,'original failed writer request differs from the exact supported historical/current contracts')
    require(request_context['kind'] == 'naturalization-writer' and request_context['ordinal'] == 1 and request_context['limit'] == 2 and request_context['request'] == request.prepared.request, 'historical request identity/budget changed')
    require(request_context['original'] == before['bindings']['original_raw_source'] and request_context['stage_tree'] == w.static.tree(selected.content.stage), 'historical writer source/stage differs')
    require(request_context['budget_evidence'] == list(request.prepared.budget_evidence), 'historical request budget snapshots differ')
    require(asdict(request.prepared.scope) == dict(workbook_id=selected.content.workbook,
        original_sha256=before['bindings']['original_raw_source']['sha256'],
        lane=producer.NAT, budget_id='naturalization-writer') and request.prepared.response_mode == 'json_object', 'historical writer scope changed')
    for binding in request_context['budget_evidence']:
        lifecycle.checked_record(binding)
    require(bool(request_context['code']) and all(set(item) == {'path', 'sha256'} and schema.HEX.fullmatch(item['sha256']) for item in request_context['code']), 'historical producer provenance missing')
    require(request.prepared.ordinal == 1 and request.prepared.limit == 2, 'writer budget scope differs')
    call = producer.observed(request, worker.campaign)
    response = producer.validate_call(call, read(request.prepared.request), policy=t.policy)['review']
    require(set(response) == {'preamble_body', 'input_body'}, 'writer response schema changed')
    for key, value in response.items():
        require(isinstance(value, str) and value.encode() == (n / 'attempt-01' / (key + '.md')).read_bytes(), 'rejected bodies differ from journaled model response')
    require(selected.content.naturalization_writer_call == call and selected.content.naturalization_writer_request == request.prepared.request, 'selected rejected writer provenance differs')
    require(before['lane_state'][producer.NAT].get('attempt', 0) in (None, 0, 1), 'tracker budget exceeds remaining attempt')
    return before, tracker, state, n, prefix, initial


def _archive(directory, records):
    rows = notes.evidence_list(records)
    total = 0; copies = []
    for index, old in enumerate(rows):
        lifecycle.checked_record(old)
        source = Path(old['path']); size = source.stat().st_size; total += size
        require(size <= 256 * 1024**2 and total <= 1024**3, 'retry archive exceeds bounded storage')
        target = directory / ('%05d.blob' % index)
        new = producer.put(target, source.read_bytes())
        require(new['sha256'] == old['sha256'] and target.stat().st_ino != source.stat().st_ino, 'retry archive differs or aliases original')
        target.chmod(0o444); copies.append(dict(original=old, retained=new))
    lifecycle.check_records(rows)
    return copies


def _project(before, permit_binding):
    after = copy.deepcopy(before)
    after['handoffs'].append(dict(schema_version=SCHEMA, permit=permit_binding))
    row = after['lane_state'][producer.NAT]
    row.update(state='repairing', attempt=1)
    return after


def authorize(worker, selected, lease, *, blocked, _fault=lambda _: None):
    """Root prepares an immutable retry permit and CAS records one spent round.

    An interrupted transaction is not rerun. Use recover() with the exact permit.
    No request is prepared or sent by this operation.
    """
    w = _worker()
    worker.run.mkdir(parents=True, exist_ok=True)
    with (worker.run / 'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before, tracker, state, nat, prefix, initial = _failure(worker, selected, lease, blocked)
        directory = worker.run / 'nat-retry-02'
        require(not directory.exists(), 'retry transaction already prepared; explicit reconciliation required')
        directory.mkdir()
        current_code = w.code(worker)
        producer.put(directory / 'intent.json', dict(blocked=blocked, tracker=tracker, code=current_code))
        proof = _fresh_prefix(before, selected, worker.context)
        records = proof + [tracker, blocked, initial, *prefix] + _disclosure_history(worker,before)
        if worker.lease_renewal is not None:
            from scripts.batch.harbor_instruction_lease_renewal import history
            records += history(worker,before)
        # Preserve every prior authoring byte and execution journal before state
        # mutation. The retry continues the same NAT and executor budget scopes.
        from scripts.batch.harbor_complete_readiness import evidence_records
        records += evidence_records(selected)
        for parent in (nat, worker.run / 'producer/requests', w.step_directory(worker,'nat-review')):
            for path in sorted(parent.rglob('*')):
                schema.safe_path(path)
                if path.is_file() and not path.name.endswith('.lock'):
                    records.append(record(path))
        copies = _archive(directory / 'archive', records)
        require(w.code(worker) == current_code, 'code changed during prefix revalidation')
        before_copy = next(c['retained'] for c in copies if c['original'] == tracker)
        permit = producer.put(directory / 'permit.json', dict(schema_version=SCHEMA, worker_run=str(worker.run), blocked=blocked,
            initial=initial, prefix=prefix, before=before_copy, selection=codec.encode(selected), lease=asdict(lease),
            code=current_code, rejection_phase=read(blocked)['step'], original_attempt=state['attempts'][0], prior_state=state, copies=copies,
            spent=1, remaining=1, limit=2, semantic_budget_reset=False,
            **({'prior_continuations':_prior_continuations(worker)} if _prior_continuations(worker) else {})))
        _fault('prepared')
        def mutate(after):
            require(w.code(worker) == current_code, 'code changed before retry CAS')
            fresh = _failure(worker, selected, lease, blocked)
            require(fresh[0] == before and fresh[2] == state, 'retry evidence changed before CAS')
            lifecycle.check_records([c['original'] for c in copies])
            projected = _project(before, permit)
            after['handoffs'] = projected['handoffs']; after['lane_state'] = projected['lane_state']
        changed = schema.cas_mutate(tracker['path'], expected_revision=lease.revision, expected_sha256=lease.sha256,
            owner=lease.owner, token=lease.token, scope='workbook:' + selected.content.workbook,
            mutate=mutate, allowed_fields={'handoffs', 'lane_state'}, allowed_lanes={producer.NAT}, fault_hook=_fault)
        _fault('committed')
        return _seal(worker, permit, changed['tracker'])


def _seal(worker, permit, after):
    value = read(permit); before = read(value['before'])
    expected = _project(before, permit)
    expected['revision'] += 1; expected['updated_at'] = after['updated_at']
    expected['leases']['task_agent']['heartbeat_at'] = after['updated_at']
    require(after == expected, 'retry CAS is not the exact permitted mutation')
    path = Path(permit['path']).parent
    binding = producer.put(path / 'after-tracker.json', after)
    lease = Lease(value['lease']['owner'], value['lease']['token'], after['revision'], schema.sha(lifecycle.canonical(after)))
    completion = producer.put(path / 'complete.json', dict(schema_version=SCHEMA, permit=permit, after=binding, lease=asdict(lease)))
    return replace(worker, retry=completion), codec.decode(value['selection']), lease, completion


def recover(worker, permit, lease):
    """Finish only an already committed exact retry CAS; never resubmit it."""
    w = _worker(); value = read(permit)
    require(value['worker_run'] == str(worker.run) and value['code'] == w.code(worker), 'retry worker/code changed')
    require(value['lease'] == asdict(lease), 'original root lease differs')
    with (worker.run / 'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for row in value['copies']: lifecycle.checked_record(row['retained'])
        path = worker.context.tracker_root / (codec.decode(value['selection']).content.workbook + '.json')
        after = read(record(path))
        require(after['revision'] == lease.revision + 1, 'retry CAS did not commit or tracker advanced')
        current_lease = Lease(lease.owner, lease.token, after['revision'], record(path)['sha256'])
        w.active(worker, codec.decode(value['selection']), current_lease)
        return _seal(worker, permit, after)


def validate(worker, before):
    """Read-only consumer of root-selected CAS evidence, never a PASS callback."""
    w = _worker(); completion = read(worker.retry)
    require(completion['schema_version'] == SCHEMA, 'unknown retry completion')
    value = read(completion['permit'])
    require(value['schema_version'] == SCHEMA and value['worker_run'] == str(worker.run), 'foreign retry permit')
    require(value['code'] == w.code(worker), 'retry code changed; fresh root revalidation required')
    require(value.get('prior_continuations',{})==_prior_continuations(worker),'NAT retry detached disclosure history')
    _disclosure_history(worker,before)
    if worker.lease_renewal is not None:
        from scripts.batch.harbor_instruction_lease_renewal import validate as validate_renewal
        validate_renewal(worker,before)
    for binding in [value['initial'], value['blocked'], *value['prefix']]:
        lifecycle.checked_record(binding)
    require(dict(schema_version=SCHEMA, permit=completion['permit']) in before['handoffs'], 'retry lacks tracker CAS authority')
    after = read(completion['after']); old = read(value['before'])
    expected = _project(old, completion['permit'])
    expected['revision'] += 1; expected['updated_at'] = after['updated_at']
    expected['leases']['task_agent']['heartbeat_at'] = after['updated_at']
    require(after == expected and before['revision'] >= after['revision'], 'retry tracker projection changed')
    require(completion['lease'] == asdict(Lease(value['lease']['owner'], value['lease']['token'], after['revision'], schema.sha(lifecycle.canonical(after)))), 'retry lease evidence changed')
    for row in value['copies']:
        lifecycle.checked_record(row['retained'])
        require(row['original']['sha256'] == row['retained']['sha256'], 'archived evidence changed')
    n = worker.run / 'producer/naturalization'
    r = producer.skill_module(worker.root, 'naturalize-finance-task-instruction', 'naturalize_recovery')
    state = r._read_state(n); r._verified_snapshot(n, state); r._verified_budget(n, state); r._verify_attempt_evidence(n, state)
    require(state['attempts'] and state['attempts'][0] == value['original_attempt'] and 1 <= r._attempts_used(state) <= 2, 'original writer attempt or cumulative budget changed')
    require(state['source_sha256'] == value['prior_state']['source_sha256'] and state['attempt_limit'] == 2 and not state.get('budget_inheritance'), 'NAT source or budget scope changed')
    prior_reviews=value['prior_state'].get('semantic_reviews',[])
    require(state.get('semantic_reviews',[])[:len(prior_reviews)]==prior_reviews,'prior semantic reviews changed')
    require(before['lane_state'][producer.NAT].get('attempt', 0) >= 1, 'tracker lost consumed writer attempt')
    return {**value, 'lease': completion['lease']}
