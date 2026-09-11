from pathlib import Path
import openpyxl
import pytest
from xl_ast_graph import AstGraph,write_csv
from xl_seg.model import load
from xl_seg.project import build
from xl_seg.evaluate import Evaluator,ExcelError,CalculationMetadata


def certificate(tmp_path,formula,endpoint=0):
    workbook=openpyxl.Workbook();s=workbook.active;s.title='S';s['A1']=formula
    source=tmp_path/'cycle.xlsx';workbook.save(source)
    ast=AstGraph(source,verbose=False).build();out=tmp_path/'ast';out.mkdir()
    write_csv(out,ast.nodes,ast.edges);graph=load(out,'cycle');cg=build(graph)
    evaluator=Evaluator(graph,cg,calculation=CalculationMetadata(available=True,iterate=True))
    evaluator.values['S!A1']=float(endpoint)
    edges=list(graph.edges);result=evaluator._affine_cycle_certificate(['S!A1'])
    assert list(graph.edges)==edges
    return result,evaluator


@pytest.mark.parametrize('formula,endpoint,coefficient',[
    ('=IFERROR((A1+1)*#REF!,0)',0,0),
    ('=IFERROR(#REF!,0.2*A1+1)',1.25,.2),
])
def test_native_constant_error_selects_proven_affine_fallback(tmp_path,formula,endpoint,coefficient):
    proof,evaluator=certificate(tmp_path,formula,endpoint)
    assert proof['certified'] and proof['coefficient']==coefficient
    assert proof['endpoint_error_bound']==0
    assert proof['constant_error_branches'][0]['native_error_nodes']
    for leaf in proof['constant_error_branches'][0]['native_error_nodes']:
        value=evaluator._eval_node(leaf)
        assert isinstance(value,ExcelError) and value.code=='#REF!'


@pytest.mark.parametrize('formula',[
    '=IFERROR(1/A1,0)',
    '=IFERROR(UNSUPPORTED(A1)*#REF!,0)',
    '=IFERROR((A1*A1)*#REF!,0)',
    '=IFERROR((A1+1)*"#REF!",0)',
    '=IFERROR(#REF!,A1*A1)',
    '=IFERROR(#REF!,2*A1+1)',
])
def test_endpoint_error_unknown_or_nonlinear_branch_does_not_certify(tmp_path,formula):
    proof,_=certificate(tmp_path,formula)
    assert not proof['certified']
