"""Finite equation substitution for a nonzero cyclic denominator."""
import pytest
from tests.test_cycle_equation_selector import run

def system(weight=0):
 return {'Z1':weight,'Z2':1326.5010200000002,'Z3':1610.51,
         'A1':'=Z1*E1','B1':'=Z3+A1','C1':'=B1',
         'D1':'=Z2/SUM(Z2,C1)','E1':'=1-D1'}

def proofs(result):
 return [p for b in result.iterated for p in b.get('certification',{}).get('equation_denominator_proofs',[])]

def test_exact_source_zero_breaks_denominator_equations(tmp_path):
 result=run(tmp_path,system(),['E1'])
 assert all(b['certified']for b in result.iterated if b['output_relevant'])
 proof=proofs(result);assert proof and proof[0]['denominator']==1326.5010200000002+1610.51
 assert proof[0]['native_denominator']==proof[0]['denominator']
 assert len(proof[0]['members'])==3 and not result.oracle_reads
 assert result.values['Sheet!E1']==1-result.values['Sheet!Z2']/(result.values['Sheet!Z2']+result.values['Sheet!Z3'])

@pytest.mark.parametrize('weight',[1,1e-12,-1e-12])
def test_nonzero_feedback_cannot_be_erased(tmp_path,weight):
 result=run(tmp_path,system(weight),['E1'])
 assert not proofs(result)

@pytest.mark.parametrize('change',[{'Z2':0,'Z3':0},{'A1':'=E1*E1'}, {'A1':'=UNSUPPORTED(E1)'}, {'A1':'=E1-E1'}])
def test_zero_unknown_bilinear_and_cancellation_decline(tmp_path,change):
 formulas=system();formulas.update(change);result=run(tmp_path,formulas,['E1']);assert not proofs(result)

def test_native_finite_stop_is_not_replaced(tmp_path):
 result=run(tmp_path,system(),['E1'],count=1)
 assert any(not b['converged']for b in result.iterated)
 assert all(b['iterations']<=1 for b in result.iterated)

def test_endpoint_must_have_exact_proved_denominator(tmp_path):
 result=run(tmp_path,system(),['E1']);evaluator=result.endpoint_equation_checker.__self__
 block=next(b for b in result.iterated if b.get('certification',{}).get('equation_denominator_proofs'))
 evaluator.values['Sheet!C1']+=1e-7
 certificate=evaluator._affine_cycle_certificate(block['members'])
 assert not certificate.get('equation_denominator_proofs')
