from pathlib import Path
from types import SimpleNamespace
import json,sys
import pytest
from scripts.fleet import aggregate_publication_context as M,fresh_publication as F,publication_launch as L
from scripts.fleet.migration import binding,checked
from scripts.fleet import advance_authoring_v8 as D

def keep(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)

@pytest.fixture
def source(tmp_path):
    source=keep(tmp_path/'source',{});old=keep(tmp_path/'old',dict(root='/code/old'));new=keep(tmp_path/'new',dict(root='/code/current'))
    cfg=keep(tmp_path/'config',dict(code=old));job=keep(tmp_path/'job',dict(config=cfg));helper=keep(tmp_path/'reader',{})
    fresh=keep(tmp_path/'fresh',{});origin=keep(tmp_path/'origin',dict(workbook='0171',fresh_plan=fresh,original_job=job))
    command=keep(tmp_path/'command',dict(schema_version='fleet-disclosure-command-continuation/v1',stop_after_disclosure_build=True,
        helper=helper,original_job=job,source=source,context=dict(plan=origin)))
    prepare=keep(tmp_path/'prepare',dict(schema_version='fleet-disclosure-review-preparation/v1',loader=dict(plan=command,helper=helper)))
    review=keep(tmp_path/'review',dict(workbook='0171',source=source,helper=helper,code_snapshots=[new],retained=dict(plan=prepare)))
    p=dict(schema_version=M.SCHEMA,helper=helper,workbook='0171',source=source,original_job=job,context=dict(plan=review,helper=helper),owning_profile=dict(worker=dict(path='/code/current/scripts/batch/harbor_instruction_worker.py',sha256='f'*64)))
    row=keep(tmp_path/'plan',p);plan=dict(disclosure_continuation=dict(reader=helper,plan=row,completed_reader=binding(M.__file__)),fresh_authoring=fresh,workbook='0171',source_sha256=source['sha256'],code_root='/code/old')
    return plan,p

@pytest.mark.parametrize('fault',[None,'source','workbook','root','reader','mixed'])
def test_aggregate_source_and_selected_version_are_closed(source,fault):
    plan,p=source;pub=SimpleNamespace(binding=binding,checked=checked)
    if fault=='source':plan['source_sha256']='a'*64
    if fault=='workbook':plan['workbook']='0001'
    if fault=='root':plan['code_root']='/foreign'
    if fault=='reader':plan['disclosure_continuation']['reader']=binding(M.__file__)
    if fault=='mixed':plan['model_tail']={}
    if fault:
        with pytest.raises((ValueError,KeyError)):M.history(pub,plan)
    else:
        assert M.history(pub,plan)==plan['disclosure_continuation']['plan']
        assert L.publication_version(plan,checked)=='current'
        assert plan['code_root']=='/code/old'


def test_version_load_refuses_changed_completed_reader(source,tmp_path):
    plan,_=source;path=tmp_path/'copied-reader.py';path.write_bytes(Path(M.__file__).read_bytes());plan['disclosure_continuation']['completed_reader']=binding(path)
    path.write_text('raise AssertionError("must not execute changed code")')
    with pytest.raises(ValueError):L.publication_version(plan,checked)

@pytest.fixture
def chain(tmp_path):
    home=tmp_path/'author';ops=tmp_path/'run/dispatcher-v2/author';permit=keep(home/'permit/result.json',{})
    p=dict(output=str(home),helper=binding(M.__file__));row=keep(tmp_path/'plan',p);prior=permit;outcomes=[]
    for index,folder in enumerate([home/'first-step',ops/'000']):
        args=dict(checkpoint=prior,descriptor=keep(tmp_path/f'desc{index}',{}));intent=keep(folder/'intent.json',dict(arguments=args,models=False,commands=False,containers=False));log=folder/'step.log';log.write_text('original command complete')
        guard=keep(folder/'resource.json',dict(intent=intent,log=binding(log),returncode=0,failure=None))
        result=keep(folder/'result.json',dict(status='STEP_COMPLETE'if index==0 else'PUBLICATION_READY',next_step='disclosure-commit'if index==0 else None))
        outcome=keep(folder/'outcome.json',dict(status='COMPLETED_OPERATION',resource=guard,result=result,automatic_retry=False));outcomes.append(outcome)
        keep(folder/'aggregate-bootstrap.json',dict(schema_version=M.SCHEMA,plan=row,helper=p['helper'],kind='instruction',intent=intent,original_command=[intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592,automatic_retry=False));prior=result
    env=(None,SimpleNamespace(guard_seconds=lambda *a:3300),None,None,None,dict(run_root=str(tmp_path/'run')))
    U=SimpleNamespace(retained_command=lambda command,*args:('instruction',dict(path=command[0],sha256=command[1])))
    S=SimpleNamespace(SCHEMA=M.SCHEMA,adoption_home=lambda p:home)
    dispatcher=SimpleNamespace(successful=lambda out,env:D.successful(out))
    ready=dict(outcome=outcomes[-1],checkpoint=prior);pub=SimpleNamespace(binding=binding,checked=checked)
    return pub,S,U,dispatcher,env,p,row,ready,ops

@pytest.mark.parametrize('fault',[None,'guard','sidecar','previous','extra','ready'])
def test_actual_original_guarded_chain_requires_every_predecessor(chain,fault):
    pub,S,U,dispatcher,env,p,row,ready,ops=chain
    if fault=='guard':keep(ops/'000/resource.json',dict(returncode=1))
    if fault=='sidecar':keep(ops/'000/aggregate-bootstrap.json',{})
    if fault=='previous':keep(ops/'000/intent.json',dict(arguments=dict(checkpoint={})))
    if fault=='extra':(ops/'002').mkdir()
    if fault=='ready':ready['outcome']={}
    if fault:
        with pytest.raises((ValueError,KeyError,FileNotFoundError)):M.completed_chain(pub,S,U,dispatcher,env,p,row,ready)
    else:assert M.completed_chain(pub,S,U,dispatcher,env,p,row,ready)[2]['status']=='PUBLICATION_READY'


def test_publication_child_loads_completed_disclosure_before_publication_only(monkeypatch,tmp_path):
    plan=dict(code_root=str(tmp_path),workbook='0171',disclosure_continuation={},ready={'path':'ready','sha256':'r'})
    row={'path':'plan','sha256':'p'};ready=dict(job={'path':'job','sha256':'j'});history={'path':'history','sha256':'h'};loaded=[]
    class Boundary(Exception):pass
    def publication_only():
        assert loaded==['completed-disclosure'];raise Boundary()
    pub=SimpleNamespace(checked=lambda r:plan if r==row else ready if r==plan['ready']else{},require=M.require,history_binding=lambda p:history,publication_only=publication_only,pinned_dispatcher=None)
    monkeypatch.setattr(F,'history_survey',lambda *a:None);monkeypatch.setattr(F,'selected_history_survey',lambda *a:None)
    monkeypatch.setattr(F,'dispatcher',lambda *a:loaded.append('completed-disclosure'))
    monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
    monkeypatch.setenv('HARBOR_FLEET_RETAINED_HISTORY_PATH','history');monkeypatch.setenv('HARBOR_FLEET_RETAINED_HISTORY_SHA256','h');monkeypatch.setenv('HARBOR_FLEET_WORKBOOK','0171')
    monkeypatch.setattr(sys,'argv',['pub','publication-child','--plan','plan','--plan-sha256','p','--intent','intent','--intent-sha256','i','--adapter','a'])
    with pytest.raises(Boundary):F.publication_child(pub,None)
