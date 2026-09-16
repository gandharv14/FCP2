"""Filesystem reservation tests; financial/prefix checks are not fixture passes."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_producer as P
from scripts.fleet import source_card_authorize as A
from scripts.fleet.source_card_reader import binding
from test_source_card_authoring import BUDGET
from test_harbor_instruction_producer import setup, dialogue_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('kind', ['writer2-partial', 'paraphraser2-partial', 'review1-partial', 'review-call', 'round2', 'future-runtime'])
def test_unspent_rejects_preparation_even_without_call_or_completed_directory(tmp_path, kind):
    run = tmp_path/'instruction'; original = run/'producer/dialogue'; original.mkdir(parents=True)
    if kind.endswith('2-partial'):
        name = 'dialogue-writer' if kind.startswith('writer') else 'dialogue-paraphraser'
        P.put(run/'producer/requests'/name/'attempt-01/context.json', {'kind': name, 'ordinal': 1, 'limit': 2})
        (run/'producer/requests'/name/'attempt-02').mkdir()
    elif kind == 'review1-partial': (run/'producer/requests/dialogue-review').mkdir(parents=True)
    elif kind == 'review-call': P.put(original/'review-call-01.json', {})
    elif kind == 'round2': (original/'round-02').mkdir()
    else: (run/'steps/final-runtime').mkdir(parents=True)
    with pytest.raises(ValueError): A._unspent(SimpleNamespace(run=run), P, original)


def test_partial_reservation_is_not_replayed(setup, monkeypatch):
    task, campaign, respond, sent = dialogue_fixture(setup)
    directory, claims, _, _ = P._dialogue(task, 1, ROOT)
    run = task.run/'reservation-fixture'; run.mkdir(); (run/'worker.lock').touch()
    target = run/'source-card-authoring-001'
    descriptor = P.put(run/'fixture-descriptor.json', {'worker': {}})
    plan = dict(worker_run=str(run), directory=str(target), blocked=P.put(run/'blocked.json', {}),
                corrected_claims=binding(directory/'claims.json'), old_descriptor=descriptor)
    plan_binding = P.put(run/'fixture-plan.json', plan)
    called = []
    def checked_proposal(reader, *, live_custody):
        live_custody(); called.append('fixture validation')
        assert not (run/'dialogue-source-card-reservation.json').exists()
        return {'fixture_only': True, 'reservation': None}
    monkeypatch.setattr(A, 'proposal', checked_proposal)
    def fail_permit(path, value):
        if Path(path).name == 'permit.json': raise RuntimeError('fixture interrupted after reservation')
        return P.put(path, value)
    reader = SimpleNamespace(plan=plan, plan_binding=plan_binding, worker=SimpleNamespace(keep=fail_permit),
        producer=P, campaign=SimpleNamespace(worker_decode=lambda _: SimpleNamespace(root=ROOT)),
        budget=lambda: BUDGET)
    with pytest.raises(RuntimeError): A.authorize(reader, live_custody=lambda: None)
    reservation = binding(run/'dialogue-source-card-reservation.json')
    assert not (target/'permit.json').exists() and not (run/'dialogue-source-card-repair.json').exists()
    with pytest.raises(AssertionError): A.authorize(reader, live_custody=lambda: None)
    assert binding(reservation['path']) == reservation and not sent


def test_readonly_cannot_reach_proposal_or_reservation(monkeypatch):
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY', '1')
    monkeypatch.setattr(A, 'proposal', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not validate owning proposal')))
    with pytest.raises(ValueError, match='read-only'):
        A.authorize(None, live_custody=lambda: None)
