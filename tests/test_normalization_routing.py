"""Real partition/executor/emission checks with synthetic HTTP responses only."""
from dataclasses import asdict, dataclass
import urllib.error
import uuid
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_normalization_routing as R
from scripts.batch import harbor_normalization_producer as N
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_normalization_compact as K
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read, record
from scripts.batch.harbor_audit_bridge import AuditTransportStop
from test_harbor_normalization_producer import setup, mock_review
from test_normalization_partitioned import respond


def oversize(monkeypatch):
    monkeypatch.setattr(N, 'MAX_CONTEXT_BYTES', 1)
    monkeypatch.setattr(K, 'MAX_REFERENCES', 2)


def finish(s, campaign, *, credential='synthetic'):
    for _ in range(10):
        result = R.produce(s, campaign, dispatch=True, credential=credential)
        if isinstance(result, R.Progress):
            R.check_progress(result.receipt)
        else:
            return result
    pytest.fail('partition protocol did not finish within fixture bound')


def test_small_request_retains_ordinary_protocol(setup, monkeypatch):
    s, campaign, _, _ = setup()
    mock_review(monkeypatch, s)
    output = finish(s, campaign)
    assert not output.partitioned and output.call is not None
    assert read(output.prepared)['request'] is not None
    assert not R.partition_root(s).exists()
    assert read(output.result)['status'] == 'phase1_evidence_ready'


def test_candidate_count_routes_complete_small_body_before_ordinary_dispatch(setup, monkeypatch):
    s, campaign, _, _ = setup()
    monkeypatch.setattr(K, 'MAX_REFERENCES', 2)
    calls = []; respond(monkeypatch, s, calls=calls)
    output = finish(s, campaign)
    manifest = read(output.prepared)
    assert output.partitioned and len(calls) == 2
    assert all(len(refs) <= 2 for refs in calls)
    assert sum(map(len, calls)) == len(manifest['full_candidate_references']) == 4
    assert (s.run / 'review-request.json').is_file()
    assert (s.run / 'prepared.json').is_file()
    assert not E.location(N.prepared_call(s, record(s.run / 'prepared.json')), campaign).exists()
    assert manifest['logical_limit'] == 2 and manifest['semantic_budget_reset'] is False


def test_full_coverage_original_budget_and_evidence_survive_routing(setup, monkeypatch):
    s, campaign, _, _ = setup(); oversize(monkeypatch)
    assert R.produce(s, campaign) is None
    prepared = record(R.partition_root(s) / 'prepared.json')
    manifest = read(prepared)
    retained = {name: N.raw(binding) for name, binding in manifest['legacy'].items()}
    calls = []; respond(monkeypatch, s, calls=calls)
    output = finish(s, campaign)
    assert output.partitioned and output.call is None and output.prepared == prepared
    assert set(r for part in calls for r in part) == set(manifest['full_candidate_references'])
    assert len(calls) == 2 and sum(map(len, calls)) == len(manifest['full_candidate_references'])
    assert manifest['original_budget'] == K.json_value(asdict(s.budget))
    assert manifest['logical_limit'] == 2 and manifest['semantic_budget_reset'] is False
    for index in range(2):
        pc = P._prepared_call(prepared, index, 1)
        assert pc.attempt_id == str(uuid.uuid5(uuid.UUID(s.budget.attempt_id), f'{index}:1'))
        assert pc.ordinal == 1 and pc.limit == 2
    assert not (campaign.root / 'attempt-identities' / (s.budget.attempt_id + '.json')).exists()
    assert all(N.raw(manifest['legacy'][name]) == value for name, value in retained.items())
    monkeypatch.setattr(E, '_open', lambda *a, **k: pytest.fail('completed routing cannot make another call'))
    assert R.produce(s, campaign, dispatch=True, credential='synthetic') == output


def test_invalid_first_review_waits_for_all_first_then_only_targeted_second(setup, monkeypatch):
    s, campaign, _, _ = setup(); oversize(monkeypatch)
    calls = []
    respond(monkeypatch, s, invalid=lambda refs: len(calls) == 1, calls=calls)
    output = finish(s, campaign)
    assert len(calls) == 3 and calls[0] == calls[2] and calls[0] != calls[1]
    proof = read(read(output.result)['normalization_partitioned_review'])
    assert proof['semantic_attempt'] == 2
    assert [p['ordinal'] for p in proof['partitions']] == [1, 2, 1]
    assert proof['partitions'][0]['valid'] is False


def test_uncertain_call_never_replayed_or_followed_by_another_partition(setup, monkeypatch):
    s, campaign, _, _ = setup(); oversize(monkeypatch)
    calls = []
    def uncertain(*args, **kwargs):
        calls.append(True)
        raise urllib.error.URLError('synthetic uncertain response')
    monkeypatch.setattr(E, '_open', uncertain)
    for _ in range(2):
        with pytest.raises(AuditTransportStop, match='incomplete'):
            R.produce(s, campaign, dispatch=True, credential='synthetic')
    assert calls == [True]
    prepared = record(R.partition_root(s) / 'prepared.json')
    assert not E.location(P._prepared_call(prepared, 1, 1), campaign).exists()
    assert not E.location(P._prepared_call(prepared, 0, 2), campaign).exists()


def test_exhausted_partition_keeps_consumed_reviews_and_never_starts_third(setup, monkeypatch):
    s, campaign, _, _ = setup(); oversize(monkeypatch)
    calls = []; respond(monkeypatch, s, invalid=lambda refs: True, calls=calls)
    for _ in range(2):
        with pytest.raises(ValueError, match='original two-round'):
            finish(s, campaign)
    assert len(calls) == 3
    assert not (s.run / 'normalized.phase1.json').exists()


def test_non_size_failure_does_not_choose_new_protocol(setup, monkeypatch):
    s, campaign, _, _ = setup()
    monkeypatch.setattr(N, 'prepare', lambda s: (_ for _ in ()).throw(ValueError('source evidence changed')))
    with pytest.raises(ValueError, match='source evidence changed'):
        R.produce(s, campaign, dispatch=True, credential='synthetic')
    assert not R.partition_root(s).exists()


def test_prior_original_submission_cannot_be_partitioned(setup, monkeypatch):
    s, campaign, _, _ = setup(); prepared = N.prepare(s); mock_review(monkeypatch, s)
    N.dispatch(s, prepared, campaign, credential='synthetic')
    monkeypatch.setattr(N, 'prepare', lambda s: (_ for _ in ()).throw(K.RequestTooLarge('bounded request')))
    with pytest.raises(ValueError, match='already submitted'):
        R.produce(s, campaign, dispatch=True, credential='synthetic')


def test_partial_plan_requires_reconciliation(setup):
    s, campaign, _, _ = setup(); R.partition_root(s).mkdir(parents=True)
    with pytest.raises(ValueError, match='preparation requires reconciliation'):
        R.produce(s, campaign)


def test_unsplittable_source_context_is_retained_without_call(setup, monkeypatch):
    s, campaign, _, _ = setup(); oversize(monkeypatch)
    monkeypatch.setattr(K, 'MAX_REQUEST_BYTES', 1)
    monkeypatch.setattr(E, '_open', lambda *a, **k: pytest.fail('oversized singleton must remain held'))
    with pytest.raises(ValueError, match='one complete candidate context exceeds'):
        R.produce(s, campaign, dispatch=True, credential='synthetic')
    assert (s.run / 'review-evidence.json').is_file()
    assert (s.run / 'draft.json').is_file()
    assert not (campaign.root / 'attempt-identities').exists()
    assert not (s.run / 'normalized.phase1.json').exists()


def test_worker_binding_passes_independent_owning_projection(setup, monkeypatch):
    from scripts.batch import harbor_environment_worker as W
    from scripts.batch.harbor_completed_lanes import normalization_projection
    s, campaign, _, _ = setup(); oversize(monkeypatch); respond(monkeypatch, s)
    @dataclass
    class Content:
        workbook: str
        bindings: dict
        normalization_run: object
        model_policy: object
        normalization_prepared: object = None
        normalization_call: object = None
    content = Content(s.workbook, s.bindings, s.run, s.policy)
    worker = SimpleNamespace(normalization_budget=s.budget, campaign=campaign)
    for _ in range(3):
        result = W._normalization(worker, content, dispatch=True, credential='synthetic')
        if not isinstance(result, R.Progress):break
    selected, result = result
    assert 'normalization_partitioned_review' in selected.bindings
    assert 'normalization_prepared_request' not in selected.bindings
    assert selected.normalization_call is None
    assert normalization_projection(selected, phase1=True)['semantic_attempt'] == 1
