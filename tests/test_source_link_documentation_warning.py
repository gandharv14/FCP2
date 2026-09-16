import copy
import pytest
from scripts.batch.harbor_readiness import skill_module,ROOT

@pytest.fixture
def case():
    c={'claims':[dict(record_id=f'source::{i}',row_label=f'Row {i}',spoken=f'Use source row {i}.',reviewer_only={'entry':'source_selection'})for i in range(24)]}
    d='\n\n'.join('<!-- claim:'+r['record_id']+' -->\n**Analyst:** Which row feeds it?\n\n**VP:** '+r['spoken'] for r in c['claims'])
    findings=['The long run of source-link exchanges is holistically checklist-like, with junior questions repeatedly asking which row feeds or drives another row.','Despite some surface variation, the repeated two-turn row-by-row confirmations read more like structured documentation than a natural Slack conversation.']
    r=dict(passed=False,round=2,accuracy=dict(verdict='pass',claims=[dict(record_id=x['record_id'],must_say='entailed',findings=[])for x in c['claims']],extras=[],cell_refs_in_senior_turns=[]),naturalness=dict(verdict='fail',findings=findings))
    return c,d,r

@pytest.mark.parametrize('change',[None,'noncontiguous','not_source','missing_turn','missing_spoken','accuracy','extra','cellref','missing_claim','substantive_suffix'])
def test_exact_style_requires_source_run_and_complete_accuracy(case,change):
    c,d,r=copy.deepcopy(case)
    if change=='noncontiguous':c['claims'][12]['reviewer_only']['entry']='method'
    elif change=='not_source':
        for x in c['claims']:x['reviewer_only']['entry']='method'
    elif change=='missing_turn':d=d.replace('**Analyst:** Which row feeds it?','',1)
    elif change=='missing_spoken':c['claims'][0]['spoken']=''
    elif change=='accuracy':r['accuracy']['claims'][0]['must_say']='contradicted'
    elif change=='extra':r['accuracy']['extras']=['Unsupported numeric target']
    elif change=='cellref':r['accuracy']['cell_refs_in_senior_turns']=['D99']
    elif change=='missing_claim':r['accuracy']['claims'].pop()
    elif change=='substantive_suffix':r['naturalness']['findings'][0]+=' It reverses the source calculation.'
    V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue');warnings=V.review_warnings(r,c,draft_text=d)
    assert (len(warnings)==2 and V.review_passed(r,c,draft_text=d))if change is None else not V.review_passed(r,c,draft_text=d)
    assert r['passed']is False
