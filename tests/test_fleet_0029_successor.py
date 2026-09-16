"""Actual source/evidence fixture, with no claims, requests or provider calls."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from scripts.fleet import build_0029_successor as B,dialogue_0029_proof as P

@pytest.fixture(scope='module')
def built(tmp_path_factory):
    value=P.read(B.build(tmp_path_factory.mktemp('0029-successor')/'build'))
    return value,Path(P.read(value['snapshot'])['root'])

def child(root,code):
    env={k:v for k,v in os.environ.items() if not k.startswith('HARBOR_')}
    run=subprocess.run([sys.executable,'-I','-B','-c','import sys\nsys.path.insert(0,'+repr(str(root))+')\n'+code],env=env,capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr
    return run.stdout

def test_actual_full_source_proof_and_unchanged_claims(built):
    child(built[1],'''
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P
from scripts.batch import harbor_dialogue_pre_review_repair as Q
p=P.profile();proof=P.read(p['source_proof']);b=proof['bindings'];root=Path(P.__file__).resolve().parents[2]
fixed,actual=Q.repair_sentence(P._text(b['original_draft']),P.read(b['claims']),b['source'],root=root)
assert actual==proof and len(actual['changed_claim_ids'])==36 and actual['claims_count']==43
assert sum(len(c['instances']) for c in actual['source_formulas'])==153
assert fixed==P._text(b['proposal']) and len(actual['unchanged_claim_ids'])==7
assert not actual['qualified'] and actual['independent_review_required']
for bad in (fixed+'\\n',fixed.replace('last period','current period',1)):
 try:Q.repair_sentence(bad,P.read(b['claims']),b['source'],root=root)
 except ValueError:pass
 else:raise AssertionError('changed author draft accepted')
''')

def test_source_cardinality_exempts_only_exact_structural_occurrence(built):
    child(built[1],'''
from scripts.fleet import dialogue_0029_proof as P
p=P.read(P.profile()['source_proof']);b=p['bindings'];claims=P.read(b['claims'])['claims'];text=P.blocks(P._text(b['proposal']))[0]['senior']
assert P.cardinality_budget(text,claims[0],b['source'])=={2.0:1}
for wrong in (text+' The answer is 2.',text.replace('2-row','3-row'),text.replace('locked 2-row input block','2'),text.replace('Seller Note','Other Debt')):
 assert not P.cardinality_budget(wrong,claims[0],b['source'])
assert not P.cardinality_budget(text,claims[1],b['source'])
bad=dict(b['source'],sha256='0'*64)
try:P.cardinality_budget(text,claims[0],bad)
except ValueError:pass
else:raise AssertionError('changed source accepted')
''')

def test_actual_proposal_full_mechanics_and_old_author_pair(built):
    child(built[1],'''
import json,os
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P
from scripts.batch import harbor_dialogue_pre_review_repair as Q,harbor_transition_codec as C
from scripts.batch.harbor_readiness import skill_module
from scripts import harbor_instruction_campaign as I
from scripts.batch import harbor_model_executor as E,harbor_instruction_worker as W,harbor_dialogue_authoring as A
root=Path(P.__file__).resolve().parents[2];p=P.profile();proof=P.read(p['source_proof']);b=proof['bindings'];home=Path(b['claims']['path']).parents[3]
failed=json.loads((home/'instruction/steps/dialogue-paraphraser/blocked.json').read_bytes());selected=C.decode(failed['selection'])
V=skill_module(root,'additional-assumptions-dialogue','validate_dialogue')
result=V.check_draft(P._text(b['proposal']),P.read(b['claims']),selected.content.stage,source_workbook=b['source'])
assert result['passed'] and not result['faults'],result
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
descriptor=json.loads((home/'dispatcher-v2/first/021/descriptor.json').read_bytes());worker=I.worker_decode(descriptor['worker'])
requests=[W.request_saved(home/'instruction/steps'/kind/'request.json') for kind in ['dialogue-writer','dialogue-paraphraser']]
calls=[E.inspect(q.prepared,worker.campaign).call for q in requests]
assert all(calls)
A.verify_pair(tuple(calls),policy=requests[0].prepared.policy,claims=P.read(b['claims']),draft=P._text(b['original_draft']),round=1,root=root,workbook='0029',original_sha256=P.SOURCE_SHA)
os.environ.update(HARBOR_MODEL_SLOTS='1',HARBOR_MODEL_SLOT_ROOT=str(worker.campaign.slot_root))
E._open=lambda *a,**k:(_ for _ in ()).throw(AssertionError('legacy Call reached network'))
for request in requests:
 location=E.location(request.prepared,worker.campaign);before={x.name:x.read_bytes() for x in location.iterdir() if x.is_file()}
 try:E.execute(request.prepared,worker.campaign,credential='fixture-only')
 except ValueError as exc:assert 'logical call identity/configuration/budget changed' in str(exc)
 else:raise AssertionError('old completed Call accepted execution')
 assert before=={x.name:x.read_bytes() for x in location.iterdir() if x.is_file()}
''')

def test_all_eight_legacy_calls_are_inspect_only(built):
    child(built[1],'''
import os
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P
from scripts.batch import harbor_model_executor as E
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765';profile=P.profile();assert len(profile['legacy_calls'])==8
for path,row in profile['legacy_calls'].items():
 old=P.read(row['plan']);expected=dict(old,fleet_transport_policy='single-dispatch-durable-global-gate/v1',max_transport_retries=0)
 assert P.fleet_legacy_plan(path,expected)==old and 'fleet_transport_policy' not in old
 prepared=E.PreparedCall(old['request'],E.Policy(**old['policy']),E.SemanticScope(**old['scope']),old['ordinal'],old['limit'],old['attempt_id'],tuple(old['budget_evidence']),response_mode=old['response_mode'],audit_context=E._audit_context(old))
 campaign=E.Campaign(Path(old['campaign_root']),Path(old['slot_root']),old['model_slots'])
 assert E.inspect(prepared,campaign).status=='completed'
 expected['ordinal']=2
 try:P.fleet_legacy_plan(path,expected)
 except ValueError:pass
 else:raise AssertionError('old budget changed')
os.environ.pop('HARBOR_FLEET_URL')
try:E.execute(None,None,credential='fixture')
except ValueError as e:assert 'requires live fleet' in str(e)
else:raise AssertionError('ungated executor')
''')

def test_no_ordinary_semantic_second_round_authority_is_invented(built):
    raw=(built[1]/'scripts/batch/harbor_dialogue_semantic_retry.py').read_bytes()
    prior=P.read(B.PREVIOUS)
    assert raw==(Path(prior['root'])/'scripts/batch/harbor_dialogue_semantic_retry.py').read_bytes()
    assert b'only a completed failed semantic review permits round two' in raw
    assert not (B.TASK/'instruction/producer/requests/dialogue-review').exists()

def test_code_delta_and_source_changes_reject(built,tmp_path):
    import shutil
    root=tmp_path/'bad-code';shutil.copytree(built[1],root)
    f=root/'plain_eligibility.py';f.write_bytes(f.read_bytes()+b'\n# unauthorized\n')
    index=json.loads((root/'.pipeline-code.json').read_bytes());index['plain_eligibility.py']=P.sha(f.read_bytes());(root/'.pipeline-code.json').write_bytes(P.canonical(index))
    child(root,'''
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P
try:P.verified_code(Path(P.__file__).resolve().parents[2])
except ValueError:pass
else:raise AssertionError('unknown implementation delta accepted')
''')
    proof=P.read(built[0]['source_proof']);b=dict(proof['bindings']);b['source']=dict(b['source'],sha256='0'*64)
    with pytest.raises(ValueError,match='identity differs'):P.build_proof(b)

def test_build_replay_rejects(built):
    with pytest.raises(ValueError,match='build exists'):B.build(Path(built[0]['profile']['path']).parent)

def test_three_actual_custody_renewals_cannot_be_skipped_or_changed(built):
    child(built[1],'''
import copy
from scripts.fleet import dialogue_0029_proof as P
from scripts.batch.harbor_workbook_bootstrap import Lease
p=P.profile();rows=[P.read(x) for x in p['custody_chain']];failed=rows[0]['old_lease'];before=P.read(rows[-1]['after']);lease=Lease(**rows[-1]['lease'])
assert P.validate_custody(failed,None,before,lease)['historical_renewals']==3
wrong=copy.deepcopy(failed);wrong['revision']+=1
try:P.validate_custody(wrong,None,before,lease)
except ValueError:pass
else:raise AssertionError('failed checkpoint lease changed')
wrong=copy.deepcopy(before);wrong['lane_state']['harbor-dialogue-author']['attempt']=0 if before['lane_state']['harbor-dialogue-author']['attempt'] else 1
try:P.validate_custody(failed,None,wrong,lease)
except ValueError:pass
else:raise AssertionError('lane consumption changed across custody')
''')

@pytest.mark.parametrize('uncertain',[False,True])
def test_real_executor_one_global_slot_and_uncertain_hold(built,tmp_path,uncertain):
    child(built[1],'''
import os,json,time,uuid,io
from pathlib import Path
from scripts.batch import harbor_model_executor as E
from scripts.fleet import model_gate as G
root=Path('''+repr(str(tmp_path))+''');campaign=E.Campaign(root/'journal',root/'slots',1)
os.environ.update(HARBOR_FLEET_URL='http://127.0.0.1:8765',HARBOR_MODEL_SLOTS='1',HARBOR_MODEL_SLOT_ROOT=str(campaign.slot_root))
policy=E.Policy('fixture-project');request=dict(model=policy.model,reasoning_effort=policy.reasoning_effort,messages=[dict(role='system',content='Fixture review'),dict(role='user',content='Fixture clause')])
E.save(root/'request.json',request);E.save(root/'budget.json',dict(ordinal=1,limit=2))
p=E.PreparedCall(E.record(root/'request.json'),policy,E.SemanticScope('0029','a'*64,'fixture','fixture'),1,2,str(uuid.uuid4()),(E.record(root/'budget.json'),))
class Client:
 def __init__(self):self.actions=[]
 def call(self,action,**args):
  self.actions.append((action,args))
  if action=='begin':return {'execute':True}
  assert action=='end';return {'state':'hold' if args['outcome']=='uncertain' else 'confirmed'}
client=Client();G.context=lambda:(client,dict(workbook='0029',owner='fixture',token='fixture-only',fence=1),root/'fleet-journal')
opened=[]
class Response(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*a):self.close()
def transport(*a,**k):
 opened.append(1)
 if '''+repr(uncertain)+''':raise TimeoutError('fixture uncertain')
 return Response(json.dumps(dict(id='fixture-completion',object='chat.completion',model=policy.model,created=int(time.time()),choices=[dict(finish_reason='stop',message=dict(role='assistant',content='{"passed":false}'))])).encode())
E._open=transport;result=E.execute(p,campaign,credential='fixture-only')
assert len(opened)==1 and [a for a,_ in client.actions]==['begin','end']
assert client.actions[-1][1]['outcome']==('uncertain' if '''+repr(uncertain)+''' else 'confirmed')
plan=json.loads((E.location(p,campaign)/'plan.json').read_bytes());assert plan['max_transport_retries']==0 and plan['model_slots']==1 and plan['ordinal']==1 and plan['limit']==2
assert result.status==('incomplete' if '''+repr(uncertain)+''' else 'completed')
E.execute(p,campaign,credential='fixture-only');assert len(opened)==1 and len(client.actions)==2
''')

def test_adoption_guard_requires_exact_source_version_fence_and_no_holds(built):
    child(built[1],'''
import copy,os
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P,model_gate as G
root=Path(P.__file__).resolve().parents[2];owner=dict(workbook='0029',owner='fixture',token='fixture-only',fence=2)
base=dict(model_limit=1,publication_limit=1,workbooks=[dict(workbook='0029',state='active',owner='fixture',fence=2,source_sha256=P.SOURCE_SHA,version=root.name)],operations=[])
class Client:
 def __init__(self,value):self.value=value;self.actions=[]
 def call(self,action,**kwargs):
  self.actions.append(action)
  if action=='status':return self.value
  assert action=='heartbeat' and kwargs==owner;return {'state':'active'}
os.environ['HARBOR_FLEET_URL']='http://127.0.0.1:8765'
for field,value in [('fence',1),('owner','foreign'),('state','held'),('source_sha256','0'*64),('version','old')]:
 bad=copy.deepcopy(base);bad['workbooks'][0][field]=value;c=Client(bad);G.context=lambda:(c,owner,root/'not-created')
 try:P.require_fleet_custody(root)
 except ValueError:pass
 else:raise AssertionError('stale custody admitted')
 assert c.actions==['status']
bad=copy.deepcopy(base);bad['operations']=[dict(state='hold')];c=Client(bad);G.context=lambda:(c,owner,root/'not-created')
try:P.require_fleet_custody(root)
except ValueError:pass
else:raise AssertionError('unresolved operation admitted')
c=Client(base);G.context=lambda:(c,owner,root/'not-created');assert P.require_fleet_custody(root)['fence']==2
assert c.actions==['status','heartbeat']
''')

def test_historical_runtime_identity_is_exact_read_only_and_native_default_retained(built):
    child(built[1],'''
from dataclasses import replace
from pathlib import Path
from scripts.fleet import dialogue_0029_proof as P
from scripts.batch import harbor_transition_codec as C,harbor_runtime_readiness as R
from scripts.batch.harbor_instruction_repair import prefix
from scripts import harbor_instruction_campaign as I
p=P.profile();home=Path(P.read(p['source_proof'])['bindings']['claims']['path']).parents[3]
failed=P.read(P.record(home/'instruction/steps/dialogue-paraphraser/blocked.json'));selected=C.decode(failed['selection'])
w=I.worker_decode(P.read(P.record(home/'dispatcher-v2/first/021/descriptor.json'))['worker'])
before=P.read(P.record(home/'trackers/workbooks/0029.json'))
R.capture_runtime_producer_identity=lambda:R.RuntimeProducerIdentity('/native/bin/python','/native/bin/docker')
try:R.validate_runtime(selected.runtime,selected.observation,root=Path(P.__file__).resolve().parents[2])
except ValueError as exc:assert 'runtime command differs' in str(exc)
else:raise AssertionError('unbound historical locator accepted')
R._command=lambda *a,**k:(_ for _ in ()).throw(AssertionError('historical tools executed'))
result=prefix(before,selected,w.context,stop='dialogue');assert len(result)==10
identity=P.runtime_producer(selected);assert identity.docker_executable=='/usr/local/bin/docker'
changed=replace(selected,observation=replace(selected.observation,execution=dict(selected.observation.execution,sha256='0'*64)))
assert P.runtime_producer(changed) is None
''')
