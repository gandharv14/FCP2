"""Private final driver must consume a real worker result with every capability off."""
from pathlib import Path
from dataclasses import replace,asdict
from types import SimpleNamespace
import importlib.util,json,time
import pytest
from scripts.batch.harbor_workbook_bootstrap import Lease
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def driver():
    path=ROOT/'diagnostics/requested-fixes-20260910/continue_0184_dialogue_partition_final_v3.py'
    spec=importlib.util.spec_from_file_location('disabled_partition_final',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def test_unbound_driver_cannot_initialize(driver):
    assert driver.INITIAL is None
    with pytest.raises(AssertionError):driver.setup()


@pytest.mark.parametrize('fault',[None,'headroom','required_model','selection','lease','phase','predecessor'])
def test_initial_consumer_uses_no_capabilities_and_requires_exact_owning_result(driver,tmp_path,monkeypatch,fault):
    def bind(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return driver.binding(p)
    selection=object();lease=Lease(owner='owner',token='token',revision=41,sha256='a'*64)
    checkpoint=bind('aggregate-complete.json',{'passed':True})
    value={'status':'RETRY_READY','next_step':'dialogue-writer','checkpoint':checkpoint,'descriptor':{'selected':'descriptor'}}
    worker=object();calls=[]
    monkeypatch.setattr(driver,'inspect_start',lambda env:value)
    monkeypatch.setattr(driver,'finalizer_guard',lambda:({},[]))
    monkeypatch.setattr(driver,'selected',lambda *args:(worker,'dependency-recipe'))
    intent=bind('worker-intent.json',{'previous':checkpoint if fault!='predecessor' else {},'lease':asdict(lease)})
    complete=bind('worker-complete.json',{'status':'completed','step':'dialogue-writer','intent':intent})
    outcome=SimpleNamespace(status='MODEL_REQUIRED' if fault=='required_model' else 'STEP_COMPLETE',
        next_step='dialogue-review' if fault=='phase' else 'dialogue-paraphraser',
        selection=object() if fault=='selection' else selection,
        lease=replace(lease,revision=42) if fault=='lease' else lease,evidence=complete,publication_performed=False)
    def step(actual,selected,current,**kwargs):
        assert actual is worker and selected is selection and current==lease
        assert kwargs==dict(phase='dialogue-writer',checkpoint=checkpoint,models=False,commands=False,containers=False)
        calls.append(kwargs);return outcome
    W=SimpleNamespace(step=step,active=lambda *args:(None,{'leases':{'task_agent':{'expires_at':time.time_ns()+(900 if fault=='headroom' else 1200)*10**9}}},None))
    C=SimpleNamespace(_prior=lambda *args:(value,selection,lease),dependency=lambda *args:bind('dependency.json',{}),selected=lambda *args:None,
        _result=lambda status,descriptor,s,l,**kwargs:{'status':status,**kwargs})
    base=SimpleNamespace(keep=lambda path,value:bind(Path(path).name,value))
    env=(ROOT,{},base,C,W,None,None,None,{},None)
    if fault is None:
        result=driver.consume_initial(env,tmp_path)
        assert driver.checked(result)['checkpoint']==complete and len(calls)==1
    else:
        with pytest.raises(AssertionError):driver.consume_initial(env,tmp_path)
        if fault=='headroom':assert not calls


@pytest.mark.parametrize('fault',[None,'failed_guard','uncertain','rejected_round','different_result','log_changed','wrong_action'])
def test_successful_finalization_guard_is_required_not_merely_existing_result(driver,tmp_path,monkeypatch,fault):
    def bind(name,value):
        p=tmp_path/name;p.write_text(value if isinstance(value,str) else json.dumps(value));return driver.binding(p)
    initial=bind('canonical-result.json',{'status':'RETRY_READY'})
    executable=bind('driver.py','# retained finalizer')
    snapshot={'path':'/exact/snapshot.json','sha256':'a'*64};original={'original':'boundary'};previous={'prior':'call'}
    intent=bind('intent.json',dict(driver=executable,snapshot=snapshot,initial=original,prior=previous,action='call' if fault=='wrong_action' else 'finalize'))
    log=bind('step.log','completed')
    result=bind('result.json',dict(status='DIALOGUE_REPAIR_REQUIRED' if fault=='rejected_round' else 'ROUND_PASSED',
        canonical_result={} if fault=='different_result' else initial,action='finalize',snapshot=snapshot,driver=executable,initial=original,prior=previous))
    resource=bind('resource.json',dict(intent=intent,log=log,returncode=1 if fault=='failed_guard' else 0,
        failure={'type':'uncertain'} if fault=='uncertain' else None))
    outcome=bind('outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
    for name,value in [('INITIAL',initial),('INITIAL_RESOURCE',resource),('INITIAL_OUTCOME',outcome),('SNAPSHOT',snapshot)]:monkeypatch.setattr(driver,name,value)
    if fault=='log_changed':Path(log['path']).write_text('different log')
    if fault is None:
        actual,evidence=driver.finalizer_guard();assert actual['canonical_result']==initial and intent in evidence and log in evidence
    else:
        with pytest.raises(AssertionError):driver.finalizer_guard()
