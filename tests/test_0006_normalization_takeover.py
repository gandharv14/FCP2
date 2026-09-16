import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.fleet import build_0006_normalization_takeover as B
from scripts.fleet import normalization_timeout_0006 as N

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT/'diagnostics/vm-fleet-20260912/control/0006-research-continuation-001/takeover-reader-001/takeover.py'


def definitions(text):
    return {x.name: ast.dump(x) for x in ast.parse(text).body if isinstance(x, (ast.FunctionDef, ast.ClassDef))}


def test_exact_parent_preserves_all_lifecycle_mutators_and_original_timeout_reader():
    original = PARENT.read_text()
    derived = B.rendered(original, {'path': '/tmp/helper', 'sha256': 'a'*64}, {'path': '/tmp/profile', 'sha256': 'b'*64})
    before, after = definitions(original), definitions(derived)
    assert set(after)-set(before) == {'_fleet_normalization_reader'}
    assert [name for name in before if before[name] != after[name]] == ['_recipe']
    for name in ('replace_expired', 'recover', '_seal', '_prior_chain', '_lock_paths', 'validate_completion'):
        assert before[name] == after[name]
    assert "MAX_REPLACEMENTS=3" in derived and "MAX_DURATION_NS=7200*10**9" in derived
    assert before['_fleet_retained_walltime'] == after['_fleet_retained_walltime']


def test_arbitrary_rehashed_parent_changes_rejected():
    with pytest.raises(ValueError, match='exact retained'):
        B.rendered(PARENT.read_text().replace('MAX_REPLACEMENTS=3', 'MAX_REPLACEMENTS=4'), {}, {})


@pytest.mark.parametrize('field', ['workbook', 'worker_kind', 'guard', 'tracker', 'checkpoint', 'config',
                                 'prior_completion', 'duration_ns'])
def test_closed_selection_rejects_other_guard_lane_budget_and_lineage(monkeypatch, field):
    row = {'path': '/original', 'sha256': 'a'*64}
    p = dict(guard=row, tracker=row, checkpoint=row, config=row, prior_takeover=row)
    monkeypatch.setattr(N, 'profile', lambda _: p)
    monkeypatch.setattr(N, 'read', lambda _: {'returncode': None})
    selection = SimpleNamespace(workbook='0006', worker_kind='environment', guard=row, tracker=row,
        checkpoint=row, config=row, prior_completion=row, duration_ns=7200*10**9)
    assert N.allowed(selection, {'returncode': None}, {})
    setattr(selection, field, 'different')
    with pytest.raises(ValueError, match='third replacement'):
        N.allowed(selection, {'returncode': None}, {})


def test_terminal_http_or_unrelated_timeout_body_is_not_accepted(monkeypatch):
    row = {'path': '/original', 'sha256': 'a'*64}
    monkeypatch.setattr(N, 'profile', lambda _: dict(guard=row, tracker=row, checkpoint=row, config=row, prior_takeover=row))
    monkeypatch.setattr(N, 'read', lambda _: {'returncode': None, 'failure': 'wall_time'})
    selection = SimpleNamespace(workbook='0006', worker_kind='environment', guard=row, tracker=row,
        checkpoint=row, config=row, prior_completion=row, duration_ns=7200*10**9)
    for body in ({'returncode': 1}, {'returncode': None, 'failure': 'uncertain_model'}, {'returncode': 400}):
        with pytest.raises(ValueError):
            N.allowed(selection, body, {})


def test_profile_shape_and_pinned_proof_reject_relabelled_validation(monkeypatch):
    p = {k: None for k in N.FIELDS}
    p.update(schema_version=N.SCHEMA, workbook='0006', source_sha256=N.SOURCE, version=N.VERSION,
             guard={'sha256': N.GUARD}, tracker={'sha256': N.TRACKER},
             normalization_proof={'sha256': N.PROOF}, semantic_budget_reset=False, expired_replacement_limit=3)
    for key, value in [('normalization_proof', {'sha256': 'f'*64}), ('semantic_budget_reset', True),
                       ('expired_replacement_limit', 4), ('workbook', '0270')]:
        changed = copy.deepcopy(p); changed[key] = value
        monkeypatch.setattr(N, 'read', lambda _, value=changed: value)
        with pytest.raises(ValueError, match='only the selected'):
            N.profile({})


def test_environment_coordinator_uses_its_actual_literal_descriptor_schema():
    from scripts import harbor_campaign as C
    assert not hasattr(C, 'SCHEMA')
    tree = ast.parse(Path(N.__file__).read_text())
    function = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'external_environment')
    assert not any(isinstance(x, ast.Attribute) and x.attr == 'SCHEMA' for x in ast.walk(function))
    assert any(isinstance(x, ast.Constant) and x.value == 'harbor-environment-coordinator/v1' for x in ast.walk(function))


def test_historical_normalized_witness_retains_original_hash_and_rejects_substitution(tmp_path):
    import json
    original=tmp_path/'normalized.json';original.write_text('{"phase":1}')
    original_binding=N.binding(original)
    witness=tmp_path/'normalized.phase1.json';witness.write_bytes(original.read_bytes())
    result=tmp_path/'phase1-result.json';result.write_text(json.dumps(dict(status='phase1_evidence_ready',phase1=N.binding(witness))))
    proof=dict(phase1=N.binding(result),normalized=original_binding)
    assert N.normalized_witness(proof)==N.binding(witness)
    original.write_text('{"phase":2}')
    assert N.normalized_witness(proof)['sha256']==original_binding['sha256']
    witness.write_bytes(original.read_bytes())
    with pytest.raises(ValueError):N.normalized_witness(proof)


def test_external_witness_path_cannot_replace_original_producer_copy(tmp_path):
    import json
    f=tmp_path/'arbitrary.json';f.write_text('{}');r=tmp_path/'phase1-result.json'
    r.write_text(json.dumps(dict(status='phase1_evidence_ready',phase1=N.binding(f))))
    with pytest.raises(ValueError):N.normalized_witness(dict(phase1=N.binding(r),normalized=dict(path=str(tmp_path/'normalized.json'),sha256=N.binding(f)['sha256'])))
