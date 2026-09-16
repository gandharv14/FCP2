"""The unknown request reserves one of two host slots and cannot be replayed."""
from concurrent.futures import ThreadPoolExecutor
import copy
import pytest
from tests.test_fleet_quarantine import setup,retained
from scripts.fleet.coordinator import Conflict,digest,ADMIN
from scripts.fleet.capacity_policy import model_admission_barriers,validate_host_capacities


def permit(c,args):
 q=c.quarantine_model(**args)
 return dict(host=q['host'],quarantine_change_id=q['change_id'],quarantine_sha256=digest(q),physical_model_limit=2,
             authorization=dict(path='/authority/host-exception',sha256='f'*64),evidence=dict(path='/proof/stopped-and-authenticated-host',sha256='a'*64),
             change_id='host-reserve-unknown-001',expected_revision=0,expires_at=q['expires_at'])


def claim(c,wb):
 c.enroll(wb,'f'*64,'source');c.reconcile(wb,'ready','ready');v=c.claim(wb,'hhu-server/slot-1/'+wb,wb,'v',3600)
 return {k:v[k]for k in ('workbook','owner','token','fence')}


def test_unknown_reserves_slot_atomic_concurrent_begins_and_no_replay(tmp_path):
 c,now,claims,args=setup(tmp_path);p=permit(c,args);before=retained(c);row=c.permit_quarantine_host_capacity(**p)
 assert retained(c)==before and validate_host_capacities(c.status())==[row]
 a=claim(c,'new-a');b=claim(c,'new-b')
 assert model_admission_barriers(c.status(),'new-a',a['owner'])==[]
 with ThreadPoolExecutor(max_workers=2)as pool:
  results=list(pool.map(lambda v:c.begin('model','op-'+v['workbook'],request_sha256='e'*64,**v),[a,b]))
 assert sum(bool(v['execute'])for v in results)==1
 winner=a if results[0]['execute']else b;loser=b if results[0]['execute']else a
 assert len(model_admission_barriers(c.status(),loser['workbook'],loser['owner']))==2
 c.end('model','op-'+winner['workbook'],evidence='actual response persisted',**winner)
 assert c.begin('model','op-'+loser['workbook'],request_sha256='e'*64,**loser)['execute']
 assert c.begin('model','unrelated-host',request_sha256='e'*64,**claims['0140'])['execute']
 with pytest.raises(Conflict):c.reconcile('0413','no response','ready')
 with pytest.raises(Conflict):c.begin('model','uncertain',request_sha256='c'*64,**claims['0413'])
 current=retained(c)
 assert current['operations'][0]==before['operations'][0]
 assert next(x for x in current['workbooks']if x[0]=='0413')==next(x for x in before['workbooks']if x[0]=='0413')


def test_expiry_and_lost_reply_never_extend_host_permission(tmp_path):
 c,now,claims,args=setup(tmp_path);p=permit(c,args);row=c.permit_quarantine_host_capacity(**p);a=claim(c,'new-a')
 now[0]=2000
 assert c.permit_quarantine_host_capacity(**p)==row and validate_host_capacities(c.status())==[]
 with pytest.raises(Conflict):c.begin('model','expired',request_sha256='f'*64,**a)
 with pytest.raises(Conflict):c.permit_quarantine_host_capacity(**dict(p,expires_at=3000))
 with pytest.raises(ValueError):model_admission_barriers(c.status(),'new-a',a['owner'])
 assert not c.begin('model','other-host-after-expiry',request_sha256='e'*64,**claims['0140'])['execute']


@pytest.mark.parametrize('key,value',[('host','hhu-server-2'),('quarantine_sha256','f'*64),('quarantine_change_id','foreign'),('physical_model_limit',3),('physical_model_limit',True),('expires_at',3000),('expires_at',1000),('expires_at',float('nan')),('expected_revision',1)])
def test_invalid_host_exception_leaves_all_retained_state_unchanged(tmp_path,key,value):
 c,now,claims,args=setup(tmp_path);p=permit(c,args);before=retained(c)
 with pytest.raises((ValueError,Conflict)):c.permit_quarantine_host_capacity(**dict(p,**{key:value}))
 assert retained(c)==before and not c.status()['quarantine_host_capacity']


@pytest.mark.parametrize('key,value',[('physical_model_limit',3),('quarantine_sha256','f'*64),('expires_at',3000),('workbook_blocked',False),('host','hhu-server-2')])
def test_reader_rejects_forged_capacity_projection(tmp_path,key,value):
 c,now,claims,args=setup(tmp_path);p=permit(c,args);c.permit_quarantine_host_capacity(**p);status=c.status();status['quarantine_host_capacity'][0][key]=value
 with pytest.raises(ValueError):validate_host_capacities(status)


def test_new_action_is_admin_only_and_second_unknown_blocks_host(tmp_path):
 assert 'permit_quarantine_host_capacity'in ADMIN
 c,now,claims,args=setup(tmp_path);p=permit(c,args);c.permit_quarantine_host_capacity(**p);a=claim(c,'new-a');b=claim(c,'new-b')
 c.begin('model','second-unknown',request_sha256='e'*64,**a);c.end('model','second-unknown',evidence='no response',outcome='uncertain',**a)
 assert not c.begin('model','third',request_sha256='e'*64,**b)['execute']
 assert model_admission_barriers(c.status(),'new-b',b['owner'])


def test_host_exception_worker_http_denied_and_admin_idempotent(tmp_path):
 import threading
 from scripts.fleet.coordinator import Client,serve
 c,now,claims,args=setup(tmp_path);p=permit(c,args)
 server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  token=tmp_path/'worker.token';token.write_text('w'*32);url='http://127.0.0.1:'+str(server.server_port)
  with pytest.raises(Exception):Client(url,token).call('permit_quarantine_host_capacity',**p)
  assert not c.status()['quarantine_host_capacity']
  token=tmp_path/'admin.token';token.write_text('a'*32);client=Client(url,token)
  first=client.call('permit_quarantine_host_capacity',**p)
  assert client.call('permit_quarantine_host_capacity',**p)==first
  assert len(c.status()['quarantine_host_capacity'])==1
 finally:server.shutdown();server.server_close();thread.join()


def test_real_model_gate_waits_for_active_host_slot_without_reconciling_unknown(tmp_path,monkeypatch):
 import json
 from scripts.fleet import model_gate
 c,now,claims,args=setup(tmp_path);p=permit(c,args);c.permit_quarantine_host_capacity(**p)
 running=claim(c,'running');waiting=claim(c,'waiting');c.begin('model','active-host-slot',request_sha256='e'*64,**running)
 denied=c.begin('model','waiting-slot',request_sha256='f'*64,**waiting)
 assert denied==dict(execute=False,busy=True,reason='model/publication capacity or reconciliation barrier',state='active')
 calls=[]
 class Direct:
  def call(self,action,**kw):calls.append(action);return getattr(c,action)(**kw)
 monkeypatch.setattr(model_gate,'context',lambda:(Direct(),waiting,tmp_path/'gate'))
 def unexpected_hold(*a):raise AssertionError('transient host saturation must not enter the uncertainty branch')
 monkeypatch.setattr(model_gate,'other_model_hold',unexpected_hold)
 sleeps=[]
 def sleep(seconds):
  sleeps.append(seconds);assert len(sleeps)==1
  c.end('model','active-host-slot',evidence='actual response persisted',**running)
 monkeypatch.setattr(model_gate.time,'sleep',sleep)
 outcome=tmp_path/'outcome.json';dispatched=[]
 with model_gate.slot(operation_id='waiting-slot',request_sha256='f'*64,outcome_path=outcome):
  dispatched.append(1);outcome.write_text(json.dumps(dict(status='completed')));(tmp_path/'response.raw.json').write_text('{}')
 assert dispatched==[1] and sleeps==[1] and calls.count('begin')==2
 assert next(x for x in c.status()['operations']if x['operation_id']=='uncertain')['state']=='hold'
 assert next(x for x in c.status()['operations']if x['operation_id']=='waiting-slot')['state']=='confirmed'
 assert not list((tmp_path/'gate').rglob('waiting-for-*'))
