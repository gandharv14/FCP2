from dataclasses import dataclass
from pathlib import Path
import json
import pytest
from scripts.batch import harbor_dialogue_first_round_warning as F,harbor_instruction_lease_renewal as L
from scripts.batch.harbor_admission import record

@pytest.mark.parametrize('changed',['scripts/batch/harbor_instruction_repair.py','scripts/batch/harbor_completed_lanes.py','financial_evaluator.py'])
def test_runtime_successor_reaches_original_history_checks(tmp_path,monkeypatch,changed):
 old=tmp_path/'old';new=tmp_path/'new';validator='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
 for root in (old,new):
  for name in (validator,changed):
   p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original'if root==old else'prospective')
 snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps(dict(root=str(old),files=[dict(relative=k,sha256=v)for k,v in F.inventory(old).items()])))
 @dataclass
 class Worker:
  root:Path
  warning_requalification:object=None
 class ReachedHistoricalValidation(Exception):pass
 def boundary(*a):raise ReachedHistoricalValidation()
 monkeypatch.setattr(L,'_historical_inventory',boundary)
 if changed=='financial_evaluator.py':
  with pytest.raises(ValueError,match='unrelated code delta'):F.history(Worker(new),{},record(snapshot))
 else:
  with pytest.raises(ReachedHistoricalValidation):F.history(Worker(new),{},record(snapshot))
