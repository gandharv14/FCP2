from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,time
import pytest
from scripts.batch import harbor_allowance_history as H
from scripts.batch import harbor_lease_takeover as T
from test_root_lease_takeover import setup,driver_setup
from test_continuation_allowance import policy

def binding(path):return T.record(path)
def keep(path,value):path.write_text(json.dumps(value));return binding(path)

def fixture_load(binding):
    p=json.loads(checked(binding))
    before=json.loads(checked(p['tracker']))
    T._limits(T._selection(p['selection']),before,at=time.time_ns())
    return p,T,T._selection(p['selection']),{'fixture':'exact owning T'}

@pytest.fixture
def case(driver_setup,tmp_path,monkeypatch):
    s,d,b,_=driver_setup
    _,allowance,_=policy(tmp_path,source=b['bindings']['original_raw_source']['sha256'])
    completions=[]
    for number in range(1,5):
        if completions:s=replace(s,tracker=binding(s.tracker['path']),prior_completion=completions[-1],checkpoint=completions[-1])
        if number==4:s=replace(s,allowance=allowance)
        before=T.read(s.tracker)
        _,complete=T.replace_expired(s,d.parent/str(number),clock=lambda:before['leases']['task_agent']['expires_at'])
        completions.append(complete)
    plan=keep(tmp_path/'runtime-plan.json',dict(tracker=s.tracker,allowance=allowance,selection=T._selection_value(s)))
    runtime=keep(tmp_path/'runtime.py.json',{'selected':'fixture'})
    monkeypatch.setattr(H,'module',lambda row:(H.checked(row),SimpleNamespace(load=fixture_load,checked=H.checked))[1])
    return runtime,plan,complete,s

def test_completed_runtime_uses_exact_archive_after_live_tracker_changed(case):
    runtime,plan,complete,s=case
    # The current file is the real T4 after; it no longer matches the pre-T4 hash.
    assert binding(s.tracker['path'])!=s.tracker
    raw=Path(s.tracker['path']).read_bytes()
    p,selected,lease,witness=H.load(runtime,plan,complete)
    assert lease==T.validate_completion(complete)[2]
    assert selected is T and witness['eligibility_reapplied'] is False
    assert Path(s.tracker['path']).read_bytes()==raw

@pytest.mark.parametrize('field',['tracker','allowance'])
def test_unrelated_plan_cannot_project_completed_history(case,tmp_path,field):
    runtime,plan,complete,s=case;p=T.read(plan)
    p[field]=dict(p[field],sha256='a'*64)
    wrong=keep(tmp_path/'wrong.json',p)
    with pytest.raises(ValueError):H.load(runtime,wrong,complete)

def test_corrupt_archived_copy_fails_even_though_current_tracker_valid(case):
    runtime,plan,complete,s=case
    stop=T.read(T.read(complete)['stopped_owner_proof'])
    Path(stop['before']['path']).write_text('{}')
    with pytest.raises(ValueError):H.load(runtime,plan,complete)

def test_selection_change_rejected_by_original_t_validator(case,tmp_path):
    runtime,plan,complete,s=case;p=T.read(plan)
    p['selection']['duration_ns']+=1
    wrong=keep(tmp_path/'different-selection.json',p)
    with pytest.raises(ValueError):H.load(runtime,wrong,complete)

def fixture_direct_load(binding):
    p=json.loads(checked(binding))
    if p.get('schema_version')=='harbor-allowance-takeover-direct/v1':return direct(p)
    raise ValueError('not direct')

def fixture_direct(p):
    s=T._selection(p['selection']);before=json.loads(checked(s.tracker))
    T._limits(s,before,at=time.time_ns())
    return p,T,s,{'fixture':'original direct T'}

def test_direct_completed_runtime_clones_its_actual_separate_globals(case,tmp_path,monkeypatch):
    runtime,old,complete,s=case
    p=dict(schema_version='harbor-allowance-takeover-direct/v1',allowance=T.read(old)['allowance'],selection=T._selection_value(s))
    plan=keep(tmp_path/'direct.json',p)
    monkeypatch.setattr(H,'module',lambda row:(H.checked(row),SimpleNamespace(load=fixture_direct_load,direct=fixture_direct,checked=H.checked))[1])
    original_globals=dict(fixture_direct.__globals__);before=Path(s.tracker['path']).read_bytes()
    _,selected,lease,witness=H.load(runtime,plan,complete)
    assert selected is T and lease==T.validate_completion(complete)[2]and witness['eligibility_reapplied']is False
    assert fixture_direct.__globals__==original_globals and Path(s.tracker['path']).read_bytes()==before
    p['selection']['tracker']['path']=str(tmp_path/'wrong-live-tracker.json')
    with pytest.raises(ValueError,match='pre-takeover tracker'):H.load(runtime,keep(tmp_path/'wrong-direct.json',p),complete)
