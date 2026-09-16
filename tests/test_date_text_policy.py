from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from dataclasses import replace
import json
import openpyxl
import pytest
from xl_date_text_policy import DateText,load,binding,SCHEMA
from xl_seg import evaluate as E

@pytest.mark.parametrize('text,days',[('2/3/2012',1062),('5/30/2012',945),('7/23/2012',891),('10/19/2012',803),('12/11/2012',750),('2/5/2013',694)])
def test_source_date_subtractions_and_text_identity(text,days):
    value=DateText(text,'MDY');end=E._datetime_to_serial(datetime(2014,12,31))
    assert E._binary('-',end,value)==days
    assert isinstance(value,str) and E._text(value)==text and json.loads(json.dumps(value))==text
    assert E.numbers([value])==[] # Text in SUM/MIN ranges remains text.
    assert isinstance(E._binary('-',end,text),E.ExcelError) # Default locale is not guessed.


def test_ambiguous_date_requires_explicit_order():
    assert float(DateText('2/5/2013','MDY'))!=float(DateText('2/5/2013','DMY'))
    with pytest.raises(ValueError):DateText('2/5/2013','AUTO')

@pytest.mark.parametrize('text',['2/29/1900','2/29/2023','2/5/13','13/2/2013','2/5/2013 12:00','2013-02-05',' 2/5/2013','0/5/2013'])
def test_invalid_or_undeclared_shapes_not_guessed(text):
    with pytest.raises(ValueError):DateText(text,'MDY')


def selected(tmp_path):
    p=tmp_path/'0434.xlsx';b=openpyxl.Workbook();b.active['B1']='2/5/2013';b.active['B1'].number_format='mm/dd/yy;@';b.active['E1']=datetime(2014,12,31);b.active['F1']='=E1-B1';b.save(p)
    auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    row=dict(schema_version=SCHEMA,source=binding(p),authorization=binding(auth),date_order='MDY',cells=[dict(sheet='Sheet',coordinate='B1',text='2/5/2013',number_format='mm/dd/yy;@')]);q=tmp_path/'policy.json';q.write_text(json.dumps(row));return p,q,row

@pytest.mark.parametrize('fault',['source','auth','duplicate','text','style','formula','epoch'])
def test_closed_source_bound_primitive_interpretation(tmp_path,fault):
    source,p,row=selected(tmp_path)
    if fault=='source':row['source']['sha256']='0'*64
    elif fault=='auth':
        a=tmp_path/'auth';a.write_text('not selected');row['authorization']=binding(a)
    elif fault=='duplicate':row['cells']*=2
    elif fault=='text':row['cells'][0]['text']='2/6/2013'
    elif fault=='style':row['cells'][0]['number_format']='dd/mm/yyyy'
    else:
        b=openpyxl.load_workbook(source)
        if fault=='formula':b.active['B1']='="2/5/2013"'
        else:b.epoch=datetime(1904,1,1)
        b.save(source);row['source']=binding(source)
    p.write_text(json.dumps(row))
    with pytest.raises(ValueError):load(binding(p),source)


def test_actual_strict_graph_keeps_primitive_text_but_computes_original_formula(tmp_path):
    from xl_ast_graph import AstGraph,write_csv
    from xl_seg import model,project
    from xl_segment import stabilize_runtime_proof
    source,p,row=selected(tmp_path);ast=tmp_path/'ast';ast.mkdir()
    with AstGraph(source,max_range_expand=100,read_values=True,consts=True,verbose=False)as tree:
        tree.build();write_csv(ast,tree.nodes,tree.edges)
    graph=model.load(ast,'0434')
    try:
        cg=project.build(graph);metadata=replace(E.workbook_calculation_metadata(source),primitive_date_policy=binding(p))
        result,inputs,_,_=stabilize_runtime_proof(graph,cg,set(),{'Sheet!F1'},calculation=metadata)
        assert result.values['Sheet!F1']==694 and result.values['Sheet!B1']=='2/5/2013'
        assert isinstance(result.values['Sheet!B1'],DateText) and not result.oracle_reads
        assert result.coverage['primitive_date_policy']==binding(p)
        node=cg.info['Sheet!B1'].node;node.value='2/6/2013'
        with pytest.raises(ValueError,match='source AST'):stabilize_runtime_proof(graph,cg,set(),{'Sheet!F1'},calculation=metadata)
    finally:graph.close()
