"""Retained profile context restores canonical imports after success and failure."""
from contextlib import contextmanager
from dataclasses import make_dataclass
from types import ModuleType, SimpleNamespace
import importlib
import sys
import pytest
from scripts.fleet import profile_skip_runtime as H

@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('cached', [False, True])
def test_exact_import_and_type_restoration(monkeypatch, fail, cached):
    import scripts.batch as package
    skip_name='scripts.batch.harbor_profile_skips'
    projection_name='scripts.batch.harbor_public_projection_recovery'
    names={'scripts.batch.harbor_profile_readiness': ('ProfileSelection','ProfileCall'),
           'scripts.batch.harbor_complete_readiness': ('ReadinessSelection','SemanticSelection')}
    originals={};selected={};aliases=ModuleType('_profile_cleanup_aliases')
    for name, symbols in names.items():
        old=ModuleType(name);new=ModuleType(name);old.__file__=name+'-old';new.__file__=name+'-new'
        for symbol in symbols:
            a=make_dataclass(symbol,[('value',int)]);b=make_dataclass(symbol,[('value',int)])
            setattr(old,symbol,a);setattr(new,symbol,b);setattr(aliases,symbol,a)
        originals[name]=old;selected[name]=new
        monkeypatch.setitem(sys.modules,name,old)
    monkeypatch.setitem(sys.modules,aliases.__name__,aliases)
    prior_skip=ModuleType(skip_name);prior_skip.__file__='skip'
    prior_projection=ModuleType(projection_name);prior_projection.__file__='projection'
    for name,prior in [(skip_name,prior_skip),(projection_name,prior_projection)]:
        if cached:
            monkeypatch.setitem(sys.modules,name,prior);monkeypatch.setattr(package,name.rsplit('.',1)[1],prior,raising=False)
        else:
            monkeypatch.delitem(sys.modules,name,raising=False);monkeypatch.delattr(package,name.rsplit('.',1)[1],raising=False)
    deps=[dict(module_name=n,selected=m.__file__) for n,m in selected.items()]
    @contextmanager
    def installed(row):
        for n,m in selected.items():sys.modules[n]=m
        try:yield
        finally:
            for n,m in originals.items():sys.modules[n]=m
    runtime=SimpleNamespace(profile=lambda row:dict(dependencies=deps),installed=installed)
    def module(name,row):
        if row=='runtime':return runtime
        m=ModuleType(name);m.__file__=row;sys.modules[name]=m;return m
    monkeypatch.setattr(H,'module',module);monkeypatch.setattr(H,'binding',lambda p:p)
    policy=dict(runtime='runtime',runtime_profile='profile',skip_helper='skip',projection_helper='projection')
    monkeypatch.setattr(H,'validate',lambda *args:(policy,[]))
    def exercise():
        with H.installed({},[None]*7):
            assert sys.modules[skip_name] is not prior_skip
            for n,m in selected.items():
                assert importlib.import_module(n) is m
                for symbol in names[n]:assert getattr(aliases,symbol) is getattr(m,symbol)
            if fail:raise RuntimeError('actual consumer failure')
    if fail:
        with pytest.raises(RuntimeError,match='actual consumer failure'):exercise()
    else:exercise()
    for n,m in originals.items():
        assert importlib.import_module(n) is m
        for symbol in names[n]:assert getattr(aliases,symbol) is getattr(m,symbol)
    for name,prior in [(skip_name,prior_skip),(projection_name,prior_projection)]:
        if cached:
            assert sys.modules[name] is prior
            assert getattr(package,name.rsplit('.',1)[1]) is prior
        else:
            assert name not in sys.modules
            assert not hasattr(package,name.rsplit('.',1)[1])
