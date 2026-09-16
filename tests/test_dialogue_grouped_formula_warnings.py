"""Closed grouped allegations still require the original target-safe operand proof."""
from collections import Counter
import pytest
from scripts.batch import harbor_dialogue_source_warnings as S
A=['Senior turns for claims 001, 002, and 003 expose the numeric formula literal "100".', 'The senior turn for claim 005 exposes the numeric modelling target "0.5".']
N={'verdict':'fail','findings':["Several senior answers are near-copies of their cards' spoken lines, especially claims 005, 006, and 008.", 'The phrase "Add, as a plus" in claim 003 sounds rubric-driven rather than like natural Slack dialogue.']}
def test_group_mapping_retains_each_reported_literal():
 assert S.accuracy_findings(A,16)==({0:[100.0],1:[100.0],2:[100.0],4:[0.5]},[],'grouped_formula_constants')
@pytest.mark.parametrize('change',[
 lambda a:a+["The total is wrong."],lambda a:[a[0],a[1].replace('005','003')],
 lambda a:[a[0].replace('001','000'),a[1]],lambda a:[a[0].replace('003','017'),a[1]],
 lambda a:[a[0],a[1]+' Also the arithmetic is incorrect.'],lambda a:[a[1],a[0]]])
def test_unknown_or_ambiguous_accuracy_is_rejected(change):
 with pytest.raises(ValueError):S.accuracy_findings(change(A),16)
def test_style_requires_actual_senior_phrase():
 claims=[{'record_id':str(i)} for i in range(16)];senior={str(i):'' for i in range(16)};senior['2']='Add, as a plus, the specified financing fee.'
 assert S.grouped_formula_style(N,claims,senior)==N['findings']
 senior['2']='Use the specified financing fee.'
 with pytest.raises(ValueError):S.grouped_formula_style(N,claims,senior)
@pytest.mark.parametrize('finding',[N['findings'][0]+' Incorrect result.',N['findings'][0].replace('008','017')])
def test_style_is_not_an_accuracy_escape(finding):
 with pytest.raises(ValueError):S.grouped_formula_style({'verdict':'fail','findings':[finding,N['findings'][1]]},[{'record_id':str(i)} for i in range(16)],{'2':'Add, as a plus'})
