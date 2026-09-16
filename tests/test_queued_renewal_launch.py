from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import json,time,copy
import pytest
from scripts.fleet import queued_renewal_launch as N

def fixture(tmp_path,monkeypatch):
 p=dict(coordinator={'path':str(tmp_path/'coordinator.json')},capacity={},expected_fence=4,expected_owner='old',source_sha256='s',version='v',outcome={'path':'outcome','sha256':'hash'},host='host',python='/python',plan={},runner={},guard={},guardian={})
 w=dict(workbook='book',state='hold',fence=4,owner='old',source_sha256='s',version='v',reason=json.dumps(p['outcome']))
 status=dict(at=time.time(),workbooks=[w],operations=[]);cap=SimpleNamespace(validate_occupancy=lambda v:None);seen=[]
 def keep(path,value,immutable):
  with path.open('x')as f:json.dump(value,f)
  return {'path':str(path),'sha256':'hash'}
 client=SimpleNamespace(call=lambda action:(seen.append(action)or status))
 L=SimpleNamespace(module=lambda *a:cap,Client=lambda *a:client,lock=lambda *a,**k:nullcontext(),persist=keep)
 monkeypatch.setattr(N,'load',lambda r:(p,{},dict(workbook='book'),L));monkeypatch.setattr(N,'read',lambda r:{'url':'url','private_directory':str(tmp_path)})
 return p,status,seen,cap

def test_once_launch_retains_intent_before_native_and_refuses_replay(tmp_path,monkeypatch):
 p,s,seen,cap=fixture(tmp_path,monkeypatch);out=tmp_path/'once'
 def run(command,stdout,stderr):
  assert (out/'intent.json').is_file();assert seen==['status','status'];seen.append('native');return SimpleNamespace(returncode=0)
 monkeypatch.setattr(N.subprocess,'run',run)
 N.launch({},out,execute=True)
 assert (out/'complete.json').is_file()
 with pytest.raises(ValueError,match='no replay'):N.launch({},out,execute=True)
 assert seen.count('native')==1

def test_uncertain_native_response_is_retained_and_never_replayed(tmp_path,monkeypatch):
 p,s,seen,cap=fixture(tmp_path,monkeypatch);out=tmp_path/'once';monkeypatch.setattr(N.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
 with pytest.raises(ValueError,match='reconciliation'):N.launch({},out,execute=True)
 assert (out/'uncertain.json').is_file()
 with pytest.raises(ValueError,match='no replay'):N.launch({},out,execute=True)

@pytest.mark.parametrize('field,value',[('state','active'),('fence',3),('owner','foreign'),('source_sha256','foreign'),('version','foreign'),('reason','{}')])
def test_wrong_stopped_custody_refuses_before_intent(tmp_path,monkeypatch,field,value):
 p,s,seen,cap=fixture(tmp_path,monkeypatch);s['workbooks'][0][field]=value;out=tmp_path/'once'
 with pytest.raises(ValueError):N.launch({},out,execute=True)
 assert not out.exists()

def test_other_publisher_allowed_but_target_uncertainty_blocks(tmp_path,monkeypatch):
 p,s,seen,cap=fixture(tmp_path,monkeypatch);s['operations']=[dict(workbook='other',state='active',kind='publication')];N.central(s,p,'book',cap)
 s['operations'].append(dict(workbook='book',state='hold',kind='model'))
 with pytest.raises(ValueError,match='unresolved'):N.central(s,p,'book',cap)

def test_guard_has_exact_no_model_runner_and_original_limits(tmp_path,monkeypatch):
 p,*_=fixture(tmp_path,monkeypatch);code=N.native_code(p,True,'/native/once');compile(code,'native','exec')
 assert 'timeout_seconds=3300' in code and 'memory_limit_bytes=8589934592' in code and 'grace_seconds=3' in code
 assert "p['guard'],p['guardian']" in code and "'HARBOR_FLEET_URL':'http://127.0.0.1:8765'"in code

@pytest.mark.parametrize('fault',[None,'unsettled','changed_owner','foreign_workbook'])
def test_settled_publication_uses_original_central_authority(tmp_path,monkeypatch,fault):
 from scripts.fleet import publication_launch as L
 p,status,seen,cap=fixture(tmp_path,monkeypatch)
 p['publication_recovery']={'path':'/retained-recovery','sha256':'exact'}
 selected=dict(expected_owner='old',expected_fence=4,worker_outcome=p['outcome'],preowning_recovery={},owner='successor')
 publication=dict(workbook='book',source_sha256='s',code_root='/v')
 prior=copy.deepcopy(status['workbooks'][0]);prior['reason']='settled publication failure'
 status['workbooks'][0]=copy.deepcopy(prior)
 op=dict(operation_id='old-P',state='abandoned',owner='old',fence=4,kind='publication',workbook='book')
 status['operations']=[op]
 monkeypatch.setattr(L,'load',lambda row:(selected,publication,{}))
 monkeypatch.setattr(L,'preowning',lambda *a,**kw:({}, {},op,prior))
 if fault=='unsettled':op['state']='hold'
 elif fault=='changed_owner':status['workbooks'][0]['owner']='other'
 elif fault=='foreign_workbook':publication['workbook']='other'
 if fault:
  with pytest.raises(ValueError):N.central(status,p,'book',cap)
 else:assert N.central(status,p,'book',cap)==prior

@pytest.mark.parametrize('fault',[None,'source','version','reader'])
def test_queued_version_uses_authenticated_completed_worker(fault):
 p=dict(source_sha256='source',version='selected')
 pub=dict(source_sha256='source',code_root='/historical')
 def version(value,read):
  assert value is pub and read is N.read
  if fault=='reader':raise ValueError('detached completed reader')
  return 'foreign' if fault=='version' else 'selected'
 if fault=='source':pub['source_sha256']='foreign'
 L=SimpleNamespace(publication_version=version)
 if fault:
  with pytest.raises(ValueError):N.validate_version(p,pub,L)
 else:N.validate_version(p,pub,L)

@pytest.mark.parametrize('fault',[None,'detached','changed','duplicate'])
def test_version_remote_input_uses_exact_local_custody(tmp_path,fault):
 raw=tmp_path/'copy.json';raw.write_text('{"selected":"worker"}');local=N.binding(raw);native=dict(path='/native/job.json',sha256=local['sha256'])
 item=dict(original=native,local=local);items=[item]
 if fault=='duplicate':items.append(item)
 c=tmp_path/'custody.json';c.write_text(json.dumps(dict(schema_version='fleet-publication-version-custody/v1',files=items)));p=dict(source_sha256='s',version='worker',version_custody=N.binding(c))
 if fault=='changed':raw.write_text('{}')
 def version(pub,reader):
  row=dict(native)
  if fault=='detached':row['sha256']='foreign'
  return reader(row)['selected']
 L=SimpleNamespace(publication_version=version)
 if fault:
  with pytest.raises(ValueError):N.validate_version(p,dict(source_sha256='s'),L)
 else:N.validate_version(p,dict(source_sha256='s'),L)
