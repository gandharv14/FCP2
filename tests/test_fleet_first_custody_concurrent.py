"""Real central transactions prove independent admission does not grant a second call."""
import copy,json
from pathlib import Path
import pytest
from scripts.fleet import first_custody_concurrent as X
from scripts.fleet import first_custody_successor as Old
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.migration import checked,binding

@pytest.fixture
def active(tmp_path):
    now=[1000.0];c=Coordinator(tmp_path/'state.sqlite',clock=lambda:now[0])
    for w in ('0173','0085'):c.enroll(w,'a'*64,'original source/handoff')
    c.reconcile('0085','stopped original owner');a=c.claim('0085','hhu-server:slot1','claim-A','b'*64,lease_seconds=30)
    lease={k:a[k] for k in ('workbook','owner','token','fence')}
    assert c.begin('model','call-A',**lease,request_sha256='c'*64)['execute']
    return c,now,dict(workbook='0173',source={'sha256':'a'*64}),dict(expected_reason='initial ownership reconciliation required'),lease

def test_real_concurrent_claim_preserves_single_model_slot(active):
    c,now,job,p,lease=active;status=c.status()
    assert X.central_gate(status,p,job)['fence']==0
    assert X.operation_admission(status,job)['model_permission_granted'] is False
    with pytest.raises(ValueError):Old.central_gate(status,p,job)
    c.reconcile('0173','verified independent source custody');b=c.claim('0173','hhu-server-4:slot2','claim-B','d'*64)
    b={k:b[k] for k in ('workbook','owner','token','fence')}
    assert c.begin('model','call-B',**b,request_sha256='e'*64)['busy']
    assert len([o for o in c.status()['operations'] if o['state']=='active'])==1
    assert not c.begin('model','call-A',**lease,request_sha256='c'*64)['execute']

@pytest.mark.parametrize('section,field,value',[
 ('op','state','hold'),('op','state','unknown'),('op','kind','publication'),('op','kind','other'),
 ('op','owner','foreign'),('op','fence',2),('op','workbook','0173'),('op','expires',1000),
 ('op','expires',float('nan')),('op','expires',1031),('op','ended',1001),('op','evidence','response'),
 ('op','identity','wrong'),('op','started',1001),('op','operation_id',None),
 ('claim','state','hold'),('claim','owner','foreign'),('claim','fence',2),('claim','expires',1000),
 ('claim','claim_id',None),('claim','version',None),('claim','source_sha256','wrong'),
 ('status','model_limit',2),('status','publication_limit',2),('status','at',float('inf')),
 ('status','schema_version','future/v9')])
def test_all_uncertain_boundaries_remain_blocked(active,section,field,value):
    c,now,job,p,lease=active;s=c.status()
    row=s if section=='status' else s['operations'][0] if section=='op' else next(w for w in s['workbooks'] if w['workbook']=='0085')
    row[field]=value
    with pytest.raises((ValueError,TypeError)):X.central_gate(s,p,job)

def test_expired_real_operation_becomes_hold_and_blocks(active):
    c,now,job,p,lease=active;now[0]+=31
    with pytest.raises(ValueError,match='held'):X.central_gate(c.status(),p,job)

def test_duplicate_claim_or_operation_blocks(active):
    c,now,job,p,lease=active
    for key in ('operations','workbooks'):
        s=c.status();s[key].append(copy.deepcopy(s[key][0]))
        with pytest.raises(ValueError):X.central_gate(s,p,job)

def test_original_authority_kinds_do_not_gain_concurrent_admission(active):
    c,now,job,p,lease=active
    for w in ('0006','0190','0029','0144'):
        with pytest.raises(ValueError):X.operation_admission(c.status(),dict(workbook=w))

@pytest.mark.parametrize('w',['0073','0173','0303','0077'])
def test_actual_closed_normalization_authority_still_required(w):
    d=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/first-custody-002'
    p=checked(binding(d/f'{w}-launch-template.json'));job=checked(p['job'])
    assert X.authority(p,job)==Old.authority(p,job)
    p['host']='hhu-server'
    with pytest.raises(ValueError):X.authority(p,job)

def test_lost_response_retains_full_known_operation_proof(active,tmp_path,monkeypatch):
    c,now,job,p,lease=active;p.update(authority={'path':'/authority','sha256':'a'},coordinator_receipt={'path':str(tmp_path/'coordinator.json'),'sha256':'b'})
    monkeypatch.setattr(X,'load',lambda row:(p,job));monkeypatch.setattr(X,'checked',lambda row:dict(url='local',private_directory=str(tmp_path)))
    class Client:
        def call(self,action,**kw):
            if action=='status':return c.status()
            assert (tmp_path/'launch/intent.json').exists()
            if action=='reconcile':raise TimeoutError('lost response')
            pytest.fail('unexpected mutation')
    monkeypatch.setattr(X,'Client',lambda *a:Client());monkeypatch.setattr(X,'smoke_gate',lambda *a:None)
    monkeypatch.setattr(X,'local_custody',lambda *a:{'pid':123});calls=[]
    monkeypatch.setattr(X,'ssh',lambda p,j,action,payload:calls.append(action) or {'status':'VALIDATED_NOT_STARTED'})
    with pytest.raises(TimeoutError):X.launch({'path':'/plan','sha256':'z'},tmp_path/'launch',execute=True)
    assert calls==['check']
    intent=json.loads((tmp_path/'launch/intent.json').read_text())
    assert intent['operation_admission']['central']['operations']==c.status()['operations']
    assert (tmp_path/'launch/operation-admission-recheck.json').exists()
    assert json.loads((tmp_path/'launch/uncertain.json').read_text())['automatic_retry'] is False

def test_real_smoke_gate_is_not_bypassed_by_active_other_call(active):
    c,now,job,p,lease=active
    with pytest.raises(KeyError):X.smoke_gate(p,c.status(),job)

def test_operation_becomes_uncertain_after_preflight_no_reconcile(active,tmp_path,monkeypatch):
    c,now,job,p,lease=active;p.update(authority={'path':'/authority','sha256':'a'},coordinator_receipt={'path':str(tmp_path/'coordinator.json'),'sha256':'b'})
    monkeypatch.setattr(X,'load',lambda row:(p,job));monkeypatch.setattr(X,'checked',lambda row:dict(url='local',private_directory=str(tmp_path)))
    count=[]
    class Client:
        def call(self,action,**kw):
            assert action=='status';count.append(action)
            if len(count)==2:now[0]+=31
            return c.status()
    monkeypatch.setattr(X,'Client',lambda *a:Client());monkeypatch.setattr(X,'smoke_gate',lambda *a:None)
    monkeypatch.setattr(X,'local_custody',lambda *a:{'pid':123});calls=[]
    monkeypatch.setattr(X,'ssh',lambda p,j,action,payload:calls.append(action) or {'status':'VALIDATED_NOT_STARTED'})
    with pytest.raises(ValueError,match='held'):X.launch({'path':'/plan','sha256':'z'},tmp_path/'launch',execute=True)
    assert calls==['check'] and (tmp_path/'launch/intent.json').exists()
    assert not (tmp_path/'launch/ready.json').exists()

def test_qualification_checks_survive_known_model_exception(active,monkeypatch):
    c,now,job,p,lease=active
    p.update(smoke_verification={'sha256':'proof'},smoke_collection={'sha256':'copy'})
    verification=dict(schema_version='independent-published-task-copy-check/v1',workbook='0270',passed=True,
        byte_identical_to_immutable_generation=True,current_qualification_verified=True,model_calls=0,runtime_operations=0,task_manifest={'sha256':'manifest'})
    collection=dict(schema_version='harbor-fleet-collection/v1',status='COLLECTED',workbook='0270',local_byte_integrity_verified=True,
        independent_remote_verification={'sha256':'proof'},output='/unused',files={'generation-manifest.json':{'sha256':'manifest'}})
    monkeypatch.setattr(X,'checked',lambda b:verification if b['sha256']=='proof' else collection)
    monkeypatch.setattr(X.F,'files',lambda path:collection['files'])
    s=c.status();s['workbooks'].append(dict(workbook='0270',state='complete'))
    X.smoke_gate(p,s,job)
    verification['current_qualification_verified']=False
    with pytest.raises(ValueError,match='qualification'):X.smoke_gate(p,s,job)
    verification['current_qualification_verified']=True
    s['workbooks'][-1]['state']='active'
    with pytest.raises(ValueError,match='not complete'):X.smoke_gate(p,s,job)

def test_original_native_authority_and_remote_execution_functions_are_identical():
    import ast,inspect
    for name in ('original_authority','native_bindings','normalization_authority','dialogue_authority','authority',
                 'load','local_custody','remote_preflight','ssh'):
        assert ast.dump(ast.parse(inspect.getsource(getattr(X,name))))==ast.dump(ast.parse(inspect.getsource(getattr(Old,name))))
