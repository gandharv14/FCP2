"""Scalar function and reference semantics from Microsoft Excel documentation."""
from pathlib import Path
import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project
from xl_seg.evaluate import Evaluator,ExcelError,RangeValues,Unresolved


def evaluate(tmp_path, cells):
    book=Workbook();sheet=book.active;sheet.title='Sheet'
    for address,value in cells.items():sheet[address]=value
    book.save(tmp_path/'case.xlsx');book.close()
    ast=AstGraph(tmp_path/'case.xlsx',verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'case')
    for node in graph.nodes.values():
        if node.kind=='formula':node.value='987654321'
    evaluator=Evaluator(graph,project.build(graph))
    result=evaluator.run({node.id for node in graph.cells() if node.kind=='input'})
    assert not result.oracle_reads
    return result,evaluator,graph


@pytest.mark.parametrize('formula,expected',[
    ('=SLN(30000,7500,10)',2250),('=SLN(30,6,2.5)',9.6),
    ('=SLN(30,6,-2)',-12),('=SLN(6,30,2)',-12),
    ('=SQRT(16)',4),('=SQRT(0)',0),('=SQRT(SQRT(1-0.9375))',.5),
    ('=1-SQRT(SQRT(1-0.9375))',.5),
    ('=SQRT(TRUE)',1),('=SQRT("16")',4),('=SLN("30",FALSE,TRUE)',30),
    ('=SQRT(A1)',0),('=SQRT(INDEX(A1:A1,0,1))',0),
])
def test_supported_scalar_formulas_reconstruct_without_cached_answers(tmp_path,formula,expected):
    result,evaluator,_=evaluate(tmp_path,{'C3':formula})
    assert result.values['Sheet!C3']==pytest.approx(expected)
    assert not evaluator.unknown_ops


@pytest.mark.parametrize('formula,code',[
    ('=SQRT(-1)','#NUM!'),('=SLN(30,6,0)','#DIV/0!'),
    ('=SQRT("bad")','#VALUE!'),('=SQRT("TRUE")','#VALUE!'),('=SQRT("")','#VALUE!'),
    ('=SLN("bad",6,2)','#VALUE!'),('=SQRT(1/0)','#DIV/0!'),
    ('=SLN(30,1/0,2)','#DIV/0!'),
])
def test_invalid_numbers_and_errors_remain_errors(tmp_path,formula,code):
    result,_,_=evaluate(tmp_path,{'C3':formula})
    assert isinstance(result.values['Sheet!C3'],ExcelError)
    assert result.values['Sheet!C3'].code==code


@pytest.mark.parametrize('function,args',[
    ('sqrt',[]),('sqrt',[1,2]),('sln',[1,2]),('sln',[1,2,3,4]),
    ('sqrt',[RangeValues([1,4],1,2)]),('sln',[RangeValues([1,4],2,1),0,1]),
    ('sqrt',[[4]]),('sqrt',[RangeValues([4],1,2)]),
    ('sqrt',[Unresolved('missing-primitive')]),
])
def test_unsupported_arity_shapes_and_missing_values_never_become_answers(function,args):
    assert isinstance(getattr(Evaluator(None,None),'_fn_'+function)(args),Unresolved)


@pytest.mark.parametrize('function,args',[
    ('sqrt',[float('inf')]),('sqrt',[float('nan')]),('sln',[1e308,-1e308,1]),
])
def test_nonfinite_results_do_not_escape_numeric_gate(function,args):
    value=getattr(Evaluator(None,None),'_fn_'+function)(args)
    assert isinstance(value,ExcelError) and value.code=='#NUM!'


def test_scalar_input_dependency_is_preserved(tmp_path):
    result,_,_=evaluate(tmp_path,{'A1':30,'A2':6,'A3':2,'C3':'=SLN(A1,A2,A3)','C4':'=SQRT(A2)'})
    assert result.values['Sheet!C3']==12
    assert result.runtime_radj['Sheet!C3']=={'Sheet!A1','Sheet!A2','Sheet!A3'}


@pytest.mark.parametrize('formula,expected',[
    ('=COLUMN()',8),('=COLUMN($H$1)',8),('=COLUMN(A1)',1),
    ('=COLUMN(A1:A3)',1),('=COLUMN(A1:C2)',1),
    ('=COLUMN()-COLUMN($H$1)',0),
])
def test_column_uses_coordinates_even_for_blank_or_bad_values(tmp_path,formula,expected):
    result,_,_=evaluate(tmp_path,{'A1':'=1/0','B1':4,'C1':9,'A2':1,'B2':4,'C2':9,'H8':formula})
    assert result.values['Sheet!H8']==expected


def test_column_horizontal_cse_projects_each_position(tmp_path):
    result,_,_=evaluate(tmp_path,{'A1':1,'B1':4,'C1':9,'H8':ArrayFormula(ref='H8:J8',text='=COLUMN(A1:C1)')})
    assert [result.values['Sheet!'+cell] for cell in ('H8','I8','J8')]==[1,2,3]


def test_column_nonreference_and_unproved_nested_array_context_reject(tmp_path):
    result,_,_=evaluate(tmp_path,{'A1':1,'B1':4,'C1':9,'H8':'=COLUMN(42)','H9':'=SUMPRODUCT(COLUMN(A1:C1))'})
    assert isinstance(result.values['Sheet!H8'],ExcelError) and result.values['Sheet!H8'].code=='#VALUE!'
    assert isinstance(result.values['Sheet!H9'],Unresolved)


def test_actual_0657_index_range_constructor_reconstructs(tmp_path):
    result,_,graph=evaluate(tmp_path,{'H1':1,'H73':1,'I73':2,'J73':3,'K73':4,'L73':5,'M73':6,'C75':2,'L78':'=IFERROR(SUM(INDEX($H$73:$M$73,MAX(1,COLUMN()-COLUMN($H$1)-$C$75)):INDEX($H$73:$M$73,COLUMN()-COLUMN($H$1)-1))/$C$75,0)*-1'})
    value=result.values['Sheet!L78']
    assert value == -2.5
    assert graph.nodes['Sheet!L78'].parse_status=='ok'


def test_spaced_index_constructor_cannot_be_hidden_by_iferror(tmp_path):
    result,_,graph=evaluate(tmp_path,{'H1':1,'H73':1,'I73':2,'J73':3,'K73':4,'L73':5,'M73':6,'C75':2,'L78':'=IFERROR(SUM(INDEX($H$73:$M$73, MAX(1, COLUMN()-COLUMN($H$1)-$C$75)) : INDEX($H$73:$M$73, COLUMN()-COLUMN($H$1)-1)) / $C$75, 0)*-1'})
    assert graph.nodes['Sheet!L78'].parse_status=='ok'
    assert 'name::' not in graph.nodes
    value=result.values['Sheet!L78']
    assert value == -2.5
    # The intended range is I73:J73, yielding -2.5; zero is a hidden parse gap.
    assert value != 0


def test_column_owner_does_not_depend_on_unrelated_previous_evaluation(tmp_path):
    result,evaluator,graph=evaluate(tmp_path,{'A1':1,'H8':'=COLUMN()'})
    evaluator._current_cell='Sheet!A1'
    root=graph.nodes[graph.root_of('Sheet!H8')]
    assert evaluator._column(root)==8


def test_column_without_reference_in_horizontal_cse_uses_each_member(tmp_path):
    result,_,_=evaluate(tmp_path,{'H8':ArrayFormula(ref='H8:J8',text='=COLUMN()')})
    assert [result.values['Sheet!'+cell] for cell in ('H8','I8','J8')]==[8,9,10]
