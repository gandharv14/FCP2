"""Transaction confidence mechanics; full skip provenance is tested separately."""
import copy
import pytest
from test_harbor_ready_transitions import fixture,kw
from scripts.batch import harbor_ready_transitions as R,harbor_tracker_lifecycle as L


def skip_proof():return dict(checked=True,result=dict(confidence='medium',public_sources=1,accepted_profiles=0,skipped_profiles=1,skip_reasons=['http_403']))


@pytest.mark.parametrize('action',['enqueue','clear'])
def test_verified_skip_medium_survives_real_ready_cas(tmp_path,monkeypatch,action):
    f,path,start,selection,proof=fixture(tmp_path,monkeypatch,failure=action=='clear')
    proof['checks']['accepted_public_profiles']=skip_proof()
    from scripts.batch import harbor_profile_skips as S
    from types import SimpleNamespace
    selection=SimpleNamespace(**vars(selection),public_profiles='separately-tested-actual-profile') if not hasattr(selection,'public_profiles')else selection
    monkeypatch.setattr(S,'marker',lambda *args:dict(schema_version=S.MARKER))
    monkeypatch.setattr(S,'validate_marker',lambda *args:True)
    if action=='enqueue':
        start['tracker']['lane_state']['harbor-source-profiler']['current_confidence']='medium'
        start['tracker']['current_confidence']['level']='medium'
        path.write_bytes(L.canonical(start['tracker']));start['sha256']=R.record(path)['sha256']
    old=copy.deepcopy(start['tracker']);result=f.transition('0245',action,selection,**kw(start));after=result['tracker']
    assert after['current_confidence']['level']=='medium'
    assert after['lane_state']['harbor-source-profiler']['current_confidence']=='medium'
    assert all(v['current_confidence']=='high'for k,v in after['lane_state'].items()if k!='harbor-source-profiler')
    assert after['repairs']==old['repairs'] and after['promotion_ids']==old['promotion_ids']
    assert after['confidence_history'][:-1]==old['confidence_history']


@pytest.mark.parametrize('change',[{'skipped_profiles':0},{'public_sources':2},{'accepted_profiles':True},{'skip_reasons':['unknown_completion']},{'confidence':'low'}])
def test_profile_medium_is_not_blanket_promotion(change):
    check=skip_proof();check['result'].update(change)
    assert not R._profile_medium(dict(checks=dict(accepted_public_profiles=check)))
