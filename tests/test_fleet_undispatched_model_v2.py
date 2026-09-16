import dataclasses
import io
import json
from pathlib import Path
import time
import uuid

import pytest

from scripts.batch import harbor_model_executor as E
from scripts.fleet import model_gate as G, undispatched_model_v2 as U
from scripts.fleet.coordinator import Coordinator, Conflict


def saved(path, value):
    path.write_text(json.dumps(value));return U.binding(path)


@pytest.fixture
def case(tmp_path, monkeypatch):
    c = Coordinator(tmp_path/'central.sqlite')
    c.enroll('0173', 'a'*64, 'fixture');c.reconcile('0173', 'fixture no worker')
    first = c.claim('0173', 'vm:old', 'first', 'fixture-code')
    owner = {k:first[k]for k in ('workbook','owner','token','fence')}
    class Direct:
        def call(self, action, **args):return getattr(c, action)(**args)
    class Before:
        def call(self, action, **args):
            if action == 'begin':raise Conflict('fixture old other-workbook hold')
            return getattr(c, action)(**args)
    monkeypatch.setattr(G, 'context', lambda:(Before(), owner, tmp_path/'old-gate'))
    monkeypatch.setenv('HARBOR_FLEET_URL', 'http://127.0.0.1:1')
    campaign = E.Campaign(tmp_path/'campaign', tmp_path/'slots', 1)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root))
    policy = E.Policy('fixture-project')
    request = saved(tmp_path/'request.json', dict(model=policy.model,reasoning_effort='high',messages=[
        dict(role='system',content='fixture'),dict(role='user',content='fixture')]))
    budget = tuple(saved(tmp_path/f'budget-{i}.json',dict(ordinal=1,limit=2,fixture=i))for i in range(2))
    prepared = E.PreparedCall(request, policy, E.SemanticScope('0173','a'*64,
        'harbor-normalization-engineer','partition-fixture'),1,2,str(uuid.uuid4()),budget)
    monkeypatch.setattr(E,'_open',lambda *a:pytest.fail('old undispatched call must not open'))
    old = E.execute(prepared,campaign,credential='fixture')
    assert old.status == 'incomplete'
    oldroot = E.location(prepared,campaign)
    intent = next((tmp_path/'old-gate').rglob('intent.json'))
    native = dict(status='SEALED_UNDISPATCHED_ATTEMPT_VERIFIED',active_original_processes=[],
        transport_started=False,fleet_acquired=False,attempt_consumed=True,automatic_replay=False,
        semantic_approval=False,model_calls_dispatched_by_probe=0,executor=U.binding(E.__file__),
        semantic_ordinal=1,semantic_limit=2,operation_id=prepared.attempt_id,workbook='0173',
        outcome=U.binding(oldroot/'outcome.json'),plan=U.binding(oldroot/'plan.json'),
        request=U.binding(oldroot/'request.json'),submitted=U.binding(oldroot/'submitted.json'),
        identity=json.loads((oldroot/'identity.json').read_bytes()),intent=U.binding(intent),
        sealed_files=[U.binding(p)for p in oldroot.iterdir()],model_journal_files=[U.binding(intent)])
    np = saved(tmp_path/'native.json',native)
    c.finish(evidence='fixture stopped', disposition='hold', **owner)
    prior = next(w for w in c.status()['workbooks']if w['workbook']=='0173')
    cp = saved(tmp_path/'central-proof.json',dict(status='VERIFIED_UNDISPATCHED_NOT_REPLAY_AUTHORIZED',
        semantic_budget_reset=False,owning_recovery_performed=False,checks=[dict(operation_id=prepared.attempt_id,
        native_proof=np,central_events=[],central_operations=[],workbook=prior)]))
    c.reconcile('0173','fixture prior worker absent')
    new = c.claim('0173','vm:new','second','fixture-code')
    owner = {k:new[k]for k in ('workbook','owner','token','fence')}
    c.set_model_limit(None, {'path':'/fixture/authorization.json','sha256':'4cf7d589371b8b7805b34658f801884ac769ea8bc69009631610975e32c94263'}, 'remove-global-model-limit-20260913-001', 0)
    monkeypatch.setattr(G,'context',lambda:(Direct(),owner,tmp_path/'new-gate'))
    return c,prepared,campaign,np,cp,tmp_path


def test_first_physical_dispatch_preserves_original_uuid_and_call_budget(case,monkeypatch):
    c,p,campaign,np,cp,root=case;sent=[]
    def opened(request, timeout):
        sent.append(request.data)
        return io.BytesIO(json.dumps(dict(id='fixture',object='chat.completion',model=p.policy.model,
            created=time.time_ns()//10**9,choices=[dict(finish_reason='stop',message=dict(role='assistant',
            content=json.dumps(dict(passed=False,finding='synthetic retained rejection'))))])).encode())
    monkeypatch.setattr(E,'_open',opened)
    before = {str(f):f.read_bytes()for f in E.location(p,campaign).iterdir()}
    identity = Path(U.read(np)['identity']['path']).read_bytes()
    recovery=U.Recovery(E,p,campaign,native_proof=np,central_proof=cp)
    result=recovery.execute(credential='fixture')
    assert result.status=='completed' and result.call is not None
    assert json.loads(Path(result.call.submitted['path']).read_bytes())['semantic_ordinal']==1
    assert json.loads(Path(result.call.submitted['path']).read_bytes())['semantic_limit']==2
    assert len(sent)==1 and sent[0]==Path(p.request['path']).read_bytes()
    assert recovery.execute(credential='fixture').status=='completed' and len(sent)==1
    assert E.execute(p,campaign,credential='fixture').status=='incomplete' and len(sent)==1
    assert all(Path(path).read_bytes()==raw for path,raw in before.items())
    assert Path(U.read(np)['identity']['path']).read_bytes()==identity
    assert len(c.status()['operations'])==1 and c.status()['operations'][0]['operation_id']==p.attempt_id
    assert c.status()['operations'][0]['state']=='confirmed'


def test_uncertain_recovery_is_not_replayed(case,monkeypatch):
    c,p,campaign,np,cp,_=case;sent=[]
    def opened(*args):sent.append(1);raise TimeoutError('fixture')
    monkeypatch.setattr(E,'_open',opened)
    recovery=U.Recovery(E,p,campaign,native_proof=np,central_proof=cp)
    assert recovery.execute(credential='fixture').status=='incomplete'
    assert recovery.execute(credential='fixture').status=='incomplete' and len(sent)==1
    assert c.status()['operations'][0]['state']=='hold'


@pytest.mark.parametrize('bad',['acquired','started','http','event','operation','uuid','ordinal','source'])
def test_recovery_rejects_any_ambiguous_or_different_attempt(case,bad):
    c,p,campaign,np,cp,root=case
    if bad in {'acquired','started','http'}:
        target = Path(U.read(np)['intent']['path']).parent/'acquired.json' if bad=='acquired' else E.location(p,campaign)/('transport-00.started.json'if bad=='started'else'transport-00.body.raw')
        target.write_text('{}')
    elif bad in {'event','operation'}:
        value=U.read(cp);value['checks'][0]['central_events'if bad=='event'else'central_operations']=[{}]
        cp=saved(root/'changed-proof.json',value)
    elif bad=='uuid':p=dataclasses.replace(p,attempt_id=str(uuid.uuid4()))
    elif bad=='ordinal':p=dataclasses.replace(p,ordinal=2)
    else:p=dataclasses.replace(p,scope=dataclasses.replace(p.scope,original_sha256='b'*64))
    with pytest.raises(ValueError):U.Recovery(E,p,campaign,native_proof=np,central_proof=cp)
    assert not (E.location(p,campaign).parent/'undispatched-continuation-01').exists()


def test_lost_begin_reply_in_new_continuation_remains_held_without_open(case,monkeypatch):
    c,p,campaign,np,cp,root=case
    direct,owner,journal=G.context()
    class Lost:
        def call(self,action,**args):
            result=direct.call(action,**args)
            if action=='begin':raise TimeoutError('fixture commit then lost reply')
            return result
    monkeypatch.setattr(G,'context',lambda:(Lost(),owner,journal))
    recovery=U.Recovery(E,p,campaign,native_proof=np,central_proof=cp)
    assert recovery.execute(credential='fixture').status=='incomplete'
    assert not list(recovery.root.glob('transport-*'))
    assert recovery.execute(credential='fixture').status=='incomplete'
    assert c.status()['operations'][0]['state']=='active'


@pytest.mark.parametrize("reject_first",[False,True])
def test_actual_partition_semantic_gate_and_phase1_use_declared_recovered_call(tmp_path,monkeypatch,request,reject_first):
    # The fixture uses real partition preparation, semantic review and emission;
    # only source publication plumbing and the endpoint are synthetic.
    from test_harbor_normalization_producer import setup as source_setup
    from test_normalization_partitioned import respond
    from scripts.batch import harbor_normalization_partitioned as P
    from scripts.fleet import undispatched_partitions as R
    factory=source_setup.__wrapped__(tmp_path,monkeypatch)
    selection,campaign,_,_=factory('0173')
    campaign=dataclasses.replace(campaign,model_slots=1)
    monkeypatch.setenv('HARBOR_MODEL_SLOTS','1');monkeypatch.setenv('HARBOR_FLEET_URL','http://127.0.0.1:1')
    monkeypatch.setattr(P.K,'MAX_REFERENCES',2)
    prepared=P.prepare(selection,campaign,tmp_path/'partitions')
    c=Coordinator(tmp_path/'partition-central.sqlite');c.enroll('0173',selection.bindings['original_raw_source']['sha256'],'fixture');c.reconcile('0173','fixture')
    claim=c.claim('0173','first','first','fixture');owner={k:claim[k]for k in ('workbook','owner','token','fence')}
    class Direct:
        def call(self,action,**args):return getattr(c,action)(**args)
    monkeypatch.setattr(G,'context',lambda:(Direct(),owner,tmp_path/'old-gate'))
    respond(monkeypatch,selection)
    assert P.dispatch(prepared,0,1,credential='fixture').status=='completed'
    pc=P._prepared_call(prepared,1,1)
    class Before:
        def call(self,action,**args):
            if action=='begin':raise Conflict('fixture before reservation')
            return getattr(c,action)(**args)
    monkeypatch.setattr(G,'context',lambda:(Before(),owner,tmp_path/'old-gate'))
    assert P.dispatch(prepared,1,1,credential='fixture').status=='incomplete'
    old=E.location(pc,campaign);intent=tmp_path/'old-gate'/E.sha(pc.attempt_id.encode())/'intent.json'
    proof=dict(status='SEALED_UNDISPATCHED_ATTEMPT_VERIFIED',active_original_processes=[],transport_started=False,
        fleet_acquired=False,attempt_consumed=True,automatic_replay=False,semantic_approval=False,model_calls_dispatched_by_probe=0,
        executor=U.binding(E.__file__),semantic_ordinal=1,semantic_limit=2,operation_id=pc.attempt_id,workbook='0173',
        outcome=U.binding(old/'outcome.json'),plan=U.binding(old/'plan.json'),request=U.binding(old/'request.json'),
        submitted=U.binding(old/'submitted.json'),identity=json.loads((old/'identity.json').read_bytes()),intent=U.binding(intent),
        sealed_files=[U.binding(x)for x in old.iterdir()],model_journal_files=[U.binding(intent)])
    np=saved(tmp_path/'p-native.json',proof);c.finish(evidence='fixture stopped',disposition='hold',**owner)
    previous=c.status()['workbooks'][0]
    cp=saved(tmp_path/'p-central.json',dict(status='VERIFIED_UNDISPATCHED_NOT_REPLAY_AUTHORIZED',semantic_budget_reset=False,
        owning_recovery_performed=False,checks=[dict(operation_id=pc.attempt_id,native_proof=np,central_operations=[],central_events=[],workbook=previous)]))
    c.reconcile('0173','fixture stopped');claim=c.claim('0173','second','second','fixture');owner={k:claim[k]for k in ('workbook','owner','token','fence')}
    monkeypatch.setattr(G,'context',lambda:(Direct(),owner,tmp_path/'new-gate'))
    if reject_first:respond(monkeypatch,selection,invalid=lambda refs:True)
    recovery=U.Recovery(E,pc,campaign,native_proof=np,central_proof=cp)
    selected=R.facade(P,recovery,prepared,partition_code=U.binding(P.__file__))
    assert selected.dispatch(prepared,1,1,credential='fixture').status=='completed'
    if reject_first:
        _,_,campaign,_,rows=selected._current(prepared)
        assert selected._review(prepared,1,1,campaign,rows)['valid'] is False
        respond(monkeypatch,selection)
        assert selected.dispatch(prepared,1,2,credential='fixture').status=='completed'
    result=P.read(selected.emit_phase1(prepared))
    assert result['status']=='phase1_evidence_ready'
    aggregate=P.read(result['normalization_partitioned_review'])
    assert len(aggregate['partitions'])==2+int(reject_first)
    assert aggregate['semantic_attempt']==1+int(reject_first)
    assert sum(not p['valid'] for p in aggregate['partitions'])==int(reject_first)
    assert U.binding(R.__file__)['path']in aggregate['checked_files']
    assert proof['outcome']['path']in aggregate['checked_files']
    assert len(c.status()['operations'])==2+int(reject_first)
    assert E.inspect(pc,campaign).status=='incomplete'
    # An ordinary second review of this passed partition is still forbidden.
    with pytest.raises(ValueError,match='normalization already aggregated'):
        selected.dispatch(prepared,1,2,credential='fixture')
    assert len(c.status()['operations'])==2+int(reject_first)
