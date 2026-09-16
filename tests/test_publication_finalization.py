"""Real lifecycle recovery; financial proof is the existing explicit test boundary."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import pytest
from test_harbor_publish import setup
from scripts import harbor_publication_finalize as F
from scripts import harbor_publish as P
from scripts.batch import harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import record, read


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    request,intent,path,queued,journal,calls=setup(tmp_path,monkeypatch)
    real=P.qualification.validate
    def interrupt(*args, **kwargs):raise TimeoutError('interrupted after actual promotion')
    monkeypatch.setattr(P.qualification,'validate',interrupt)
    with pytest.raises(TimeoutError):P.execute(intent)
    assert read(record(path))['execution_state']=='PROMOTED'
    monkeypatch.setattr(P.qualification,'validate',real)
    monkeypatch.setattr(L.time,'time_ns',lambda:10**15)
    # The test creates no OS worker; stopped process proof is the boundary here.
    seen=[]
    def stopped(*args,**kwargs):seen.append(True);return read(intent)
    monkeypatch.setattr(F,'stopped',stopped)
    dummy=tmp_path/'selected.json';dummy.write_text('{}')
    return request,intent,path,journal,calls,record(dummy),seen


def test_finalize_does_not_republish_or_reset_consumed_history(interrupted,tmp_path):
    r,intent,path,journal,calls,b,seen=interrupted
    original=read(record(path));failed_journal={p:p.read_bytes() for p in journal.rglob('*') if p.is_file()}
    result=read(F.finalize(P,L,r,intent=intent,failure=b,original_plan=b,original_publisher=b,directory=tmp_path/'finalize'))
    assert result['status']=='PROMOTED' and len(calls)==1
    current=read(record(path));P.qualification.after_releases(original,current)
    assert all(v is None for v in current['leases'].values()) and len(seen)>=4
    assert failed_journal=={p:p.read_bytes() for p in journal.rglob('*') if p.is_file()}
    assert current['repairs']==original['repairs'] and current['lane_state']==original['lane_state']
    with pytest.raises(ValueError,match='consumed'):
        F.finalize(P,L,r,intent=intent,failure=b,original_plan=b,original_publisher=b,directory=tmp_path/'finalize')


def test_rejected_current_qualification_releases_nothing(interrupted,tmp_path,monkeypatch):
    r,intent,path,journal,calls,b,seen=interrupted
    before=path.read_bytes()
    monkeypatch.setattr(P.qualification,'validate',lambda *a,**k:dict(verified_promoted=False,revalidation_required=True))
    with pytest.raises(ValueError,match='qualification failed'):
        F.finalize(P,L,r,intent=intent,failure=b,original_plan=b,original_publisher=b,directory=tmp_path/'finalize')
    assert path.read_bytes()==before and len(calls)==1


def test_incompatible_release_reader_fails_before_expensive_proof_or_intent(interrupted,tmp_path,monkeypatch):
    from types import SimpleNamespace
    r,intent,path,journal,calls,b,seen=interrupted
    before=path.read_bytes()
    def legacy_reader(tracker):
        assert tracker==read(record(path))
        raise ValueError('retained allowance event unsupported by selected legacy reader')
    monkeypatch.setattr(P.qualification,'validate',lambda *a,**k:pytest.fail('expensive proof ran before lifecycle compatibility'))
    with pytest.raises(ValueError,match='retained allowance'):
        F.finalize(P,SimpleNamespace(validate_lifecycle=legacy_reader),r,
            intent=intent,failure=b,original_plan=b,original_publisher=b,directory=tmp_path/'finalize')
    assert path.read_bytes()==before and not(tmp_path/'finalize').exists() and len(calls)==1


def test_final_qualification_failure_is_retained_not_pass(interrupted,tmp_path,monkeypatch):
    r,intent,path,journal,calls,b,seen=interrupted
    real=P.qualification.validate;count=[]
    def check(*a,**k):
        count.append(True)
        if len(count)>1:raise ValueError('post-release changed evidence')
        return real(*a,**k)
    monkeypatch.setattr(P.qualification,'validate',check)
    with pytest.raises(ValueError,match='post-release changed evidence'):
        F.finalize(P,L,r,intent=intent,failure=b,original_plan=b,original_publisher=b,directory=tmp_path/'finalize')
    assert not (tmp_path/'finalize/result.json').exists()
    assert (tmp_path/'finalize/publication-released.json').exists() and len(calls)==1

@pytest.mark.parametrize('fault', ['none','foreign-intent','live-original','false-timeout','foreign-controller'])
def test_actual_stopped_boundary_rejects_inconsistent_or_live_evidence(tmp_path, monkeypatch, fault):
    journal=tmp_path/'old/owning-publication';journal.mkdir(parents=True)
    invocation=journal/'invocation-original';invocation.mkdir()
    def put(path,value):
        return P._keep(path,value)
    oldplan=put(tmp_path/'plan.json',{})
    publisher=put(tmp_path/'publisher.py',{})
    intent=put(journal/'intent.json',dict(schema_version=P.SCHEMA,code=P._codes()))
    controller=dict(plan=oldplan,driver=publisher,action='remote',automatic_retry=False)
    if fault=='foreign-controller':controller['plan']=publisher
    put(journal.parent/'intent.json',controller)
    log=put(invocation/'publication.log',{})
    resource=dict(intent=intent,log=log,failure={'type':'ConversionTimeout'},returncode=None,metrics={'limit_reason':'wall_time'})
    if fault=='foreign-intent':resource['intent']=oldplan
    if fault=='false-timeout':resource['returncode']=0
    r=put(invocation/'resource.json',resource)
    failed=put(invocation/'outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,result=None,resource=r))
    proc=tmp_path/'proc';proc.mkdir()
    if fault=='live-original':
        item=proc/'999999999';item.mkdir();(item/'stat').write_text('999999999 (python) S')
        (item/'cmdline').write_bytes(b'python\0'+intent['path'].encode()+b'\0')
    original_path=Path
    monkeypatch.setattr(F,'Path',lambda value:proc if str(value)=='/proc' else original_path(value))
    if fault=='none':
        assert F.stopped(P,intent,failed,original_plan=oldplan,original_publisher=publisher)==read(intent)
    else:
        with pytest.raises(ValueError):F.stopped(P,intent,failed,original_plan=oldplan,original_publisher=publisher)

@pytest.mark.parametrize('fault',[None,'wrong-exit','different-error','no-promotion','foreign-producer','models-allowed'])
def test_exact_postpromotion_type_failure_can_finalize_without_republishing(tmp_path,monkeypatch,fault):
    journal=tmp_path/'old/owning-publication';journal.mkdir(parents=True);inv=journal/'invocation-original';inv.mkdir()
    put=lambda path,value:P._keep(path,value)
    plan=put(tmp_path/'plan',{});publisher=put(tmp_path/'publisher',{})
    intent=put(journal/'intent.json',dict(schema_version=P.SCHEMA,code=P._codes()))
    put(journal.parent/'intent.json',dict(plan=plan,driver=publisher,action='remote',automatic_retry=False))
    if fault!='no-promotion':put(journal/'promoted.json',{'actual':'retained-marker'})
    log=inv/'publication.log';log.write_text('scripts.batch.harbor_admission.AdmissionError: '+('different error'if fault=='different-error'else'typed release selection required')+'\n')
    resource=put(inv/'resource.json',dict(intent=intent,log=record(log),failure=None,returncode=0 if fault=='wrong-exit'else 1,metrics={}))
    failure=put(inv/'outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,result=None,resource=resource))
    side=dict(schema_version='fleet-declared-publication-child-reader/v1',producer=plan if fault=='foreign-producer'else record(P.__file__),intent=intent,models_allowed=fault=='models-allowed',automatic_retry=False,timeout_seconds=3300,memory_limit_bytes=8589934592,reader_plan=plan,child_wrapper=publisher,adapter=publisher,guard=publisher,reader_evidence=[])
    put(inv/'declared-publication-reader.json',side)
    proc=tmp_path/'proc';proc.mkdir();original_path=Path
    monkeypatch.setattr(F,'Path',lambda value:proc if str(value)=='/proc'else original_path(value))
    before={p:p.read_bytes()for p in journal.rglob('*')if p.is_file()}
    if fault:
        with pytest.raises(ValueError):F.stopped(P,intent,failure,original_plan=plan,original_publisher=publisher)
    else:assert F.stopped(P,intent,failure,original_plan=plan,original_publisher=publisher)==read(intent)
    assert before=={p:p.read_bytes()for p in journal.rglob('*')if p.is_file()}
