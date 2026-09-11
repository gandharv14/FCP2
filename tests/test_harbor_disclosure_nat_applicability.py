"""Actual retained-call contexts and owning NAT proof, without mocked PASS."""
import dataclasses,json
from pathlib import Path
import pytest
from openpyxl import Workbook
from scripts.batch import harbor_complete_readiness as c
from scripts.batch.harbor_admission import record
from scripts.batch import harbor_semantic_readiness as semantic
from test_harbor_complete_readiness import naturalization_fixture,ROOT
from test_harbor_model_review_provenance import fixture as journal
from test_harbor_semantic_readiness import set_call,bind_json


def fixture(tmp_path,reviewed='final'):
    selection,nat=naturalization_fixture(tmp_path);s=selection.content
    book=Workbook();book.active['A1']=2
    source=tmp_path/'source.xlsx';book.save(source)
    book.save(s.stage/'environment/0001-inputs.xlsx');book.close()
    payload=bind_json(s.stage/'tests/disclosure.json',{'records':[],'agent_records':[]})
    state=json.loads((nat/'state.json').read_text());candidate=nat/state['final_candidate']
    dialogue=c.static.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    claims=bind_json(tmp_path/'claims.json',{'claims':[]})
    rewritten=dialogue.rewrite_instruction(candidate.read_text(),s.stage,{'claims':[]})
    (s.stage/'instruction.md').write_text(rewritten)
    p=s.stage/'task.toml';p.write_text(dialogue.update_instruction_hash(p.read_text(),c.static.digest(s.stage/'instruction.md')))
    instruction=record(candidate if reviewed=='final' else nat/'source.snapshot.md' if reviewed=='source' else s.stage/'instruction.md')
    if reviewed=='other':
        p=tmp_path/'other.md';p.write_text(candidate.read_text()+'\nUnreviewed intermediate.\n');instruction=record(p)
    args={'instruction':instruction,'disclosure':payload,'golden':record(source),'delivered':record(s.stage/'environment/0001-inputs.xlsx')}
    manifest=semantic.disclosure_manifest(**args,root=ROOT);bound=bind_json(tmp_path/'manifest.json',manifest)
    directory=tmp_path/'disclosure-review';directory.mkdir();call,policy,request=journal(directory,'naturalization')
    request['messages'][1]['content']=json.dumps({'manifest':manifest,'manifest_sha256':bound['sha256'],'instruction':Path(instruction['path']).read_text(),'disclosure':semantic.read(payload),'workbooks':{k:semantic.workbook_evidence(args[k]) for k in ('golden','delivered')}})
    review={'schema_version':'harbor-disclosure-faithfulness/v1','model':'gpt-5.6-sol-high','input_hashes':manifest['input_hashes'],'manifest_sha256':bound['sha256'],'sentences':[],'blocking_findings':[],'non_blocking_flags':[],'passed':True}
    set_call(call,request,review)
    s=dataclasses.replace(s,bindings={'source_file':record(source)},dialogue_claims=claims,stage_sha256=c.static.publication._tree_hash(s.stage)[0])
    semantic_selection=c.SemanticSelection(call,bound,instruction,None,{},())
    return dataclasses.replace(selection,content=s,semantic=semantic_selection),nat


@pytest.mark.parametrize('reviewed,expected',[('final','validated_final_nat_candidate'),('source','original_nat_source'),('staged','staged_instruction')])
def test_exact_reviewed_stage_is_explicit_and_call_bytes_preserved(tmp_path,reviewed,expected):
    selection,nat=fixture(tmp_path,reviewed);before=c.static.tree(nat);stage=c.static.tree(selection.content.stage)
    call=selection.semantic.disclosure_call;request=record(call.request['path']);response=record(call.response['path'])
    result=c.check_disclosure(selection,root=ROOT)
    assert result['instruction_applicability']==expected and result['records']==0
    if reviewed=='final':
        assert any(x==selection.content.naturalization_call.response for x in result['evidence'])
    assert record(call.request['path'])==request and record(call.response['path'])==response
    assert c.static.tree(nat)==before and c.static.tree(selection.content.stage)==stage


@pytest.mark.parametrize('change',['other','writer_missing','writer_body','raw_review','budget','candidate','dialogue_rewrite','metadata'])
def test_post_nat_review_requires_complete_current_chain(tmp_path,change):
    selection,nat=fixture(tmp_path,'other' if change=='other' else 'final');s=selection.content
    if change=='writer_missing':selection=dataclasses.replace(selection,content=dataclasses.replace(s,naturalization_writer_call=None))
    elif change=='budget':
        p=nat/'state.json';state=json.loads(p.read_text());state['attempts']=[];p.write_text(json.dumps(state))
    elif change!='other':
        p={'writer_body':nat/'attempt-01/input_body.md','raw_review':Path(s.naturalization_call.response['path']),'candidate':nat/'attempt-01/candidate.md','dialogue_rewrite':s.stage/'instruction.md','metadata':s.stage/'task.toml'}[change]
        p.chmod(0o600);p.write_text(p.read_text()+' changed')
    with pytest.raises(Exception):c.check_disclosure(selection,root=ROOT)
