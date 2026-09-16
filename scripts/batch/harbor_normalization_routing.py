"""Prospective normalization routing without altering consumed review budgets.

Only the producer's complete-context size guard selects the existing closed
partition protocol. The original evidence, logical budget, and delegation are
retained by that protocol. Existing journals are inspected, never redispatched;
an incomplete response therefore needs explicit reconciliation outside this
helper. This module does not interpret or combine reviewer decisions itself.

One new physical Call is permitted per invocation; completed progress uses the
existing PENDING_MODEL boundary. The owning 3300-second/8-GiB guard is unchanged.
Global-slot waiting remains inside that guard. A deadline during a queued or
active Call is an explicit reconciliation hold, never permission to resubmit.
"""
from dataclasses import dataclass
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_compact as K
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_audit_bridge import AuditTransportStop


@dataclass(frozen=True)
class Production:
    prepared: dict
    result: dict
    call: object = None
    partitioned: bool = False


PROGRESS_SCHEMA = 'harbor-normalization-partition-progress/v1'


@dataclass(frozen=True)
class Progress:
    prepared: dict
    receipt: dict


def progress_receipt(prepared, index, ordinal, reviewed):
    """Seal a completed physical Call, without claiming phase-one approval."""
    m = read(prepared)
    value = dict(schema_version=PROGRESS_SCHEMA, prepared=prepared,
                 index=index, ordinal=ordinal, outcome=reviewed['outcome'],
                 valid=reviewed['valid'], logical_limit=2,
                 semantic_budget_reset=False, lane_approval=False,
                 automatic_retry=False)
    root = E.safe(m['root']) / 'progress'
    root.mkdir(exist_ok=True, mode=0o700)
    return Progress(prepared, N.keep(root / ('partition-%04d-round-%d.json' % (index, ordinal)), value))


def check_progress(binding):
    """Cheap boundary inspection; the next guarded step rechecks full context."""
    value = read(binding)
    require(set(value) == {'schema_version','prepared','index','ordinal','outcome',
            'valid','logical_limit','semantic_budget_reset','lane_approval','automatic_retry'}
            and value['schema_version'] == PROGRESS_SCHEMA, 'invalid partition progress')
    require(type(value['valid']) is bool and value['logical_limit'] == 2
            and value['semantic_budget_reset'] is False and value['lane_approval'] is False
            and value['automatic_retry'] is False, 'partition progress changes authority')
    pc = P._prepared_call(value['prepared'], value['index'], value['ordinal'])
    m = read(value['prepared']); c = m['campaign']
    from pathlib import Path
    campaign = E.Campaign(Path(c['root']), Path(c['slot_root']), c['model_slots'])
    require(value['outcome'] == record(E.location(pc, campaign) / 'outcome.json'),
            'partition progress detached from completed Call')
    outcome = read(value['outcome'])
    require(outcome['status'] in {'completed','response_rejected'},
            'uncertain partition cannot authorize continuation')
    require(Path(binding['path']) == Path(m['root']) / 'progress' /
            ('partition-%04d-round-%d.json' % (value['index'], value['ordinal'])),
            'partition progress namespace differs')
    return value


def partition_root(selection):
    return selection.run.parent / (selection.workbook + '-normalization-partitions')


def _partition_review(prepared, index, ordinal, campaign, rows, *, dispatch, credential):
    pc = P._prepared_call(prepared, index, ordinal)
    journal = E.location(pc, campaign)
    submitted = not journal.exists()
    if submitted:
        if not dispatch:
            return None
        P.dispatch(prepared, index, ordinal, credential=credential)
    # Even an empty or partly written journal is not authority to repeat a call.
    observed = E.inspect(pc, campaign)
    if observed.status not in {'completed', 'response_rejected'}:
        raise AuditTransportStop('normalization partition transport ' + observed.status)
    reviewed = P._review(prepared, index, ordinal, campaign, rows)
    if submitted:
        # Always yield after one new completed Call, including the final Call.
        # Aggregation/emission therefore receives its own ordinary stage guard.
        return progress_receipt(prepared, index, ordinal, reviewed)
    return reviewed


def _partitioned(selection, campaign, prepared, *, dispatch, credential):
    manifest, selected, bound_campaign, _, rows = P._current(prepared, allow_emitted=True)
    require(selected == selection and bound_campaign == campaign,
            'normalization routing selection or campaign changed')
    # The protocol requires every first-round review before any second round.
    first = []
    for index in range(len(manifest['partitions'])):
        reviewed = _partition_review(prepared, index, 1, campaign, rows,
                                     dispatch=dispatch, credential=credential)
        if reviewed is None:
            return None
        if isinstance(reviewed, Progress):
            return reviewed
        first.append(reviewed)
    for index, reviewed in enumerate(first):
        if reviewed['valid']:
            continue
        corrected = _partition_review(prepared, index, 2, campaign, rows,
                                      dispatch=dispatch, credential=credential)
        if corrected is None:
            return None
        if isinstance(corrected, Progress):
            return corrected
        require(corrected['valid'], 'normalization partition exhausted original two-round review cap')
    # Owning aggregation rechecks exact coverage, source meaning, chronology,
    # independence, all consumed calls, and the original logical budget.
    return Production(prepared, P.emit_phase1(prepared), partitioned=True)


def produce(selection, campaign, *, dispatch=False, credential=None):
    """Return phase-one evidence, or None when an unsubmitted call is pending."""
    root = partition_root(selection)
    if root.exists():
        # A partially created partition plan cannot silently fall back to the
        # original request or recreate a new namespace after an uncertain stop.
        require((root / 'prepared.json').is_file(),
                'interrupted normalization partition preparation requires reconciliation')
        return _partitioned(selection, campaign, record(root / 'prepared.json'),
                            dispatch=dispatch, credential=credential)
    try:
        prepared = N.prepare(selection)
        # Byte size alone can admit hundreds of requested decisions. Preserve
        # the original unsubmitted request and choose complete bounded batches
        # before dispatch. P.prepare rejects any already consumed old scope.
        if len(read(prepared)['candidate_references']) > K.MAX_REFERENCES:
            raise K.RequestTooLarge('normalization reference count requires complete bounded partitions')
    except K.RequestTooLarge:
        prepared = P.prepare(selection, campaign, root)
        return _partitioned(selection, campaign, prepared,
                            dispatch=dispatch, credential=credential)
    call = None
    if read(prepared)['request'] is not None:
        pc = N.prepared_call(selection, prepared)
        if not dispatch and not E.location(pc, campaign).exists():
            return None
        observed = (N.dispatch(selection, prepared, campaign, credential=credential)
                    if dispatch else E.inspect(pc, campaign))
        if observed.status != 'completed':
            raise AuditTransportStop('normalization transport ' + observed.status)
        call = observed.call
    return Production(prepared, N.emit_phase1(selection, prepared, call), call)
