import json
from pathlib import Path
import pytest
from scripts.fleet import first_custody as X
from scripts.fleet.migration import retain,checked,binding


@pytest.fixture
def gate():
    job=dict(workbook='0006',source={'sha256':'a'*64})
    p=dict(expected_reason='preserved initial handoff hold')
    w=dict(workbook='0006',state='hold',fence=0,owner=None,version=None,source_sha256='a'*64,reason=p['expected_reason'])
    return dict(model_limit=1,publication_limit=1,operations=[],workbooks=[w]),p,job


def test_first_hold_is_accepted_without_invented_prior_worker(gate):
    status,p,job=gate;assert X.central_gate(status,p,job)['fence']==0


@pytest.mark.parametrize('key,value',[('state','ready'),('fence',1),('owner','priorworker'),('version','oldrelease'),('source_sha256','b'*64),('reason','different')])
def test_changed_first_custody_fails_closed(gate,key,value):
    status,p,job=gate;status['workbooks'][0][key]=value
    with pytest.raises(ValueError):X.central_gate(status,p,job)


@pytest.mark.parametrize('state',['active','hold'])
def test_global_uncertainty_blocks_first_custody(gate,state):
    status,p,job=gate;status['operations']=[dict(state=state)]
    with pytest.raises(ValueError,match='global operation'):X.central_gate(status,p,job)


def test_actual_retained0006_authority_and_changed_checkpoint(tmp_path):
    root=Path(__file__).resolve().parents[1];d=root/'diagnostics/vm-fleet-20260912'
    home=root/'diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0006-generated-01'
    if not home.exists():pytest.skip('optional historical fixture')
    jb=binding(d/'holds-0006/migrate-job-001.json');job=checked(jb)
    a=dict(schema_version='harbor-fleet-retained-native-authority/v1',kind='source-only',workbook='0006',job=jb,source=job['source'],
        tracker=binding(home/'trackers/workbooks/0006.json'),checkpoint=binding(home/'dispatcher-v2/first/000/result.json'),descriptor=binding(home/'admission-001/descriptor.json'),
        inspection=binding(d/'holds-0006/remote-inspection-02/inspection.json'),history=job['continuation_history'])
    p=dict(job=jb,authority=retain(tmp_path/'authority.json',a));assert X.authority(p,job)==a
    changed=checked(a['checkpoint']);changed['descriptor']={'path':'/foreign/descriptor','sha256':'f'*64}
    a['checkpoint']=retain(tmp_path/'changed.json',changed);p['authority']=retain(tmp_path/'bad-authority.json',a)
    with pytest.raises(ValueError,match='checkpoint'):X.authority(p,job)


def test_actual_retained0190_completed_response_authority(tmp_path):
    root=Path(__file__).resolve().parents[1];d=root/'diagnostics/vm-fleet-20260912/holds-0190/gated-successor-001'
    if not (d/'refresh-plan.json').exists():pytest.skip('optional historical fixture')
    h=checked(binding(d/'refresh-plan.json'));jb=binding(d/'refresh-worker-job.json');job=checked(jb)
    a=dict(schema_version='harbor-fleet-retained-native-authority/v1',kind='existing-review',workbook='0190',job=jb,source=job['source'],tracker=h['original_tracker'],checkpoint=h['original_checkpoint'],descriptor=h['original_descriptor'],inspection=binding(d/'inspection-linux-001/inspection.json'),history=binding(d/'refresh-plan.json'))
    p=dict(job=jb,authority=retain(tmp_path/'authority.json',a));assert X.authority(p,job)==a
    job['command']=[*job['command'],'--extra-model']
    with pytest.raises(ValueError):X.authority(p,job)


def test_lost_reconcile_preserves_intent_without_spawn(tmp_path,monkeypatch,gate):
    status,p,job=gate;p={**p,'authority':{'path':'/authority','sha256':'a'},'coordinator_receipt':{'path':str(tmp_path/'coordinator.json'),'sha256':'b'}}
    monkeypatch.setattr(X,'load',lambda row:(p,job));monkeypatch.setattr(X,'checked',lambda row:dict(url='http://127.0.0.1:8765',private_directory=str(tmp_path)))
    calls=[]
    class Client:
        def call(self,action,**kw):
            if action=='status':return status
            if action=='reconcile':raise TimeoutError('lost response')
            pytest.fail('unexpected operation')
    monkeypatch.setattr(X,'Client',lambda *a:Client());monkeypatch.setattr(X.F,'smoke_gate',lambda *a:None)
    monkeypatch.setattr(X,'local_custody',lambda *a:{'pid':123})
    monkeypatch.setattr(X,'ssh',lambda p,j,action,payload:calls.append(action) or {'status':'VALIDATED_NOT_STARTED'})
    with pytest.raises(TimeoutError):X.launch({'path':'/plan','sha256':'z'},tmp_path/'launch',execute=True)
    assert calls==['check'] and (tmp_path/'launch/intent.json').exists()
    assert json.loads((tmp_path/'launch/uncertain.json').read_text())['phase']=='reconcile'
    with pytest.raises(ValueError,match='journal exists'):X.launch({'path':'/plan','sha256':'z'},tmp_path/'launch',execute=True)
    assert calls==['check','check']


def test_smoke_is_mandatory_before_remote_or_state_mutation(tmp_path,monkeypatch,gate):
    status,p,job=gate;p={**p,'coordinator_receipt':{'path':str(tmp_path/'coordinator.json'),'sha256':'b'}}
    monkeypatch.setattr(X,'load',lambda row:(p,job));monkeypatch.setattr(X,'checked',lambda row:dict(url='http://127.0.0.1:8765',private_directory=str(tmp_path)))
    class Client:
        def call(self,action,**kw):assert action=='status';return status
    monkeypatch.setattr(X,'Client',lambda *a:Client())
    def fail(*a):raise ValueError('smoke not qualified')
    monkeypatch.setattr(X.F,'smoke_gate',fail)
    monkeypatch.setattr(X,'ssh',lambda *a:pytest.fail('must not reach remote'))
    with pytest.raises(ValueError,match='smoke'):X.launch({'path':'/plan','sha256':'z'},tmp_path/'launch',execute=True)
    assert not (tmp_path/'launch').exists()
