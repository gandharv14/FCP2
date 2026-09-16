"""LOG reconstructs original source arithmetic without reading cached answers.

Syntax/default base: https://learn.microsoft.com/en-us/office/vba/api/excel.worksheetfunction.log
"""
import math
import pytest
from xl_seg.evaluate import Evaluator,ExcelError,Unresolved,RangeValues
from test_elementary_financial_functions import evaluate


@pytest.mark.parametrize('formula,expected',[
 ('=LOG(10)',1),('=LOG(8,2)',3),('=LOG(0.01)',-2),('=LOG(8,0.5)',-3),
 ('=LOG(1,2)',0),('=LOG("100","10")',2),('=LOG(TRUE,10)',0),
 ('=IF(A1=0,0,LOG(A1,(1.1)))',math.log(9.068847442363075,1.1)),
])
def test_exact_source_formulas_reconstruct_without_oracle(tmp_path,formula,expected):
 result,evaluator,_=evaluate(tmp_path,{'A1':9.068847442363075,'C3':formula})
 assert result.values['Sheet!C3']==pytest.approx(expected)
 assert not evaluator.unknown_ops and not result.oracle_reads


@pytest.mark.parametrize('formula,code',[
 ('=LOG(0)','#NUM!'),('=LOG(-1)','#NUM!'),('=LOG(2,0)','#NUM!'),
 ('=LOG(2,-2)','#NUM!'),('=LOG(2,1)','#DIV/0!'),('=LOG("bad")','#VALUE!'),
 ('=LOG(1/0)','#DIV/0!'),('=LOG(2,1/0)','#DIV/0!'),
])
def test_invalid_domain_and_source_errors_are_not_numbers(tmp_path,formula,code):
 result,_,_=evaluate(tmp_path,{'C3':formula});value=result.values['Sheet!C3']
 assert isinstance(value,ExcelError)and value.code==code


@pytest.mark.parametrize('args',[[],[1,2,3],[RangeValues([1,2],1,2)],[[1]],[Unresolved('source-gap')]])
def test_unproved_arity_shape_and_dependencies_remain_unresolved(args):
 assert isinstance(Evaluator(None,None)._fn_log(args),Unresolved)


@pytest.mark.parametrize('args',[[float('inf'),2],[2,float('nan')],[float('nan')],[2,float('inf')]])
def test_nonfinite_cannot_escape_as_numeric_output(args):
 result=Evaluator(None,None)._fn_log(args);assert isinstance(result,ExcelError)and result.code=='#NUM!'


def test_both_dynamic_operands_remain_dependencies(tmp_path):
 result,_,_=evaluate(tmp_path,{'A1':8,'A2':2,'C3':'=LOG(A1,A2)'})
 assert result.values['Sheet!C3']==3
 assert result.runtime_radj['Sheet!C3']=={'Sheet!A1','Sheet!A2'}


def test_inactive_if_branch_does_not_execute_invalid_log(tmp_path):
 result,_,_=evaluate(tmp_path,{'A1':0,'C3':'=IF(A1=0,0,LOG(A1,1.1))'})
 assert result.values['Sheet!C3']==0
