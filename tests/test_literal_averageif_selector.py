from pathlib import Path
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph,write_csv
from xl_seg import model,project,evaluate
from xl_segment import stabilize_runtime_proof
from xl_seg import literal_criteria as S


def evaluator(tmp_path,monkeypatch,*,label='Forecasted',formula='=AVERAGEIF(A1:C1,"Actual",A2:C2)',values=(10,20,'=D2')):
    book=Workbook();sheet=book.active;sheet.title='Sheet'
    for ref,value in {'A1':'Actual','B1':'Actual','C1':label,'D2':formula,**dict(zip(('A2','B2','C2'),values))}.items():sheet[ref]=value
    p=tmp_path/'book.xlsx';book.save(p);book.close();g=AstGraph(p,verbose=False).build();write_csv(tmp_path,g.nodes,g.edges)
    graph=model.load(tmp_path,'book');cls=evaluate.Evaluator
    e=cls(graph,project.build(graph),strict_proof=True)
    return e,graph


def test_literal_excluded_feedback_is_acyclic_and_native_no_oracle(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch)
    result,*_=stabilize_runtime_proof(g,e.cg,set(),{'Sheet!D2'},evaluator_factory=type(e),calculation=evaluate.CalculationMetadata(available=True,iterate=True,iterate_count=100,iterate_delta=.001))
    assert result.values['Sheet!D2']==15
    assert not result.oracle_reads and not result.iterated
    n=g.nodes[g.root_of('Sheet!D2')];proof=S.projection(e,n)
    assert proof['selected_targets']==['Sheet!A2','Sheet!B2']
    assert proof['all_targets']==['Sheet!A2','Sheet!B2','Sheet!C2'] and proof['lane_approval'] is False
    assert set(e._active_node_sources(n.id))=={'Sheet!A1','Sheet!B1','Sheet!C1','Sheet!A2','Sheet!B2'}


@pytest.mark.parametrize('label,formula',[('="Forecasted"','=AVERAGEIF(A1:C1,"Actual",A2:C2)'),
    ('Forecasted','=AVERAGEIF(A1:C1,"Act*",A2:C2)'),
    ('Forecasted','=AVERAGEIF(A1:C1," Actual",A2:C2)'),
    ('Forecasted','=AVERAGEIF(A1:C1,"Actual",A2:B2)'),
    ('Forecasted','=AVERAGEIF(A1:C1,A1,A2:C2)'),
    ('Fórecasted','=AVERAGEIF(A1:C1,"Actual",A2:C2)')])
def test_unproved_selectors_keep_conservative_dependency_frontier(tmp_path,monkeypatch,label,formula):
    e,g=evaluator(tmp_path,monkeypatch,label=label,formula=formula);n=g.nodes[g.root_of('Sheet!D2')]
    assert S.projection(e,n) is None
    if 'A2:C2' in formula:assert 'Sheet!C2' in e._active_node_sources(n.id)


def test_selected_feedback_is_never_dropped(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch,label='Actual');n=g.nodes[g.root_of('Sheet!D2')]
    assert 'Sheet!C2' in S.projection(e,n)['selected_targets']
    assert 'Sheet!C2' in e._active_node_sources(n.id)


def test_case_insensitive_native_average_ignores_boolean_and_text_targets(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch,label='actual',values=(True,'text',12))
    e.values={'Sheet!A2':True,'Sheet!B2':'text','Sheet!C2':12}
    assert e._eval_cell('Sheet!D2')==12


def test_unselected_unresolved_does_not_supply_a_value_or_an_oracle(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch,values=(10,20,'=D2'));e.values={'Sheet!A2':10,'Sheet!B2':20}
    assert e._eval_cell('Sheet!D2')==15
    assert 'Sheet!C2' not in e.values


def test_selected_error_remains_a_failure(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch,label='Actual');e.values={'Sheet!A2':10,'Sheet!B2':20,'Sheet!C2':evaluate.ExcelError('#VALUE!')}
    assert isinstance(e._eval_cell('Sheet!D2'),evaluate.ExcelError)


def test_changed_live_selector_expands_frontier_without_reusing_stale_proof(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch);n=g.nodes[g.root_of('Sheet!D2')]
    assert 'Sheet!C2' not in e._active_node_sources(n.id)
    e.values['Sheet!C1']='Actual'
    assert S.projection(e,n) is None
    assert 'Sheet!C2' in e._active_node_sources(n.id)
    assert not e._source_walk_is_pure(n.id)


def test_selector_helper_is_bound_by_owning_verifier_fingerprint():
    from xl_seg.publication import verifier_paths
    assert Path(S.__file__).resolve() in verifier_paths()


def test_empty_selected_set_stays_division_error(tmp_path,monkeypatch):
    e,g=evaluator(tmp_path,monkeypatch,formula='=AVERAGEIF(A1:C1,"Missing",A2:C2)')
    assert repr(e._eval_cell('Sheet!D2'))==repr(evaluate.ExcelError('#DIV/0!'))
