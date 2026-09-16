import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import retained_pre_dispatch_context as M
from scripts.fleet import instruction_semantic_continue as U


def fixture(tmp_path):
    def keep(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
    ops=tmp_path/'ops';ops.mkdir();driver=keep(tmp_path/'driver.json',{});descriptor=keep(tmp_path/'descriptor.json',{});blocked=keep(tmp_path/'blocked.json',{})
    cp=keep(tmp_path/'prior.json',dict(status='LEASE_RENEWAL_REQUIRED',descriptor=descriptor));oldout=keep(tmp_path/'prior-outcome.json',dict(result=cp));job=dict(operations=str(ops),outcome=oldout,driver=driver,maximum_steps=100);jr=keep(tmp_path/'job.json',job);keep(ops/'job.json',job)
    for i,status in enumerate(('RETRY_READY','DIALOGUE_REPAIR_REQUIRED')):
        d=ops/('%03d'%i);d.mkdir();intent=dict(job=jr,checkpoint=cp,seconds=900,driver=driver)if i==0 else dict(action='step',arguments=dict(descriptor=descriptor,checkpoint=cp))
        ir=keep(d/'intent.json',intent);gr=keep(d/'resource.json',dict(intent=ir));value=dict(status=status,descriptor=descriptor,next_step='dialogue-review',checkpoint=blocked);cp=keep(d/'result.json',value);out=keep(d/'outcome.json',dict(status='COMPLETED_OPERATION',resource=gr,result=cp))
    keep(ops/'hold.json',dict(outcome=out));p=dict(original_job=jr,outcome=out,result=cp,descriptor=descriptor,blocked=blocked);calls=[]
    def successful(row,env):
        assert row!=p['outcome'],'quality hold must never be treated as resumable'
        calls.append(row);r=M.read(row)['result'];return r,M.read(r)
    def rejected(plan,I,controls):
        assert plan is p;calls.append('original completed_failure');return value,None,None
    return p,dict(operations=str(ops)),SimpleNamespace(successful=successful),SimpleNamespace(completed_failure=rejected),job,keep,calls


def test_completed_rejection_keeps_renewal_and_original_quality_validator(tmp_path):
    p,old,D,utilities,job,_,calls=fixture(tmp_path)
    assert M.completed_prefix(utilities,p,old,D,(None,)*8,job)[0]==p['result']
    assert len(calls)==3 and calls[-1]=='original completed_failure'


@pytest.mark.parametrize('fault',['first_job','first_driver','first_checkpoint','seconds','step_checkpoint','step_descriptor','extra_argument','extra_file','last_outcome','hold'])
def test_no_skipped_guard_or_foreign_renewal_can_enter_second_round(tmp_path,fault):
    p,old,D,utilities,job,keep,_=fixture(tmp_path);root=Path(old['operations'])
    if fault=='extra_file':keep(root/'unexplained.json',{})
    elif fault=='last_outcome':p['outcome']=job['outcome']
    elif fault=='hold':keep(root/'hold.json',dict(outcome=job['outcome']))
    else:
        d=root/('000'if fault.startswith('first')or fault=='seconds'else'001');v=M.read(M.binding(d/'intent.json'))
        if fault.startswith('first'):v[dict(first_job='job',first_driver='driver',first_checkpoint='checkpoint')[fault]]=p['blocked']
        elif fault=='seconds':v['seconds']=3600
        elif fault=='extra_argument':v['arguments']['captures']=None
        else:v['arguments']['checkpoint'if fault=='step_checkpoint'else'descriptor']=p['blocked']
        ir=keep(d/'intent.json',v);gr=keep(d/'resource.json',dict(intent=ir));out=M.read(M.binding(d/'outcome.json'));out['resource']=gr;row=keep(d/'outcome.json',out)
        if d.name=='001':p['outcome']=row;keep(root/'hold.json',dict(outcome=row))
    with pytest.raises((ValueError,AssertionError)):M.completed_prefix(utilities,p,old,D,(None,)*8,job)


def test_only_explicit_completed_kinds_can_use_retained_manager():
    assert U.completed_disclosure_context(dict(retained_context=dict(kind='completed_pre_dispatch')))
    assert U.completed_disclosure_context(dict(retained_context=dict(kind='completed_disclosure')))
    assert not U.completed_disclosure_context(dict(retained_context=dict(kind='generic')))
    assert not U.completed_disclosure_context({})


def test_completed_context_never_enters_initial_custody():
    with pytest.raises(ValueError,match='history only'):M.context({},initial=True)

@pytest.mark.parametrize('status',['MODEL_RECONCILIATION_REQUIRED','DIALOGUE_REPAIR_REQUIRED','STEP_COMPLETE'])
def test_http_prefix_uses_separate_closed_terminal_without_semantic_pass(tmp_path,status):
    p,old,D,utilities,job,keep,calls=fixture(tmp_path)
    value=M.read(p['result']);value.update(status=status,next_step='disclosure-review')
    p['result']=keep(Path(p['result']['path']),value)
    out=M.read(p['outcome']);out['result']=p['result'];p['outcome']=keep(Path(p['outcome']['path']),out)
    keep(Path(old['operations'])/'hold.json',dict(outcome=p['outcome']))
    used=[]
    def terminal():used.append('sealed HTTP validator');return value,None,None
    if status=='MODEL_RECONCILIATION_REQUIRED':
        assert M.completed_prefix(utilities,p,old,D,(None,)*8,job,terminal=terminal)[1]==value
        assert used==['sealed HTTP validator']and 'original completed_failure'not in calls
    else:
        with pytest.raises(ValueError):M.completed_prefix(utilities,p,old,D,(None,)*8,job,terminal=terminal)


def test_http_reader_cannot_start_initial_work():
    with pytest.raises(ValueError,match='completed stopped'):
        with M.cold_context({},continued=False):pass
