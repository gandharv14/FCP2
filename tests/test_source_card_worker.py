"""Actual fixed worker step composition; no real lease/model/task admission."""
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.batch import harbor_instruction_worker as W
from scripts.batch.harbor_admission import record, read
from scripts.fleet import source_card_worker as adapter, source_card_authoring as core
from test_instruction_worker import case, instruction_setup


def prepared(case, monkeypatch):
    worker, selection, old_lease, *_ = case
    def old_operation(worker, s, lease, phase, directory, **options):
        return s, lease, 'DIALOGUE_REPAIR_REQUIRED' if phase == 'dialogue-paraphraser' else None
    monkeypatch.setattr(W, '_operation', old_operation)
    prior = W.run(worker, selection, old_lease)
    assert prior.status == 'DIALOGUE_REPAIR_REQUIRED'
    old_files = {str(p): p.read_bytes() for p in worker.run.rglob('*') if p.is_file()}
    home = worker.run/'source-card-authoring-001'
    claims = W.keep(home/'corrected-claims.json', {'fixture_only': True})
    selection = replace(selection, content=replace(selection.content, dialogue_claims=claims))
    lease = replace(old_lease, token='different-takeover-token', revision=old_lease.revision+1)
    authority = W.keep(home/'permit.json', dict(schema_version=core.SCHEMA,
        selection=W.codec.encode(selection), lease=asdict(lease), predecessor=prior.evidence))
    renewal = W.keep(home/'fixture-prior-renewal.json', dict(fixture_only=True))
    worker = replace(worker, dialogue_pre_review_repair=authority, lease_renewal=renewal)
    verified = []

    class Reader:
        plan = {'directory': str(home)}
        def is_card(self, value): return value.dialogue_pre_review_repair == authority
        def dependencies(self): return [record(adapter.__file__), record(core.__file__)]
        def validate(self, value, before):
            assert self.is_card(value)
            verified.append('source/old-calls/current-takeover fixture')
        def worker_renewal(self, value, before):
            assert value.lease_renewal == renewal
            verified.append('old-renewal fixture')
        def owned(self): return read(authority)

    return worker, selection, lease, old_lease, authority, old_files, Reader(), verified


def install(reader, monkeypatch, operation):
    monkeypatch.setattr(W, '_operation', operation)
    original = dict(W.__dict__)
    selected = adapter.install(W, reader, W.producer)
    assert W.__dict__ == original
    for name in ('step', '_model', '_guarded', '_operation', 'active', 'request_saved'):
        assert getattr(selected, name).__code__ is getattr(W, name).__code__
    return selected


def test_actual_worker_keeps_takeover_lease_across_pending_and_executes_both_authors(case, monkeypatch):
    worker, s, lease, old, authority, files, reader, verified = prepared(case, monkeypatch)
    seen = []
    def operation(worker, selected, current, phase, directory, **options):
        seen.append((phase, current, options['models']))
        assert directory == Path(reader.plan['directory'])/'steps'/phase
        return selected, current, None if options['models'] else 'MODEL_REQUIRED'
    selected = install(reader, monkeypatch, operation)
    a = selected.step(worker, s, lease, checkpoint=authority, models=False)
    b = selected.step(worker, s, lease, checkpoint=a.evidence, models=False)
    c = selected.step(worker, s, lease, checkpoint=b.evidence, models=True)
    assert (a.status, b.status, c.status, c.next_step) == ('MODEL_REQUIRED', 'MODEL_REQUIRED', 'STEP_COMPLETE', 'dialogue-paraphraser')
    d = selected.step(worker, c.selection, c.lease, checkpoint=c.evidence, models=False)
    e = selected.step(worker, d.selection, d.lease, checkpoint=d.evidence, models=True)
    assert e.status == 'STEP_COMPLETE' and e.next_step == 'dialogue-review'
    assert [x[0] for x in seen] == ['dialogue-writer']*3 + ['dialogue-paraphraser']*2
    assert all(current == lease for _, current, _ in seen) and verified
    assert read(a.evidence)['intent'] == read(b.evidence)['intent'] == read(c.evidence)['intent']
    assert all(Path(path).read_bytes() == raw for path, raw in files.items())
    # Actual task inventory remains exact; the runtime adapter is independently
    # pinned in its permit/guarded-child sidecar and new request dependencies.
    assert selected.code(worker) == W.code(worker)


@pytest.mark.parametrize('fault', ['old-lease', 'old-selection', 'foreign-previous', 'changed-pending-lease', 'blocked', 'exception', 'skip-author'])
def test_pending_or_old_checkpoint_cannot_bypass_source_card_boundary(case, monkeypatch, fault):
    worker, s, lease, old, authority, files, reader, _ = prepared(case, monkeypatch)
    count = []
    def operation(worker, selected, current, phase, directory, **options):
        count.append(phase)
        return selected, current, 'MODEL_REQUIRED'
    selected = install(reader, monkeypatch, operation)
    first = selected.step(worker, s, lease, checkpoint=authority)
    checkpoint, supplied, current = first.evidence, s, lease
    if fault == 'old-lease': current = old
    elif fault == 'old-selection': supplied = replace(s, content=replace(s.content, dialogue_claims=None))
    elif fault == 'foreign-previous':
        value = read(first.evidence); intent = read(value['intent'])
        intent['previous'] = read(authority)['predecessor']
        Path(value['intent']['path']).write_bytes(W.producer.canonical(intent))
        value['intent'] = record(value['intent']['path'])
        Path(checkpoint['path']).write_bytes(W.producer.canonical(value))
        checkpoint = record(checkpoint['path'])
    elif fault == 'changed-pending-lease':
        value = read(checkpoint); value['lease'] = asdict(old)
        Path(checkpoint['path']).write_bytes(W.producer.canonical(value)); checkpoint = record(checkpoint['path'])
    elif fault in ('blocked', 'exception'):
        W.keep(selected.step_directory(worker, 'dialogue-writer')/(fault+'.json'), {'failed': True})
    if fault in ('blocked', 'exception'):
        result = selected.step(worker, supplied, current, checkpoint=checkpoint)
        assert result.status == 'RECONCILIATION_REQUIRED'
    else:
        with pytest.raises((ValueError, AssertionError)):
            selected.step(worker, supplied, current, checkpoint=checkpoint,
                          phase='dialogue-review' if fault == 'skip-author' else None)
    assert count == ['dialogue-writer']


def test_ordinary_worker_does_not_use_source_reader(case, monkeypatch):
    worker, s, lease, *_ = case
    class Reader:
        def is_card(self, value): return False
        def dependencies(self): raise AssertionError('ordinary authority changed')
    seen = []
    monkeypatch.setattr(W, '_operation', lambda worker, s, lease, phase, directory, **kw:
                        (seen.append(phase) or s, lease, 'MODEL_REQUIRED'))
    selected = adapter.install(W, Reader(), W.producer)
    assert selected.step(worker, s, lease).status == 'MODEL_REQUIRED'
    assert seen == ['disclosure-build'] and selected.code(worker) == W.code(worker)
