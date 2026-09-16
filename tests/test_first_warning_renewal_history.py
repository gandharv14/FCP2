"""Warning history preserves a real worker's selected renewal identity."""
from dataclasses import replace
from pathlib import Path
import pytest
from scripts.batch import harbor_dialogue_first_round_warning as F,harbor_instruction_worker as W

@pytest.mark.parametrize('selected',['none','renewed'])
def test_selected_renewal_is_not_erased_during_warning_validation(monkeypatch,selected):
    renewal={'path':'/retained/renewal.json','sha256':'a'*64}if selected=='renewed'else None
    worker=W.Worker(None,Path('/retained/instruction'),None,Path('/code'),lease_renewal=renewal)
    assert F.ordinary(worker)
    expected={'validated_renewal':renewal}
    calls=[]
    def history(original,before,snapshot):
        calls.append(original.lease_renewal)
        assert original.warning_requalification is None
        return {'validated_renewal':original.lease_renewal}
    monkeypatch.setattr(F,'history',history)
    permit=dict(prior_continuations={'lease_renewal':renewal}if renewal else{},round=1,semantic=None,code_repair=None,model_calls=0,semantic_budget_reset=False,old_snapshot={},ordinary_first_round=expected)
    assert F.validate_history(worker,{},permit)==expected
    assert calls==[renewal]
    changed={**permit,'prior_continuations':{'lease_renewal':{'path':'/foreign','sha256':'b'*64}}}
    with pytest.raises(ValueError):F.validate_history(worker,{},changed)
    assert calls==[renewal]

@pytest.mark.parametrize('field',['retry','dialogue_retry','boundary_recovery','dialogue_semantic_retry','pretransport_recovery','code_repair','warning_requalification','disclosure_repair','disclosure_revision','dialogue_pre_review_repair'])
def test_renewal_does_not_license_other_specialized_history(field):
    worker=W.Worker(None,Path('/retained/instruction'),None,Path('/code'),lease_renewal={'path':'/renewal','sha256':'a'*64})
    assert not F.ordinary(replace(worker,**{field:{'path':'/specialized','sha256':'b'*64}}))
