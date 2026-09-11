import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def driver(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/run_0184_dialogue_partitions.py'
    spec=importlib.util.spec_from_file_location('dialogue_partition_driver',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    monkeypatch.setattr(m,'HOME',tmp_path/'unused');return m

def test_disabled_initial_cannot_even_load_or_prepare(driver,monkeypatch):
    assert driver.INITIAL is None
    monkeypatch.setattr(driver,'checked',lambda *_:(_ for _ in ()).throw(AssertionError('artifact read before explicit binding')))
    with pytest.raises(AssertionError,match='disabled'):driver.setup()
    assert not driver.HOME.exists()

@pytest.mark.parametrize('n',[1,22])
def test_exact_three_role_calls_per_partition_then_finalize(driver,monkeypatch,n):
    seen=[]
    def guarded(action,prior,number):seen.append((action,number));return {'status':'CALL_COMPLETE'}
    D=SimpleNamespace(calls=lambda worker:(None,None,None,{'projection':{'partitions':[None]*n}},None))
    monkeypatch.setattr(driver,'setup',lambda:(None,None,None,D,None,None,None,None,None,None,None,None))
    monkeypatch.setattr(driver,'guarded',guarded);monkeypatch.setattr(driver,'checked',lambda b:b)
    driver.run()
    assert seen[0]==('begin',1) and seen[-1]==('finalize',3*n+2)
    assert len([x for x in seen if x[0]=='call'])==3*n
    assert [i for _,i in seen]==list(range(1,3*n+3))

def test_failed_guard_stops_without_next_call_or_finalize(driver,monkeypatch):
    seen=[]
    def guarded(action,prior,number):
        seen.append(action)
        if action=='call':raise ValueError('retained uncertain call')
        return {'status':'ROUND_READY'}
    D=SimpleNamespace(calls=lambda worker:(None,None,None,{'projection':{'partitions':[None]*22}},None))
    monkeypatch.setattr(driver,'setup',lambda:(None,None,None,D,None,None,None,None,None,None,None,None))
    monkeypatch.setattr(driver,'guarded',guarded);monkeypatch.setattr(driver,'checked',lambda b:b)
    with pytest.raises(ValueError):driver.run()
    assert seen==['begin','call']
