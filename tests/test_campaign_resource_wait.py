from pathlib import Path
import pytest
from scripts import harbor_campaign as C
from scripts.batch import harbor_admission as A
from test_harbor_admission import setup
from test_harbor_workbook_bootstrap import selected
from test_harbor_campaign import intake,inprocess,start


def test_disk_wait_keeps_exact_checkpoint_then_resumes(intake,inprocess,monkeypatch):
 _,controls,home,initial=start(intake);checkpoint=C.record(home/'admit-step/result.json');s,lease,_=C.selected(initial['descriptor']);tracker=s.bootstrap.policy.tracker_root/'0001.json';before=tracker.read_bytes();original=C._dependency
 def unavailable(*args):raise A.DiskReserveUnavailable(0,s.bootstrap.policy.dependencies.minimum_disk_bytes,{'non_disk_checks':'passed'})
 monkeypatch.setattr(C,'_dependency',unavailable)
 outcome=C.run('step',{'descriptor':initial['descriptor'],'checkpoint':checkpoint,'captures':None},controls,home/'wait')
 result=C.read(C.read(outcome)['result']);assert result['status']=='RESOURCE_WAIT' and tracker.read_bytes()==before
 assert result['lease']==initial['lease'] and result['selection']==initial['selection']
 C._prior(C.read(outcome)['result'],initial['descriptor'])
 # A repeated low-space observation flattens to the same original checkpoint.
 again=C.run('step',{'descriptor':initial['descriptor'],'checkpoint':C.read(outcome)['result'],'captures':None},controls,home/'wait-again')
 value=C.read(C.read(again)['result']);assert C.read(value['resource_wait'])['checkpoint']==checkpoint and tracker.read_bytes()==before
 monkeypatch.setattr(C,'_dependency',original)
 resumed=C.run('step',{'descriptor':initial['descriptor'],'checkpoint':C.read(again)['result'],'captures':None},controls,home/'resumed')
 assert C.read(C.read(resumed)['result'])['status']=='STEP_COMPLETE'

@pytest.mark.parametrize('error',[A.AdmissionError('required distribution missing'),ValueError('disk reserve unavailable')])
def test_only_typed_disk_deficit_is_wait(intake,inprocess,monkeypatch,error):
 _,controls,home,initial=start(intake);checkpoint=C.record(home/'admit-step/result.json')
 def fail(*args):raise error
 monkeypatch.setattr(C,'_dependency',fail)
 outcome=C.run('step',{'descriptor':initial['descriptor'],'checkpoint':checkpoint,'captures':None},controls,home/'failed')
 assert C.read(outcome)['status']=='RECONCILIATION_REQUIRED' and not (home/'failed/resource-wait.json').exists()

@pytest.mark.parametrize('free,expected',[(0,0),(1,1)])
def test_dispatcher_resource_wait_never_spins_below_reserve(free,expected):
 import ast
 from types import SimpleNamespace as NS
 p=Path(__file__).resolve().parents[1]/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v4.py';tree=ast.parse(p.read_text())
 branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="status == 'RESOURCE_WAIT'")
 calls=[];base=NS(selected=lambda *_:(NS(bootstrap=NS(policy=NS(dependencies=NS(minimum_disk_bytes=1)))),None,None),_prior=lambda *a:None,run=lambda *a,**k:calls.append((a,k)))
 loop=ast.For(target=ast.Name(id='_',ctx=ast.Store()),iter=ast.Tuple(elts=[ast.Constant(1)],ctx=ast.Load()),body=branch.body,orelse=[])
 scope=dict(base=base,value={'descriptor':'descriptor'},checkpoint='checkpoint',shutil=NS(disk_usage=lambda _:NS(free=free)),root='root',job={'models':True},controls='controls',directory='directory',fleet_fd=(7,8))
 exec(compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),'<actual-resource-route>','exec'),scope)
 assert len(calls)==expected
 if calls:assert calls[0][1]['guardian_lock_fds']==(7,8) and calls[0][0][1]['checkpoint']=='checkpoint'
