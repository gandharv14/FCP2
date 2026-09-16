import copy,json
from concurrent.futures import ThreadPoolExecutor
import pytest
from scripts.fleet.coordinator import Coordinator,Conflict,digest,ADMIN
from scripts.fleet import capacity_policy as P


def fixture(tmp_path):
    now=[1000.];c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:now[0]);auth=dict(path='/authorization.json',sha256=P.AUTHORIZATION_SHA256);evidence=dict(path='/stopped-two-unknowns.json',sha256='c'*64)
    c.set_model_limit(None,auth,P.CHANGE_ID,0);claims={}
    for wb in ['0119','0059','0095','0001','0002','0003']:
        c.enroll(wb,'a'*64,'original source');c.reconcile(wb,'unused')
    for wb in ['0119','0059']:
        x=c.claim(wb,'hhu-server-3:slot-0:'+wb,'claim-'+wb,'b'*64,3600);claims[wb]={k:x[k]for k in ('workbook','owner','token','fence')}
        assert c.begin('model','model-'+wb,request_sha256='d'*64,**claims[wb])['execute']
    for wb in ['0119','0059']:c.end('model','model-'+wb,evidence='retained original unknown',outcome='uncertain',**claims[wb])
    quarantines=[]
    for wb in ['0119','0059']:
        op=next(o for o in c.status()['operations']if o['workbook']==wb)
        quarantines.append(c.quarantine_model(operation_id=op['operation_id'],operation_sha256=digest(op),workbook=wb,owner=op['owner'],fence=op['fence'],source_sha256='a'*64,version='b'*64,host='hhu-server-3',authorization=auth,evidence=evidence,change_id='scope-'+wb,expected_revision=len(quarantines),expires_at=20000))
    q=quarantines[0];cap=c.permit_quarantine_host_capacity('hhu-server-3',q['change_id'],digest(q),2,auth,evidence,'host-cap',0,20000)
    c.set_author_capacity(2,auth,evidence,'author-cap',digest(c.status()['workbooks']))
    args=dict(host='hhu-server-3',prior_capacity_sha256=digest(cap),quarantines={q['change_id']:digest(q)for q in quarantines},physical_model_limit=3,maximum_fresh_models=1,authorization=auth,evidence=evidence,change_id='physical-plus-one',expected_revision=0,expires_at=5000)
    return c,now,claims,args


def claim(c,wb,host='hhu-server-3'):
    x=c.claim(wb,host+':slot-1:'+wb,'next-'+wb,'b'*64,3600);return {k:x[k]for k in ('workbook','owner','token','fence')}


def test_two_unknowns_plus_one_fresh_api_slot_preserves_old_records(tmp_path):
    c,now,claims,args=fixture(tmp_path);before=c.status();r=c.permit_physical_capacity_extension(**args);after=c.status()
    for key in ('quarantines','quarantine_host_capacity','workbooks','operations','author_capacity_policy','author_reservations'):assert after[key]==before[key]
    assert P.validate_physical_extensions(after)==[r]
    assert P.validate_host_capacities(after)==before['quarantine_host_capacity']
    a=claim(c,'0095');b=claim(c,'0001');assert not P.model_admission_barriers(c.status(),'0095',a['owner'])
    assert c.begin('model','fresh',request_sha256='e'*64,**a)['execute']
    assert not c.begin('model','over-limit',request_sha256='e'*64,**b)['execute']
    assert len(P.model_admission_barriers(c.status(),'0001',b['owner']))==3
    assert c.begin('model','fresh',request_sha256='e'*64,**a)['execute']is False
    with pytest.raises(Conflict):c.begin('model','model-0059',request_sha256='d'*64,**claims['0059'])
    assert c.permit_physical_capacity_extension(**args)==r
    assert 'permit_physical_capacity_extension'in ADMIN


def test_racing_fresh_requests_share_one_remaining_physical_slot(tmp_path):
    c,now,_,args=fixture(tmp_path);c.permit_physical_capacity_extension(**args);a=claim(c,'0095');b=claim(c,'0001')
    def run(x):
        client=Coordinator(c.database,clock=c.clock);return client.begin('model','new-'+x['workbook'],request_sha256='e'*64,**x)
    results=list(ThreadPoolExecutor(max_workers=2).map(run,[a,b]));assert sum(r.get('execute')is True for r in results)==1
    assert len([x for x in c.status()['operations']if x['state']in ('active','hold')])==3


@pytest.mark.parametrize('delta',[{'physical_model_limit':4},{'maximum_fresh_models':2},{'host':'hhu-server-4'},{'prior_capacity_sha256':'e'*64},{'quarantines':{}},{'expected_revision':1},{'expires_at':float('inf')},{'expires_at':20000},{'expires_at':900}])
def test_wrong_or_unbounded_authority_cannot_create_ledger(tmp_path,delta):
    c,_,_,args=fixture(tmp_path);before=c.status()
    with pytest.raises(ValueError):c.permit_physical_capacity_extension(**dict(args,**delta))
    assert c.status()==before
    with c.connect()as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='physical_capacity_extensions'").fetchone()


def test_policy_expiry_restores_two_without_releasing_any_operation(tmp_path):
    c,now,_,args=fixture(tmp_path);c.permit_physical_capacity_extension(**args);a=claim(c,'0095');before=c.status()['operations'];now[0]=5001
    assert not P.validate_physical_extensions(c.status())
    assert P.model_admission_barriers(c.status(),'0095',a['owner'])
    with c.connect()as db:assert c.physical_model_limit(db,'hhu-server-3')==2
    assert c.status()['operations']==before


def test_other_hosts_still_have_two_authors_and_two_models(tmp_path):
    c,_,_,args=fixture(tmp_path);c.permit_physical_capacity_extension(**args);a=claim(c,'0095','hhu-server-4');b=claim(c,'0001','hhu-server-4')
    with pytest.raises(Conflict):claim(c,'0002','hhu-server-4')
    assert c.begin('model','healthy-a',request_sha256='e'*64,**a)['execute']
    assert c.begin('model','healthy-b',request_sha256='e'*64,**b)['execute']
    with c.connect()as db:assert c.physical_model_limit(db,'hhu-server-4')==2


def test_event_failure_rolls_back_new_ledger_and_policy(tmp_path,monkeypatch):
    c,_,_,args=fixture(tmp_path);before=c.status();monkeypatch.setattr(c,'event',lambda *a:(_ for _ in ()).throw(RuntimeError('stopped')))
    with pytest.raises(RuntimeError):c.permit_physical_capacity_extension(**args)
    assert c.status()==before


@pytest.mark.parametrize('key',['identity','author_policy_sha256','prior_capacity_sha256','physical_model_limit','unknown_operations_released','quarantines'])
def test_new_reader_rejects_tampered_extension(tmp_path,key):
    c,_,_,args=fixture(tmp_path);c.permit_physical_capacity_extension(**args);s=c.status();r=s['physical_capacity_extensions'][0];r[key]={'quarantines':{},'physical_model_limit':4,'unknown_operations_released':True}.get(key,'0'*64)
    with pytest.raises(ValueError):P.model_admission_barriers(s,'0095','hhu-server-3:slot-1:0095')


def test_original_frozen_reader_keeps_vm3_conservative_and_other_hosts_available(tmp_path):
    import importlib.util,hashlib
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/control/job-launch-v3-scoped-capacity-001/implementation/scripts/fleet/capacity_policy.py'
    if not path.exists():pytest.skip('actual frozen fleet reader is not present in this checkout')
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='805f0bbc95fecc24ce32e2e20a84a282d7537c94bd226355f9ebf90eb382da77'
    spec=importlib.util.spec_from_file_location('original_frozen_capacity_reader',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    c,_,_,args=fixture(tmp_path);c.permit_physical_capacity_extension(**args);s=c.status()
    assert len(old.model_admission_barriers(s,'0095','hhu-server-3:slot-1:0095'))==2
    assert not old.model_admission_barriers(s,'0001','hhu-server-4:slot-1:0001')
    assert not P.model_admission_barriers(s,'0095','hhu-server-3:slot-1:0095')


def test_real_http_admin_extension_and_worker_heartbeat_remain_compatible(tmp_path):
    import threading
    from scripts.fleet.coordinator import serve,Client
    c,_,_,args=fixture(tmp_path);server=serve(c,'127.0.0.1',0,'w'*32,'a'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    worker=tmp_path/'worker.token';admin=tmp_path/'admin.token';worker.write_text('w'*32);admin.write_text('a'*32);url='http://127.0.0.1:'+str(server.server_address[1]);reader=Client(url,worker);root=Client(url,admin)
    try:
        with pytest.raises(Exception):reader.call('permit_physical_capacity_extension',**args)
        record=root.call('permit_physical_capacity_extension',**args)
        assert reader.call('status')['physical_capacity_extensions']==[record]
        a=reader.call('claim',workbook='0095',owner='hhu-server-3:slot-0:0095',claim_id='actual-http-claim',version='b'*64,lease_seconds=3600);fields={k:a[k]for k in ('workbook','owner','token','fence')}
        assert reader.call('begin',kind='model',operation_id='fresh-http',request_sha256='e'*64,**fields)['execute']
        reader.call('heartbeat',lease_seconds=3600,**fields)
        reader.call('end',kind='model',operation_id='fresh-http',evidence='retained actual response',outcome='confirmed',**fields)
        assert len([x for x in reader.call('status')['operations']if x['state']=='hold'])==2
    finally:server.shutdown();server.server_close();thread.join()
