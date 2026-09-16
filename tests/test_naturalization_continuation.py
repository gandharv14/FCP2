from pathlib import Path
import pytest
from scripts.fleet import naturalization_continue as N, coordinator
from test_fleet_quarantine import setup

@pytest.fixture
def custody(tmp_path,monkeypatch):
    c,now,claims,args=setup(tmp_path);c.quarantine_model(**args)
    c.enroll('0001','f'*64,'new source');c.reconcile('0001','original custody checked','ready')
    claim=c.claim('0001','hhu-server-7:slot-1:nat','distinct-claim','a'*64,3600)
    p=dict(workbook='0001',owner=claim['owner'],fence=claim['fence'],source=dict(sha256='f'*64),
           capacity_policy=N.binding(Path('scripts/fleet/capacity_policy.py').absolute()))
    class Client:
        def __init__(self,*args):pass
        def call(self,operation):
            assert operation=='status';return c.status()
    monkeypatch.setattr(coordinator,'Client',Client)
    monkeypatch.setattr(N.time,'time',lambda:now[0])
    for k,v in dict(HARBOR_FLEET_URL='unused',HARBOR_FLEET_TOKEN_FILE='unused',
                    HARBOR_FLEET_OWNER=p['owner'],HARBOR_FLEET_FENCE=str(p['fence'])).items():
        monkeypatch.setenv(k,v)
    return c,now,claims,claim,p,Path('/code')/('a'*64)

def auth(claim):return {k:claim[k]for k in ('workbook','owner','fence','token')}

def test_exact_quarantined_other_model_preserves_original_unknown(custody):
    c,_,_,_,p,root=custody;before=c.status()['operations'];N.custody(p,root)
    assert c.status()['operations']==before

def test_unrelated_publication_hold_does_not_consume_or_clear_it(custody):
    c,_,claims,_,p,root=custody
    c.begin('publication','prior-publication',request_sha256='e'*64,**claims['0141'])
    c.end('publication','prior-publication',evidence='uncertain archive',outcome='uncertain',**claims['0141'])
    before=c.status()['operations'];N.custody(p,root);assert c.status()['operations']==before

@pytest.mark.parametrize('state',['active','hold'])
def test_own_model_operation_requires_reconciliation(custody,state):
    c,_,_,claim,p,root=custody
    c.begin('model','own-model',request_sha256='e'*64,**auth(claim))
    if state=='hold':c.end('model','own-model',evidence='response unknown',outcome='uncertain',**auth(claim))
    with pytest.raises(ValueError):N.custody(p,root)

def test_second_unisolated_unknown_never_replays(custody):
    c,_,claims,_,p,root=custody
    c.begin('model','second-unknown',request_sha256='e'*64,**claims['0140'])
    c.end('model','second-unknown',evidence='unknown response',outcome='uncertain',**claims['0140'])
    with pytest.raises(ValueError):N.custody(p,root)

def test_expired_quarantine_restores_barrier(custody):
    _,now,_,_,p,root=custody;now[0]=2000
    with pytest.raises(ValueError):N.custody(p,root)

@pytest.mark.parametrize('field,value',[('fence',99),('owner','hhu-server-7:other'),('source',dict(sha256='0'*64))])
def test_wrong_claim_identity_is_rejected(custody,field,value):
    *_,p,root=custody
    with pytest.raises(ValueError):N.custody(dict(p,**{field:value}),root)

def test_immutable_retry_journal_is_never_overwritten(tmp_path):
    p=tmp_path/'intent.json';r=N.keep(p,dict(automatic_retry=False))
    with pytest.raises(FileExistsError):N.keep(p,dict(automatic_retry=True))
    assert N.read(r)==dict(automatic_retry=False)

def test_settled_import_never_authorizes_retry_again(monkeypatch):
    monkeypatch.setattr(N,'load',lambda _:(dict(resume={}),None,None,None,None,None,None))
    with pytest.raises(ValueError,match='cannot authorize'):N.permit_child({})

def test_completed_takeover_authorizes_exact_remaining_nat_without_renewal(tmp_path,monkeypatch):
    from types import SimpleNamespace
    output=tmp_path/'continued';output.mkdir()
    tracker=tmp_path/'tracker.json';tracker.write_text('{}')
    completion={'path':'/sealed/takeover/complete.json','sha256':'a'*64}
    p=dict(output=str(output),entry={'selected':True},tracker=N.binding(tracker),
           takeover_context=dict(completed_takeover=dict(completion=completion)))
    row={'selected-plan':True};N.keep(output/'intent.json',dict(plan=row,entry=p['entry'],automatic_retry=False))
    old_lease=object();current_lease=object();d=SimpleNamespace(_nat_takeover_lease=current_lease)
    worker=object();selection=object();blocked={'retained-rejection':True};calls=[]
    def authorize(w,s,lease,**kwargs):
        assert (w,s,lease)==(worker,selection,current_lease)
        assert kwargs==dict(blocked=blocked,takeover=completion)
        calls.append('authorize');return w,s,lease,{'actual-retry-completion':True}
    def forbidden(*a,**k):pytest.fail('completed takeover must not renew or dispatch')
    retry=SimpleNamespace(authorize=authorize)
    monkeypatch.setattr(N,'load',lambda _:(p,None,d,(None,None,None,None,None,None,tmp_path),None,SimpleNamespace(renew=forbidden),retry))
    monkeypatch.setattr(N,'completed_rejection',lambda *a:({'checkpoint':blocked},worker,selection,old_lease))
    monkeypatch.setattr(N,'custody',lambda *a:None)
    result=N.read(N.permit_child(row))
    assert calls==['authorize'] and result['renewal'] is None
    assert result['semantic_budget_reset'] is False and result['models']==0

def test_failed_selected_nat_dependency_restores_custody_manager(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts.batch import harbor_nat_retry
    calls=[];manager=SimpleNamespace(__exit__=lambda *args:calls.append(args))
    helper=SimpleNamespace(retained_lease=lambda _:('lease',SimpleNamespace(manager=manager)))
    profile=N.keep(tmp_path/'dependencies.json',dict(dependencies=[]))
    context=dict(helper={},fresh_plan={},completed_takeover=dict(dependency_profile=profile))
    p={key:{} for key in ('original_job','outcome','result','source','tracker')}
    p.update(workbook='0563',owner='selected-owner',takeover_context=context)
    monkeypatch.setattr(N,'module',lambda *a:helper)
    with pytest.raises(ValueError,match='not declared'):N.completed_takeover(p)
    assert len(calls)==1 and calls[0][0] is ValueError

@pytest.fixture
def imported(tmp_path):
    from types import SimpleNamespace
    def saved(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        return N.keep(path,value)
    prior={k:dict(path='/fixture/'+k,sha256='a'*64)for k in ('original_job','outcome','result','source','authorization','renewal','capacity_policy')}
    home=tmp_path/'continuations'/'prior';home.mkdir(parents=True)
    prior.update(schema_version=N.SCHEMA,workbook='0498',output=str(home),entry=saved(tmp_path/'prior-entry.json',{}))
    pr=saved(tmp_path/'prior.json',prior)
    intent=saved(home/'intent.json',dict(plan=pr,entry=prior['entry'],automatic_retry=False))
    completion=saved(home/'completion.json',dict(consumed=1,allowed=2))
    permit=saved(home/'permit-complete.json',dict(plan=pr,completion=completion,models=0,semantic_budget_reset=False,automatic_retry=False))
    log=home/'permit.log';log.write_text('completed')
    guard=saved(home/'permit-resource.json',dict(intent=intent,log=N.binding(log),returncode=0,failure=None,seconds=3300,memory_bytes=8*1024**3,models=0))
    checkpoint=saved(home/'import/result.json',dict(status='RETRY_READY',next_step='nat-writer',descriptor={'original':True}))
    saved(home/'import/intent.json',dict(action='import-retry',arguments=dict(retry_completion=completion,checkpoint=prior['result'])))
    outcome=saved(home/'import/outcome.json',dict(result=checkpoint))
    failed=tmp_path/'dispatcher-v2'/'prior'/'000';failed.mkdir(parents=True)
    fi=saved(failed/'intent.json',dict(action='step',arguments=dict(descriptor={'original':True},checkpoint=checkpoint)))
    log=failed/'step.log';log.write_text(prior['entry']['path']+' in load\nValueError: dispatcher namespace exists; no replay\n')
    fr=saved(failed/'resource.json',dict(returncode=1,failure=None,intent=fi,log=N.binding(log)))
    fo=saved(failed/'outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,resource=fr))
    r=dict(schema_version='fleet-naturalization-settled-import/v1',prior_plan=pr,permit=permit,guard=guard,import_outcome=outcome,failed_outcome=fo)
    p=dict(prior,output=str(tmp_path/'continuations'/'next'),resume=saved(tmp_path/'resume.json',r))
    def successful(row,env):
        value=N.read(row);return value['result'],N.read(value['result'])
    d=SimpleNamespace(successful=successful)
    env=(None,None,None,None,None,dict(run_root=str(tmp_path)))
    return p,d,env,r

def test_completed_import_uses_existing_permit_without_reset(imported):
    p,d,env,r=imported
    assert N.settled_import(p,d,env)==(r['import_outcome'],r['guard'])
    assert N.read(N.read(r['permit'])['completion'])==dict(consumed=1,allowed=2)

@pytest.mark.parametrize('change',['uncertain-guard','changed-permit','different-step','completed-child'])
def test_settled_import_rejects_uncertain_or_changed_boundary(imported,change):
    import json
    p,d,env,r=imported
    if change=='completed-child':
        Path(r['failed_outcome']['path']).with_name('result.json').write_text('{}')
    else:
        key={'uncertain-guard':'guard','changed-permit':'permit','different-step':'import_outcome'}[change]
        path=Path(r[key]['path']);value=N.read(r[key]);value['changed']=True;path.write_text(json.dumps(value))
    with pytest.raises(ValueError):N.settled_import(p,d,env)
