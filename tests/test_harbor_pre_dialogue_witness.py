import dataclasses,json,shutil
from pathlib import Path
import pytest
from openpyxl import Workbook
from scripts.batch import harbor_readiness as r
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_transition_codec import encode,decode
from test_harbor_complete_readiness import naturalization_fixture,ROOT


def fixture(tmp_path):
    selection,nat=naturalization_fixture(tmp_path);s=selection.content
    book=Workbook();book.active.title='Valuation';book.active['A1']=2
    source=tmp_path/'source.xlsx';book.save(source);book.save(s.stage/'environment/0001-inputs.xlsx');book.close()
    payload={'golden':str(source),'golden_sha256':record(source)['sha256'],'records':[],'agent_records':[]}
    (s.stage/'tests/disclosure.json').write_text(json.dumps(payload))
    witness=tmp_path/'witness';shutil.copytree(s.stage,witness)
    # Retained unrelated code is hash bound but never imported/executed.
    (witness/'unrelated.py').write_text("raise AssertionError('witness code must not execute')\n")
    (witness/'tests/__pycache__').mkdir();(witness/'tests/__pycache__/unused.pyc').write_bytes(b'non-executable retained evidence')
    dialogue=r.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    state=json.loads((nat/'state.json').read_text());candidate=nat/state['final_candidate']
    claims=tmp_path/'claims.json';claims.write_text(json.dumps({'claims':[]}))
    p=s.stage/'instruction.md';p.write_text(dialogue.rewrite_instruction(candidate.read_text(),s.stage,{'claims':[]}))
    p=s.stage/'task.toml';p.write_text(dialogue.update_instruction_hash(p.read_text(),r.digest(s.stage/'instruction.md')))
    s=dataclasses.replace(s,bindings={'source_file':record(source)},dialogue_claims=record(claims),pre_dialogue_witness=r.PreDialogueWitness(witness,r.publication._tree_hash(witness)[0]))
    return s,nat


def rebind_witness(s):
    return dataclasses.replace(s,pre_dialogue_witness=r.PreDialogueWitness(s.pre_dialogue_witness.stage,r.publication._tree_hash(s.pre_dialogue_witness.stage)[0]))


def test_exact_witness_inputs_and_current_nat_chain_are_read_only(tmp_path):
    s,nat=fixture(tmp_path);before=r.tree(s.stage);prior=r.tree(s.pre_dialogue_witness.stage);budget=r.tree(nat)
    assert r.disclosure_verification_stage(s,root=ROOT)==s.pre_dialogue_witness.stage
    assert r.tree(s.stage)==before and r.tree(s.pre_dialogue_witness.stage)==prior and r.tree(nat)==budget


@pytest.mark.parametrize('change',['tree','instruction','delivered','disclosure','key','extra_workbook','source','raw_nat_review','writer_missing','budget','rewrite','metadata'])
def test_witness_never_overrides_current_inputs_or_nat_proof(tmp_path,change):
    s,nat=fixture(tmp_path);w=s.pre_dialogue_witness.stage
    if change in ('tree','instruction','delivered','disclosure','key','extra_workbook'):
        p={'tree':w/'unrelated.py','instruction':w/'instruction.md','delivered':w/'environment/0001-inputs.xlsx','disclosure':w/'tests/disclosure.json','key':w/'tests/answer_key.json','extra_workbook':w/'environment/0000-inputs.xlsx'}[change]
        p.write_bytes((p.read_bytes() if p.exists() else b'')+b'changed')
        if change!='tree':s=rebind_witness(s)
    elif change=='source':
        p=Path(s.bindings['source_file']['path']);p.write_bytes(p.read_bytes()+b'changed')
    elif change=='raw_nat_review':
        p=Path(s.naturalization_call.response['path']);p.write_text(p.read_text()+' changed')
    elif change=='writer_missing':s=dataclasses.replace(s,naturalization_writer_call=None)
    elif change=='budget':
        p=nat/'state.json';state=json.loads(p.read_text());state['attempts']=[];p.write_text(json.dumps(state))
    else:
        p=s.stage/('instruction.md' if change=='rewrite' else 'task.toml');p.write_text(p.read_text()+' changed')
    with pytest.raises(Exception):r.disclosure_verification_stage(s,root=ROOT)


def test_identically_invalid_provenance_still_reaches_unchanged_verifier(tmp_path):
    s,nat=fixture(tmp_path)
    # Both tasks agree, but no real strict generation exists. Equality cannot
    # substitute for the owning current source/AST/provenance validator.
    assert r.disclosure_verification_stage(s,root=ROOT)==s.pre_dialogue_witness.stage
    with pytest.raises(ValueError,match='structure/leak'):r.disclosure_mechanics(s,root=ROOT)


def test_codec_requires_explicit_typed_witness_selection(tmp_path):
    s,_=fixture(tmp_path);value=encode(s);assert decode(value)==s
    missing=json.loads(json.dumps(value));missing['value'].pop('pre_dialogue_witness')
    with pytest.raises(ValueError,match='fields differ'):decode(missing)
    ordinary=dataclasses.replace(s,pre_dialogue_witness=None)
    assert decode(encode(ordinary)).pre_dialogue_witness is None
    assert r.disclosure_verification_stage(ordinary,root=ROOT)==s.stage
    with pytest.raises(ValueError,match='structure/leak'):r.disclosure_mechanics(ordinary,root=ROOT)


def test_untyped_witness_is_not_an_approval_flag(tmp_path):
    s,_=fixture(tmp_path);s=dataclasses.replace(s,pre_dialogue_witness={'passed':True})
    with pytest.raises(ValueError,match='typed'):r.disclosure_verification_stage(s,root=ROOT)


@pytest.mark.parametrize('suffix',['.xlsx','.xlsm'])
@pytest.mark.parametrize('change',[None,'id_only','wrong_suffix','missing_notes','ambiguous_workbook'])
def test_dialogue_packaging_uses_exact_resolved_artifact(tmp_path,suffix,change):
    from types import SimpleNamespace
    stage=tmp_path/'0001-outputs';env=stage/'environment';env.mkdir(parents=True)
    (stage/'task.toml').write_text('[metadata]\nname="synthetic"\n')
    filename='0001-inputs'+suffix;(env/filename).write_bytes(b'COPY existence only')
    (env/'additional-assumptions.md').write_text('Retained dialogue')
    copy_name='0001' if change=='id_only' else '0001-inputs'+('.xlsm' if suffix=='.xlsx' else '.xlsx') if change=='wrong_suffix' else filename
    (env/'Dockerfile').write_text('WORKDIR /app\nCOPY '+copy_name+' /app/'+copy_name+'\nCOPY additional-assumptions.md /app/additional-assumptions.md\n')
    if change=='missing_notes':(env/'additional-assumptions.md').unlink()
    if change=='ambiguous_workbook':(env/('0001-inputs'+('.xlsm' if suffix=='.xlsx' else '.xlsx'))).write_bytes(b'other')
    selection=SimpleNamespace(stage=stage,workbook='0001')
    if change is None:assert r.dialogue_packaging_content(selection,root=ROOT)=={'valid':True,'workbook':filename}
    else:
        with pytest.raises(ValueError):r.dialogue_packaging_content(selection,root=ROOT)
