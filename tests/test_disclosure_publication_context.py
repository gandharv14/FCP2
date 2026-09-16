from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

from scripts.fleet import disclosure_publication_context as M,fresh_publication as F
from scripts.fleet.publication_launch import publication_version


def fixture():
    def b(p):return dict(path=str(p),sha256=str(p))
    a=dict(reader=b('/reader'),plan=b('/plan'),completed_reader=b('/completed'))
    p=dict(schema_version='fleet-disclosure-command-continuation/v1',helper=a['reader'],
        source_revision=True,completed_takeover={},workbook='0102',source=b('/source'),
        utilities=b('/utilities'),original_job=b('/job'),profile=b('/profile'))
    p['context']=dict(helper=p['utilities'],plan=b('/context'),function='load',kwargs={},
        projection=dict(dispatcher=2,environment=3),manager=None)
    plan=dict(disclosure_continuation=a,fresh_authoring=b('/fresh'),workbook='0102',
        source_sha256='/source',code_root='/code/old')
    rows={'/plan':p,'/context':dict(workbook='0102',fresh_plan=plan['fresh_authoring'],original_job=p['original_job']),
        '/fresh':dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0102',code_root='/code/old'),
        '/profile':dict(snapshot=b('/snapshot'),old_snapshot=b('/old')),
        '/snapshot':dict(root='/code/new'),'/old':dict(root='/code/old')}
    def require(ok,message):
        if not ok:raise ValueError(message)
    pub=SimpleNamespace(require=require,binding=b,checked=lambda row:rows[row['path']])
    return pub,plan,p,rows


def test_history_and_current_version_preserve_original_source_root(monkeypatch):
    pub,plan,p,rows=fixture()
    assert M.history(pub,plan)==plan['disclosure_continuation']['plan']
    from scripts.fleet import publication_launch as L
    monkeypatch.setattr(L,'module',lambda name,row:M if row==plan['disclosure_continuation']['completed_reader']else None)
    assert publication_version(plan,pub.checked)=='new'
    assert plan['code_root']=='/code/old'


@pytest.mark.parametrize('change',['source','workbook','context','mixed','non-source','helper','unfinished-variant'])
def test_closed_history_rejects_foreign_or_mixed_authority(change):
    pub,plan,p,rows=fixture()
    if change=='source':plan['source_sha256']='foreign'
    elif change=='workbook':p['workbook']='0095'
    elif change=='context':p['context']['function']='run'
    elif change=='mixed':plan['history']={}
    elif change=='non-source':p['source_revision']=False
    elif change=='helper':p['helper']={}
    else:p['stop_after_disclosure_build']=True
    with pytest.raises(ValueError):M.history(pub,plan)


@pytest.mark.parametrize('change',[None,'failed','model','limits','command','intent','log'])
def test_exact_original_permit_guard_required(change):
    pub,plan,p,rows=fixture();a=plan['disclosure_continuation'];home=Path('/output');b=pub.binding
    value=dict(intent=b('/intent'),returncode=0,failure=None,models_dispatched=0,seconds=3300,
        memory_bytes=8589934592,log=b(home/'permit.log'),command=[sys.executable,'-I','-B',a['reader']['path'],
            'permit-child','--plan',a['plan']['path'],'--sha256',a['plan']['sha256']])
    rows[str(home/'permit-resource.json')]=value
    rows['/intent']=dict(plan=a['plan'],helper=a['reader'],automatic_retry=False)
    ready=dict(permit_guard=b(home/'permit-resource.json'))
    if change=='failed':value['returncode']=1
    elif change=='model':value['models_dispatched']=1
    elif change=='limits':value['seconds']=9999
    elif change=='command':value['command'][4]='run'
    elif change=='intent':rows['/intent']['automatic_retry']=True
    elif change=='log':value['log']=b('/other')
    if change is None:assert M.guard(pub,a,p,ready,home)==ready['permit_guard']
    else:
        with pytest.raises(ValueError):M.guard(pub,a,p,ready,home)


def test_routing_and_reader_evidence_share_completed_reader():
    pub,plan,p,rows=fixture();sentinel=object();calls=[]
    pub.load=lambda name,row:SimpleNamespace(history=lambda pub,p:plan['disclosure_continuation']['plan'],
        dispatcher=lambda *args:sentinel,dependencies=lambda *args:calls.append('dependencies')or[pub.binding('/evidence')])
    pub.FRESH_DEPENDENCIES={};plan['author_helper']=pub.binding('/author')
    assert F.dispatcher(pub,None,plan,{}, {})is sentinel
    assert F.selected_history_survey(plan)is None
    F.history_survey(pub,plan,plan['disclosure_continuation']['plan'],None)
    assert pub.binding('/evidence')in F.reader_evidence(pub,plan)
    assert calls==['dependencies']


@pytest.mark.parametrize('fault',[None,'source','workbook','job','archive','root','schema','helper-context','unused','false-kind'])
def test_completed_ordinary_environment_history_is_exact(fault):
    from scripts.fleet import environment_checkpoint_continue as E
    pub,plan,p,rows=fixture();b=pub.binding
    p.pop('source_revision');p.pop('completed_takeover')
    p['context']=dict(helper=b('/environment-reader'),plan=b('/environment-plan'),function='completed_context',kwargs={'probe':True},projection=dict(dispatcher=1,environment=2),manager=None)
    plan['fresh_authoring']=p['context']['plan'];p['tracker']=dict(path='/tracker',sha256='current')
    e={k:b('/original-'+k)for k in E.FIELDS};e.update(schema_version=E.SCHEMA,workbook=p['workbook'],original_job=p['original_job'],source=p['source']);rows['/environment-plan']=e
    rows['/profile']['initial_tracker']=dict(path='/archive',sha256='current')
    rows['/job']=dict(config=b('/config'));rows['/config']=dict(code=b('/old'))
    pub.load=lambda name,row:E if row==b('/environment-reader')else None
    if fault=='source':e['source']=b('/foreign')
    elif fault=='workbook':e['workbook']='9999'
    elif fault=='job':e['original_job']=b('/foreign')
    elif fault=='archive':rows['/profile']['initial_tracker']['sha256']='foreign'
    elif fault=='root':rows['/old']['root']='/other'
    elif fault=='schema':e['unexpected']=True
    elif fault=='helper-context':p['context']['kwargs']={}
    elif fault=='unused':p['stop_after_disclosure_build']=True
    elif fault=='false-kind':p['source_revision']=False
    if fault:
        with pytest.raises((ValueError,KeyError)):M.history(pub,plan)
    else:
        assert M.history(pub,plan)==plan['disclosure_continuation']['plan']
        assert M.version(plan,pub.checked)=='new'
