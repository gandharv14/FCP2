"""Root CAS permit for exactly one completed failed dialogue review -> round two.

The old calls and continuation transactions remain immutable. Current owning
prefix/runtime gates run before CAS. No model call, reviewer override, grouping
change, structural correction or extra semantic budget is authorized here.
"""
from dataclasses import asdict, replace
from pathlib import Path
import copy
import fcntl
import json
from scripts.batch import harbor_tracker as schema, harbor_tracker_lifecycle as life
from scripts.batch import harbor_instruction_producer as producer
from scripts.batch import harbor_dialogue_retry as structural
from scripts.batch import harbor_transition_codec as codec
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_workbook_bootstrap import Lease
from scripts.batch.harbor_model_review_provenance import Call, validate_call, require_independent

SCHEMA = 'harbor-dialogue-semantic-retry/v1'


def _worker():
    from scripts.batch import harbor_instruction_worker
    return harbor_instruction_worker


def _continuations(worker):
    result = {name: getattr(worker, name) for name in ('retry', 'dialogue_retry', 'boundary_recovery')}
    if worker.code_repair is not None:
        result['code_repair'] = worker.code_repair
    if worker.disclosure_repair is not None:
        result['disclosure_repair']=worker.disclosure_repair
    if worker.lease_renewal is not None:result['lease_renewal']=worker.lease_renewal
    if worker.disclosure_revision is not None:result['disclosure_revision']=worker.disclosure_revision
    if worker.dialogue_pre_review_repair is not None:result['dialogue_pre_review_repair']=worker.dialogue_pre_review_repair
    from scripts.batch.harbor_instruction_lease_renewal import retry_continuations
    return retry_continuations(worker,'dialogue_semantic_retry',result)


def _history(worker, before):
    """Validate exact prior root CAS projections without executing old code."""
    records = []
    if worker.dialogue_pre_review_repair is not None:
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        q=Q.validate_historical_integrity(worker,before,root=worker.root)
        records += [worker.dialogue_pre_review_repair,q['reservation'],q['blocked'],q['old_snapshot'],q['corrected_draft'],*q['history']]
    if worker.lease_renewal is not None:
        from scripts.batch.harbor_instruction_lease_renewal import history
        records.extend(history(worker,before))
    modules = [('retry', 'harbor_nat_retry'), ('dialogue_retry', 'harbor_dialogue_retry'),
               ('boundary_recovery', 'harbor_nat_boundary_resume')]
    import importlib
    for field, name in modules:
        binding = getattr(worker, field)
        if binding is None:
            continue
        module = importlib.import_module('scripts.batch.' + name)
        complete = read(binding)
        permit = read(complete['permit'])
        old, after = read(permit['before']), read(complete['after'])
        require(complete['schema_version'] == permit['schema_version'] == module.SCHEMA,
                'unknown previous continuation')
        expected = module._project(old, complete['permit'])
        expected['revision'] += 1
        expected['updated_at'] = after['updated_at']
        expected['leases']['task_agent']['heartbeat_at'] = after['updated_at']
        require(expected == after and {'schema_version': module.SCHEMA, 'permit': complete['permit']} in before['handoffs'],
                'previous continuation CAS changed')
        require(complete['lease'] == asdict(Lease(permit['lease']['owner'], permit['lease']['token'],
                after['revision'], schema.sha(life.canonical(after)))), 'previous continuation lease changed')
        require(permit['worker_run'] == str(worker.run), 'previous continuation belongs to another worker')
        records.extend([binding, complete['permit'], permit['before'], complete['after'], permit['initial']])
        for row in permit['copies']:
            life.checked_record(row['retained'])
            require(row['original']['sha256'] == row['retained']['sha256'], 'previous continuation archive changed')
            records.append(row['retained'])
    if worker.code_repair is not None:
        from scripts.batch import harbor_disclosure_command_repair as command_repair
        if read(worker.code_repair).get('schema_version')==command_repair.SCHEMA:
            # A deterministic disclosure suffix is a different recorded repair
            # type. Its original validator owns source, full snapshots, command
            # history and custody; never reinterpret it as an extraction repair.
            permit=command_repair.validate(worker,before)
            from scripts.batch.harbor_complete_readiness import evidence_records
            records.extend([worker.code_repair,*(permit[k]for k in ('profile','initial','original','tracker')),
                            *evidence_records(read(permit['profile']))])
        else:
            from scripts.batch.harbor_instruction_repair import validate_historical_integrity
            _, retained = validate_historical_integrity(worker, before)
            records.extend(retained)
    from scripts.batch.harbor_nat_retry import _disclosure_history
    if worker.dialogue_pre_review_repair is not None:
        require(worker.disclosure_repair is None,'unsupported physical disclosure before pre-review correction')
        Q.historical_manifest(worker,before);records.append(worker.disclosure_revision)
    else:records.extend(_disclosure_history(worker,before))
    return records


def _call_records(call):
    return [getattr(call, name) for name in ('request', 'submitted', 'response', 'completed', 'parsed')
            if getattr(call, name) is not None]


def _failure(worker, selected, lease, blocked, takeover=None):
    w = _worker()
    require(worker.dialogue_semantic_retry is None, 'semantic round two already selected')
    _, before, tracker = w.active(worker, selected, lease)
    require(not any(h.get('schema_version') == SCHEMA for h in before['handoffs']),
            'semantic continuation already authorized')
    require(int(before['lane_state'][producer.DIALOGUE].get('attempt') or 0) <= 1,
            'no remaining semantic round')
    step = w.step_directory(worker, 'dialogue-review')
    require(blocked == record(step/'blocked.json'), 'exact first review failure required')
    failure = read(blocked)
    require(failure['status'] == 'DIALOGUE_REPAIR_REQUIRED' and failure['step'] == 'dialogue-review',
            'only a completed failed semantic review permits round two')
    require(not (step/'complete.json').exists() and not (step/'exception.json').exists(),
            'completed or uncertain review cannot be retried')
    from scripts.batch.harbor_continuation_audit import validate_selection
    old_selection = codec.decode(failure['selection'])
    if old_selection.content.dialogue_scope_receipt != selected.content.dialogue_scope_receipt:
        from scripts.batch.harbor_dialogue_scope_context import validate_equivalence
        validate_equivalence(selected.content,root=worker.root)
        scope_proof=read(selected.content.dialogue_scope_receipt)
        require(scope_proof.get('replaces',scope_proof['previous'])==old_selection.content.dialogue_scope_receipt,
                'semantic retry scope equivalence detached from original')
        old_selection=replace(old_selection,content=replace(old_selection.content,
            dialogue_scope_receipt=selected.content.dialogue_scope_receipt))
    validate_selection(replace(old_selection, runtime=selected.runtime, observation=selected.observation), selected)
    request = w.request_saved(step/'request.json')
    context = read(request.context)
    from scripts.batch.harbor_lease_takeover import continuation_chain
    continuation_chain(context['budget_evidence'][0], failure['lease'], before, lease,
                       takeover=takeover, tracker_path=tracker['path'])
    task = w.task(worker, selected, tracker)
    if worker.dialogue_pre_review_repair is not None:
        from scripts.batch import harbor_dialogue_pre_review_repair as Q
        directory,claims,template,slots=Q.historical_context(worker,selected,tracker)
    else:directory, claims, template, slots = producer._dialogue(task, 1, worker.root)
    require(not (directory/'round-02').exists(), 'second-round evidence already exists')
    for name in w.STEPS[w.STEPS.index('dialogue-review')+1:]:
        require(not (worker.run/'steps'/name).exists(), 'later step already attempted')
    require(request.prepared.ordinal == context['ordinal'] == 1 and request.prepared.limit == context['limit'] == 2,
            'first semantic call budget differs')
    require(context['kind'] == 'dialogue-review' and context['request'] == request.prepared.request
            and context['stage_tree'] == task.stage_tree
            and context['workbook'] == before['workbook_id']
            and context['original'] == before['bindings']['original_raw_source']
            and context['budget_evidence'] == list(request.prepared.budget_evidence),
            'first semantic request provenance changed')
    call = producer.observed(request, worker.campaign)
    payload = read(call.request)
    body = {'claims.json': claims, 'draft.md': producer.text(record(directory/'round-01/draft.md')), 'round': 1}
    prompt=producer.skill_prompt(worker.root,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
    if worker.dialogue_pre_review_repair is not None:
        body['source_proved_correction']=Q.review_projection(worker.dialogue_pre_review_repair,claims,body['draft.md'],root=worker.root)
        prompt+=Q.REVIEW_GUIDANCE;Q.validate_review_call(worker.dialogue_pre_review_repair,call)
    require(json.loads(payload['messages'][1]['content']) == body, 'first review claims or draft changed')
    require(payload['messages'][0]['content'] == prompt,
            'unsupported historical reviewer prompt')
    submitted = read(call.submitted)
    require(submitted['scope'] == {'workbook_id': before['workbook_id'],
            'original_sha256': before['bindings']['original_raw_source']['sha256'],
            'lane': producer.DIALOGUE, 'budget_id': 'dialogue-review'}
            and submitted['semantic_ordinal'] == 1 and submitted['semantic_limit'] == 2,
            'original reviewer scope changed')
    checked = validate_call(call, payload, policy=task.policy)
    authors = tuple(Call(**read(record(directory/(kind+'-call-01.json')))) for kind in ('writer','paraphraser'))
    from scripts.batch.harbor_dialogue_authoring import verify_pair
    if worker.dialogue_pre_review_repair is not None:
        independent=Q.verify_pair(worker.dialogue_pre_review_repair,authors,policy=task.policy,claims=claims,draft=body['draft.md'],round=1,root=worker.root,workbook=before['workbook_id'],original_sha256=before['bindings']['original_raw_source']['sha256'])
    else:
        independent = verify_pair(authors, policy=task.policy, claims=claims, draft=body['draft.md'],
                round=1, root=worker.root, workbook=before['workbook_id'],
                original_sha256=before['bindings']['original_raw_source']['sha256'])
    require_independent(checked, independent)
    v = producer.skill_module(worker.root, 'additional-assumptions-dialogue', 'validate_dialogue')
    review = read(record(directory/'round-01/review.json'))
    require(review == checked['review'] and review.get('round') == 1 and review.get('passed') is False,
            'saved first review is not an actual failed round')
    faults = v.review_faults(review, claims,draft_text=body['draft.md'])
    require(faults and not v.review_passed(review, claims), 'successful review cannot be rewritten')
    receipt = read(record(directory/'round-01/review-result.json'))
    mechanics = v.check_draft(body['draft.md'], claims, task.stage, source_workbook=producer.dialogue_source_workbook(task))
    require(mechanics['passed'] is True,
            'current dialogue mechanics require a separately classified repair')
    require(receipt == {'passed': False, 'round': 1, 'draft_report': mechanics,
            'review_faults': faults, 'review': record(directory/'round-01/review.json'),
            'draft': record(directory/'round-01/draft.md'), 'call': asdict(call),
            'publication_ready': False}, 'saved semantic failure receipt changed')
    require(read(record(directory/'review-call-01.json')) == asdict(call), 'retained review identity changed')
    initial = record(worker.run/'initial.json')
    require(failure['intent'] == record(step/'intent.json'), 'failed intent differs')
    plan = read(failure['intent'])
    require(plan['initial'] == initial and plan['selection'] == failure['selection']
            and plan['lease'] == failure['lease'] and plan['step'] == 'dialogue-review', 'failed intent context differs')
    records = [tracker, blocked, failure['intent'], record(step/'request.json'), request.context, initial]
    records += _history(worker, before) + _call_records(call)
    for author in authors: records += _call_records(author)
    if takeover is not None:
        from scripts.batch.harbor_lease_takeover import completion_records
        records += completion_records(takeover)
    # Every predecessor is linked through its own immutable intent, including
    # pending steps and explicitly identified previous root continuation CAS.
    prior = plan['previous']; seen = set()
    permit_links = {}
    for field, key in (('retry','blocked'),('dialogue_retry','checkpoint'),('boundary_recovery','blocked')):
        binding = getattr(worker, field)
        if binding is not None: permit_links[binding['path']] = (binding, read(read(binding)['permit'])[key])
    if worker.code_repair is not None:
        binding = worker.code_repair
        permit_links[binding['path']] = (binding, read(binding)['predecessor'])
    if worker.disclosure_repair is not None:
        binding=worker.disclosure_repair
        permit_links[binding['path']]=(binding,read(binding)['blocked'])
    if worker.disclosure_revision is not None:
        binding=worker.disclosure_revision
        permit_links[binding['path']]=(binding,read(binding)['predecessor'])
    if worker.dialogue_pre_review_repair is not None:
        binding=worker.dialogue_pre_review_repair
        permit_links[binding['path']]=(binding,read(binding)['blocked'])
    while prior is not None:
        require(prior['path'] not in seen and len(seen) < 256, 'broken first-round checkpoint chain')
        seen.add(prior['path']); value = read(prior); records.append(prior)
        if 'intent' in value:
            intent = read(value['intent']); records.append(value['intent'])
            require(intent['initial'] == initial, 'first-round prefix initial differs')
            prior = intent['previous']
        elif worker.lease_renewal is not None and value.get('schema_version')=='harbor-instruction-lease-renewal/v1':
            from scripts.batch.harbor_instruction_lease_renewal import checkpoint_predecessor
            prior=checkpoint_predecessor(worker,prior)
        else:
            require(prior['path'] in permit_links and prior == permit_links[prior['path']][0],
                    'unknown first-round root continuation')
            prior = permit_links[prior['path']][1]
    return before, tracker, faults, records


def _project(before, permit):
    after = copy.deepcopy(before)
    after['handoffs'].append({'schema_version': SCHEMA, 'permit': permit})
    after['lane_state'][producer.DIALOGUE].update(state='repairing', attempt=1)
    return after


def _seal(worker, binding, after):
    permit = read(binding); old = read(permit['before']); expected = _project(old, binding)
    expected['revision'] += 1; expected['updated_at'] = after['updated_at']
    expected['leases']['task_agent']['heartbeat_at'] = after['updated_at']
    require(after == expected, 'semantic CAS differs from exact projection')
    out = Path(binding['path']).parent
    after_record = producer.put(out/'after-tracker.json', after)
    lease = Lease(permit['lease']['owner'], permit['lease']['token'], after['revision'], schema.sha(life.canonical(after)))
    complete = producer.put(out/'complete.json', {'schema_version': SCHEMA, 'permit': binding,
                           'after': after_record, 'lease': asdict(lease)})
    return replace(worker, dialogue_semantic_retry=complete), codec.decode(permit['selection']), lease, complete


def authorize(worker, selected, lease, *, blocked, takeover=None, scope_evidence=None, _fault=lambda _: None):
    w = _worker()
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        before, tracker, faults, records = _failure(worker, selected, lease, blocked, takeover)
        out = worker.run/'dialogue-semantic-retry-02'
        require(not out.exists(), 'prepared semantic transaction requires explicit reconciliation')
        out.mkdir(); current = w.code(worker)
        producer.put(out/'intent.json', {'blocked': blocked, 'tracker': tracker, 'code': current})
        continuation=selected;scope_transition=None
        if scope_evidence is not None:
            from scripts.batch import harbor_dialogue_scope_context as scope_context
            task=w.task(worker,selected,tracker)
            scope_transition=scope_context.prepare_transition(task,scope_evidence,root=worker.root)
            _,_,scope=scope_context.validate_transition(scope_transition,root=worker.root)
            continuation=replace(selected,content=replace(selected.content,dialogue_claims=scope['artifacts']['claims'],scope_evidence=scope['scope'],dialogue_scope_receipt=scope_transition))
            records+=scope_context.evidence(scope_transition)
        records += structural._prefix(before, continuation, worker.context)
        dialogue_directory=Path(read(worker.dialogue_pre_review_repair)['dialogue_directory']) if worker.dialogue_pre_review_repair is not None else worker.run/'producer/dialogue'
        for path in sorted(dialogue_directory.rglob('*')):
            schema.safe_path(path)
            if path.is_file(): records.append(record(path))
        from scripts.batch.harbor_nat_retry import _archive
        copies = _archive(out/'archive', records)
        before_copy = next(row['retained'] for row in copies if row['original'] == tracker)
        require(w.code(worker) == current, 'code changed during current prefix validation')
        permit = producer.put(out/'permit.json', {'schema_version': SCHEMA, 'worker_run': str(worker.run),
            'before': before_copy, 'selection': codec.encode(continuation), 'lease': asdict(lease), 'code': current,
            'copies': copies, 'blocked': blocked, 'initial': record(worker.run/'initial.json'),
            'prior_continuations': _continuations(worker),
            'faults': faults, 'scope_transition':scope_transition, 'round': 2, 'consumed_rounds': 1, 'semantic_limit': 2,
            'structural_retry_authorized': False, 'semantic_budget_reset': False, 'lease_takeover': takeover})
        _fault('prepared')
        def mutate(after):
            require(w.code(worker) == current, 'code changed before semantic CAS')
            fresh = _failure(worker, selected, lease, blocked, takeover)
            require(fresh[0] == before and fresh[2] == faults, 'semantic fault or context changed before CAS')
            life.check_records([row['original'] for row in copies])
            if scope_transition is not None:
                scope_context.validate_transition(scope_transition,root=worker.root)
            projection = _project(before, permit)
            after['handoffs'] = projection['handoffs']; after['lane_state'] = projection['lane_state']
        result = schema.cas_mutate(tracker['path'], expected_revision=lease.revision, expected_sha256=lease.sha256,
            owner=lease.owner, token=lease.token, scope='workbook:'+selected.content.workbook, mutate=mutate,
            allowed_fields={'handoffs','lane_state'}, allowed_lanes={producer.DIALOGUE}, fault_hook=_fault)
        _fault('committed')
        return _seal(worker, permit, result['tracker'])


def recover(worker, permit, lease):
    w = _worker(); value = read(permit)
    require(value['worker_run'] == str(worker.run) and value['code'] == w.code(worker)
            and value['lease'] == asdict(lease), 'semantic recovery identity or code changed')
    require(value['prior_continuations'] == _continuations(worker), 'semantic recovery detached prior history')
    with (worker.run/'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        for row in value['copies']: life.checked_record(row['retained'])
        selected = codec.decode(value['selection'])
        path = worker.context.tracker_root/(selected.content.workbook+'.json'); after = read(record(path))
        require(after['revision'] == lease.revision+1, 'semantic CAS not committed or advanced')
        w.active(worker, selected, Lease(lease.owner, lease.token, after['revision'], record(path)['sha256']))
        return _seal(worker, permit, after)


def validate_completed_integrity(worker, before):
    """Historical transaction integrity only; never establishes current quality."""
    if worker.warning_requalification is not None and worker.lease_renewal is not None:
        from scripts.batch.harbor_dialogue_warning_requalification import completed_semantic_history
        return completed_semantic_history(worker,before)
    w = _worker(); complete = read(worker.dialogue_semantic_retry); value = read(complete['permit'])
    require(complete['schema_version'] == value['schema_version'] == SCHEMA
            and value['worker_run'] == str(worker.run), 'semantic continuation context changed')
    require(value['round']==2 and value['consumed_rounds']==1 and value['semantic_limit']==2
            and value['structural_retry_authorized'] is False and value['semantic_budget_reset'] is False, 'semantic permit budget changed')
    require(value['prior_continuations'] == _continuations(worker),
            'prior continuation history detached')
    require({'schema_version': SCHEMA, 'permit': complete['permit']} in before['handoffs'], 'semantic permit lacks root CAS')
    old, after = read(value['before']), read(complete['after']); expected = _project(old, complete['permit'])
    expected['revision'] += 1; expected['updated_at'] = after['updated_at']
    expected['leases']['task_agent']['heartbeat_at'] = after['updated_at']
    require(after == expected and before['revision'] >= after['revision'], 'semantic tracker projection changed')
    require(complete['lease'] == asdict(Lease(value['lease']['owner'],value['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),
            'semantic lease changed')
    for row in value['copies']:
        life.checked_record(row['retained'])
        require(row['retained']['sha256'] == row['original']['sha256'], 'semantic archive changed')
    _history(worker,before)
    if value.get('scope_transition') is not None:
        from scripts.batch import harbor_dialogue_scope_context as scope_context
        _,_,scope=scope_context.validate_transition(value['scope_transition'],root=worker.root)
        selected=codec.decode(value['selection']).content
        require(selected.dialogue_scope_receipt==value['scope_transition'] and selected.dialogue_claims==scope['artifacts']['claims'] and selected.scope_evidence==scope['scope'],'semantic grouping selection detached')
    for binding in (value['initial'], value['blocked']): life.checked_record(binding)
    require(before['lane_state'][producer.DIALOGUE].get('attempt',0) >= 1, 'dialogue consumption regressed')
    return {**value, 'lease':complete['lease']}


def validate(worker, before):
    value = validate_completed_integrity(worker, before)
    require(value['code'] == _worker().code(worker), 'semantic continuation context/code changed')
    return value



def validate_pre_review_link(binding,pre_review,before,run,*,root):
    """Nonrecursive exact semantic CAS proof for a retained pre-review correction."""
    from types import SimpleNamespace
    complete=read(binding);p=read(complete['permit'])
    require(binding==record(Path(run)/'dialogue-semantic-retry-02/complete.json'),'foreign semantic completion')
    require(complete['schema_version']==p['schema_version']==SCHEMA and p['worker_run']==str(run),'semantic pre-review context changed')
    require(p['prior_continuations'].get('dialogue_pre_review_repair')==pre_review,'semantic correction link omitted or substituted')
    require(p['round']==2 and p['consumed_rounds']==1 and p['semantic_limit']==2 and p['semantic_budget_reset'] is False and p['structural_retry_authorized'] is False,'semantic correction budget changed')
    require(p['code']==_worker().code(SimpleNamespace(root=root)),'semantic correction current code changed')
    old,after=read(p['before']),read(complete['after']);expected=_project(old,complete['permit'])
    expected['revision']+=1;expected['updated_at']=after['updated_at'];expected['leases']['task_agent']['heartbeat_at']=after['updated_at']
    require(expected==after and before['revision']>=after['revision'] and {'schema_version':SCHEMA,'permit':complete['permit']} in before['handoffs'],'semantic correction lacks exact owning CAS')
    require(complete['lease']==asdict(Lease(p['lease']['owner'],p['lease']['token'],after['revision'],schema.sha(life.canonical(after)))),'semantic correction lease changed')
    for row in p['copies']:
        life.checked_record(row['retained']);require(row['retained']['sha256']==row['original']['sha256'],'semantic correction archive changed')
    return p


def historical_pre_review(worker,before):
    from scripts.batch import harbor_dialogue_pre_review_repair as Q
    if worker.dialogue_semantic_retry is not None:
        validate_pre_review_link(worker.dialogue_semantic_retry,worker.dialogue_pre_review_repair,before,worker.run,root=worker.root)
    return Q.validate_historical_integrity(worker,before,root=worker.root)


def historical_lease(worker,before):
    from scripts.batch import harbor_dialogue_pre_review_repair as Q
    historical_pre_review(worker,before)
    return Q.historical_lease(worker,before)
