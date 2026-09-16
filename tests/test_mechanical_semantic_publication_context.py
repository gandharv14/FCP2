import copy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import mechanical_semantic_publication_context as M
from scripts.fleet.migration import binding,checked,retain
from scripts.fleet import advance_authoring_v8 as D

@pytest.fixture
def source():
    common=dict(workbook='0171',source={'path':'/source','sha256':'s'},original_job='job',fresh_plan='history')
    mechanical=dict(common,schema_version='fleet-ordinary-mechanical-dialogue/v1',helper='mechanical-reader',utilities='tail-reader',fence=8,
        semantic_context=dict(reader='context-reader',plan='tail',outcomes=[],library='lib',original_library='oldlib',instruction_history='archive'))
    tail=dict(common,schema_version='fleet-completed-instruction-continuation/v1',helper='tail-reader',checkpoint_lease={},unstarted_semantic={},fence=7,semantic_context={'plan':'semantic','helper':'semantic-reader'})
    c=dict(kind='completed_disclosure',helper='history-reader',plan='history',config='config',snapshot='new')
    semantic=dict(common,schema_version='fleet-instruction-semantic-continuation/v1',helper='semantic-reader',retained_context=c)
    q=dict(common,schema_version='fleet-retained-completed-aggregate/v1',helper='history-reader',config='config',snapshot='new')
    rows=dict(mechanical=mechanical,tail=tail,semantic=semantic,history=q,job={'config':'config'},config={'code':'old'},old={'root':'/code/original'},new={'root':'/code/selected'})
    plan=dict(semantic_continuation={'reader':'mechanical-reader','plan':'mechanical','completed_reader':'publication-reader'},workbook='0171',source_sha256='s',fresh_authoring='history',code_root='/code/original')
    return plan,rows

@pytest.mark.parametrize('fault',[None,'source','root','helper','snapshot','fence','generation','context','job'])
def test_closed_actual_mechanical_source_chain(source,fault):
    plan,rows=copy.deepcopy(source)
    if fault=='source':rows['history']['source']={'path':'/other','sha256':'s'}
    elif fault=='root':plan['code_root']='/other'
    elif fault=='helper':rows['mechanical']['utilities']='foreign'
    elif fault=='snapshot':rows['history']['snapshot']='old'
    elif fault=='fence':rows['mechanical']['fence']=7
    elif fault=='generation':rows['tail'].pop('unstarted_semantic')
    elif fault=='context':rows['semantic']['retained_context']['kind']='physical'
    elif fault=='job':rows['mechanical']['original_job']='different'
    if fault:
        with pytest.raises((KeyError,ValueError)):M.version(plan,rows.__getitem__)
    else:assert M.version(plan,rows.__getitem__)=='selected'


def test_bound_version_and_dependencies_are_used(source,monkeypatch):
    from scripts.fleet import publication_launch as P,fresh_publication as F
    plan,rows=source;seen=[]
    monkeypatch.setattr(P,'module',lambda name,row:seen.append(row)or NS(version=M.version))
    assert P.publication_version(plan,rows.__getitem__)=='selected'and seen==['publication-reader']
    exact=dict(path='/specific/runtime',sha256='hash')
    pub=NS(FRESH_DEPENDENCIES={},checked=rows.__getitem__,require=M.require,load=lambda name,row:NS(dependencies=lambda pub,p:[exact]))
    assert exact in F.reader_evidence(pub,dict(plan,author_helper='original'))

@pytest.mark.parametrize('fault',[None,'gap','guard','predecessor','end','empty'])
def test_original_guard_chain_starts_at_actual_mechanical_completion(tmp_path,fault):
    p=dict(output=str(tmp_path/'mechanical'),helper={'path':'/mechanical.py','sha256':'s'})
    row=retain(tmp_path/'plan',p);start=retain(tmp_path/'mechanical-complete',{})
    ops=tmp_path/'run/dispatcher-v2/mechanical';ops.mkdir(parents=True);prior=start
    for name in ('000','001'):
        folder=ops/name;intent=retain(folder/'intent.json',dict(arguments=dict(checkpoint=prior)))
        log=folder/'step.log';log.write_text('original completed guard')
        guard=retain(folder/'resource.json',dict(intent=intent,log=binding(log),returncode=0,failure=None))
        result=retain(folder/'result.json',dict(status='PUBLICATION_READY'if name=='001'else'STEP_COMPLETE'))
        out=retain(folder/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result))
        retain(folder/'semantic-bootstrap.json',dict(schema_version='fleet-retained-semantic-child/v1',plan=row,helper=p['helper'],kind='instruction',intent=intent,original_command=[intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False));prior=result
    ready=dict(outcome=out,checkpoint=result)
    if fault=='gap':(ops/'003').mkdir()
    elif fault=='guard':(ops/'001/resource.json').write_text('{}')
    elif fault=='predecessor':start={}
    elif fault=='end':ready['outcome']={}
    elif fault=='empty':
        import shutil
        shutil.rmtree(ops);ops.mkdir()
    pub=NS(binding=binding,checked=checked);U=NS(retained_command=lambda c,*a:('instruction',dict(path=c[0],sha256=c[1])),retained_seconds=lambda *a:3300)
    driver=NS(successful=lambda row,env:D.successful(row));env=(None,None,None,None,None,{'run_root':str(tmp_path/'run')})
    if fault:
        with pytest.raises((ValueError,KeyError,FileNotFoundError)):M.chain(pub,U,driver,env,p,row,ready,start)
    else:assert M.chain(pub,U,driver,env,p,row,ready,start)[1]['status']=='PUBLICATION_READY'
