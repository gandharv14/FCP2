"""Actual SQLite/HTTP policy migration; no live fleet state is accessed."""
from concurrent.futures import ThreadPoolExecutor
import json,sqlite3,threading
import pytest
from scripts.fleet.coordinator import Coordinator,Conflict,Client,serve
AUTH={'path':'/retained/user-authorization.json','sha256':'a'*64}

def fleet(tmp_path,n=3):
 c=Coordinator(tmp_path/'db.sqlite',clock=lambda:1000)
 owners=[]
 for i in range(n):
  w=f'w{i}';c.enroll(w,'a'*64,'source proof');c.reconcile(w,'stopped owner verified')
  v=c.claim(w,f'host{i}:worker',f'claim{i}','b'*64,lease_seconds=180)
  owners.append({k:v[k] for k in ('workbook','owner','token','fence')})
 return c,owners

def policy(c,limit=None,**kw):return c.set_model_limit(limit,AUTH,kw.get('change_id','change-1'),kw.get('revision',0))
def begin(c,o,id):return c.begin('model',id,**o,request_sha256='c'*64)
def indexes(c):
 with c.connect() as db:return {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}

def test_default_retains_legacy_limit_and_index(tmp_path):
 c,o=fleet(tmp_path);assert c.status()['model_limit']==1;assert 'one_global_operation' in indexes(c)
 assert begin(c,o[0],'a')['execute'];assert not begin(c,o[1],'b')['execute']
 assert c.status()['model_policy']['revision']==0

def test_atomic_unbounded_migration_preserves_rows_and_idempotency(tmp_path):
 c,o=fleet(tmp_path);begin(c,o[0],'a')
 with c.connect() as db:before={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in ('workbooks','claims','operations')}
 p=policy(c);assert p==c.status()['model_policy'];assert p['model_limit'] is None;assert p['authorization']==AUTH
 with c.connect() as db:assert before=={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in before}
 assert 'one_global_operation' not in indexes(c);assert {'one_publication_operation','one_workbook_operation'}<=indexes(c)
 assert policy(c)==p # Simulate lost response, exactly one event remains.
 assert [e for e in events(c) if e['action']=='set_model_limit'].__len__()==1
 assert begin(c,o[1],'b')['execute'];assert not begin(c,o[0],'a')['execute'];assert not begin(c,o[0],'other')['execute']
 restarted=Coordinator(c.database,clock=c.clock);assert restarted.status()['model_limit'] is None
 assert begin(restarted,o[2],'c')['execute'];assert 'one_global_operation' not in indexes(c)

def events(c):
 with c.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM events')]

def test_parallel_unbounded_remains_one_per_workbook(tmp_path):
 c,o=fleet(tmp_path,16);policy(c)
 with ThreadPoolExecutor(max_workers=32) as pool:
  results=list(pool.map(lambda i:begin(c,o[i//2],str(i)),range(32)))
 assert sum(x['execute'] for x in results)==16
 assert len([x for x in c.status()['operations'] if x['state']=='active'])==16

def test_explicit_finite_capacity_concurrent_boundary(tmp_path):
 c,o=fleet(tmp_path,12);policy(c,3)
 with ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(lambda i:begin(c,o[i],str(i)),range(12)))
 assert sum(x['execute'] for x in results)==3
 with pytest.raises(Conflict):policy(c,2,change_id='lower',revision=1)
 assert c.status()['model_limit']==3

def test_any_uncertain_model_remains_global_barrier(tmp_path):
 c,o=fleet(tmp_path);policy(c);begin(c,o[0],'a');begin(c,o[1],'b')
 c.end('model','a',**o[0],evidence='sealed incomplete response',outcome='uncertain')
 assert begin(c,o[2],'c')==dict(execute=False,busy=True,reason='model/publication capacity or reconciliation barrier',state='hold')
 # Existing other confirmed response may finish, but this never settles 'a'.
 c.end('model','b',**o[1],evidence='sealed response');assert not begin(c,o[2],'c')['execute']
 c.reconcile_operation('model','a','actual stopped terminal rejection proof','confirmed')
 assert begin(c,o[2],'c')['execute']
 assert c.status()['workbooks'][0]['state']=='hold'

def test_publication_one_and_model_workbook_constraints_at_database_level(tmp_path):
 c,o=fleet(tmp_path);policy(c)
 assert c.begin('publication','p1',**o[0],request_sha256='a'*64)['execute']
 assert not c.begin('publication','p2',**o[1],request_sha256='a'*64)['execute']
 begin(c,o[0],'m')
 with c.connect() as db:
  row=tuple(db.execute("SELECT * FROM operations WHERE operation_id='m'").fetchone())
  with pytest.raises(sqlite3.IntegrityError):db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(row[0],'m2',*row[2:]))
  row=tuple(db.execute("SELECT * FROM operations WHERE operation_id='p1'").fetchone());r=list(row);r[1]='p2';r[3]=o[1]['workbook']
  with pytest.raises(sqlite3.IntegrityError):db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',r)

def test_failed_transaction_rolls_back_policy_and_indexes(tmp_path,monkeypatch):
 c,o=fleet(tmp_path)
 def fail(*args):raise RuntimeError('simulated crash before commit')
 monkeypatch.setattr(c,'event',fail)
 with pytest.raises(RuntimeError):policy(c)
 assert c.status()['model_limit']==1 and 'one_global_operation' in indexes(c)
 assert 'one_workbook_operation' not in indexes(c)

def test_policy_identity_revision_and_binding_fail_closed(tmp_path):
 c,o=fleet(tmp_path);policy(c)
 with pytest.raises(Conflict):policy(c,2)
 with pytest.raises(Conflict):policy(c,None,change_id='other',revision=0)
 for bad in (0,-1,False,True,'unbounded',1.5):
  with pytest.raises(ValueError):policy(c,bad,change_id='bad',revision=1)
 for bad in ('approval',{'path':'relative','sha256':'a'*64},{'path':'/p','sha256':'x'*64},{**AUTH,'extra':True}):
  with pytest.raises(ValueError):c.set_model_limit(None,bad,'bad',1)
 assert c.status()['model_policy']['revision']==1

def test_http_policy_setter_is_admin_only(tmp_path):
 c,o=fleet(tmp_path);wt='w'*32;at='a'*32
 wp=tmp_path/'worker';wp.write_text(wt);ap=tmp_path/'admin';ap.write_text(at)
 server=serve(c,'127.0.0.1',0,wt,at);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  url=f'http://127.0.0.1:{server.server_port}';args=dict(model_limit=None,authorization=AUTH,change_id='http',expected_revision=0)
  with pytest.raises(RuntimeError,match='403'):Client(url,wp).call('set_model_limit',**args)
  assert Client(url,ap).call('set_model_limit',**args)['model_limit'] is None
  assert Client(url,wp).call('status')['model_policy']['authorization']==AUTH
 finally:server.shutdown();server.server_close();thread.join()

def test_expired_parallel_operations_all_become_retained_holds(tmp_path):
 c,o=fleet(tmp_path);policy(c);begin(c,o[0],'a');begin(c,o[1],'b');c.clock=lambda:1181
 status=c.status();assert len([x for x in status['operations'] if x['state']=='hold'])==2
 assert status['model_limit'] is None
 # Policy changes are never an implicit reconciliation or owner renewal.
 p=policy(c,None,change_id='policy-2',revision=1);assert p['revision']==2
 assert len([x for x in c.status()['operations'] if x['state']=='hold'])==2
 assert policy(c)==dict(status['model_policy'])

def test_return_to_default_after_verified_operations_finish(tmp_path):
 c,o=fleet(tmp_path);policy(c);begin(c,o[0],'a');begin(c,o[1],'b')
 c.end('model','a',**o[0],evidence='confirmed-a');c.end('model','b',**o[1],evidence='confirmed-b')
 assert policy(c,1,change_id='return-one',revision=1)['model_limit']==1
 assert begin(c,o[0],'next-a')['execute'];assert not begin(c,o[1],'next-b')['execute']
 assert {x['operation_id'] for x in c.status()['operations']}=={'a','b','next-a'}
