from types import SimpleNamespace
import pytest
from scripts.batch import harbor_dialogue_warning_requalification as Q
from scripts.batch import harbor_dialogue_first_round_warning as F
from scripts.batch import harbor_instruction_lease_renewal as R
from scripts.batch import harbor_instruction_worker as W

class Worker(SimpleNamespace):
    def __getattr__(self, name): return None

@pytest.mark.parametrize('fault',[None,'missing','foreign'])
def test_real_renewal_adapter_returns_validated_permit_not_history_envelope(tmp_path,monkeypatch,fault):
    run=tmp_path/'run';run.mkdir();(run/'initial.json').write_text('{}')
    renewal={'path':str(tmp_path/'renewal.json'),'sha256':'a'*64}
    warning={'path':str(tmp_path/'warning.json'),'sha256':'b'*64}
    w=Worker(run=run,root=tmp_path,lease_renewal=renewal,warning_requalification=warning)
    permit=dict(schema_version=Q.SCHEMA,worker_run=str(run),initial=Q.record(run/'initial.json'),
        code={},prior_continuations={'lease_renewal':renewal},semantic=None,code_repair=None,
        old_snapshot={},code_changes=[],ordinary_first_round={'authenticated':'history'})
    lease_permit={'checkpoint':{'path':'actual/completed.json','sha256':'c'*64},'selection':'retained'}
    proof={'lease_renewal':renewal,'renewal_history':lease_permit,'records':['author','paraphraser','review']}
    if fault=='missing':proof.pop('renewal_history')
    if fault=='foreign':proof['lease_renewal']={'path':'foreign','sha256':'d'*64}
    monkeypatch.setattr(Q,'read',lambda row: permit)
    monkeypatch.setattr(R,'read',lambda row: permit)
    monkeypatch.setattr(W,'code',lambda worker:{})
    monkeypatch.setattr(Q,'changes',lambda *args:[])
    validated=[]
    def validate(worker,before,p):
        assert worker is w and p is permit
        validated.append(True);return proof
    monkeypatch.setattr(F,'validate_history',validate)
    if fault:
        with pytest.raises(ValueError,match='authenticated renewal'):R.validate(w,{})
    else:
        actual=R.validate(w,{})
        assert actual is lease_permit and actual['checkpoint']==lease_permit['checkpoint']
    assert validated==[True]
