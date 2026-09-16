import copy
import pytest
from scripts.fleet.coordinator import Coordinator
from scripts.fleet import pool_v5 as P

@pytest.fixture
def actual(tmp_path):
    c=Coordinator(tmp_path/'db.sqlite',clock=lambda:1000.)
    c.enroll('0498','a'*64,'exact original');c.reconcile('0498','stopped prior')
    claim=c.claim('0498','hhu-server-7:publication:0498:001','unique','bundle',lease_seconds=180)
    auth={k:claim[k]for k in('workbook','owner','token','fence')}
    c.begin('publication','publish-once',request_sha256='b'*64,**auth)
    return c,c.status()

def test_real_coordinator_operation_has_no_invented_fields(actual):
    c,status=actual;assert 'source_sha256'not in status['operations'][0]and 'version'not in status['operations'][0]
    before=copy.deepcopy(status)
    assert P.author_claims(status)==[]
    assert c.status()==before

@pytest.mark.parametrize('fault',['owner','fence','workbook','expires','missing','duplicate','held','unknown_owner','extra_source'])
def test_only_exact_active_publication_custody_is_excluded(actual,fault):
    _,status=actual;op=status['operations'][0]
    if fault in('owner','fence','workbook','expires'):op[fault]='foreign'
    elif fault=='missing':status['operations']=[]
    elif fault=='duplicate':status['operations'].append(copy.deepcopy(op))
    elif fault=='held':op['state']='hold'
    elif fault=='unknown_owner':status['workbooks'][0]['owner']='unknown-host:writer'
    elif fault=='extra_source':op['source_sha256']='c'*64
    with pytest.raises(ValueError):P.author_claims(status)
