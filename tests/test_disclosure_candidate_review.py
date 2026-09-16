"""A candidate review cannot rewrite financial files or trust a forged preview."""
import copy
import json
import shutil
from pathlib import Path
import pytest
from scripts.fleet import disclosure_candidate_review as M
from test_disclosure_column_increment import D,fixture,detect


def data(tmp_path):
    gold,delivered,path,old=fixture(tmp_path)
    (old/'instruction.md').write_text('Rebuild the source.\n## Output\nReturn the result.\n')
    (old/'tests/disclosure.json').write_text(json.dumps({'records':[],'agent_records':[]}))
    new=tmp_path/'candidate';shutil.copytree(old,new)
    records=detect(gold,delivered);section=D.render_section(D.agent_records(records))
    (new/'instruction.md').write_text((old/'instruction.md').read_text().replace('\n## Output\n','\n'+section+'\n## Output\n'))
    (new/'tests/disclosure.json').write_text(json.dumps({'records':records,'agent_records':D.agent_records(records),'golden':str(path),'golden_sha256':M.binding(path)['sha256']}))
    proof=dict(status='ISOLATED_SOURCE_DISCLOSURE_CANDIDATE',models=0,original_task_mutations=0,qualified=False,original_tree=M.tree(old),candidate_tree=M.tree(new),candidate_instruction=M.binding(new/'instruction.md'),candidate_disclosure=M.binding(new/'tests/disclosure.json'),source=M.binding(path))
    f=tmp_path/'proof.json';f.write_text(json.dumps(proof))
    return dict(proof=M.binding(f),original_task=str(old),candidate=str(new)),proof,path


def test_actual_faithcheck_validates_new_rule_but_no_qualification(tmp_path,monkeypatch):
    p,proof,path=data(tmp_path)
    # Existing verifier additionally requires production source-generation metadata;
    # this fixture exercises the real faithcheck and exact financial-tree boundary.
    monkeypatch.setattr(D,'verify_task',lambda _:dict(passed=True,faults=[]))
    out=M.candidate(p,D);assert out[0]['qualified']is False
    assert out[2]['passed']is True

@pytest.mark.parametrize('change',['source','old','financial','instruction','proof_models','proof_qualified','candidate_binding'])
def test_changed_inputs_fail_before_any_review(tmp_path,monkeypatch,change):
    p,proof,path=data(tmp_path);new=Path(p['candidate']);old=Path(p['original_task'])
    if change=='source':path.write_bytes(b'changed')
    elif change=='old':(old/'instruction.md').write_text('changed')
    elif change=='financial':(new/'tests/answer_key.json').write_text('{}')
    elif change=='instruction':(new/'instruction.md').write_text('Different task\n## Output\n')
    elif change=='proof_models':proof['models']=1
    elif change=='proof_qualified':proof['qualified']=True
    else:proof['candidate_instruction']=M.binding(old/'instruction.md')
    if change.startswith('proof_')or change=='candidate_binding':
        f=Path(p['proof']['path']);f.write_text(json.dumps(proof));p['proof']=M.binding(f)
    monkeypatch.setattr(D,'verify_task',lambda _:dict(passed=True,faults=[]))
    with pytest.raises(ValueError):M.candidate(p,D)


def review_fixture():
    manifest=dict(input_hashes={'source':'abc'},sentences=[dict(sentence_id='s1',text='exact',record_ids=['r1'])])
    row=dict(**manifest['sentences'][0],golden_evidence=['source formula'],delivered_evidence=['blank'],verdict='blocking',findings=['mismatch'])
    review=dict(schema_version='harbor-disclosure-faithfulness/v1',model='gpt-5.6-sol-high',input_hashes=manifest['input_hashes'],manifest_sha256='m',sentences=[row],blocking_findings=['mismatch'],non_blocking_flags=[],passed=False)
    return review,manifest


def test_actual_semantic_rejection_is_distinct_from_malformed_response():
    r,m=review_fixture();assert M.semantic_status(r,m,'m',lambda *args:None)=='CANDIDATE_REVIEW_REJECTED'
    for change in ('identity','evidence','hash','contradiction','missing'):
        value=copy.deepcopy(r)
        if change=='identity':value['sentences'][0]['text']='different'
        elif change=='evidence':value['sentences'][0]['golden_evidence']=[]
        elif change=='hash':value['manifest_sha256']='wrong'
        elif change=='contradiction':value['passed']=True
        else:value['sentences']=[]
        assert M.semantic_status(value,m,'m',lambda *args:None)=='CANDIDATE_REVIEW_UNRESOLVED'


def test_positive_response_must_pass_original_validator():
    r,m=review_fixture();r['passed']=True;r['blocking_findings']=[];r['sentences'][0]['verdict']='pass'
    observed=[]
    def validator(*args):observed.append(args)
    assert M.semantic_status(r,m,'m',validator)=='CANDIDATE_REVIEW_PASSED_NOT_ADOPTED'
    assert observed==[(r,m,'m')]


@pytest.mark.parametrize('encoding',['raw','projected','records'])
def test_original_review_authenticates_full_input_and_its_exact_projection(encoding):
    from test_disclosure_lossless_tables import workbooks
    from scripts.batch import harbor_disclosure_evidence as E
    full=dict(manifest={},manifest_sha256='a'*64,instruction='Complete instruction',workbooks=workbooks(),
        disclosure=dict(records=[{'cell_keys':['S!A1']}],agent_records=[{'cell_keys':['S!A1']}],
            method_assessments=[{'cell_keys':['S!A1']},{'cell_keys':['Other!Z99']},{'cell_keys':[]}]))
    before=copy.deepcopy(full)
    body=copy.deepcopy(full)if encoding=='raw'else E.project_body(full)
    if encoding=='records':body=E.compact_records(body)
    M.validate_original_body(body,full)
    assert full==before
    bad=copy.deepcopy(body);bad['instruction']='Different task'
    with pytest.raises(ValueError):M.validate_original_body(bad,full)
    if encoding!='raw':
        assert len(body['disclosure']['method_assessments'])==2
        bad=copy.deepcopy(body);bad['disclosure']['method_assessments'].pop()
        with pytest.raises(ValueError):M.validate_original_body(bad,full)
        bad=copy.deepcopy(body);bad['workbooks']['workbooks']['golden']['sheets'][0]['cells']['rows'].pop()
        with pytest.raises(ValueError):M.validate_original_body(bad,full)


def test_original_projection_with_older_selected_codec(monkeypatch):
    from types import SimpleNamespace
    from test_disclosure_lossless_tables import workbooks
    from scripts import batch
    from scripts.batch import harbor_disclosure_evidence as E
    full=dict(manifest={},manifest_sha256='a'*64,instruction='Complete instruction',workbooks=workbooks(),
        disclosure=dict(records=[{'cell_keys':['S!A1']}],agent_records=[{'cell_keys':['S!A1']}],method_assessments=[]))
    body=E.project_body(full)
    if body['evidence_projection']==E.RECORD_VERSION:
        body['disclosure']['records']=E.decode_records(body['disclosure']['records'])
        body['evidence_projection']=E.VERSION
    legacy=SimpleNamespace(VERSION=E.VERSION,project_body=lambda _:copy.deepcopy(body))
    monkeypatch.setattr(batch,'harbor_disclosure_evidence',legacy)
    M.validate_original_body(body,full)
    changed=copy.deepcopy(body);changed['instruction']='different'
    with pytest.raises(ValueError):M.validate_original_body(changed,full)
    changed=copy.deepcopy(body);changed['evidence_projection']='unknown'
    with pytest.raises(ValueError):M.validate_original_body(changed,full)
