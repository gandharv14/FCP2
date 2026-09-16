"""Source row-label metadata must not license numbers elsewhere in a question."""
import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.batch import harbor_instruction_producer as P
ROOT=Path(__file__).resolve().parents[1]
V=P.skill_module(ROOT,'additional-assumptions-dialogue','validate_dialogue')
LABEL='CONSTRUCTION INTEREST @ 11% ON 1ST 8 HOMES'
CELL='S!D18'
@pytest.fixture
def case(tmp_path):
    claim=dict(record_id='one',sheet='S',row_label=LABEL,junior_scope=[CELL],must_say=['the row labelled '+json.dumps(LABEL)],reviewer_only=dict(source='custom_method_detector',cells=[CELL],evidence='=(D16+D17)/25*0.11*8'))
    gold=SimpleNamespace(row_label=lambda cell:LABEL,formula={CELL:claim['reviewer_only']['evidence']})
    text='For `S!D18`: How should I calculate the row labelled '+json.dumps(LABEL)+'?'
    (tmp_path/'tests').mkdir();(tmp_path/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!Z1':1.0}}))
    return claim,gold,text,tmp_path

def test_one_exact_junior_label_with_independent_source_formula(case):
    c,g,t,task=case;spans=[]
    assert V.numeric_faults_with_budget(t,task)==['numeric literal 1 matches target 1.0']
    masked=V.source_metadata_mask(t,c,g,junior=True,spans=spans)
    assert not V.numeric_faults_with_budget(masked,task)
    assert len(masked)==len(t) and len(spans)==1
    assert t[spans[0][1]:spans[0][2]]==json.dumps(LABEL)
    assert V.source_metadata_mask(t,c,g,junior=False)==t

@pytest.mark.parametrize('defect',['source','scope','formula','label','required','unquoted','duplicate','extra_answer'])
def test_no_budget_for_unproved_label_or_numbers_outside_exact_span(case,defect):
    c,g,t,task=case
    if defect=='source':g=None
    elif defect=='scope':c['junior_scope']=['S!Z1']
    elif defect=='formula':c['reviewer_only']['evidence']='=1'
    elif defect=='label':g.row_label=lambda cell:'Other'
    elif defect=='required':c['must_say']=[]
    elif defect=='unquoted':t=t.replace('"','')
    elif defect=='duplicate':t+=' Again, the row labelled '+json.dumps(LABEL)
    elif defect=='extra_answer':t+=' Is the answer 1?'
    masked=V.source_metadata_mask(t,c,g,junior=True)
    assert V.numeric_faults_with_budget(masked,task)
