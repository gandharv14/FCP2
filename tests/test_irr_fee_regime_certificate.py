from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project,evaluate
from xl_seg.cycle_regimes import certify
from xl_segment import stabilize_runtime_proof


def model_case(tmp_path,**changes):
    formulas={'A1':'=-IF(D1>32.5%,0.3*B1,IF(AND(D1>=27.5%,D1<32.5%),0.25*B1,0.2*B1))','A2':'=B1+A1','B1':10,'C1':-100,'C2':0,'C3':'=100+A2','D1':'=IFERROR(IRR(C1:C6),"N/A")'}
    formulas.update(changes);w=Workbook();s=w.active;s.title='S'
    for ref,value in formulas.items():s[ref]=value
    source=tmp_path/'book.xlsx';w.save(source);w.close()
    ast=AstGraph(source,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges);g=model.load(tmp_path,'book')
    calc=evaluate.CalculationMetadata(available=True,iterate=True,iterate_count=100,iterate_delta=.001)
    result,*_=stabilize_runtime_proof(g,project.build(g),set(),{'S!D1'},calculation=calc)
    e=result.endpoint_equation_checker.__self__;members=[m for m,c in result.cycle_membership.items() if c==result.cycle_membership['S!D1']]
    before=dict(e.values);p=certify(e,members);assert e.values==before
    return p,e,members


def test_all_source_fee_regimes_are_exhaustive_and_below_band(tmp_path):
    p,e,members=model_case(tmp_path)
    assert p['certified'] and set(p['members'])==set(members)
    assert p['periods']==2 and p['ignored_blank_cells']==['S!C4','S!C5','S!C6']
    assert len(p['regimes'])==3 and p['selected_regime']==0
    assert all(x['terminal_interval'][1]<x['threshold_terminal_interval'][0] for x in p['regimes'])
    assert p['native_endpoint_replaced'] is False and p['independent_endpoint_equivalence_required'] is True


def test_positive_intermediate_flow_rejected(tmp_path):
    p,*_=model_case(tmp_path,C2=1);assert not p['certified']


def test_multiple_sign_changes_rejected(tmp_path):
    p,*_=model_case(tmp_path,C2=-1);assert not p['certified']


def test_near_band_boundary_rejected_conservatively(tmp_path):
    p,*_=model_case(tmp_path,B1=78.203125);assert not p['certified']
    assert 'regime reaches' in p['detail']


def test_fee_expression_cannot_borrow_cyclic_value(tmp_path):
    p,*_=model_case(tmp_path,A1='=-IF(D1>32.5%,0.3*A2,IF(AND(D1>=27.5%,D1<32.5%),0.25*A2,0.2*A2))')
    assert not p['certified']


def test_inconsistent_band_coverage_rejected(tmp_path):
    p,*_=model_case(tmp_path,A1='=-IF(D1>32.5%,0.3*B1,IF(AND(D1>=27.5%,D1<33.5%),0.25*B1,0.2*B1))')
    assert not p['certified']


def test_native_branch_cannot_be_replaced_by_regime_equilibrium(tmp_path):
    p,e,members=model_case(tmp_path);assert p['certified']
    e.values['S!D1']=.30
    assert not certify(e,members)['certified']


def test_mixed_regime_envelope_must_also_stay_below_band(tmp_path):
    p,*_=model_case(tmp_path,B1=100,C3='=160+A1-A1')
    assert not p['certified'] and 'mixed-regime' in p['detail']


def test_a_fixed_point_proof_cannot_accept_a_bad_native_endpoint(tmp_path):
    p,e,members=model_case(tmp_path);assert p['certified']
    e.values['S!A1']=1000
    rejected=certify(e,members)
    assert not rejected['certified'] and 'native endpoint' in rejected['detail']
    assert e.values['S!A1']==1000


def test_hidden_indirect_cycle_in_external_parameter_rejected(tmp_path):
    import pytest
    from xl_seg.cycle_regimes import Recognizer,Unsupported
    p,e,members=model_case(tmp_path,B1='=IF(TRUE,10,INDIRECT("A2"))')
    assert not p['certified'] and 'S!B1' not in members
    with pytest.raises(Unsupported,match='dynamic or opaque'):
        Recognizer(e,members).outside('S!B1')


def test_hidden_offset_cycle_in_external_parameter_rejected(tmp_path):
    p,*_=model_case(tmp_path,B1='=IF(TRUE,10,OFFSET(Z1,1,-25))',Z1=0)
    assert not p['certified']


def test_interval_underflow_encloses_zero_and_overflow_fails_closed():
    import sys
    import pytest
    from xl_seg.cycle_regimes import point,mul,Unsupported
    tiny=float.fromhex('0x0.0000000000001p-1022')
    lo,hi=mul(point(tiny),point(tiny))
    assert lo<0<hi
    with pytest.raises(Unsupported):mul(point(sys.float_info.max),point(2))
