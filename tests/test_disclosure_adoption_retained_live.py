"""Read the original L no-CAS receipt instead of shortening a live four-hour lease."""
from dataclasses import asdict,replace
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_round_adoption as A,harbor_instruction_worker as W,harbor_tracker_lifecycle as life
from scripts.fleet import continuation_lease as L
from scripts.batch.harbor_admission import read,record
from test_disclosure_adoption_renewal import renewed,case,instruction_setup,worker,selected,setup

@pytest.fixture
def retained(renewed,tmp_path):
    w,s,lease,old_certificate,plan,result,old,*_=renewed
    changed=life.lease_operation(old['path'],authority=life.RootAuthority('harbor-orchestrator',14400*10**9,lambda *a:True),operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+s.content.workbook,token=lease.token,duration_ns=14400*10**9,expected_revision=lease.revision,expected_sha256=lease.sha256)
    lease=replace(lease,revision=changed['revision'],sha256=changed['sha256']);tracker=record(old['path']);before=read(tracker);home=tmp_path/'retained';home.mkdir()
    parent={**read(read(old_certificate)['authority']),'retain_sufficient':True,'output':str(home),'adoption':record(A.__file__)}
    authority=W.keep(home/'authority.json',parent);prior=read(old_certificate)
    proof=W.keep(home/'validated-round.json',dict(round_plan=plan,round_result=result,tracker=tracker,historical_records=prior['historical_records'],historical_instruction=prior['historical_instruction'],models_dispatched=0))
    archive_before=W.snapshot(home/'before.json',tracker)
    current,complete=L.renew(life=life,read=read,record=record,keep=W.keep,path=old['path'],lease=lease,authority_record=authority,validation=lambda b,l:[plan,result,proof],custody=lambda:None,directory=home/'lease',retain_sufficient=True)
    archive_after=W.snapshot(home/'after.json',tracker)
    assert current==lease and read(tracker)==before and not(home/'lease/intent.json').exists()
    certificate=W.keep(home/'certificate.json',dict(schema_version=A.RETAINED,implementation=record(A.__file__),authority=authority,round_plan=plan,round_result=result,module=record(L.__file__),completion=complete,before=archive_before,after=archive_after,historical_records=prior['historical_records'],historical_instruction=prior['historical_instruction']))
    return w,s,lease,certificate,plan,result,tracker


def test_actual_no_cas_original_retained_receipt(retained):
    w,s,l,c,p,r,t=retained
    value=A.validate_renewal(c,w,l,p,r,live=True)
    assert value['schema_version']==A.RETAINED and read(value['completion'])['lease_operations']==0
    assert read(t)['leases']['task_agent']['owner']==l.owner


@pytest.mark.parametrize('fault',['before','after','authority','completion','proof','current'])
def test_retained_custody_refuses_missing_or_changed_evidence(retained,fault):
    w,s,l,c,p,r,t=retained;value=read(c)
    target=value[fault]if fault in ('before','after','authority','completion')else t if fault=='current'else record(Path(read(value['authority'])['output'])/'validated-round.json')
    Path(target['path']).write_text('{}')
    with pytest.raises((ValueError,KeyError)):A.validate_renewal(c,w,l,p,r,live=True)


def test_retained_certificate_is_historical_after_real_later_cas(retained):
    w,s,l,c,p,r,t=retained
    life.lease_operation(t['path'],authority=life.RootAuthority('harbor-orchestrator',14400*10**9,lambda *a:True),operation='renew',lease_kind='task_agent',owner=l.owner,scope='workbook:'+s.content.workbook,token=l.token,duration_ns=14400*10**9,expected_revision=l.revision,expected_sha256=l.sha256)
    assert A.validate_renewal(c,w,l,p,r,live=False)
    with pytest.raises(ValueError):A.validate_renewal(c,w,l,p,r,live=True)
