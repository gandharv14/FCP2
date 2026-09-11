"""Invocation state tests only; these do not simulate a passed financial gate."""
import importlib.util
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.batch import harbor_completed_lanes as r

def tracker():return {'lane_state':{lane:{'state':'pending','phase':None} for _,lane in r.STEPS}}

def advance(t,kind,lane):
    t['lane_state'][lane]['state']='running' if kind=='normalization_phase1' else 'passed'
    if kind.startswith('normalization_'):t['lane_state'][lane]['phase']=kind.removeprefix('normalization_')+'_complete'

def test_exact_eleven_invocations_complete_ten_lanes_in_order():
    t=tracker()
    for kind,lane in r.STEPS:
        assert r.predecessors(t,kind)==lane
        advance(t,kind,lane)
    assert len(t['lane_state'])==10 and all(row['state']=='passed' for row in t['lane_state'].values())

@pytest.mark.parametrize('kind',[kind for kind,_ in r.STEPS[1:]])
def test_no_step_skips_required_prior_lane(kind):
    with pytest.raises(ValueError):r.predecessors(tracker(),kind)

def test_source_profile_needs_phase1_not_merely_normalization_pass_flag():
    t=tracker()
    for kind,lane in r.STEPS[:3]:advance(t,kind,lane)
    t['lane_state']['harbor-normalization-engineer']['state']='passed'
    with pytest.raises(ValueError,match='phase1'):r.predecessors(t,'source_profile')

def test_phase2_needs_frozen_phase1_and_completed_profile():
    t=tracker()
    for kind,lane in r.STEPS[:4]:advance(t,kind,lane)
    with pytest.raises(ValueError):r.predecessors(t,'normalization_phase2')
    advance(t,*r.STEPS[4]);assert r.predecessors(t,'normalization_phase2')=='harbor-normalization-engineer'
    t['lane_state']['harbor-normalization-engineer']['phase']=None
    with pytest.raises(ValueError):r.predecessors(t,'normalization_phase2')

def test_unknown_or_alias_lane_is_closed():
    for kind in ['normalization','passed','quality_gate']:
        with pytest.raises(ValueError):r.predecessors(tracker(),kind)
