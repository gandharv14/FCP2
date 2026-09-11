"""Exercise actual maintained prompt extraction and unchanged owning gates."""
from pathlib import Path
from collections import Counter
import json
import pytest
from scripts.batch import harbor_dialogue_authoring as A
from scripts.batch import harbor_instruction_producer as P
ROOT=P.ROOT


@pytest.mark.parametrize('kind',['writer','paraphraser'])
def test_current_slot_prompt_preserves_entire_owning_hard_rules(kind):
    legacy=A.prompt(ROOT,kind,legacy=True);actual=A.prompt(ROOT,kind)
    assert legacy.count(A.END)==1
    assert actual.split('HARD RULES',1)[1]==legacy.split('HARD RULES',1)[1].replace(A.END,A.FORMAT)
    assert A.FORMAT in actual and A.END not in actual
    assert 'junior_scope' in actual and 'named ranges' in actual
    assert 'JSON-quoted text literal' in actual
    assert 'No numeric-answer exemption is granted' in actual or 'numeric-answer checks still apply' in actual
    assert 'Juniors should not mention them either' not in actual
    assert 'Never write A1, `cell`' not in actual
    assert 'whole-column' in actual and 'whole-row' in actual
    if kind=='paraphraser':
        assert 'Copy every junior slot string byte-for-byte; rewrite senior strings only.' in actual


def test_reviewer_actual_extraction_requires_full_independent_semantics():
    prompt=P.skill_prompt(ROOT,'additional-assumptions-dialogue','You are reviewing notes from a deal-team conversation')
    for clause in ('You did not write the','Cover every claim in the claims array exactly once, in order',
                   'both verdicts are pass','every\nmust_say is entailed',
                   'reviewer_only.reference_literal_proof','not an accuracy verdict',
                   'Fail unproved, extra or other-claim occurrences',
                   'Numeric-answer leakage remains forbidden without any new exemption',
                   'Missing or altered frozen junior_scope headers',
                   'Named ranges and row labels remain\n  distinct operand types'):
        assert clause in prompt


def validator():return P.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')


def test_source_literal_prompt_does_not_exempt_numeric_target(tmp_path):
    stage=tmp_path/'0001-outputs';(stage/'tests').mkdir(parents=True)
    (stage/'tests/answer_key.json').write_text(json.dumps({'targets':{'Ops!B2':17},'tolerance':{'numeric_abs':1e-6,'numeric_rel':1e-6}}))
    v=validator()
    assert v.numeric_faults_with_budget('The text literal "17" is the final answer.',stage,Counter())
    assert v.numeric_faults_with_budget('For `Ops!B3`: The answer is 17.',stage,Counter())


@pytest.mark.parametrize('mutation',['naturalness','contradiction','extra','address','missing'])
def test_required_review_cannot_be_replaced_by_literal_proof(mutation):
    claims={'claims':[{'record_id':'one','must_say':['the text literal "B3"'],'reviewer_only':{'reference_literal_proof':{'occurrence_limit':1}}}]}
    review={'agent_model':'gpt-5.6-sol-high','round':2,'accuracy':{'verdict':'pass','claims':[{'record_id':'one','must_say':'entailed','findings':[]}],'extras':[],'cell_refs_in_senior_turns':[]},'naturalness':{'verdict':'pass','findings':[]},'passed':True}
    v=validator();assert v.review_passed(review,claims)
    if mutation=='naturalness':review['naturalness']['verdict']='fail'
    if mutation=='contradiction':review['accuracy']['claims'][0]['must_say']='contradicted'
    if mutation=='extra':review['accuracy']['extras']=['unlicensed factual assertion']
    if mutation=='address':review['accuracy']['cell_refs_in_senior_turns']=['Ops!B3']
    if mutation=='missing':review['accuracy']['claims']=[]
    assert not v.review_passed(review,claims)
