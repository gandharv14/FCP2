"""Actual durable transactions across unrelated pools and old worker clients."""
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from scripts.fleet.coordinator import Coordinator,Conflict,digest


def fleet(tmp_path,n=12):
    now=[1000.0];c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:now[0])
    for i in range(n):
        c.enroll(str(i),'a'*64,'inventory');c.reconcile(str(i),'native stopped proof')
    return c,now


def activate(c):
    return c.set_author_capacity(2,{'path':'/authorization.json','sha256':'a'*64},
        {'path':'/all-hosts-census.json','sha256':'b'*64},'capacity-1',digest(c.status()['workbooks']))


def claim(c,wb,owner=None):
    return c.claim(str(wb),owner or f'hhu-server-2/slot-0/pool-{wb}',f'claim-{wb}','v1',30)


def auth(row):return {k:row[k]for k in ('workbook','owner','token','fence')}


def test_cross_pool_and_oneoff_claims_atomically_share_two_slots(tmp_path):
    c,_=fleet(tmp_path);activate(c)
    def attempt(i):
        independent=Coordinator(c.database,clock=c.clock)
        try:return claim(independent,i,f'hhu-server-2'+(f':slot-0:oneoff-{i}'if i%2 else f'/slot-1/pool-{i}'))
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=12)as pool:winners=[r for r in pool.map(attempt,range(12))if r]
    assert len(winners)==2
    status=c.status();assert len([r for r in status['workbooks']if r['state']=='active'])==2
    assert len([r for r in status['author_reservations']if r['state']=='active'])==2
    assert all(r['fence']==0 for r in status['workbooks']if r['state']=='ready')
    assert 'token'not in json.dumps(status['author_reservations'])


def test_activation_adopts_existing_workers_without_changing_any_claim(tmp_path):
    c,_=fleet(tmp_path);a=claim(c,0);b=claim(c,1);before=c.status()['workbooks']
    policy=activate(c)
    assert c.status()['workbooks']==before
    assert policy['authors_per_host']==2
    assert claim(c,0)==a
    with pytest.raises(Conflict):claim(c,2)
    c.heartbeat(**auth(b),lease_seconds=40)
    assert len(c.status()['author_reservations'])==2


def test_expiry_remains_occupied_after_restart_until_exact_reconciliation(tmp_path):
    c,now=fleet(tmp_path);activate(c);a=claim(c,0);claim(c,1);now[0]+=31
    c=Coordinator(c.database,clock=c.clock)
    with pytest.raises(Conflict):claim(c,2)
    assert {r['state']for r in c.status()['author_reservations']}=={'hold'}
    with pytest.raises(Conflict):c.finish(**auth(a),evidence='late stale owner')
    c.reconcile('0','exact native worker stopped and artifacts retained')
    assert claim(c,2)['fence']==1
    assert c.status()['author_reservations'][0]['state']=='released'


def test_original_worker_finish_releases_slot_but_preserves_workbook_hold(tmp_path):
    c,_=fleet(tmp_path);activate(c);a=claim(c,0);claim(c,1)
    c.finish(**auth(a),evidence='bound original worker outcome after guardian drain',disposition='hold')
    assert c.status()['workbooks'][0]['state']=='hold'
    assert claim(c,2)['fence']==1
    with pytest.raises(Conflict):c.finish(**auth(a),evidence='stale')


def test_publication_and_other_host_do_not_consume_author_slot(tmp_path):
    c,_=fleet(tmp_path);activate(c);claim(c,0);claim(c,1)
    claim(c,2,'hhu-server-2:publication:2:001')
    claim(c,3,'local:collection:3:001')
    claim(c,4,'hhu-server-3:slot-1:author')
    assert len(c.status()['author_reservations'])==3
    with pytest.raises(Conflict):claim(c,5,'hhu-server-2:publication:other:001')
    with pytest.raises(Conflict):claim(c,5,'hhu-server-20:slot-0:author')


def test_failed_activation_is_atomic_and_default_policy_unchanged(tmp_path):
    c,_=fleet(tmp_path);[claim(c,i)for i in range(3)]
    before=c.status()['workbooks']
    with pytest.raises(Conflict):activate(c)
    assert c.status()['author_capacity_policy']is None
    assert c.status()['author_reservations']==[]and c.status()['workbooks']==before


def test_activation_idempotence_stale_snapshot_and_conflicting_authority(tmp_path):
    c,_=fleet(tmp_path);args=(2,{'path':'/auth','sha256':'a'*64},{'path':'/proof','sha256':'b'*64},'once',digest(c.status()['workbooks']))
    claim(c,0)
    with pytest.raises(Conflict):c.set_author_capacity(*args)
    args=(*args[:-1],digest(c.status()['workbooks']))
    p=c.set_author_capacity(*args);assert c.set_author_capacity(*args)==p
    with pytest.raises(Conflict):c.set_author_capacity(*args[:-2],'different',args[-1])
    assert len(c.status()['author_reservations'])==1


def test_old_operation_uncertainty_is_never_removed_by_author_finish(tmp_path):
    c,_=fleet(tmp_path);activate(c);a=claim(c,0)
    c.begin('model','original-call',**auth(a),request_sha256='c'*64)
    c.finish(**auth(a),evidence='guard stopped; outcome uncertain',disposition='hold')
    s=c.status();assert s['operations'][0]['state']=='hold'
    assert s['author_reservations'][0]['state']=='released'
    with pytest.raises(Conflict):c.reconcile('0','cannot erase outstanding call')


@pytest.mark.parametrize('owner',['hhu-server-2:publication:0:001','local:collection:0:001','local:publication:0:001'])
def test_publication_exempt_identity_cannot_dispatch_model(tmp_path,owner):
    c,_=fleet(tmp_path);activate(c);a=claim(c,0,owner)
    with pytest.raises(Conflict,match='publication-exempt'):
        c.begin('model','bad-model',**auth(a),request_sha256='d'*64)
    assert c.status()['operations']==[]
    assert c.begin('publication','publication',**auth(a),request_sha256='d'*64)['execute']


def test_local_author_requires_explicit_local_resource_policy(tmp_path):
    c,_=fleet(tmp_path);p=activate(c)
    assert p['local_authors']=='blocked-pending-local-resource-policy'
    with pytest.raises(Conflict,match='local resource policy'):claim(c,0,'local:slot-0:author')
    assert c.status()['author_reservations']==[]
