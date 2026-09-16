from pathlib import Path
from types import SimpleNamespace as NS
import sys
import pytest
from scripts.fleet import adoption_renewal_runtime as M

@pytest.fixture
def case(tmp_path,monkeypatch):
    from scripts.batch import harbor_disclosure_command_repair as K,harbor_continuation_allowance as C
    from scripts.batch import harbor_disclosure_review_resume as D
    import scripts.batch as package
    root=tmp_path/'code';root.mkdir();tracker=tmp_path/'0279.json';tracker.write_text('{}')
    w=NS(root=root,run=tmp_path/'worker',code_repair='command',disclosure_repair='aggregate',context=NS(tracker_root=tmp_path))
    p=dict(schema_version='fleet-adoption-command-renewal/v1',helper='helper',renewal='renewal',history_helper='history',snapshot='snapshot',allowance='allowance',duration_ns=7200*10**9)
    values={'profile':p,'command':{'profile':'command-profile'},'command-profile':dict(snapshot='snapshot',workbook='0279'),'snapshot':dict(root=str(root)),'tracker':{}}
    calls=[]
    def require(ok,msg):
        if not ok:raise ValueError(msg)
    authorize=lambda *a,**kw:calls.append((a,kw))or 'original-complete'
    R=NS(authorize=authorize)
    H=NS(adoption_history=lambda *a:({'validated':True},['real-archive']))
    S=NS(read=values.__getitem__,binding=lambda path:'helper'if str(path)==M.__file__ else 'tracker',require=require,module=lambda name,row: R if row=='renewal'else H)
    monkeypatch.setattr(K,'selected',lambda worker:worker.code_repair=='command')
    monkeypatch.setattr(C,'lease_limits',lambda row,before:calls.append(('limits',row)))
    A=NS(selected=lambda worker:worker.disclosure_repair=='aggregate')
    env=(0,1,2,3,4,{'code':'original-snapshot'},root,7)
    return S,p,values,w,A,R,env,calls,D,package

def test_exact_original_dispatcher_receives_selected_renewal(case):
    S,p,values,w,A,R,env,calls,D,package=case
    prior_module=sys.modules.get('scripts.batch.harbor_instruction_lease_renewal');prior_attr=getattr(package,'harbor_instruction_lease_renewal',None);prior=(D.validate,D.validate_historical_integrity)
    with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value)))as selected:
        assert selected==(*env[:4],R,*env[5:])
        assert R.authorize(w,'selection','lease','checkpoint','original-snapshot')=='original-complete'
        assert calls[-1][0][-1]=='snapshot'
        assert calls[-1][1]==dict(duration_ns=7200*10**9,allowance='allowance')
        assert D.validate(w,{})=={'validated':True}
        assert D.validate_historical_integrity(w,{})[1]==['real-archive']
    assert (D.validate,D.validate_historical_integrity)==prior
    assert sys.modules.get('scripts.batch.harbor_instruction_lease_renewal')is prior_module
    assert getattr(package,'harbor_instruction_lease_renewal',None)is prior_attr

@pytest.mark.parametrize('change',[dict(duration_ns=7201*10**9),dict(snapshot='other'),dict(extra=True),dict(schema_version='other')])
def test_foreign_or_unbounded_profile_refuses(case,change):
    S,p,*rest=case;p.update(change);w,A,R,env=case[3:7]
    with pytest.raises((ValueError,KeyError)):
        with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value))):pass

@pytest.mark.parametrize('which',['snapshot','worker','kwargs'])
def test_original_delegation_rejects_changed_context(case,which):
    S,p,values,w,A,R,env,*_=case
    with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value))):
        other=NS(**vars(w));other.run=Path('/foreign')
        with pytest.raises(ValueError,match='detached'):
            R.authorize(other if which=='worker'else w,'s','l','c','other'if which=='snapshot'else'original-snapshot',**({'duration_ns':1}if which=='kwargs'else{}))

def test_failed_scope_restores_original_consumers(case):
    S,p,values,w,A,R,env,calls,D,_=case;prior=(D.validate,D.validate_historical_integrity);authorize=R.authorize
    with pytest.raises(RuntimeError):
        with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value))):raise RuntimeError('stop before write')
    assert (D.validate,D.validate_historical_integrity)==prior and R.authorize is authorize


def test_original_selected_gets_its_exact_retained_environment(case):
    S,p,values,w,A,R,env,*_=case
    calls=[];original=lambda observed,value:calls.append((observed,value))or 'selected'
    D=NS(selected=original)
    with M.installed(S,'profile',env,w,A,D)as selected:
        assert D.selected(selected,'checkpoint')=='selected'
        assert calls==[(env,'checkpoint')]
        with pytest.raises(ValueError):D.selected(env,'checkpoint')
    assert D.selected is original


def test_optional_archive_reader_is_explicit_and_scoped(case):
    S,p,values,w,A,R,env,calls,D,package=case
    archive=lambda *args:'real-archives';G=NS(SCHEMA='fleet-disclosure-semantic-archive/v1',historical_evidence=archive);seen=[]
    H=NS(adoption_history=lambda *a,**kw:seen.append((a,kw))or ({'validated':True},['real-archive']))
    S.module=lambda name,row:{'renewal':R,'history':H,'archive':G}[row]
    p['history_archive']='archive'
    with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value))):
        assert D.validate_historical_integrity(w,{})[1]==['real-archive']
    assert seen[0][1]=={'historical_evidence':archive}
    G.SCHEMA='foreign'
    with pytest.raises(ValueError,match='unsupported historical archive'):
        with M.installed(S,'profile',env,w,A,NS(selected=lambda observed,value:(observed,value))):pass
