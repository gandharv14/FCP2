from pathlib import Path
from copy import deepcopy
import pytest
from scripts.fleet import normalization_imported_continue as I
from scripts.fleet.normalization_remaining import keep

@pytest.fixture
def completed(tmp_path):
    n=iter(range(100))
    def put(v,path=None):
        f=tmp_path/(path or str(next(n)));f.parent.mkdir(parents=True,exist_ok=True);return keep(f,v)
    helper=tmp_path/'original.py';helper.write_text('')
    p=dict(single_author=put({}),prepared_refresh=None,workbook='0316',source=put({'raw':True}),home=str(tmp_path/'old'),fence=4,owner='previous-owner',helper=I.binding(helper))
    prepared=put(dict(selection={'exact':'selection'},last='normalization_phase1'),'first/prepared.json');p['prepared_refresh']=put(dict(prepared=prepared));previous=put(p)
    committed=put({'actual':'cas'},'first/committed.json');descriptor=put(dict(refresh=prepared))
    cp=put(dict(status='IMPORTED',selection={'exact':'selection'},step='normalization_phase1',descriptor=descriptor,evidence=[prepared,committed]),'old/refresh/result.json')
    resource=put(dict(returncode=0),'old/refresh/resource.json');refresh=put(dict(status='COMPLETED_OPERATION',result=cp,resource=resource),'old/refresh/outcome.json')
    gi=put(dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=cp,captures=None)))
    log=tmp_path/'old/bridge-step/step.log';log.parent.mkdir();log.write_text('ValueError: single emitted phase one changed\n')
    guard=put(dict(returncode=1,failure=None,intent=gi,log=I.binding(log)),'old/bridge-step/resource.json')
    job=put(dict(driver=p['helper'],inputs=[previous],workbook='0316',source=p['source'],version='version',result=str(tmp_path/'old/result.json'),command=['python','-I','-B',str(helper),'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    intent=put(dict(owner=p['owner'],job=job,claim_id='claim'),'worker/intent.json')
    put(dict(claim_id='claim',owner=p['owner'],fence=4,workbook='0316',version='version',source_sha256=p['source']['sha256']),'worker/claim.json')
    outer=put(dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=intent))
    q={k:put({})for k in I.FIELDS};q.update(schema_version=I.SCHEMA,helper=I.binding(I.__file__),previous=previous,workbook='0316',source=p['source'],home=str(tmp_path/'next'),fence=5,outer=outer,checkpoint=cp,refresh_outcome=refresh,bridge_guard=guard,committed=committed)
    return q,p,put

def test_exact_completed_import_without_repeating_it(completed):
    q,p,_=completed
    prepared,cp,outer=I.identity(q,p)
    assert cp['status']=='IMPORTED'
    assert prepared==I.read(p['prepared_refresh'])['prepared']

@pytest.mark.parametrize('field,value',[('fence',6),('home','/foreign/new'),('source',{'path':'/wrong','sha256':'0'*64}),('workbook','0000')])
def test_other_identity_refused(completed,field,value):
    q,p,_=completed;q[field]=value
    with pytest.raises(ValueError):I.identity(q,p)

def test_completed_bridge_cannot_be_replayed(completed):
    q,p,put=completed;put({'completed':True},'old/bridge-step/result.json')
    with pytest.raises(ValueError,match='post-import'):I.identity(q,p)

def test_claim_fence_must_be_original(completed):
    q,p,_=completed;path=Path(I.read(q['outer'])['intent']['path']).parent/'claim.json'
    import json
    v=json.loads(path.read_text());v['fence']=99;path.write_text(json.dumps(v))
    with pytest.raises(ValueError,match='claim custody'):I.identity(q,p)

def test_different_failure_is_not_reclassified(completed):
    q,p,put=completed;guard=I.read(q['bridge_guard']);guard['returncode']=124
    path=Path(q['bridge_guard']['path']);path.unlink();q['bridge_guard']=put(guard,'old/bridge-step/resource.json')
    with pytest.raises(ValueError,match='post-import'):I.identity(q,p)

@pytest.fixture
def preexecuted(completed,tmp_path):
    q,p,put=completed
    previous=put(q);job=put(dict(driver=q['helper'],inputs=[previous],source=q['source'],workbook=q['workbook'],version='version',command=['python','-I','-B',q['helper']['path'],'run','--plan',previous['path'],'--plan-sha256',previous['sha256']]))
    wi=put(dict(owner=q['owner'],claim_id='next-claim',job=job),'next-worker/intent.json')
    claim=put(dict(owner=q['owner'],fence=q['fence'],claim_id='next-claim',version='version',workbook=q['workbook'],source_sha256=q['source']['sha256']),'next-worker/claim.json')
    log=tmp_path/'next-worker/worker.log';log.write_text("if live:N.active(q)\nModuleNotFoundError: No module named 'scripts'\n")
    outer=put(dict(status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False,intent=wi,log=I.binding(log)),'next-worker/outcome.json')
    rows=[previous,job,wi,claim,I.binding(log),outer];stop=put(dict(at=1,processes=[],files=[dict(native=x,copy=x)for x in rows]))
    proof=put(dict(schema_version='fleet-imported-preexecution-stop/v1',previous=previous,outer=outer,stopped=stop))
    new=dict(q,home=str(tmp_path/'successor'),fence=6,preexecution=proof)
    return new,q,p,put


def test_preexecution_preserves_complete_import_and_no_task_effects(preexecuted):
    new,q,p,_=preexecuted;I.preexecution(new);assert I.identity(new,p)[1]['status']=='IMPORTED'

@pytest.mark.parametrize('change',[{'owner':'foreign'},{'fence':7},{'takeover_completion':{'path':'/foreign','sha256':'0'*64}}])
def test_preexecution_cannot_reset_authority(preexecuted,change):
    new,*_=preexecuted
    with pytest.raises(ValueError):I.preexecution(dict(new,**change))


def test_preexecution_refuses_any_task_namespace(preexecuted):
    new,q,*_=preexecuted;Path(q['home']).mkdir()
    with pytest.raises(ValueError,match='task effects'):I.preexecution(new)


def test_early_environment_check_requires_exact_claim_without_project_imports(monkeypatch):
    q=dict(workbook='0316',owner='owner',fence=6)
    for k,v in dict(HARBOR_FLEET_WORKBOOK='0316',HARBOR_FLEET_OWNER='owner',HARBOR_FLEET_FENCE='6',HARBOR_FLEET_URL='local',HARBOR_FLEET_TOKEN_FILE='private',HARBOR_FLEET_CLAIM_TOKEN='secret').items():monkeypatch.setenv(k,v)
    I.claim_environment(q)
    monkeypatch.setenv('HARBOR_FLEET_FENCE','5')
    with pytest.raises(ValueError):I.claim_environment(q)


def test_preexecution_claim_version_cannot_change(preexecuted):
    new,q,p,put=preexecuted;proof=I.read(new['preexecution']);outer=I.read(proof['outer']);f=Path(outer['intent']['path']).parent/'claim.json'
    import json
    v=json.loads(f.read_text());v['version']='foreign';f.write_text(json.dumps(v))
    with pytest.raises(ValueError):I.preexecution(new)


def test_replacement_takeover_follows_actual_completed_predecessor(preexecuted):
    new,previous,p,put=preexecuted
    tracker=put({'owner':'old-owner','revision':10})
    old_profile=put(dict(runtime={'retained':True},selection=dict(tracker=dict(path='/canonical/tracker',sha256='old'),checkpoint=new['checkpoint'],prior_completion={'old':'T1'},owner='old-owner')))
    old_done=put(dict(after=tracker))
    previous.update(takeover_profile=old_profile,takeover_completion=old_done,owner='old-owner')
    copied=put(I.read(tracker))
    stop=put(dict(before=copied));done=put(dict(stopped_owner_proof=stop))
    selected=I.read(old_profile);selected['selection'].update(tracker=dict(path='/canonical/tracker',sha256=tracker['sha256']),checkpoint=old_done,prior_completion=old_done,owner='new-owner')
    new.update(takeover_profile=put(selected),takeover_completion=done,owner='new-owner')
    assert I.replacement_takeover(new,previous)==old_done
    selected['selection']['checkpoint']=new['checkpoint']
    with pytest.raises(ValueError,match='checkpoint or authority'):
        I.replacement_takeover(dict(new,takeover_profile=put(selected)),previous)
    wrong=put(dict(before=put({'owner':'foreign-owner','revision':10})))
    with pytest.raises(ValueError,match='retained custody'):
        I.replacement_takeover(dict(new,takeover_completion=put(dict(stopped_owner_proof=wrong))),previous)
