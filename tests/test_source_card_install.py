"""Explicit namespace composition, preserving every original module global."""
import importlib
import sys
import pytest
from scripts.fleet import source_card_install as A
from test_source_card_worker import prepared, case, instruction_setup


def test_complete_cold_namespace_routes_owning_readiness_and_restores_originals(case, monkeypatch):
    worker, selection, lease, _, authority, _, reader, _ = prepared(case, monkeypatch)
    reader.authority = authority
    originals = {key: importlib.import_module(name) for key, name in A.NAMES.items()}
    old_globals = {key: dict(module.__dict__) for key, module in originals.items()}
    try:
        selected = A.install(originals, reader)
        assert reader.worker is selected['worker'] and reader.current_renewal is selected['renewal']
        for key, name in A.NAMES.items():
            assert sys.modules[name] is selected[key]
            assert originals[key].__dict__ == old_globals[key]
        assert selected['worker']._model.__globals__['producer'] is selected['producer']
        assert selected['worker']._model.__globals__['complete_readiness'] is selected['complete'].complete_readiness
        assert selected['complete'].check_dialogue.__globals__['dialogue_review'] is selected['semantic'].dialogue_review
        assert selected['complete'].complete_readiness.__globals__['static'] is selected['static']
        assert selected['campaign']._step.__globals__['worker'] is selected['worker']
        with pytest.raises(ValueError, match='cached'):
            A.install(originals, reader)
    finally:
        for key, name in A.NAMES.items():
            package, leaf = name.rsplit('.', 1)
            sys.modules[name] = originals[key]
            setattr(sys.modules[package], leaf, originals[key])


def test_missing_module_inventory_is_rejected_before_publication(case, monkeypatch):
    *_, reader, verified = prepared(case, monkeypatch)
    originals = {key: importlib.import_module(name) for key, name in A.NAMES.items()}
    with pytest.raises(ValueError, match='complete declared'):
        A.install({key: value for key, value in originals.items() if key != 'complete'}, reader)
    assert all(sys.modules[name] is originals[key] for key, name in A.NAMES.items())
