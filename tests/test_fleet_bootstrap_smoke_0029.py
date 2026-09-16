import ast,copy,json
from pathlib import Path
import pytest
from scripts.fleet import bootstrap_smoke_0029 as X
from scripts.fleet.migration import binding,checked,retain
R=Path(__file__).resolve().parents[1]
PLAN=R/'diagnostics/vm-fleet-20260912/control/bootstrap-smoke-0029-001/plan.json'
def test_actual_closed_authority():
 p,j=X.load(binding(PLAN));assert j['workbook']=='0029' and len(checked(checked(p['authority'])['inspection'])['legacy_calls'])==8
@pytest.mark.parametrize('field,value',[('host','hhu-server-4'),('slot',1),('slot',True),('new_owner','hhu-server-3:slot-2:other'),('known_jobs',[{}]),('pool_roots',['/tmp/pool']),('smoke_verification',{})])
def test_closed_bootstrap_changes_reject(tmp_path,field,value):
 p=checked(binding(PLAN));p[field]=value
 with pytest.raises((ValueError,KeyError)):X.load(retain(tmp_path/'p.json',p))
def status():
 p=checked(binding(PLAN));j=checked(p['job']);s=dict(at=100,model_limit=1,publication_limit=1,operations=[],workbooks=[dict(workbook='0029',state='hold',fence=0,claim_id=None,owner=None,version=None,source_sha256=j['source']['sha256'],reason=p['expected_reason']),dict(p['protected_0270'])]);return p,j,s
@pytest.mark.parametrize('field,value',[('fence',1),('claim_id','old'),('owner','other'),('version','old'),('source_sha256','wrong'),('state','ready')])
def test_consumed_or_changed_custody_rejects(field,value):
 p,j,s=status();s['workbooks'][0][field]=value
 with pytest.raises(ValueError):X.central_gate(s,p,j)
@pytest.mark.parametrize('state',['active','hold'])
def test_any_uncertain_or_active_operation_rejects(state):
 p,j,s=status();s['operations']=[dict(state=state,workbook='0190')]
 with pytest.raises(ValueError):X.central_gate(s,p,j)
def test_protected_hold_and_global_limit():
 p,j,s=status();assert X.central_gate(s,p,j)['fence']==0
 s['workbooks'][1]['fence']=6
 with pytest.raises(ValueError):X.central_gate(s,p,j)
 p,j,s=status();s['model_limit']=2
 with pytest.raises(ValueError):X.central_gate(s,p,j)
def test_reviewed_host_primitives_exact():
 source=(R/'diagnostics/vm-fleet-20260912/control/source-first-custody-004/implementation/scripts/fleet/reconstruction_custody.py').read_text();tree=ast.parse(source)
 for name in ('owned_argv','reconcile_host','process_cwd','remote_launch'):
  n=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
  assert ast.dump(n)==ast.dump(ast.parse(X.function_source(name)).body[0])
def test_unknown_orphan_rejects():
 p,j,s=status();process=dict(pid=9,ppid=1,argv=['python','unknown.py'],task_related=True,pool=None)
 with pytest.raises(ValueError,match='orphan'):X.reconcile_host(p['host'],s,[process],{},[],now=100,slot=2)
def test_readonly_and_lost_reply(tmp_path,monkeypatch):
 p,j,s=status();p.update(coordinator_receipt=retain(tmp_path/'coordinator.json',dict(url='fake',private_directory=str(tmp_path))))
 monkeypatch.setattr(X,'load',lambda row:(p,j));monkeypatch.setattr(X,'local_custody',lambda p:dict(stopped=True));monkeypatch.setattr(X,'ssh',lambda *a,**kw:dict(status='checked'))
 calls=[]
 class C:
  def __init__(self,*a):pass
  def call(self,action,**kw):
   calls.append(action)
   if action=='status':return s
   raise TimeoutError('reply lost')
 monkeypatch.setattr(X,'Client',C)
 assert X.launch({'test':1})['workers_started']==0 and calls==['status']
 with pytest.raises(TimeoutError):X.launch({'test':1},tmp_path/'journal',execute=True)
 assert (tmp_path/'journal/intent.json').exists() and checked(binding(tmp_path/'journal/uncertain.json'))['automatic_retry'] is False
 with pytest.raises(ValueError,match='journal exists'):X.launch({'test':1},tmp_path/'journal',execute=True)
 assert calls.count('reconcile')==1
