"""Early startup renewal preserves exact custody before expensive quality work."""
import ast, copy, time
from pathlib import Path
from types import SimpleNamespace as N
import pytest


def functions():
    source=Path('scripts/harbor_instruction_campaign.py').read_text()
    tree=ast.parse(source)
    return [x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name in {'_startup_lease','_start'}]


def case(tmp_path):
    calls=[];source={'path':'/source.xlsx','sha256':'source'};selection=N(content=N(workbook='0496',bindings={'original_raw_source':source},stage=tmp_path/'stage'))
    s=N(bootstrap=N(workbook='0496',run=tmp_path/'bootstrap',policy=N(tracker_root=tmp_path/'trackers')),run=tmp_path/'environment',campaign=N(root=tmp_path/'campaign',slot_root=tmp_path/'slots'))
    before=dict(workbook_id='0496',bindings={'original_raw_source':source},leases={'task_agent':{'expires_at':time.time_ns()+3600*10**9}})
    evidence={'path':'/actual/environment-checkpoint.json','sha256':'actual'};prior=dict(selection=selection,evidence=[evidence]);old=object();new=object()
    values={'descriptor':{}}
    def check(row):calls.append(('check',row))
    def renew(*args):calls.append(('renew',args));return new
    base=N(worker=N(_active=lambda *args:(None,before),_read_checkpoint=lambda *args:selection),executor=N(checked_bytes=check),_lease=renew)
    def require(ok,message):
        if not ok:raise ValueError(message)
    env=dict(base=base,read=lambda row:values[row],record=lambda path:str(path),codec=N(encode=lambda x:x,decode=lambda x:x),Path=Path,time=time,require=require)
    exec(compile(ast.Module(body=functions(),type_ignores=[]),'<original-start>','exec'),env)
    args=(s,'descriptor',prior,selection,old,tmp_path/'instruction',tmp_path/'child')
    return env,args,before,calls,new


def test_source_checkpoint_and_evidence_precede_original_renewal(tmp_path):
    env,args,before,calls,new=case(tmp_path)
    assert env['_startup_lease'](*args)is new
    assert [x[0]for x in calls]==['check','check','renew']
    assert calls[-1][1]==(args[0],args[4],args[-1])

@pytest.mark.parametrize('fault',['source','workbook','expired','short','checkpoint','namespace','evidence'])
def test_changed_or_expired_start_never_renews(tmp_path,fault):
    env,args,before,calls,_=case(tmp_path)
    if fault=='source':before['bindings']['original_raw_source']={'foreign':True}
    if fault=='workbook':before['workbook_id']='0001'
    if fault=='expired':before['leases']['task_agent']['expires_at']=time.time_ns()-1
    if fault=='short':before['leases']['task_agent']['expires_at']=time.time_ns()+30*10**9
    if fault=='checkpoint':env['base'].worker._read_checkpoint=lambda *args:object()
    if fault=='namespace':args[-2].mkdir()
    if fault=='evidence':
        def changed(row):raise ValueError('changed retained evidence')
        env['base'].executor.checked_bytes=changed
    with pytest.raises(ValueError):env['_startup_lease'](*args)
    assert not any(x[0]=='renew'for x in calls)


def test_original_cas_failure_propagates(tmp_path):
    env,args,before,calls,_=case(tmp_path)
    def stale(*a):raise RuntimeError('original CAS changed')
    env['base']._lease=stale
    with pytest.raises(RuntimeError,match='original CAS'):env['_startup_lease'](*args)


def test_expensive_validation_stays_after_early_renewal():
    start=next(x for x in functions()if x.name=='_start')
    lines=ast.unparse(start)
    assert lines.index('_startup_lease(')<lines.index('refreshed.validate(')
    assert lines.index('refreshed.validate(')<lines.index('check_completed_gate(')
    assert 'base._lease('not in lines
