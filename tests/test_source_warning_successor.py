from dataclasses import dataclass
from pathlib import Path
import pytest
from scripts.batch import harbor_dialogue_first_round_warning as F,harbor_dialogue_warning_requalification as Q,harbor_instruction_lease_renewal as R
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_instruction_producer import put

@pytest.fixture
def successor(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new'
    names=['.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py','scripts/batch/harbor_dialogue_source_warnings.py','scripts/batch/harbor_dialogue_first_round_warning.py','scripts/batch/harbor_dialogue_warning_requalification.py']
    for root in [old,new]:
        for name in names:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original')
    for name in names[1:]:(new/name).write_text('prospective source-warning policy')
    bound=put(tmp_path/'snapshot.json',dict(root=str(old),files=[dict(relative=n,sha256=record(old/n)['sha256'])for n in names]))
    return bound,new,names

def test_source_warning_policy_does_not_require_artificial_validator_change(successor):
    bound,new,names=successor
    assert Q.changes(bound,new)==sorted(names[1:])
    assert (new/names[0]).read_text()=='original'

@pytest.mark.parametrize('kind',['unrelated','no-warning-policy'])
def test_source_warning_policy_rejects_unrelated_or_no_policy_delta(successor,kind):
    bound,new,names=successor
    if kind=='unrelated':(new/'grader.py').write_text('foreign')
    else:(new/names[1]).write_text('original')
    with pytest.raises(ValueError):Q.changes(bound,new)

def test_ordinary_history_reaches_original_evidence_validation_with_source_only_policy(successor,monkeypatch):
    bound,new,names=successor
    @dataclass
    class Worker:
        root:Path
        warning_requalification:object=None
    class OriginalHistory(Exception):pass
    def history(*args):raise OriginalHistory()
    monkeypatch.setattr(R,'_historical_inventory',history)
    with pytest.raises(OriginalHistory):F.history(Worker(new),{},bound)
