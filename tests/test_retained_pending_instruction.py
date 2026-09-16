import json
from pathlib import Path
from types import FunctionType
import pytest
from scripts.fleet import retained_pending_instruction as M


def keep(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value));return M.binding(path)


def fixture(tmp_path):
    descriptor=keep(tmp_path/'descriptor.json',{})
    lease={'owner':'old','token':'native','revision':7,'sha256':'original'}
    prior=keep(tmp_path/'prior.json',dict(selection='selected',lease=lease))
    d=tmp_path/'instruction/steps/disclosure-review'
    wi=keep(d/'intent.json',dict(previous=prior,step='disclosure-review',selection='selected',lease=lease))
    pending=keep(d/'pending-0000.json',dict(intent=wi,status='MODEL_REQUIRED',step='disclosure-review',selection='selected',lease=lease))
    for name in ('request.json','manifest.json','instruction.snapshot.md'):keep(d/name,{})
    result=keep(tmp_path/'prepared/result.json',dict(status='MODEL_REQUIRED',next_step='disclosure-review',descriptor=descriptor,checkpoint=pending,publication_performed=False,selection='selected'))
    intent=keep(tmp_path/'prepared/intent.json',dict(action='step',models=False,commands=False,containers=False,arguments=dict(descriptor=descriptor)))
    guard=keep(tmp_path/'prepared/guard.json',dict(intent=intent,returncode=0,failure=None))
    outcome=keep(tmp_path/'prepared/outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=guard))
    fi=keep(tmp_path/'failed/intent.json',dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=result)))
    fg=keep(tmp_path/'failed/guard.json',dict(intent=fi,returncode=1,failure=None))
    failed=keep(tmp_path/'failed/outcome.json',dict(status='RECONCILIATION_REQUIRED',result=None,resource=fg))
    p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,helper=M.binding(M.__file__),outcome=outcome,result=result,descriptor=descriptor,pending=pending,directory=str(d),tree={x.name:M.binding(x)['sha256']for x in d.iterdir()},predecessor=prior,failed=failed)
    return p


def test_actual_model_preparation_is_not_completed_review(tmp_path):
    p=fixture(tmp_path);v,pending,*_=M.retained(p)
    assert v['status']==pending['status']=='MODEL_REQUIRED'


@pytest.mark.parametrize('name',['exception.json','blocked.json','complete.json','pending-0001.json','unsealed.txt'])
def test_later_or_uncertain_step_effects_refuse_historical_reentry(tmp_path,name):
    p=fixture(tmp_path);keep(Path(p['directory'])/name,{})
    with pytest.raises(ValueError,match='tree changed'):M.retained(p)


def test_changed_retained_pending_rejected_even_with_rebound_profile(tmp_path):
    p=fixture(tmp_path);pending=M.read(p['pending']);pending['lease']['revision']+=1
    p['pending']=keep(Path(p['pending']['path']),pending)
    with pytest.raises(ValueError):M.retained(p)


@pytest.mark.parametrize('flag',['models','commands','containers'])
def test_preparation_must_have_no_owning_effect_allowance(tmp_path,flag):
    p=fixture(tmp_path);out=M.read(p['outcome']);g=M.read(out['resource']);i=M.read(g['intent']);i[flag]=True
    g['intent']=keep(Path(g['intent']['path']),i);out['resource']=keep(Path(out['resource']['path']),g);p['outcome']=keep(Path(p['outcome']['path']),out)
    with pytest.raises(ValueError,match='zero-effect'):M.retained(p)


def test_failed_guard_must_name_actual_prepared_checkpoint(tmp_path):
    p=fixture(tmp_path);out=M.read(p['failed']);g=M.read(out['resource']);i=M.read(g['intent']);i['arguments']['checkpoint']=p['predecessor']
    g['intent']=keep(Path(g['intent']['path']),i);out['resource']=keep(Path(out['resource']['path']),g);p['failed']=keep(Path(p['failed']['path']),out)
    with pytest.raises(ValueError,match='detached'):M.retained(p)


def test_exact_ast_projection_keeps_other_assertions_and_module_side_effects(tmp_path):
    path=tmp_path/'original.py';path.write_text("calls=[]\ndef require(ok,message):\n if not ok:raise ValueError(message)\ndef boundary(value):\n require(value>0,'original')\n require(value==1,'pending')\n calls.append(value)\n return value\n")
    original=M.module('test_pending_original',M.binding(path));seen=[]
    selected=M.changed_function(original.boundary,"require(value==1,'pending')","require(value==1 or authorized(value),'pending')",{'authorized':lambda v:seen.append(v)or v==2})
    assert selected(2)==2 and original.calls==[2]and seen==[2]
    with pytest.raises(ValueError,match='original'):selected(0)
    with pytest.raises(ValueError,match='pending'):original.boundary(2)
    assert original.calls==[2]


def test_changed_loaded_function_and_missing_target_reject(tmp_path):
    path=tmp_path/'original.py';path.write_text('def boundary(value):\n return int(value)\n')
    original=M.module('test_pending_changed',M.binding(path))
    with pytest.raises(ValueError,match='one exact'):M.changed_function(original.boundary,'str(value)','float(value)',{})
    other=compile('def boundary(value):\n return value+1\n',str(path),'exec');code=next(x for x in other.co_consts if hasattr(x,'co_code'))
    changed=FunctionType(code,original.__dict__)
    with pytest.raises(ValueError,match='loaded'):M.changed_function(changed,'int(value)','float(value)',{})

@pytest.mark.parametrize('fault',[None,'extra','failed_guard','different_intent','wrong_phase'])
def test_completed_preparation_requires_actual_closed_successor(tmp_path,fault):
    p=fixture(tmp_path);d=Path(p['directory']);wi=M.read(p['pending'])['intent']
    complete=keep(d/'complete.json',dict(status='completed',step='disclosure-review',intent=wi,selection='reviewed'))
    value=dict(status='STEP_COMPLETE',next_step='disclosure-commit',checkpoint=complete,publication_performed=False,selection='reviewed')
    if fault=='wrong_phase':value['next_step']='nat-writer'
    if fault=='different_intent':complete=keep(d/'complete.json',dict(status='completed',step='disclosure-review',intent={},selection='reviewed'));value['checkpoint']=complete
    result=keep(tmp_path/'completed/result.json',value)
    intent=keep(tmp_path/'completed/intent.json',dict(action='step',models=True))
    guard=keep(tmp_path/'completed/guard.json',dict(intent=intent,returncode=1 if fault=='failed_guard'else 0,failure=None))
    outcome=keep(tmp_path/'completed/outcome.json',dict(status='COMPLETED_OPERATION',result=result,resource=guard))
    if fault=='extra':keep(d/'pending-0001.json',{})
    if fault:
        with pytest.raises(ValueError):M.completed_record(p,outcome)
    else:
        assert M.completed_record(p,outcome)==value
        with pytest.raises(ValueError,match='tree changed'):M.retained(p)
