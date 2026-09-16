"""Once-only source-card reservation after an explicit root takeover.

No lease CAS, model call, source generation or publication is performed here.
The root-selected guarded worker supplies its original modules and live custody
check. Validation may run read-only; committing requires a live worker lease and
creates one permanent reservation before any corrected author request.
"""
from dataclasses import asdict, replace
from pathlib import Path
import fcntl

from scripts.fleet import source_card_authoring as core
from scripts.fleet.source_card_reader import binding, checked


def _unspent(worker, producer, original):
    core.require(not (original/'round-02').exists() and
                 not any((original/name).exists() for name in
                         ('review-call-01.json', 'review-call-02.json', 'round-01/review.json')) and
                 not (worker.run/'producer/requests/dialogue-review').exists(),
                 'source-card correction cannot replay prepared or consumed work')
    for name in ('dialogue-review', 'final-runtime', 'enqueue'):
        core.require(not (worker.run/'steps'/name).exists(), 'later dialogue phase already attempted')
    # A second author can be prepared before its directory is populated. The
    # immutable executor preparation is itself consumption evidence here.
    for name in ('dialogue-writer', 'dialogue-paraphraser'):
        requests = worker.run/'producer/requests'/name
        if requests.exists():
            core.require(set(path.name for path in requests.iterdir()) == {'attempt-01'},
                         'another author preparation already consumes the remaining generation')
            value = checked(binding(requests/'attempt-01/context.json'))
            core.require(type(value['ordinal']) is int and value['ordinal'] == 1 and
                         value['kind'] == name and value['limit'] == 2,
                         'original author preparation budget changed')


def proposal(reader, *, live_custody):
    """Full original source/Call/prefix checks, before any reservation write."""
    p = reader.plan
    W, P, C, R, T = reader.worker, reader.producer, reader.campaign, reader.renewal, reader.takeover
    live_custody()
    descriptor = checked(p['old_descriptor'])
    old = C.worker_decode(descriptor['worker'])
    core.require(str(old.run) == p['worker_run'] and descriptor['code'] == binding(C.__file__),
                 'original source-card coordinator differs')
    core.require(all(getattr(old, key) is None for key in R.CONTINUATIONS if key != 'lease_renewal') and
                 old.lease_renewal is not None, 'unselected original source-card history')
    old_inventory = R._historical_inventory(old, p['old_snapshot'])
    full_inventory = {str(old.root/row['relative']): row['sha256']
                      for row in checked(p['old_snapshot'])['files']}
    failure = checked(p['blocked'])
    core.require(p['blocked'] == binding(W.step_directory(old, 'dialogue-paraphraser')/'blocked.json') and
                 failure['step'] == 'dialogue-paraphraser' and failure['status'] == 'DIALOGUE_REPAIR_REQUIRED' and
                 not (W.step_directory(old, 'dialogue-paraphraser')/'complete.json').exists(),
                 'original mechanically rejected paraphraser boundary differs')
    failed_intent = checked(failure['intent'])
    core.require(failed_intent['code'] == old_inventory and failed_intent['selection'] == failure['selection'] and
                 failed_intent['initial'] == binding(old.run/'initial.json'),
                 'failed original task implementation or selection changed')
    from scripts.batch import harbor_transition_codec as codec
    from scripts.batch.harbor_tracker_lifecycle import validate_lifecycle
    old_selection = codec.decode(failure['selection'])
    prior, after, lease = T.validate_completion(p['takeover'])
    core.require(validate_lifecycle(prior) == 0 and validate_lifecycle(after) == 1 and
                 prior['workbook_id'] == '0200', 'exact first bounded takeover required')
    _, before, tracker = W.active(old, old_selection, lease)
    core.require(before == after and tracker['sha256'] == checked(p['takeover'])['after']['sha256'],
                 'source-card authorization requires the exact unused takeover boundary')
    R.validate_historical_integrity(old, before, p['old_snapshot'])
    prior_renewal = checked(checked(old.lease_renewal)['permit'])
    T.continuation_chain(prior_renewal['tracker'], prior_renewal['lease'], before, lease,
                         takeover=p['takeover'], tracker_path=Path(tracker['path']))
    original = old.run/'producer/dialogue'
    _unspent(old, P, original)
    claims = binding(original/'claims.json')
    draft, report = binding(original/'round-01/draft.md'), binding(original/'round-01/draft-check.json')
    core.require(checked(report)['passed'] is False, 'original mechanical failure was changed')
    proof = checked(p['source_proof'])
    core.require(proof['original_claims'] == claims and proof['old_draft'] == draft and proof['old_check'] == report,
                 'source proof does not bind the actual owning author pair')
    requests = [W.request_saved(W.step_directory(old, kind)/'request.json')
                for kind in ('dialogue-writer', 'dialogue-paraphraser')]
    calls = tuple(P.observed(request, old.campaign) for request in requests)
    core.original_pair(reader.author(), calls, policy=requests[0].prepared.policy, claims=checked(claims),
        draft=Path(draft['path']).read_text(), root=old.root, workbook='0200',
        source_sha256=before['bindings']['original_raw_source']['sha256'])
    history = [p['blocked'], failure['intent'], claims, draft, report, *T.completion_records(p['takeover'])]
    for req in requests:
        context = checked(req.context)
        core.require(context['request'] == req.prepared.request and
                     context['budget_evidence'] == list(req.prepared.budget_evidence),
                     'old author request context changed')
        for row in context['code']:
            core.require(full_inventory.get(row['path']) == row['sha256'],
                         'original author context is outside its full selected implementation')
        history.extend([req.context, *context['code'], *context['evidence'], *context['budget_evidence']])
    for call in calls:
        history.extend(row for row in asdict(call).values() if row is not None)
    for row in history:
        core.require(binding(row['path']) == row, 'retained source-card history changed')
    corrected = checked(p['corrected_claims'])
    core.require(reader.extractor().extract(old_selection.content.stage) == corrected,
                 'complete current source extraction differs from proposed corrected cards')
    from scripts.batch.harbor_instruction_repair import prefix
    current_prefix = prefix(before, old_selection, old.context, stop='dialogue')
    selected = replace(old_selection, content=replace(old_selection.content, dialogue_claims=p['corrected_claims']))
    chosen = replace(old, dialogue_pre_review_repair=reader.authority)
    directory = Path(p['directory'])
    reserve = old.run/'dialogue-source-card-reservation.json'
    core.require(not directory.exists() and not reserve.exists() and
                 not (old.run/'dialogue-source-card-repair.json').exists(),
                 'source-card authority was already attempted; reconcile without replay')
    live_custody()
    core.require(W.active(old, old_selection, lease)[1] == before,
                 'native lease or task state changed during source-card validation')
    return dict(schema_version=core.SCHEMA, plan=reader.plan_binding, phase='dialogue-writer',
        budget=reader.budget(), initial=binding(old.run/'initial.json'), reservation=None,
        worker_run=str(old.run), old_descriptor=p['old_descriptor'], old_snapshot=p['old_snapshot'],
        blocked=p['blocked'], predecessor=p['blocked'], old_selection=codec.encode(old_selection),
        selection=codec.encode(selected), lease=asdict(lease), tracker=checked(p['takeover'])['after'],
        prior_continuations={'lease_renewal': old.lease_renewal}, source=p['source'],
        original=before['bindings']['original_raw_source'], original_claims=claims, claims=p['corrected_claims'],
        old_draft=draft, old_check=report, author_calls=[asdict(c) for c in calls],
        author_requests=[binding(W.step_directory(old, kind)/'request.json')
                         for kind in ('dialogue-writer', 'dialogue-paraphraser')],
        source_proof=p['source_proof'], code=W.code(chosen), history=history,
        prefix=current_prefix, takeover=p['takeover'], semantic_budget_reset=False, models_dispatched=0)


def authorize(reader, *, live_custody):
    """One reservation and permit; no automatic completion or retry on failure."""
    import os
    core.require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY') != '1',
                 'read-only source probe cannot reserve remaining authoring')
    run = Path(reader.plan['worker_run'])
    with (run/'worker.lock').open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        value = proposal(reader, live_custody=live_custody)
        W = reader.worker
        claims = checked(reader.plan['corrected_claims'])
        old = reader.campaign.worker_decode(checked(reader.plan['old_descriptor'])['worker'])
        composer = reader.producer.skill_module(old.root, 'additional-assumptions-dialogue', 'compose_draft')
        template, slots = composer.compose(claims, claims['writer_pack'])
        reservation = W.keep(run/'dialogue-source-card-reservation.json', dict(schema_version=core.SCHEMA,
            plan=reader.plan_binding, directory=reader.plan['directory'], blocked=reader.plan['blocked'],
            budget=reader.budget()))
        directory = Path(reader.plan['directory']); directory.mkdir()
        target = directory/'producer/dialogue'; target.mkdir(parents=True)
        for name, payload in (('claims.json', claims), ('writer_pack.json', claims['writer_pack']),
                              ('draft_template.md', template), ('slots.json', slots)):
            W.keep(target/name, payload.encode() if type(payload) is str else payload)
        value['reservation'] = reservation
        authority = W.keep(directory/'permit.json', value)
        W.keep(run/'dialogue-source-card-repair.json', authority)
        return authority
