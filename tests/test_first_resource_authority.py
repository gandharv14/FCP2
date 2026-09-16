"""First resource-only admission retains consumed attempts and atomic custody."""
import copy
from pathlib import Path
import pytest
from scripts.fleet import first_resource_authority as A,first_custody as X
from scripts.fleet.migration import retain,binding,checked
from scripts.fleet.coordinator import Coordinator,Conflict,digest

@pytest.fixture
def fixture(tmp_path):
    i=[0]
    def write(v):
        i[0]+=1;return retain(tmp_path/f'{i[0]}.json',v)
    source=write({'fixture':'source'});driver=write({'fixture':'driver'})
    code=tmp_path/'version';code.mkdir();(code/'original.py').write_text('# original source producer\n')
    snap=write(dict(root=str(code),fingerprint='version',bindings={'original.py':binding(code/'original.py')}))
    ledger=write(dict(build=dict(state='STOPPED',stage='segmentation',exit_code=124,resources={'limit_reason':'wall_time'},attempts=1,source_checkpoint={'state':'complete'},source_generation_id='generation'),build_history=[]))
    closure=write({'files':{'ledger':ledger}})
    allowance=write({'fixture':'explicit policy'})
    plan=dict(schema_version='fleet-resource-continuation/v1',helper=driver,models=0,automatic_retry=False,workbook='0098',source=source,ledger=ledger,snapshot=snap,previous_attempt=1,next_attempt=2,cumulative_reconstruction_limit=2,generation='generation',memory_bytes=8*1024**3,seconds=7200,home=str(tmp_path/'next'),allowance=allowance,closure=closure)
    pb=write(plan)
    job=dict(workbook='0098',source=source,driver=driver,inputs=[pb],version='version',cwd=str(code),result=str(tmp_path/'next/result.json'),memory_limit_bytes=8*1024**3,timeout_seconds=7500,minimum_disk_bytes=12*1024**3,operational_allowance=allowance)
    job['command']=['/runtime/python','-I','-B',driver['path'],'run','--plan',pb['path'],'--sha256',pb['sha256']]
    inspection=write(dict(status='SOURCE_REUSE_PREFLIGHT_PASS',plan=pb,generation='generation',previous_attempt=1,next_attempt=2,source_reused=True,numerical_segmentation_validated=False,models_dispatched=0,attempts_dispatched=0))
    guard=write(dict(returncode=0,failure_type=None,log=inspection,models_dispatched=0,attempts_dispatched=0,command=job['command'][:4]+['inspect']+job['command'][5:],metrics={'peak_rss_bytes':360000000},started_at=1,ended_at=80))
    files=[pb,snap,driver,ledger,binding(code/'original.py')]
    manifest=write(dict(archive_sha256='a'*64,files=files))
    receipt=write(dict(archive_sha256='a'*64,absolute_paths_preserved=True,historical_bytes_rewritten=False,attempts_reset=False,files=[dict(original_path=x['path'],**x)for x in files]))
    transfer=write(dict(host='hhu-server-6',claims=0,reconstructions=0,manifest=manifest,receipt=receipt))
    jb=write(job);a=dict(schema_version=A.SCHEMA,kind='resource-reconstruction',workbook='0098',job=jb,source=source,plan=pb,inspection=inspection,resource=guard,transfers=[transfer])
    p=dict(host='hhu-server-6',job=jb,authority=write(a))
    return p,job,write

def test_reuses_actual_source_proof_without_reset_or_qualification(fixture):
    p,j,_=fixture;a=A.authority(p,j)
    assert checked(a['plan'])['next_attempt']==2
    assert checked(a['inspection'])['numerical_segmentation_validated']is False

@pytest.mark.parametrize('target,key,value',[
    ('inspection','numerical_segmentation_validated',True),('inspection','models_dispatched',1),
    ('inspection','attempts_dispatched',1),('resource','returncode',124),
    ('resource','failure_type','ConversionTimeout'),('resource','ended_at',0),
    ('plan','next_attempt',1),('plan','models',1),('plan','automatic_retry',True),
])
def test_changed_native_proof_or_consumed_attempt_rejected(fixture,target,key,value):
    p,j,write=fixture;a=checked(p['authority']);v=checked(a[target]);v[key]=value;a[target]=write(v);p['authority']=write(a)
    with pytest.raises(ValueError):A.authority(p,j)

@pytest.mark.parametrize('key,value',[('timeout_seconds',86400),('memory_limit_bytes',16*1024**3),('operational_allowance',None),('version','foreign')])
def test_worker_cannot_expand_policy_or_select_other_code(fixture,key,value):
    p,j,write=fixture;j[key]=value;p['job']=write(j);a=checked(p['authority']);a['job']=p['job'];p['authority']=write(a)
    with pytest.raises(ValueError):A.authority(p,j)

def test_retained_transfer_does_not_authorize_changed_artifact(fixture):
    p,j,_=fixture;Path(j['driver']['path']).write_text('changed historical bytes')
    with pytest.raises(ValueError):A.authority(p,j)

def test_transfer_receipt_must_cover_every_original_file(fixture):
    p,j,write=fixture;a=checked(p['authority']);t=checked(a['transfers'][0]);r=checked(t['receipt']);r['files'].pop();t['receipt']=write(r);a['transfers']=[write(t)];p['authority']=write(a)
    with pytest.raises(ValueError,match='transfer membership'):A.authority(p,j)

def initial(c):
    c.enroll('0098','a'*64,'initial ownership reconciliation required')
    p={'runtime':{},'new_owner':'hhu-server-6:slot-0:0098','expected_reason':'initial ownership reconciliation required'}
    j={'workbook':'0098','source':{'sha256':'a'*64}}
    return p,j

def test_unrelated_publication_does_not_block_initial_resource_author(tmp_path):
    c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:1000);p,j=initial(c)
    c.enroll('0029','b'*64,'source');c.reconcile('0029','stopped')
    claim=c.claim('0029','hhu-server-5:publication:0029:001','p','v',60)
    c.begin('publication','publish',**{k:claim[k]for k in('workbook','owner','token','fence')},request_sha256='c'*64)
    assert X.central_gate(c.status(),p,j)['fence']==0

def test_initial_route_never_reclaims_interrupted_attempt(tmp_path):
    c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:1000);p,j=initial(c)
    c.reconcile('0098','retained source proof');a=c.claim('0098',p['new_owner'],'once','v',60)
    c.finish(**{k:a[k]for k in('workbook','owner','token','fence')},evidence='interrupted; reconcile',disposition='hold')
    with pytest.raises(ValueError,match='never retry'):X.central_gate(c.status(),p,j)

def test_fresh_resource_claim_shares_coordinator_host_capacity(tmp_path):
    c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:1000);p,j=initial(c)
    for w in('0001','0002'):c.enroll(w,'a'*64,'source');c.reconcile(w,'stopped')
    c.set_author_capacity(2,{'path':'/auth','sha256':'a'*64},{'path':'/census','sha256':'b'*64},'two',digest(c.status()['workbooks']))
    for w in('0001','0002'):c.claim(w,f'hhu-server-6:slot-0:{w}',w,'v',60)
    assert X.central_gate(c.status(),p,j)['fence']==0
    c.reconcile('0098','retained first custody proof')
    with pytest.raises(Conflict):c.claim('0098',p['new_owner'],'once','v',60)
    assert next(w for w in c.status()['workbooks']if w['workbook']=='0098')['fence']==0

def test_runtime_extra_file_or_changed_worker_is_rejected(tmp_path):
    root=tmp_path/'runtime';worker=root/'scripts/fleet/worker.py';worker.parent.mkdir(parents=True);worker.write_text('# worker\n')
    p={'worker':binding(worker),'runtime':retain(tmp_path/'runtime.json',dict(schema_version='fleet-worker-runtime/v1',root=str(root),python='/runtime/python',files={'scripts/fleet/worker.py':binding(worker)}))};j={'command':['/runtime/python']}
    assert X.resource_runtime(p,j)['root']==str(root)
    (root/'foreign.py').write_text('unreviewed import')
    with pytest.raises(ValueError,match='closure changed'):X.resource_runtime(p,j)
    (root/'foreign.py').unlink();worker.write_text('changed worker')
    with pytest.raises(ValueError):X.resource_runtime(p,j)

def test_uncertain_operation_cannot_be_erased_by_resource_reconstruction(tmp_path):
    c=Coordinator(tmp_path/'fleet.sqlite',clock=lambda:1000);p,j=initial(c)
    c.enroll('0059','b'*64,'source');c.reconcile('0059','stopped');a=c.claim('0059','hhu-server-3:slot-0:author','old','v',60)
    auth={k:a[k]for k in('workbook','owner','token','fence')};c.begin('model','uncertain',**auth,request_sha256='c'*64);c.finish(**auth,evidence='outcome unknown',disposition='hold')
    with pytest.raises(ValueError,match='requires reconciliation'):X.central_gate(c.status(),p,j)
    assert c.status()['operations'][0]['state']=='hold'
