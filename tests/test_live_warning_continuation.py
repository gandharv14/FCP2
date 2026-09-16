import copy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import retained_warning_continue as M

@dataclass
class Lease:
    owner:str='native-owner'
    token:str='opaque-test-token'
    revision:int=14
    sha256:str='old'


def fixture(tmp_path):
    def put(name,v):
        f=tmp_path/name;f.parent.mkdir(parents=True,exist_ok=True);return M.keep(f,v)
    old=Lease();prior=dict(owner=old.owner,token=old.token,revision=14,sha256='old')
    plan=put('original-plan.json',dict(tracker=dict(path='/native/tracker',sha256='old')))
    selected=put('plan.json',dict(plan='selected'))
    intent=put('out/live-renewal/intent.json',dict(authority=selected,previous=prior,tracker=dict(path='/native/tracker',sha256='old'),duration_ns=7200*10**9,original_expired_replacements=0))
    v=dict(schema_version='fleet-validated-continuation-lease/v1',duration_ns=7200*10**9,previous=prior,lease={**prior,'revision':15,'sha256':'new'},tracker=dict(path='/native/tracker',sha256='new'),intent=intent,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False,original_expired_replacements=0)
    p=dict(output=str(tmp_path/'out'),live_renewal=dict(plan=plan));C=NS(decode=lambda x:Lease(**x),bootstrap=NS(Lease=Lease))
    return selected,p,C,dict(lease=prior),v,put


def test_completed_live_renewal_does_not_require_mutable_current_tracker(tmp_path):
    row,p,C,old,v,put=fixture(tmp_path);put('out/live-renewal/complete.json',v)
    assert M.live_lease(row,p,C,old)==Lease(revision=15,sha256='new')
    assert not Path('/native/tracker').exists()

@pytest.mark.parametrize('change',['owner','token','revision','digest','duration','budget','guard','retry','authority'])
def test_renewal_receipt_cannot_change_owner_budget_or_authority(tmp_path,change):
    row,p,C,old,v,put=fixture(tmp_path)
    if change in ('owner','token'):v['lease'][change]='foreign'
    elif change=='revision':v['lease']['revision']=16
    elif change=='digest':v['tracker']['sha256']='foreign'
    elif change=='duration':v['duration_ns']*=2
    elif change=='budget':v['semantic_budget_reset']=True
    elif change=='guard':v['task_guards_changed']=True
    elif change=='retry':v['automatic_retry']=True
    elif change=='authority':row=put('foreign-plan.json',{})
    put('out/live-renewal/complete.json',v)
    with pytest.raises(ValueError):M.live_lease(row,p,C,old)


def test_native_renewal_guard_keeps_original_resources_and_retains_failure(tmp_path):
    calls=[]
    def failed(command,**kwargs):calls.append((command,kwargs));return 1
    row=M.keep(tmp_path/'plan.json',{});p=dict(helper=dict(path='/selected/helper.py'));C=NS(_run_in_process_group=failed)
    with pytest.raises(ValueError,match='never replay'):M.live_renew_guard(row,p,C,tmp_path,tmp_path)
    assert calls[0][1]['timeout_seconds']==3300 and calls[0][1]['memory_limit_bytes']==8589934592
    assert calls[0][0][1:3]==['-I','-B'] and 'live-renew-child'in calls[0][0]
    assert M.read(M.binding(tmp_path/'renew-resource.json'))['returncode']==1
    with pytest.raises(FileExistsError):M.live_renew_guard(row,p,C,tmp_path,tmp_path)
    assert len(calls)==1

@pytest.mark.parametrize('defect',[None,'prepared','snapshot'])
def test_live_warning_selects_exact_retained_normalization(monkeypatch,defect):
    import json
    from types import SimpleNamespace
    from scripts.fleet import retained_warning_continue as R
    row={'path':'/profile','sha256':'p'};prepared={'path':'/prepared','sha256':'q'};snapshot={'path':'/snapshot','sha256':'s'}
    p={'normalization_profile':row,'old_snapshot':snapshot};v={'prepared':prepared,'snapshot':snapshot}
    if defect:v[defect]={}
    monkeypatch.setattr(R,'read',lambda bound:v)
    monkeypatch.delenv('HARBOR_FLEET_NORMALIZATION_PROFILE',raising=False)
    selection=SimpleNamespace(content=SimpleNamespace(normalization_prepared=prepared))
    if defect:
        with pytest.raises(ValueError):R.select_normalization_profile(p,selection)
        assert 'HARBOR_FLEET_NORMALIZATION_PROFILE'not in R.os.environ
    else:
        R.select_normalization_profile(p,selection)
        assert json.loads(R.os.environ['HARBOR_FLEET_NORMALIZATION_PROFILE'])==row

