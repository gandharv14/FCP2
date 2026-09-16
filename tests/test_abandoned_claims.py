"""Durable no-grant certificates arbitrate atomically with delayed HTTP claims."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from scripts.fleet.coordinator import Coordinator, Conflict, Client, serve


def setup(tmp_path):
    c=Coordinator(tmp_path/'ledger.sqlite')
    c.enroll('one','a'*64,'source');c.reconcile('one','verified stopped')
    return c,dict(workbook='one',owner='host:worker',claim_id='retired-uuid',version='v1'),dict(path=str(tmp_path/'proof.json'),sha256='b'*64)


def test_tombstone_survives_restart_and_rejects_delayed_and_foreign_requests(tmp_path):
    c,args,evidence=setup(tmp_path);before=c.status()
    receipt=c.abandon_claim(**args,evidence=evidence)
    assert c.abandon_claim(**args,evidence=evidence)==receipt
    restarted=Coordinator(c.database)
    for override in ({},{'owner':'other'},{'version':'v2'},{'workbook':'other'}):
        with pytest.raises(Conflict,match='permanently abandoned'):
            restarted.claim(**dict(args,**override))
    after=c.status()
    assert after['workbooks']==before['workbooks'] and after['operations']==before['operations']
    assert after['abandoned_claims']==[receipt]
    with c.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM claims').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM events WHERE action='abandon_claim'").fetchone()[0]==1
    # A distinct request still goes through ordinary ready/capacity admission.
    assert c.claim(**dict(args,claim_id='fresh'))['fence']==1


def test_granted_request_cannot_be_retired_even_after_finish(tmp_path):
    c,args,evidence=setup(tmp_path);owned=c.claim(**args)
    for finished in (False,True):
        if finished:c.finish(**{k:owned[k]for k in ('workbook','owner','token','fence')},evidence='done',disposition='hold')
        with pytest.raises(Conflict,match='was granted'):c.abandon_claim(**args,evidence=evidence)
    assert c.status()['abandoned_claims']==[]


@pytest.mark.parametrize('changed',['owner','version','workbook','evidence'])
def test_retirement_identity_is_immutable(tmp_path,changed):
    c,args,evidence=setup(tmp_path);c.enroll('other','c'*64,'source')
    c.abandon_claim(**args,evidence=evidence)
    if changed=='evidence':evidence=dict(evidence,sha256='c'*64)
    else:args[changed]='other'
    with pytest.raises(Conflict):c.abandon_claim(**args,evidence=evidence)


def test_concurrent_grant_and_retirement_have_exactly_one_winner(tmp_path):
    c,args,evidence=setup(tmp_path)
    for i in range(12):
        wb=f'race{i}';c.enroll(wb,'a'*64,'source');c.reconcile(wb,'stopped')
        selected=dict(args,workbook=wb,claim_id=f'race-{i}');barrier=threading.Barrier(2)
        def attempt(retire):
            barrier.wait()
            try:
                return ('retired' if retire else 'granted',c.abandon_claim(**selected,evidence=evidence) if retire else c.claim(**selected))
            except Conflict:return None
        with ThreadPoolExecutor(max_workers=2)as pool:results=list(pool.map(attempt,[False,True]))
        assert sum(x is not None for x in results)==1
        with c.connect()as db:
            granted=db.execute('SELECT COUNT(*) FROM claims WHERE claim_id=?',(selected['claim_id'],)).fetchone()[0]
            retired=db.execute('SELECT COUNT(*) FROM abandoned_claims WHERE claim_id=?',(selected['claim_id'],)).fetchone()[0]
        assert granted+retired==1


def test_http_retirement_is_admin_only_and_late_http_claim_is_rejected(tmp_path):
    c,args,evidence=setup(tmp_path);server=serve(c,'127.0.0.1',0,'w'*32,'a'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    worker=tmp_path/'worker';worker.write_text('w'*32);admin=tmp_path/'admin';admin.write_text('a'*32)
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(RuntimeError,match='403'):Client(url,worker).call('abandon_claim',**args,evidence=evidence)
        saved=Client(url,admin).call('abandon_claim',**args,evidence=evidence)
        with pytest.raises(Conflict,match='permanently abandoned'):Client(url,worker).call('claim',**args)
        assert Client(url,worker).call('status')['abandoned_claims']==[saved]
    finally:server.shutdown();server.server_close();thread.join()
