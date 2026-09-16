from scripts.batch import harbor_dialogue_first_round_warning as W
from scripts.batch.harbor_readiness import skill_module,ROOT
import copy
import pytest

@pytest.fixture
def case():
    rows=[dict(record_id='npv::1',row_label='NPV',reviewer_only={'entry':'npv_timing'}),dict(record_id='npv::2',row_label='NPV',reviewer_only={'entry':'npv_timing'}),dict(record_id='projection::3',row_label='Margin',reviewer_only={'entry':'projection_rule'}),dict(record_id='source::4',row_label='Net Income',reviewer_only={'entry':'source_selection'})]
    draft='\n\n'.join('<!-- claim:'+r['record_id']+' -->\n**Analyst:** Which convention?\n\n**VP:** Use '+r['row_label']+'.'for r in rows)
    findings=['The 2 consecutive NPV exchanges repeat the same junior-question pattern with superficial rewording, making the file read like a checklist rather than Slack.',"Several projection and source-selection senior turns are near-copies of the cards' spoken lines, including the Net Income response for claim ::004.",'The repeated two-turn structure and rubric-like recitation of long row labels make the overall conversation read as generated documentation rather than a natural deal-team exchange.']
    return {'claims':rows},draft,findings

@pytest.mark.parametrize('fault',[None,'count','missing_claim','noncontiguous','wrong_source','wrong_label','unknown_meaning'])
def test_exact_presentation_finding_requires_real_claim_topology(case,fault):
    c,d,findings=case;c=copy.deepcopy(c)
    if fault=='count':findings[0]=findings[0].replace('2 consecutive','3 consecutive')
    elif fault=='missing_claim':d=d[:d.index('<!-- claim:source')]
    elif fault=='noncontiguous':c['claims'][1]['reviewer_only']['entry']='source_selection'
    elif fault=='wrong_source':c['claims'][-1]['reviewer_only']['entry']='method'
    elif fault=='wrong_label':findings[1]=findings[1].replace('Net Income','Foreign')
    elif fault=='unknown_meaning':findings[2]+=' The answer reverses the sign.'
    observed=[W.style_only(f,c,d)for f in findings]
    assert all(observed) if fault is None else not all(observed)


def test_style_classifier_never_discards_accuracy_extra(case):
    c,d,findings=case;V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
    review=dict(round=3,passed=False,accuracy=dict(verdict='fail',claims=[dict(record_id=r['record_id'],must_say='entailed',findings=[])for r in c['claims']],extras=['unlicensed numeric target'],cell_refs_in_senior_turns=[]),naturalness=dict(verdict='fail',findings=findings))
    assert V.review_warnings(review,c,draft_text=d)==[]
    assert V.review_faults(review,c,draft_text=d)and not V.review_passed(review,c,draft_text=d)

@pytest.mark.parametrize('variant',['verbatim','whole','different','two_insertions'])
def test_exact_spoken_copy_or_single_whole_value_restatement(case,variant):
    c,d,findings=case;c['claims'][-1]['spoken']='Net Income takes its value from Source.'
    text=c['claims'][-1]['spoken']
    if variant=='whole':text=text.replace('its value','its whole value')
    elif variant=='different':text='Net Income equals 7.'
    elif variant=='two_insertions':text=text.replace('its value','its whole entire value')
    d=d.replace('Use Net Income.',text)
    f="The senior response for claim ::004 reproduces the card's spoken line verbatim, and several projection and source-selection responses are near-copies of their spoken lines."
    assert W.style_only(f,c,d)==(variant in('verbatim','whole'))
