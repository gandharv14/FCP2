import pytest
from scripts.fleet import retained_warning_publication as P
from test_direct_warning_publication import fixture

def criterion():
 plan,values,kw=fixture();add=lambda path,value:(values.__setitem__(path,value) or dict(path=path,sha256='a'*64))
 helper=add('/enqueue.py',{})
 prior=add('/criterion.json',dict(workbook='0303',version='version'))
 enqueue=add('/enqueue.json',dict(schema_version='fleet-criterion-enqueue-tail/v2',entry=helper,workbook='0303',source={'sha256':'s'},version='version',prior_plan=prior,output='/work',operations='/ops'))
 ready=values['/work/result.json'];ready.update(schema_version='fleet-criterion-enqueue-tail/v1',PUBLICATION_READY=True,plan=enqueue,outcome=add('/ops/000/outcome.json',{}),checkpoint=add('/ops/000/result.json',{}))
 values['/job.json'].update(inputs=[enqueue],driver=helper)
 plan.pop('retained_warning');plan['criterion_history']=dict(enqueue_plan=enqueue,enqueue_helper=helper,root_launch=dict(path='/launched.json',sha256='a'*64),job=dict(path='/job.json',sha256='a'*64))
 return plan,values,kw

def test_actual_criterion_enqueue_history_requires_completed_root_launch():
 p,v,kw=criterion();assert P.history_binding(p,**kw)==p['criterion_history']['enqueue_plan']
 assert P.evidence(p)==[*p['criterion_history'].values(),p['ready']]

@pytest.mark.parametrize('field',['workbook','source','status','calls','namespace','launch','job','unknown'])
def test_criterion_publication_rejects_changed_history(field):
 p,v,kw=criterion()
 if field=='workbook':v['/enqueue.json']['workbook']='0000'
 elif field=='source':v['/enqueue.json']['source']={'sha256':'foreign'}
 elif field=='status':v['/work/result.json']['status']='STEP_COMPLETE'
 elif field=='calls':v['/work/result.json']['models_dispatched']=1
 elif field=='namespace':v['/enqueue.json']['operations']='/other'
 elif field=='launch':v['/launched.json']['remote']['job']={}
 elif field=='job':v['/job.json']['result']='/other'
 else:p['criterion_history']['unselected']={}
 with pytest.raises((ValueError,AssertionError)):P.history_binding(p,**kw)
