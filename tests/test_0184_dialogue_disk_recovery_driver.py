from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,hashlib
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def driver(tmp_path,monkeypatch):
 path=ROOT/'diagnostics/requested-fixes-20260910/resume_0184_dialogue_partitions.py'
 spec=importlib.util.spec_from_file_location('disk_recovery_driver',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 monkeypatch.setattr(m,'HOME',tmp_path/'new');monkeypatch.setattr(m,'OLD_HOME',tmp_path/'old')
 def binding(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
 def checked(b):assert binding(b['path'])==b;return json.loads(Path(b['path']).read_text())
 def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
 monkeypatch.setattr(m,'binding',binding);monkeypatch.setattr(m,'checked',checked)
 return m,put

def test_resume_only_24_unspent_calls_then_finalize(driver,monkeypatch):
 m,_=driver;seen=[];monkeypatch.setattr(m,'preflight',lambda:None)
 def guarded(action,prior,number):seen.append((action,prior,number));return {'result':number}
 monkeypatch.setattr(m,'guarded',guarded);monkeypatch.setattr(m,'checked',lambda b:{'status':'CALL_COMPLETE'})
 m.run();assert seen[0]==('call',m.RECOVERY_START,44)
 assert [x[2] for x in seen]==list(range(44,69)) and seen[-1][0]=='finalize'
 assert len([x for x in seen if x[0]=='call'])==24

def test_new_failure_stops_without_advancing(driver,monkeypatch):
 m,_=driver;seen=[];monkeypatch.setattr(m,'preflight',lambda:None)
 def guarded(*a):seen.append(a);raise ValueError('retained failure')
 monkeypatch.setattr(m,'guarded',guarded)
 with pytest.raises(ValueError):m.run()
 assert len(seen)==1

@pytest.fixture
def history(driver,monkeypatch):
 m,put=driver;initial=put(m.OLD_HOME/'initial.json',{});monkeypatch.setattr(m,'INITIAL',initial)
 prior=initial;original={'descriptor':{}};selection={};nodes=[]
 for number in range(1,45):
  old=number<=43;parent=m.OLD_HOME if old else m.HOME;action='begin' if number==1 else 'call';d=parent/('%03d-%s'%(number,action));who=m.ORIGINAL_DRIVER if old else m.binding(m.__file__)
  value=dict(schema_version=m.SCHEMA,driver=who,snapshot=m.SNAPSHOT,initial=initial,descriptor={},selection={},action=action,status='ROUND_READY' if number==1 else 'CALL_COMPLETE',prior=prior)
  if not old:value['recovery']=m.FAILED
  result=put(d/'result.json',value);intent=put(d/'intent.json',m.intent_value(action,prior,who));log=put(d/'step.log',{})
  resource=put(d/'resource.json',dict(intent=intent,log=log,returncode=0,failure=None));put(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
  if number==43:monkeypatch.setattr(m,'RECOVERY_START',result)
  prior=result;nodes.append(d)
 return m,put,original,selection,SimpleNamespace(encode=lambda x:x),prior,nodes

def test_old_and_new_namespaces_form_one_exact_chain(history):
 m,_,original,selection,K,last,_=history
 assert len(m.guarded_history(last,original,selection,K))==44

@pytest.mark.parametrize('fault',['driver','recovery','skip','old_failure'])
def test_history_substitution_rejects(history,fault):
 m,put,original,selection,K,last,nodes=history
 if fault=='old_failure':
  r=m.checked(m.binding(nodes[10]/'resource.json'));r['returncode']=1;put(nodes[10]/'resource.json',r)
 else:
  v=m.checked(last)
  if fault=='driver':v['driver']=m.ORIGINAL_DRIVER
  elif fault=='recovery':v['recovery']={'wrong':'failure'}
  else:v['prior']=m.checked(m.RECOVERY_START)['prior']
  last=put(nodes[-1]/'result.json',v)
 with pytest.raises(AssertionError):m.guarded_history(last,original,selection,K)
