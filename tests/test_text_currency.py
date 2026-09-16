import pytest
from xl_seg.evaluate import Evaluator,Unresolved

@pytest.mark.parametrize('amount,pattern,expected',[(8500000,'$0,0','$8,500,000'),(0,'$0,0','$0'),(12.5,'$0.00','$12.50'),(1234.5,'$#,##0.00','$1,234.50'),(8500000,'0,0','8,500,000'),(.125,'0.0%','12.5%')])
def test_literal_currency_prefix_and_existing_number_formats(amount,pattern,expected):
    assert Evaluator._fn_text(None,[amount,pattern])==expected

def test_exact_source_concat_keeps_dollar_literal():
    # 0279 Summary!B55: ="Equity Returns "&TEXT(I16,"$0,0")&" equity invested"
    assert 'Equity Returns '+Evaluator._fn_text(None,[8500000,'$0,0'])+' equity invested'=='Equity Returns $8,500,000 equity invested'

def test_negative_currency_is_not_guessed_from_positive_format():
    assert isinstance(Evaluator._fn_text(None,[-5,'$0,0']),Unresolved)

@pytest.mark.parametrize('amount,pattern',[(2.5,'$0'),(2.345,'$0.00'),(1.001,'$0.00')])
def test_currency_needing_rounding_is_not_bankers_rounded(amount,pattern):
    value=Evaluator._fn_text(None,[amount,pattern])
    assert isinstance(value,Unresolved) and value.reason=='unsupported-currency-text-rounding'
