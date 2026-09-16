"""Exact source operand classification never exempts a graded cell or value."""
from collections import Counter
from pathlib import Path
import json
import pytest
from scripts.batch import harbor_dialogue_source_warnings as S
from scripts.batch import harbor_instruction_producer as P
from xl_ast_graph import parse_formula

FINDING='The senior turns for claims 001 through 007 disclose numeric literals that look like graded targets, including the calculation inputs and resulting signed assumptions.'

def test_closed_interval_keeps_all_original_indices():
    values,prefixes,variant=S.accuracy_findings([FINDING],11,[{}]*11)
    assert values==dict.fromkeys(range(7)) and prefixes==[] and variant=='nongraded_source_input_interval'

@pytest.mark.parametrize('text',[FINDING.replace('007','012'),FINDING.replace('001','000'),FINDING.replace('001 through 007','007 through 001'),FINDING+' Unsupported result.'])
def test_foreign_or_partial_allegation_refused(text):
    with pytest.raises(ValueError):S.accuracy_findings([text],11,[{}]*11)

@pytest.fixture
def case(tmp_path):
    v=P.skill_module(Path(__file__).resolve().parents[1],'additional-assumptions-dialogue','validate_dialogue')
    (tmp_path/'tests').mkdir();(tmp_path/'tests/answer_key.json').write_text(json.dumps({'targets':{'Output!A1':42},'tolerance':{'numeric_abs':1e-6,'numeric_rel':1e-6}}))
    c={'sheet':'Inputs','reviewer_only':{'source':'custom_method_detector','cells':["Inputs!B1"],'evidence':'=-29.631-7.326'}}
    return v,c,tmp_path

def test_actual_owning_license_and_numeric_target_gate(case):
    v,c,p=case;l=S._literals(parse_formula(c['reviewer_only']['evidence']))
    assert Counter(S.nongraded_input_literals(v,c,l,'Use (-29.631) minus 7.326.',p))==l

@pytest.mark.parametrize('defect',['graded_cell','graded_reference','graded_value','new_number','computed_result','missing_operand'])
def test_actual_owning_target_rejections(case,defect):
    v,c,p=case;text='Use (-29.631) minus 7.326.'
    if defect=='graded_cell':c['reviewer_only']['cells']=['Output!A1']
    elif defect=='graded_reference':c['reviewer_only']['evidence']='=Output!A1-29.631-7.326'
    elif defect=='graded_value':(p/'tests/answer_key.json').write_text(json.dumps({'targets':{'Output!A1':-29.631}}))
    elif defect=='new_number':text+=' Then use 3.'
    elif defect=='computed_result':text='Use -36.957.'
    else:text='Use -29.631.'
    l=S._literals(parse_formula(c['reviewer_only']['evidence']))
    with pytest.raises(ValueError):S.nongraded_input_literals(v,c,l,text,p)

def test_explicit_negative_word_is_a_sign_not_a_subtraction_inference(case):
    v,c,p=case;l=S._literals(parse_formula(c['reviewer_only']['evidence']))
    text=S.explicit_negative_literals('Start at negative 29.631, then minus 7.326.')
    assert Counter(S.nongraded_input_literals(v,c,l,text,p))==l
    assert S.explicit_negative_literals('Subtract 29.631 then flip the sign.')=='Subtract 29.631 then flip the sign.'
    with pytest.raises(ValueError):S.nongraded_input_literals(v,c,l,'Start at 29.631 then minus 7.326.',p)
