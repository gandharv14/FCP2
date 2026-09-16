from dataclasses import replace
from pathlib import Path
import sys
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph, write_csv
from xl_seg import model, project, evaluate
from xl_segment import stabilize_runtime_proof
from xl_seg.formula_presence import projection
from xl_seg.evaluate import Evaluator

def fixture(tmp_path, formula='=COUNTA(A1:B1)', first='=C1', second='=""'):
    wb=Workbook();s=wb.active;s.title='Sheet'
    s['A1']=first;s['B1']=second;s['C1']=formula
    path=tmp_path/'book.xlsx';wb.save(path);wb.close()
    ast=AstGraph(path,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    g=model.load(tmp_path,'book');e=Evaluator(g,project.build(g),strict_proof=True)
    return e,g,g.nodes[g.root_of('Sheet!C1')]

def test_formula_presence_breaks_only_value_cycle(tmp_path):
    e,g,n=fixture(tmp_path)
    result,*_=stabilize_runtime_proof(g,e.cg,set(),{'Sheet!C1'},evaluator_factory=Evaluator)
    assert result.values['Sheet!C1']==2 and not result.iterated and not result.oracle_reads
    assert projection(e,n)['formula_cells']==['Sheet!A1','Sheet!B1']
    assert e._active_node_sources(n.id)==set()

@pytest.mark.parametrize('value',[None,'',False,0,17,evaluate.ExcelError('#VALUE!'),evaluate.Unresolved('cycle')])
def test_computed_state_does_not_delete_source_formula(tmp_path,value):
    e,g,n=fixture(tmp_path);e.values={'Sheet!A1':value,'Sheet!B1':value}
    assert e._eval_cell('Sheet!C1')==2
    assert not e.oracle_reads and not e.read_attempts

@pytest.mark.parametrize('first',[3,'literal',None])
def test_primitive_or_absent_cell_cannot_be_pruned(tmp_path,first):
    e,g,n=fixture(tmp_path,first=first)
    assert projection(e,n) is None
    assert 'Sheet!A1' in e._active_node_sources(n.id)

@pytest.mark.parametrize('formula',['=COUNTA(A1:B1,A1)','=COUNTA(INDIRECT("A1:B1"))','=COUNTA(A1:A600)'])
def test_nonclosed_shapes_keep_existing_path(tmp_path,formula):
    e,g,n=fixture(tmp_path,formula=formula);assert projection(e,n) is None

@pytest.mark.parametrize('mutation',['missing_edge','duplicate_edge','wrong_range','truncated','array','absent_root','empty_formula','bad_parse'])
def test_independent_completeness_and_source_constraints(tmp_path,mutation):
    e,g,n=fixture(tmp_path);assert projection(e,n)
    if mutation=='missing_edge':g.in_edges[n.id].pop()
    elif mutation=='duplicate_edge':g.in_edges[n.id][1]=g.in_edges[n.id][0]
    elif mutation=='wrong_range':g.in_edges[n.id][0]=replace(g.in_edges[n.id][0],via_range='A1:A1')
    elif mutation=='truncated':n.range_truncated=True
    elif mutation=='array':g.nodes['Sheet!A1'].array_formula=True
    elif mutation=='empty_formula':g.nodes['Sheet!A1'].formula=''
    elif mutation=='bad_parse':g.nodes['Sheet!A1'].parse_status='error'
    else:g.root_of=lambda _:None
    assert projection(e,n) is None

def test_actual_primitive_override_preserves_dependency_and_counts_blank(tmp_path):
    e,g,n=fixture(tmp_path,first=4);e.values={'Sheet!A1':None,'Sheet!B1':''}
    assert projection(e,n) is None and e._eval_cell('Sheet!C1')==1
    assert 'Sheet!A1' in e._active_node_sources(n.id)


@pytest.mark.parametrize('value',[None,'',0,'replacement'])
def test_explicit_override_cannot_supply_formula_occupancy(tmp_path,value):
    e,g,n=fixture(tmp_path);e.overrides={'Sheet!A1':value}
    assert projection(e,n) is None
    assert 'Sheet!A1' in e._active_node_sources(n.id)


def test_real_workbook_formula_replaced_by_blank_loses_presence_proof(tmp_path):
    e,g,n=fixture(tmp_path,first=None)
    assert 'Sheet!A1' not in g.nodes
    assert projection(e,n) is None
    e.values={'Sheet!B1':''}
    assert e._eval_cell('Sheet!C1')==1


def test_unrecognized_override_contract_is_not_assumed(tmp_path):
    e,g,n=fixture(tmp_path);e.overrides=None
    assert projection(e,n) is None


def test_presence_module_is_in_publication_verifier_identity():
    from xl_seg import formula_presence
    from xl_seg.publication import verifier_paths
    assert Path(formula_presence.__file__).resolve() in verifier_paths()
