from pathlib import Path
from dataclasses import dataclass,asdict
from types import SimpleNamespace
import importlib.util,json
import pytest
from scripts.batch import harbor_disclosure_rejection_history as H

def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return H.binding(path)

@pytest.mark.parametrize('fault',[None,'time','owner','source','stage','archive','worker','receipt'])
@pytest.mark.parametrize('reserved',[False,True])
def test_archived_check_uses_real_admission_and_preserves_source_gates(tmp_path,fault,reserved):
 code=tmp_path/'worker.py';code.write_text('''import json,time
from pathlib import Path
class Facade:
 context=None
 def require_synchronized(self,wb):
  path=self.context.tracker_root/(wb+'.json')
  tracker=json.loads(path.read_bytes())
  assert tracker['revision']==3, 'notes tracker changed'
 def _active(self,before,raw,revision,sha,owner,token,scope):
  assert before['revision']==revision
  assert (before['leases']['task_agent']['owner'],before['leases']['task_agent']['token'])==(owner,token)
  assert before['leases']['task_agent']['heartbeat_at']<=time.time_ns()<before['leases']['task_agent']['expires_at']
def record(path):return {'live':str(path)}
def active(worker,selection,lease):
 facade=Facade();wb=selection.workbook;facade.require_synchronized(wb)
 path=worker.context.tracker_root/(wb+'.json')
 raw=path.read_bytes();before=json.loads(raw)
 facade._active(before, raw, lease.revision, lease.sha256, lease.owner, lease.token, 'workbook:'+wb)
 assert selection.stage_valid, 'source stage changed'
 return facade,before,record(path)
''')
 spec=importlib.util.spec_from_file_location('test_archived_worker',code);W=importlib.util.module_from_spec(spec);spec.loader.exec_module(W)
 @dataclass(frozen=True)
 class Lease:
  owner:str
  token:str
  revision:int
  sha256:str
 tracker=tmp_path/'live/0111.json';source={'sha256':'original'}
 before=dict(revision=3,bindings=dict(original_raw_source=source),leases=dict(task_agent=dict(owner='old',token='secret',heartbeat_at=1,expires_at=10000*10**9)))
 archive=save(tmp_path/'archive.json',before);lease=Lease('old','secret',3,archive['sha256']);bound=dict(path=str(tracker),sha256=archive['sha256'])
 run=save(tmp_path/'plan.json',{});selected=dict(output=str(tmp_path/'round'),workbook='0111',source=source)
 receipt=save(tmp_path/'round/initial-lease/retained-live.json',dict(schema_version='fleet-retained-live-continuation-lease/v1',authority=run,lease=asdict(lease),tracker=bound,minimum_remaining_ns=7200*10**9,lease_operations=0,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False,observed_at_ns=100*10**9,validated_evidence=[bound]))
 admission=save(tmp_path/'round/batches/0000/admission.json',dict(tracker=bound,transport_started=False,required_remaining_seconds=660,observed_at_ns=200*10**9))
 p=dict(worker=H.binding(code),active_sha256=H.function_sha(W.active),facade=H.binding(code),facade_active_sha256=H.function_sha(W.Facade._active),tracker_archive=archive,lease_receipt=receipt,first_admission=admission,run_plan=run)
 prepared=dict(completed_custody=dict(tracker=bound,takeover=dict(completion=save(tmp_path/'complete.json',dict(after=archive)))))
 if reserved:
  selected['reserved_round']=save(tmp_path/'reserved-round.json',{'old_reservation':True})
  p.pop('lease_receipt');p['reserved_continuation']=save(tmp_path/'round/continued.json',selected['reserved_round'])
 if fault=='time':p['first_admission']=save(Path(admission['path']),dict(H.read(admission),observed_at_ns=11000*10**9))
 elif fault=='owner':lease=Lease('other','secret',3,archive['sha256'])
 elif fault=='source':selected['source']={'sha256':'foreign'}
 elif fault=='archive':Path(archive['path']).write_text('{}')
 elif fault=='worker':p['active_sha256']='wrong'
 elif fault=='receipt':
  if reserved:p['reserved_continuation']=save(tmp_path/'round/continued.json',{'foreign':True})
  else:p['lease_receipt']=save(Path(receipt['path']),dict(H.read(receipt),lease_operations=1))
 worker=SimpleNamespace(context=SimpleNamespace(tracker_root=tracker.parent));selection=SimpleNamespace(workbook='0111',stage_valid=fault!='stage')
 W.Facade.context=worker.context
 active=W.active;native=W.Facade._active
 if fault:
  with pytest.raises((ValueError,AssertionError)):H.archived_active(W,worker,selection,lease,p,selected,prepared)
 else:
  _,actual,record=H.archived_active(W,worker,selection,lease,p,selected,prepared)
  assert actual==before and record==archive and not tracker.exists()
 assert W.active is active and W.Facade._active is native
 with pytest.raises(FileNotFoundError):W.active(worker,selection,lease)
