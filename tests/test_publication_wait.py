import ast,copy,hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_wait as W,build_publication_wait as B
BASE=Path(__file__).resolve().parents[1]
ORIGINAL=BASE/'diagnostics/vm-fleet-20260912/control/0029-publication-003/collector.py'
def require(ok,message):
 if not ok:raise ValueError(message)
def configuration():
 policy=dict(schema_version=W.SCHEMA,workbook='0190',source_sha256='s',maximum_wait_seconds=120,authorization=dict(path='/auth',sha256=W.AUTHORIZATION_SHA256),lease_tracker=dict(path='/tracker',sha256='t'))
 row=dict(path='/policy',sha256='p');plan=dict(schema_version='harbor-fleet-remote-publication/v1',workbook='0190',source_sha256='s',publication_wait=row)
 return policy,row,plan

def test_default_wait_preserves3300():assert W.configuration({},checked=None,binding=None,require=require)==dict(maximum_wait_seconds=3300,policy=None)
@pytest.mark.parametrize('field,value',[('maximum_wait_seconds',121),('maximum_wait_seconds',True),('workbook','0200'),('source_sha256','other'),('schema_version','foreign')])
def test_unbound_policy_rejected(field,value):
 p,row,plan=configuration();p[field]=value
 with pytest.raises(ValueError):W.configuration(plan,checked=lambda r:p,binding=lambda pth:p['authorization'],require=require)
@pytest.mark.parametrize('remaining,passed',[(3700,False),(3699,False),(3701,True)])
def test_exact_post_slot_runway(remaining,passed):
 p,row,plan=configuration();selected=W.configuration(plan,checked=lambda r:p,binding=lambda path:p['authorization'],require=require)
 tracker=dict(workbook_id='0190',bindings=dict(original_raw_source=dict(sha256='s')),execution_state='publication_queued',terminal_verdict='PUBLICATION_READY',promotion_failure=None,leases=dict(task_agent=dict(expires_at=(100+remaining)*10**9)))
 if passed:assert W.enforce_after_slot(selected,checked=lambda r:tracker,require=require,now_ns=100*10**9)['remaining_seconds']==3701
 else:
  with pytest.raises(ValueError,match='lease'):W.enforce_after_slot(selected,checked=lambda r:tracker,require=require,now_ns=100*10**9)

def built(tmp_path):
 m=tmp_path/'parent.json';m.write_text(json.dumps(dict(publisher=B.binding(ORIGINAL),dependencies={})));row=B.build(tmp_path/'new',B.binding(m),ORIGINAL);manifest=json.loads(Path(row['path']).read_text());path=manifest['publisher']['path'];spec=importlib.util.spec_from_file_location('wait_fixture_pub',path);pub=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pub;spec.loader.exec_module(pub);return pub,manifest

def test_builder_preserves_all_other_original_functions(tmp_path):
 pub,m=built(tmp_path)
 def funcs(s):return {n.name:ast.dump(n,include_attributes=False)for n in ast.parse(s).body if isinstance(n,ast.FunctionDef)}
 before=funcs(ORIGINAL.read_text());after=funcs(Path(pub.__file__).read_text());assert set(before)==set(after);assert [k for k in before if before[k]!=after[k]]==['controlled']

def test_actual_controlled_busy_wait_stops_before_owning(tmp_path,monkeypatch):
 pub,m=built(tmp_path);policy,row,plan=configuration()
 auth=BASE/'diagnostics/bulk-production/authorization-20260913.md';assert B.binding(auth)['sha256']==W.AUTHORIZATION_SHA256
 policy['authorization']=B.binding(auth);pp=tmp_path/'policy.json';pp.write_text(json.dumps(policy));plan['publication_wait']=B.binding(pp);plan.update(journal=str(tmp_path/'journal'),code_root=str(tmp_path))
 pf=tmp_path/'plan.json';pf.write_text(json.dumps(plan));events=[]
 class Client:
  def __init__(self,*a):pass
  def call(self,method,**kw):
   events.append(method)
   if method=='claim':return dict(workbook='0190',owner='pub',token='fixture',fence=1,source_sha256='s')
   if method=='begin':return dict(busy=True,state='busy')
   return {}
 from scripts.fleet import coordinator
 monkeypatch.setattr(coordinator,'Client',Client);monkeypatch.setattr(pub,'history_binding',lambda p:None);monkeypatch.setattr(pub.shutil,'disk_usage',lambda p:SimpleNamespace(free=20*1024**3));monkeypatch.setattr(pub.time,'monotonic',lambda:next(clock));clock=iter([0,121,122])
 with pytest.raises(ValueError,match='wait limit'):pub.controlled(B.binding(pf),action='remote',url='fixture',token_file='/fixture',owner='pub',version='code')
 assert events==['claim','heartbeat','begin','finish']
 assert not (tmp_path/'journal/controller.log').exists() and (tmp_path/'journal/reconciliation.json').exists()

def test_actual_controlled_checks_lease_after_slot_before_child(tmp_path,monkeypatch):
 pub,m=built(tmp_path);policy,row,plan=configuration();policy['authorization']=B.binding(BASE/'diagnostics/bulk-production/authorization-20260913.md')
 tracker=dict(workbook_id='0190',bindings=dict(original_raw_source=dict(sha256='s')),execution_state='publication_queued',terminal_verdict='PUBLICATION_READY',promotion_failure=None,leases=dict(task_agent=dict(expires_at=3700*10**9)))
 tp=tmp_path/'tracker.json';tp.write_text(json.dumps(tracker));policy['lease_tracker']=B.binding(tp)
 pp=tmp_path/'policy.json';pp.write_text(json.dumps(policy));plan['publication_wait']=B.binding(pp);plan.update(journal=str(tmp_path/'journal'),code_root=str(tmp_path));pf=tmp_path/'plan.json';pf.write_text(json.dumps(plan));events=[]
 class Client:
  def __init__(self,*a):pass
  def call(self,method,**kw):
   events.append(method)
   if method=='claim':return dict(workbook='0190',owner='pub',token='fixture',fence=1,source_sha256='s')
   if method=='begin':return dict(execute=True)
   return {}
 from scripts.fleet import coordinator
 monkeypatch.setattr(coordinator,'Client',Client);monkeypatch.setattr(pub,'history_binding',lambda p:None);monkeypatch.setattr(pub.shutil,'disk_usage',lambda p:SimpleNamespace(free=20*1024**3));monkeypatch.setattr(pub.time,'monotonic',lambda:0);monkeypatch.setattr(pub.time,'time_ns',lambda:0)
 with pytest.raises(ValueError,match='after publication slot'):pub.controlled(B.binding(pf),action='remote',url='fixture',token_file='/fixture',owner='pub',version='code')
 assert events==['claim','heartbeat','begin','end']
 assert (tmp_path/'journal/acquired.json').exists() and not(tmp_path/'journal/controller.log').exists()
 assert json.loads((tmp_path/'journal/admission-rejected.json').read_text())['owning_child_started']is False
 assert (tmp_path/'journal/reconciliation.json').exists()
