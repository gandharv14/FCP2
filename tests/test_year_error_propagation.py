import pytest
from xl_seg.evaluate import Evaluator,ExcelError,Unresolved,RangeValues


@pytest.mark.parametrize('value',['-','not a date','','2024-99-99',RangeValues(['-'],rows=1,cols=1)])
def test_invalid_date_text_is_value_error(value):
    result=Evaluator._fn_year(None,[value])
    assert isinstance(result,ExcelError) and result.code=='#VALUE!'


@pytest.mark.parametrize('value,expected',[(1,1900),(60,1900),(61,1900),(45292,2024),('45292',2024),('2024-01-01T00:00:00',2024),(45292.8,2024),(True,1900)])
def test_supported_numeric_and_iso_dates_keep_their_year(value,expected):
    assert Evaluator._fn_year(None,[value])==expected


def test_year_error_reaches_iferror_instead_of_a_false_historical_date(tmp_path):
    import openpyxl
    from test_excel_compatibility_semantics import _evaluate
    workbook=openpyxl.Workbook();sheet=workbook.active;sheet.title='S'
    sheet['A1']='-'
    sheet['B1']='=YEAR(A1)'
    sheet['C1']='=IFERROR(YEARFRAC(DATE(YEAR(A1)-1,12,31),DATE(2013,12,31)),"")'
    path=tmp_path/'dates.xlsx';workbook.save(path)
    _,_,result=_evaluate(path)
    assert isinstance(result.values['S!B1'],ExcelError)
    assert result.values['S!C1']==''


def test_existing_excel_error_is_preserved():
    error=ExcelError('#REF!')
    assert Evaluator._fn_year(None,[error]) is error


def test_blank_single_cell_range_preserves_existing_blank_scalar_semantics():
    assert Evaluator._fn_year(None,[RangeValues([None],rows=1,cols=1)])==Evaluator._fn_year(None,[None])
