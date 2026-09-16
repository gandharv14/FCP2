from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import pytest
from scripts.fleet import disclosure_adoption_continue as S,continuation_lease as L
from scripts.batch import harbor_tracker_lifecycle as life,harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup

@pytest.fixture(params=[False,True])
def completed(failed,tmp_path,request):
 worker,selection,lease,blocked,task,*_=failed
 home=tmp_path/'review';home.mkdir();row=W.keep(tmp_path/'plan.json',dict(real='selected review authority'))
 if request.param:
  change=life.lease_operation(task.tracker['path'],authority=life.RootAuthority('harbor-orchestrator',14400*10**9,lambda *a:True),operation='renew',lease_kind='task_agent',owner=lease.owner,scope='workbook:'+selection.content.workbook,token=lease.token,duration_ns=14400*10**9,expected_revision=lease.revision,expected_sha256=lease.sha256)
  lease=replace(lease,revision=change['revision'],sha256=change['sha256'])
 original=read(record(task.tracker['path']));(tmp_path/'before.json').write_bytes(Path(task.tracker['path']).read_bytes());before=record(tmp_path/'before.json')
 current,done=L.renew(life=life,read=read,record=record,keep=W.keep,path=task.tracker['path'],lease=lease,authority_record=row,validation=lambda *a:[row],custody=lambda:None,directory=home/'initial-lease',retain_sufficient=request.param)
 (tmp_path/'after.json').write_bytes(Path(task.tracker['path']).read_bytes());after=record(tmp_path/'after.json')
 inputs=W.keep(tmp_path/'inputs.json',dict(workbook=selection.content.workbook))
 authority=W.keep(home/'authority.json',dict(build={},inputs=read(inputs),round=1,limit=2,created_at_ns=read(after)['updated_at']))
 review=W.keep(home/'review-plan.json',dict(review_round=authority))
 W.keep(home/'reserved.json',dict(plan=review,automatic_retry=False))
 result=W.keep(home/'result.json',dict(status='DISCLOSURE_REVIEW_COMPLETE_NOT_ADOPTED',plan=row,workbook=selection.content.workbook,automatic_retry=False,publication_performed=False,review_plan=review))
 helper=record(L.__file__);selected=dict(output=str(home),renewal_module=helper,retained=dict(plan=W.keep(tmp_path/'retained.json',{})),build={},inputs=inputs,round=1,limit=2)
 p=dict(initial_review_lease=dict(module=helper,completion=done,before=before,after=after),round_result=result,context=dict(plan=row),workbook=selection.content.workbook,source=original['bindings']['original_raw_source'])
 return p,selected,worker,lease,SimpleNamespace(life=life),current

def test_actual_original_renewal_yields_historical_lease_without_cas(completed):
 p,s,w,l,Q,current=completed;path=w.context.tracker_root/(p['workbook']+'.json');before=path.read_bytes()
 assert S.initial_review_lease(p,s,w,l,Q)==current
 assert path.read_bytes()==before

@pytest.mark.parametrize('defect',['source','authority','before','module','reserved','foreign-custody'])
def test_initial_review_lease_rejects_detached_history(completed,defect):
 p,s,w,l,Q,current=completed
 if defect=='source':p['source']={}
 elif defect=='authority':p['context']['plan']={}
 elif defect=='before':p['initial_review_lease']['before']={**p['initial_review_lease']['after'],'sha256':'0'*64}
 elif defect=='module':p['initial_review_lease']['module']={}
 elif defect=='reserved':s['round']=2
 else:l=replace(l,owner='foreign')
 with pytest.raises((ValueError,KeyError,AssertionError)):S.initial_review_lease(p,s,w,l,Q)
