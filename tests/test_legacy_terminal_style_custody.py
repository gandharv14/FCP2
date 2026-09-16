from pathlib import Path
import copy
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_staged_history as S
from scripts.batch import harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
HISTORY=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/histories/0027.json'

def test_actual_full_source_history_keeps_failed_review_and_finite_counts():
    row=L.binding(ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-staged-pipeline-002/terminal-style-candidate-002/0027.json')
    p=L.read(row);result=S.validate(row,at_ns=p['created_at_ns'])
    assert result['staged_history']['dialogue_rounds_upper_bound']==2
    assert result['staged_history']['new_authoring_authorized'] is False
    assert result['new_source_reconstruction_ordinal']==2
    assert result['audit_files']==L.inventory(result['audit_directory'])

def context():
    p=L.read(L.binding(HISTORY));h=p['staged_history']
    aa=next(v for k,v in h['namespaces'].items()if k.endswith('0027-additional-assumptions'))
    return p,aa,h['namespaces'][h['package']],L.read(p['tracker'])

def test_actual_failed_style_review_retained_without_quality_adoption():
    p,aa,files,old=context()
    S.terminal_style_application('0027',aa,files,old,2)
    assert L.read(aa['review.json'])['passed'] is False
    assert L.read(aa['apply.json'])['review_passed'] is False
    assert p['staged_history']['new_authoring_authorized'] is False
    with pytest.raises(ValueError):S.reviewed_dialogue_artifacts('0027',aa,files,2)

@pytest.mark.parametrize('defect',['accuracy','missing-claim','duplicate-claim','contradiction','extra','cell-leak','review-passed','round','no-style-finding','application-pass','marker-pass','draft-failed','summary-path','summary-verdict','summary-extra','count-reset','review-bytes'])
def test_custody_rejects_changed_or_contradictory_evidence(defect):
    p,aa,files,old=context();aa=copy.deepcopy(aa);old=copy.deepcopy(old)
    if defect=='summary-path':old['additional_assumptions']['review']='/foreign/review.json'
    if defect=='summary-verdict':old['additional_assumptions']['accuracy']='fail'
    if defect=='summary-extra':old['additional_assumptions']['new_authority']=True
    if defect=='review-bytes':aa['review.r2.json']['sha256']='0'*64
    real=L.read
    def read(row):
        v=copy.deepcopy(real(row));name=Path(row['path']).name
        if row==aa['review.json']:
            if defect=='accuracy':v['accuracy']['verdict']='fail'
            if defect=='missing-claim':v['accuracy']['claims'].pop()
            if defect=='duplicate-claim':v['accuracy']['claims'][1]=v['accuracy']['claims'][0]
            if defect=='contradiction':v['accuracy']['claims'][0]['must_say']='contradicted'
            if defect=='extra':v['accuracy']['extras']=['unsupported claim']
            if defect=='cell-leak':v['accuracy']['cell_refs_in_senior_turns']=['A1']
            if defect=='review-passed':v['passed']=True
            if defect=='round':v['round']=1
            if defect=='no-style-finding':v['naturalness']['findings']=[]
        if name=='apply.json'and defect=='application-pass':v['review_passed']=True
        if name=='dialogue-applied.json'and defect=='marker-pass':v['review_passed']=True
        if name=='draft-check.json'and defect=='draft-failed':v['passed']=False
        return v
    with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):
        S.terminal_style_application('0027',aa,files,old,0 if defect=='count-reset'else 2)
