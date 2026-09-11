from collections import Counter
from pathlib import Path
import json
import pytest
from openpyxl import Workbook
from scripts.batch import harbor_dialogue_operand_evidence as operands
from scripts.batch.harbor_readiness import skill_module,ROOT
from scripts.batch.harbor_admission import record
from xl_ast_graph import parse_formula

@pytest.fixture
def v():return skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')

@pytest.mark.parametrize('number',[0,1,-1,2,12,365,10000])
def test_every_target_collision_requires_its_own_occurrence_budget(v,tmp_path,monkeypatch,number):
    monkeypatch.setattr(v.disclose,'numeric_targets',lambda _: [float(number)])
    assert v.numeric_faults_with_budget(str(number),tmp_path,{})
    assert not v.numeric_faults_with_budget(str(number),tmp_path,Counter({float(number):1}))
    assert len(v.numeric_faults_with_budget(f'{number} then {number}',tmp_path,Counter({float(number):1})))==1
    assert v.numeric_faults_with_budget(str(number),tmp_path,None)

@pytest.mark.parametrize('number',[0,1,-1,2,12,365])
def test_exact_required_label_permits_only_one_occurrence(v,tmp_path,monkeypatch,number):
    monkeypatch.setattr(v.disclose,'numeric_targets',lambda _: [float(number)])
    budget=v.licensed_row_label_literals({'row_label':f'Period {number}'})
    assert not v.numeric_faults_with_budget(f'Period {number}',tmp_path,budget)
    assert v.numeric_faults_with_budget(f'Period {number}; the answer is {number}',tmp_path,budget)

@pytest.fixture
def context(tmp_path):
    source=tmp_path/'source.xlsx';book=Workbook();sheet=book.active;sheet.title='S'
    sheet['A1']='=CHOOSE(Circ,A3,-100)';sheet['B1']='=CHOOSE(Circ,B3,-100)';book.save(source)
    stage=tmp_path/'task';(stage/'tests').mkdir(parents=True)
    key=stage/'tests/answer_key.json';key.write_text(json.dumps({'targets':{'S!D4':42}}))
    (stage/'tests/disclosure.json').write_text(json.dumps({'golden':str(source),'golden_sha256':record(source)['sha256']}))
    claim={'record_id':'method::S::Interest::001','sheet':'S','reviewer_only':{'source':'custom_method_detector','evidence':'=CHOOSE(Circ,A3,-100)','cells':['S!A1','S!B1']}}
    claims={'claims':[claim]}
    draft='<!-- claim:method::S::Interest::001 -->\n**Analyst:** What is the fallback?\n\n**VP:** Use negative one hundred.\n'
    return source,stage,key,claims,draft

def project(context):
    source,stage,key,claims,draft=context
    return operands.project(claims,draft,source=record(source),answer_key=record(key),task_dir=stage)

def test_signed_exact_source_operand_is_review_evidence_not_target_or_approval(context):
    r=project(context);item=r['claims'][0]['operands'][0]
    assert item['value']==-100 and item['licensed_occurrences']==1
    assert item['observed_occurrences'][0]['text']=='negative one hundred'
    assert item['within_occurrence_budget'] and not item['target_collision']
    assert r['semantic_review_required'] and not r['target_values_disclosed']
    assert 'passed' not in r and set(r)=={'schema_version','source_sha256','answer_key_sha256','claims_sha256','draft_sha256','claims','target_values_disclosed','semantic_review_required'}

def test_source_operand_target_collision_and_extra_occurrence_are_explicit(context):
    source,stage,key,claims,draft=context;key.write_text(json.dumps({'targets':{'S!D4':-100}}))
    item=project(context)['claims'][0]['operands'][0]
    assert item['target_collision'] and item['within_occurrence_budget']
    with pytest.raises(ValueError,match='extra target occurrence'):
        project((source,stage,key,claims,draft+'\nNegative one hundred.'))


@pytest.mark.parametrize('change',['missing_source','source_bytes','formula','copied_formula','literal_text','target_formula','target_ref','missing_key','key_bytes','disclosure_source','claim_map'])
def test_missing_altered_or_target_derived_inputs_reject(context,change):
    source,stage,key,claims,draft=context;src=record(source);kb=record(key)
    if change=='missing_source':source.unlink()
    elif change=='source_bytes':source.write_bytes(b'corrupt')
    elif change in ('formula','copied_formula','literal_text'):
        from openpyxl import load_workbook
        book=load_workbook(source)
        if change=='literal_text':book['S']['A1'].data_type='s'
        else:book['S']['A1' if change=='formula' else 'B1']='=CHOOSE(Circ,A3,-99)'
        book.save(source)
        src=record(source);d=json.loads((stage/'tests/disclosure.json').read_text());d['golden_sha256']=src['sha256'];(stage/'tests/disclosure.json').write_text(json.dumps(d))
    elif change=='target_formula':key.write_text(json.dumps({'targets':{'S!A1':42}}));kb=record(key)
    elif change=='target_ref':key.write_text(json.dumps({'targets':{'S!A3':42}}));kb=record(key)
    elif change=='missing_key':key.unlink()
    elif change=='key_bytes':key.write_text('{}')
    elif change=='disclosure_source':(stage/'tests/disclosure.json').write_text('{}')
    else:draft=draft.replace('method::S::Interest::001','unknown')
    with pytest.raises((ValueError,FileNotFoundError)):
        operands.project(claims,draft,source=src,answer_key=kb,task_dir=stage)

@pytest.mark.parametrize('formula,expected',[('=-1',{-1.0:1}),('=+1',{1.0:1}),('=CHOOSE(A1,B1,-100)',{-100.0:1}),('=1+1',{1.0:2})])
def test_exact_signed_operand_accounting(formula,expected):
    assert operands.literal_operands(parse_formula(formula))==Counter(expected)

@pytest.mark.parametrize('number',[0,1,-1,2,12,365,-100])
def test_actual_formula_literal_budget_matches_sign_and_never_extra_target(v,tmp_path,number):
    (tmp_path/'tests').mkdir();(tmp_path/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!Z99':number}}))
    claim={'sheet':'S','reviewer_only':{'source':'custom_method_detector','evidence':f'=IF(A1,{number},A2)','cells':['S!B1']}}
    budget=v.licensed_formula_literals(claim,tmp_path)
    assert budget==Counter({float(number):1})
    assert not v.numeric_faults_with_budget(str(number),tmp_path,budget)
    assert v.numeric_faults_with_budget(f'{number} and {number}',tmp_path,budget)


def alias(formula,text):
    import re
    occurrences=[{'text':m.group(),'start':m.start(),'end':m.end()} for m in re.finditer(r'\bzero\b',text)]
    return operands.bound_restatement(parse_formula(formula),text,0.0,occurrences)


def test_source_max_zero_restatement_records_both_spans_and_one_bound():
    text='The row is the greater of that result and zero, so zero is the floor.'
    result=alias('=+MAX(A1-B1,0)',text)
    assert result['logical_operand_occurrences']==1 and len(result['observed_spans'])==2
    assert result['code']=='redundant_zero_floor_restatement'


@pytest.mark.parametrize('formula,text',[
    ('=MIN(A1,0)','The row is the greater of that result and zero, so zero is the floor.'),
    ('=-MAX(A1,0)','The row is the greater of that result and zero, so zero is the floor.'),
    ('=MAX(A1,0)+1','The row is the greater of that result and zero, so zero is the floor.'),
    ('=MAX(A1,1)','The row is the greater of that result and zero, so zero is the floor.'),
    ('=MAX(A1,0)','The row is the greater of that result and zero. The answer is zero.'),
    ('=MAX(A1,0)','The row is the greater of that result and zero, so zero is the floor. Output zero.'),
])
def test_unrelated_or_extra_zero_is_never_an_alias(formula,text):
    assert alias(formula,text) is None


def test_actual_project_accepts_only_proved_restatement(context):
    from openpyxl import load_workbook
    source,stage,key,claims,draft=context
    book=load_workbook(source);book['S']['A1']='=MAX(A3,0)';book['S']['B1']='=MAX(B3,0)';book.save(source)
    c=claims['claims'][0];c['reviewer_only']['evidence']='=MAX(A3,0)'
    key.write_text(json.dumps({'targets':{'S!D4':0}}))
    d=stage/'tests/disclosure.json';v=json.loads(d.read_text());v['golden_sha256']=record(source)['sha256'];d.write_text(json.dumps(v))
    draft=draft.replace('Use negative one hundred.','The row is the greater of that result and zero, so zero is the floor.')
    item=project((source,stage,key,claims,draft))['claims'][0]['operands'][0]
    assert item['within_occurrence_budget'] and item['target_collision'] and item['licensed_occurrences']==1
    assert len(item['observed_occurrences'])==2 and item['restatement_warning']
    with pytest.raises(ValueError,match='extra target occurrence'):
        project((source,stage,key,claims,draft+' The answer is zero.'))
