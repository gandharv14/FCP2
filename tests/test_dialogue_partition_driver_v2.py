import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.fixture
def driver(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/run_0184_dialogue_partitions_v2.py'
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

@pytest.fixture
def history(driver,tmp_path,monkeypatch):
    import json,hashlib
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,sort_keys=True));return bind(path)
    def bind(path):return dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    def checked(binding):
        assert bind(binding['path'])==binding
        return json.loads(Path(binding['path']).read_text())
    monkeypatch.setattr(driver,'binding',bind);monkeypatch.setattr(driver,'checked',checked)
    monkeypatch.setattr(driver,'INITIAL',put(tmp_path/'initial.json',{'initial':True}))
    original={'descriptor':{'exact':'descriptor'}};selection={'exact':'selection'}
    previous=driver.INITIAL;nodes=[]
    for number,action in ((1,'begin'),(2,'call')):
        d=driver.HOME/('%03d-%s'%(number,action))
        intent=put(d/'intent.json',dict(driver=bind(driver.__file__),snapshot=driver.SNAPSHOT,initial=driver.INITIAL,action=action,prior=previous,timeout_seconds=3300 if action=='call' else 900,memory_limit_bytes=8*1024**3))
        log=put(d/'step.log',{'log':'retained'})
        value=dict(schema_version=driver.SCHEMA,driver=bind(driver.__file__),snapshot=driver.SNAPSHOT,initial=driver.INITIAL,descriptor=original['descriptor'],selection=selection,action=action,status='ROUND_READY' if action=='begin' else 'CALL_COMPLETE',prior=previous)
        result=put(d/'result.json',value)
        resource=put(d/'resource.json',dict(intent=intent,log=log,returncode=0,failure=None))
        put(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
        previous=result;nodes.append(d)
    return original,selection,SimpleNamespace(encode=lambda x:x),previous,nodes,put,checked

def test_full_guard_chain_is_checked(driver,history):
    original,selection,K,last,_,_,_=history
    assert [x['action'] for x in driver.guarded_history(last,original,selection,K)]==['begin','call']

@pytest.mark.parametrize('defect',['outcome_resource','outcome_status','intent_prior','log_path','ancestor_failure','reordered'])
def test_guard_ancestry_tampering_rejects(driver,history,defect):
    original,selection,K,last,nodes,put,read=history
    d=nodes[-1];resource=read(driver.binding(d/'resource.json'));outcome=read(driver.binding(d/'outcome.json'))
    if defect=='outcome_resource':outcome['resource']=driver.binding(nodes[0]/'resource.json');put(d/'outcome.json',outcome)
    elif defect=='outcome_status':outcome['status']='RECONCILIATION_REQUIRED';put(d/'outcome.json',outcome)
    elif defect=='intent_prior':
        intent=read(resource['intent']);intent['prior']=driver.INITIAL;resource['intent']=put(d/'intent.json',intent);outcome['resource']=put(d/'resource.json',resource);put(d/'outcome.json',outcome)
    elif defect=='log_path':
        resource['log']=driver.binding(nodes[0]/'step.log');outcome['resource']=put(d/'resource.json',resource);put(d/'outcome.json',outcome)
    elif defect=='ancestor_failure':
        old=read(driver.binding(nodes[0]/'resource.json'));old['returncode']=1;put(nodes[0]/'resource.json',old)
    else:
        value=read(last);value['action']='begin';last=put(d/'result.json',value)
    with pytest.raises(AssertionError):driver.guarded_history(last,original,selection,K)
