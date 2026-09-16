"""Exact scenario arithmetic preserves complete scope and original target exclusion."""
import copy
import importlib.util
import json
from pathlib import Path
import openpyxl
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scenario_disclose',ROOT/'.cursor/skills/task-disclosure/scripts/disclose.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
CELLS=['S!N23','S!N24']

def fixture(tmp_path,mutation=None):
    w=openpyxl.Workbook();s=w.active;s.title='S';s['B22']='SG&A Expenses (less interest expense)'
    for r,name in [(23,'Mgmt Case'),(24,'Base Case'),(25,'Downside Case')]:
        s[f'B{r}']=name;s[f'M{r}']=.1;s[f'N{r}']=f'=M{r}-0.005' if r<25 else f'=M{r}'
    s['Z50']='=N23+N24'
    if mutation=='duplicate':s['B24']='Mgmt Case'
    if mutation=='no_heading':s['B22']=None
    if mutation=='busy_heading':s['C22']=1
    if mutation=='other_label':s['C24']='Separate cost'
    if mutation=='fixed':s['N24']='=$M$24-0.005'
    if mutation=='different':s['N24']='=M24-0.006'
    if mutation=='branch':s['N24']='=IF(M24>0,M24-0.005,0)'
    if mutation=='zero':
        for r in (23,24):s[f'N{r}']=f'=M{r}+0'
    path=tmp_path/'gold.xlsx';w.save(path)
    task=tmp_path/'task';(task/'environment').mkdir(parents=True);(task/'tests').mkdir()
    for c in list(s._cells.values()):
        if c.data_type=='f':c.value=None
    if mutation=='changed_heading':s['B22']='Other source'
    if mutation=='changed_marker':s['B24']='Downside Case'
    if mutation=='populated':s['N24']=.09
    w.save(task/'environment/delivered.xlsx')
    (task/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!Z50':.19}}))
    return D.Book(path),D.Book(task/'environment/delivered.xlsx'),path,task

def test_exact_source_block_full_family_and_independent_check(tmp_path):
    gold,delivered,path,task=fixture(tmp_path)
    assert gold.row_label('S!N24')=='' # No global label or role arbitration change.
    records=D.detect_scenario_literal_increment(gold,delivered,set(CELLS));assert len(records)==1
    rec=records[0];assert D.ship_scenario_literal_increment(rec,dict(gold=gold,delivered=delivered,targets={'S!Z50'}))
    rec['disposition']='disclosed';sentence=D.render_sentence(rec)
    assert 'Base Case' in sentence and '0.005' in sentence and 'N23:N24' in sentence
    (task/'tests/disclosure.json').write_text(json.dumps({'records':[rec]}))
    result=D.faithcheck_task(task,golden=str(path));assert result['passed'],result

@pytest.mark.parametrize('mutation',['duplicate','no_heading','busy_heading','other_label','fixed','different','branch','zero','changed_heading','changed_marker','populated'])
def test_unsupported_source_or_changed_visible_context_declines(tmp_path,mutation):
    gold,delivered,_,_=fixture(tmp_path,mutation)
    assert D.scenario_increment_record(gold,delivered,CELLS)is None

def test_partial_selection_declines(tmp_path):
    gold,delivered,_,_=fixture(tmp_path)
    assert D.detect_scenario_literal_increment(gold,delivered,{'S!N23'})==[]

@pytest.mark.parametrize('target',['S!N23','S!N24','S!M23','S!M24','S!B22','S!B24'])
def test_original_targets_and_source_operands_still_block(tmp_path,target):
    gold,delivered,_,_=fixture(tmp_path);rec=D.scenario_increment_record(gold,delivered,CELLS)
    assert not D.ship_scenario_literal_increment(rec,dict(gold=gold,delivered=delivered,targets={target}))

@pytest.mark.parametrize('field',['steps','heading','heading_cell','scenario_rows','band'])
def test_independent_faithcheck_rejects_changed_claim(tmp_path,field):
    gold,delivered,path,task=fixture(tmp_path);rec=D.scenario_increment_record(gold,delivered,CELLS)
    rec['fields'][field]='foreign';rec['disposition']='disclosed'
    (task/'tests/disclosure.json').write_text(json.dumps({'records':[rec]}))
    assert not D.faithcheck_task(task,golden=str(path))['passed']

@pytest.mark.parametrize('cells',[['S!N23','S!N24','S!N25'],['S!N24','S!Q24']])
def test_partial_overlap_retains_original_custom_record_and_claims(tmp_path,cells):
    gold,delivered,_,_=fixture(tmp_path)
    original={'cell_keys':cells,'entry':'method_interest','retained_arbitration':'unchanged'}
    claims=set(cells);saved=copy.deepcopy(original)
    scenarios,covered,methods,remaining=D.prefer_scenario_arithmetic(
        gold,delivered,set(CELLS),set(),[original],claims)
    assert scenarios==[] and covered==set() and methods==[saved] and remaining==claims
    assert original==saved and claims==set(cells)


def test_complete_coverage_replaces_only_covered_records(tmp_path):
    gold,delivered,_,_=fixture(tmp_path)
    covered={'cell_keys':['S!N23'],'entry':'method_interest'}
    unrelated={'cell_keys':['S!Q30'],'entry':'method_tax'}
    scenarios,keys,methods,remaining=D.prefer_scenario_arithmetic(
        gold,delivered,set(CELLS),set(),[covered,unrelated],{'S!N23','S!Q30'})
    assert len(scenarios)==1 and keys==set(CELLS)
    assert methods==[unrelated] and remaining=={'S!Q30'}
