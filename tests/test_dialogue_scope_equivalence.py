from dataclasses import dataclass,replace
from pathlib import Path
import pytest
from test_dialogue_scope_discovery import fixture,prepared,ROOT
from scripts.batch import harbor_dialogue_scope_context as scope,harbor_readiness as readiness
from scripts.batch.harbor_admission import read,record
from scripts.batch.harbor_model_executor import canonical

@dataclass
class Selection:
    stage: Path
    bindings: dict
    dialogue_scope_receipt: dict
    dialogue_claims: dict
    scope_evidence: dict


def selected(fixture,monkeypatch):
    _,stage,_,tmp=fixture
    proof,(claims,_)=prepared(fixture);v=read(proof)
    monkeypatch.setattr(readiness,
                        'disclosure_verification_stage',lambda s,root:stage)
    claims_path=tmp/'claims.json';claims_path.write_bytes(canonical(claims))
    # Genuine unchanged historical implementation bytes with old locators.
    for key in ('producer','scope_validator','extractor'):
        old=tmp/(key+'.py');old.write_bytes(Path(v[key]['path']).read_bytes());v[key]=record(old)
    Path(proof['path']).write_bytes(canonical(v));proof=record(proof['path'])
    return Selection(stage,{'source_file':v['source']},proof,record(claims_path),v['scope']),tmp


def test_explicit_scope_equivalence_preserves_original_and_semantics(fixture,monkeypatch):
    s,tmp=selected(fixture,monkeypatch);old=Path(s.dialogue_scope_receipt['path']).read_bytes()
    assert scope.validate_selected(s,root=ROOT)  # Byte-identical locator changes need no semantic transition.
    updated,receipt=scope.requalify_selected(s,tmp/'equivalence',root=ROOT)
    assert Path(s.dialogue_scope_receipt['path']).read_bytes()==old
    assert updated.dialogue_claims==s.dialogue_claims and updated.scope_evidence==s.scope_evidence
    assert read(receipt)['previous']==s.dialogue_scope_receipt
    assert scope.validate_selected(updated,root=ROOT)
    with pytest.raises(ValueError,match='already exists'):scope.requalify_selected(s,tmp/'equivalence',root=ROOT)


@pytest.mark.parametrize('fault',['claims','header','old_code','current_code','budget','previous'])
def test_equivalence_rejects_substantive_or_provenance_drift(fixture,monkeypatch,fault):
    s,tmp=selected(fixture,monkeypatch)
    updated,receipt=scope.requalify_selected(s,tmp/'equivalence',root=ROOT)
    v=read(receipt)
    if fault=='claims':
        p=Path(s.dialogue_claims['path']);x=read(s.dialogue_claims);x['claims'][0]['must_say']='Wrong';p.write_bytes(canonical(x))
    elif fault=='header':
        p=Path(read(s.dialogue_scope_receipt)['discovery']['path']);p.write_text('{}')
    elif fault=='old_code':Path(read(s.dialogue_scope_receipt)['extractor']['path']).write_text('changed')
    else:
        if fault=='current_code':v['implementation'][0]['sha256']='0'*64
        elif fault=='budget':v['semantic_attempts_added']=1
        else:v['previous']=receipt
        Path(receipt['path']).write_bytes(canonical(v));updated=replace(updated,dialogue_scope_receipt=record(receipt['path']))
    with pytest.raises((ValueError,KeyError)):scope.validate_selected(updated,root=ROOT)


def test_task_requires_explicit_equivalent_receipt_and_original_binding(fixture,monkeypatch):
    from types import SimpleNamespace
    s,tmp=selected(fixture,monkeypatch)
    updated,receipt=scope.requalify_selected(s,tmp/'equivalence',root=ROOT)
    task=SimpleNamespace(stage=s.stage)
    actual,_=scope.validate_task_equivalence(task,receipt,s.dialogue_scope_receipt,root=ROOT)
    assert actual==read(s.dialogue_claims)
    with pytest.raises(ValueError,match='detached'):
        scope.validate_task_equivalence(task,receipt,s.dialogue_claims,root=ROOT)
    with pytest.raises(ValueError,match='identity'):
        scope.validate_task_equivalence(SimpleNamespace(stage=tmp/'wrong'),receipt,s.dialogue_scope_receipt,root=ROOT)


def successor_selection(fixture,monkeypatch):
    s,tmp=selected(fixture,monkeypatch)
    old,receipt=scope.requalify_selected(s,tmp/'old-equivalence',root=ROOT)
    value=read(receipt)
    (tmp/'historical').mkdir()
    for i,binding in enumerate(value['implementation']):
        path=tmp/'historical'/Path(binding['path']).name;path.write_bytes(Path(binding['path']).read_bytes())
        value['implementation'][i]=record(path)
    Path(receipt['path']).write_bytes(canonical(value))
    return replace(old,dialogue_scope_receipt=record(receipt['path'])),tmp


def test_scope_successor_retains_previous_receipt_and_original_claims(fixture,monkeypatch):
    from types import SimpleNamespace
    old,tmp=successor_selection(fixture,monkeypatch);binding=old.dialogue_scope_receipt
    with pytest.raises(ValueError,match='code changed'):scope.validate_selected(old,root=ROOT)
    new,receipt=scope.requalify_selected(old,tmp/'successor',root=ROOT)
    value=read(receipt)
    assert value['replaces']==binding and value['previous']==read(binding)['previous']
    assert new.dialogue_claims==old.dialogue_claims and record(binding['path'])==binding
    assert scope.validate_selected(new,root=ROOT)
    assert scope.validate_task_equivalence(SimpleNamespace(stage=new.stage),receipt,value['previous'],root=ROOT)[0]==read(new.dialogue_claims)
    Path(read(binding)['implementation'][0]['path']).write_text('changed historical code')
    with pytest.raises(ValueError):scope.validate_selected(new,root=ROOT)
    with pytest.raises(ValueError):scope.validate_task_equivalence(SimpleNamespace(stage=new.stage),receipt,value['previous'],root=ROOT)


@pytest.mark.parametrize('field',['claims','previous','semantic_attempts_added','unknown'])
def test_scope_successor_cannot_hide_historical_selection_change(fixture,monkeypatch,field):
    old,tmp=successor_selection(fixture,monkeypatch);binding=old.dialogue_scope_receipt;v=read(binding)
    if field=='claims':v[field]=v['previous']
    elif field=='previous':v[field]=old.dialogue_claims
    elif field=='semantic_attempts_added':v[field]=1
    else:v[field]='invented'
    Path(binding['path']).write_bytes(canonical(v));old=replace(old,dialogue_scope_receipt=record(binding['path']))
    with pytest.raises((ValueError,KeyError)):scope.requalify_selected(old,tmp/'successor',root=ROOT)
