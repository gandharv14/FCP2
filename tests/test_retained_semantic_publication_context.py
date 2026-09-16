from pathlib import Path
from types import SimpleNamespace as NS
import copy
import pytest
from scripts.fleet import retained_semantic_publication_context as M

@pytest.fixture
def case():
    a=dict(reader='author',plan='semantic',completed_reader='reader')
    c=dict(kind='completed_disclosure',helper='dcc-reader',plan='dcc',snapshot='new',config='config')
    common=dict(workbook='0102',source={'path':'/source','sha256':'s'},original_job='job',outcome='outcome',result='result',descriptor='descriptor',blocked='blocked')
    p=dict(common,schema_version='fleet-instruction-semantic-continuation/v1',helper='author',retained_context=c,fresh_plan='dcc')
    q=dict(common,schema_version='fleet-retained-completed-disclosure/v1',helper='dcc-reader',snapshot='new',config='config',original_snapshot='old')
    rows=dict(semantic=p,dcc=q,old=dict(root='/code/original'),new=dict(root='/code/selected'))
    plan=dict(semantic_continuation=a,fresh_authoring='dcc',workbook='0102',source_sha256='s',code_root='/code/original')
    return plan,rows

@pytest.mark.parametrize('fault',[None,'workbook','source','review','job','descriptor','root','snapshot','reader','kind','fresh'])
def test_closed_semantic_source_and_version(case,fault):
    plan,rows=copy.deepcopy(case)
    if fault=='workbook':rows['dcc']['workbook']='other'
    elif fault=='source':rows['dcc']['source']['sha256']='changed'
    elif fault=='review':rows['semantic']['outcome']='foreign'
    elif fault=='job':rows['semantic']['original_job']='foreign'
    elif fault=='descriptor':rows['semantic']['descriptor']='foreign'
    elif fault=='root':plan['code_root']='/code/foreign'
    elif fault=='snapshot':rows['dcc']['snapshot']='foreign'
    elif fault=='reader':rows['semantic']['helper']='foreign'
    elif fault=='kind':rows['semantic']['retained_context']['kind']='fresh'
    elif fault=='fresh':plan['fresh_authoring']='fresh'
    if fault:
        with pytest.raises((ValueError,KeyError)):M.version(plan,rows.__getitem__)
    else:
        assert M.version(plan,rows.__getitem__)=='selected'
        assert M.selection(plan,rows.__getitem__)[1]is rows['semantic']


def test_publication_version_delegates_exact_selected_retained_reader(case,monkeypatch):
    from scripts.fleet import publication_launch as L
    plan,rows=case
    seen=[]
    def load(name,bound):
        seen.append(bound);return NS(version=lambda p,r:M.version(p,r))
    monkeypatch.setattr(L,'module',load)
    assert L.publication_version(plan,rows.__getitem__)=='selected'
    assert seen==['reader']


def test_publication_dependencies_use_retained_contract_without_physical_keys(monkeypatch):
    from scripts.fleet import fresh_publication as F
    a=dict(reader='author',plan='semantic',completed_reader='reader');selected=dict(helper='author',retained_context={'kind':'completed_disclosure'})
    exact={'path':'/retained/original-proof','sha256':'bound'}
    pub=NS(FRESH_DEPENDENCIES={},checked=lambda row:selected,require=lambda ok,m:None if ok else (_ for _ in()).throw(ValueError(m)),load=lambda name,row:NS(dependencies=lambda pub,p:[exact]))
    assert exact in F.reader_evidence(pub,dict(semantic_continuation=a,author_helper='original',fresh_authoring='fresh'))

@pytest.mark.parametrize('fault',[None,'archive','takeover','schema','source','extra'])
def test_completed_pre_dispatch_requires_exact_original_root_and_renewal(case,fault):
    plan,rows=copy.deepcopy(case);p=rows['semantic'];q=rows['dcc'];p['retained_context']['kind']='completed_pre_dispatch';p['tracker']={'path':'/native/tracker','sha256':'before'};q['schema_version']='fleet-retained-pre-dispatch-review/v1';q.pop('original_snapshot');plan['code_root']='/code/selected';plan['semantic_continuation']['renewal_before']={'path':'/retained/before','sha256':'before'}
    if fault=='archive':plan['semantic_continuation']['renewal_before']['sha256']='other'
    elif fault=='takeover':p['completed_takeover']={}
    elif fault=='schema':q['schema_version']='fleet-retained-completed-disclosure/v1'
    elif fault=='source':q['source']={'path':'/foreign','sha256':'s'}
    elif fault=='extra':plan['semantic_continuation']['skip_history']=True
    if fault:
        with pytest.raises(ValueError):M.selection(plan,rows.__getitem__)
    else:assert M.version(plan,rows.__getitem__)=='selected'

@pytest.mark.parametrize('fault',[None,'archive','source','extra'])
def test_root_version_delegates_closed_pre_dispatch_semantic_history(case,monkeypatch,fault):
    from scripts.fleet import publication_launch as L
    plan,rows=copy.deepcopy(case);p=rows['semantic'];q=rows['dcc'];p['retained_context']['kind']='completed_pre_dispatch';p['tracker']={'path':'/native/tracker','sha256':'before'};q['schema_version']='fleet-retained-pre-dispatch-review/v1';q.pop('original_snapshot');plan['code_root']='/code/selected';plan['semantic_continuation']['renewal_before']={'path':'/retained/before','sha256':'before'}
    if fault=='archive':plan['semantic_continuation']['renewal_before']['sha256']='foreign'
    elif fault=='source':q['source']={'path':'/foreign','sha256':'s'}
    elif fault=='extra':plan['semantic_continuation']['skip_history']=True
    seen=[]
    monkeypatch.setattr(L,'module',lambda name,bound:seen.append(bound)or NS(version=M.version))
    if fault:
        with pytest.raises(ValueError):L.publication_version(plan,rows.__getitem__)
    else:
        assert L.publication_version(plan,rows.__getitem__)=='selected'
        assert seen==['reader']
