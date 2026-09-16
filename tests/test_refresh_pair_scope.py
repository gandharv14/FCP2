"""Scope wiring tests; synthetic gate bodies are not workbook quality proofs."""
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import hashlib
import pytest
from scripts.fleet import refresh_pair_scope as S


@pytest.fixture
def selected(tmp_path):
    lane = tmp_path / 'harbor_completed_lanes.py'
    lane.write_text('''from contextlib import contextmanager
cache=None
calls=[]
@contextmanager
def pair_validation_scope():
 global cache
 assert cache is None
 cache={}
 try: yield
 finally: cache=None

def _pair_and_audit_uncached(value):
 calls.append(value)
 return {'value':value}

def pair_and_audit(value):
 if cache is None:return _pair_and_audit_uncached(value)
 if cache:
  if cache['value']!=value:raise ValueError('changed evidence')
  return dict(cache)
 result=_pair_and_audit_uncached(value)
 cache.update(result)
 return result
''')
    refresh = tmp_path / 'harbor_environment_refresh.py'
    refresh.write_text('''def checks(values):
 return [W.lanes.pair_and_audit(value) for value in values]
''')
    M=ModuleType('scope_lanes');M.__file__=str(lane)
    exec(compile(lane.read_bytes(),str(lane),'exec'),vars(M))
    F=ModuleType('scope_refresh');F.__file__=str(refresh);F.W=NS(lanes=M)
    exec(compile(refresh.read_bytes(),str(refresh),'exec'),vars(F))
    row=lambda p:dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    return F,M,dict(refresh=row(refresh),lanes=row(lane))


def test_each_original_prefix_has_private_scope_and_same_results(selected):
    F,M,rows=selected;original=F.checks
    expected=original([3,3,3]);assert len(M.calls)==3
    with S.installed(F,**rows):
        assert F.checks([3,3,3])==expected and len(M.calls)==4
        assert F.checks([3,3])==expected[:2] and len(M.calls)==5
        assert M.cache is None
    assert F.checks is original
    assert original([3])==expected[:1] and len(M.calls)==6


def test_mutation_rejected_and_exception_restores(selected):
    F,M,rows=selected;original=F.checks
    with pytest.raises(ValueError,match='changed evidence'):
        with S.installed(F,**rows):F.checks([1,2])
    assert F.checks is original and M.cache is None


@pytest.mark.parametrize('target',['refresh','lanes'])
def test_code_change_before_reuse_rejects(selected,target):
    F,M,rows=selected
    with S.installed(F,**rows):
        Path(rows[target]['path']).write_text('changed')
        with pytest.raises(ValueError,match='binding changed'):F.checks([1])
    assert not M.calls


def test_rebound_callbacks_reject_before_gate(selected):
    F,M,rows=selected
    with S.installed(F,**rows):
        M.pair_and_audit=lambda x:x
        with pytest.raises(ValueError,match='callbacks changed'):F.checks([1])
    assert not M.calls


def test_same_filename_modified_function_not_original(selected):
    F,M,rows=selected
    exec(compile('def checks(values):return []',F.__file__,'exec'),vars(F))
    with pytest.raises(ValueError,match='original module functions'):
        with S.installed(F,**rows):pass


def test_module_identity_rejects(selected):
    F,M,rows=selected;F.__file__=M.__file__
    with pytest.raises(ValueError,match='foreign refresh'):
        with S.installed(F,**rows):pass
