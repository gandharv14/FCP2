"""One-cell INDEX/reference values must retain their scalar numeric value."""
from datetime import datetime
import pytest
from openpyxl import Workbook
from openpyxl.utils.datetime import to_excel
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project
from xl_seg.evaluate import CalculationMetadata,Evaluator,ExcelError,RangeValues,Unresolved,_binary,num
from xl_segment import stabilize_runtime_proof


def evaluate(tmp_path, values, formula):
    book=Workbook();sheet=book.active;sheet.title='Sheet'
    for address,value in values.items():sheet[address]=value
    sheet['C1']=formula;book.save(tmp_path/'case.xlsx');book.close()
    ast=AstGraph(tmp_path/'case.xlsx',verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'case')
    for node in graph.nodes.values():
        if node.kind=='formula':node.value='987654321'
    inputs={node.id for node in graph.nodes.values() if node.kind=='input'}
    result,supplied,_,proof=stabilize_runtime_proof(graph,project.build(graph),inputs,{'Sheet!C1'},calculation=CalculationMetadata(available=True,iterate=False))
    assert not result.oracle_reads and 'Sheet!C1' not in supplied
    return result,proof


@pytest.mark.parametrize('function,expected',[('EOMONTH',datetime(2011,2,28)),('EDATE',datetime(2011,2,1))])
def test_nested_index_dates_and_months_with_exact_dependencies(tmp_path,function,expected):
    result,proof=evaluate(tmp_path,{'A1':'=DATE(2011,1,1)','B1':1.9},f'={function}(INDEX(A1:A1,0,1),INDEX(B1:B1,1,0))')
    assert result.values['Sheet!C1']==to_excel(expected)
    assert result.runtime_radj['Sheet!C1']=={'Sheet!A1','Sheet!B1'}
    assert proof['closure']['stabilized']


@pytest.mark.parametrize('function,expected',[('YEAR',2011),('MONTH',1),('DAY',15)])
def test_date_parts_receive_singleton_value(tmp_path,function,expected):
    result,_=evaluate(tmp_path,{'A1':'=DATE(2011,1,15)'},f'={function}(INDEX(A1:A1,0,1))')
    assert result.values['Sheet!C1']==expected
    assert 'Sheet!A1' in result.runtime_radj['Sheet!C1']


@pytest.mark.parametrize('formula,expected',[('ABS(INDEX(A1:A1,0,1))',2.4),('ROUND(INDEX(A1:A1,0,1),0)',-2),('INT(INDEX(A1:A1,0,1))',-3),('INDEX(A1:A1,0,1)+1',-1.4)])
def test_other_numeric_consumers(tmp_path,formula,expected):
    result,_=evaluate(tmp_path,{'A1':-2.4},'='+formula)
    assert result.values['Sheet!C1']==pytest.approx(expected)
    assert result.runtime_radj['Sheet!C1']=={'Sheet!A1'}


@pytest.mark.parametrize('value',[43000.25,-3,True,False,None,'12.5','2011-01-15T00:00:00','not a number'])
def test_singleton_coercion_uses_existing_scalar_rules(value):
    wrapped=RangeValues([value],rows=1,cols=1,refs=['Sheet!A1'])
    assert num(wrapped,12345)==num(value,12345)
    assert wrapped==[value] and wrapped.refs==('Sheet!A1',)


@pytest.mark.parametrize('value',[[1],[[1]],RangeValues([1,2],rows=1,cols=2),RangeValues([1,2],rows=2,cols=1),RangeValues([1],rows=1,cols=2),RangeValues([1],rows=2,cols=1),RangeValues([],rows=1,cols=1),RangeValues([[1]],rows=1,cols=1)])
def test_general_list_multicell_malformed_and_nested_values_not_guessed(value):
    assert num(value,12345)==12345


def test_array_arithmetic_shape_and_reference_identity_unchanged():
    values=RangeValues([1,2],rows=2,cols=1,refs=['Sheet!A1','Sheet!A2'])
    result=_binary('+',values,3)
    assert isinstance(result,RangeValues) and result==[4,5] and (result.rows,result.cols)==(2,1)
    selected=Evaluator._fn_index(None,[values,0,1])
    assert selected==[1,2] and selected.refs==values.refs and (selected.rows,selected.cols)==(2,1)


@pytest.mark.parametrize('function',['EOMONTH','EDATE','YEAR','ABS'])
def test_error_inside_selected_singleton_propagates_before_num(tmp_path,function):
    suffix=',1' if function in ('EOMONTH','EDATE') else ''
    result,_=evaluate(tmp_path,{'A1':'=1/0'},f'={function}(INDEX(A1:A1,0,1){suffix})')
    assert isinstance(result.values['Sheet!C1'],ExcelError)
    assert result.values['Sheet!C1'].code=='#DIV/0!'
    assert 'Sheet!A1' in result.runtime_radj['Sheet!C1']


@pytest.mark.parametrize('function',['EOMONTH','EDATE'])
def test_unresolved_inside_selected_singleton_stays_unresolved(tmp_path,function):
    result,_=evaluate(tmp_path,{'A1':'=UNSUPPORTED_FN(1)'},f'={function}(INDEX(A1:A1,0,1),1)')
    assert isinstance(result.values['Sheet!C1'],Unresolved)


@pytest.mark.parametrize('function',['EOMONTH','EDATE'])
def test_invalid_numeric_date_range_still_fails(tmp_path,function):
    # Existing scalar date validation raises on out-of-range numeric dates.
    # The singleton must reach that same rejection, never become serial zero.
    handler=getattr(Evaluator, '_fn_'+function.lower())
    with pytest.raises(OverflowError):handler(None,[4000000,1])
    with pytest.raises(OverflowError):
        evaluate(tmp_path,{'A1':4000000},f'={function}(INDEX(A1:A1,0,1),1)')


@pytest.mark.parametrize('day,months',[(datetime(2011,1,31),1),(datetime(2020,2,29),12),(datetime(2011,1,15),-1.9)])
def test_date_offset_boundary_matches_scalar_path(day,months):
    for handler in (Evaluator._fn_eomonth,Evaluator._fn_edate):
        scalar=[to_excel(day),months]
        wrapped=[RangeValues([v],1,1) for v in scalar]
        assert handler(None,wrapped)==handler(None,scalar)
