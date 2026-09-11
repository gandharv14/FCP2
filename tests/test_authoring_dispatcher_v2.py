from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def driver(monkeypatch):
    p=ROOT/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v2.py';monkeypatch.syspath_prepend(str(p.parent))
    spec=importlib.util.spec_from_file_location('dispatch_v2',p);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d);return d

def test_fleet_two_and_workbook_exclusion(driver,tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'FLEET',tmp_path/'fleet')
    with driver.fleet_slot('0001'):
        with pytest.raises(BlockingIOError):
            with driver.fleet_slot('0001'):pass
        with driver.fleet_slot('0002'):
            with pytest.raises(ValueError,match='capacity'):
                with driver.fleet_slot('0003'):pass
    with driver.fleet_slot('0003'):pass

@pytest.mark.parametrize('history',['deterministic_rebuild_only','legacy_model_artifacts'])
def test_history_never_becomes_fresh_admission(driver,history):
    q={'schema_version':'authoring-request-queue/v1','counts':{'total':1},'items':[{'workbook_id':'0001','history_class':history}]}
    with pytest.raises(ValueError,match='lossless migration'):driver.queue_item(q,'0001')

def test_duplicate_queue_rejected(driver):
    row={'workbook_id':'0001','history_class':'no_history'}
    with pytest.raises(ValueError):driver.queue_item({'schema_version':'authoring-request-queue/v1','counts':{'total':2},'items':[row,row]},'0001')

@pytest.mark.parametrize('fault',[None,'outer','returncode','failure','log','missing_result'])
def test_prior_guard_must_be_complete(driver,tmp_path,fault):
    def b(name,value):
        p=tmp_path/name;p.write_text(value if isinstance(value,str) else json.dumps(value));return driver.binding(p)
    result=b('result.json',{'status':'STEP_COMPLETE'});intent=b('intent.json',{});log=b('step.log','retained')
    resource=b('resource.json',dict(returncode=1 if fault=='returncode' else 0,failure={} if fault=='failure' else None,intent=intent,log=log))
    outcome=b('outcome.json',dict(status='RECONCILIATION_REQUIRED' if fault=='outer' else 'COMPLETED_OPERATION',automatic_retry=False,resource=resource,result=None if fault=='missing_result' else result))
    if fault=='log':Path(log['path']).write_text('tampered')
    if fault is None:assert driver.successful(outcome)==(result,{'status':'STEP_COMPLETE'})
    else:
        with pytest.raises(ValueError):driver.successful(outcome)

@pytest.mark.parametrize('fault',[None,'pending','different_checkpoint','wrong_selection'])
def test_renewal_only_completed_exact_prior(driver,tmp_path,monkeypatch,fault):
    def b(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return driver.binding(p)
    complete=b('complete.json',{'status':'pending' if fault=='pending' else 'completed'})
    prior=dict(status='STEP_COMPLETE',checkpoint=complete,selection={'s':1},lease={'l':1},next_step='dialogue-review')
    held={**prior,'status':'LEASE_RENEWAL_REQUIRED','descriptor':{},'evidence':[{'prior':True}]}
    if fault=='different_checkpoint':held['checkpoint']={}
    if fault=='wrong_selection':held['selection']={}
    checkpoint=b('held.json',held);w=object();s=object();lease=object();active=[]
    C=SimpleNamespace(_prior=lambda *a:(prior,s,lease));W=SimpleNamespace(active=lambda *a:active.append(a))
    env=(None,C,None,W,None,None,None,None);monkeypatch.setattr(driver,'selected',lambda *a:(w,'recipe'))
    if fault is None:assert driver.renewal_context(env,checkpoint)[1:]==(w,'recipe',s,lease) and active
    else:
        with pytest.raises(ValueError):driver.renewal_context(env,checkpoint)
        assert not active

def test_missing_evidence_is_retained_hold(driver,tmp_path):
    missing={'path':str(tmp_path/'absent.json'),'sha256':'0'*64}
    assert driver.inspect_prior(missing,None,tmp_path) is None
    hold=json.loads((tmp_path/'hold.json').read_text())
    assert hold['automatic_retry'] is False and hold['outcome']==missing and hold['error_type']=='FileNotFoundError'

def test_namespace_rejects_escape_symlink_and_replay(driver,tmp_path):
    root=tmp_path/'run';root.mkdir();config={'run_root':str(root)}
    good=root/'dispatcher-v2'/'job'
    assert driver.operation_namespace(config,str(good))==good
    for bad in (root/'task',root/'dispatcher-v2'/'..'/'task'):
        with pytest.raises(ValueError):driver.operation_namespace(config,str(bad))
    (root/'dispatcher-v2').symlink_to(tmp_path/'elsewhere')
    with pytest.raises(ValueError):driver.operation_namespace(config,str(good))
