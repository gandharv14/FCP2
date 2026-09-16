"""Exact source literal interpretation; no source or formula-cache edits."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime
import json
import openpyxl
import pytest
from xl_date_text_policy import binding,load,graph_literals,LITERAL_SCHEMA
from xl_seg import evaluate as E,model,project
from xl_ast_graph import AstGraph,write_csv
from xl_segment import stabilize_runtime_proof

def fixture(tmp_path):
    source=tmp_path/'0213.xlsx';book=openpyxl.Workbook();s=book.active
    s['I595']=datetime(2033,12,31);s['M594']='=YEARFRAC("12/31/2015",$I$595)+0.5';s['I594']=.1
    s['M595']='=1/(1+I594)^M594';s['I596']=6900000;s['M596']='=PRODUCT(I596,M595)';book.save(source);book.close()
    auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
    p=dict(schema_version=LITERAL_SCHEMA,source=binding(source),authorization=binding(auth),date_order='MDY',cells=[],formula_literals=[dict(sheet='Sheet',coordinate='M594',formula='=YEARFRAC("12/31/2015",$I$595)+0.5',text='12/31/2015')])
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(p))
    ast=tmp_path/'ast';ast.mkdir()
    with AstGraph(source,max_range_expand=100,read_values=True,consts=True,verbose=False)as tree:tree.build();write_csv(ast,tree.nodes,tree.edges)
    graph=model.load(ast,'0213');return source,policy,p,graph

def test_actual_source_formula_and_discount_reconstruct_without_cached_answers(tmp_path):
    source,policy,p,graph=fixture(tmp_path);before=binding(source)
    try:
        cg=project.build(graph);meta=replace(E.workbook_calculation_metadata(source),primitive_date_policy=binding(policy))
        r,inputs,_,_=stabilize_runtime_proof(graph,cg,set(),{'Sheet!M596'},calculation=meta)
        assert r.values['Sheet!M594']==18.5
        assert r.values['Sheet!M596']==pytest.approx(6900000/1.1**18.5)
        assert not r.oracle_reads and not r.oracle_accesses and r.strict_proof
        assert not (inputs & {'Sheet!M594','Sheet!M595','Sheet!M596'})
        assert r.coverage['formula_date_literals'] and binding(source)==before
    finally:graph.close()

@pytest.mark.parametrize('fault',['formula','text','order','duplicate','absent','source','auth','extra','empty','ast_formula','ast_literal'])
def test_declared_literal_source_and_ast_are_exact(tmp_path,fault):
    source,policy,p,graph=fixture(tmp_path)
    try:
        r=p['formula_literals'][0]
        if fault=='formula':r['formula']+=' + 1'
        elif fault=='text':r['text']='12/30/2015'
        elif fault=='order':p['date_order']='DMY'
        elif fault=='duplicate':p['formula_literals']*=2
        elif fault=='absent':r['coordinate']='M590'
        elif fault=='source':p['source']['sha256']='0'*64
        elif fault=='auth':p['authorization']['sha256']='0'*64
        elif fault=='extra':r['ignored']=True
        elif fault=='empty':p['formula_literals']=[]
        elif fault=='ast_formula':graph.nodes['Sheet!M594'].formula+=' + 1'
        elif fault=='ast_literal':
            n=next(n for n in graph.nodes.values()if n.kind=='const' and n.op=='text');n.value='12/30/2015'
        policy.write_text(json.dumps(p))
        with pytest.raises(ValueError):graph_literals(load(binding(policy),source),graph)
    finally:graph.close()

@pytest.mark.parametrize('text',['12/31/2015','2/3/2015','not-a-date',''])
def test_undeclared_date_text_does_not_silently_become_serial_zero(text):
    actual=E.Evaluator(None,None)._fn_yearfrac([text,E._datetime_to_serial(datetime(2033,12,31))])
    assert isinstance(actual,E.ExcelError) and actual.code=='#VALUE!'
