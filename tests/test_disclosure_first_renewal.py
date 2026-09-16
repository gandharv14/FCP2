from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
import json
import pytest
from scripts.fleet import disclosure_command_continue as D,continuation_lease as L
from scripts.batch import harbor_tracker_lifecycle as life,harbor_instruction_worker as W
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup

def put(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return D.binding(path)

@pytest.mark.parametrize('fault',[None,'central','changed_owner','expired'])
def test_original_renewal_precedes_expensive_context_and_races_fail_closed(failed,tmp_path,monkeypatch,fault):
 worker,selection,lease,blocked,task,*_=failed;tracker=Path(task.tracker['path']);before=tracker.read_bytes();home=tmp_path/'new';evidence=put(tmp_path/'evidence.json',{'actual':'retained'})
 p=dict(prepermit_renewal_first=True,output=str(home),helper=D.binding(Path(D.__file__).resolve()),tracker=D.binding(tracker),context={'plan':evidence},prepermit_failure=evidence,source=evidence,profile=evidence)
 row=put(tmp_path/'plan.json',p);profile=dict(worker_run=str(worker.run),completion=evidence);calls=[]
 def custody(*a):
  calls.append('custody')
  if fault=='central':raise ValueError('central custody refused')
  if fault=='changed_owner'and len(calls)==2:
   v=D.read(D.binding(tracker));v['leases']['task_agent']['owner']='foreign';tracker.write_text(json.dumps(v))
 def validate():
  assert D.binding(tracker)==p['tracker'];return lease
 actualL=L
 if fault=='expired':
  actualL=SimpleNamespace(renew=lambda **kw:L.renew(**kw,clock=lambda:D.read(D.binding(tracker))['leases']['task_agent']['expires_at']+1))
 @contextmanager
 def authority(selected):
  assert selected==row
  yield p,SimpleNamespace(custody=custody,keep=W.keep),profile,SimpleNamespace(life=life),actualL,lease,worker.root,validate
 monkeypatch.setattr(D,'first_renewal_authority',authority)
 class NextBoundary(Exception):pass
 @contextmanager
 def context(selected,*,permit=False):
  assert selected==row and permit is True
  completion=D.read(D.binding(home/'renewal/complete.json'))
  assert completion['previous']==asdict(lease)and completion['lease']['revision']==lease.revision+1
  assert (home/'renewal/before.json').read_bytes()==before
  assert D.binding(home/'renewal/after.json')['sha256']==D.binding(tracker)['sha256']
  calls.append('full_original_context');raise NextBoundary()
  yield
 monkeypatch.setattr(D,'context',context)
 if fault:
  with pytest.raises((ValueError,AssertionError)):D.run(row)
  assert 'full_original_context'not in calls and not(home/'renewal/complete.json').exists()
  if fault!='changed_owner':assert tracker.read_bytes()==before
 else:
  with pytest.raises(NextBoundary):D.run(row)
  assert calls[-1]=='full_original_context'
  assert not(home/'permit').exists()
  with pytest.raises(ValueError,match='already attempted'):D.run(row)
