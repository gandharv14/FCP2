import json
from types import SimpleNamespace
import pytest
from scripts.fleet import collection_launch as C
@pytest.fixture
def data():
 result=dict(path='/publication/result.json',sha256='a'*64)
 p=dict(expected_fence=7,expected_owner='remote:publication')
 plan=dict(workbook='0190',source_sha256='b'*64,code_root='/local/imports')
 remote={**plan,'code_root':'/native/'+'c'*64}
 s=dict(remote_result=result)
 w=dict(workbook='0190',state='hold',owner=p['expected_owner'],fence=7,version='c'*64,source_sha256='b'*64,reason=json.dumps(result,sort_keys=True))
 op=dict(kind='publication',workbook='0190',owner=p['expected_owner'],fence=7,state='confirmed',evidence=json.dumps(result))
 return p,s,plan,remote,dict(at=100,workbooks=[w],operations=[op])
def check(data):
 p,s,plan,r,state=data;return C.central(state,p,s,plan,r,SimpleNamespace(validate_occupancy=lambda s:None),now=100)
def test_native_version_preserved_with_distinct_local_import_runtime(data):assert check(data)['version']=='c'*64
@pytest.mark.parametrize('field,value',[('state','hold'),('fence',6),('owner','foreign'),('evidence','{}')])
def test_unconfirmed_or_foreign_publication_blocks_collection(data,field,value):
 data[-1]['operations'][0][field]=value
 with pytest.raises(ValueError):check(data)
def test_duplicate_publication_claim_blocks_collection(data):
 data[-1]['operations'].append(dict(data[-1]['operations'][0]))
 with pytest.raises(ValueError):check(data)

def test_collection_allows_only_recognized_other_held_model(data):
 state=data[-1];held=dict(kind='model',state='hold',workbook='0413',owner='hhu-server/slot-0/retained',fence=1)
 state['operations'].append(held);state['workbooks'].append(dict(workbook='0413',state='hold',owner=held['owner'],fence=1))
 assert check(data)['workbook']=='0190'
 held['fence']=2
 with pytest.raises(ValueError):check(data)
 held['fence']=1;held['kind']='publication'
 with pytest.raises(ValueError):check(data)
 held['kind']='model';held['workbook']='0190'
 with pytest.raises(ValueError):check(data)


def test_separate_independent_completion_preserves_exact_abandoned_operation(data,monkeypatch):
 p,s,plan,remote,state=data
 op=state['operations'][0];op.update(state='abandoned',operation_id='consumed',evidence='original failure')
 original=json.loads(json.dumps(op));held=json.loads(json.dumps(state['workbooks'][0]))
 s.update(remote_plan={'path':'original'},verification_continuation={'path':'certificate'})
 monkeypatch.setattr(C.Q,'verification_continuation',lambda *a:({'reconciliation':{'path':'settled'}},{},original))
 monkeypatch.setattr(C,'checked',lambda r:{'after':{'workbooks':[held]}})
 assert check(data)==held and op['state']=='abandoned'
 op['evidence']='changed original failure'
 with pytest.raises(ValueError,match='abandoned operation'):
  check(data)
 op.update(original);state['workbooks'][0]['reason']='different custody'
 with pytest.raises(ValueError,match='custody|held outcome'):
  check(data)


def test_separate_verification_fresh_native_rehashes_released_tracker(tmp_path):
 import hashlib
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 tracker=put('tracker.json',dict(execution_state='PROMOTED',leases={'task_agent':None,'publication':None}))
 publication=put('publication.json',{'status':'PROMOTED'});resource=put('resource.json',{'returncode':0})
 verification=put('verification.json',{'passed':True});plan={'path':'original-plan','sha256':'a'*64}
 profile=put('profile.json',dict(plan=plan,publication=publication,resource=resource,tracker=tracker))
 result=put('result.json',dict(status='REMOTE_VERIFIED',plan=plan,model_calls=0,publication=publication,verification=verification,verification_continuation=profile))
 payload=dict(result=result,plan=plan,publisher={'path':'/unused/publisher'},verification_continuation=profile)
 # Use the actual native function with a private empty /proc directory view.
 from pathlib import Path
 original=Path.iterdir
 from unittest.mock import patch
 def entries(p):return iter(())if str(p)=='/proc'else original(p)
 with patch.object(Path,'iterdir',entries):
  assert C.native(payload)['status']=='VERIFIED_PUBLICATION_STOPPED'
  Path(tracker['path']).write_text('{}')
  with pytest.raises(ValueError,match='evidence changed'):C.native(payload)


def test_completed_checkpoint_collection_uses_same_verified_successor(data,monkeypatch):
 p,s,plan,remote,state=data
 def b(path):return dict(path=path,sha256=path)
 source=b('/source');source['sha256']=remote['source_sha256']
 selected=dict(schema_version='fleet-completed-instruction-continuation/v1',helper=b('/helper'),workbook=remote['workbook'],source=source,bridge_profile=b('/bridge'),facade=dict(original=remote['code_root'],selected='/current/successor'))
 bridge=dict(schema_version='fleet-completed-checkpoint-takeover/v1',worker_root='/current/successor',source=source,workbook=remote['workbook'])
 remote['completed_checkpoint']=dict(reader=selected['helper'],plan=b('/selected'))
 read=lambda row:selected if row==b('/selected')else bridge
 monkeypatch.setattr(C.P,'checked',read);monkeypatch.setattr(C.Q,'checked',read)
 state['workbooks'][0]['version']='successor'
 assert C.Q.remote_version(remote)=='successor'
 assert check(data)['version']=='successor'
 state['workbooks'][0]['version']='c'*64
 with pytest.raises(ValueError,match='custody'):check(data)
 bridge['worker_root']='/foreign'
 with pytest.raises(ValueError,match='authority'):C.Q.remote_version(remote)

@pytest.mark.parametrize('changed',[False,True])
def test_collection_preserves_authenticated_publication_version_custody(data,tmp_path,monkeypatch,changed):
 p,s,plan,remote,state=data
 f=tmp_path/'original-job.json';f.write_text(json.dumps({'version':state['workbooks'][0]['version']}));local=C.binding(f);native=dict(path='/native/original-job.json',sha256=local['sha256'])
 c=tmp_path/'custody.json';c.write_text(json.dumps(dict(schema_version='fleet-publication-version-custody/v1',files=[dict(original=native,local=local)])));s['version_custody']=C.binding(c)
 monkeypatch.setattr(C.P,'publication_version',lambda plan,read:read(native)['version'])
 if changed:f.write_text('{}')
 if changed:
  with pytest.raises(ValueError):C.Q.remote_version(remote,s['version_custody'])
  with pytest.raises(ValueError):check(data)
 else:
  assert C.Q.remote_version(remote,s['version_custody'])==state['workbooks'][0]['version']
  assert check(data)==state['workbooks'][0]
