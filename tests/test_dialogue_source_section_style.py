import copy
import pytest
from scripts.batch.harbor_readiness import skill_module,ROOT

FINDINGS=[
'The long P&L Output section is holistically checklist-like: dozens of isolated two-turn exchanges repeat the same source-row question and full-value answer pattern across adjacent periods.',
'Many senior source-selection answers are near-copies of the cards\' spoken construction, changing little beyond synonyms such as "full," "whole," "supply," and "feed," so the file reads more like generated model documentation than Slack.',
]

def fixture():
    rows=[dict(record_id=f'source_selection::P&L Output::Revenue::{i:03}',sheet='P&L Output',row_label='Revenue',spoken='Use the row labelled Revenue from the source.',reviewer_only={'entry':'source_selection'})for i in range(24)]
    draft='\n\n'.join('<!-- claim:'+r['record_id']+' -->\n**Analyst:** Which source applies?\n\n**VP:** '+r['spoken']for r in rows)
    review=dict(passed=False,naturalness=dict(verdict='fail',findings=FINDINGS.copy()),accuracy=dict(verdict='pass',claims=[dict(record_id=r['record_id'],must_say='entailed',findings=[])for r in rows],extras=[],cell_refs_in_senior_turns=[]))
    return {'claims':rows},draft,review

@pytest.mark.parametrize('change',[None,'contradiction','missing_claim','extra','cellref','different_sheet','different_entry','missing_spoken','missing_turn','substantive_suffix','unrecognized_finding'])
def test_exact_retained_style_preserves_substantive_gates(change):
    c,d,r=fixture();before=copy.deepcopy(r)
    if change=='contradiction':r['accuracy']['claims'][0]['must_say']='contradicted'
    elif change=='missing_claim':r['accuracy']['claims'].pop()
    elif change=='extra':r['accuracy']['extras']=['Unsupported numerical assumption.']
    elif change=='cellref':r['accuracy']['cell_refs_in_senior_turns']=['C99']
    elif change=='different_sheet':c['claims'][0]['sheet']='Other'
    elif change=='different_entry':c['claims'][0]['reviewer_only']['entry']='custom_method'
    elif change=='missing_spoken':c['claims'][0]['spoken']=''
    elif change=='missing_turn':d=d.replace('**Analyst:** Which source applies?','',1)
    elif change=='substantive_suffix':r['naturalness']['findings'][0]+=' A required clause is missing.'
    elif change=='unrecognized_finding':r['naturalness']['findings'][0]='Incorrect source meaning.'
    V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    warnings=V.review_warnings(r,c,draft_text=d)
    assert bool(warnings)==(change is None)
    if change is None:
        assert len(warnings)==2 and all(w['code']=='dialogue_style_only'for w in warnings)
        assert r==before and r['passed'] is False
