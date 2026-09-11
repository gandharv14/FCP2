"""Narrow scope-prefix warnings do not waive substantive dialogue checks."""
import copy
import importlib
from pathlib import Path

import pytest

@pytest.fixture
def validator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts'))
    return importlib.import_module('validate_dialogue')

@pytest.fixture
def example():
    claim={'record_id':'method::Ops::Metric::001','sheet':'Ops','row_label':'Metric','junior_scope':['Ops!C16'], 'must_say':['only for this cell']}
    finding='The frozen junior_scope header is altered by inserting "Ops:" before the required scoped-cell prefix.'
    review={'agent_model':'gpt-5.6-sol-high','round':2,'accuracy':{'verdict':'fail','claims':[{'record_id':claim['record_id'],'must_say':'entailed','findings':[finding]}],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':False}
    draft='<!-- claim:method::Ops::Metric::001 -->\n**Analyst:** Ops: For `Ops!C16`: What happens here?\n**VP:** Use this calculation only for this cell.\n'
    return {'claims':[claim]},review,draft

def test_scope_prefix_warning_requires_actual_draft_and_preserves_review(validator,example):
    claims,review,draft=example;old=copy.deepcopy(review)
    assert not validator.review_passed(review,claims)
    assert validator.review_passed(review,claims,draft_text=draft)
    assert validator.review_warnings(review,claims,draft_text=draft)[0]['code']=='redundant_sheet_scope_prefix'
    assert review==old

@pytest.mark.parametrize('change',['missing','contradiction','unknownfinding','extra','leak','wrongscope','wrongsheet','naturalness','claimorder'])
def test_substantive_or_unproved_rejections_remain_blocking(validator,example,change):
    claims,review,draft=example
    if change=='missing':review['accuracy']['claims'][0]['must_say']='missing'
    elif change=='contradiction':review['accuracy']['claims'][0]['must_say']='contradicted'
    elif change=='unknownfinding':review['accuracy']['claims'][0]['findings'].append('The calculation sign is wrong.')
    elif change=='extra':review['accuracy']['extras']=['An unsupported input was invented.']
    elif change=='leak':review['accuracy']['cell_refs_in_senior_turns']=['C16']
    elif change=='wrongscope':draft=draft.replace('Ops!C16','Ops!D16')
    elif change=='wrongsheet':draft=draft.replace('Ops: For','Other: For')
    elif change=='naturalness':review['naturalness']={'verdict':'fail','findings':['Forced repetition.']}
    else:review['accuracy']['claims'][0]['record_id']='unknown'
    assert not validator.review_passed(review,claims,draft_text=draft)

def test_header_finding_in_extras_only_is_warning(validator,example):
    claims,review,draft=example
    review['accuracy']['extras']=review['accuracy']['claims'][0].pop('findings')
    assert validator.review_passed(review,claims,draft_text=draft)

def test_pure_style_restatement_is_warning_without_review_rewrite(validator,example):
    claims,review,draft=example;review['round']=1
    review['naturalness']={'verdict':'fail','findings':['The VP response is repetitive and forced: "plus, don’t populate it" merely restates "leave ... empty" and reads like wording inserted to satisfy a rubric rather than natural Slack dialogue.']}
    old=copy.deepcopy(review)
    assert validator.review_passed(review,claims,draft_text=draft)
    assert {w['code'] for w in validator.review_warnings(review,claims,draft_text=draft)}=={'redundant_sheet_scope_prefix','dialogue_style_only'}
    assert review==old
    review['accuracy']['claims'][0]['must_say']='missing'
    review['accuracy']['claims'][0]['findings']=['Required multiplication operation missing.']
    assert not validator.review_passed(review,claims,draft_text=draft)


def test_style_only_does_not_excuse_unexplained_accuracy_failure(validator,example):
    claims,review,draft=example
    review['accuracy']['claims'][0]['findings']=[]
    review['naturalness']={'verdict':'fail','findings':['Repetitive phrasing']}
    assert not validator.review_passed(review,claims,draft_text=draft)
    review['accuracy']['verdict']='pass'
    assert validator.review_passed(review,claims,draft_text=draft)
    review['naturalness']['findings'].append('The calculation is ambiguous.')
    assert not validator.review_passed(review,claims,draft_text=draft)


@pytest.mark.parametrize('header',['junior scope','junior_scope'])
@pytest.mark.parametrize('shared',['','shared '])
@pytest.mark.parametrize('verb',['inserting','prepending'])
def test_scope_finding_variants_require_same_actual_claim_mapping(validator,example,header,shared,verb):
    claims,review,draft=example
    review['accuracy']['claims'][0]['findings']=[f'The {shared}frozen {header} header is altered by {verb} "Ops:" before the required scoped-cell prefix.']
    original=copy.deepcopy(review)
    assert validator.review_passed(review,claims,draft_text=draft)
    assert review==original
    assert not validator.review_passed(review,claims,draft_text=draft.replace('Ops!C16','Ops!D16'))
    review['accuracy']['claims'][0]['findings'].append('Required multiplication is missing.')
    assert not validator.review_passed(review,claims,draft_text=draft)


def test_shared_scope_warning_requires_complete_ordered_authenticated_header(validator,example):
    claims,review,draft=example
    first=claims['claims'][0];second={**first,'record_id':'method::Ops::Metric::002','junior_scope':['Ops!D16']}
    claims['claims'].append(second)
    claims['shared_source_links']={'groups':[[first['record_id'],second['record_id']]]}
    finding='The shared frozen junior scope header is altered by inserting "Ops:" before the required scoped-cell prefix.'
    review['accuracy']['claims']=[{'record_id':c['record_id'],'must_say':'entailed','findings':[finding]} for c in claims['claims']]
    draft='<!-- claim:method::Ops::Metric::001 -->\n<!-- claim:method::Ops::Metric::002 -->\n**Analyst:** Ops: For `Ops!C16`; For `Ops!D16`: What happens here?\n**VP:** Use this calculation only for these cells.\n'
    assert validator.review_passed(review,claims,draft_text=draft)
    assert len(validator.review_warnings(review,claims,draft_text=draft))==2
    assert not validator.review_passed(review,claims,draft_text=draft.replace('For `Ops!D16`','For `Ops!E16`'))
    claims['shared_source_links']['groups'][0].reverse()
    assert not validator.review_passed(review,claims,draft_text=draft)
