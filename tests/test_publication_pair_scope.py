import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_publication as F


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path));m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def binding(path):
    p=Path(path);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


@pytest.fixture
def scoped(tmp_path,monkeypatch):
    import scripts.batch as package
    root=tmp_path/'original';folder=root/'scripts/batch';folder.mkdir(parents=True)
    pair=folder/'harbor_completed_lanes.py';pair.write_text('def pair_and_audit(*args):\n    return "original proof"\n')
    proof=folder/'harbor_ready_transitions.py';proof.write_text('def _proof(before,selection,**kwargs):\n    from scripts.batch import harbor_completed_lanes as lanes\n    if kwargs.get("fail"):raise RuntimeError("original gate failed")\n    return lanes.pair_and_audit(before,selection)\n')
    cache_path=tmp_path/'cache.py';cache_path.write_text('''from contextlib import contextmanager
entries=[]
@contextmanager
def pair_validation_scope():
    entries.append('enter')
    try:yield
    finally:entries.append('exit')
def _pair_and_audit_uncached(*args):
    return "original proof"
def pair_and_audit(*args):
    entries.append('cached wrapper')
    return _pair_and_audit_uncached(*args)
''')
    lanes=load('fixture_original_pair',pair);ready=load('fixture_original_ready',proof);cache=load('fixture_selected_cache',cache_path)
    monkeypatch.setattr(package,'harbor_completed_lanes',lanes,raising=False)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_completed_lanes',lanes)
    plan=dict(workbook='0415',source_sha256='a'*64,code_root=str(root))
    profile=dict(schema_version='fleet-publication-pair-scope/v1',**plan,
        original_pair=binding(pair),original_proof=binding(proof),pair_cache=binding(cache_path))
    record=tmp_path/'profile.json';record.write_text(json.dumps(profile));plan['validation_runtime']=binding(record)
    def require(value,message):
        if not value:raise ValueError(message)
    def checked(row):
        require(binding(row['path'])==row,'changed binding');return json.loads(Path(row['path']).read_bytes())
    def selected(name,row):
        require(binding(row['path'])==row,'changed cache');return cache
    pub=SimpleNamespace(require=require,checked=checked,binding=binding,load=selected)
    env=(None,None,SimpleNamespace(ready=ready));before=dict(workbook_id='0415',bindings=dict(original_raw_source=dict(sha256='a'*64)))
    return pub,plan,env,lanes,ready,cache,before,record,profile


def test_original_proof_and_original_pair_globals_are_delegated(scoped):
    pub,plan,env,lanes,ready,cache,before,*_=scoped;original=lanes.pair_and_audit
    F.install_validation(pub,plan,env)
    assert ready._proof(before,{})=='original proof'
    assert cache.entries==['enter','cached wrapper','exit']
    assert cache._pair_and_audit_uncached is original and lanes.pair_and_audit is original
    selected=ready._proof;F.install_validation(pub,plan,env);assert ready._proof is selected


def test_failed_original_gate_restores_pair_scope(scoped):
    pub,plan,env,lanes,ready,cache,before,*_=scoped;original=lanes.pair_and_audit
    F.install_validation(pub,plan,env)
    with pytest.raises(RuntimeError,match='original gate failed'):ready._proof(before,{},fail=True)
    assert cache.entries==['enter','exit'] and lanes.pair_and_audit is original


@pytest.mark.parametrize('changed',['source','workbook','validator'])
def test_changed_source_or_validator_cannot_reuse_scope(scoped,changed):
    pub,plan,env,lanes,ready,cache,before,*_=scoped
    F.install_validation(pub,plan,env)
    if changed=='source':before['bindings']['original_raw_source']['sha256']='b'*64
    elif changed=='workbook':before['workbook_id']='9999'
    else:lanes.pair_and_audit=lambda *a:True
    with pytest.raises(ValueError):ready._proof(before,{})
    assert not cache.entries


def test_different_uncached_validator_is_rejected_before_install(scoped):
    pub,plan,env,lanes,ready,cache,before,*_=scoped;original=ready._proof
    cache._pair_and_audit_uncached=ready._proof
    with pytest.raises(ValueError,match='changed the original validator'):F.install_validation(pub,plan,env)
    assert ready._proof is original and not cache.entries
