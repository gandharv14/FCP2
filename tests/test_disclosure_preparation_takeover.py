from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_lease_takeover as T,harbor_tracker_lifecycle as L,harbor_continuation_allowance as A
from scripts.fleet import disclosure_review_prepare as P,instruction_semantic_continue as U,retained_runtime as RT
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,save


@pytest.mark.parametrize('fault',[None,'old_lease','current_tracker','owner','checkpoint','before_archive','completion'])
def test_actual_takeover_history_and_live_custody_both_required(setup,tmp_path,monkeypatch,fault):
    selected,d,before,previous=setup
    descriptor=save(tmp_path/'descriptor.json',{})
    guard=read(selected.guard);intent=read(guard['intent'])
    intent.update(action='step',arguments=dict(descriptor=descriptor,checkpoint=selected.checkpoint))
    guard['intent']=save(Path(guard['intent']['path']),intent);selected=replace(selected,guard=save(Path(selected.guard['path']),guard))
    current,done=T.replace_expired(selected,d,clock=lambda:120)
    outcome=save(tmp_path/'outcome.json',dict(resource=selected.guard))
    tp=save(tmp_path/'takeover-plan.json',dict(schema_version='harbor-allowance-takeover-direct/v1',selection=T._selection_value(selected),shared=dict(takeover=record(T.__file__),lifecycle=record(L.__file__),allowance=record(A.__file__))))
    loader=tmp_path/'loader.py';loader.write_text('def tail_context(*a, **k): pass\n');oldplan=save(tmp_path/'old-plan.json',{})
    deps=dict(schema_version=RT.SCHEMA,runtime=record(RT.__file__),entrypoint=record(loader),read_call=dict(function='tail_context',args=[oldplan],kwargs=dict(continued=True)),patches=[],guard_modules=[],child_plans=[],journal_roots=[],symbols=[],dependencies=[dict(module_name=n,original=record(m.__file__),selected=record(m.__file__))for n,m in [('scripts.batch.harbor_tracker_lifecycle',L),('scripts.batch.harbor_lease_takeover',T)]])
    profile=save(tmp_path/'dependencies.json',deps)
    c=dict(utilities=record(U.__file__),takeover=dict(runtime=record(T.__file__),plan=tp,completion=done,dependency_runtime=record(RT.__file__),dependency_profile=profile),tracker=record(selected.tracker['path']),owner=current.owner)
    p=dict(completed_custody=c,build=dict(outcome=outcome,descriptor=descriptor),workbook=selected.workbook,source=before['bindings']['original_raw_source'],loader=dict(helper=record(loader),plan=oldplan,function='tail_context',kwargs=dict(continued=True)))
    w=SimpleNamespace(context=SimpleNamespace(tracker_root=Path(selected.tracker['path']).parent));calls=[]
    def live(w,s,l):
        value=read(record(selected.tracker['path']));L.validate_lifecycle(value)
        assert (value['revision'],record(selected.tracker['path'])['sha256'])==(l.revision,l.sha256)
        assert (value['leases']['task_agent']['owner'],value['leases']['task_agent']['token'])==(l.owner,l.token)
        calls.append(l)
    env=(None,None,None,SimpleNamespace(active=live))
    if fault=='old_lease':previous=replace(previous,token='foreign')
    elif fault=='current_tracker':Path(selected.tracker['path']).write_text('{}')
    elif fault=='owner':c['owner']='foreign'
    elif fault=='checkpoint':p['build']['descriptor']=save(tmp_path/'foreign-descriptor.json',{})
    elif fault=='before_archive':Path(read(read(done)['stopped_owner_proof'])['before']['path']).write_text('{}')
    elif fault=='completion':c['takeover']['completion']={**done,'sha256':'f'*64}
    if fault:
        with pytest.raises((ValueError,FileNotFoundError,AssertionError)):
            with P.completed_custody({},p,env,w,None,previous):pytest.fail('foreign custody opened')
    else:
        with P.completed_custody({},p,env,w,None,previous)as actual:assert actual==current
        assert calls==[current] and current.owner!=previous.owner and current.token!=previous.token
    assert not (tmp_path/'review').exists()


def test_tail_invocation_names_exact_retained_plan():
    row=dict(path='/bound/old-plan.json',sha256='a'*64)
    p=dict(retained_context=dict(plan=row),retained_read_call=dict(function='tail_context',args=[row],kwargs=dict(continued=True)))
    assert U.retained_invocation(p)==p['retained_read_call']
    p['retained_read_call']['args']=[dict(row,sha256='b'*64)]
    with pytest.raises(ValueError):U.retained_invocation(p)
