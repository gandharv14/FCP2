import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet import quarantine_physical_renewal as R,physical_capacity_renewal as P,capacity_policy as V
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from test_physical_capacity_extension import fixture,claim


def setup(tmp_path):
    c,now,claims,args=fixture(tmp_path);p=c.permit_physical_capacity_extension(**args);s=c.status();now[0]=20001
    a=dict(host='hhu-server-3',original_physical_id=p['change_id'],previous_physical_sha256=digest(p),expected_physical_revision=0,original_capacity_id=s['quarantine_host_capacity'][0]['change_id'],previous_capacity_sha256=digest(s['quarantine_host_capacity'][0]),expected_capacity_revision=0,quarantines={q['change_id']:digest(q)for q in s['quarantines']},expected_quarantine_revision=0,quarantine_expires_at=100000,physical_expires_at=34000,authorization=args['authorization'],evidence=args['evidence'],change_id='renew-all')
    return c,now,claims,a


def test_expired_all_policies_renew_atomically_preserving_originals(tmp_path):
    c,now,claims,a=setup(tmp_path);before=c.status()
    with c.connect()as db:original={name:[tuple(x)for x in db.execute('SELECT * FROM '+name)]for name in ('model_quarantines','quarantine_host_capacity','physical_capacity_extensions')}
    r=R.renew(c,**a);after=c.status()
    assert all(after[k]==v for k,v in before.items()if k not in ('events','quarantines','quarantine_host_capacity','physical_capacity_extensions'))
    assert after['events']==before['events']+1 and V.validate_physical_extensions(after)==[r['effective']]
    with c.connect()as db:
        for old,new in [('model_quarantines','model_quarantine_originals'),('quarantine_host_capacity','quarantine_host_originals'),('physical_capacity_extensions','physical_capacity_originals')]:assert [tuple(x)for x in db.execute('SELECT * FROM '+new)]==original[old]
        assert P.history(db)[1]==[r]
    x=claim(c,'0095');y=claim(c,'0001')
    assert c.begin('model','one-new',request_sha256='e'*64,**x)['execute']
    assert not c.begin('model','fourth',request_sha256='e'*64,**y)['execute']
    with pytest.raises(Conflict):c.begin('model','model-0059',request_sha256='d'*64,**claims['0059'])
    assert R.renew(c,**a)==r

@pytest.mark.parametrize('delta',[{'physical_expires_at':float('inf')},{'physical_expires_at':40000},{'quarantine_expires_at':110000},{'expected_capacity_revision':1},{'expected_physical_revision':1},{'expected_quarantine_revision':1},{'previous_physical_sha256':'f'*64},{'previous_capacity_sha256':'f'*64},{'quarantines':{}}])
def test_bad_composed_authority_preserves_entire_ledger(tmp_path,delta):
    c,_,_,a=setup(tmp_path);before=c.status()
    with pytest.raises(ValueError):R.renew(c,**dict(a,**delta))
    assert c.status()==before
    with c.connect()as db:assert not P.installed(db)


def test_failure_rolls_back_every_relation_and_race_has_one_winner(tmp_path,monkeypatch):
    c,_,_,a=setup(tmp_path);before=c.status();original=c.event
    monkeypatch.setattr(c,'event',lambda *a:(_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError):R.renew(c,**a)
    assert c.status()==before
    monkeypatch.setattr(c,'event',original)
    def run(i):
        try:return R.renew(c,**dict(a,change_id='race'+str(i)))
        except Conflict:return None
    assert sum(x is not None for x in ThreadPoolExecutor(max_workers=3).map(run,range(3)))==1


def test_second_composed_renewal_and_later_plain_physical_renewal(tmp_path):
    c,now,_,a=setup(tmp_path);first=R.renew(c,**a);now[0]=34001
    with c.connect()as db:
        qo,qr,qe=R.Q.history(db);ho,hr,he=R.H.history(db)
    b=dict(a,previous_physical_sha256=digest(first['effective']),expected_physical_revision=1,previous_capacity_sha256=digest(he[0]),expected_capacity_revision=1,quarantines={o['change_id']:digest(q)for o,q in zip(qo,qe)},expected_quarantine_revision=2,quarantine_expires_at=110000,physical_expires_at=48000,change_id='again')
    second=R.renew(c,**b);now[0]=48001
    third=P.renew(c,original_change_id=a['original_physical_id'],previous_sha256=digest(second['effective']),expected_revision=2,expires_at=60000,authorization=a['authorization'],evidence=a['evidence'],change_id='plain')
    with c.connect()as db:assert P.history(db)[1]==[first,second,third]


def test_changed_unknown_or_removed_composed_lineage_refused(tmp_path):
    c,_,_,a=setup(tmp_path);r=R.renew(c,**a)
    with c.transaction()as db:
        bad=dict(r);bad['authority_renewal']=dict(r['authority_renewal'],quarantines=r['authority_renewal']['quarantines'][:1]);db.execute('UPDATE physical_capacity_renewals SET record=?',(json.dumps(bad),))
    with c.connect()as db,pytest.raises(Conflict):P.history(db)


def test_original_http_reader_and_healthy_host_continue(tmp_path):
    import threading
    from scripts.fleet.coordinator import serve,Client
    c,now,_,a=setup(tmp_path);token=tmp_path/'token';token.write_text('w'*32);server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        R.renew(c,**a);client=Client('http://127.0.0.1:'+str(server.server_address[1]),token);assert len(V.validate_quarantines(client.call('status')))==2
        x=claim(c,'0095','hhu-server-4');assert c.begin('model','otherhost',request_sha256='e'*64,**x)['execute'];client.call('heartbeat',**x,lease_seconds=3600)
    finally:server.shutdown();server.server_close();t.join()


def test_original_controller_full_database_copy_preserves_live(tmp_path):
    from types import SimpleNamespace
    from scripts.fleet import quarantine_physical_renewal_control as K
    c,now,_,a=setup(tmp_path);before=c.status();original=SimpleNamespace(Coordinator=lambda p:Coordinator(p,clock=lambda:now[0]))
    result=K.copy_proof((dict(arguments=a),dict(database=c.database),original,R,V),tmp_path/'copy')
    assert K.read(result)['status']=='FULL_DATABASE_COPY_COMPOSED_RENEWAL_PASS'and c.status()==before
