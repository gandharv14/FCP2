import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet import physical_capacity_renewal as R, capacity_policy as P
from scripts.fleet.coordinator import Coordinator, Conflict, digest
from test_physical_capacity_extension import fixture, claim


def setup(tmp_path):
    c, now, claims, a = fixture(tmp_path)
    old = c.permit_physical_capacity_extension(**a); now[0] = 5001
    args = dict(original_change_id=old['change_id'], previous_sha256=digest(old), expected_revision=0, expires_at=9000, authorization=a['authorization'], evidence=a['evidence'], change_id='renew-1')
    return c, now, claims, old, args


def test_append_only_expired_renewal_works_with_original_reader_and_api(tmp_path):
    c, now, claims, old, args = setup(tmp_path); before = c.status()
    with c.connect() as db: raw = [tuple(r) for r in db.execute('SELECT * FROM physical_capacity_extensions')]
    r = R.renew(c, **args); after = c.status()
    assert all(after[k] == v for k,v in before.items() if k not in ('events','physical_capacity_extensions'))
    assert after['events'] == before['events']+1 and P.validate_physical_extensions(after) == [r['effective']]
    with c.connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM physical_capacity_originals')] == raw
        assert R.history(db) == ([old], [r], [r['effective']])
    a = claim(c,'0095'); b = claim(c,'0001')
    assert c.begin('model','fresh',request_sha256='e'*64,**a)['execute']
    assert not c.begin('model','over',request_sha256='e'*64,**b)['execute']
    with pytest.raises(Conflict): c.begin('model','model-0059',request_sha256='d'*64,**claims['0059'])
    assert R.renew(c, **args) == r
    now[0] = 9001
    assert not P.validate_physical_extensions(c.status())


@pytest.mark.parametrize('delta', [{'previous_sha256':'e'*64},{'original_change_id':'wrong'},{'expected_revision':1},{'expires_at':float('inf')},{'expires_at':float('nan')},{'expires_at':5000},{'expires_at':20001}])
def test_bad_authority_does_not_migrate_or_mutate(tmp_path,delta):
    c,_,_,_,a=setup(tmp_path); before=c.status()
    with pytest.raises(ValueError): R.renew(c,**dict(a,**delta))
    assert c.status()==before
    with c.connect() as db: assert not R.installed(db)


def test_active_target_or_changed_unknown_refused(tmp_path):
    c,_,_,_,a=setup(tmp_path); claim(c,'0095'); before=c.status()
    with pytest.raises(Conflict,match='target host'): R.renew(c,**a)
    assert c.status()==before


def test_event_failure_rolls_back_relation_migration(tmp_path,monkeypatch):
    c,_,_,_,a=setup(tmp_path); before=c.status()
    monkeypatch.setattr(c,'event',lambda *x: (_ for _ in ()).throw(RuntimeError('event failed')))
    with pytest.raises(RuntimeError): R.renew(c,**a)
    assert c.status()==before
    with c.connect()as db: assert not R.installed(db)


def test_race_once_then_second_explicit_renewal_preserves_chain(tmp_path):
    c,now,_,old,a=setup(tmp_path)
    def run(_): return R.renew(Coordinator(c.database,clock=c.clock),**a)
    rows=list(ThreadPoolExecutor(max_workers=2).map(run,range(2))); assert rows[0]==rows[1]
    now[0]=9001; b=dict(a,previous_sha256=digest(rows[0]['effective']),expected_revision=1,expires_at=12000,change_id='renew-2')
    r=R.renew(c,**b)
    with c.connect()as db: assert R.history(db)==([old],[rows[0],r],[r['effective']])
    with pytest.raises(Conflict): R.renew(c,**dict(b,evidence={'path':'/changed','sha256':'f'*64}))


@pytest.mark.parametrize('change', ['unknown','projection','predecessor'])
def test_changed_unknown_or_projection_is_not_accepted(tmp_path, change):
    c,_,_,_,a=setup(tmp_path)
    if change=='unknown':
        with c.transaction()as db: db.execute("UPDATE operations SET evidence='changed' WHERE operation_id='model-0059'")
        before=c.status()
        with pytest.raises(Conflict): R.renew(c,**a)
        assert c.status()==before
    else:
        r=R.renew(c,**a)
        with c.transaction()as db:
            if change=='projection': db.execute("UPDATE physical_capacity_extensions SET record=?",(json.dumps(dict(r['effective'],expires_at=10000)),))
            else: db.execute("UPDATE physical_capacity_renewals SET record=?",(json.dumps(dict(r,previous_sha256='f'*64)),))
        with c.connect()as db, pytest.raises(Conflict): R.history(db)


def test_original_running_http_observes_atomic_renewal_without_restart(tmp_path):
    import threading
    from scripts.fleet.coordinator import serve,Client
    c,_,_,_,a=setup(tmp_path); server=serve(c,'127.0.0.1',0,'w'*32,'a'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    token=tmp_path/'token';token.write_text('w'*32);client=Client('http://127.0.0.1:'+str(server.server_address[1]),token)
    try:
        assert not P.validate_physical_extensions(client.call('status'))
        r=R.renew(Coordinator(c.database,clock=c.clock),**a)
        assert P.validate_physical_extensions(client.call('status'))==[r['effective']]
        x=client.call('claim',workbook='0095',owner='hhu-server-3:slot-1:0095',claim_id='http-fresh',version='b'*64,lease_seconds=3600)
        fields={k:x[k]for k in('workbook','owner','token','fence')}
        assert client.call('begin',kind='model',operation_id='http-new',request_sha256='e'*64,**fields)['execute']
        client.call('heartbeat',lease_seconds=3600,**fields)
    finally: server.shutdown();server.server_close();thread.join()
