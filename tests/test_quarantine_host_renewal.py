from concurrent.futures import ThreadPoolExecutor
import importlib.util,json
from pathlib import Path
import pytest
from scripts.fleet import quarantine_host_renewal as R,quarantine_scope as Q
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from scripts.fleet.capacity_policy import validate_host_capacities,validate_quarantines,model_admission_barriers

def setup(tmp_path):
    p=Path(__file__).with_name('test_quarantine_scope.py');s=importlib.util.spec_from_file_location('host_renewal_fixture',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);c,now,claims,args=m.setup(tmp_path);r=Q.isolate(c,**args)
    fields=dict(original_quarantine_id=r['quarantine']['change_id'],previous_quarantine_sha256=digest(r['quarantine']),expected_quarantine_revision=0,original_capacity_id=r['host_capacity']['change_id'],previous_capacity_sha256=digest(r['host_capacity']),expected_capacity_revision=0,expires_at=3000,authorization=args['authorization'],evidence=args['evidence'],change_id='renew-host-001')
    return c,now,claims,r,fields

def test_active_original_claims_and_calls_survive_paired_renewal(tmp_path):
    c,now,claims,old,args=setup(tmp_path);before=c.status()
    with c.connect()as db:qrows=[tuple(r)for r in db.execute('SELECT * FROM model_quarantines')];crows=[tuple(r)for r in db.execute('SELECT * FROM quarantine_host_capacity')]
    r=R.renew(c,**args);after=c.status()
    assert all(v==after[k]for k,v in before.items()if k not in ('events','quarantines','quarantine_host_capacity'))
    assert after['events']==before['events']+1 and validate_quarantines(after)==[r['quarantine_renewal']['effective']] and validate_host_capacities(after)==[r['effective']]
    with c.connect()as db:
        assert [tuple(r)for r in db.execute('SELECT * FROM model_quarantine_originals')]==qrows
        assert [tuple(r)for r in db.execute('SELECT * FROM quarantine_host_originals')]==crows
        assert R.history(db)[1]==[r]
    now[0]=2100
    c.heartbeat(**claims['0007'],lease_seconds=3600);c.end('model','healthy',evidence='real response',outcome='confirmed',**claims['0007'])
    assert c.begin('model','next',request_sha256='f'*64,**claims['0007'])['execute']
    assert not c.begin('model','too-many',request_sha256='f'*64,**claims['0020'])['execute']
    assert not model_admission_barriers(c.status(),'0010',claims['0010']['owner'])
    reopened=Coordinator(c.database,clock=lambda:now[0]);assert reopened.status()['quarantine_host_capacity']==[r['effective']]
    assert next(o for o in reopened.status()['operations']if o['operation_id']=='unknown')['state']=='hold'

def test_idempotent_and_authenticated_second_renewal(tmp_path):
    c,now,claims,old,args=setup(tmp_path);r=R.renew(c,**args);n=c.status()['events'];assert R.renew(c,**args)==r and c.status()['events']==n
    args.update(previous_quarantine_sha256=digest(r['quarantine_renewal']['effective']),previous_capacity_sha256=digest(r['effective']),expected_quarantine_revision=1,expected_capacity_revision=1,expires_at=4000,change_id='renew-host-002');r2=R.renew(c,**args)
    with c.connect()as db:assert R.history(db)[1]==[r,r2]

@pytest.mark.parametrize('change',[{'expires_at':float('inf')},{'expires_at':2000},{'expires_at':100000},{'previous_quarantine_sha256':'0'*64},{'previous_capacity_sha256':'0'*64},{'expected_quarantine_revision':1},{'expected_capacity_revision':True},{'original_capacity_id':'foreign'}])
def test_invalid_request_has_no_partial_policy(tmp_path,change):
    c,now,claims,old,args=setup(tmp_path);before=c.status()
    with pytest.raises((Conflict,ValueError)):R.renew(c,**dict(args,**change))
    assert before==c.status()
    with c.connect()as db:assert not R.installed(db)

def test_atomic_race_and_failure_rollback(tmp_path,monkeypatch):
    c,now,claims,old,args=setup(tmp_path);before=c.status();original=c.event
    monkeypatch.setattr(c,'event',lambda *a:(_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError):R.renew(c,**args)
    assert c.status()==before
    monkeypatch.setattr(c,'event',original)
    def run(i):
        try:return R.renew(c,**dict(args,change_id='race-'+str(i)))
        except Conflict:return None
    assert sum(r is not None for r in ThreadPoolExecutor(max_workers=4).map(run,range(4)))==1

def test_changed_unknown_and_capacity_are_rejected(tmp_path):
    c,now,claims,old,args=setup(tmp_path)
    with c.transaction()as db:db.execute("UPDATE operations SET evidence='changed'WHERE operation_id='unknown'")
    before=c.status()
    with pytest.raises(Conflict,match='unknown'):R.renew(c,**args)
    assert c.status()==before

def test_original_controller_copy_proof_and_live_http_reader(tmp_path):
    import threading
    from types import SimpleNamespace
    from scripts.fleet import quarantine_host_renewal_control as K
    from scripts.fleet.coordinator import serve,Client
    c,now,claims,old,args=setup(tmp_path);before=c.status()
    original=SimpleNamespace(Coordinator=lambda path:Coordinator(path,clock=lambda:now[0]))
    import scripts.fleet.capacity_policy as reader
    proof=K.copy_proof((dict(arguments=args),dict(database=c.database),original,R,reader),tmp_path/'copy')
    assert K.read(proof)['status']=='FULL_DATABASE_COPY_PAIRED_RENEWAL_PASS' and c.status()==before
    server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();token=tmp_path/'worker.token';token.write_text('w'*32);client=Client('http://127.0.0.1:'+str(server.server_address[1]),token)
    try:
        r=R.renew(c,**args);s=client.call('status');assert validate_host_capacities(s)==[r['effective']]
        assert client.call('heartbeat',**claims['0007'],lease_seconds=3600)['state']=='active'
        client.call('end',kind='model',operation_id='healthy',evidence='actual response',outcome='confirmed',**claims['0007'])
    finally:server.shutdown();t.join();server.server_close()
