import copy,json
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_launch as L

@pytest.fixture
def selected():
 p=dict(expected_fence=6,expected_owner='host:author',worker_outcome=dict(path='/old/outcome',sha256='a'*64))
 plan=dict(workbook='0190',source_sha256='b'*64,code_root='/code/'+'c'*64)
 w=dict(workbook='0190',state='hold',fence=6,owner=p['expected_owner'],source_sha256=plan['source_sha256'],version='c'*64,reason=json.dumps(p['worker_outcome'],sort_keys=True))
 status=dict(at=100,workbooks=[w],operations=[])
 return p,plan,status

def cap():return SimpleNamespace(validate_occupancy=lambda s:None)

def test_unrelated_confirmed_or_live_model_allowed(selected):
 p,plan,s=selected
 s['workbooks'].append(dict(workbook='0200',state='active',owner='other',fence=2,expires=101))
 s['operations']=[dict(workbook='0200',kind='model',state='active',owner='other',fence=2)]
 assert L.central(s,p,plan,cap(),now=100)['fence']==6

@pytest.mark.parametrize('field,value',[('fence',7),('owner','foreign'),('source_sha256','d'*64),('version','old'),('reason','unrelated'),('state','active')])
def test_changed_stopped_authority_rejected(selected,field,value):
 p,plan,s=selected;s['workbooks'][0][field]=value
 with pytest.raises(ValueError):L.central(s,p,plan,cap(),now=100)

@pytest.mark.parametrize('operation',[dict(workbook='other',kind='model',state='hold'),dict(workbook='other',kind='publication',state='active'),dict(workbook='0190',kind='model',state='active'),dict(workbook='other',kind='model',state='active',owner='lost',fence=1)])
def test_uncertain_publication_target_and_unknown_owner_block(selected,operation):
 p,plan,s=selected;s['operations']=[operation]
 with pytest.raises(ValueError):L.central(s,p,plan,cap(),now=100)

def test_stale_observation_blocks(selected):
 p,plan,s=selected
 with pytest.raises(ValueError):L.central(s,p,plan,cap(),now=161)

def test_real_once_journal_retains_uncertainty_without_retry(tmp_path,monkeypatch,selected):
 p,plan,s=selected;p.update(coordinator_receipt={'path':str(tmp_path/'coordinator.json')},capacity_policy={},python='/bin/python',host='hhu-server-7',publisher={'path':'/bound/publisher'},plan={'path':'/bound/plan','sha256':'e'*64},owner='publisher')
 events=[]
 class Client:
  def __init__(self,*a):pass
  def call(self,method,**kw):
   events.append(method)
   if method=='status':return s
   assert method=='reconcile';assert (tmp_path/'journal/intent.json').exists()
   return dict(state='ready',fence=6)
 monkeypatch.setattr(L,'load',lambda r:(p,plan,{}));monkeypatch.setattr(L,'checked',lambda r:dict(url='fixture',private_directory=str(tmp_path)))
 monkeypatch.setattr(L,'Client',Client);monkeypatch.setattr(L,'module',lambda *a:cap());monkeypatch.setattr(L,'central',lambda *a,**k:s['workbooks'][0]);monkeypatch.setattr(L,'ssh',lambda *a:dict(status='PUBLICATION_VALIDATED_NOT_STARTED'))
 def foreground(*a,**k):
  # Prove a separate dispatcher can acquire the same real flock during owning SSH.
  with L.lock(tmp_path/'job-launch.lock',blocking=False):pass
  return SimpleNamespace(returncode=1)
 monkeypatch.setattr(L.subprocess,'run',foreground)
 with pytest.raises(ValueError):L.launch({},tmp_path/'journal',True)
 assert (tmp_path/'journal/uncertain.json').exists() and events.count('reconcile')==1
 with pytest.raises(ValueError,match='consumed'):L.launch({},tmp_path/'journal',True)
 assert events.count('reconcile')==1

@pytest.mark.parametrize('remaining,passes,short_wait',[(7000,False,False),(6999,False,False),(7001,True,False),(3820,False,True),(3819,False,True),(3821,True,True)])
def test_native_exact_combined_wait_execution_runway(tmp_path,monkeypatch,remaining,passes,short_wait):
 import hashlib,pathlib,time
 def put(name,value):
  q=tmp_path/name;q.write_text(json.dumps(value));return dict(path=str(q),sha256=hashlib.sha256(q.read_bytes()).hexdigest())
 publisher=put('publisher.py',{})
 manifest=put('manifest.json',dict(publisher=publisher,dependencies={}))
 ready=put('ready.json',dict(status='PUBLICATION_READY',publication_performed=False))
 job=put('job.json',dict(workbook='0190',version='code',source=dict(sha256='s'),inputs=[],run=str(tmp_path/'old-run'),result=ready['path']))
 intent=put('intent.json',dict(job=job,owner='old'))
 outcome=put('outcome.json',dict(returncode=0,failure_type=None,result=ready,intent=intent))
 process=put('process.json',dict(pid=999999999,start_ticks='1'))
 plan=put('plan.json',dict(workbook='0190',code_root='/code',source_sha256='s',ready=ready,journal=str(tmp_path/'pub')))
 tracker=put('tracker.json',dict(leases=dict(task_agent=dict(expires_at=(100+remaining)*10**9))))
 if short_wait:
  from scripts.fleet import publication_wait as W
  auth=pathlib.Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
  wait=pathlib.Path(W.__file__)
  wb=dict(path=str(wait),sha256=hashlib.sha256(wait.read_bytes()).hexdigest())
  manifest=put('manifest.json',dict(publisher=publisher,dependencies=dict(publication_wait=wb)))
  policy=put('wait.json',dict(schema_version=W.SCHEMA,workbook='0190',source_sha256='s',maximum_wait_seconds=120,authorization=dict(path=str(auth),sha256=W.AUTHORIZATION_SHA256),lease_tracker=tracker))
  value=json.loads(pathlib.Path(plan['path']).read_bytes());value.update(schema_version='harbor-fleet-remote-publication/v1',publication_wait=policy);plan=put('plan.json',value)
 proof=put('proof.json',dict(tracker=tracker,paths=dict(journal=str(tmp_path/'pub'),output=str(tmp_path/'output'),publication_root=str(tmp_path/'publication'))))
 p=dict(plan=plan,proof=proof,implementation=manifest,publisher=publisher,worker_outcome=outcome,process=process,expected_owner='old')
 original=pathlib.Path.iterdir
 monkeypatch.setattr(pathlib.Path,'iterdir',lambda self:iter(())if str(self)=='/proc'else original(self))
 monkeypatch.setattr(time,'time_ns',lambda:100*10**9)
 if passes:assert L.native(dict(profile=p))['status']=='PUBLICATION_VALIDATED_NOT_STARTED'
 else:
  with pytest.raises(ValueError,match='runway'):L.native(dict(profile=p))

@pytest.mark.parametrize('change',[None,'no-witness','wrong-archive','foreign-workbook','foreign-source','foreign-history','foreign-proof','foreign-ready','not-input','other-input'])
def test_native_completed_tracker_input_is_historical_not_rewritten(tmp_path,monkeypatch,change):
 import hashlib,pathlib,time
 def put(name,value):
  q=tmp_path/name;q.write_text(json.dumps(value));return dict(path=str(q),sha256=hashlib.sha256(q.read_bytes()).hexdigest())
 before=dict(workbook_id='0004',raw_source={'sha256':'source'},batch='b',revision=18,handoffs=[{'real':'old'}],leases=dict(task_agent=dict(expires_at=1)))
 archive=put('archive.json',before);tracker=put('tracker.json',{**before,'revision':21,'handoffs':before['handoffs']+[{'real':'new'}],'leases':dict(task_agent=dict(expires_at=9000*10**9))});old=dict(path=tracker['path'],sha256=archive['sha256'])
 publisher=put('publisher.py',{});manifest=put('manifest.json',dict(publisher=publisher,dependencies={}))
 ready=put('ready.json',dict(status='PUBLICATION_READY',publication_performed=False));inputs=[old]
 if change=='other-input':inputs.append(dict(path=publisher['path'],sha256='incorrect'))
 if change=='not-input':inputs=[]
 job=put('job.json',dict(workbook='0004',version='code',source=dict(sha256='original-source'),inputs=inputs,run=str(tmp_path/'run'),result=ready['path']))
 intent=put('intent.json',dict(job=job,owner='old'));outcome=put('outcome.json',dict(returncode=0,failure_type=None,result=ready,intent=intent));process=put('process.json',dict(pid=999999999,start_ticks=1))
 plan=put('plan.json',dict(workbook='0004',code_root='/code',source_sha256='original-source',ready=ready,journal=str(tmp_path/'pub')))
 proof=put('proof.json',dict(tracker=tracker,paths=dict(journal=str(tmp_path/'pub'),output=str(tmp_path/'output'),publication_root=str(tmp_path/'release'))))
 h=dict(schema_version='fleet-completed-tracker-input/v1',input=old,archive=archive,proof=proof,ready=ready)
 if change=='wrong-archive':h['archive']=put('foreign.json',{})
 elif change in ('foreign-workbook','foreign-source','foreign-history'):
  current=json.loads(pathlib.Path(tracker['path']).read_bytes())
  if change=='foreign-workbook':current['workbook_id']='foreign'
  elif change=='foreign-source':current['raw_source']={}
  else:current['handoffs'][0]={'foreign':'history'}
  tracker=put('tracker.json',current);v=json.loads(pathlib.Path(proof['path']).read_bytes());v['tracker']=tracker;proof=put('proof.json',v);h['proof']=proof
 elif change=='foreign-proof':h['proof']={}
 elif change=='foreign-ready':h['ready']={}
 p=dict(plan=plan,proof=proof,implementation=manifest,publisher=publisher,worker_outcome=outcome,process=process,expected_owner='old')
 if change!='no-witness':p['input_history']=put('history.json',h)
 original=pathlib.Path.iterdir;monkeypatch.setattr(pathlib.Path,'iterdir',lambda self:iter(())if str(self)=='/proc'else original(self));monkeypatch.setattr(time,'time_ns',lambda:100*10**9)
 if change:
  with pytest.raises(ValueError):L.native(dict(profile=p))
 else:
  assert L.native(dict(profile=p))['status']=='PUBLICATION_VALIDATED_NOT_STARTED'
  assert hashlib.sha256(pathlib.Path(archive['path']).read_bytes()).hexdigest()==old['sha256']

@pytest.mark.parametrize('fault',[None,'tracker','process','owning-intent','extra-file','changed-work','old-owner','missing-settlement','foreign-policy','new-tracker'])
def test_real_retained_preowning_closure_reader(tmp_path,fault):
 from pathlib import Path
 import hashlib
 h=Path('diagnostics/bulk-production/0141-publication-001').resolve()
 def bind(p):
  p=Path(p);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return bind(p)
 old=bind(h/'root-launch-004/profile.json');original=L.checked(old)
 recovery=dict(schema_version='fleet-publication-preowning-recovery/v1',prior_profile=old,root_intent=bind(h/'root-launch-004/launch-001/intent.json'),failure_proof=bind(h/'failure-custody-001/native.json'),reconciliation=bind(h/'failure-reconciliation-001/completed.json'))
 p=dict(original,expected_fence=5,expected_owner=original['owner'],owner='new-publisher',publisher=L.checked(bind(h/'implementation-004/manifest.json'))['publisher'],implementation=bind(h/'implementation-004/manifest.json'))
 plan=L.checked(bind(h/'plan-003.json'))
 if fault in ('tracker','process','owning-intent','extra-file'):
  outer=L.checked(recovery['failure_proof']);v=json.loads(outer['stdout'])
  if fault=='tracker':v['tracker']['sha256']='0'*64
  elif fault=='process':v['processes']=[{'pid':1}]
  elif fault=='owning-intent':v['owning_intent_exists']=True
  else:v['files'].append(dict(path='/foreign',sha256='x',size=1))
  outer['stdout']=json.dumps(v);recovery['failure_proof']=put('bad-proof.json',outer)
 elif fault=='new-tracker':
  v=L.checked(p['proof']);v['tracker']['sha256']='0'*64;p['proof']=put('new-proof.json',v)
 elif fault=='changed-work':plan['ready']={'path':'/foreign','sha256':'x'}
 elif fault=='old-owner':p['expected_owner']='other'
 elif fault=='missing-settlement':
  v=L.checked(recovery['reconciliation']);v['result']['state']='hold';recovery['reconciliation']=put('bad-settlement.json',v)
 elif fault=='foreign-policy':
  m=L.checked(p['implementation']);m['dependencies']['capacity']={'path':'/foreign','sha256':'x'};p['implementation']=put('bad-manifest.json',m)
 p['preowning_recovery']=put('recovery.json',recovery)
 if fault:
  with pytest.raises((ValueError,AssertionError)):L.preowning(p,plan,L.checked,bind)
 else:
  retained,observed,op,wb=L.preowning(p,plan,L.checked,bind)
  assert retained['expected_fence']==4 and op['state']=='hold' and wb['fence']==5
  assert observed['tracker']['sha256']=='b2808e07470b71f445880d503ee8d5eb24b508a69fdb06a80b325dfa589d8f4d'

@pytest.mark.parametrize('fault',[None,'tracker','process','owning-intent','extra-file','changed-work','wrong-owner','missing-settlement','foreign-function','fresh-history'])
def test_retained_history_import_recovery_preserves_actual_preowning_failure(tmp_path,fault):
 from pathlib import Path
 import hashlib
 h=Path('diagnostics/bulk-production/0173-publication-001').resolve()
 def bind(p):p=Path(p);return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 def put(name,v):p=tmp_path/name;p.write_text(json.dumps(v));return bind(p)
 oldrow=bind(h/'root-launch-001/profile.json');old=L.checked(oldrow)
 recovery=dict(schema_version='fleet-publication-retained-history-import-recovery/v1',prior_profile=oldrow,root_intent=bind(h/'root-launch-001/launch-001/intent.json'),failure_proof=bind(h/'failure-custody-001/native.json'),reconciliation=bind(h/'operation-reconciliation-001/completed.json'))
 mrow=bind(h/'implementation-005/manifest.json');manifest=L.checked(mrow)
 p={**old,'expected_fence':8,'expected_owner':old['owner'],'owner':'new-publisher','publisher':manifest['publisher'],'implementation':mrow}
 plan=L.checked(bind(h/'plan-002.json'))
 if fault in ('tracker','process','owning-intent','extra-file'):
  v=L.checked(recovery['failure_proof'])
  if fault=='tracker':v['tracker']['sha256']='0'*64
  elif fault=='process':v['processes']=[dict(pid=1)]
  elif fault=='owning-intent':v['owning_intent_exists']=True
  else:v['files'].append(dict(path='/foreign',sha256='x',size=1))
  recovery['failure_proof']=put('bad-proof.json',v)
 elif fault=='changed-work':plan['ready']={}
 elif fault=='wrong-owner':p['expected_owner']='foreign'
 elif fault=='missing-settlement':
  v=L.checked(recovery['reconciliation']);v['result']['state']='hold';recovery['reconciliation']=put('bad-settlement.json',v)
 elif fault=='foreign-function':
  f=tmp_path/'publish.py';f.write_text(Path(p['publisher']['path']).read_text().replace('def controlled(', 'def unreviewed('));p['publisher']=bind(f);manifest['publisher']=p['publisher'];p['implementation']=put('manifest.json',manifest)
 elif fault=='fresh-history':plan['history']={}
 p['preowning_recovery']=put('recovery.json',recovery)
 if fault:
  with pytest.raises((ValueError,AssertionError)):L.preowning(p,plan,L.checked,bind)
 else:
  retained,observed,op,wb=L.preowning(p,plan,L.checked,bind)
  assert retained==old and observed['owning_intent_exists']is False and op['state']=='hold' and wb['fence']==8
