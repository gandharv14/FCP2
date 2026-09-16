from pathlib import Path
from dataclasses import replace
import json
import pytest
from scripts.fleet import package_recovery_continue as M,continuation_lease as L
from scripts.batch import harbor_tracker_lifecycle as life,harbor_instruction_worker as W
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup
from test_completed_checkpoint_takeover import failed_successor_fixture
from types import SimpleNamespace

def put(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)

@pytest.mark.parametrize('fault',[None,'source','authority','before','after','lease','partial'])
def test_real_original_renewal_and_immutable_archives(failed,tmp_path,fault):
 worker,s,old,blocked,task,*_=failed;tracker=Path(task.tracker['path']);before=tracker.read_bytes();profile={'tracker':M.binding(tracker)};home=tmp_path/'renewal'
 p={'helper':M.binding(Path(M.__file__).resolve()),'lease_renewal':{'module':M.binding(Path(L.__file__).resolve()),'directory':str(home)}};row=put(tmp_path/'context.json',p)
 completion=put(tmp_path/'takeover.json',dict(after={'sha256':old.sha256}));profile.update(completion=completion,source={'actual':'source'},workbook='0001');authority=put(tmp_path/'plan.json',dict(context=dict(plan=row,helper=p['helper'],function='context',manager='contextmanager'),renewal_module=p['lease_renewal']['module'],output=str(tmp_path),tracker=profile['tracker'],source=profile['source'],workbook=profile['workbook'],prepermit_failure={}))
 assert M.failed_renewal_lease(row,p,profile,old,life)==old
 current,receipt=L.renew(life=life,read=M.read,record=M.binding,keep=W.keep,path=tracker,lease=old,authority_record=authority,validation=lambda *a:[],custody=lambda:None,directory=home)
 (home/'before.json').write_bytes(before);(home/'after.json').write_bytes(tracker.read_bytes())
 if fault=='source':v=M.read(M.binding(home/'after.json'));v['bindings']['original_raw_source']={'foreign':True};put(home/'after.json',v)
 elif fault=='authority':v=M.read(authority);v['context']['plan']={'foreign':True};put(Path(authority['path']),v)
 elif fault=='before':(home/'before.json').write_bytes(tracker.read_bytes())
 elif fault=='after':(home/'after.json').write_bytes(before)
 elif fault=='lease':old=replace(old,owner='foreign')
 elif fault=='partial':Path(receipt['path']).unlink()
 if fault:
  with pytest.raises(Exception):M.failed_renewal_lease(row,p,profile,old,life)
 else:
  assert M.failed_renewal_lease(row,p,profile,old,life)==current
  assert tracker.read_bytes()==(home/'after.json').read_bytes()

@pytest.mark.parametrize('fail',[False,True])
def test_historical_handoff_stays_original_current_active_uses_real_renewal(tmp_path,fail):
 from scripts.fleet import completed_checkpoint_takeover as B
 profile,I,W,T,successful,keep,p,old,live,row,failure,worker=failed_successor_fixture(tmp_path)
 new=replace(old,revision=old.revision+1,sha256='actual-renewal');observed=[]
 def active(w,s,lease):
  observed.append(lease)
  if fail:raise ValueError('live owner rejected')
 W.active=active;original=SimpleNamespace(__file__=B.__file__,validate=B.validate);h=B.read(row);prior=I._prior
 with B.prior_scope(row,profile,I,W,T,successful,failed_successor=failure,original_reader=original,failed_lease=lambda l:new if l==old else None):
  if fail:
   with pytest.raises(ValueError,match='live owner'):I._prior(h['result'],h['descriptor'])
  else:assert I._prior(h['result'],h['descriptor'])[2]==old
 assert observed==[new]and I._prior is prior
