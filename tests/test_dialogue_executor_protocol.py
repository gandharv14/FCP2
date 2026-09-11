import urllib.error
import dataclasses
import importlib.util
import json
from pathlib import Path
import sys
import uuid
import pytest
from scripts.batch import harbor_model_executor as E
import io
import time
from types import SimpleNamespace
TOKEN='fixture-credential-not-secret'
class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
def response_bytes(policy,content):
    return json.dumps(dict(id=str(uuid.uuid4()),object='chat.completion',model=policy.model,
        created=time.time_ns()//10**9,choices=[dict(finish_reason='stop',message=dict(role='assistant',content=json.dumps(content)))])).encode()
def setup(tmp_path,monkeypatch):
    root=tmp_path.resolve();campaign=E.Campaign(root/'journal',root/'slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root));monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
    def prepare(scope,ordinal,limit):
        own=root/('prepared-'+str(uuid.uuid4()));own.mkdir();policy=E.Policy('fixture-project')
        request=dict(model=policy.model,reasoning_effort='high',messages=[dict(role='system',content='Synthetic independent role'),dict(role='user',content='Synthetic claim')])
        (own/'request.json').write_text(json.dumps(request));(own/'budget.json').write_text(json.dumps(dict(scope=scope,ordinal=ordinal,limit=limit)))
        workbook,lane=scope.split('/',1)
        return E.PreparedCall(E.record(own/'request.json'),policy,E.SemanticScope(workbook,'a'*64,lane,'fixture'),ordinal,limit,str(uuid.uuid4()),(E.record(own/'budget.json'),))
    return campaign,prepare
B=SimpleNamespace(TOKEN=TOKEN,Response=Response,response_bytes=response_bytes)
@pytest.fixture
def pair(tmp_path,monkeypatch):
    campaign,prepare=setup(tmp_path,monkeypatch)
    def make(role='dialogue-writer',ordinal=1,mode='text',lane='harbor-dialogue-author',limit=2):
        p=prepare(scope='0001/'+lane,ordinal=ordinal,limit=limit)
        return dataclasses.replace(p,scope=dataclasses.replace(p.scope,budget_id=role),response_mode=mode)
    calls=[]
    def transport(req,timeout):
        calls.append(req.data);return B.Response(B.response_bytes(E.Policy('fixture-project'),{'schema_version':'dialogue-slot-prose/v1','slots':{'one':'synthetic prose'}}))
    monkeypatch.setattr(E,'_open',transport)
    return campaign,make,calls

@pytest.mark.parametrize('role',['dialogue-writer','dialogue-paraphraser'])
def test_real_completed_text_to_json_second_preserves_both_calls_and_budget(pair,role):
    c,make,calls=pair;a=make(role);b=make(role,2,'json_object')
    first=E.execute(a,c,credential=B.TOKEN);old={str(p):p.read_bytes() for p in E.location(a,c).iterdir() if p.is_file()}
    second=E.execute(b,c,credential=B.TOKEN)
    assert first.status==second.status=='completed' and first.call.parsed['path'].endswith('content.txt') and second.call.parsed['path'].endswith('parsed.json')
    assert len(calls)==2 and E.execute(b,c,credential=B.TOKEN).reused and len(calls)==2
    assert old=={str(p):p.read_bytes() for p in E.location(a,c).iterdir() if p.is_file()}
    assert json.loads((E.location(b,c)/'prior-attempts.json').read_text())==[E.record(E.location(a,c)/'outcome.json')]
    with pytest.raises(E.ExecutorError):E.execute(dataclasses.replace(b,ordinal=3),c,credential=B.TOKEN)
    assert len(calls)==2

@pytest.mark.parametrize('role,lane', [('dialogue-review','harbor-dialogue-author'),('naturalization-writer','harbor-dialogue-author'),('dialogue-writer','harbor-instruction-naturalizer'),('dialogue-paraphraser-other','harbor-dialogue-author')])
def test_other_semantic_scopes_cannot_change_parser(pair,role,lane):
    c,make,calls=pair;E.execute(make(role,lane=lane),c,credential=B.TOKEN)
    with pytest.raises(E.ExecutorError,match='prior semantic'):E.execute(make(role,2,'json_object',lane),c,credential=B.TOKEN)
    assert len(calls)==1

def test_json_to_text_rejects(pair):
    c,make,calls=pair;E.execute(make(mode='json_object'),c,credential=B.TOKEN)
    with pytest.raises(E.ExecutorError):E.execute(make(ordinal=2,mode='text'),c,credential=B.TOKEN)
    assert len(calls)==1

@pytest.mark.parametrize('field',['policy','limit','scope'])
def test_changed_policy_limit_or_scope_rejects(pair,field):
    c,make,calls=pair;E.execute(make(),c,credential=B.TOKEN);b=make(ordinal=2,mode='json_object')
    if field=='policy':b=dataclasses.replace(b,policy=dataclasses.replace(b.policy,project_id='different'))
    elif field=='limit':b=dataclasses.replace(b,limit=1)
    else:b=dataclasses.replace(b,scope=dataclasses.replace(b.scope,original_sha256='b'*64))
    with pytest.raises(E.ExecutorError):E.execute(b,c,credential=B.TOKEN)
    assert len(calls)==1

@pytest.mark.parametrize('file',['response.raw.json','transport-00.body.raw','request.json','outcome.json','plan.json','budget-00.snapshot'])
def test_prior_actual_call_integrity_required(pair,file):
    c,make,calls=pair;a=make();E.execute(a,c,credential=B.TOKEN);p=E.location(a,c)/file;p.write_bytes(p.read_bytes()+b' ')
    # JSON whitespace is semantic in most retained byte receipts. plan/outcome
    # have self-derivation checks rather than their own enclosing byte hash;
    # change meaningful content for those two.
    if file in {'plan.json','outcome.json'}:
        v=json.loads(p.read_bytes());v['attempt_id' if file=='plan.json' else 'status']='changed';p.write_text(json.dumps(v))
    with pytest.raises(Exception):E.execute(make(ordinal=2,mode='json_object'),c,credential=B.TOKEN)
    assert len(calls)==1

@pytest.mark.parametrize('fault',['incomplete','rejected'])
def test_incomplete_or_rejected_prior_cannot_enter_new_protocol(pair,monkeypatch,fault):
    c,make,calls=pair;a=make()
    def invalid(*args):
        calls.append(b'fixture')
        if fault=='incomplete':raise urllib.error.URLError('opaque synthetic delivery state')
        return B.Response(b'{"object":"not-a-completion"}')
    monkeypatch.setattr(E,'_open',invalid)
    assert E.execute(a,c,credential=B.TOKEN).status in {'incomplete','response_rejected'}
    with pytest.raises(E.ExecutorError):E.execute(make(ordinal=2,mode='json_object'),c,credential=B.TOKEN)
    assert len(calls)==1

def test_second_transport_interruption_stays_spent_and_never_replays(pair):
    c,make,calls=pair;a=make();E.execute(a,c,credential=B.TOKEN);b=make(ordinal=2,mode='json_object')
    def interrupt(point):
        if point=='after_dispatch_started':raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):E.execute(b,c,credential=B.TOKEN,_failpoint=interrupt)
    assert E.execute(b,c,credential=B.TOKEN).status=='incomplete' and len(calls)==1
