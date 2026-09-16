import importlib.util
import sys
import json
from pathlib import Path
from dataclasses import replace, asdict
import time
import fcntl
import ast

import pytest
from scripts.batch import harbor_instruction_producer as p
from scripts.batch import harbor_tracker_lifecycle as life
from scripts.batch import harbor_tracker as schema
from scripts.batch.harbor_lane_notes import RootContext
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from scripts.batch.harbor_workbook_bootstrap import Lease
from test_harbor_instruction_producer import setup
from test_naturalization_frozen_submission import make


@pytest.fixture
def modules():
    from scripts.batch import harbor_instruction_worker, harbor_nat_retry
    return harbor_instruction_worker, harbor_nat_retry


@pytest.fixture
def failed(setup, modules, monkeypatch):
    return build_failed(setup,modules,monkeypatch,False)


@pytest.fixture
def semantic_failed(setup,modules,monkeypatch):
    return build_failed(setup,modules,monkeypatch,True)


def build_failed(setup, modules, monkeypatch, semantic):
    w, retry = modules
    t, campaign, respond, calls = setup
    path = Path(t.tracker['path']); before = json.loads(path.read_text())
    for key in ('source_file', 'source_generation', 'segmentation_generation', 'curation'):
        before['bindings'][key] = before['raw_source']
    path.write_text(json.dumps(before))
    authority = life.RootAuthority('harbor-orchestrator', 10**12, lambda *a: True)
    acquired = life.lease_operation(path, operation='acquire', authority=authority, lease_kind='task_agent', owner='fixture-worker', scope='workbook:0245', expected_revision=before['revision'], expected_sha256=record(path)['sha256'], duration_ns=10**12)
    current = json.loads(path.read_text()); token = current['leases']['task_agent']['token']
    lease = Lease('fixture-worker', token, acquired['revision'], acquired['sha256'])
    t = replace(t, tracker=record(path))
    old_respond = respond
    def respond(request, value):
        original = p.dispatch
        with monkeypatch.context() as local:
            local.setattr(p, 'dispatch', lambda _task, *a, **kw: original(replace(t, tracker=record(path)), *a, **kw))
            return old_respond(request, value)
    worker = w.Worker(RootContext(path.parent), t.run.parent, campaign)
    norm = t.run / '0245-variable-sources'; norm.mkdir(parents=True); (norm / 'normalized.json').write_text('{}')
    p.initialize_naturalization(t)
    selected = ReadinessSelection(Selection('0245', t.stage, p.tree(t.stage), current['bindings'], norm, t.run / 'naturalization', None, None, model_policy=t.policy), None, None, None, None)
    # Financial upstream fixtures are covered by installed owning-gate tests.
    # Only this fixed boundary is replaced here; real strict CAS/lease, executor
    # journals, frozen assembly and mechanical validators remain active.
    monkeypatch.setattr(retry, '_fresh_prefix', lambda *a: [record(t.stage / 'instruction.md')])
    import scripts.batch.harbor_complete_readiness as complete
    monkeypatch.setattr(complete, 'evidence_records', lambda _: [])
    initial = w.keep(worker.run / 'initial.json', dict(selection=w.codec.encode(selected), lease=asdict(lease), code={'old-worker-code': '0'*64}))
    previous = None
    for phase in retry.PREFIX:
        directory = worker.run / 'steps' / phase
        intent = w.keep(directory / 'intent.json', dict(step=phase, initial=initial, previous=previous))
        previous = w.keep(directory / 'complete.json', dict(step=phase, status='completed', intent=intent, selection=w.codec.encode(selected), lease=asdict(lease)))
    req = p.prepare_naturalization_writer(t)
    w.request_saved(worker.run / 'steps/nat-writer/request.json', req)
    source = p.text(record(t.run / 'naturalization/writer_source.md'))
    spans = p.skill_module(t.root, 'naturalize-finance-task-instruction', 'instruction_spans')
    a, b = spans.extract_editable_bodies(source.encode(), spans.scan_instruction(source.encode()))
    respond(req, {'preamble_body': a.decode(), 'input_body': b.decode() + ('' if semantic else '\n## Output\nDuplicated immutable tail\n')})
    result = p.consume_naturalization_writer(t, req, campaign)
    assert result['validation']['valid'] is semantic
    selected = w.refreshed(replace(selected, content=replace(selected.content, naturalization_writer_call=result['call'], naturalization_writer_request=req.prepared.request)))
    directory = worker.run / 'steps/nat-writer'
    intent = w.keep(directory / 'intent.json', dict(step='nat-writer', initial=initial, previous=previous))
    if semantic:
        previous=w.keep(directory/'complete.json',dict(step='nat-writer',status='completed',intent=intent,selection=w.codec.encode(selected),lease=asdict(lease)))
        review=p.prepare_naturalization_review(t)
        directory=worker.run/'steps/nat-review'
        w.request_saved(directory/'request.json',review)
        respond(review,dict(valid=False,blocking_findings=['The procedure was weakened'],clause_checks=[]))
        reviewed=p.consume_naturalization_review(t,review,req,campaign)
        selected=w.refreshed(replace(selected,content=replace(selected.content,naturalization_call=reviewed['call'],naturalization_request=review.prepared.request)))
        intent=w.keep(directory/'intent.json',dict(step='nat-review',initial=initial,previous=previous))
    blocked = w.keep(directory / 'blocked.json', dict(step='nat-review' if semantic else 'nat-writer', status='NAT_REPAIR_REQUIRED', intent=intent, selection=w.codec.encode(selected), lease=asdict(lease)))
    return w, retry, worker, selected, lease, blocked, t, respond, calls, a, b


def test_root_authorization_preserves_attempt_and_uses_real_cas(failed):
    w, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    state = record(t.run / 'naturalization/state.json'); prior = record(blocked['path'])
    worker2, s2, lease2, checkpoint = r.authorize(worker, s, lease, blocked=blocked)
    assert lease2.revision == lease.revision + 1 and len(calls) == 1
    assert record(t.run / 'naturalization/state.json') == state and record(blocked['path']) == prior
    tracker = json.loads(Path(t.tracker['path']).read_text())
    assert tracker['lane_state'][p.NAT]['attempt'] == 1
    assert tracker['promotion_failure'] is None and tracker['terminal_verdict'] is None
    assert r.validate(worker2, tracker)['remaining'] == 1
    result = w.step(worker2, s2, lease2, checkpoint=checkpoint)
    assert result.status == 'MODEL_REQUIRED' and len(calls) == 1
    request = w.request_saved(worker.run / 'steps/nat-writer/round-02/request.json')
    assert request.prepared.ordinal == request.prepared.limit == 2
    first = w.request_saved(worker.run / 'steps/nat-writer/request.json')
    assert request.prepared.scope == first.prepared.scope
    assert request.prepared.attempt_id != first.prepared.attempt_id
    payload = p.read(request.prepared.request)
    assert json.loads(payload['messages'][1]['content']) == json.loads(p.read(first.prepared.request)['messages'][1]['content'])
    assert 'heading_structure_changed' in payload['messages'][0]['content']
    assert 'stop before the next level-two heading' in payload['messages'][0]['content']
    assert record(t.run / 'naturalization/attempt-01/validation.json') in p.read(request.context)['evidence']


def test_round2_valid_writer_requires_fresh_independent_review(failed, monkeypatch):
    w, r, worker, s, lease, blocked, t, respond, calls, a, b = failed
    worker, s, lease, checkpoint = r.authorize(worker, s, lease, blocked=blocked)
    pending = w.step(worker, s, lease, checkpoint=checkpoint)
    req = w.request_saved(worker.run / 'steps/nat-writer/round-02/request.json')
    # Existing response fixture holds the original tracker binding; dispatch the
    # synthetic response through current task (no real socket).
    current = w.task(worker, s, record(t.tracker['path']))
    original_dispatch = p.dispatch
    def synthetic_dispatch(_task, *args, **kw):
        return original_dispatch(current, *args, **kw)
    monkeypatch.setattr(p, 'dispatch', synthetic_dispatch)
    respond(req, {'preamble_body': a.decode(), 'input_body': b.decode()})
    done = w.step(worker, s, lease, checkpoint=pending.evidence)
    assert done.status == 'STEP_COMPLETE' and done.next_step == 'nat-review'
    review = w.step(worker, done.selection, done.lease, checkpoint=done.evidence)
    assert review.status == 'MODEL_REQUIRED' and len(calls) == 2
    review_request = w.request_saved(worker.run / 'steps/nat-review/request.json')
    assert p.read(review_request.context)['writer_round'] == 2
    assert review_request.prepared.ordinal == review_request.prepared.limit == 1
    state = p.read(record(t.run / 'naturalization/state.json'))
    assert len(state['attempts']) == 2 and state['status'] == 'validated'
    assert not state.get('accepted_semantic_review')
    assert record(blocked['path']) == blocked
    body = json.loads(p.read(review_request.prepared.request)['messages'][1]['content'])
    respond(review_request, {'valid': True, 'blocking_findings': [], 'clause_checks': [{'source_claim': line, 'candidate_claim': line, 'preserved': True} for line in p.editable_claims(t.root, body['source'])]})
    applied = w.step(worker, done.selection, done.lease, checkpoint=review.evidence)
    assert applied.status == 'STEP_COMPLETE' and applied.next_step == 'nat-commit'
    assert len(calls) == 3
    assert p.read(record(t.run / 'naturalization/state.json'))['status'] == 'applied'


@pytest.mark.parametrize('fault', ['prepared', 'after_temp_fsync', 'before_replace', 'after_replace', 'committed'])
def test_interruption_requires_explicit_exact_reconciliation(failed, fault):
    w, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    def crash(point):
        if point == fault: raise KeyboardInterrupt('synthetic crash')
    with pytest.raises(KeyboardInterrupt):
        r.authorize(worker, s, lease, blocked=blocked, _fault=crash)
    permit = record(worker.run / 'nat-retry-02/permit.json')
    if fault in ('after_replace', 'committed'):
        worker2, _, lease2, _ = r.recover(worker, permit, lease)
        assert lease2.revision == lease.revision + 1
        assert r.validate(worker2, p.read(record(t.tracker['path'])))['spent'] == 1
    else:
        with pytest.raises(ValueError): r.recover(worker, permit, lease)
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['state', 'candidate', 'response', 'blocked', 'wrong_lease', 'expired', 'downstream', 'prompt', 'valid_candidate'])
def test_rejects_changed_unknown_or_unowned_retry(failed, mutation, monkeypatch):
    w, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    if mutation == 'state':
        path = t.run / 'naturalization/state.json'; d = json.loads(path.read_text()); d['attempt_limit'] = 3; path.write_text(json.dumps(d))
    elif mutation == 'candidate':
        (t.run / 'naturalization/attempt-01/candidate.md').write_text('changed')
    elif mutation == 'response':
        Path(s.content.naturalization_writer_call.response['path']).write_text('changed')
    elif mutation == 'blocked': blocked = record(worker.run / 'initial.json')
    elif mutation == 'wrong_lease': lease = replace(lease, token='wrong')
    elif mutation == 'expired':
        path = Path(t.tracker['path']); d = json.loads(path.read_text()); l = d['leases']['task_agent']; l['expires_at'] = l['heartbeat_at'] + 1; path.write_text(json.dumps(d)); lease = replace(lease, sha256=record(path)['sha256'])
    elif mutation == 'downstream': (worker.run / 'steps/nat-review').mkdir()
    elif mutation == 'prompt':
        monkeypatch.setattr(p, 'naturalization_writer_prompt', lambda *_, **__: 'different current financial instruction')
    else:
        original = p.skill_module(t.root, 'naturalize-finance-task-instruction', 'naturalize_recovery')._load_validator()
        monkeypatch.setattr(original, 'validate', lambda *_: {'valid': True})
    with pytest.raises((ValueError, KeyError, p.skill_module(t.root, 'naturalize-finance-task-instruction', 'naturalize_recovery').RecoveryError)):
        r.authorize(worker, s, lease, blocked=blocked)
    assert len(calls) == 1


def test_second_authorization_cannot_reset_scope(failed):
    _, r, worker, s, lease, blocked, _, _, calls, *_ = failed
    worker2, s2, lease2, _ = r.authorize(worker, s, lease, blocked=blocked)
    with pytest.raises(ValueError): r.authorize(worker2, s2, lease2, blocked=blocked)
    assert len(calls) == 1


def test_same_owner_root_renewal_preserves_blocked_lease_history(failed):
    w, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    path = Path(t.tracker['path'])
    authority = life.RootAuthority('harbor-orchestrator', 10**12, lambda *a: True)
    renewed = life.lease_operation(path, operation='renew', authority=authority,
        lease_kind='task_agent', owner=lease.owner, token=lease.token,
        scope='workbook:0245', expected_revision=lease.revision,
        expected_sha256=lease.sha256, duration_ns=10**12)
    current = Lease(lease.owner, lease.token, renewed['revision'], renewed['sha256'])
    worker2, s2, lease2, checkpoint = r.authorize(worker, s, current, blocked=blocked)
    assert lease2.revision == current.revision + 1
    assert p.read(blocked)['lease'] == asdict(lease)
    assert w.step(worker2, s2, lease2, checkpoint=checkpoint).status == 'MODEL_REQUIRED'
    assert len(calls) == 1


def test_ad_hoc_tracker_edit_is_not_a_lease_renewal(failed):
    _, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    path = Path(t.tracker['path']); value = json.loads(path.read_text())
    value['current_gate'] = 'other'; path.write_text(json.dumps(value))
    current = replace(lease, sha256=record(path)['sha256'])
    with pytest.raises(ValueError): r.authorize(worker, s, current, blocked=blocked)
    assert len(calls) == 1


def test_archive_tampering_and_code_drift_block_consumer(failed, monkeypatch):
    w, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    worker2, s2, lease2, checkpoint = r.authorize(worker, s, lease, blocked=blocked)
    value = r.read(r.read(checkpoint)['permit'])
    retained = Path(value['copies'][0]['retained']['path']); retained.chmod(0o600); retained.write_bytes(b'changed')
    with pytest.raises(ValueError): w.step(worker2, s2, lease2, checkpoint=checkpoint)
    assert len(calls) == 1


def test_concurrent_authorization_uses_same_worker_lock(failed):
    _, r, worker, s, lease, blocked, _, _, calls, *_ = failed
    with (worker.run / 'worker.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError): r.authorize(worker, s, lease, blocked=blocked)
    assert len(calls) == 1


def test_prefix_revalidation_failure_never_cas_or_dispatches(failed, monkeypatch):
    _, r, worker, s, lease, blocked, t, _, calls, *_ = failed
    original = record(t.tracker['path'])
    def reject(*a): raise ValueError('real owning prefix rejection fixture')
    monkeypatch.setattr(r, '_fresh_prefix', reject)
    with pytest.raises(ValueError): r.authorize(worker, s, lease, blocked=blocked)
    assert record(t.tracker['path']) == original and len(calls) == 1
    assert not (worker.run / 'nat-retry-02/complete.json').exists()


def test_identical_second_response_spends_two_and_exact_replay_is_idempotent(failed):
    w, r, worker, s, lease, blocked, t, respond, calls, a, b = failed
    worker, s, lease, checkpoint = r.authorize(worker, s, lease, blocked=blocked)
    pending = w.step(worker, s, lease, checkpoint=checkpoint)
    request = w.request_saved(worker.run / 'steps/nat-writer/round-02/request.json')
    respond(request, {'preamble_body': a.decode(), 'input_body': b.decode() + '\n## Output\nDuplicated immutable tail\n'})
    stopped = w.step(worker, s, lease, checkpoint=pending.evidence)
    assert stopped.status == 'NAT_REPAIR_REQUIRED'
    assert len(calls) == 2 and request.prepared.ordinal == 2
    state = p.read(record(t.run / 'naturalization/state.json'))
    assert len(state['attempts']) == 2 and state['status'] == 'exhausted'
    current = w.task(worker, stopped.selection, record(t.tracker['path']))
    replayed = p.consume_naturalization_writer(current, request, worker.campaign)
    assert replayed['validation']['idempotent'] is True
    assert len(p.read(record(t.run / 'naturalization/state.json'))['attempts']) == 2
    with pytest.raises(ValueError): r.authorize(worker, stopped.selection, lease, blocked=stopped.evidence)


@pytest.mark.parametrize('ordinal', [0, 3, -1, True, 1.0, '2'])
def test_explicit_ordinal_rejects_invalid_identity_without_mutation(failed, ordinal):
    _, _, _, _, _, _, t, _, calls, *_ = failed
    root = t.run / 'naturalization'
    recovery = p.skill_module(t.root, 'naturalize-finance-task-instruction', 'naturalize_recovery')
    before = record(root / 'state.json')
    with pytest.raises(recovery.RecoveryError, match='ordinal'):
        recovery.submit_attempt(root, (root / 'attempt-01/preamble_body.md').read_bytes(),
            (root / 'attempt-01/input_body.md').read_bytes(), submission_mode='frozen', expected_attempt=ordinal)
    assert record(root / 'state.json') == before and len(calls) == 1


def test_default_same_bodies_idempotence_and_explicit_replay_remain_unchanged(failed):
    _, _, _, _, _, _, t, _, calls, *_ = failed
    root = t.run / 'naturalization'
    recovery = p.skill_module(t.root, 'naturalize-finance-task-instruction', 'naturalize_recovery')
    bodies = [(root / 'attempt-01' / name).read_bytes() for name in ('preamble_body.md', 'input_body.md')]
    mode = p.read(record(root / 'state.json'))['attempts'][0]['submission_mode']
    assert mode in {'frozen','frozen-boundary-v1'}
    before = record(root / 'state.json')
    assert recovery.submit_attempt(root, *bodies, submission_mode=mode)['idempotent']
    assert recovery.submit_attempt(root, *bodies, submission_mode=mode, expected_attempt=1)['idempotent']
    with pytest.raises(recovery.RecoveryError, match='different submitted bodies'):
        recovery.submit_attempt(root, bodies[0] + b'changed', bodies[1], submission_mode=mode, expected_attempt=1)
    assert record(root / 'state.json') == before and len(calls) == 1


def test_changed_code_after_authorization_requires_root_revalidation(failed, monkeypatch):
    w, r, worker, selected, lease, blocked, _, _, calls, *_ = failed
    worker, selected, lease, checkpoint = r.authorize(worker, selected, lease, blocked=blocked)
    original = w.code
    monkeypatch.setattr(w, 'code', lambda value: {**original(value), 'changed': '0' * 64})
    with pytest.raises(ValueError, match='code changed'):
        w.step(worker, selected, lease, checkpoint=checkpoint)
    assert len(calls) == 1


@pytest.mark.parametrize('mode',['frozen','frozen-boundary-v1'])
def test_explicit_mode_same_body_replay_preserves_legacy_contract(make,mode):
    from test_instruction_naturalization import RECOVERY as recovery
    root,bodies=make('explicit-mode')
    recovery.submit_attempt(root,*bodies,submission_mode=mode,expected_attempt=1)
    before=record(root/'state.json')
    assert recovery.submit_attempt(root,*bodies,submission_mode=mode)['idempotent']
    assert recovery.submit_attempt(root,*bodies,submission_mode=mode,expected_attempt=1)['idempotent']
    different='frozen-boundary-v1' if mode=='frozen' else 'frozen'
    with pytest.raises(recovery.RecoveryError,match='different mode'):
        recovery.submit_attempt(root,*bodies,submission_mode=different,expected_attempt=1)
    assert record(root/'state.json')==before


def test_semantic_retry_preserves_rejection_and_informs_remaining_revision(semantic_failed):
    w,r,worker,s,lease,blocked,t,respond,calls,a,b=semantic_failed
    state=p.read(record(t.run/'naturalization/state.json'))
    before=record(t.run/'naturalization/attempt-01/semantic-review.json')
    worker,s,lease,checkpoint=r.authorize(worker,s,lease,blocked=blocked)
    assert len(calls)==2
    assert r.validate(worker,p.read(record(t.tracker['path'])))['rejection_phase']=='nat-review'
    pending=w.step(worker,s,lease,checkpoint=checkpoint)
    assert pending.status=='MODEL_REQUIRED'
    request=w.request_saved(w.step_directory(worker,'nat-writer')/'request.json')
    assert request.prepared.ordinal==request.prepared.limit==2
    assert 'The procedure was weakened' in p.read(request.prepared.request)['messages'][0]['content']
    assert before==record(before['path'])

    assert p.read(record(t.run/'naturalization/state.json'))==state
    assert w.step_directory(worker,'nat-review')==worker.run/'steps/nat-review/round-02'
    assert len(calls)==2
    respond(request,{'preamble_body':a.decode(),'input_body':b.decode()})
    done=w.step(worker,s,lease,checkpoint=pending.evidence)
    assert done.status=='STEP_COMPLETE'
    review_pending=w.step(worker,done.selection,done.lease,checkpoint=done.evidence)
    assert review_pending.status=='MODEL_REQUIRED'
    review=w.request_saved(w.step_directory(worker,'nat-review')/'request.json')
    body=json.loads(p.read(review.prepared.request)['messages'][1]['content'])
    respond(review,dict(valid=True,blocking_findings=[],clause_checks=[dict(source_claim=line,candidate_claim=line,preserved=True) for line in p.editable_claims(t.root,body['source'])]))
    applied=w.step(worker,done.selection,done.lease,checkpoint=review_pending.evidence)
    assert applied.status=='STEP_COMPLETE' and applied.next_step=='nat-commit'
    final=p.read(record(t.run/'naturalization/state.json'))
    assert final['attempts'][0]==state['attempts'][0]
    assert final['semantic_reviews'][0]==state['semantic_reviews'][0]
    assert len(final['attempts'])==2 and len(calls)==4
    assert before==record(before['path'])

def test_completed_takeover_retains_original_request_through_real_retry_cas(semantic_failed, monkeypatch):
    from scripts.batch import harbor_lease_takeover as takeover
    w,r,worker,s,lease,blocked,t,_,calls,*_=semantic_failed
    selected=w.keep(worker.run/'selected-takeover.json',{'fixture':'completed custody'})
    original_request=w.request_saved(w.step_directory(worker,'nat-review')/'request.json')
    original_tracker=p.read(original_request.context)['budget_evidence'][0]
    witnesses=[]
    def chain(original,old,current,actual,**kw):
        assert original==original_tracker and old==p.read(blocked)['lease']
        assert kw==dict(takeover=selected,tracker_path=Path(t.tracker['path']))
        assert actual.owner==lease.owner and actual.token==lease.token
        witnesses.append(current['revision'])
    monkeypatch.setattr(takeover,'continuation_chain',chain)
    monkeypatch.setattr(takeover,'completion_records',lambda value:[selected] if value==selected else [])
    monkeypatch.setattr(r,'_renewal_chain',lambda *a:pytest.fail('explicit takeover cannot use renewal-only history'))
    old_blocked=record(blocked['path'])
    renewed,s,lease,checkpoint=r.authorize(worker,s,lease,blocked=blocked,takeover=selected)
    current=p.read(record(t.tracker['path']))
    permit=r.validate(renewed,current)
    assert permit['lease_takeover']==selected
    assert selected in [row['original'] for row in permit['copies']]
    assert record(blocked['path'])==old_blocked and len(calls)==2
    # Historical validation must read the original review request, even though
    # the selected worker now resolves future NAT paths under round-02.
    assert len(witnesses)>=3 and w.step_directory(renewed,'nat-review').name=='round-02'


def test_completed_takeover_rejection_precedes_retry_cas(semantic_failed, monkeypatch):
    from scripts.batch import harbor_lease_takeover as takeover
    w,r,worker,s,lease,blocked,t,_,calls,*_=semantic_failed
    selected=w.keep(worker.run/'foreign-takeover.json',{'fixture':'wrong custody'})
    before=record(t.tracker['path'])
    def reject(*a,**k):raise ValueError('foreign completed takeover')
    monkeypatch.setattr(takeover,'continuation_chain',reject)
    with pytest.raises(ValueError,match='foreign completed takeover'):
        r.authorize(worker,s,lease,blocked=blocked,takeover=selected)
    assert record(t.tracker['path'])==before and len(calls)==2
