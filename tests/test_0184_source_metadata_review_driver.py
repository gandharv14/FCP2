"""Guard-history tests use synthetic files only; no Calls or leases are mutated."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,hashlib
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def driver(tmp_path,monkeypatch):
 path=ROOT/'diagnostics/requested-fixes-20260910/continue_0184_source_metadata_reviews.py'
 spec=importlib.util.spec_from_file_location('metadata_review_driver',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 for key,name in [('HOME','new'),('OLD_HOME','old'),('RECOVERY_HOME','recovery')]:monkeypatch.setattr(m,key,tmp_path/name)
 def binding(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
 def checked(b):assert binding(b['path'])==b;return json.loads(Path(b['path']).read_text())
 def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
 monkeypatch.setattr(m,'binding',binding);monkeypatch.setattr(m,'checked',checked)
 return m,put

def test_runs_mechanics_then_only_22_reviews_then_finalize(driver,monkeypatch):
 m,_=driver;seen=[];monkeypatch.setattr(m,'previous',lambda _:None)
 def guarded(action,prior,number):seen.append((action,prior,number));return {'result':number}
 monkeypatch.setattr(m,'guarded',guarded);monkeypatch.setattr(m,'checked',lambda b:{'status':'CALL_COMPLETE'})
 m.run();assert seen[0]==('mechanics',m.RECONCILIATION,1)
 assert [x[0] for x in seen]==['mechanics']+['call']*22+['finalize']
 assert [x[2] for x in seen]==list(range(1,25))

def test_failed_guard_stops_without_next_review(driver,monkeypatch):
 m,_=driver;seen=[];monkeypatch.setattr(m,'previous',lambda _:None)
 def guarded(*a):seen.append(a);raise ValueError('retained failure')
 monkeypatch.setattr(m,'guarded',guarded)
 with pytest.raises(ValueError):m.run()
 assert len(seen)==1

@pytest.fixture
def history(driver,monkeypatch):
 m,put=driver;initial=put(m.OLD_HOME/'initial.json',{});monkeypatch.setattr(m,'INITIAL',initial)
 prior=initial;nodes=[]
 for number in range(1,45):
  old=number<=43;parent=m.OLD_HOME if old else m.RECOVERY_HOME;action='begin' if number==1 else 'call';d=parent/('%03d-%s'%(number,action));who=m.ORIGINAL_DRIVER if old else m.RECOVERY_DRIVER
  value=dict(schema_version=m.SCHEMA,driver=who,snapshot=m.LEGACY_SNAPSHOT,initial=initial,descriptor={},selection={},action=action,status='ROUND_READY' if number==1 else 'CALL_COMPLETE',prior=prior)
  if not old:value['recovery']=m.FAILED_DEPENDENCY
  result=put(d/'result.json',value);intent=put(d/'intent.json',m.legacy_intent(action,prior,who));log=put(d/'step.log',{})
  resource=put(d/'resource.json',dict(intent=intent,log=log,returncode=0,failure=None));put(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
  if number==43:monkeypatch.setattr(m,'LAST_ORIGINAL',result)
  prior=result;nodes.append(d)
 return m,put,prior,nodes

def test_full_legacy43_and_recovery44_chain(history):
 m,_,last,_=history
 assert len(m.legacy_history(last,{'descriptor':{}},{},SimpleNamespace(encode=lambda x:x)))==44

@pytest.mark.parametrize('fault',['driver','recovery','skip','old_failure','log','snapshot'])
def test_history_substitution_rejects(history,fault):
 m,put,last,nodes=history
 if fault=='old_failure':
  r=m.checked(m.binding(nodes[10]/'resource.json'));r['returncode']=1;put(nodes[10]/'resource.json',r)
 elif fault=='log':put(nodes[-1]/'step.log',{'tampered':True})
 else:
  v=m.checked(last)
  if fault=='driver':v['driver']=m.ORIGINAL_DRIVER
  elif fault=='recovery':v['recovery']={'wrong':'failure'}
  elif fault=='snapshot':v['snapshot']={'wrong':'code'}
  else:v['prior']=m.checked(m.LAST_ORIGINAL)['prior']
  last=put(nodes[-1]/'result.json',v)
 with pytest.raises(AssertionError):m.legacy_history(last,{'descriptor':{}},{},SimpleNamespace(encode=lambda x:x))
