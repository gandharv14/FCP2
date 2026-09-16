import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet import stopped_author_reconciliation as R
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID


def fixture(tmp_path):
    now=[1000.];c=Coordinator(tmp_path/'db',clock=lambda:now[0]);auth=dict(path='/auth',sha256=AUTHORIZATION_SHA256);e=dict(path='/stopped',sha256='c'*64)
    c.set_model_limit(None,auth,CHANGE_ID,0)
    for wb in ('0119','0059','0095','fresh2','fresh3','other'):
        c.enroll(wb,'a'*64,'source');c.reconcile(wb,'unused')
    c.set_author_capacity(2,auth,e,'capacity',digest(c.status()['workbooks']))
    claims={}
    for wb in ('0119','0059'):
        r=c.claim(wb,'hhu-server-3:slot-0:'+wb,'claim-'+wb,'b'*64,3600);claims[wb]={k:r[k]for k in('workbook','owner','token','fence')}
        assert c.begin('model','call-'+wb,request_sha256='d'*64,**claims[wb])['execute']
    for wb in claims:c.end('model','call-'+wb,evidence='unknown submitted response',outcome='uncertain',**claims[wb])
    for wb in claims:
        op=next(o for o in c.status()['operations']if o['workbook']==wb)
        c.quarantine_model(operation_id=op['operation_id'],operation_sha256=digest(op),workbook=wb,owner=op['owner'],fence=op['fence'],source_sha256='a'*64,version='b'*64,host='hhu-server-3',authorization=auth,evidence=e,change_id='q-'+wb,expected_revision=len(c.status()['quarantines']),expires_at=5000)
    q=c.status()['quarantines'][0];cap=c.permit_quarantine_host_capacity('hhu-server-3',q['change_id'],digest(q),2,auth,e,'cap',0,5000)
    c.permit_physical_capacity_extension(host='hhu-server-3',prior_capacity_sha256=digest(cap),quarantines={q['change_id']:digest(q)for q in c.status()['quarantines']},physical_model_limit=3,maximum_fresh_models=1,authorization=auth,evidence=e,change_id='plus',expected_revision=0,expires_at=4000)
    return c,now,claims,auth,e


def args(c,auth,e,wb='0119'):
    s=c.status();w=next(w for w in s['workbooks']if w['workbook']==wb);r=next(r for r in s['author_reservations']if r['workbook']==wb)
    return dict(workbook=wb,claim_id=r['claim_id'],owner=r['owner'],fence=r['fence'],reservation_sha256=digest(r),workbook_sha256=digest(w),quarantines={q['change_id']:digest(q)for q in s['quarantines']if q['workbook']==wb},authorization=auth,evidence=e,change_id='release-'+wb)


def test_real_original_transactions_free_author_only_and_preserve_unknown_slots(tmp_path):
    c,_,claims,auth,e=fixture(tmp_path)
    with pytest.raises(Conflict):c.claim('0095','hhu-server-3:slot-0:0095','fresh','b'*64)
    with pytest.raises(Conflict):c.reconcile('0119','must not erase unknown')
    a=args(c,auth,e);before=c.status();receipt=R.reconcile(c,**a);after=c.status()
    for key in before:
        if key not in('author_reservations','events','at'):assert after[key]==before[key]
    assert after['events']==before['events']+1 and next(r for r in after['author_reservations']if r['workbook']=='0119')['state']=='released'
    assert R.reconcile(c,**a)==receipt
    with pytest.raises(Conflict):c.begin('model','call-0119',request_sha256='d'*64,**claims['0119'])
    fresh=c.claim('0095','hhu-server-3:slot-0:0095','fresh','b'*64,3600)
    assert c.begin('model','new',request_sha256='d'*64,**{k:fresh[k]for k in('workbook','owner','token','fence')})['execute']
    with pytest.raises(Conflict):c.claim('fresh2','hhu-server-3:slot-0:fresh2','fresh2','b'*64)
    R.reconcile(c,**args(c,auth,e,'0059'))
    fresh2=c.claim('fresh2','hhu-server-3:slot-1:fresh2','fresh2','b'*64,3600)
    assert not c.begin('model','overcap',request_sha256='d'*64,**{k:fresh2[k]for k in('workbook','owner','token','fence')})['execute']


@pytest.mark.parametrize('delta',[{'claim_id':'foreign'},{'owner':'hhu-server-3:slot-0:foreign'},{'fence':2},{'reservation_sha256':'e'*64},{'workbook_sha256':'e'*64},{'quarantines':{}},{'quarantines':{'wrong':'a'*64}}])
def test_changed_custody_cannot_release(tmp_path,delta):
    c,_,_,auth,e=fixture(tmp_path);a=args(c,auth,e);before=c.status()
    with pytest.raises(Conflict):R.reconcile(c,**dict(a,**delta))
    assert c.status()==before


def test_expired_quarantine_and_event_failure_cannot_release(tmp_path,monkeypatch):
    c,now,_,auth,e=fixture(tmp_path);a=args(c,auth,e);now[0]=5001;before=c.status()
    with pytest.raises(Conflict):R.reconcile(c,**a)
    assert c.status()==before
    now[0]=1000;monkeypatch.setattr(c,'event',lambda *a:(_ for _ in ()).throw(RuntimeError('journal failure')))
    with pytest.raises(RuntimeError):R.reconcile(c,**a)
    assert c.status()['author_reservations']==before['author_reservations']


def test_concurrent_identical_reconciliation_is_once(tmp_path):
    c,_,_,auth,e=fixture(tmp_path);a=args(c,auth,e);count=c.status()['events']
    def run(_):return R.reconcile(Coordinator(c.database,clock=c.clock),**a)
    out=list(ThreadPoolExecutor(max_workers=2).map(run,range(2)))
    assert out[0]==out[1]and c.status()['events']==count+1
    with pytest.raises(Conflict):R.reconcile(c,**dict(a,evidence=dict(path='/different',sha256='f'*64)))


def test_no_outstanding_operation_releases_only_expired_author(tmp_path):
    c,now,_,auth,e=fixture(tmp_path)
    r=c.claim('fresh2','hhu-server-4:slot-0:fresh2','stopped-clean','b'*64,180)
    identity={k:r[k]for k in('workbook','owner','token','fence')}
    assert c.begin('model','confirmed-clean',request_sha256='d'*64,**identity)['execute']
    c.end('model','confirmed-clean',evidence='original completed response',outcome='confirmed',**identity)
    now[0]+=181;c.status();a=args(c,auth,e,'fresh2');before=c.status()
    receipt=R.reconcile(c,**a);after=c.status()
    assert receipt['schema_version']=='fleet-stopped-author-no-outstanding-operation/v2'
    for key in before:
        if key not in('author_reservations','events','at'):assert after[key]==before[key]
    assert next(w for w in after['workbooks']if w['workbook']=='fresh2')['owner']==identity['owner']
    assert next(r for r in after['author_reservations']if r['workbook']=='fresh2')['state']=='released'
    assert R.reconcile(c,**a)==receipt


def test_empty_quarantine_cannot_hide_an_outstanding_operation(tmp_path):
    c,now,_,auth,e=fixture(tmp_path)
    r=c.claim('fresh2','hhu-server-4:slot-0:fresh2','stopped-clean','b'*64,180)
    assert c.begin('model','unresolved-clean',request_sha256='d'*64,**{k:r[k]for k in('workbook','owner','token','fence')})['execute']
    now[0]+=181;c.status();a=args(c,auth,e,'fresh2');before=c.status()
    with pytest.raises(Conflict,match='outstanding operations'):R.reconcile(c,**a)
    assert c.status()==before
