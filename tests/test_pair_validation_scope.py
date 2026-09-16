import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from scripts.batch import harbor_completed_lanes as M
from scripts.batch.harbor_admission import record
from scripts.batch import harbor_tracker_lifecycle as L


@pytest.fixture
def pair(tmp_path, monkeypatch):
    bindings = {}
    for kind in ('source', 'segmentation'):
        root = tmp_path / kind
        root.mkdir()
        (root / 'generation-manifest.json').write_text('{}')
        bindings[kind + '_generation'] = {'path': str(root), 'id': kind}
    leaf = tmp_path / 'source' / 'source.csv'
    leaf.write_text('a,b\n1,2\n')
    raw = record(leaf)
    bindings.update(original_raw_source=raw, source_file=raw, curation=raw, audit=raw)
    key = tmp_path / 'key.pem'; key.write_text('selected public key')
    builder = tmp_path / 'builder.py'; builder.write_text('builder')
    import xl_source_publication
    monkeypatch.setattr(xl_source_publication, '_default_builder_paths', lambda: [builder])
    calls = []
    def validate(*args):
        calls.append(True)
        return [raw]
    monkeypatch.setattr(M, '_pair_and_audit_uncached', validate)
    before = dict(workbook_id='0001', raw_source=raw, bindings=copy.deepcopy(bindings))
    selected = NS(bindings=bindings, normalization_records=())
    context = M.notes.RootContext(tmp_path, str(key))
    return before, selected, context, leaf, builder, key, calls


def test_scoped_reuse_keeps_evidence_and_fresh_invocations(pair):
    before, s, c, *_, calls = pair
    with M.pair_validation_scope():
        first = M.pair_and_audit(before, s, c)
        first.clear()  # Returned lists cannot alter the stored proof.
        second = M.pair_and_audit(before, s, c)
        assert second and len(calls) == 1
    with M.pair_validation_scope():
        M.pair_and_audit(before, s, c)
    M.pair_and_audit(before, s, c)
    assert len(calls) == 3


@pytest.mark.parametrize('changed', ['leaf', 'builder', 'key', 'extra', 'removed', 'binding', 'raw', 'normalizations'])
def test_changes_reject_reuse(pair, changed):
    before, s, c, leaf, builder, key, calls = pair
    with M.pair_validation_scope():
        M.pair_and_audit(before, s, c)
        if changed in ('leaf', 'builder', 'key'):
            {'leaf': leaf, 'builder': builder, 'key': key}[changed].write_text('changed')
        elif changed == 'extra': (leaf.parent / 'injected.csv').write_text('extra')
        elif changed == 'removed': leaf.unlink()
        elif changed == 'binding': s.bindings['audit'] = {'path': 'different'}
        elif changed == 'raw': before['raw_source'] = {'path': 'different'}
        else: s.normalization_records = ({'path': 'different'},)
        with pytest.raises((ValueError, OSError)):
            M.pair_and_audit(before, s, c)
    assert len(calls) == 1


def test_exception_never_leaves_a_reusable_scope(pair):
    before, s, c, *_, calls = pair
    with pytest.raises(RuntimeError):
        with M.pair_validation_scope():
            M.pair_and_audit(before, s, c)
            raise RuntimeError('interrupted')
    M.pair_and_audit(before, s, c)
    assert len(calls) == 2


def test_rejected_original_validator_never_caches(pair, monkeypatch):
    before, s, c, *_ = pair
    calls = []
    def failed(*args):
        calls.append(True)
        raise ValueError('substantive failure')
    monkeypatch.setattr(M, '_pair_and_audit_uncached', failed)
    with M.pair_validation_scope():
        for _ in range(2):
            with pytest.raises(ValueError, match='substantive failure'):
                M.pair_and_audit(before, s, c)
    assert len(calls) == 2


@pytest.mark.parametrize('target', ['builder', 'key'])
def test_first_fill_rejects_external_input_change_during_validation(pair, monkeypatch, target):
    before, s, c, leaf, builder, key, _ = pair
    def racing(*args):
        {'builder': builder, 'key': key}[target].write_text('changed during validation')
        return [record(leaf)]
    monkeypatch.setattr(M, '_pair_and_audit_uncached', racing)
    with M.pair_validation_scope():
        with pytest.raises(ValueError):
            M.pair_and_audit(before, s, c)


def test_unused_absent_signer_is_not_required_but_appearance_rejected(pair):
    before, s, c, leaf, builder, key, _ = pair
    key.unlink()
    with M.pair_validation_scope():
        M.pair_and_audit(before, s, c)
        M.pair_and_audit(before, s, c)
        key.write_text('new signer')
        with pytest.raises(ValueError, match='signer availability'):
            M.pair_and_audit(before, s, c)
