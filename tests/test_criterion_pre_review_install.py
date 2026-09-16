import importlib
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import criterion_pre_review_install as M


def test_selected_runtime_preserves_original_worker_boundary_and_restores_modules(monkeypatch):
    originals={k:importlib.import_module(n)for k,n in M.NAMES.items()}
    calls=[];authority={'path':'/selected','sha256':'s'}
    r=NS(authority=authority,dependencies=lambda:[],plan={'corrected_claims':{'path':'/claims','sha256':'c'}},
         is_card=lambda value:getattr(value,'dialogue_pre_review_repair',None)==authority)
    for name in ('validate','validate_task','context','historical_lease','validate_retained_request',
                 'review_projection','verify_pair','validate_review_call'):
        setattr(r,name,lambda *a,_name=name,**kw:calls.append(_name) or 'selected')
    monkeypatch.setattr(originals['q'],'validate',lambda *a,**kw:'original')
    original_globals=originals['worker']._step.__globals__;original_producer=original_globals['producer']
    with M.installed(originals,r)as selected:
        assert selected['worker']._step.__code__ is originals['worker']._step.__code__
        assert selected['worker']._step.__globals__['producer'] is selected['producer']
        assert original_globals['producer'] is original_producer
        assert selected['q'].validate(NS(dialogue_pre_review_repair=authority),{})=='selected'
        assert selected['q'].validate(NS(dialogue_pre_review_repair=None),{})=='original'
        assert selected['q'].review_projection(authority,{},'',root=None)=='selected'
        assert importlib.import_module(M.NAMES['q'])is selected['q']
    assert importlib.import_module(M.NAMES['q'])is originals['q']
    assert importlib.import_module(M.NAMES['worker'])is originals['worker']
    assert calls==['validate','review_projection']


def test_foreign_cached_module_is_rejected_before_install(monkeypatch):
    originals={k:importlib.import_module(n)for k,n in M.NAMES.items()}
    monkeypatch.setitem(__import__('sys').modules,M.NAMES['producer'],NS())
    with pytest.raises(ValueError):
        with M.installed(originals,NS(dependencies=lambda:[])):pass
