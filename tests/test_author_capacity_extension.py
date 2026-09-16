"""Real SQLite admission under a finite per-host author extension."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet.coordinator import Coordinator, Conflict, digest, ADMIN
from scripts.fleet import capacity_policy as P
from test_fleet_author_capacity import fleet, activate, claim, auth


def extension(c, now, **changes):
    args=dict(host='hhu-server-4',authors_per_host=3,
        author_policy_sha256=digest(c.status()['author_capacity_policy']),
        authorization=dict(path='/authorization.json',sha256='a'*64),
        evidence=dict(path='/measured-resource-census.json',sha256='b'*64),
        change_id='three-author-canary',expected_revision=0,expires_at=now[0]+120)
    return dict(args,**changes)


def test_racing_independent_pools_and_oneoff_share_exactly_three_slots(tmp_path):
    c,now=fleet(tmp_path);activate(c);args=extension(c,now)
    before=c.status();record=c.permit_author_capacity_extension(**args)
    after=c.status()
    for key in ('workbooks','operations','author_reservations','author_capacity_policy',
                'model_policy','model_limit','quarantines','physical_capacity_extensions'):
        assert after[key]==before[key]
    def attempt(i):
        other=Coordinator(c.database,clock=c.clock)
        try:return claim(other,i,'hhu-server-4'+(f':slot-2:oneoff-{i}'if i%2 else f'/slot-0/pool-{i}'))
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=12)as pool:
        winners=[r for r in pool.map(attempt,range(12))if r]
    assert len(winners)==3
    assert len([r for r in c.status()['author_reservations']if r['state']=='active'])==3
    assert c.permit_author_capacity_extension(**args)==record
    assert 'permit_author_capacity_extension'in ADMIN


def test_three_authors_still_share_two_physical_models(tmp_path):
    c,now=fleet(tmp_path);activate(c)
    c.set_model_limit(None,dict(path='/models.json',sha256=P.AUTHORIZATION_SHA256),P.CHANGE_ID,0)
    c.permit_author_capacity_extension(**extension(c,now))
    rows=[claim(c,i,f'hhu-server-4:slot-{i}:author-{i}')for i in range(3)]
    answers=[c.begin('model','op-'+str(i),request_sha256='c'*64,**auth(r))for i,r in enumerate(rows)]
    assert [r['execute']for r in answers]==[True,True,False]
    assert c.status()['model_limit']is None and c.status()['publication_limit']==1
    # Independent hosts retain the original two-author limit.
    claim(c,3);claim(c,4)
    with pytest.raises(Conflict):claim(c,5)


def test_expiry_reverts_admission_without_killing_or_releasing_existing_owners(tmp_path):
    c,now=fleet(tmp_path);activate(c);c.permit_author_capacity_extension(**extension(c,now,expires_at=1010))
    rows=[claim(c,i,f'hhu-server-4:slot-{i}:author-{i}')for i in range(3)]
    now[0]=1011
    for row in rows:c.heartbeat(**auth(row),lease_seconds=30)
    assert claim(c,0,rows[0]['owner'])==c.claim('0',rows[0]['owner'],'claim-0','v1',30)
    with pytest.raises(Conflict):claim(c,3,'hhu-server-4:slot-0:next')
    c.finish(**auth(rows[0]),evidence='actual stopped',disposition='hold')
    with pytest.raises(Conflict):claim(c,3,'hhu-server-4:slot-0:next')
    c.finish(**auth(rows[1]),evidence='actual stopped',disposition='hold')
    assert claim(c,3,'hhu-server-4:slot-0:next')['fence']==1


def test_expired_worker_still_occupies_extended_capacity_until_reconciled(tmp_path):
    c,now=fleet(tmp_path);activate(c);c.permit_author_capacity_extension(**extension(c,now))
    [claim(c,i,f'hhu-server-4:slot-{i}:author-{i}')for i in range(3)]
    now[0]+=31
    c=Coordinator(c.database,clock=c.clock)
    with pytest.raises(Conflict):claim(c,3,'hhu-server-4:slot-0:next')
    assert {r['state']for r in c.status()['author_reservations']}=={'hold'}
    c.reconcile('0','exact process stopped and complete retained artifacts')
    assert claim(c,3,'hhu-server-4:slot-0:next')['fence']==1


@pytest.mark.parametrize('change',[dict(authors_per_host=4),dict(authors_per_host=True),
    dict(host='local'),dict(host='hhu-server-9'),dict(expires_at=1000),dict(expires_at=15401),
    dict(expires_at=float('nan')),dict(expected_revision=1),dict(author_policy_sha256='0'*64)])
def test_wrong_authority_is_atomic(tmp_path,change):
    c,now=fleet(tmp_path);activate(c);before=c.status()
    with pytest.raises(ValueError):c.permit_author_capacity_extension(**extension(c,now,**change))
    assert c.status()==before


def test_duplicate_or_interrupted_activation_does_not_mutate_history(tmp_path,monkeypatch):
    c,now=fleet(tmp_path);activate(c);args=extension(c,now);before=c.status()
    with monkeypatch.context()as m:
        def failed(*a):raise RuntimeError('interrupted before transaction commit')
        m.setattr(c,'event',failed)
        with pytest.raises(RuntimeError):c.permit_author_capacity_extension(**args)
    assert c.status()==before
    c.permit_author_capacity_extension(**args)
    with pytest.raises(Conflict):c.permit_author_capacity_extension(**dict(args,expires_at=1110))
    assert len(c.status()['author_capacity_extensions'])==1


def test_existing_unknown_host_policy_remains_closed(tmp_path):
    from test_physical_capacity_extension import fixture
    c,now,_,physical=fixture(tmp_path);before=c.status()
    with pytest.raises(Conflict):c.permit_author_capacity_extension(**extension(c,now,host='hhu-server-3'))
    assert c.status()==before
    c.permit_author_capacity_extension(**extension(c,now))
    # A different host's author canary cannot invalidate or relax model quarantine.
    assert P.validate_quarantines(c.status())==P.validate_quarantines(before)
