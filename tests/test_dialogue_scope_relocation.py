from pathlib import Path
from dataclasses import replace
import copy
import pytest
from test_dialogue_scope_equivalence import selected
from test_dialogue_scope_discovery import fixture,ROOT
from scripts.batch import harbor_dialogue_scope_context as scope
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_model_executor import canonical

@pytest.mark.parametrize('fault',[None,'old_bytes','new_bytes','rebound_changed_bytes','source','budget','claims','extra_field'])
def test_exact_relocated_implementation_and_evidence(tmp_path,fault):
    old={'source':{'exact':'source'},'semantic_budget_reset':False};new=copy.deepcopy(old)
    for role in ('producer','scope_validator','extractor'):
        a=tmp_path/(role+'-old.py');b=tmp_path/(role+'-new.py');a.write_text(role);b.write_text(role)
        old[role]=record(a);new[role]=record(b)
    if fault=='old_bytes':Path(old['producer']['path']).write_text('tamper')
    if fault=='new_bytes':Path(new['extractor']['path']).write_text('tamper')
    if fault=='rebound_changed_bytes':
        p=Path(new['scope_validator']['path']);p.write_text('new semantic code');new['scope_validator']=record(p)
    if fault=='source':new['source']={'foreign':'source'}
    if fault=='budget':new['semantic_budget_reset']=True
    if fault=='claims':new['claim_ids']=['changed']
    if fault=='extra_field':old['extractor']['extra']=True
    if fault is None:
        assert scope._relocated_discovery_implementation(old,new)==[new[k] for k in ('producer','scope_validator','extractor')]
    else:
        with pytest.raises((ValueError,KeyError)):scope._relocated_discovery_implementation(old,new)

@pytest.mark.parametrize('fault',[None,'claims','scope','source'])
def test_selected_receipt_preserved_and_semantics_still_required(fixture,monkeypatch,fault):
    s,tmp=selected(fixture,monkeypatch);original=Path(s.dialogue_scope_receipt['path']).read_bytes();v=read(s.dialogue_scope_receipt)
    if fault=='claims':
        x=read(s.dialogue_claims);x['claims'][0]['must_say']='wrong';p=tmp/'changed-claims.json';p.write_bytes(canonical(x));s=replace(s,dialogue_claims=record(p))
    if fault=='scope':s=replace(s,scope_evidence=None)
    if fault=='source':s=replace(s,bindings={'source_file':s.dialogue_claims})
    if fault is None:
        evidence=scope.validate_selected(s,root=ROOT)
        assert all(v[k] in evidence for k in ('producer','scope_validator','extractor'))
        assert len(evidence)==12  # Original nine records plus three current locators.
        assert record(scope.discovery.__file__) in evidence
    else:
        with pytest.raises(ValueError):scope.validate_selected(s,root=ROOT)
    assert Path(s.dialogue_scope_receipt['path']).read_bytes()==original
