"""An expired or changed native lease cannot consume a new spawn intent."""
from pathlib import Path
import hashlib,json,sys,time
import pytest
from scripts.fleet import launch_job_v3 as L

def save(path,value):
 path.write_text(json.dumps(value,sort_keys=True));return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
def fixture(tmp_path,monkeypatch):
 now=[1000*10**9];monkeypatch.setattr(time,'time_ns',lambda:now[0])
 tracker=save(tmp_path/'tracker.json',dict(workbook_id='0077',raw_source=dict(sha256='a'*64),leases=dict(task_agent=dict(expires_at=now[0]+4600*10**9))))
 profile=save(tmp_path/'runway.json',dict(schema_version='fleet-native-job-lease-runway/v1',workbook='0077',source_sha256='a'*64,tracker=tracker,required_seconds=4500))
 job=save(tmp_path/'job.json',dict(workbook='0077',source=dict(sha256='a'*64),inputs=[profile]))
 return now,tracker,profile,job

def test_exact_fresh_custody_and_no_mutation(tmp_path,monkeypatch):
 now,t,p,j=fixture(tmp_path,monkeypatch);before={x.name:x.read_bytes()for x in tmp_path.iterdir()}
 assert L.native_runway(p,j)['remaining_ns']==4600*10**9
 assert before=={x.name:x.read_bytes()for x in tmp_path.iterdir()}

@pytest.mark.parametrize('change',['expired','boundary','changed_tracker','foreign_workbook','foreign_source','missing_input','bool_seconds','unbounded_seconds','unknown_field'])
def test_changed_expired_or_detached_custody_rejected(tmp_path,monkeypatch,change):
 now,t,p,j=fixture(tmp_path,monkeypatch)
 if change=='expired':now[0]+=5000*10**9
 elif change=='boundary':now[0]+=100*10**9
 elif change=='changed_tracker':Path(t['path']).write_text('{}')
 elif change in ('foreign_workbook','foreign_source','missing_input'):
  job=json.loads(Path(j['path']).read_bytes())
  if change=='foreign_workbook':job['workbook']='0004'
  elif change=='foreign_source':job['source']['sha256']='b'*64
  else:job['inputs']=[]
  j=save(Path(j['path']),job)
 else:
  v=json.loads(Path(p['path']).read_bytes())
  if change=='bool_seconds':v['required_seconds']=True
  elif change=='unbounded_seconds':v['required_seconds']=14401
  else:v['extra']='unknown'
  p=save(Path(p['path']),v);job=json.loads(Path(j['path']).read_bytes());job['inputs']=[p];j=save(Path(j['path']),job)
 with pytest.raises(ValueError):L.native_runway(p,j)

def test_expiry_after_preflight_is_rechecked_before_spawn_intent(tmp_path,monkeypatch):
 now,t,p,j=fixture(tmp_path,monkeypatch);assert L.native_runway(p,j)['status']=='PASS';now[0]+=101*10**9
 receipt=tmp_path/'new/process.json'
 payload=dict(receipt=str(receipt),command=[sys.executable,'-c','raise AssertionError("must not run")'],job=j,root_intent=dict(path='/retained/intent',sha256='c'*64),cwd=str(tmp_path),native_lease_runway=p)
 with pytest.raises(ValueError):L.remote_spawn(payload)
 assert not receipt.parent.exists()

def test_fresh_bound_runway_allows_one_synthetic_child(tmp_path,monkeypatch):
 import os
 now,t,p,j=fixture(tmp_path,monkeypatch);receipt=tmp_path/'new/process.json'
 payload=dict(receipt=str(receipt),command=[sys.executable,'-I','-B','-c','pass'],job=j,root_intent=dict(path='/retained/intent',sha256='c'*64),cwd=str(tmp_path),native_lease_runway=p)
 result=L.remote_spawn(payload)
 assert json.loads(receipt.read_bytes())==result
 assert os.waitpid(result['pid'],0)[1]==0
 with pytest.raises(ValueError):L.remote_spawn(payload)

def test_ssh_declares_runway_function_and_exact_launch_payload():
 source=Path(L.__file__).read_text()
 assert "function_source('native_runway')"in source
 assert "payload['launch']['native_lease_runway']=p['native_lease_runway']"in source

@pytest.mark.parametrize('change',[None,'foreign_original','unknown_field','changed_bytes','null_original'])
def test_normalized_tracker_retains_exact_original_source(tmp_path,monkeypatch,change):
 now,t,p,j=fixture(tmp_path,monkeypatch)
 original=tmp_path/'source.xlsx';original.write_bytes(b'original workbook')
 source=dict(path=str(original),sha256=hashlib.sha256(original.read_bytes()).hexdigest())
 v=json.loads(Path(t['path']).read_bytes());v['raw_source']={'sha256':'d'*64};v['bindings']={'original_raw_source':dict(source)}
 if change=='foreign_original':v['bindings']['original_raw_source']['sha256']='e'*64
 elif change=='unknown_field':v['bindings']['original_raw_source']['extra']='unbound'
 elif change=='null_original':v['bindings']['original_raw_source']=None
 elif change=='changed_bytes':original.write_bytes(b'changed workbook')
 t=save(Path(t['path']),v);v=json.loads(Path(p['path']).read_bytes());v.update(tracker=t,source_sha256=source['sha256']);p=save(Path(p['path']),v)
 v=json.loads(Path(j['path']).read_bytes());v.update(source=source,inputs=[p]);j=save(Path(j['path']),v)
 if change:
  with pytest.raises(ValueError):L.native_runway(p,j)
 else:assert L.native_runway(p,j)['status']=='PASS'

@pytest.mark.parametrize('remaining,maximum,ok',[(2000,3330,True),(3330,3330,True),(3331,3330,False),(1500,3330,False),(2000,True,False),(2000,1500,False),(2000,14401,False)])
def test_bounded_renewal_first_window(tmp_path,monkeypatch,remaining,maximum,ok):
 now,t,p,j=fixture(tmp_path,monkeypatch)
 v=json.loads(Path(p['path']).read_bytes());v.update(required_seconds=1500,maximum_seconds=maximum);p=save(Path(p['path']),v)
 v=json.loads(Path(j['path']).read_bytes());v['inputs']=[p];j=save(Path(j['path']),v)
 now[0]+=(4600-remaining)*10**9
 if ok:assert L.native_runway(p,j)['remaining_ns']==remaining*10**9
 else:
  with pytest.raises(ValueError):L.native_runway(p,j)
