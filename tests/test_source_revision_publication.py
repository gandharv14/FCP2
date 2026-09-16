from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_publication as F


def fixture():
    def b(name):return dict(path='/'+name,sha256=name)
    a=dict(reader=b('reader'),plan=b('plan'),outcome=b('ops/000/outcome.json'))
    job=dict(config=b('config'),driver=b('driver'))
    p=dict(schema_version='derivative/v1',helper=a['reader'],workbook='0245',source=dict(sha256='source'),authoring_job=b('job'),operations='/ops',continuation=dict(completed_outcomes=[{},{}]))
    actual=dict(status='COMPLETED_OPERATION',result=b('checkpoint'))
    ready=dict(job=b('job'),config=job['config'],outcome=a['outcome'],checkpoint=actual['result'],plan=a['plan'],source_revision_outcome=a['outcome'])
    plan=dict(source_revision=a,workbook='0245',source_sha256='source',code_root='/code',author_helper=dict(path='/author',sha256='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666'))
    calls=[];env=(None,None,None,None,None,None,Path('/code'),None)
    D=SimpleNamespace(setup=lambda j:env,successful=lambda row,e:(b('checkpoint'),dict(status='PUBLICATION_READY')))
    M=SimpleNamespace(context=lambda row,**kw:calls.append((row,kw))or(p,None,dict(round=4),[],env,D,None,None,None,None,None))
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(checked=lambda row:p if row==a['plan']else actual,require=require,binding=lambda path:b(str(path)),load=lambda *a:M)
    pin=SimpleNamespace(load=lambda *a,**kw:calls.append('pinned-before-context'))
    return pub,pin,plan,ready,job,p,actual,D,calls


def test_actual_completion_reader_preserves_selected_cumulative_count():
    pub,pin,plan,ready,job,p,a,D,calls=fixture()
    assert F.dispatcher(pub,pin,plan,ready,job)is D
    assert calls[0]=='pinned-before-context' and calls[1][1]['probe']is True
    assert calls[1][1]['owning_intent']['path'].endswith('/ops/000/intent.json')


@pytest.mark.parametrize('kind',['unready','source','helper','job','checkpoint','outcome','record','count','original-ready'])
def test_foreign_or_unfinished_derivative_is_rejected(kind):
    pub,pin,plan,ready,job,p,a,D,calls=fixture()
    if kind=='unready':a['status']='RECONCILIATION_REQUIRED'
    elif kind=='source':plan['source_sha256']='foreign'
    elif kind=='helper':p['helper']={}
    elif kind=='job':ready['job']={}
    elif kind=='checkpoint':ready['checkpoint']={}
    elif kind=='outcome':ready['outcome']={}
    elif kind=='record':ready['source_revision_outcome']={}
    elif kind=='count':p['continuation']['completed_outcomes'].append({})
    else:D.successful=lambda *args:(ready['checkpoint'],dict(status='STEP_COMPLETE'))
    with pytest.raises(ValueError):F.dispatcher(pub,pin,plan,ready,job)

@pytest.mark.parametrize('fault',[None,'unfinished','wrong_plan','extra_guard','model','replay','wrong_result','source'])
def test_original_derivative_ready_projection_cannot_invent_completion(fault):
    pub,pin,plan,ready,job,p,a,D,calls=fixture()
    plan['source_revision']['completed_reader']={'path':'/completed','sha256':'completed'}
    plan['ready']={'path':'/derivative/result.json','sha256':'actual'}
    p.update(schema_version='fleet-dialogue-source-revision-adoption/v1')
    p['continuation'].update(output='/derivative',plan={'path':'/authority','sha256':'authority'})
    actual=dict(schema_version=p['schema_version'],status='PUBLICATION_READY',plan=plan['source_revision']['plan'],operations=[plan['source_revision']['outcome']],checkpoint=ready['checkpoint'],models_dispatched=0,publication_performed=False,automatic_retry=False,retained_adoption=p['continuation']['plan'])
    if fault=='unfinished':actual['status']='RECONCILIATION_REQUIRED'
    elif fault=='wrong_plan':actual['plan']={}
    elif fault=='extra_guard':actual['operations'].append({})
    elif fault=='model':actual['models_dispatched']=1
    elif fault=='replay':actual['automatic_retry']=True
    elif fault=='wrong_result':a['result']={}
    elif fault=='source':p['source']['sha256']='different'
    def checked(row):
        if row==plan['ready']:return actual
        if row==plan['source_revision']['plan']:return p
        if row==p['authoring_job']:return job
        return a
    pub.checked=checked
    if fault:
        with pytest.raises(ValueError):
            F.derivative_history_binding(pub,plan);F.ready_value(pub,plan)
    else:
        F.derivative_history_binding(pub,plan)
        seen=F.ready_value(pub,plan)
        assert seen['job']==p['authoring_job'] and seen['outcome']==plan['source_revision']['outcome']
        assert 'job'not in actual and actual['checkpoint']==seen['checkpoint']

@pytest.mark.parametrize('fault',[None,'missing_phase','first_is_ready','reordered'])
def test_three_phase_ready_projects_actual_last_guard_only(fault):
    pub,pin,plan,ready,job,p,guard,D,calls=fixture()
    p['schema_version']='fleet-dialogue-source-revision-adoption/v1';p['continuation']={'plan':{'path':'/authority','sha256':'a'},'output':'/tail'}
    pub.binding=lambda path:dict(path=str(path),sha256=str(path))
    ops=[pub.binding(Path('/ops')/('%03d'%i)/'outcome.json')for i in range(3)]
    plan['source_revision'].update(completed_reader={'path':'/completed','sha256':'c'},outcome=ops[-1]);plan['ready']={'path':'/tail/result.json','sha256':'r'}
    actual=dict(schema_version=p['schema_version'],status='PUBLICATION_READY',plan=plan['source_revision']['plan'],operations=ops[:],checkpoint=ready['checkpoint'],models_dispatched=0,publication_performed=False,automatic_retry=False,retained_adoption=p['continuation']['plan'])
    if fault=='missing_phase':actual['operations'].pop(0)
    elif fault=='first_is_ready':plan['source_revision']['outcome']=ops[0]
    elif fault=='reordered':actual['operations'].reverse()
    pub.checked=lambda row:actual if row==plan['ready']else p if row==plan['source_revision']['plan']else job if row==p['authoring_job']else guard
    if fault:
        with pytest.raises(ValueError):F.ready_value(pub,plan)
    else:
        seen=F.ready_value(pub,plan)
        assert seen['outcome']==ops[-1] and seen['operations']==ops
        assert 'outcome'not in actual and 'job'not in actual
