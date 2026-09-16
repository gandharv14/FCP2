from pathlib import Path
import pytest
from scripts.fleet import first_rejected_review_authority as A
from scripts.fleet.migration import retain,binding,checked

@pytest.fixture
def selection(tmp_path):
    count=[0]
    def put(v):
        count[0]+=1;return retain(tmp_path/(str(count[0])+'.json'),v)
    source=put({'original':True});root=tmp_path/'code'/'version';root.mkdir(parents=True)
    names=['scripts/fleet/dialogue_revision_review.py','scripts/fleet/retained_rejected_review_context.py','scripts/batch/harbor_model_executor.py']
    files={}
    for n in names:
        p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# frozen\n');files[n]=binding(p)
    rejection=put(dict(schema_version='harbor-completed-review-consumption-proof/v1',round=2,passed=False,publication_ready=False,model_calls_executed=0,call={'request':{'path':str(tmp_path/'old/model-campaign/scope/attempt-02/request.json')}}))
    rev=put({'original_evidence':True});snap=put({'historical':True})
    reader=put(dict(rejection=rejection,revision=rev,snapshot=snap,source=source))
    allowance=put({'bounded':True})
    plan=put(dict(schema_version='fleet-rejected-reader-review/v1',helper=files[names[0]],context_helper=files[names[1]],executor=files[names[2]],reader_selection=reader,source=source,workbook='0061',owner='hhu-server-5:review',fence=1,python='/python',home=str(tmp_path/'new'),allowance=allowance))
    j=dict(workbook='0061',source=source,driver=files[names[0]],inputs=[plan],cwd=str(root),version='version',operational_allowance=allowance,memory_limit_bytes=8*1024**3,timeout_seconds=3300,minimum_disk_bytes=12*1024**3,result=str(tmp_path/'new/result.json'))
    j['command']=['/python','-I','-B',j['driver']['path'],'run','--plan',plan['path'],'--sha256',plan['sha256']]
    probe=put(dict(status='SOURCE_DERIVATIVE_REVIEW_VALIDATED_NOT_STARTED',plan=plan,original_consumed=2,next_ordinal=3,limit=3,claims=192,changed_claims=1,unchanged_claims=191,mechanics={'passed':True},models_dispatched=0,task_mutations=0,qualified=False))
    guard=put(dict(command=j['command'][:4]+['probe']+j['command'][5:],returncode=0,inspection=probe,host='hhu-server-5',models=0,claims=0,memory_limit_bytes=8*1024**3,timeout_seconds=1500))
    review_runtime=put(dict(schema_version='fleet-worker-runtime/v1',root=str(root),files=files));required=[plan,reader,rejection,rev,snap,review_runtime];closure=put(dict(files=required,historical_bytes_preserved=True))
    manifest=put(dict(files=required,archive_sha256='archive'));receipt=put(dict(files=[dict(original_path=x['path'],sha256=x['sha256'])for x in required],archive_sha256='archive',absolute_paths_preserved=True,historical_bytes_rewritten=False,attempts_reset=False))
    transfer=put(dict(host='hhu-server-5',claims=0,manifest=manifest,receipt=receipt));jb=put(j)
    a=dict(schema_version=A.SCHEMA,kind='rejected-reader-review',workbook='0061',job=jb,source=source,plan=plan,inspection=probe,resource=guard,closure=closure,transfers=[transfer],original_home=str(tmp_path/'old'),offline_bindings=None,review_runtime=review_runtime)
    p=dict(host='hhu-server-5',job=jb,authority=put(a),new_owner='hhu-server-5:review',runtime=put(dict(root=str(root),files=files)))
    return p,j,put


def test_exact_retained_rejection_can_enter_one_additional_review(selection):
    p,j,_=selection
    assert A.authority(p,j)['kind']=='rejected-reader-review'


@pytest.mark.parametrize('target,key,value',[
 ('inspection','original_consumed',0),('inspection','next_ordinal',1),('inspection','limit',4),
 ('inspection','models_dispatched',1),('inspection','task_mutations',1),('inspection','qualified',True),
 ('inspection','unchanged_claims',190),('resource','returncode',124),('resource','host','hhu-server-2'),
 ('resource','timeout_seconds',86400),('plan','fence',2),('plan','owner','foreign'),
])
def test_unproven_or_replayed_authority_rejected(selection,target,key,value):
    p,j,put=selection;a=checked(p['authority']);obj=checked(a[target]);obj[key]=value;a[target]=put(obj);p['authority']=put(a)
    with pytest.raises(ValueError):A.authority(p,j)


def test_missing_transfer_file_rejected(selection):
    p,j,put=selection;a=checked(p['authority']);t=checked(a['transfers'][0]);r=checked(t['receipt']);r['files'].pop();t['receipt']=put(r);a['transfers']=[put(t)];p['authority']=put(a)
    with pytest.raises(ValueError,match='incomplete custody'):A.authority(p,j)


def test_changed_original_bytes_rejected(selection):
    p,j,_=selection;a=checked(p['authority']);reader=checked(checked(a['plan'])['reader_selection']);Path(reader['revision']['path']).write_text('{}')
    with pytest.raises(ValueError,match='exact authenticated input'):A.authority(p,j)


def test_native_probe_copies_retain_exact_original_bindings(selection,tmp_path):
    p,j,put=selection;a=checked(p['authority']);copies=[]
    for key in ('inspection','resource'):
        old=a[key];native=dict(path='/home/gmahajan_labelbox_com/synthetic-data-fleet/probe/'+key+'.json',sha256=old['sha256']);copies.append(dict(native=native,copy=old));a[key]=native
    # The original native guard points to the original native inspection.
    guard=checked(copies[1]['copy']);guard['inspection']=a['inspection'];gb=put(guard);copies[1]['copy']=gb;copies[1]['native']['sha256']=gb['sha256'];a['resource']=copies[1]['native']
    a['offline_bindings']=put(dict(schema_version='fleet-offline-bindings/v1',copies=copies));p['authority']=put(a)
    assert A.authority(p,j)['inspection']==copies[0]['native']
    Path(copies[0]['copy']['path']).write_text('{}')
    with pytest.raises(ValueError,match='offline'):A.authority(p,j)
