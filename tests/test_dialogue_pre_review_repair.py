"""A fixed-cell prose repair preserves source math and original author evidence."""
import importlib,json
from pathlib import Path
import pytest
from openpyxl import Workbook
from scripts.batch import harbor_dialogue_pre_review_repair as Q
from scripts.batch.harbor_admission import record
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def source(tmp_path):
    wb=Workbook();s=wb.active;s.title='Forecast'
    s['B5']='Term loan interest expense';s['C5']=0.03;s['B10']='Term loan';s['F10']=10
    s['F12']='=F10*($C$5+F6)'
    p=tmp_path/'source.xlsx';wb.save(p)
    old='Keep the text literal "\'Forecast\'!C5" fixed as the formula moves across; the other references shift with position.'
    claim=dict(record_id='debt::Forecast::Loan::001',sheet='Forecast',must_say=['locked input','the row labelled "Term loan interest expense"'],reviewer_only=dict(source='custom_method_detector',evidence='=F10*($C$5+F6)',cells=["'Forecast'!F12"],steps="'Forecast'!C5 stays fixed while every other reference shifts"))
    draft='<!-- claim:'+claim['record_id']+' -->\n**VP:** Calculate the interest. '+old+'\n'
    return p,{'claims':[claim]},draft,old

def test_exact_single_sentence_and_formula_proof(source):
    p,claims,draft,old=source
    fixed,proof=Q.repair_sentence(draft,claims,record(p),root=ROOT)
    assert proof['formula']=='=F10*($C$5+F6)'
    assert proof['fixed_ref']=='Forecast!C5'
    assert fixed==draft.replace(old,proof['new_sentence'],1)
    assert 'locked input on the row labelled "Term loan interest expense"' in fixed
    assert old in draft

@pytest.mark.parametrize('change',['bytes','relative','formula','label','duplicate','nochange'])
def test_repair_rejects_unproved_or_multiple_changes(source,change):
    p,claims,draft,old=source;b=record(p)
    if change=='bytes':p.write_bytes(b'changed')
    elif change=='relative':
        wb=__import__('openpyxl').load_workbook(p);wb['Forecast']['F12']='=F10*(C5+F6)';wb.save(p);b=record(p);claims['claims'][0]['reviewer_only']['evidence']='=F10*(C5+F6)'
    elif change=='formula':claims['claims'][0]['reviewer_only']['evidence']='=-F10*($C$5+F6)'
    elif change=='label':claims['claims'][0]['must_say']=['locked input','the row labelled "Other"']
    elif change=='duplicate':draft+='\n'+old
    else:draft=draft.replace(old,'Use the locked input.')
    with pytest.raises(Exception):Q.repair_sentence(draft,claims,b,root=ROOT)

@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'.cursor/skills/additional-assumptions-dialogue/scripts'))
    return importlib.import_module('aa_lib'),importlib.import_module('validate_dialogue')

def test_multiline_claim_comment_preserves_exact_identity(modules):
    aa,_=modules;identity='method::Forecast::Line one\nLine two::001'
    text='<!-- claim:'+identity+' -->\n**Analyst:** For `Forecast!F12`: How?\n<!-- SLOT: hidden\nmetadata -->\n<!-- claim:'+identity+' -->\n**VP:** Use Line one\nLine two.'
    turns=aa.parse_turns(text)
    assert len(turns)==2 and all(t['record_id']==identity for t in turns)
    assert turns[1]['text']=='Use Line one Line two.'
    assert 'metadata' not in str(turns)

def test_multiline_label_and_explicit_addition_are_covered(modules):
    aa,_=modules
    assert aa.clause_covered('the row labelled '+json.dumps('Gross margin\npercentage'),'Use the row labelled "Gross margin percentage".')
    assert aa.clause_covered('plus','Add the four named rows.')
    assert not aa.clause_covered('plus','Multiply the two named rows.')

def test_numeric_source_label_allowance_is_bounded(modules):
    _,v=modules
    claim={'row_label':'Margin','must_say':['the row labelled '+json.dumps("Distribution gross margin ($000's)")]}
    assert v.licensed_row_label_literals(claim)=={0.0:1}
    claim['must_say']*=2
    assert v.licensed_row_label_literals(claim)=={0.0:1}

@pytest.fixture
def permit(tmp_path,monkeypatch):
    from types import SimpleNamespace
    def put(path,value):
        path=tmp_path/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
    code=tmp_path/'code';code.mkdir();(code/'parser.py').write_text('approved parser bytes')
    source=put('source.json',{'formula':'=F10*($C$5+F6)'})
    blocked=put('blocked.json',{'step':'dialogue-paraphraser'})
    run=tmp_path/'worker';initial=put('worker/initial.json',{'identity':'original'})
    reservation=put('worker/dialogue-pre-review-reservation.json',dict(schema_version=Q.SCHEMA,directory=str(tmp_path/'repair'),blocked=blocked,sentence_replacements=1,model_calls_added=0))
    b=put('evidence.json',{'retained':'evidence'})
    lease=dict(owner='owner',token='token',revision=3,sha256='a'*64)
    p=dict(schema_version=Q.SCHEMA,phase='dialogue-review',round=1,semantic_budget_reset=False,model_calls_added=0,sentence_replacements=1,worker_run=str(run),initial=initial,
        reservation=reservation,dialogue_directory=str(tmp_path/'repair/producer/dialogue'),blocked=blocked,predecessor=blocked,current_root=str(code),current_inventory=Q.inventory(code),
        source=source,selection={},failed_lease=lease,lease=lease,renewal=None,tracker=b,old_descriptor=b,old_snapshot=b,old_draft=b,old_check=b,claims=b,corrected_draft=b,history=[b])
    monkeypatch.setattr(Q.codec,'decode',lambda _:SimpleNamespace(content=SimpleNamespace(bindings={'source_file':source})))
    bound=put('repair/permit.json',p);put('worker/dialogue-pre-review-repair.json',bound)
    return p,bound,put,code

def test_owned_receipt_rechecks_source_code_and_same_lease(permit):
    p,b,put,code=permit;Q.owned(b,p)

@pytest.mark.parametrize('change',['parser','source','reservation','phase','callbudget','lease','predecessor'])
def test_rehashed_permit_cannot_bypass_owning_proofs(permit,change):
    p,b,put,code=permit
    if change=='parser':(code/'parser.py').write_text('mutated allowed parser')
    elif change=='source':Path(p['source']['path']).write_text('changed source')
    elif change=='reservation':p['reservation']=put('foreign-reservation.json',{'forged':True})
    elif change=='phase':p['phase']='dialogue-writer'
    elif change=='callbudget':p['model_calls_added']=1
    elif change=='lease':p['lease']={**p['lease'],'token':'different'}
    else:p['predecessor']=p['old_draft']
    b=put('repair/permit.json',p);put('worker/dialogue-pre-review-repair.json',b)
    with pytest.raises(Exception):Q.owned(b,p)
