import pytest
from xl_seg.evaluate import _binary,ExcelError,RangeValues


@pytest.mark.parametrize('operator',['+','-','*','/','^'])
@pytest.mark.parametrize('text',['','-','not a number'])
def test_arithmetic_nonnumeric_text_is_value_error(operator,text):
    for left,right in [(2,text),(text,2)]:
        result=_binary(operator,left,right)
        assert isinstance(result,ExcelError) and result.code=='#VALUE!'


@pytest.mark.parametrize('operator,expected',[('+',3),('-',1),('*',2),('/',2),('^',2)])
def test_numeric_text_remains_coercible(operator,expected):
    assert _binary(operator,'2','1')==expected


def test_blank_boolean_error_and_array_semantics():
    assert _binary('+',None,2)==2
    assert _binary('-',2,None)==2
    assert _binary('*',True,2)==2
    error=ExcelError('#REF!');assert _binary('*',error,'') is error
    result=_binary('+',RangeValues([None,'','2'],1,3),1)
    assert result[0]==1 and isinstance(result[1],ExcelError) and result[2]==3
    assert _binary('&','','x')=='x' and _binary('=','','') is True


def test_0061_invalid_date_chain_keeps_empty_text_out_of_rate_schedule(tmp_path):
    import openpyxl
    from test_excel_compatibility_semantics import _evaluate
    workbook=openpyxl.Workbook();s=workbook.active;s.title='S'
    s['A1']='-';s['B1']='=IFERROR(YEARFRAC(DATE(YEAR(A1)-1,12,31),DATE(2013,12,31)),"")'
    s['C1']='=IFERROR(1-B1,"")'
    s['D1']='=IFERROR(12.5*B1+734.3475914838455*C1,"")'
    s['E1']='=IFERROR(734.3475914838455-SUM(D1:D1),"")'
    path=tmp_path/'ratecase.xlsx';workbook.save(path);_,_,result=_evaluate(path)
    assert [result.values['S!'+cell] for cell in ['B1','C1','D1']]==['','','']
    assert result.values['S!E1']==734.3475914838455
