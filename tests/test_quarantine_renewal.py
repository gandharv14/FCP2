from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import copy,importlib.util,json,sqlite3
import pytest
from scripts.fleet import quarantine_renewal as R
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from scripts.fleet.capacity_policy import validate_quarantines,model_admission_barriers

ROOT=Path(__file__).resolve().parents[1]

def setup(tmp_path):
    spec=importlib.util.spec_from_file_location('original_quarantine_fixture',ROOT/'tests/test_fleet_quarantine.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    c,now,claims,args=m.setup(tmp_path);q=c.quarantine_model(**args)
    fields=dict(original_change_id=q['change_id'],previous_sha256=digest(q),expected_revision=0,expires_at=3000,authorization=dict(path='/new/auth.json',sha256='1'*64),evidence=dict(path='/new/evidence.json',sha256='2'*64),change_id='renew-001')
    return c,now,claims,args,q,fields,m

def test_original_live_coordinator_and_frozen_consumer_see_new_authority_without_restart(tmp_path):
    c,now,claims,args,q,fields,m=setup(tmp_path);before=m.retained(c)
    with c.connect()as db:original=[tuple(r)for r in db.execute('SELECT * FROM model_quarantines')]
    record=R.renew(c,**fields);assert m.retained(c)==before
    with c.connect()as db:
        assert [tuple(r)for r in db.execute('SELECT * FROM model_quarantine_originals')]==original
        assert R.history(db)==([q],[record],[record['effective']])
    assert record['effective']['change_id']=='renew-001'and record['effective']['created_at']==1000
    now[0]=2100
    assert validate_quarantines(c.status())==[record['effective']]
    assert not model_admission_barriers(c.status(),'0140',claims['0140']['owner'])
    assert c.begin('model','later',request_sha256='f'*64,**claims['0140'])['execute']
    c.heartbeat(**claims['0140'],lease_seconds=3600)
    c.end('model','later',evidence='actual result',outcome='confirmed',**claims['0140'])
    with pytest.raises(Conflict):c.claim('0413','hhu-server:slot-1:0413','newclaim','v')
    assert next(o for o in c.status()['operations']if o['operation_id']=='uncertain')['state']=='hold'
    # An unchanged original server can reconnect/construct against the view.
    second=Coordinator(c.database,clock=lambda:now[0]);assert second.status()['quarantines']==[record['effective']]
    now[0]=3000;assert not validate_quarantines(c.status())

def test_idempotency_chain_and_stale_cas_do_not_repeat_or_extend(tmp_path):
    c,now,claims,args,q,fields,m=setup(tmp_path);one=R.renew(c,**fields);before=c.status()['events'];assert R.renew(c,**fields)==one and c.status()['events']==before
    with pytest.raises(Conflict):R.renew(c,**dict(fields,expires_at=3100))
    now[0]=1500;two=R.renew(c,**dict(fields,previous_sha256=digest(one['effective']),expected_revision=1,expires_at=4000,change_id='renew-002'))
    assert len(c.status()['quarantines'])==1 and c.status()['quarantines'][0]==two['effective']
    with pytest.raises(Conflict):R.renew(c,**dict(fields,change_id='renew-stale'))

@pytest.mark.parametrize('change',[{'expires_at':float('inf')},{'expires_at':float('nan')},{'expires_at':100000},{'expires_at':2000},{'expected_revision':True},{'expected_revision':1},{'previous_sha256':'3'*64},{'original_change_id':'foreign'},{'change_id':'isolate-0413-001'}])
def test_invalid_authority_preserves_schema_rows_and_events(tmp_path,change):
    c,now,claims,args,q,fields,m=setup(tmp_path);before=c.status();ledger=m.retained(c)
    with pytest.raises((Conflict,ValueError)):R.renew(c,**dict(fields,**change))
    assert c.status()==before and m.retained(c)==ledger
    with c.connect()as db:assert not R.installed(db)

def test_atomic_competing_renewals_only_one_wins(tmp_path):
    c,now,claims,args,q,fields,m=setup(tmp_path)
    def run(i):
        try:return R.renew(c,**dict(fields,change_id='renew-'+str(i)))
        except Conflict:return None
    results=list(ThreadPoolExecutor(max_workers=5).map(run,range(5)))
    assert sum(v is not None for v in results)==1
    with c.connect()as db:assert len(R.history(db)[1])==1

def test_exception_rolls_back_ddl_and_all_rows(tmp_path,monkeypatch):
    c,now,claims,args,q,fields,m=setup(tmp_path);before=c.status()
    monkeypatch.setattr(c,'event',lambda *args:(_ for _ in ()).throw(RuntimeError('failed final event')))
    with pytest.raises(RuntimeError):R.renew(c,**fields)
    assert c.status()==before
    with c.connect()as db:assert not R.installed(db)

def test_original_insert_api_remains_available_for_distinct_quarantine(tmp_path):
    c,now,claims,args,q,fields,m=setup(tmp_path);R.renew(c,**fields)
    c.begin('model','second',request_sha256='f'*64,**claims['0140']);c.end('model','second',evidence='unknown',outcome='uncertain',**claims['0140'])
    op=next(o for o in c.status()['operations']if o['operation_id']=='second')
    new=c.quarantine_model(**dict(args,operation_id='second',operation_sha256=digest(op),workbook='0140',owner=claims['0140']['owner'],host='hhu-server-7',change_id='second-original',expected_revision=1))
    assert len(c.status()['quarantines'])==2 and new in validate_quarantines(c.status())
    with c.connect()as db:assert len(R.history(db)[0])==2

def test_existing_http_server_keeps_heartbeats_and_model_completion_during_renewal(tmp_path):
    import threading
    from scripts.fleet.coordinator import serve,Client
    c,now,claims,args,q,fields,m=setup(tmp_path)
    assert c.begin('model','inflight',request_sha256='f'*64,**claims['0140'])['execute']
    server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    token=tmp_path/'token';token.write_text('w'*32);client=Client('http://127.0.0.1:'+str(server.server_address[1]),token)
    try:
        def pulse(i):return client.call('heartbeat',**claims['0140'],lease_seconds=3600)
        with ThreadPoolExecutor(max_workers=5)as pool:
            futures=[pool.submit(pulse,i)for i in range(20)]
            renewed=R.renew(c,**fields)
            assert all(f.result()['state']=='active'for f in futures)
        assert client.call('status')['quarantines']==[renewed['effective']]
        client.call('end',kind='model',operation_id='inflight',evidence='preserved response',outcome='confirmed',**claims['0140'])
        with pytest.raises(Exception):client.call('renew_quarantine',**fields)
    finally:server.shutdown();thread.join();server.server_close()
