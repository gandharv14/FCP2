from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
from scripts.fleet import continuation_lease as L,disclosure_reserved_round as R
from scripts.batch import harbor_tracker_lifecycle as life,harbor_dialogue_semantic_retry as retry,harbor_instruction_worker as W
from scripts.batch.harbor_admission import read,record
from test_dialogue_semantic_retry import failed
from test_harbor_instruction_producer import setup

@pytest.fixture
def completed(failed,tmp_path):
 worker,s,lease,blocked,task,*_=failed;home=tmp_path/'old';home.mkdir();source=s.content.bindings['original_raw_source']
 old=dict(output=str(home),renewal_module=record(L.__file__),workbook=s.content.workbook,source=source);plan=W.keep(tmp_path/'old-plan.json',old)
 before=tmp_path/'before.json';before.write_bytes(Path(task.tracker['path']).read_bytes())
 current,receipt=L.renew(life=life,read=read,record=record,keep=W.keep,path=task.tracker['path'],lease=lease,authority_record=plan,validation=lambda before,l:retry._failure(worker,s,l,blocked)[3],custody=lambda:None,directory=home/'initial-lease')
 proof=dict(schema_version='fleet-disclosure-initial-cache-failure/v1',plan=plan,worker_outcome={},process={},authority={},retained_lease=receipt,lease_before=record(before),lease_after=record(task.tracker['path']))
 binding=W.keep(tmp_path/'proof.json',proof);Q=SimpleNamespace(life=life,bound_module=lambda row:L)
 return binding,old,worker,lease,Q,current,proof,tmp_path

def test_original_completed_renewal_authenticated_no_replay(completed):
 b,p,w,l,Q,current,v,root=completed;before={x:x.read_bytes()for x in root.rglob('*')if x.is_file()}
 assert R.completed_initial_lease(b,p,w,l,Q)==current
 assert all(x.read_bytes()==raw for x,raw in before.items())

@pytest.mark.parametrize('change',['missing_before','foreign_before','foreign_after','foreign_owner','different_source','partial_renewal'])
def test_changed_custody_rejected(completed,change):
 b,p,w,l,Q,current,v,root=completed
 if change=='missing_before':v.pop('lease_before')
 elif change=='foreign_before':v['lease_before']=v['lease_after']
 elif change=='foreign_after':v['lease_after']=v['lease_before']
 elif change=='foreign_owner':l=replace(l,owner='foreign')
 elif change=='different_source':p=dict(p,source={'sha256':'0'*64,'path':'/foreign'})
 else:Path(v['retained_lease']['path']).unlink()
 b=W.keep(root/'changed.json',v)
 with pytest.raises((ValueError,FileNotFoundError)):R.completed_initial_lease(b,p,w,l,Q)

def test_additional_renewal_effect_rejected(completed):
 b,p,w,l,Q,current,v,root=completed;W.keep(Path(v['retained_lease']['path']).parent/'retained-live.json',{})
 with pytest.raises(ValueError):R.completed_initial_lease(b,p,w,l,Q)
