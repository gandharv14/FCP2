import json
from pathlib import Path

import pytest
from scripts.fleet import normalization_undispatched_continue as D


def saved(path,value):
 path.write_text(json.dumps(value));return D.binding(path)


def test_failed_guard_retains_intent_and_does_not_retry(tmp_path,monkeypatch):
 from scripts.batch import hardened_runner
 old=saved(tmp_path/'old.json',dict(code_root=str(tmp_path)))
 plan=saved(tmp_path/'plan.json',dict(home=str(tmp_path/'new'),old_plan=old))
 calls=[]
 def interrupted(command,**kwargs):
  calls.append(command)
  assert kwargs['timeout_seconds']==3300 and kwargs['memory_limit_bytes']==8*1024**3
  intent=Path(command[command.index('--plan')+1]);assert intent.is_file()
  raise TimeoutError('synthetic interrupted guarded child')
 monkeypatch.setattr(hardened_runner,'_run_in_process_group',interrupted)
 monkeypatch.setattr(D.shutil,'disk_usage',lambda _:type('Disk',(),{'free':12*1024**3})())
 with pytest.raises(ValueError,match='stopped'):
  D.guarded(plan,'dispatch',dict(index=3,ordinal=1),{'fixture':'lease'},'partition-0003-round-1',None)
 resource=json.loads((tmp_path/'new/stages/partition-0003-round-1/resource.json').read_bytes())
 assert resource['failure_type']=='TimeoutError' and resource['automatic_retry']is False
 with pytest.raises(ValueError,match='exists'):
  D.guarded(plan,'dispatch',dict(index=3,ordinal=1),{'fixture':'lease'},'partition-0003-round-1',None)
 assert len(calls)==1


def test_disk_guard_creates_no_intent(tmp_path,monkeypatch):
 old=saved(tmp_path/'old.json',dict(code_root=str(tmp_path)))
 plan=saved(tmp_path/'plan.json',dict(home=str(tmp_path/'new'),old_plan=old))
 monkeypatch.setattr(D.shutil,'disk_usage',lambda _:type('Disk',(),{'free':8*1024**3-1})())
 with pytest.raises(ValueError,match='reserve'):D.guarded(plan,'dispatch',{}, {},'first',None)
 assert not (tmp_path/'new').exists()


@pytest.mark.parametrize('change',[{'workbook':'0073'},{'expected_previous_fence':2},{'owner':'hhu-server-8:slot-2:0173-normalization-002'},{'schema_version':'unknown'}])
def test_closed_profile_rejects_unreviewed_workbook_owner_or_fence(tmp_path,change):
 fields={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence','old_helper','old_plan','executor_adapter','partition_adapter','model_gate','native_proof','central_proof','prepared','original_custody','tracker','native_probe','home'}
 p={k:None for k in fields};p.update(schema_version=D.SCHEMA,workbook='0173',owner=D.OWNERS['0173'],expected_previous_fence=1,native_proof={'sha256':D.NATIVE_PROOFS['0173']});p.update(change)
 with pytest.raises(ValueError):D.load(saved(tmp_path/'plan.json',p))
