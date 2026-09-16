from pathlib import Path
from types import SimpleNamespace,ModuleType
import sys
import pytest
from scripts.fleet import continue_0017_authoring as A

def test_readonly_or_existing_namespace_cannot_replay(tmp_path,monkeypatch):
    monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
    with pytest.raises(ValueError,match='read-only'):A.run({'path':'/absent','sha256':'0'*64},tmp_path/'never')
    monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY')
    used=tmp_path/'used';used.mkdir();(used/'intent.json').write_text('uncertain retained intent')
    with pytest.raises(ValueError,match='namespace used'):A.run({},used)
    assert (used/'intent.json').read_text()=='uncertain retained intent'

def test_failed_actual_owning_outcome_is_retained_and_never_passed_to_dispatcher(tmp_path,monkeypatch):
    calls=[];owning=A.retain(tmp_path/'owning-outcome.json',{'status':'RECONCILIATION_REQUIRED','automatic_retry':False})
    def step(action,arguments,controls,directory,**flags):
        calls.append((action,arguments,directory,flags));return owning
    def successful(*a):raise ValueError('real guard failed')
    dispatcher=SimpleNamespace(successful=successful,run=lambda *a:pytest.fail('v7 must not receive failed first guard'))
    C=SimpleNamespace(_campaign=lambda campaign:None);I=SimpleNamespace(run=step);worker=SimpleNamespace(root=tmp_path,campaign='original1slot')
    prepared={'path':'/actual/prepared','sha256':'0'*64};checkpoint={'path':'/actual/guard/result','sha256':'1'*64};descriptor={'path':'/actual/guard/descriptor','sha256':'2'*64}
    monkeypatch.setattr(A,'prepared_context',lambda b:({'checkpoint':checkpoint},{},dispatcher,{'operations':str(tmp_path/'remaining/operations')},(C,I),{'descriptor':descriptor},worker,'original-controls'))
    fake=ModuleType('scripts.fleet.disclosure_0017_proof');fake.require_fleet_custody=lambda root:None;monkeypatch.setitem(sys.modules,'scripts.fleet.disclosure_0017_proof',fake)
    import scripts.fleet
    monkeypatch.setattr(scripts.fleet,'disclosure_0017_proof',fake,raising=False)
    monkeypatch.setattr(A.shutil,'disk_usage',lambda root:SimpleNamespace(free=20*1024**3))
    result=A.checked(A.run(prepared,tmp_path/'remaining'))
    assert result['status']=='RECONCILIATION_REQUIRED' and result['owning_first_build_outcome']==owning and result['automatic_retry'] is False
    assert calls==[('step',{'descriptor':descriptor,'checkpoint':checkpoint},tmp_path/'remaining/deterministic-build',{'models':False,'commands':True,'containers':True})]


def test_actual_original_dispatcher_setup_retains_generated_history_authority():
    import subprocess
    r=subprocess.run([sys.executable,'-c',"import runpy; A=runpy.run_path('scripts/fleet/continue_0017_authoring.py'); m,j=A['modules'](); e=m.setup(j); assert e[5]['workbook']=='0017'; assert j['publish'] is False; assert j['models'] is True; assert j['outcome'] is None; print('PASS')"],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
