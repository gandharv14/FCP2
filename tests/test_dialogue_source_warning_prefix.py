from collections import Counter
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_dialogue_source_warnings as W
FINDING='The first seven senior turns disclose numeric literals that look like graded targets, including the detailed calculation inputs and fixed values.'
def claims(n=11):return [dict(reviewer_only=dict(source='custom_method_detector'))for _ in range(n)]

def test_closed_prefix_only_identifies_the_alleged_seven_formula_claims():
    indices,prefixes,variant=W.accuracy_findings([FINDING],11,claims())
    assert indices==dict.fromkeys(range(7))and prefixes==[]and variant=='nongraded_source_input_prefix'

@pytest.mark.parametrize('change',['count','source','extra','substance','unknown'])
def test_prefix_does_not_accept_other_scope_or_allegations(change):
    rows=claims();extras=[FINDING];count=11
    if change=='count':rows=claims(6);count=6
    elif change=='source':rows[3]['reviewer_only']['source']='source_selection'
    elif change=='extra':extras.append('The formula omits a summation.')
    elif change=='substance':extras[0]=FINDING.replace('numeric literals','incorrect numeric calculations')
    elif change=='unknown':extras[0]=FINDING.replace('seven','eleven')
    with pytest.raises(ValueError):W.accuracy_findings(extras,count,rows)

@pytest.mark.parametrize('fault',[None,'changed','target','unlicensed','implicit_sign'])
def test_full_operand_proof_preserves_sign_multiplicity_and_target_exclusion(fault):
    literals=Counter({-29.631:1,7.326:1});text='negative 29.631 minus 7.326'
    licensed=literals;targets=[]
    if fault=='changed':text='negative 29.631 minus 7.327'
    elif fault=='target':targets=[-29.631]
    elif fault=='unlicensed':licensed=Counter({7.326:1})
    elif fault=='implicit_sign':text='subtract 29.631 and 7.326'
    validator=SimpleNamespace(licensed_formula_literals=lambda c,t:licensed,disclose=SimpleNamespace(numeric_targets=lambda t:targets,same_number=lambda a,b:a==b))
    if fault:
        with pytest.raises(ValueError):W.nongraded_input_literals(validator,{},literals,W.explicit_negative_literals(text),'/task')
    else:assert Counter(W.nongraded_input_literals(validator,{},literals,W.explicit_negative_literals(text),'/task'))==literals
