"""Real successor-code boundaries; all provider traffic is synthetic or disabled."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from scripts.fleet import build_0190_successor as B


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    outcome=B.read(B.build(tmp_path_factory.mktemp('0190-successor')/'build'))
    snapshot=B.read(outcome['snapshot'])
    return outcome,Path(snapshot['root'])


def child(root,code):
    env={k:v for k,v in os.environ.items() if not k.startswith('HARBOR_')}
    prefix='import sys\nsys.path.insert(0,'+repr(str(root))+')\n'
    result=subprocess.run([sys.executable,'-I','-B','-c',prefix+code],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


def test_patch_context_is_exact():
    with pytest.raises(ValueError,match='exactly once'):B.apply(b'old old',[('old','new')])
    with pytest.raises(ValueError,match='exactly once'):B.apply(b'other',[('old','new')])


def test_all_future_public_model_entrypoints_require_fleet(built):
    child(built[1],'''
from scripts.batch import harbor_model_executor as E
import xl_transport as T
for call in (lambda:E.execute(None,None,credential='fixture'),lambda:T.read_response(None,timeout=1),lambda:T.model_slot().__enter__()):
 try:call()
 except ValueError as exc:assert 'requires live fleet' in str(exc)
 else:raise AssertionError('ungated model entrypoint allowed')
''')


def test_legacy_inspection_is_exact_and_does_not_label_old_calls_gated(built):
    child(built[1],'''
import os,copy
from pathlib import Path
from scripts.batch import harbor_normalization_existing_review as M
from scripts.batch import harbor_model_executor as E
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
def forbidden(*a,**k):raise AssertionError('inspection invoked transport')
E._open=forbidden;E.execute=forbidden
profile=M.fleet_profile()
assert len(profile['legacy_calls'])==2
for path,row in profile['legacy_calls'].items():
 old=M.read(row['plan']);raw=Path(row['plan']['path']).read_bytes();current=copy.deepcopy(old)
 current.update(fleet_transport_policy='single-dispatch-durable-global-gate/v1',max_transport_retries=0)
 assert M.fleet_legacy_plan(Path(path),current)==old
 assert 'fleet_transport_policy' not in old and Path(row['plan']['path']).read_bytes()==raw
 wrong=copy.deepcopy(current);wrong['attempt_id']='changed'
 try:M.fleet_legacy_plan(Path(path),wrong)
 except ValueError:pass
 else:raise AssertionError('changed historical identity accepted')
try:M.fleet_legacy_plan(Path('/tmp/unlisted-call'),current)
except ValueError:pass
else:raise AssertionError('unlisted historical call accepted')
# The actual retained original normalization Call remains accepted under the
# fleet environment, without any new response, tracker write or emission.
home=Path(next(iter(profile['legacy_calls']))).parents[2]
proof=M.validate(M.record(home/'environment/normalization-existing-review-v1/0190-variable-sources/existing-review.json'))
assert proof['original'].workbook=='0190' and proof['original'].budget.ordinal==1 and proof['original'].budget.limit==2
''')


def test_profile_rejects_unreviewed_complete_inventory_change(built,tmp_path):
    import shutil,hashlib
    root=tmp_path/'changed';shutil.copytree(built[1],root)
    path=root/'plain_eligibility.py';path.write_bytes(path.read_bytes()+b'\n# unreviewed change\n')
    index=json.loads((root/'.pipeline-code.json').read_bytes());index['plain_eligibility.py']=hashlib.sha256(path.read_bytes()).hexdigest()
    (root/'.pipeline-code.json').write_bytes(B.canonical(index))
    child(root,'''
from scripts.batch import harbor_normalization_existing_review as M
try:M._fleet_predecessor(M.inventory(M.ROOT))
except ValueError as exc:assert 'unreviewed fleet successor code changes' in str(exc)
else:raise AssertionError('arbitrary code delta accepted')
''')


@pytest.mark.parametrize('uncertain',[False,True])
def test_actual_executor_uses_one_fleet_operation_and_no_transport_retry(built,tmp_path,uncertain):
    child(built[1],'''
import os,json,time,uuid,io
from pathlib import Path
from scripts.batch import harbor_model_executor as E
from scripts.fleet import model_gate as G
root=Path('''+repr(str(tmp_path))+''')
campaign=E.Campaign(root/'model-journal',root/'slots',1)
os.environ.update(HARBOR_FLEET_URL='http://127.0.0.1:8765',HARBOR_MODEL_SLOTS='1',HARBOR_MODEL_SLOT_ROOT=str(campaign.slot_root))
policy=E.Policy('fixture-project')
request=dict(model=policy.model,reasoning_effort=policy.reasoning_effort,messages=[dict(role='system',content='Fixture independent review'),dict(role='user',content='Fixture claim')])
E.save(root/'request.json',request);E.save(root/'budget.json',dict(ordinal=1,limit=2))
p=E.PreparedCall(E.record(root/'request.json'),policy,E.SemanticScope('0190','a'*64,'fixture','fixture'),1,2,str(uuid.uuid4()),(E.record(root/'budget.json'),))
class Client:
 def __init__(self):self.actions=[]
 def call(self,action,**args):
  self.actions.append((action,args))
  if action=='begin':return {'execute':True}
  assert action=='end';return {'state':'hold' if args['outcome']=='uncertain' else 'confirmed'}
client=Client();G.context=lambda:(client,dict(workbook='0190',owner='fixture',token='fixture-only',fence=1),root/'fleet-journal')
opened=[]
class Response(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*a):self.close()
def transport(*a,**k):
 opened.append(1)
 if '''+repr(uncertain)+''':raise TimeoutError('fixture uncertain')
 return Response(json.dumps(dict(id='fixture-completion',object='chat.completion',model=policy.model,created=int(time.time()),choices=[dict(finish_reason='stop',message=dict(role='assistant',content='{"passed":false}'))])).encode())
E._open=transport
result=E.execute(p,campaign,credential='fixture-not-secret')
assert len(opened)==1
assert [a for a,_ in client.actions]==['begin','end']
assert client.actions[0][1]['operation_id']==p.attempt_id
assert client.actions[0][1]['request_sha256']==E.sha(Path(p.request['path']).read_bytes())
assert client.actions[1][1]['outcome']==('uncertain' if '''+repr(uncertain)+''' else 'confirmed')
plan=json.loads((E.location(p,campaign)/'plan.json').read_bytes())
assert plan['max_transport_retries']==0 and plan['model_slots']==1
assert plan['ordinal']==1 and plan['limit']==2 and plan['attempt_id']==p.attempt_id
assert result.status==('incomplete' if '''+repr(uncertain)+''' else 'completed'),result.status
again=E.execute(p,campaign,credential='fixture-not-secret')
assert len(opened)==1 and len(client.actions)==2
''')


def test_execute_cannot_replay_exact_legacy_completed_attempt(built):
    child(built[1],'''
import os,json
from pathlib import Path
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_normalization_existing_review as M
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
profile=M.fleet_profile();home=Path(next(iter(profile['legacy_calls']))).parents[2]
receipt=M.read(M.record(home/'environment/normalization-existing-review-v1/0190-variable-sources/existing-review.json'))
s=M.selection(receipt['original_selection']);saved=M.read(receipt['prepared'])
c=receipt['campaign'];campaign=E.Campaign(Path(c['root']),Path(c['slot_root']),c['model_slots'])
p=E.PreparedCall(saved['request'],s.policy,E.SemanticScope(s.workbook,s.bindings['original_raw_source']['sha256'],M.N.LANE,'normalization-review'),s.budget.ordinal,s.budget.limit,s.budget.attempt_id,s.budget.evidence,response_mode='json_object')
os.environ.update(HARBOR_MODEL_SLOTS='1',HARBOR_MODEL_SLOT_ROOT=str(campaign.slot_root))
root=E.location(p,campaign);before={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
E._open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('legacy attempt reached transport'))
assert E.inspect(p,campaign).status=='completed'
try:E.execute(p,campaign,credential='fixture-only')
except ValueError as exc:assert 'logical call identity/configuration/budget changed' in str(exc)
else:raise AssertionError('legacy completed execution accepted prospective replay')
assert before=={x.name:x.read_bytes() for x in root.iterdir() if x.is_file()}
''')
def test_actual_retained_audit_calls_are_readable_but_never_reexecuted(built):
    audit_call_check(built[1],B.Q/'authoring/0190-generated-02',1)


def audit_call_check(root,home,count):
    child(root,'''
import os,json
from pathlib import Path
from scripts.batch import harbor_model_executor as E,harbor_audit_bridge as A
from scripts import harbor_campaign as C
home=Path('''+repr(str(home))+''')
descriptor=json.loads((home/'dispatcher-v2/first/002/result.json').read_bytes())['descriptor']
worker=C.decode(json.loads(Path(descriptor['path']).read_bytes())['worker'])
campaign=worker.campaign
os.environ.update(HARBOR_FLEET_URL='http://127.0.0.1:8765',HARBOR_MODEL_SLOTS='1',HARBOR_MODEL_SLOT_ROOT=str(campaign.slot_root))
E._open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('inspection reached provider'))
paths=sorted((home/'environment/audit-bridge').glob('batch-*/prepared.json'));assert len(paths)=='''+repr(count)+'''
for path in paths:
 prepared=A.prepared_from(json.loads(path.read_bytes()));root=E.location(prepared,campaign)
 before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
 assert isinstance(prepared.audit_context.previous,tuple)
 assert E.inspect(prepared,campaign).status=='completed'
 try:E.execute(prepared,campaign,credential='fixture-only')
 except ValueError as exc:assert 'logical call identity/configuration/budget changed' in str(exc)
 else:raise AssertionError('historical call execution accepted')
 assert before=={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
''')
