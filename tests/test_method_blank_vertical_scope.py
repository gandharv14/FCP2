"""A blank tail is not a second financial label; copied scope remains exact."""
import copy
import json
from pathlib import Path
import openpyxl
import pytest
from scripts.batch import harbor_method_items as M
from test_disclosure_column_increment import D


def fixture(tmp_path,n=4):
    wb=openpyxl.Workbook();s=wb.active;s.title='S';s['E20']='Residual Value Multiple ($/kW)'
    for row in range(20,20+n):s['F'+str(row)]='=F'+str(row+1)+'+50'
    s['F'+str(20+n)]=500
    goldpath=tmp_path/'gold.xlsx';wb.save(goldpath);gold=D.Book(goldpath)
    task=tmp_path/'task';(task/'environment').mkdir(parents=True);(task/'tests').mkdir()
    for row in range(20,20+n):s['F'+str(row)]=None
    wb.save(task/'environment/delivered.xlsx');delivered=D.Book(task/'environment/delivered.xlsx')
    (task/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!Z99':7}}))
    cells=['S!F'+str(row)for row in range(20,20+n)]
    return gold,delivered,D.make_vertical_band(gold,'S',6,cells),task,goldpath


def test_full_blank_tail_survives_indexed_preparation_and_actual_faithcheck(tmp_path):
    gold,delivered,band,task,path=fixture(tmp_path)
    items=M.prepare(D,gold,[band]);assert len(items)==1
    records,assessments,claimed=D.detect_custom_methods(gold,delivered,[band],{'S!Z99'})
    records=D.apply_ship_when(records,dict(gold=gold,delivered=delivered,targets={'S!Z99'},raw_targets={'S!Z99'}))
    assert len(records)==1 and claimed==set(band['cell_keys'])
    assert 'copied down the rows' in D.render_sentence(records[0])
    (task/'tests/disclosure.json').write_text(json.dumps({'records':records}))
    proof=D.faithcheck_task(task,task.parent/'runs',golden=str(path))
    assert proof['passed'] and proof['checked_records']==1,proof


@pytest.mark.parametrize('kind',['conflicting_label','lower_heading','horizontal','duplicate','gap','formula','partial','oversize'])
def test_other_shapes_retain_original_split(kind,tmp_path):
    gold,_,band,_,path=fixture(tmp_path,33 if kind=='oversize'else 4)
    cells=band['cell_keys'];labels=[gold.row_label(c)for c in cells]
    if kind=='conflicting_label':labels[1]='Operating expense'
    elif kind=='lower_heading':labels[0]='';labels[1]='Residual Value Multiple ($/kW)'
    elif kind=='horizontal':band['orientation']='horizontal'
    elif kind=='duplicate':cells[1]=cells[0]
    elif kind=='gap':cells[1]='S!F50'
    elif kind=='formula':gold.formula[cells[1]]='=F22-50'
    elif kind=='partial':cells.pop();labels.pop()
    assert not M.top_labelled_vertical_family(D,gold,band,cells,labels)


def test_graded_family_cannot_pass_faithcheck(tmp_path):
    gold,delivered,band,task,path=fixture(tmp_path)
    records,_,_=D.detect_custom_methods(gold,delivered,[band],set())
    records=D.apply_ship_when(records,dict(gold=gold,delivered=delivered,targets=set(),raw_targets=set()))
    (task/'tests/disclosure.json').write_text(json.dumps({'records':records}))
    (task/'tests/answer_key.json').write_text(json.dumps({'targets':{'S!F22':600}}))
    assert not D.faithcheck_task(task,task.parent/'runs',golden=str(path))['passed']
