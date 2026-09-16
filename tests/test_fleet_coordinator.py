"""Real durable transactions, fencing, uncertain side effects and HTTP auth."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from scripts.fleet.coordinator import Client, Conflict, Coordinator, serve


@pytest.fixture
def fleet(tmp_path):
    now=[1000.0]
    c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:now[0])
    for wb in ['one','two']:
        c.enroll(wb,'a'*64,'inventory.json sha256:'+('b'*64))
        c.reconcile(wb,'stopped-owner-and-budget-report.json')
    return c,now


def claim(c,wb='one',owner='vm-1/worker-1',cid='claim-1'):
    result=c.claim(wb,owner,cid,'bundle-sha256',lease_seconds=30)
    return {k:result[k] for k in ['workbook','owner','token','fence']}


def test_parallel_claims_have_exactly_one_owner(fleet):
    c,_=fleet
    def attempt(index):
        try:return claim(c,owner=f'vm-{index}',cid=f'claim-{index}')
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        winners=[r for r in pool.map(attempt,range(16)) if r]
    assert len(winners)==1
    assert c.status()['workbooks'][0]['fence']==1
    assert 'token' not in json.dumps(c.status())


def test_lost_claim_response_idempotent_but_expiry_never_requeues(fleet):
    c,now=fleet
    owner=claim(c)
    assert claim(c)==owner
    now[0]+=31
    with pytest.raises(Conflict):claim(c,owner='another',cid='another')
    assert c.status()['workbooks'][0]['state']=='hold'
    with pytest.raises(Conflict):c.heartbeat(**owner)
    c.reconcile('one','remote process stopped; artifacts verified; budget retained')
    replacement=claim(c,owner='replacement',cid='replacement')
    assert replacement['fence']==2
    with pytest.raises(Conflict):c.finish(**owner,evidence='stale')


def test_global_model_limit_survives_restart_and_uncertain_response(fleet):
    c,_=fleet
    a=claim(c);b=claim(c,'two','vm-2','claim-2')
    assert c.begin('model','model-a',**a,request_sha256='b'*64)['execute']
    assert not c.begin('model','model-b',**b,request_sha256='c'*64)['execute']
    c.end('model','model-a',**a,evidence='partial-response.sha256',outcome='uncertain')
    restarted=Coordinator(c.database,clock=c.clock)
    assert restarted.begin('model','model-b',**b,request_sha256='c'*64)['state']=='hold'
    with pytest.raises(Conflict):c.reconcile('one','owner stopped')
    c.reconcile_operation('model','model-a','provider settled; consumed attempt retained','abandoned')
    assert c.begin('model','model-b',**b,request_sha256='c'*64)['execute']


def test_interrupted_active_operation_holds_and_fences(fleet):
    c,now=fleet
    a=claim(c)
    c.begin('model','request',**a,request_sha256='b'*64)
    now[0]+=31
    with pytest.raises(Conflict):c.end('model','request',**a,evidence='late response')
    assert c.status()['operations'][0]['state']=='hold'
    c.reconcile_operation('model','request','retained late complete response hash verified','confirmed')
    c.reconcile('one','old worker stopped; durable call retained')
    a2=claim(c,cid='claim-2')
    assert c.begin('model','request',**a2,request_sha256='b'*64)['execute'] is False


def test_publication_serialization_identity_and_idempotency(fleet):
    c,_=fleet
    a=claim(c);b=claim(c,'two','other','claim-2')
    assert c.begin('publication','generation-a',**a,request_sha256='d'*64)['execute']
    assert c.begin('publication','generation-b',**b,request_sha256='e'*64)['busy']
    c.end('publication','generation-a',**a,evidence='independently-verified-tree-sha256')
    assert c.begin('publication','generation-a',**a,request_sha256='d'*64)['execute'] is False
    with pytest.raises(Conflict):c.begin('publication','generation-a',**a,request_sha256='f'*64)
    assert c.begin('publication','generation-b',**b,request_sha256='e'*64)['execute']


def test_heartbeat_renews_global_operation(fleet):
    c,now=fleet;a=claim(c)
    c.begin('model','req',**a,request_sha256='b'*64)
    now[0]+=25;c.heartbeat(**a,lease_seconds=30)
    now[0]+=20
    assert c.status()['operations'][0]['state']=='active'
    c.end('model','req',**a,evidence='durable response hash')
    c.finish(**a,evidence='independent verified publication')
    assert c.status()['workbooks'][0]['state']=='complete'


def test_http_worker_cannot_admin_or_leak_claim_tokens(fleet,tmp_path):
    c,_=fleet
    server=serve(c,'127.0.0.1',0,'w'*32,'a'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    token=tmp_path/'worker-token';token.write_text('w'*32)
    client=Client(f'http://127.0.0.1:{server.server_port}',token)
    try:
        assert client.call('status')['model_limit']==1
        with pytest.raises(RuntimeError,match='403'):client.call('reconcile',workbook='one',evidence='fake')
        a=client.call('claim',workbook='one',owner='test',claim_id='http',version='version')
        assert len(a['token'])==64
        assert a['token'] not in json.dumps(client.call('status'))
    finally:
        server.shutdown();server.server_close();thread.join()


def test_inventory_change_and_initial_ownership_are_fail_closed(tmp_path):
    c=Coordinator(tmp_path/'db.sqlite')
    c.enroll('new','a'*64,'inventory')
    with pytest.raises(Conflict):claim(c,'new')
    with pytest.raises(Conflict):c.enroll('new','b'*64,'changed')
