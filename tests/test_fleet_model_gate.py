"""End-to-end executor journal checks against the real coordinator ledger."""
import io
import json
import uuid
import urllib.error

import pytest

from scripts.batch import harbor_model_executor as executor
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_model_review_provenance import Policy
from scripts.fleet.coordinator import Coordinator
from scripts.fleet import model_gate


@pytest.fixture
def case(tmp_path,monkeypatch):
    c=Coordinator(tmp_path/'coordinator.sqlite')
    c.enroll('0001','a'*64,'source inventory');c.reconcile('0001','old owner stopped; no history')
    claimed=c.claim('0001','vm-1/worker-1','claim','bundle')
    owner={k:claimed[k] for k in ['workbook','owner','token','fence']}
    class Direct:
        def call(self,action,**args):return getattr(c,action)(**args)
    monkeypatch.setattr(model_gate,'context',lambda:(Direct(),owner,tmp_path/'fleet-journal'))
    monkeypatch.setenv('HARBOR_FLEET_URL','http://127.0.0.1:1')
    campaign=executor.Campaign(tmp_path/'model-journal',tmp_path/'slots',model_slots=1)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    policy=Policy('fixture-project')
    req=tmp_path/'request.json';req.write_text(json.dumps(dict(model=policy.model,reasoning_effort='high',messages=[dict(role='system',content='review fixture'),dict(role='user',content='fixture') ])))
    budget=tmp_path/'budget.json';budget.write_text('{"ordinal":1,"limit":1}')
    scope=executor.SemanticScope('0001','a'*64,'fixture','fixture-budget')
    prepared=executor.PreparedCall(record(req),policy,scope,1,1,str(uuid.uuid4()),(record(budget),))
    return c,campaign,prepared


def test_timeout_is_sent_once_and_holds_global_slot(case,monkeypatch):
    c,campaign,prepared=case
    sent=[]
    def opened(*args):sent.append(1);raise TimeoutError('uncertain fixture')
    monkeypatch.setattr(executor,'_open',opened)
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='incomplete'
    assert len(sent)==1
    assert c.status()['operations'][0]['state']=='hold'
    assert c.status()['workbooks'][0]['state']=='hold'
    assert executor.execute(prepared,campaign,credential='fixture-only').status=='incomplete'
    assert len(sent)==1
    plan=json.loads((executor.location(prepared,campaign)/'plan.json').read_text())
    assert plan['max_transport_retries']==0


def test_caught_response_rejection_releases_only_with_persisted_response(case,monkeypatch):
    c,campaign,prepared=case
    monkeypatch.setattr(executor,'_open',lambda *args:io.BytesIO(b'{"malformed":"observed complete response"}'))
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='response_rejected'
    assert c.status()['operations'][0]['state']=='confirmed'
    assert c.status()['workbooks'][0]['state']=='active'
    outcome=executor.location(prepared,campaign)/'outcome.json'
    assert outcome.is_file() and (outcome.parent/'response.raw.json').is_file()


def test_normal_return_without_terminal_evidence_still_holds(case):
    c,campaign,prepared=case
    with model_gate.slot(operation_id='caught-failure',request_sha256='b'*64,outcome_path=campaign.root/'missing.json'):
        pass
    assert c.status()['operations'][0]['state']=='hold'


def test_terminal_http_error_releases_transport_without_approval_or_replay(case,monkeypatch):
    c,campaign,prepared=case;sent=[]
    def opened(*args):
        sent.append(1)
        raise urllib.error.HTTPError('https://fixture.invalid',400,'fixture',{},io.BytesIO(b'{"error":"provider rejected"}'))
    monkeypatch.setattr(executor,'_open',opened)
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='http_rejected' and result.call is None
    assert json.loads((executor.location(prepared,campaign)/'outcome.json').read_text())['semantic_approval'] is False
    assert c.status()['operations'][0]['state']=='confirmed'
    assert executor.execute(prepared,campaign,credential='fixture-only').status=='http_rejected'
    assert sent==[1]


@pytest.mark.parametrize('defect', ['body_changed','request_changed','retry','ambiguous_body','missing_receipt','receipt_binding','wrong_operation','wrong_request','response.raw.json','response.json','completed.json','parsed.json','content.txt'])
def test_terminal_http_rejection_rejects_incomplete_or_detached_evidence(case,monkeypatch,defect):
    _,campaign,prepared=case
    def opened(*args):
        raise urllib.error.HTTPError('https://fixture.invalid',400,'fixture',{},io.BytesIO(b'{"error":"provider rejected"}'))
    monkeypatch.setattr(executor,'_open',opened)
    executor.execute(prepared,campaign,credential='fixture-only')
    root=executor.location(prepared,campaign);outcome=root/'outcome.json'
    operation=prepared.attempt_id;request_hash=executor.sha((root/'request.json').read_bytes())
    if defect=='body_changed':(root/'transport-00.body.raw').write_text('modified')
    elif defect=='request_changed':(root/'request.json').write_text('modified')
    elif defect=='wrong_operation':operation='different-operation'
    elif defect=='wrong_request':request_hash='f'*64
    else:
        receipt=root/'transport-00.http-error.json';value=json.loads(receipt.read_text())
        if defect=='retry':value['retry']=True
        elif defect=='receipt_binding':value['body']['path']='/other/body.raw'
        elif defect=='ambiguous_body':
            (root/'transport-00.body.raw').write_text('{"object":"chat.completion"}')
            value['body']=executor.record(root/'transport-00.body.raw')
        elif defect in {'response.raw.json','response.json','completed.json','parsed.json','content.txt'}:
            (root/defect).write_text('{}')
        receipt.write_bytes(executor.canonical(value))
        if defect=='missing_receipt':receipt.unlink()
        # Even a newly sealed manifest cannot promote a structurally bad receipt.
        value=json.loads(outcome.read_text());value['evidence']=executor.all_files(root)
        outcome.write_bytes(executor.canonical(value))
    assert model_gate.terminal_http_rejection(outcome,operation,request_hash) is False


def test_http_error_with_completion_body_stays_uncertain(case,monkeypatch):
    c,campaign,prepared=case
    def opened(*args):
        raise urllib.error.HTTPError('https://fixture.invalid',400,'fixture',{},io.BytesIO(b'{"object":"chat.completion"}'))
    monkeypatch.setattr(executor,'_open',opened)
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='incomplete' and result.call is None
    assert c.status()['operations'][0]['state']=='hold'


def test_lost_begin_response_never_sends_or_retries(case,monkeypatch):
    c,campaign,prepared=case
    direct,owner,journal=model_gate.context()
    calls=[]
    class Lost:
        def call(self,action,**args):
            calls.append(action)
            result=direct.call(action,**args)
            if action=='begin':raise TimeoutError('commit happened; response lost')
            return result
    monkeypatch.setattr(model_gate,'context',lambda:(Lost(),owner,journal))
    monkeypatch.setattr(executor,'_open',lambda *args:pytest.fail('must not dispatch after lost reservation response'))
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='incomplete'
    assert calls==['begin']
    assert c.status()['operations'][0]['state']=='active'
    assert executor.execute(prepared,campaign,credential='fixture-only').status=='incomplete'
    assert calls==['begin']


def test_other_held_call_waits_without_sealing_or_dispatch_and_heartbeats(case,monkeypatch):
    c,campaign,prepared=case
    c.enroll('0002','b'*64,'fixture');c.reconcile('0002','fixture stopped')
    claim=c.claim('0002','vm2','other-claim','version')
    owner={k:claim[k]for k in ('workbook','owner','token','fence')}
    c.begin('model','other-operation',request_sha256='c'*64,**owner)
    c.end('model','other-operation',evidence='retained unknown',outcome='uncertain',**owner)
    direct,ours,journal=model_gate.context();actions=[]
    class Observed:
        def call(self,action,**args):actions.append(action);return direct.call(action,**args)
    monkeypatch.setattr(model_gate,'context',lambda:(Observed(),ours,journal))
    def release(_):
        assert not list(executor.location(prepared,campaign).glob('transport-*.started.json'))
        assert not (executor.location(prepared,campaign)/'outcome.json').exists()
        assert all(op['workbook']!='0001' for op in c.status()['operations'])
        c.reconcile_operation('model','other-operation','proved terminal response','abandoned')
    monkeypatch.setattr(model_gate.time,'sleep',release)
    monkeypatch.setattr(executor,'_open',lambda *args:io.BytesIO(b'{"malformed":"terminal fixture"}'))
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='response_rejected' and 'heartbeat'in actions
    assert len(list(journal.rglob('waiting-for-*.json')))==1
    assert len(list(executor.location(prepared,campaign).glob('transport-*.started.json')))==1


def test_own_unknown_or_unbound_hold_cannot_be_waited(case):
    c,campaign,prepared=case;_,owner,_=model_gate.context()
    c.begin('model','own',request_sha256='c'*64,**owner)
    c.end('model','own',evidence='unknown',outcome='uncertain',**owner)
    with pytest.raises(model_gate.Conflict):model_gate.other_model_hold(c.status(),owner,'next')
    for kind,state in [('model','unexpected'),('unknown','hold')]:
        status=c.status();status['operations'][0].update(kind=kind,state=state)
        with pytest.raises(model_gate.Conflict):model_gate.other_model_hold(status,owner,'next')


def test_unbounded_policy_waits_for_every_other_hold_without_dispatch(case,monkeypatch):
    from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID
    c,campaign,prepared=case
    c.set_model_limit(None,dict(path='/retained/authorization.json',sha256=AUTHORIZATION_SHA256),CHANGE_ID,0)
    others={}
    for n in (2,3,4):
        wb=f'{n:04d}';c.enroll(wb,'b'*64,'fixture');c.reconcile(wb,'fixture stopped')
        claim=c.claim(wb,f'vm{n}',f'claim{n}','version');o={k:claim[k] for k in ('workbook','owner','token','fence')}
        c.begin('model',f'other-{n}',request_sha256='c'*64,**o)
        others[n]=o
    for n in (2,3):c.end('model',f'other-{n}',evidence='retained unknown',outcome='uncertain',**others[n])
    _,ours,journal=model_gate.context();proof=model_gate.other_model_hold(c.status(),ours,'ours')
    assert [x['operation_id'] for x in proof['held_operations']]==['other-2','other-3']
    sleeps=[]
    def release(_):
        assert not list(executor.location(prepared,campaign).glob('transport-*.started.json'))
        assert not (executor.location(prepared,campaign)/'outcome.json').exists()
        sleeps.append(1);c.reconcile_operation('model',f'other-{len(sleeps)+1}','actual terminal proof','confirmed')
    monkeypatch.setattr(model_gate.time,'sleep',release)
    monkeypatch.setattr(executor,'_open',lambda *args:io.BytesIO(b'{"malformed":"terminal fixture"}'))
    result=executor.execute(prepared,campaign,credential='fixture-only')
    assert result.status=='response_rejected' and len(sleeps)==2
    assert len(list(executor.location(prepared,campaign).glob('transport-*.started.json')))==1
    assert next(x for x in c.status()['operations'] if x['operation_id']=='other-4')['state']=='active'


def test_unbounded_own_hold_cannot_hide_after_other_hold(case):
    from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID
    c,campaign,prepared=case;c.set_model_limit(None,dict(path='/retained/authorization.json',sha256=AUTHORIZATION_SHA256),CHANGE_ID,0)
    _,owner,_=model_gate.context();c.enroll('0002','b'*64,'fixture');c.reconcile('0002','stopped')
    v=c.claim('0002','other','other-claim','version');other={k:v[k] for k in ('workbook','owner','token','fence')}
    c.begin('model','a-other',request_sha256='c'*64,**other);c.begin('model','z-own',request_sha256='c'*64,**owner)
    c.end('model','a-other',evidence='unknown',outcome='uncertain',**other);c.end('model','z-own',evidence='unknown',outcome='uncertain',**owner)
    with pytest.raises(model_gate.Conflict):model_gate.other_model_hold(c.status(),owner,'next')
