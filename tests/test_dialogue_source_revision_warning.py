import copy,json
from pathlib import Path
import pytest
from scripts.batch.harbor_readiness import skill_module,ROOT
V=skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
H=ROOT/'diagnostics/bulk-production/0078-source-revision-001'

def fixture():
    value=json.loads(json.loads((H/'review-result-001/native.json').read_text())['stdout'])
    review=value['review'];claims=json.loads((H/'custody-001/00-claims.json').read_text());draft=(H/'candidate-002/draft.md').read_text()
    return review,claims,draft

def test_exact_actual_third_review_style_warning_preserves_rejection():
    r,c,d=fixture();before=copy.deepcopy(r)
    assert len(c['claims'])==57 and V.review_accuracy_passed(r,c)
    assert V.review_warnings(r,c,draft_text=d)==[dict(code='dialogue_style_only',finding=r['naturalness']['findings'][0])]
    assert V.review_passed(r,c,draft_text=d) and r==before and r['passed']is False and r['round']==3

@pytest.mark.parametrize('change',['contradicted','missing','extras','leak','unknown_finding','substantive_finding','wrong_order','duplicate'])
def test_warning_does_not_cover_source_or_schema_errors(change):
    r,c,d=fixture();r=copy.deepcopy(r)
    if change in ('contradicted','missing'):r['accuracy']['claims'][0]['must_say']=change
    elif change=='extras':r['accuracy']['extras']=['Unsupported modeling instruction']
    elif change=='leak':r['accuracy']['cell_refs_in_senior_turns']=['F38']
    elif change=='unknown_finding':r['naturalness']['findings']=['Unrecognized concern']
    elif change=='substantive_finding':r['naturalness']['findings'].append('The sign flips only the hurdle product.')
    elif change=='wrong_order':r['accuracy']['claims'].reverse()
    else:r['accuracy']['claims'][1]=copy.deepcopy(r['accuracy']['claims'][0])
    assert not V.review_passed(r,c,draft_text=d)
