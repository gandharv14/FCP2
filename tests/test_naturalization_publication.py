from pathlib import Path
from types import SimpleNamespace
import copy
import pytest
from scripts.fleet import fresh_publication as F

@pytest.fixture
def completed():
    def b(p):return dict(path=str(p),sha256=str(p))
    def require(ok,message):
        if not ok:raise ValueError(message)
    row=b('/continuation');reader=b('/reader');freshrow=b('/fresh');home=Path('/run/continuations/next')
    original=dict(driver=b('/code/dispatcher.py'),config=b('/config'),outcome=b('/admission'),operations='/run/dispatcher-v2/first')
    p=dict(schema_version='fleet-naturalization-continuation/v1',entry=reader,workbook='0498',source=dict(sha256='source'),original_job=b('/original-job'),output=str(home))
    fresh=dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0498',code_root='/code',dispatcher=original['driver'],home='/fresh-home')
    plan=dict(naturalization_continuation=dict(reader=reader,plan=row),fresh_authoring=freshrow,workbook='0498',source_sha256='source',code_root='/code',author_helper=dict(path='/author.py',sha256='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666'))
    job=dict(original,outcome=b(home/'import/outcome.json'),operations='/run/dispatcher-v2/next')
    ready=dict(status='PUBLICATION_READY',workbook='0498',continuation=row,publication_performed=False,automatic_retry=False,config=original['config'],job=b(home/'authoring-job.json'),outcome=b('/ready-outcome'),checkpoint=b('/ready-checkpoint'),permit_guard=b(home/'permit-resource.json'))
    guard=dict(returncode=0,failure=None,models=0,seconds=3300,memory_bytes=8589934592,intent=b(home/'intent.json'),log=b(home/'permit.log'))
    permit=dict(plan=row,models=0,semantic_budget_reset=False,automatic_retry=False,completion=b('/permit-completion'))
    env=(None,None,None,None,None,dict(run_root='/run'),Path('/code'),None)
    D=SimpleNamespace(successful=lambda r,e:(ready['checkpoint'],dict(status='PUBLICATION_READY')))
    M=SimpleNamespace(load=lambda r:(p,original,D,env,None,None,None))
    values={'/continuation':p,'/fresh':fresh,'/fresh-home/authoring-job.json':original,str(home/'permit-resource.json'):guard,str(home/'intent.json'):dict(plan=row,entry=reader,automatic_retry=False),str(home/'permit-complete.json'):permit}
    pub=SimpleNamespace(checked=lambda r:values[r['path']],binding=b,require=require,load=lambda *args:M)
    pinned=SimpleNamespace(load=lambda *args,**kwargs:None)
    return pub,pinned,plan,ready,job,p,guard,permit,values,D

def test_completed_nat_keeps_fresh_history_and_original_publication_dispatcher(completed):
    pub,pinned,plan,ready,job,*_=completed
    assert F.dispatcher(pub,pinned,plan,ready,job) is completed[-1]
    assert F.history_prefix(plan)=='HARBOR_FLEET_HISTORY_'

@pytest.mark.parametrize('defect',['source','fresh-source','job','ready','guard','budget-reset','permit','final-checkpoint'])
def test_nat_publication_rejects_changed_custody_budget_or_completion(completed,defect):
    pub,pinned,plan,ready,job,p,guard,permit,values,D=completed
    if defect=='source':plan['source_sha256']='foreign'
    elif defect=='fresh-source':values['/fresh']['workbook']='other'
    elif defect=='job':job['outcome']={}
    elif defect=='ready':ready['status']='RECONCILIATION_REQUIRED'
    elif defect=='guard':guard['returncode']=1
    elif defect=='budget-reset':permit['semantic_budget_reset']=True
    elif defect=='permit':permit['plan']={}
    else:D.successful=lambda *args:(None,dict(status='STEP_COMPLETE'))
    with pytest.raises(ValueError):F.dispatcher(pub,pinned,plan,ready,job)

def test_settled_import_uses_original_guard_and_new_remaining_namespace(completed):
    pub,pinned,plan,ready,job,p,guard,permit,values,D=completed
    b=pub.binding;prior=copy.deepcopy(p);prior['output']='/run/continuations/prior';prior['entry']=b('/prior-reader')
    p['resume']=b('/resume');values['/prior']=prior
    start=b('/run/continuations/prior/import/outcome.json');values['/resume']=dict(prior_plan=b('/prior'),import_outcome=start)
    values['/run/continuations/prior/permit-resource.json']=guard
    values['/run/continuations/prior/permit-complete.json']=permit
    guard['intent']=b('/prior-intent');values['/prior-intent']=dict(plan=b('/prior'),entry=prior['entry'],automatic_retry=False)
    permit['plan']=b('/prior');ready['permit_guard']=b('/run/continuations/prior/permit-resource.json');job['outcome']=start
    assert F.dispatcher(pub,pinned,plan,ready,job) is D
    assert job['operations']=='/run/dispatcher-v2/next'
