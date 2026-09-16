"""AVERAGE counts numeric references; blanks are absent, never numeric zero."""
import pytest
from tests.test_cycle_equation_selector import run

def certified(r):return [b for b in r.iterated if b['output_relevant']]

@pytest.mark.parametrize('formula',['=0.5*AVERAGE(A1,B1)+1','=0.5*AVERAGE(A1:B1)+1'])
def test_blank_reference_does_not_change_denominator(tmp_path,formula):
 r=run(tmp_path,{'A1':formula},['A1'],delta=1e-10)
 assert certified(r) and all(b['certified']for b in certified(r))
 assert abs(r.values['Sheet!A1']-2)<1e-8
 assert not r.oracle_reads


def test_zero_is_counted_and_proves_different_fixed_point(tmp_path):
 r=run(tmp_path,{'A1':'=0.5*AVERAGE(A1,B1)+1','B1':0},['A1'],delta=1e-10)
 assert all(b['certified']for b in certified(r))
 assert abs(r.values['Sheet!A1']-4/3)<1e-8

@pytest.mark.parametrize('formula',['=AVERAGE(B1)+0.5*A1','=AVERAGE(A1,1/0)','=AVERAGE(A1,UNSUPPORTED(B1))'])
def test_empty_average_errors_and_unknown_expressions_not_erased(tmp_path,formula):
 r=run(tmp_path,{'A1':formula},['A1'])
 assert not all(b['certified']for b in certified(r))


def test_native_iteration_limit_preserved(tmp_path):
 r=run(tmp_path,{'A1':'=0.5*AVERAGE(A1,B1)+1'},['A1'],count=1)
 assert any(not b['converged']for b in r.iterated)
 assert all(b['iterations']<=1 for b in r.iterated)


def test_uncomputed_external_formula_is_not_an_absent_blank(tmp_path):
 r=run(tmp_path,{'A1':'=0.5*AVERAGE(A1,B1)+1','B1':'=C1+1','C1':1},['A1'])
 evaluator=r.endpoint_equation_checker.__self__
 assert 'Sheet!B1' in evaluator.values
 del evaluator.values['Sheet!B1']
 block=next(b for b in r.iterated if b['output_relevant'])
 proof=evaluator._affine_cycle_certificate(block['members'])
 assert not proof['certified']
 assert any(n['node']=='Sheet!B1' and n['reason']=='non_numeric_external_cell'for n in proof['unsupported_nodes'])
