import json
from pathlib import Path
from urllib.error import URLError
import pytest
from scripts.fleet import heartbeat_reconciliation as H
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.migration import binding, retain

@pytest.fixture
def setup(tmp_path):
    auth = Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    policy=retain(tmp_path/'policy.json',dict(schema_version=H.SCHEMA,authorization=binding(auth),**H.LIMITS))
    clock=[1000.0]; c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:clock[0])
    c.enroll('0001','a'*64,'original');c.reconcile('0001','stopped original')
    claim=c.claim('0001','vm/one','original-uuid','v'*64,lease_seconds=180)
    auth={k:claim[k]for k in('workbook','owner','token','fence')}
    h=H.Heartbeats(policy,claim,clock=lambda:clock[0],monotonic=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
    return c,clock,h,auth,tmp_path

class Transport:
    def __init__(self,c,failures):self.c=c;self.failures=list(failures);self.calls=[]
    def call(self,action,**kw):
        self.calls.append((action,kw))
        if action=='heartbeat' and self.failures:
            how=self.failures.pop(0)
            if how=='lost':self.c.heartbeat(**kw);raise TimeoutError('secret transport')
            if how=='500':raise RuntimeError('coordinator HTTP 500: private response')
            if how=='403':raise RuntimeError('coordinator HTTP 403: forbidden')
            if how=='400':raise RuntimeError('coordinator HTTP 400: rejected')
            if how=='url':raise URLError('private transport')
        return getattr(self.c,action)(**kw)

@pytest.mark.parametrize('failure',['lost','500','url'])
def test_original_sql_custody_allows_distinct_next_heartbeat(setup,failure):
    c,clock,h,auth,home=setup;t=Transport(c,[failure]);guards=[]
    result=h.beat(t,auth,{'intent':{'path':'original','sha256':'a'*64},'pid':123},home,lambda:guards.append(True))
    assert result['claim_id']=='original-uuid' and result['fence']==1
    beats=[kw for action,kw in t.calls if action=='heartbeat']
    assert [kw['checkpoint']['heartbeat_sequence']for kw in beats]==[1,2]
    assert [action for action,_ in t.calls]==['heartbeat','status','heartbeat']
    assert len(guards)>=4
    assert c.status()['operations']==[]
    assert 'secret transport' not in (home/'heartbeat-reconciliation-001/intent.json').read_text()
    assert auth['token'] not in ''.join(p.read_text()for p in home.glob('heartbeat-reconciliation-*/*.json'))

@pytest.mark.parametrize('failure',['403','400'])
def test_nontransient_rejection_never_reconciles(setup,failure):
    c,_,h,auth,home=setup;t=Transport(c,[failure])
    with pytest.raises(RuntimeError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert [a for a,_ in t.calls]==['heartbeat']

@pytest.mark.parametrize('field,value',[('owner','other'),('claim_id','other'),('fence',2),('version','other'),('source_sha256','b'*64),('checkpoint','{"changed":true}'),('state','hold'),('expires',1010)])
def test_changed_custody_fails_closed(setup,field,value):
    c,_,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        r=original(action,**kw)
        if action=='status':r['workbooks'][0][field]=value
        return r
    t.call=call
    with pytest.raises(ValueError,match='custody'):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert len([a for a,_ in t.calls if a=='heartbeat'])==1

def test_expiry_during_transport_cannot_revive_claim(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        if action=='status':clock[0]+=181
        return original(action,**kw)
    t.call=call
    with pytest.raises(TimeoutError,match='bounded'):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert c.status()['workbooks'][0]['state']=='hold'

def test_two_reconciliations_is_whole_worker_budget(setup):
    c,_,h,auth,home=setup;t=Transport(c,['500','500','500'])
    with pytest.raises(RuntimeError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert h.reconciliations==2 and [a for a,_ in t.calls].count('status')==2

def test_elapsed_and_resource_guards_stop_before_next_request(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        r=original(action,**kw)
        if action=='status':clock[0]+=29;r['at']=clock[0]
        return r
    t.call=call
    with pytest.raises(TimeoutError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert [a for a,_ in t.calls].count('heartbeat')==1

def test_missing_or_stale_status_no_retry(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        r=original(action,**kw)
        if action=='status':r['at']-=6
        return r
    t.call=call
    with pytest.raises(ValueError,match='fresh'):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert [a for a,_ in t.calls].count('heartbeat')==1

def test_changed_policy_rejected_before_request(setup):
    c,_,h,auth,home=setup;t=Transport(c,[])
    Path(h.policy_binding['path']).write_text('{}')
    with pytest.raises(ValueError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert t.calls==[]

def test_resource_failure_after_status_prevents_next_heartbeat(setup):
    c,_,h,auth,home=setup;t=Transport(c,['500']);n=[0]
    def guard():
        n[0]+=1
        if n[0]==3:raise RuntimeError('process group exceeded original memory guard')
    with pytest.raises(RuntimeError,match='memory guard'):h.beat(t,auth,{'pid':1},home,guard)
    assert [a for a,_ in t.calls].count('heartbeat')==1

def test_unavailable_status_prevents_next_heartbeat(setup):
    c,_,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        if action=='status':raise TimeoutError('unavailable status')
        return original(action,**kw)
    t.call=call
    with pytest.raises(TimeoutError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert [a for a,_ in t.calls].count('heartbeat')==1

def test_timeout_scoped_to_remaining_window_and_restored(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);t.timeout=30;original=t.call;timeouts=[]
    def call(action,**kw):
        timeouts.append((action,t.timeout))
        if action=='status':clock[0]+=20
        return original(action,**kw)
    t.call=call
    h.beat(t,auth,{'pid':1},home,lambda:None)
    assert timeouts==[('heartbeat',30),('status',30),('heartbeat',9)]
    assert t.timeout==30

def test_initial_failure_time_not_charged_to_recovery_window(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);original=t.call
    def call(action,**kw):
        if len(t.calls)==0:clock[0]+=30
        return original(action,**kw)
    t.call=call
    h.beat(t,auth,{'pid':1},home,lambda:None)
    assert clock[0]==1031

def test_status_exhausting_timeout_prevents_next_heartbeat_and_restores(setup):
    c,clock,h,auth,home=setup;t=Transport(c,['500']);t.timeout=30;original=t.call
    def call(action,**kw):
        if action=='status':
            clock[0]+=t.timeout
            raise TimeoutError('status timed out')
        return original(action,**kw)
    t.call=call
    with pytest.raises(TimeoutError):h.beat(t,auth,{'pid':1},home,lambda:None)
    assert t.timeout==30 and [a for a,_ in t.calls].count('heartbeat')==1
