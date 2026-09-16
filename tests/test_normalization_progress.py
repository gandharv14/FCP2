"""Real executor journals and owning normalization, synthetic HTTP only."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import importlib.util
import json
import pytest
from scripts.batch import harbor_normalization_routing as R
from scripts.batch import harbor_normalization_partitioned as P
from scripts.batch import harbor_model_executor as E
from scripts.batch.harbor_admission import read,record
from test_harbor_normalization_producer import setup
from test_normalization_partitioned import respond
from test_normalization_routing import oversize


def test_each_guard_boundary_submits_only_one_call_then_separate_emission(setup,monkeypatch):
    s,c,_,_=setup();oversize(monkeypatch);calls=[];respond(monkeypatch,s,calls=calls)
    first=R.produce(s,c,dispatch=True,credential='synthetic')
    assert isinstance(first,R.Progress) and len(calls)==1
    assert R.check_progress(first.receipt)['ordinal']==1
    assert not (s.run/'phase1-result.json').exists()
    pc=P._prepared_call(first.prepared,0,1)
    journal=E.location(pc,c);original={p:p.read_bytes() for p in journal.iterdir() if p.is_file()}
    second=R.produce(s,c,dispatch=True,credential='synthetic')
    assert isinstance(second,R.Progress) and len(calls)==2
    assert R.check_progress(second.receipt)['index']==1
    assert not (s.run/'phase1-result.json').exists()
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('emission must not submit a model call'))
    final=R.produce(s,c,dispatch=True,credential='synthetic')
    assert isinstance(final,R.Production) and final.prepared==first.prepared==second.prepared
    assert read(final.result)['status']=='phase1_evidence_ready'
    assert all(p.read_bytes()==raw for p,raw in original.items())
    assert read(first.prepared)['original_budget']['attempt_id']==s.budget.attempt_id
    assert read(first.prepared)['logical_limit']==2


def test_lost_progress_write_keeps_completed_call_and_readonly_inspection_never_replays(setup,monkeypatch):
    s,c,_,_=setup();oversize(monkeypatch);calls=[];respond(monkeypatch,s,calls=calls)
    original=R.progress_receipt
    monkeypatch.setattr(R,'progress_receipt',lambda *a:(_ for _ in ()).throw(OSError('synthetic fsync failure')))
    with pytest.raises(OSError):R.produce(s,c,dispatch=True,credential='synthetic')
    prepared=record(R.partition_root(s)/'prepared.json');pc=P._prepared_call(prepared,0,1)
    assert E.inspect(pc,c).status=='completed'
    monkeypatch.setattr(R,'progress_receipt',original)
    assert R.produce(s,c,dispatch=False) is None and len(calls)==1
    # Explicit later continuation may inspect that completed call and dispatch
    # only the next partition. A failed owning guard is never auto-retried.
    result=R.produce(s,c,dispatch=True,credential='synthetic')
    assert R.check_progress(result.receipt)['index']==1 and len(calls)==2


@pytest.mark.parametrize('field,value',[('ordinal',3),('logical_limit',3),('lane_approval',True),
    ('semantic_budget_reset',True),('automatic_retry',True),('outcome',{'path':'/absent','sha256':'0'*64})])
def test_changed_progress_cannot_authorize_boundary(setup,monkeypatch,field,value):
    s,c,_,_=setup();oversize(monkeypatch);respond(monkeypatch,s)
    result=R.produce(s,c,dispatch=True,credential='synthetic');path=Path(result.receipt['path'])
    body=read(result.receipt);body[field]=value;path.write_text(json.dumps(body))
    with pytest.raises((ValueError,OSError)):R.check_progress(record(path))


@pytest.fixture
def driver():
    path=Path(__file__).resolve().parents[1]/'scripts/fleet/advance_authoring_v8.py'
    spec=importlib.util.spec_from_file_location('progress_dispatcher',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_ordinary_driver_verifies_completed_progress_without_models_and_same_guards(setup,monkeypatch,driver):
    s,c,_,_=setup();c=replace(c,model_slots=1);monkeypatch.setenv('HARBOR_MODEL_SLOTS','1')
    oversize(monkeypatch);respond(monkeypatch,s)
    p=R.produce(s,c,dispatch=True,credential='synthetic')
    worker=NS(normalization_budget=s.budget,bootstrap=NS(workbook=s.workbook),campaign=c)
    base=NS(selected=lambda _: (worker,None,None),SECONDS=3300,MEMORY=8*1024**3)
    env=(base,);value=dict(status='PENDING_MODEL',step='normalization_phase1',descriptor={},evidence=[p.receipt])
    monkeypatch.setattr(E,'_open',lambda *a,**k:pytest.fail('driver inspection must not dispatch'))
    driver.normalization_progress(env,value)
    for field,changed in [('SECONDS',3301),('MEMORY',16*1024**3)]:
        old=getattr(base,field);setattr(base,field,changed)
        with pytest.raises(ValueError,match='stage guards'):driver.normalization_progress(env,value)
        setattr(base,field,old)


def test_ordinary_dispatcher_uses_distinct_guarded_steps_then_stops_on_uncertainty(driver,tmp_path,monkeypatch):
    import contextlib
    config={'workbook':'9472','run_root':str(tmp_path/'task')};controls=object();calls=[]
    results={'initial':{'status':'ADMITTED','descriptor':{}},
        'completed-progress':{'status':'PENDING_MODEL','step':'normalization_phase1','descriptor':{}},
        'uncertain':{'status':'UNRESOLVED','descriptor':{}}}
    def run(action,args,observed_controls,directory,**kwargs):
        calls.append((action,args,observed_controls,directory,kwargs))
        return 'completed-progress' if len(calls)==1 else 'uncertain'
    base=NS(run=run,SECONDS=3300,MEMORY=8*1024**3);C=NS(SCHEMA='instruction')
    env=(base,C,None,None,None,config,tmp_path,controls)
    job=dict(outcome='initial',operations=str(tmp_path/'task/dispatcher-v2/test'),maximum_steps=10,models=True)
    monkeypatch.setattr(driver,'checked',lambda _:job)
    monkeypatch.setattr(driver,'setup',lambda _:env)
    monkeypatch.setattr(driver,'inspect_prior',lambda outcome,*_: (outcome,results[outcome]))
    monkeypatch.setattr(driver,'fleet_slot',lambda _:contextlib.nullcontext((10,11)))
    driver.run({'synthetic':'job'})
    assert len(calls)==2
    assert [call[3].name for call in calls]==['000','001']
    assert [call[1]['checkpoint'] for call in calls]==['initial','completed-progress']
    assert all(call[4]==dict(dispatch=True,guardian_lock_fds=(10,11)) for call in calls)
    hold=json.loads((Path(job['operations'])/'hold.json').read_text())
    assert hold['outcome']=='uncertain' and hold['automatic_retry'] is False
