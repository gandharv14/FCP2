import copy,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import semantic_tail_publication_context as M
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet import advance_authoring_v8 as D

@pytest.fixture
def source():
    a=dict(reader='tail-reader',plan='tail',completed_reader='publication-reader')
    common=dict(workbook='0171',source={'path':'/source','sha256':'s'},original_job='job')
    completed=dict(outcome='outcome',result='result',descriptor='descriptor',blocked='blocked')
    c=dict(kind='completed_disclosure',helper='history-reader',plan='history',snapshot='new',config='config')
    original=dict(common,**completed,schema_version='fleet-instruction-semantic-continuation/v1',helper='semantic-reader',retained_context=c,fresh_plan='history')
    tail=dict(common,schema_version='fleet-completed-instruction-continuation/v1',helper='tail-reader',semantic_context={'helper':'semantic-reader','plan':'semantic'},fresh_plan='history',checkpoint_lease={},unstarted_semantic={})
    q=dict(common,**completed,schema_version='fleet-retained-completed-aggregate/v1',helper='history-reader',snapshot='new',config='config')
    rows=dict(tail=tail,semantic=original,history=q,job={'config':'config'},config={'code':'old'},old={'root':'/code/original'},new={'root':'/code/selected'})
    plan=dict(semantic_continuation=a,fresh_authoring='history',workbook='0171',source_sha256='s',code_root='/code/original')
    return plan,rows

@pytest.mark.parametrize('fault',[None,'source','blocked','root','snapshot','reader','permission','both','neither','job'])
def test_closed_tail_source_and_actual_selected_version(source,fault):
    plan,rows=copy.deepcopy(source)
    if fault=='source':rows['history']['source']['sha256']='different'
    elif fault=='blocked':rows['semantic']['blocked']='foreign'
    elif fault=='root':plan['code_root']='/code/other'
    elif fault=='snapshot':rows['history']['snapshot']='other'
    elif fault=='reader':rows['tail']['helper']='other'
    elif fault=='permission':rows['semantic']['schema_version']='fabricated'
    elif fault=='both':rows['tail']['interrupted_enqueue']={}
    elif fault=='neither':rows['tail'].pop('unstarted_semantic')
    elif fault=='job':rows['tail']['original_job']='foreign'
    if fault:
        with pytest.raises((ValueError,KeyError)):M.version(plan,rows.__getitem__)
    else:assert M.version(plan,rows.__getitem__)=='selected'


def test_completed_enqueue_disclosure_context_uses_same_interface(source):
    plan,rows=source;rows['tail'].pop('unstarted_semantic');rows['tail']['interrupted_enqueue']={}
    rows['history']['schema_version']='fleet-retained-completed-disclosure/v1'
    assert M.version(plan,rows.__getitem__)=='selected'


@pytest.mark.parametrize('takeover',[False,True])
def test_exact_tail_reader_delegated_by_version_and_dependencies(source,monkeypatch,takeover):
    from scripts.fleet import publication_launch as P,fresh_publication as F
    plan,rows=source;seen=[]
    if takeover:
        tail=rows['tail'];tail.pop('checkpoint_lease');tail.pop('unstarted_semantic')
        tail.update(checkpoint_takeover={'helper':'K','profile':'profile'},interrupted_commit={},next_step='dialogue-commit')
    monkeypatch.setattr(P,'module',lambda name,row:seen.append(row)or NS(version=M.version))
    assert P.publication_version(plan,rows.__getitem__)=='selected'and seen==['publication-reader']
    exact=dict(path='/specific/dependency',sha256='hash')
    pub=NS(FRESH_DEPENDENCIES={},checked=rows.__getitem__,require=M.require,
        load=lambda name,row:NS(dependencies=lambda pub,p:[exact]))
    assert exact in F.reader_evidence(pub,dict(plan,author_helper='original'))

@pytest.fixture
def chain(tmp_path):
    home=tmp_path/'tail';ops=tmp_path/'run/dispatcher-v2/tail';ops.mkdir(parents=True)
    p=dict(output=str(home),helper={'path':'/tail.py','sha256':'s'})
    row=retain(tmp_path/'plan',p);prior=retain(tmp_path/'lease-result',{});start=prior;outcomes=[]
    for folder in [home/'first-step',ops/'000']:
        intent=retain(folder/'intent.json',dict(arguments=dict(checkpoint=prior)))
        log=folder/'step.log';log.write_text('original completed guard')
        guard=retain(folder/'resource.json',dict(intent=intent,log=binding(log),returncode=0,failure=None))
        result=retain(folder/'result.json',dict(status='PUBLICATION_READY'))
        out=retain(folder/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
        retain(folder/'semantic-bootstrap.json',dict(schema_version='fleet-retained-semantic-child/v1',plan=row,
            helper=p['helper'],kind='instruction',intent=intent,original_command=[intent['path'],intent['sha256']],
            seconds=3300,memory_bytes=8589934592,automatic_retry=False));outcomes.append(out);prior=result
    pub=NS(binding=binding,checked=checked);U=NS(retained_command=lambda c,*a:('instruction',dict(path=c[0],sha256=c[1])),retained_seconds=lambda *a:3300)
    driver=NS(successful=lambda row,env:D.successful(row));env=(None,None,None,None,None,{'run_root':str(tmp_path/'run')})
    return pub,U,driver,env,p,row,dict(outcome=outcomes[-1],checkpoint=prior),start,ops

@pytest.mark.parametrize('fault',[None,'gap','guard','predecessor','ready'])
def test_each_original_guard_and_predecessor_required(chain,fault):
    *args,ops=chain
    if fault=='gap':(ops/'002').mkdir()
    elif fault=='guard':(ops/'000/resource.json').write_text('{}')
    elif fault=='predecessor':(ops/'000/intent.json').write_text('{}')
    elif fault=='ready':args[-2]['outcome']={}
    if fault:
        with pytest.raises((ValueError,KeyError,FileNotFoundError)):M.chain(*args)
    else:assert M.chain(*args)[2]['status']=='PUBLICATION_READY'

@pytest.mark.parametrize('fault',[None,'mixed','source','renewal','fence','reader'])
def test_completed_enqueue_retains_exact_prior_renewal(source,fault):
    plan,rows=copy.deepcopy(source);old=rows['tail']
    old.pop('unstarted_semantic');old.update(interrupted_enqueue={},fence=4,owner='old',output='/old',authorization='auth',outcome='actual',next_step='enqueue')
    rows['previous']=copy.deepcopy(old)
    old.pop('interrupted_enqueue');old.pop('checkpoint_lease')
    old.update(completed_enqueue={'previous':{'helper':'tail-reader','plan':'previous'}},fence=5,owner='new',output='/new')
    if fault=='mixed':old['checkpoint_lease']={}
    elif fault=='source':rows['previous']['source']={'path':'/other','sha256':'s'}
    elif fault=='renewal':rows['previous'].pop('checkpoint_lease')
    elif fault=='fence':old['fence']=4
    elif fault=='reader':rows['previous']['helper']='other'
    if fault:
        with pytest.raises((ValueError,KeyError)):M.version(plan,rows.__getitem__)
    else:
        assert M.version(plan,rows.__getitem__)=='selected'
        assert M.renewal_predecessor(old,rows.__getitem__) is rows['previous']

@pytest.mark.parametrize('fault',[None,'mixed','phase','missing_commit','shape'])
def test_completed_commit_takeover_uses_original_semantic_source(source,fault):
    plan,rows=copy.deepcopy(source);p=rows['tail'];p.pop('checkpoint_lease');p.pop('unstarted_semantic')
    p.update(checkpoint_takeover={'helper':'original-takeover','profile':'original-profile'},interrupted_commit={},next_step='dialogue-commit')
    if fault=='mixed':p['checkpoint_lease']={}
    elif fault=='phase':p['next_step']='dialogue-writer'
    elif fault=='missing_commit':p.pop('interrupted_commit')
    elif fault=='shape':p['checkpoint_takeover']['extra']='unbound'
    if fault:
        with pytest.raises(ValueError):M.version(plan,rows.__getitem__)
    else:assert M.version(plan,rows.__getitem__)=='selected'
