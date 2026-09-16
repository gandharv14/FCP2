from dataclasses import dataclass,replace,asdict
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from test_root_lease_takeover import setup,driver_setup
from test_continuation_allowance import policy
from scripts.batch import harbor_lease_takeover as T
from scripts.batch import harbor_retained_warning_lease as R


def keep(path,value):
    path.write_text(json.dumps(value));return R.record(path)


@dataclass
class Worker:
    warning_requalification:dict
    context:object
    lease_renewal:dict=None


@pytest.fixture
def case(driver_setup,tmp_path):
    s,d,b,original=driver_setup
    _,allowance,_=policy(tmp_path,source=b['bindings']['original_raw_source']['sha256'])
    checkpoints=[]
    for number in range(1,5):
        if checkpoints:s=replace(s,tracker=R.record(s.tracker['path']),prior_completion=checkpoints[-1],checkpoint=checkpoints[-1])
        if number==4:s=replace(s,allowance=allowance)
        current=R.read(s.tracker)
        lease,complete=T.replace_expired(s,d.parent/str(number),clock=lambda:current['leases']['task_agent']['expires_at'])
        checkpoints.append(complete)
    before,after,lease=T.validate_completion(checkpoints[-1])
    prior=R.read(checkpoints[1]);oldtracker=prior['after']
    permit=keep(tmp_path/'permit.json',dict(schema_version='fixture-warning/v1',lease=prior['lease'],tracker=oldtracker,selection={'source':'exact'},blocked={'path':'fixture-original-blocked','sha256':'a'*64},model_calls=0,semantic_budget_reset=False))
    side=keep(tmp_path/'context.json',dict(schema_version=R.SCHEMA,original_warning=permit,permit=permit,blocked=R.read(permit)['blocked'],takeover=checkpoints[-1],selection={'source':'exact'},lease=asdict(lease),semantic_budget_reset=False,Q_authorization_replayed=False))
    calls=[]
    def validate(worker,tracker):
        assert worker.warning_requalification==permit and tracker==R.read(oldtracker)
        calls.append('original_Q_validate');return R.read(permit)
    modules={}
    for name in ('I','W','Q','R','history'):
        path=tmp_path/(name+'.py');path.write_text('# fixture module '+name)
        modules[name]=SimpleNamespace(__file__=str(path))
    I,W,Q,renewal,history=(modules[k] for k in ('I','W','Q','R','history'))
    I._warning_history=lambda *a:a
    Q.SCHEMA='fixture-warning/v1';Q.validate=validate;Q.historical_lease=lambda *a:None
    renewal._retained_lease_adapter=lambda *a:None;history.install=lambda I:None
    runtime=keep(tmp_path/'runtime.json',dict(shared=dict(takeover=R.record(T.__file__),lifecycle=R.record(T.life.__file__))))
    profile=keep(tmp_path/'profile.json',dict(schema_version=R.SCHEMA,adapter=R.record(R.__file__),context=side,original_permit=permit,takeover=checkpoints[-1],module_inventory={k:R.record(modules[k].__file__) for k in ('I','W','Q','R')},instruction_reader=R.record(history.__file__),takeover_runtime=runtime))
    R.install(profile,I=I,W=W,Q=Q,R=renewal,T=T,original_history_reader=history)
    worker=Worker(side,SimpleNamespace(tracker_root=Path(s.tracker['path']).parent))
    return Q,worker,after,permit,calls,checkpoints,profile


def test_original_warning_validated_under_exact_historical_lease_then_projected(case):
    Q,w,after,permit,calls,_,_=case
    old=Path(permit['path']).read_bytes()
    value=Q.validate(w,after)
    assert calls==['original_Q_validate']
    assert value['lease']['token']==after['leases']['task_agent']['token']
    assert value['lease']!=R.read(permit)['lease']
    assert Path(permit['path']).read_bytes()==old and value['model_calls']==0


@pytest.mark.parametrize('mutation',['owner','token','source','drop_history','omit_policy'])
def test_bad_current_custody_cannot_requalify_old_warning(case,mutation):
    import copy
    Q,w,b,permit,calls,_,_=case;after=copy.deepcopy(b)
    if mutation in ('owner','token'):after['leases']['task_agent'][mutation]='foreign'
    elif mutation=='source':after['bindings']['original_raw_source']['sha256']='f'*64
    elif mutation=='drop_history':after['handoffs'].pop(-2)
    else:after['handoffs'][-1].pop('allowance')
    with pytest.raises((ValueError,AssertionError)):Q.validate(w,after)
    assert not calls


def test_tampered_original_warning_does_not_become_new_approval(case):
    Q,w,after,permit,calls,_,_=case
    Path(permit['path']).write_text('{}')
    with pytest.raises(ValueError):Q.validate(w,after)
    assert not calls
