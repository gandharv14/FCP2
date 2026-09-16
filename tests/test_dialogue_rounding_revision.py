import copy
import hashlib
import pytest
from openpyxl import Workbook
from scripts.fleet.dialogue_source_revision import repair


def case(tmp_path,formula='=ROUND(MAX(B2,C2),0)',wording='round it to the nearest multiple of 0',count=1):
    w=Workbook();s=w.active;s.title='Matrix';claims=[];parts=[];selected=[]
    for i in range(count):
        cell=f'D{i+2}';s[cell]=formula;rid=f'round-{i}'
        claims.append(dict(record_id=rid,sheet='Matrix',junior_scope=['Matrix!'+cell],
            reviewer_only=dict(source='custom_method_detector',cells=['Matrix!'+cell],evidence=formula)))
        parts.append(f'<!-- claim:{rid} -->\n**Analyst:** What rule?\n\n**VP:** Take the greater amount, then {wording}.\n\n')
        selected.append(dict(record_id=rid,kind='decimal_rounding'))
    claims.append(dict(record_id='unchanged'))
    parts.append('<!-- claim:unchanged -->\n**VP:** Keep the exact source operands 6 plus 1.\n')
    p=tmp_path/'source.xlsx';w.save(p)
    return p,dict(claims=claims),''.join(parts),selected


@pytest.mark.parametrize('wording',['round it to the nearest multiple of 0','rounded to the nearest multiple of 0','round to the nearest multiple of 0','round the greater value to the nearest multiple of 0'])
def test_source_precision_repair_preserves_every_other_byte(tmp_path,wording):
    p,c,d,s=case(tmp_path,wording=wording);before=hashlib.sha256(p.read_bytes()).hexdigest();original=copy.deepcopy(c)
    fixed,proof=repair(d,c,p,s)
    assert fixed==d.replace('to the nearest multiple of 0','to 0 decimal places')
    assert c==original and hashlib.sha256(p.read_bytes()).hexdigest()==before
    assert proof['unchanged_claims']==1 and proof['model_calls_added']==0 and proof['qualified']is False
    assert proof['edits'][0]['meaning']['operator']=='decimal_rounding'
    with pytest.raises(ValueError):repair(fixed,c,p,s)


@pytest.mark.parametrize('formula',['=MROUND(MAX(B2,C2),0)','=ROUND(MAX(B2,C2),2)','=ROUND(ROUND(B2,1),0)','=ROUND(B2,C2)','=ROUND(B2,0)+1'])
def test_different_source_operation_cannot_be_reinterpreted(tmp_path,formula):
    p,c,d,s=case(tmp_path,formula=formula)
    with pytest.raises(ValueError):repair(d,c,p,s)


def test_junior_and_required_source_labels_cannot_be_edited(tmp_path):
    p,c,d,s=case(tmp_path)
    with pytest.raises(ValueError,match='senior'):repair(d.replace('**VP:** Take','**Analyst:** Take'),c,p,s)
    c['claims'][0]['must_say']=['the row labelled "round it to the nearest multiple of 0"']
    with pytest.raises(ValueError,match='source label'):repair(d,c,p,s)


def test_larger_source_rounding_family_remains_bounded(tmp_path):
    p,c,d,s=case(tmp_path,count=15);_,proof=repair(d,c,p,s)
    assert len(proof['edits'])==15 and proof['unchanged_claims']==1
    with pytest.raises(ValueError,match='bounded'):repair(d,c,p,[dict(record_id=str(i),kind='decimal_rounding')for i in range(33)])
    with pytest.raises(ValueError,match='remains eight'):repair(d,c,p,[dict(record_id=str(i),kind='negated_sum')for i in range(9)])
