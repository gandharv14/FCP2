"""Equation constraints may certify a selector, never replace native endpoints."""
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project
from xl_seg.evaluate import CalculationMetadata
from xl_segment import stabilize_runtime_proof


def run(tmp_path,formulas,outputs,*,delta=.001,count=100):
    book=Workbook();sheet=book.active;sheet.title='Sheet'
    for ref,value in formulas.items():sheet[ref]=value
    source=tmp_path/'book.xlsx';book.save(source);book.close()
    ast=AstGraph(source,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'book')
    inputs={ref for ref,node in graph.nodes.items() if node.is_cell and node.kind in {'input','label'}}
    calculation=CalculationMetadata(available=True,iterate=True,iterate_count=count,iterate_delta=delta,iterate_origin='explicit',iterate_count_origin='explicit',iterate_delta_origin='explicit')
    result,*_=stabilize_runtime_proof(graph,project.build(graph),inputs,{'Sheet!'+x for x in outputs},calculation=calculation)
    return result


def balance():
    return {'A1':100,'A2':'=150+0.1*A5','A3':'=-MIN(A1,A2)',
            'A4':'=SUM(A1,A3)','A5':'=IF(A4>0,0,-5)'}


def test_repaid_balance_selector_is_constant_on_its_equations(tmp_path):
    result=run(tmp_path,balance(),['A5'])
    cyclic=[x for x in result.iterated if x['output_relevant']]
    assert cyclic and all(x['certified'] for x in cyclic)
    proofs=[p for x in cyclic for p in x['certification'].get('equation_condition_proofs',[])]
    assert proofs and any(p['left_constant']==p['right_constant']==0 and p['selected'] is False for p in proofs)
    assert result.values['Sheet!A4']==0 and result.values['Sheet!A5']==-5
    assert not result.oracle_reads


def test_recursive_selector_cannot_certify_itself(tmp_path):
    result=run(tmp_path,{'A1':'=IF(A1>0,1,-1)'},['A1'])
    assert any(x['uniqueness']=='demonstrated_non_unique' for x in result.iterated)
    assert not isinstance(result.values['Sheet!A1'],(int,float))
    assert not any(x['certification'].get('equation_condition_proofs') for x in result.iterated)


def test_tiny_feedback_is_not_an_identically_zero_selector(tmp_path):
    formulas=balance();formulas['A4']='=SUM(A1,A3)+0.000000000001*A5'
    result=run(tmp_path,formulas,['A5'])
    assert result.values['Sheet!A4']==-5e-12
    assert not any(x['certification'].get('equation_condition_proofs') for x in result.iterated)


def test_unstable_capacity_branch_does_not_gain_constant_proof(tmp_path):
    formulas=balance();formulas['A2']='=100+0.1*A5'
    result=run(tmp_path,formulas,['A5'])
    assert not all(x['certified'] for x in result.iterated)


def test_valid_equilibrium_does_not_replace_native_finite_stop(tmp_path):
    formulas=balance();formulas['B1']='=0.5*B1+1';formulas['B2']='=ROUND(B1,3)'
    result=run(tmp_path,formulas,['A5','B2'])
    assert result.values['Sheet!B1']==1.9990234375
    assert result.values['Sheet!B2']==1.999
    assert all(x['iteration_limit']==100 and x['iteration_delta']==.001 for x in result.iterated)


def test_iteration_budget_remains_binding(tmp_path):
    result=run(tmp_path,balance(),['A5'],count=1)
    assert any(not x['converged'] for x in result.iterated)
    assert all(x['iterations']<=1 for x in result.iterated)


def test_boundary_substitution_cannot_revisit_cycle_member(tmp_path):
    from xl_seg.evaluate import Evaluator
    book=Workbook();book.active.title='Sheet';book.active['A1']='=IF(A1>0,1,-1)'
    path=tmp_path/'recursive.xlsx';book.save(path)
    ast=AstGraph(path,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'recursive');evaluator=Evaluator(graph,project.build(graph))
    evaluator.values={'Sheet!A1':0.0}
    certificate=evaluator._affine_cycle_certificate(['Sheet!A1'])
    assert not certificate['certified']
    assert certificate['unsupported_nodes'][0]['reason']=='non_constant_if_selector'


def test_equilibrium_constraint_cannot_change_native_endpoint_branch(tmp_path):
    result=run(tmp_path,balance(),['A5'])
    evaluator=result.endpoint_equation_checker.__self__
    members=result.iterated[0]['members']
    original=dict(evaluator.values)
    # This near-boundary endpoint selects the opposite branch to R=0. The
    # constraint proof must not silently substitute its equilibrium for it.
    evaluator.values['Sheet!A4']=1e-12
    proof=evaluator._affine_cycle_certificate(members)
    assert not proof['certified']
    evaluator.values=original
