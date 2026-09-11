from dataclasses import replace
from pathlib import Path
import copy
import pytest
from scripts.batch import harbor_continuation_audit as C
from scripts.batch.harbor_complete_readiness import ReadinessSelection
from scripts.batch.harbor_readiness import Selection
from scripts.batch.harbor_admission import record
from test_harbor_normalization_producer import setup
from test_audit_inventory_upgrade import prior,upgraded


@pytest.fixture
def selections(prior,tmp_path):
    s,c,code,resolver=prior
    content=Selection(s.workbook,tmp_path/'stage','a'*64,s.bindings,s.run,tmp_path/'nat',None,None,model_policy=s.policy)
    old=ReadinessSelection(content,None,None,None,None)
    value,receipt,b=upgraded(prior,tmp_path)
    new=replace(old,content=replace(content,bindings=b))
    return old,new,prior


def test_direct_upgrade_preserves_original_request_locator_and_all_context(selections):
    old,new,prior=selections
    evidence=C.validate_selection(old,new)
    assert new.content.bindings['audit_validator_upgrade'] in evidence
    assert new.content.bindings['audit_upgrade_original_code'] in evidence
    assert 'audit_equivalence' not in new.content.bindings
    assert C.validate_selection(old,old)==[]


@pytest.mark.parametrize('field',['source_file','baseline_inputs','variable_source_audit','curation','source_generation'])
def test_existing_evidence_cannot_be_replaced_even_with_equal_receipt(selections,field):
    old,new,_=selections;b=copy.deepcopy(new.content.bindings);b[field]['path']+='-different'
    with pytest.raises(ValueError):C.validate_selection(old,replace(new,content=replace(new.content,bindings=b)))


@pytest.mark.parametrize('change',['one_key','extra_key','existing_upgrade','source','stage','policy','runtime','wrapper','response','old_code','current_inventory'])
def test_scope_tampering_and_owning_proof_failure_reject(selections,change,monkeypatch):
    old,new,(s,c,code,resolver)=selections;b=copy.deepcopy(new.content.bindings)
    if change=='one_key':b.pop('audit_upgrade_original_code')
    elif change=='extra_key':b['audit_equivalence']=b['audit_validator_upgrade']
    elif change=='existing_upgrade':old=replace(old,content=replace(old.content,bindings=copy.deepcopy(b)))
    elif change=='source':Path(b['source_file']['path']).write_bytes(b'changed')
    elif change=='stage':new=replace(new,content=replace(new.content,stage=Path('/different')))
    elif change=='policy':new=replace(new,content=replace(new.content,model_policy=replace(new.content.model_policy,model='changed')))
    elif change=='runtime':new=replace(new,runtime='unrelated')
    elif change=='wrapper':
        p=Path(b['audit_validator_upgrade']['path']);p.write_text('{"schema_version":"renderer-wrapper"}');b['audit_validator_upgrade']=record(p)
    elif change=='response':
        import json
        m=json.loads(Path(b['variable_source_metadata']['path']).read_bytes());coverage=json.loads(Path(m['coverage_path']).read_bytes());Path(coverage['batches'][0]['response']['path']).write_bytes(b'changed')
    elif change=='old_code':Path(code['path']).write_bytes(b'changed')
    elif change=='current_inventory':resolver.side_effect=ValueError('current segmentation rejected')
    if change=='existing_upgrade':
        # Existing upgrade may not be replaced/removed, even by another valid one.
        b['audit_validator_upgrade']={**b['audit_validator_upgrade'],'sha256':'0'*64}
    with pytest.raises(ValueError):C.validate_selection(old,replace(new,content=replace(new.content,bindings=b)))
