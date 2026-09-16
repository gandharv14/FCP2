"""Closed allegation resolution and actual retained source/scaffold checks.

Unit mechanics are isolated; the native proof uses the exact owning task.
"""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_dialogue_source_warnings as S
from scripts.batch import harbor_instruction_producer as P
from scripts.batch.harbor_admission import record
ROOT=Path(__file__).resolve().parents[1]
FINDING="The Director's answer for the Period claim leaks the numeric graded target 0.5."

def test_unique_label_and_unrecognized_allegation():
    assert S.accuracy_findings([FINDING],2,[{'row_label':'Other'},{'row_label':'Period'}])==({1:[.5]},[],'single_row_formula_operand')
    for rows in ([{'row_label':'Period'},{'row_label':'Period'}],[{'row_label':'Other'}]):
        with pytest.raises(ValueError):S.accuracy_findings([FINDING],len(rows),rows)
    with pytest.raises(ValueError):S.accuracy_findings([FINDING,'Wrong operand'],1,[{'row_label':'Period'}])

@pytest.fixture
def evidence(monkeypatch,tmp_path):
    p=ROOT/'diagnostics/bulk-production/0320-warning-001/custody-003/mapping.json'
    if not p.exists():pytest.skip('retained source/scaffold fixture unavailable')
    m=json.loads(p.read_bytes());rows={Path(x['native']['path']).name:x['copy']for x in m['copies']}
    args=[rows[n]for n in ['review.json','claims.json','draft.md','draft_template.md','writer.filled.md','paraphraser.filled.md']]
    args.append(record(ROOT.parent/'RawWorkbooks3/0320.xlsx'))
    v=P.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    facade=SimpleNamespace(**{n:getattr(v,n)for n in ['fill_faults','strip_slot_scaffold','parse_turns','is_senior','disclose']},check_draft=lambda *a,**k:dict(passed=True,unit_only=True))
    monkeypatch.setattr(P,'skill_module',lambda *a:facade)
    return args,tmp_path,facade

def run(e):return S.validate(*e[0],task_dir=e[1],root=ROOT)

def test_actual_formula_literal_and_unmodified_review(evidence):
    p=run(evidence)
    assert p['claim_count']==61 and len(p['warnings'])==3
    assert p['warnings'][0]['formula']=='=I53+0.5' and p['warnings'][0]['numbers']==[.5]
    assert json.loads(Path(evidence[0][0]['path']).read_bytes())['passed'] is False

@pytest.mark.parametrize('fault',['contradiction','extra','wrong_number','wrong_formula','style','source_hash','mechanics','claim_finding'])
def test_substantive_or_unproved_findings_still_block(evidence,fault):
    a,tmp,facade=evidence
    if fault=='source_hash':
        a[6]={**a[6],'sha256':'0'*64}
    elif fault=='mechanics':facade.check_draft=lambda *a,**k:dict(passed=False)
    else:
        i=1 if fault=='wrong_formula' else 0;v=json.loads(Path(a[i]['path']).read_bytes())
        if fault=='contradiction':v['accuracy']['claims'][0]['must_say']='contradicted'
        if fault=='extra':v['accuracy']['extras'].append('An unsupported assumption.')
        if fault=='claim_finding':v['accuracy']['claims'][0]['findings']=['The source sign is wrong.']
        if fault=='wrong_number':v['accuracy']['extras'][0]=FINDING.replace('0.5','5.0')
        if fault=='wrong_formula':next(x for x in v['claims']if x.get('row_label')=='Period')['reviewer_only']['evidence']='=I53+5'
        if fault=='style':v['naturalness']['findings'].append('The source sign is incorrect.')
        p=tmp/'changed.json';p.write_text(json.dumps(v));a[i]=record(p)
    with pytest.raises(ValueError):run(evidence)
