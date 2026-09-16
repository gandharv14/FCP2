import json,types
from pathlib import Path
import pytest
from scripts.fleet import naturalization_continue as N

def put(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return N.binding(p)

def fixture(tmp_path,defect=None):
    home=tmp_path/'old';home.mkdir();entry=put(tmp_path/'old.py',{});tracker=put(tmp_path/'tracker.json',{'revision':3})
    old=dict(entry=entry,output=str(home),owner='old',fence=3,renewal={},tracker=tracker,source={'sha256':'s'})
    prior=put(tmp_path/'plan.json',old)
    intent=put(home/'intent.json',dict(plan=prior,entry=entry,automatic_retry=False))
    log=home/'permit.log';log.write_text('scripts.batch.harbor_tracker.Conflict: renewal must extend expiry\n')
    guard=put(home/'permit-resource.json',dict(returncode=1,failure=None,models=0,seconds=3300,memory_bytes=8*1024**3,intent=intent,log=N.binding(log)))
    renewal=put(home/'renewal/intent.json',dict(authority=prior,tracker=tracker,duration_ns=7200*10**9,automatic_retry=False))
    p=dict(old,entry={},owner='new',fence=4,output=str(tmp_path/'new'),renewal={'path':'/new'},lease_policy=dict(mode='retain-sufficient-or-renew',minimum_seconds=7200),prepermit_recovery=dict(prior_plan=prior,guard=guard,renewal_intent=renewal))
    if defect=='tracker':(tmp_path/'tracker.json').write_text('{}')
    if defect=='source':p['source']={'sha256':'other'}
    if defect=='renewal_complete':put(home/'renewal/complete.json',{})
    if defect=='permit_complete':put(home/'permit-complete.json',{})
    if defect=='import':(home/'import').mkdir()
    if defect=='uncertain':
        value=N.read(guard);value['failure']='TimeoutError';p['prepermit_recovery']['guard']=put(home/'permit-resource.json',value)
    if defect=='different_error':
        log.write_text('provider outcome uncertain\n');value=N.read(guard);value['log']=N.binding(log);p['prepermit_recovery']['guard']=put(home/'permit-resource.json',value)
    if defect=='wrong_authority':
        value=N.read(renewal);value['authority']={};p['prepermit_recovery']['renewal_intent']=put(home/'renewal/intent.json',value)
    return p

def test_pre_cas_nonextending_renewal_is_reconciled_without_replay(tmp_path):
    p=fixture(tmp_path);before=N.binding(p['tracker']['path']);N.reconcile_prepermit(p)
    assert N.binding(p['tracker']['path'])==before and not Path(p['output']).exists()

@pytest.mark.parametrize('defect',['tracker','source','renewal_complete','permit_complete','import','uncertain','different_error','wrong_authority'])
def test_never_replay_changed_completed_or_uncertain_attempt(tmp_path,defect):
    with pytest.raises(ValueError):N.reconcile_prepermit(fixture(tmp_path,defect))

def test_explicit_sufficient_lease_policy_requires_supported_helper():
    p={'lease_policy':dict(mode='retain-sufficient-or-renew',minimum_seconds=7200)}
    assert N.lease_options(p,types.SimpleNamespace(renew=lambda *,retain_sufficient=False:None))==dict(retain_sufficient=True)
    with pytest.raises(ValueError):N.lease_options(p,types.SimpleNamespace(renew=lambda:None))
    p['lease_policy']['minimum_seconds']=1
    with pytest.raises(ValueError):N.lease_options(p,types.SimpleNamespace(renew=lambda *,retain_sufficient=False:None))
