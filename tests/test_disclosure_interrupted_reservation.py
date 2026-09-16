from pathlib import Path
from dataclasses import dataclass,asdict
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import disclosure_reserved_round as S
from scripts.batch import harbor_disclosure_partition_review as R
from scripts.batch.harbor_admission import record,read


def keep(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)

@pytest.fixture
def case(tmp_path,monkeypatch):
    h=tmp_path/'old';wdir=tmp_path/'worker';run=tmp_path/'instruction';tracker=keep(tmp_path/'trackers/0001.json',{'revision':7})
    @dataclass
    class Lease:
        token:str='unchanged';revision:int=7
    lease=Lease();source=keep(tmp_path/'source',{'unchanged':True});projection=keep(tmp_path/'projection',{'batches':[{},{}]})
    inputs=keep(tmp_path/'inputs',{'projection':projection,'workbook':'0001','original_source':source})
    old=dict(helper=keep(tmp_path/'runner',{}),owner='old',fence=2,output=str(h),modules={'rounds':keep(tmp_path/'rounds',{})},
        allowance=keep(tmp_path/'allowance',{}),build={},prior_rejections=[],round=1,limit=1,inputs=inputs,source=source,workbook='0001')
    plan=keep(tmp_path/'old-plan',old);job=keep(tmp_path/'job',dict(driver=old['helper'],source=source,workbook='0001',result=str(h/'result.json'),command=['python','run','--plan',plan['path'],'--sha256',plan['sha256']]))
    intent=keep(wdir/'intent.json',dict(owner='old',job=job));(wdir/'worker.log').write_bytes(b'')
    outer=keep(wdir/'outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=None,
        failure_type='RuntimeError',result=None,automatic_retry=False,publication_performed=False,intent=intent,log=record(wdir/'worker.log')))
    keep(wdir/'finish-uncertain.json',dict(automatic_retry=False,failure_type='RuntimeError',outcome=outer,reconciliation_required=True))
    process=keep(tmp_path/'process',dict(pid=98765,automatic_retry=False,job=job,command=['python','--job',job['path'],'--job-sha256',job['sha256'],'--owner','old']))
    retained=keep(h/'initial-lease/retained-live.json',dict(schema_version='fleet-retained-live-continuation-lease/v1',authority=plan,lease=asdict(lease),tracker=tracker,
        lease_operations=0,automatic_retry=False,semantic_budget_reset=False,validated_evidence=[source]))
    keep(h/'intent.json',dict(plan=plan,automatic_retry=False))
    authority_value=dict(implementation=old['modules']['rounds'],allowance=old['allowance'],build={},prior_rejections=[],round=1,limit=1)
    authority=keep(run/'disclosure-review-rounds/0001/authority.json',authority_value);keep(Path(authority['path']).parent/'prefix.json',{})
    review=keep(Path(authority['path']).parent/'review-plan.json',dict(read(inputs),review_round=authority));keep(h/'reserved.json',dict(plan=review,automatic_retry=False))
    proof=keep(tmp_path/'proof',dict(schema_version='fleet-disclosure-interrupted-reservation/v1',plan=plan,worker_outcome=outer,process=process,authority=authority,retained_lease=retained))
    p={**old,'owner':'new','fence':3,'output':str(tmp_path/'new'),'reserved_round':proof,'round_reader':dict(runtime=record(S.__file__),profile=None)}
    calls=[]
    def validate(a,i):
        assert a==authority and i==read(inputs);calls.append('original authority');return authority_value
    Q=SimpleNamespace(validate=validate,life=SimpleNamespace(checked_record=lambda b:read(b)),semantic_scope=lambda x:x,
        _primary_history=lambda *a:calls.append('original executor history'))
    worker=SimpleNamespace(context=SimpleNamespace(tracker_root=tmp_path/'trackers'),campaign=SimpleNamespace(root=tmp_path/'campaign'))
    monkeypatch.setattr(S,'stopped_process',lambda *a:calls.append('native process absence'))
    monkeypatch.setattr(R,'scope',lambda p,i:SimpleNamespace(index=i))
    # The real executor canonical directory formula remains owning below.
    monkeypatch.setattr('dataclasses.asdict',lambda x:dict(index=x.index) if hasattr(x,'index') else x.__dict__)
    return proof,p,worker,lease,Q,calls,h,wdir,review


def test_existing_reservation_reused_without_another_reserve(case):
    proof,p,w,l,Q,calls,h,wd,review=case
    before={x: x.read_bytes()for x in h.parent.rglob('*')if x.is_file()}
    assert S.interrupted_reservation(proof,p,w,None,l,Q)==(Path(review['path']),read(review))
    assert calls==['native process absence','original authority','original executor history']
    assert all(x.read_bytes()==b for x,b in before.items())

@pytest.mark.parametrize('effect',['batches','validation-metrics.json','result.json'])
def test_later_review_effect_cannot_restart(case,effect):
    proof,p,w,l,Q,calls,h,*_=case;keep(h/effect,{})
    with pytest.raises(ValueError,match='first-request'):S.interrupted_reservation(proof,p,w,None,l,Q)

@pytest.mark.parametrize('key,value',[('round',2),('limit',2),('allowance',{}),('modules',{}),('inputs',{}),('source',{}),('fence',4)])
def test_review_authority_changes_rejected(case,key,value):
    proof,p,w,l,Q,*_=case;p[key]=value
    with pytest.raises(ValueError):S.interrupted_reservation(proof,p,w,None,l,Q)


def test_existing_shard_executor_even_without_local_request_blocks(case):
    proof,p,w,l,Q,calls,*_=case
    root=Path(w.campaign.root)/R.E.sha(R.E.canonical({'index':1}));root.mkdir(parents=True)
    keep(root/'intent.json',{'uncertain':True})
    with pytest.raises(ValueError,match='shard executor'):S.interrupted_reservation(proof,p,w,None,l,Q)


def test_original_validator_failure_is_not_waived(case):
    proof,p,w,l,Q,*_=case
    def fail(*a):raise ValueError('original reservation invalid')
    Q.validate=fail
    with pytest.raises(ValueError,match='original reservation invalid'):S.interrupted_reservation(proof,p,w,None,l,Q)

@pytest.mark.parametrize('alive',['supervisor','child','reused_pid'])
def test_native_process_presence_rejects(tmp_path,alive):
    proc=tmp_path/'proc';proc.mkdir();worker=tmp_path/'worker';plan={'path':str(tmp_path/'old-plan')};pid=33
    actual=pid if alive=='reused_pid' else 44;d=proc/str(actual);d.mkdir()
    args=['python',str(worker)]if alive=='supervisor'else ['python','--plan',plan['path']]if alive=='child'else ['unrelated']
    (d/'cmdline').write_bytes(b'\0'.join(x.encode()for x in args)+b'\0')
    with pytest.raises(ValueError):S.stopped_process({'pid':pid},worker,plan,proc=proc)


def test_absent_native_process_allows_exact_stopped_identity(tmp_path):
    proc=tmp_path/'proc';proc.mkdir()
    S.stopped_process({'pid':33},tmp_path/'worker',{'path':'/original-plan'},proc=proc)
