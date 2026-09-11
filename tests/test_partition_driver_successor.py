"""Explicit driver succession retains guard ownership and rejects failed dispatch."""
import importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def driver():
    p=ROOT/'diagnostics/requested-fixes-20260910/partition_0184_guarded_v6.py'
    spec=importlib.util.spec_from_file_location('partition_successor_driver',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def save(driver,path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return driver.record(path)

@pytest.mark.parametrize('which',['self','prior','recovery'])
def test_only_explicit_guard_and_result_driver_pairs_pass(driver,tmp_path,which):
    owner={'self':driver.record(driver.__file__),'prior':driver.PRIOR_DRIVER,'recovery':driver.RECOVERY_DRIVER}[which]
    result=save(driver,tmp_path/'result.json',{'driver':owner})
    intent=save(driver,tmp_path/'intent.json',dict(driver=owner,snapshot=driver.SNAPSHOT,old_result=driver.BUILD,
        old_descriptor=driver.DESCRIPTOR,renewal=driver.RENEWAL))
    (tmp_path/'step.log').write_text('retained successful operation')
    resource=save(driver,tmp_path/'resource.json',dict(returncode=0,failure=None,intent=intent,log=driver.record(tmp_path/'step.log')))
    save(driver,tmp_path/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
    assert driver.successful(result)=={'driver':owner}
    changed=save(driver,tmp_path/'result.json',{'driver':{'path':'/unreviewed','sha256':'0'*64}})
    save(driver,tmp_path/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=changed,automatic_retry=False))
    with pytest.raises(AssertionError,match='driver differ'):driver.successful(changed)

def test_unbound_recovery_cannot_enable_successor(driver,monkeypatch):
    monkeypatch.setattr(driver,'RECOVERY_DRIVER',None)
    with pytest.raises(AssertionError,match='recovery binding required'):driver.allowed_drivers()
