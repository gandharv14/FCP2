from pathlib import Path
import pytest
from scripts.fleet import publication_history_0190 as H,publish as P


def actual():
    path=Path(__file__).resolve().parents[1]/'diagnostics/vm-fleet-20260912/holds-0190/publication-history-001.json'
    if not path.exists():pytest.skip('optional retained original/current0190 history fixture')
    return P.binding(path)


def test_actual_history_preserves_existing_admission_and_full_central_presence():
    h=H.history(P,actual())
    assert h['workbook']=='0190' and h['fresh_admission_allowed'] is False and h['central_histories']['0190']
    assert h['source']['sha256']==H.SOURCE


@pytest.mark.parametrize('defect',['fresh','workbook','source','roots','omission'])
def test_changed_retained_history_authority_rejected(tmp_path,defect):
    h=P.checked(actual())
    if defect=='fresh':h['fresh_admission_allowed']=True
    elif defect=='workbook':h['workbook']='0006'
    elif defect=='source':h['source']['sha256']='f'*64
    elif defect=='roots':h['roots']=[]
    else:del h['central_histories']['0190']
    with pytest.raises(ValueError):H.history(P,P.retain(tmp_path/'bad-history.json',h))


@pytest.mark.parametrize('name',['history','continuation_history','source_continuation'])
def test_other_admission_kind_cannot_authorize0190(name):
    with pytest.raises(ValueError,match='fresh/source-only'):
        H.validate(P,dict(workbook='0190',**{name:{'path':'/unread','sha256':'f'*64}}))


def test_missing_actual_refresh_cannot_be_certified(tmp_path):
    plan=dict(workbook='0190',existing_review_history=actual(),existing_review_lineage=P.retain(tmp_path/'lineage.json',dict(schema_version=H.LINEAGE,model_calls=0,fresh_admission=False)))
    with pytest.raises(ValueError,match='closed0190 refresh'):H.validate(P,plan)
