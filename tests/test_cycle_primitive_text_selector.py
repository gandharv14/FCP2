"""Only source primitives can certify invariant text selectors in a cycle."""
import pytest
from tests.test_cycle_equation_selector import run

@pytest.mark.parametrize('label,op,expected',[('yes','=',2),('no','=',4),('yes','<>',4),('no','<>',2)])
def test_primitive_text_selects_unique_affine_branch(tmp_path,label,op,expected):
 r=run(tmp_path,{'A1':f'=IF(B1{op}"yes",0.5*A1+1,0.5*A1+2)','B1':label},['A1'],delta=1e-10)
 blocks=[x for x in r.iterated if x['output_relevant']]
 assert blocks and all(x['certified']for x in blocks)
 assert r.values['Sheet!A1']==pytest.approx(expected,abs=1e-8)
 assert any(x['certification'].get('constant_text_condition_proofs')for x in blocks)
 assert not r.oracle_reads

@pytest.mark.parametrize('formula',['=IF(B1="yes",A1,0)','=IF(B1="yes",A1+1,0)','=IF(B1="yes",UNSUPPORTED(A1),0)'])
def test_invariant_text_does_not_waive_nonunique_divergent_or_unsupported_equations(tmp_path,formula):
 r=run(tmp_path,{'A1':formula,'B1':'yes'},['A1'])
 assert not all(x['certified']for x in r.iterated if x['output_relevant'])

def test_formula_text_cannot_be_mistaken_for_primitive_text(tmp_path):
 r=run(tmp_path,{'A1':'=IF(B1="yes",0.5*A1+1,0)','B1':'="yes"'},['A1'])
 blocks=[x for x in r.iterated if x['output_relevant']]
 assert blocks and not all(x['certified']for x in blocks)
 assert not any(x['certification'].get('constant_text_condition_proofs')for x in blocks)

def test_changed_endpoint_primitive_does_not_certify(tmp_path):
 r=run(tmp_path,{'A1':'=IF(B1="yes",0.5*A1+1,0)','B1':'yes'},['A1'])
 e=r.endpoint_equation_checker.__self__;e.values['Sheet!B1']='no'
 proof=e._affine_cycle_certificate(['Sheet!A1'])
 assert not proof['certified']

def test_native_finite_iteration_endpoint_and_cap_remain_unchanged(tmp_path):
 r=run(tmp_path,{'A1':'=IF(B1="yes",0.5*A1+1,0)','B1':'yes'},['A1'],count=1)
 assert all(x['iterations']<=1 for x in r.iterated)
 assert any(not x['converged']for x in r.iterated)
