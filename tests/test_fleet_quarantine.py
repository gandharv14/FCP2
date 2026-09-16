"""Real SQLite isolation leaves uncertain effects consumed and visible."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import pytest
from scripts.fleet.coordinator import Coordinator, Conflict, digest, ADMIN, Client, serve
from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256, CHANGE_ID, model_admission_barriers, validate_quarantines


def setup(tmp):
    now=[1000.0];c=Coordinator(tmp/'ledger',clock=lambda:now[0])
    auth=dict(path='/authorized/concurrency.json',sha256=AUTHORIZATION_SHA256)
    c.set_model_limit(None,auth,CHANGE_ID,0)
    claims={}
    for wb,host in [('0413','hhu-server'),('0140','hhu-server-7'),('0141','hhu-server-3')]:
        c.enroll(wb,'a'*64,'original source');c.reconcile(wb,'stopped original','ready')
        v=c.claim(wb,host+':slot-1:'+wb,wb+'-claim','b'*64,3600)
        claims[wb]={k:v[k]for k in ('workbook','owner','token','fence')}
    assert c.begin('model','uncertain',request_sha256='c'*64,**claims['0413'])['execute']
    c.end('model','uncertain',evidence='transport-start without response',outcome='uncertain',**claims['0413'])
    op=next(o for o in c.status()['operations']if o['operation_id']=='uncertain')
    arguments=dict(operation_id='uncertain',workbook='0413',owner=claims['0413']['owner'],fence=1,
                   source_sha256='a'*64,version='b'*64,host='hhu-server',operation_sha256=digest(op),
                   authorization=dict(path='/authorized/quarantine.json',sha256='d'*64),
                   evidence=dict(path='/proof/exact-stopped-transport.json',sha256='e'*64),
                   change_id='isolate-0413-001',expected_revision=0,expires_at=2000)
    return c,now,claims,arguments


def retained(c):
    with c.connect()as db:return {table:[tuple(row)for row in db.execute('SELECT * FROM '+table)]for table in ('workbooks','claims','operations')}


def test_uncertain_effect_stays_byte_identical_and_unrelated_models_can_overlap(tmp_path):
    c,now,claims,args=setup(tmp_path)
    assert c.begin('model','first',request_sha256='f'*64,**claims['0140'])['execute']is False
    before=retained(c);q=c.quarantine_model(**args)
    assert retained(c)==before
    assert c.status()['model_policy']['unknown_model_hold_barrier']=='global'
    assert validate_quarantines(c.status())==[q]
    assert model_admission_barriers(c.status(),'0140',claims['0140']['owner'])==[]
    with ThreadPoolExecutor(max_workers=2)as pool:
        values=list(pool.map(lambda wb:c.begin('model','actual-'+wb,request_sha256='f'*64,**claims[wb]),['0140','0141']))
    assert all(v['execute']for v in values)
    assert len([o for o in c.status()['operations']if o['state']in('active','hold')])==3
    assert not c.begin('model','actual-0140',request_sha256='f'*64,**claims['0140'])['execute']
    assert not c.begin('model','second-samebook',request_sha256='f'*64,**claims['0140'])['execute']
    with pytest.raises(Conflict):c.reconcile('0413','still no response','ready')
    with pytest.raises(Conflict):c.begin('model','uncertain',request_sha256='c'*64,**claims['0413'])
    assert next(o for o in c.status()['operations']if o['operation_id']=='uncertain')['state']=='hold'


def test_same_host_claim_rejected_and_expiry_restores_global_barrier(tmp_path):
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
    c.enroll('new','f'*64,'source');c.reconcile('new','fresh','ready')
    with pytest.raises(Conflict):c.claim('new','hhu-server:slot-2:new','new','v')
    with pytest.raises(ValueError):model_admission_barriers(c.status(),'new','hhu-server:slot-2:new')
    now[0]=2000
    assert validate_quarantines(c.status())==[]
    assert c.begin('model','newrequest',request_sha256='f'*64,**claims['0140'])['execute']is False
    with pytest.raises(Conflict):c.claim('new','hhu-server:slot-2:new','new','v')


def test_lost_reply_is_idempotent_without_changing_effect_or_extending_authority(tmp_path):
    c,now,claims,args=setup(tmp_path);q=c.quarantine_model(**args);before=retained(c)
    now[0]=2100
    assert c.quarantine_model(**args)==q
    assert retained(c)==before
    with pytest.raises(Conflict):c.quarantine_model(**{**args,'expires_at':3000})
    assert len(c.status()['quarantines'])==1


@pytest.mark.parametrize('key,value',[('operation_sha256','f'*64),('owner','hhu-server:slot-2:foreign'),('fence',2),('source_sha256','f'*64),('version','foreign'),('workbook','0140'),('expected_revision',1),('host','hhu-server-2'),('expires_at',1000),('expires_at',100000),('expires_at',float('nan'))])
def test_foreign_stale_unbounded_authority_rejected_without_mutation(tmp_path,key,value):
    c,now,claims,args=setup(tmp_path);before=retained(c)
    with pytest.raises((ValueError,Conflict)):c.quarantine_model(**{**args,key:value})
    assert retained(c)==before and not c.status()['quarantines']


def test_host_with_other_active_claim_cannot_be_quarantined(tmp_path):
    c,now,claims,args=setup(tmp_path)
    c.enroll('other','f'*64,'source');c.reconcile('other','stopped','ready');c.claim('other','hhu-server:slot-2:other','other','v')
    with pytest.raises(Conflict):c.quarantine_model(**args)


def test_other_unknown_hold_and_publication_capacity_are_not_ignored(tmp_path):
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
    c.begin('model','secondunknown',request_sha256='f'*64,**claims['0140'])
    c.end('model','secondunknown',evidence='unknown second transport',outcome='uncertain',**claims['0140'])
    assert not c.begin('model','third',request_sha256='f'*64,**claims['0141'])['execute']
    assert len(model_admission_barriers(c.status(),'0141',claims['0141']['owner']))==1
    assert c.begin('publication','pub',request_sha256='f'*64,**claims['0141'])['execute']
    assert not c.begin('publication','pub2',request_sha256='f'*64,**claims['0141'])['execute']


@pytest.mark.parametrize('target,key,value',[('quarantine','operation_sha256','f'*64),('claim','source_sha256','f'*64),('operation','evidence','forged'),('quarantine','host','hhu-server-2'),('quarantine','expires_at',100000)])
def test_status_reader_rejects_unbound_quarantine_projection(tmp_path,target,key,value):
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args);status=c.status()
    selected={'quarantine':status['quarantines'][0],'claim':next(w for w in status['workbooks']if w['workbook']=='0413'),'operation':status['operations'][0]}
    selected[target][key]=value
    with pytest.raises(ValueError):validate_quarantines(status)


def test_quarantine_is_admin_only_over_actual_http(tmp_path):
    c,now,claims,args=setup(tmp_path)
    assert 'quarantine_model'in ADMIN
    server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        token=tmp_path/'worker.token';token.write_text('w'*32)
        client=Client('http://127.0.0.1:'+str(server.server_port),token)
        with pytest.raises(Exception):client.call('quarantine_model',**args)
        assert not c.status()['quarantines']
    finally:server.shutdown();server.server_close();thread.join()


def test_actual_pool_slash_owner_and_local_publication_identity_are_supported(tmp_path):
    from scripts.fleet.capacity_policy import quarantine_host
    actual='hhu-server/slot-0/2cf9c9a1-20df-49ec-a48b-0db75fb28be5'
    assert Coordinator.host(actual)==quarantine_host(actual)=='hhu-server'
    assert Coordinator.host('local:collection:0004:001')=='local'
    for owner in ('hhu-server-10/slot-0/a','prefixhhu-server:slot-1:x','hhu-server','local:collection:arbitrary'):
        with pytest.raises((Conflict,ValueError)):Coordinator.host(owner)
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
    c.enroll('0004','f'*64,'source');c.reconcile('0004','collected input','ready')
    value=c.claim('0004','local:collection:0004:001','local','v')
    auth={k:value[k]for k in ('workbook','owner','token','fence')}
    assert c.begin('publication','collect',request_sha256='f'*64,**auth)['execute']
    with pytest.raises(Conflict):c.begin('model','bad-local-model',request_sha256='f'*64,**auth)


def test_generic_launch_gate_uses_only_explicit_quarantine_without_relaxing_target(tmp_path):
    from scripts.fleet.launch_job_v3 import central_gate
    c,now,claims,args=setup(tmp_path);prior=dict(path='/original/held.json',sha256='a'*64)
    c.finish(evidence=json.dumps(prior),disposition='hold',**claims['0140'])
    p=dict(new_owner='hhu-server-7:slot-1:next',expected_fence=1,expected_owner=claims['0140']['owner'],expected_version='b'*64,prior_outcome=prior)
    job=dict(workbook='0140',source=dict(sha256='a'*64))
    with pytest.raises(ValueError):central_gate(c.status(),p,job)
    c.quarantine_model(**args)
    assert central_gate(c.status(),p,job)['workbook']=='0140'
    with pytest.raises(ValueError):central_gate(c.status(),{**p,'new_owner':'hhu-server/slot-2/foreign'},job)
    now[0]=2000
    with pytest.raises(ValueError):central_gate(c.status(),p,job)
