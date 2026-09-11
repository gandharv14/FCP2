import pytest
from xl_seg.evaluate import Evaluator,ExcelError,_edate,_eomonth


@pytest.mark.parametrize('operation',[_edate,_eomonth])
@pytest.mark.parametrize('arguments',[['-',0],['',0],[45292,'-'],[45292,'']])
def test_invalid_date_or_month_text_propagates_value_error(operation,arguments):
    result=Evaluator._date_month_shift(arguments,operation)
    assert isinstance(result,ExcelError) and result.code=='#VALUE!'


@pytest.mark.parametrize('operation',[_edate,_eomonth])
@pytest.mark.parametrize('arguments',[[45292,1],['45292','1'],['2024-01-01T00:00:00',1],[45292,-1],[45292,1.9],[None,0],[True,0]])
def test_supported_month_shift_inputs_preserved(operation,arguments):
    assert Evaluator._date_month_shift(arguments,operation)==float(operation(*arguments))


def test_0061_eomonth_hyphen_chain_stays_blank(tmp_path):
    import openpyxl
    from test_excel_compatibility_semantics import _evaluate
    workbook=openpyxl.Workbook();s=workbook.active;s.title='S';s['A1']='-'
    s['B1']='=IFERROR(YEARFRAC(EOMONTH(A1,0),DATE(2014,12,31)),"")'
    s['C1']='=IFERROR(1-B1,"")'
    s['D1']='=IFERROR(EDATE(A1,12),"")'
    path=tmp_path/'months.xlsx';workbook.save(path);_,_,result=_evaluate(path)
    assert [result.values['S!'+cell] for cell in ['B1','C1','D1']]==['','','']
