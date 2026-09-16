"""Repair traversal tests; stubbed lane bodies are not financial gate proofs."""
from types import SimpleNamespace as NS

import pytest
from scripts.batch import harbor_instruction_repair as R
from scripts.batch import harbor_completed_lanes as M
from test_pair_validation_scope import pair


def setup_prefix(pair, monkeypatch, mutate=None):
    before, content, context, leaf, builder, key, calls = pair
    before['lane_state'] = {lane: dict(state='passed', phase=None) for _, lane in M.STEPS}
    seen = []
    def gate(view, packet, ctx, **kwargs):
        seen.append(packet.kind)
        M.predecessors(view, packet.kind)
        if packet.kind not in {'source', 'segmentation'}:
            M.pair_and_audit(view, content, ctx)
            if mutate and packet.kind == 'research':
                mutate(leaf)
        phase = ('phase1_complete' if packet.kind == 'normalization_phase1'
                 else 'phase2_complete' if packet.kind == 'normalization_phase2' else None)
        return dict(passed=True, bindings={}, phase=phase)
    monkeypatch.setattr(R.notes, 'check_gate', gate)
    monkeypatch.setattr(R, 'check_completed_gate', gate)
    return before, NS(content=content), context, seen, calls


def test_repair_scope_runs_every_lane_but_reuses_only_exact_pair(pair, monkeypatch):
    before, selected, context, seen, calls = setup_prefix(pair, monkeypatch)
    first = R.prefix(before, selected, context, stop='disclosure')
    assert seen == [kind for kind, _ in M.STEPS[:7]]
    assert len(calls) == 1 and len(first) == 7
    assert M._PAIR_VALIDATION.get() is None
    R.prefix(before, selected, context, stop='disclosure')
    assert len(calls) == 2  # A separate repair must establish a new proof.


def test_source_change_between_repair_lanes_rejects_and_discards_scope(pair, monkeypatch):
    args = setup_prefix(pair, monkeypatch, lambda leaf: leaf.write_text('changed'))
    with pytest.raises(ValueError):
        R.prefix(*args[:3], stop='disclosure')
    assert args[3][-1] == 'normalization_phase1'
    assert len(args[4]) == 1 and M._PAIR_VALIDATION.get() is None


def test_substantive_lane_failure_is_not_hidden_by_pair_reuse(pair, monkeypatch):
    args = setup_prefix(pair, monkeypatch)
    original = R.check_completed_gate
    def failed(view, packet, context, **kwargs):
        result = original(view, packet, context, **kwargs)
        if packet.kind == 'source_profile':
            result['passed'] = False
        return result
    monkeypatch.setattr(R, 'check_completed_gate', failed)
    with pytest.raises(ValueError, match='source_profile'):
        R.prefix(*args[:3], stop='disclosure')
    assert len(args[4]) == 1 and M._PAIR_VALIDATION.get() is None
