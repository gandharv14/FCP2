import importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(params=['partition_0184_guarded.py','partition_0184_guarded_v2.py'])
def driver(request):
    path=ROOT/'diagnostics/requested-fixes-20260910'/request.param
    spec=importlib.util.spec_from_file_location('partition_driver_test',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.parametrize('action',['authorize','batch','finalize'])
def test_unapproved_snapshot_cannot_start_any_operation(driver,tmp_path,monkeypatch,action):
    monkeypatch.setattr(driver,'HOME',tmp_path/'unused')
    with pytest.raises(AssertionError,match='execution disabled'):driver.run(action)
    assert not driver.HOME.exists()

def sealed(driver,tmp_path):
    def keep(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return driver.record(p)
    result=keep('result.json',{'actual':'completed'})
    intent=keep('intent.json',dict(driver=driver.record(driver.__file__),snapshot=driver.SNAPSHOT,
        old_result=driver.BUILD,old_descriptor=driver.DESCRIPTOR,**({'renewal':driver.RENEWAL} if hasattr(driver,'RENEWAL') else {})))
    (tmp_path/'step.log').write_text('complete')
    resource=keep('resource.json',dict(returncode=0,failure=None,intent=intent,log=driver.record(tmp_path/'step.log')))
    keep('outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=resource,automatic_retry=False))
    return result

def test_exact_successful_guard_required_before_next_operation(driver,tmp_path):
    result=sealed(driver,tmp_path)
    assert driver.successful(result)=={'actual':'completed'}
    (tmp_path/'step.log').write_text('modified')
    with pytest.raises(AssertionError):driver.successful(result)

@pytest.mark.parametrize('change',['failed','foreign-result','replay'])
def test_failed_or_detached_guard_cannot_be_carried_forward(driver,tmp_path,change):
    result=sealed(driver,tmp_path);p=tmp_path/'outcome.json';v=json.loads(p.read_text())
    if change=='failed':v['status']='RECONCILIATION_REQUIRED'
    elif change=='foreign-result':v['result']={'path':'/foreign','sha256':'0'*64}
    else:v['automatic_retry']=True
    p.write_text(json.dumps(v))
    with pytest.raises(AssertionError):driver.successful(result)
