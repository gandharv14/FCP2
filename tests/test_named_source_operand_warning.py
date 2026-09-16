import copy
import pytest
from scripts.batch import harbor_dialogue_source_warnings as W

IDS=['method_discounting::2.0::Participation Rate| % Growth::001',
     'method_discounting::2.0::Participation Rate| % Growth::002']
TEXT=f'The senior turns for {IDS[0]} and {IDS[1]} disclose the numeric literals 0.005 and 0.01, which look like graded targets and are not must_say clauses.'

def test_named_pair_maps_exact_distinct_claims():
    rows=[{'record_id':i}for i in IDS]
    assert W.accuracy_findings([TEXT],2,rows)==({0:[.005],1:[.01]},[],'named_source_operand_pair')

def test_actual_same_prefix_shorthand_is_unambiguous():
    rows=[{'record_id':i}for i in IDS]
    actual=TEXT.replace(IDS[1],'::002')
    assert W.accuracy_findings([actual],2,rows)==({0:[.005],1:[.01]},[],'named_source_operand_pair')
    rows[1]['record_id']='other::002'
    with pytest.raises(ValueError):W.accuracy_findings([actual],2,rows)

@pytest.mark.parametrize('case',['missing','duplicate','extra','substantive_suffix'])
def test_named_pair_rejects_ambiguous_or_substantive_findings(case):
    rows=[{'record_id':i}for i in IDS];extras=[TEXT]
    if case=='missing':rows[1]['record_id']='unrelated'
    if case=='duplicate':rows[1]['record_id']=IDS[0]
    if case=='extra':extras.append('A required formula clause is missing.')
    if case=='substantive_suffix':extras[0]+=' The calculation is numerically incorrect.'
    with pytest.raises(ValueError):W.accuracy_findings(extras,2,rows)

def test_formula_operand_still_rejects_graded_value_collision():
    from collections import Counter
    class D:
        @staticmethod
        def numeric_targets(task):return [.005]
        @staticmethod
        def same_number(a,b):return a==b
    class V:
        disclose=D
        @staticmethod
        def licensed_formula_literals(claim,task):return Counter({.005:1})
    with pytest.raises(ValueError,match='equals a graded target'):
        W.nongraded_input_literals(V,{},Counter({.005:1}),'add 0.005','/tmp')
