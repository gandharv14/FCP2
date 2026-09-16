"""Source-derived column conventions preserve graded target and exact heading gates."""
import copy
import importlib.util
import json
from pathlib import Path
import openpyxl
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('column_disclose',ROOT/'.cursor/skills/task-disclosure/scripts/disclose.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
CELLS=[f'S!B{r}' for r in range(13,23)]

def fixture(tmp_path, mutation=None):
    wb=openpyxl.Workbook();s=wb.active;s.title='S';s['B9']='Capital Raised';s['B12']=500000
    s['C9']='Ownership Stake';s['C12']=.175;s['D9']='Pre-Money Valuation'
    for r in range(13,23):s[f'B{r}']=f'=B{r-1}+100000';s[f'C{r}']=f'=C{r-1}'
    s['D22']='=B22/C22'
    if mutation=='row_label':s['A13']='Explicit row name'
    if mutation=='branch':s['B16']='=IF(B15>0,B15+100000,0)'
    if mutation=='growth':
        for r in range(13,23):s[f'B{r}']=f'=B{r-1}*1.1'
    if mutation=='fixed_row':
        for r in range(13,23):s[f'B{r}']='=B$12+100000'
    if mutation=='nonfinite':
        for r in range(13,23):s[f'B{r}']=f'=B{r-1}+'+'9'*400
    if mutation=='zero':
        for r in range(13,23):s[f'B{r}']=f'=B{r-1}+0'
    gold=tmp_path/'gold.xlsx';wb.save(gold)
    task=tmp_path/'task';(task/'environment').mkdir(parents=True);(task/'tests').mkdir()
    for cell in list(s._cells.values()):
        if cell.data_type=='f':cell.value=None
    if mutation=='heading':s['B9']='Altered name'
    if mutation=='populated':s['B15']=800000
    wb.save(task/'environment/delivered.xlsx')
    (task/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!D22':8000000}}))
    return D.Book(gold),D.Book(task/'environment/delivered.xlsx'),gold,task

def detect(gold,delivered,scope=CELLS,targets=()):
    records=D.detect_column_literal_increment(gold,delivered,set(scope))
    ctx=dict(gold=gold,delivered=delivered,targets=set(targets),raw_targets=set(targets))
    return D.suppress_closure_reach(D.apply_ship_when(records,ctx),set(targets))

def test_actual_column_geometry_renders_one_full_nongraded_rule(tmp_path):
    gold,delivered,path,task=fixture(tmp_path)
    records=detect(gold,delivered,CELLS+[f'S!C{r}' for r in range(13,23)]+['S!D22'],['S!D22'])
    assert len(records)==1 and records[0]['disposition']=='disclosed'
    rec=records[0];text=D.render_sentence(rec)
    assert 'column heading "Capital Raised"' in text and 'S!B9' in text
    assert 'copied down the rows' in text and '100000' in text
    assert rec['cell_keys']==CELLS and 'label' not in rec['fields']
    assert 'Pre-Money' not in text and D.Book(path).row_label('S!B13')==''
    (task/'tests/disclosure.json').write_text(json.dumps({'records':records}))
    result=D.faithcheck_task(task,golden=str(path));assert result['passed'],result

@pytest.mark.parametrize('mutation',['row_label','growth','fixed_row','zero','nonfinite','heading','populated'])
def test_unsupported_or_changed_column_declines(tmp_path,mutation):
    gold,delivered,_,_=fixture(tmp_path,mutation)
    assert not [r for r in detect(gold,delivered) if r['disposition']=='disclosed']

def test_partial_selection_cannot_shorten_source_family(tmp_path):
    gold,delivered,_,_=fixture(tmp_path)
    assert detect(gold,delivered,CELLS[:-1])==[]

@pytest.mark.parametrize('target',['S!B17','S!B12'])
def test_original_graded_cells_and_operands_are_never_disclosed(tmp_path,target):
    gold,delivered,_,_=fixture(tmp_path)
    records=detect(gold,delivered,targets=[target]);assert len(records)==1
    assert records[0]['disposition']=='suppressed'

@pytest.mark.parametrize('mutation',['heading','heading_cell','steps','scope','evidence'])
def test_independent_faithcheck_rejects_forged_record(tmp_path,mutation):
    gold,delivered,path,task=fixture(tmp_path);rec=copy.deepcopy(detect(gold,delivered)[0])
    if mutation in ('heading','heading_cell','steps'):rec['fields'][mutation]='wrong'
    elif mutation=='scope':rec['cell_keys']=rec['cell_keys'][:-1]
    else:rec['evidence']='=B12+200000'
    (task/'tests/disclosure.json').write_text(json.dumps({'records':[rec]}))
    result=D.faithcheck_task(task,golden=str(path));assert not result['passed']
