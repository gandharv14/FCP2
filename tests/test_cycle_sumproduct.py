"""Range positions and linear factors are required for SUMPRODUCT proofs."""
import pytest
from tests.test_cycle_equation_selector import run

@pytest.mark.parametrize('formula',['=SUMPRODUCT(A1:B1,A2:B2)','=SUMPRODUCT(A2:B2,A1:B1)'])
def test_linear_range_sumproduct_unique_fixed_point(tmp_path,formula):
 r=run(tmp_path,{'A1':formula,'B1':1,'A2':.5,'B2':1},['A1'],delta=1e-10)
 blocks=[b for b in r.iterated if b['output_relevant']]
 assert blocks and all(b['certified']for b in blocks)
 assert r.values['Sheet!A1']==pytest.approx(2,abs=1e-8)
 assert not r.oracle_reads

@pytest.mark.parametrize('formula',['=SUMPRODUCT(A1:B1,{0.5,1})','=SUMPRODUCT(A1:B1,A1:B1)','=SUMPRODUCT(A1:B1,A2:A3)','=SUMPRODUCT(A1:B1,A2:C2)','=SUMPRODUCT(A1:B1,A2:B2,1/0)'])
def test_nonlinear_different_shapes_and_errors_cannot_certify(tmp_path,formula):
 r=run(tmp_path,{'A1':formula,'B1':1,'A2':.5,'A3':1,'B2':1,'C2':1},['A1'])
 assert not all(b['certified']for b in r.iterated if b['output_relevant'])

def test_different_constant_weights_preserve_array_position(tmp_path):
 r=run(tmp_path,{'A1':'=SUMPRODUCT(A1:B1,A2:B2)','B1':4,'A2':.25,'B2':.5},['A1'],delta=1e-10)
 assert all(b['certified']for b in r.iterated if b['output_relevant'])
 assert r.values['Sheet!A1']==pytest.approx(8/3,abs=1e-8)

def test_native_iteration_cap_not_replaced_by_fixed_point(tmp_path):
 r=run(tmp_path,{'A1':'=SUMPRODUCT(A1:B1,A2:B2)','B1':1,'A2':.5,'B2':1},['A1'],count=1)
 assert r.values['Sheet!A1']!=2
 assert all(b['iterations']<=1 for b in r.iterated)
