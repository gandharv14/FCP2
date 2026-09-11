"""Reference-returning INDEX range endpoints, never value/cached-answer guesses."""
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph, write_csv, parse_formula
from xl_seg import model, project, restriction_cone
from xl_seg.evaluate import Evaluator, CalculationMetadata, Unresolved, ExcelError
from xl_segment import stabilize_runtime_proof


def build(tmp_path, formula, *, cells=None, target='G8', omitted=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    book=Workbook(); sheet=book.active; sheet.title='Sheet'
    cells=dict(cells or {'A1':2,'B1':3,'C1':5,'A2':7,'B2':11,'C2':13})
    for ref, value in cells.items(): sheet[ref]=value
    sheet[target]=formula
    path=tmp_path/'book.xlsx'; book.save(path);book.close()
    ast=AstGraph(path,verbose=False).build();write_csv(tmp_path,ast.nodes,ast.edges)
    graph=model.load(tmp_path,'book')
    for node in graph.nodes.values():
        if node.kind=='formula':node.value='987654321'
    seeds={'Sheet!'+ref for ref,value in cells.items() if not(isinstance(value,str) and value.startswith('=')) and ref not in omitted}
    result,inputs,_,proof=stabilize_runtime_proof(graph,project.build(graph),seeds,{'Sheet!'+target},calculation=CalculationMetadata(available=True,iterate=False))
    assert not result.oracle_reads
    return graph,result,inputs,proof


@pytest.mark.parametrize('separator',[':', ' : ', ' :', ': ', '\n:\n'])
def test_exact_parser_spelling_has_real_colon_and_two_index_nodes(separator):
    ast=parse_formula('=SUM(INDEX(A1:C1,1)'+separator+'INDEX(A1:C1,3))')
    colon=ast.args[0]
    assert colon.kind=='infix' and colon.name==':'
    assert [(a.kind,a.name) for a in colon.args]==[('func','INDEX'),('func','INDEX')]


@pytest.mark.parametrize('formula,expected',[
    ('=SUM(INDEX(A1:C1,1):INDEX(A1:C1,3))',10),
    ('=SUM(INDEX(A1:C1,3) : INDEX(A1:C1,1))',10),
    ('=SUM(INDEX(A1:C2,1,2):INDEX(A1:C2,2,3))',32),
    ('=SUM(INDEX(A1:C2,0,2):INDEX(A1:C2,1,1))',23),
    ('=SUM(INDEX(A1:C1,1):INDEX(A1:C1,0))',10),
    ('=SUM(INDEX(A1:C2,0,0):INDEX(A1:C2,2,3))',41),
    ('=IFERROR(SUM(INDEX(A1:C1,-1):INDEX(A1:C1,1)),0)',0),
    ('=SUM(INDEX(A1:A2,1):INDEX(A1:A2,2))',9),
    ('=SUM(INDEX(A1,1):INDEX(C2,1))',41),
])
def test_reference_shapes_equal_direct_range_values(tmp_path,formula,expected):
    _,result,_,_=build(tmp_path,formula)
    assert result.values['Sheet!G8']==expected


@pytest.mark.parametrize('separator',[':', ' : '])
@pytest.mark.parametrize('column,expected',[('H',0),('I',-10.5),('J',-.5),('K',-1.5),('L',-2.5),('M',-3.5)])
def test_0657_formula_dependency_shape_without_workbook_cache(tmp_path,separator,column,expected):
    formula='=IFERROR(SUM(INDEX($H$73:$M$73, MAX(1, COLUMN()-COLUMN($H$1)-$C$75))'+separator+'INDEX($H$73:$M$73, COLUMN()-COLUMN($H$1)-1)) / $C$75, 0)*-1'
    cells={f'{chr(72+i)}73':i+1 for i in range(6)};cells['C75']=2
    _,result,inputs,proof=build(tmp_path,formula,cells=cells,target=column+'78')
    assert result.values['Sheet!'+column+'78']==expected
    assert 'Sheet!'+column+'78' not in inputs
    if column=='L':
        assert set(proof['runtime_radj']['Sheet!L78'])=={'Sheet!I73','Sheet!J73','Sheet!C75'}


@pytest.mark.parametrize('end',[
    'INDEX(OFFSET(A1,0,0),1)', 'INDEX({1,2},1)', 'INDEX((A1,B1),1,1,1)',
    'INDEX(A1:C1,1.5)', 'INDEX(A1:C1,"2")', 'INDEX(A1:C1,TRUE)',
    'INDEX(A1:C1,UNKNOWN())', 'INDEX(A1:C1,A1:B1)', 'INDEX(A1:C1,1,1,1)',
])
def test_unproved_reference_or_selector_cannot_be_caught_as_valid_answer(tmp_path,end):
    _,result,_,_=build(tmp_path,'=IFERROR(SUM(INDEX(A1:C1,1) : '+end+'),0)')
    assert isinstance(result.values['Sheet!G8'],Unresolved)


def test_selected_values_only_and_selector_formula_frontier(tmp_path):
    cells={'A1':'=G8','B1':'=J1+J2','C1':'=1/0','J1':7,'J2':11,'K1':'=L1','L1':2}
    _,result,inputs,proof=build(tmp_path,'=SUM(INDEX(A1:C1,K1):INDEX(A1:C1,K1))',cells=cells)
    assert result.values['Sheet!G8']==18
    cone=restriction_cone._proof_cone(['Sheet!G8'],proof['runtime_radj'])
    assert {'Sheet!K1','Sheet!L1','Sheet!B1','Sheet!J1','Sheet!J2'} <= cone
    assert not {'Sheet!A1','Sheet!C1'} & cone
    assert {'Sheet!L1','Sheet!J1','Sheet!J2'} <= inputs
    assert 'Sheet!G8' not in result.cycle_membership


def test_selected_target_cycle_is_not_erased(tmp_path):
    _,result,_,proof=build(tmp_path,'=SUM(INDEX(A1:C1,1):INDEX(A1:C1,2))',cells={'A1':'=G8','B1':3,'C1':5})
    assert isinstance(result.values['Sheet!G8'],Unresolved)
    assert 'Sheet!A1' in proof['runtime_radj']['Sheet!G8']


def test_selector_error_is_excel_error_but_missing_frontier_unresolved(tmp_path):
    _,result,_,_=build(tmp_path,'=SUM(INDEX(A1:C1,1/0):INDEX(A1:C1,1))')
    assert isinstance(result.values['Sheet!G8'],ExcelError)
    graph,_,_,_=build(tmp_path/'missing','=SUM(INDEX(A1:C1,J1):INDEX(A1:C1,1))',cells={'A1':2,'B1':3,'C1':5,'J1':2})
    evaluator=Evaluator(graph,project.build(graph),strict_proof=True,proof_outputs={"Sheet!G8"})
    result=evaluator.run({'Sheet!A1','Sheet!B1','Sheet!C1'})
    assert isinstance(result.values['Sheet!G8'],Unresolved)
    assert 'Sheet!J1' in result.runtime_radj['Sheet!G8']


def test_literal_and_quoted_sheet_colons_untouched():
    assert parse_formula('="INDEX(A1,1):INDEX(A1,2)"').kind=='const'
    assert parse_formula("=SUM('A:B'!A1:A3)").args[0].name=="'A:B'!A1:A3"


@pytest.mark.parametrize('formula,expected',[
    ('=SUM(INDEX(A1:C1,1):INDEX(A1:C1,3))',3),
    ('=COUNT(INDEX(A1:C1,1):INDEX(A1:C1,3))',1),
    ('=AVERAGE(INDEX(A1:C1,1):INDEX(A1:C1,3))',3),
])
def test_genuine_blank_cells_keep_range_aggregation_semantics(tmp_path,formula,expected):
    _,result,_,_=build(tmp_path,formula,cells={'B1':3})
    assert result.values['Sheet!G8']==expected


def test_selected_formula_error_propagates(tmp_path):
    _,result,_,_=build(tmp_path,'=SUM(INDEX(A1:C1,1):INDEX(A1:C1,2))',cells={'A1':2,'B1':'=1/0','C1':5})
    assert isinstance(result.values['Sheet!G8'],ExcelError)
    assert result.values['Sheet!G8'].code=='#DIV/0!'


def test_middle_formula_between_different_endpoint_areas_joins_cone(tmp_path):
    _,result,inputs,proof=build(tmp_path,'=SUM(INDEX(A1,1):INDEX(C1,1))',cells={'A1':2,'B1':'=J1*J2','C1':5,'J1':3,'J2':7})
    assert result.values['Sheet!G8']==28
    assert {'Sheet!J1','Sheet!J2'} <= inputs
    assert 'Sheet!B1' in proof['runtime_radj']['Sheet!G8']


@pytest.mark.parametrize('selector',['G8','J1','RAND()'])
def test_cyclic_or_random_selectors_not_seeded(tmp_path,selector):
    _,result,_,_=build(tmp_path,f'=IFERROR(SUM(INDEX(A1:C1,{selector}):INDEX(A1:C1,1)),0)',cells={'A1':2,'B1':3,'C1':5,'J1':'=G8'})
    assert isinstance(result.values['Sheet!G8'],Unresolved)


def test_dynamic_plan_recomputed_when_proven_selector_changes(tmp_path):
    graph,_,_,_=build(tmp_path,'=SUM(INDEX(A1:C1,J1):INDEX(A1:C1,J1))',cells={'A1':2,'B1':3,'C1':5,'J1':2})
    ev=Evaluator(graph,project.build(graph));op=next(n for n in graph.nodes.values() if n.op==':')
    ev.values['Sheet!J1']=2
    assert ev._reference_range_targets(op)==['Sheet!B1']
    ev.values['Sheet!J1']=3
    assert ev._reference_range_targets(op)==['Sheet!C1']
    ev.values['Sheet!J1']=Unresolved('missing-primitive')
    assert isinstance(ev._reference_range_targets(op),Unresolved)


@pytest.mark.parametrize('payload',[
    ['Sheet!A1','Sheet!A1'], ['Sheet!A1','Sheet!C1'], ['Sheet!B1','Sheet!A1'],
    ['Sheet!A1','Other!B1'], ['Sheet!A0'], ['Sheet!XFE1'], ['invalid'],
    ['Sheet!A1']*10001,
])
def test_malformed_reference_shape_rejected_before_value_reads(tmp_path,monkeypatch,payload):
    graph,_,_,_=build(tmp_path,'=SUM(INDEX(A1:C1,1):INDEX(A1:C1,2))')
    ev=Evaluator(graph,project.build(graph));op=next(n for n in graph.nodes.values() if n.op=='INDEX')
    specs=list(ev._arg_specs(op));specs[0]=('span',payload);ev._arg_cache[op.id]=specs
    def forbidden(*args,**kwargs):raise AssertionError('rejected shape must not read a value')
    monkeypatch.setattr(ev,'_read',forbidden)
    assert isinstance(ev._index_reference_endpoint(op),Unresolved)


@pytest.mark.parametrize('reference',['A1:XFD1048576','A:A','A1:A10001','A0:C1','A1:XFE1'])
def test_unbounded_or_invalid_range_nodes_rejected_before_expansion(tmp_path,monkeypatch,reference):
    from dataclasses import replace
    graph,_,_,_=build(tmp_path,'=SUM(INDEX(A1:C1,1):INDEX(A1:C1,2))')
    ev=Evaluator(graph,project.build(graph));op=next(n for n in graph.nodes.values() if n.op=='INDEX')
    source=replace(graph.nodes['Sheet!A1'],id='test-range',kind='range',coordinate=reference)
    graph.nodes[source.id]=source;specs=list(ev._arg_specs(op));specs[0]=('scalar',source.id);ev._arg_cache[op.id]=specs
    def forbidden(*args,**kwargs):raise AssertionError('rejected range must not read a value')
    monkeypatch.setattr(ev,'_read',forbidden)
    assert isinstance(ev._index_reference_endpoint(op),Unresolved)


def test_cross_sheet_endpoints_and_external_origin_remain_unproved(tmp_path):
    from dataclasses import replace
    graph,_,_,_=build(tmp_path,'=IFERROR(SUM(INDEX(A1:C1,1):INDEX(A1:C1,2)),0)')
    ev=Evaluator(graph,project.build(graph));colon=next(n for n in graph.nodes.values() if n.op==':')
    endpoint=ev._index_range_endpoints(colon)[1]
    other=replace(graph.nodes['Sheet!A1'],id='Other!A1',sheet='Other');graph.nodes[other.id]=other
    specs=list(ev._arg_specs(endpoint));specs[0]=('scalar',other.id);ev._arg_cache[endpoint.id]=specs
    # Bind the endpoint origin consistently: cross-sheet rejection is in colon.
    edges=[replace(e,source=other.id) if e.arg_index==0 else e for e in graph.in_edges[endpoint.id]]
    graph.in_edges[endpoint.id]=edges
    graph.nodes[next(e.source for e in edges if e.arg_index==1)].value='1'
    assert isinstance(ev._reference_range_targets(colon),Unresolved)
    other.kind='external'
    assert isinstance(ev._index_reference_endpoint(endpoint),Unresolved)


def test_old_malformed_colon_name_still_fails_closed(tmp_path):
    from dataclasses import replace
    graph,_,_,_=build(tmp_path,'=SUM(A1:C1)')
    old=replace(graph.nodes['Sheet!A1'],id='name::',kind='name',label=':',value='0')
    graph.nodes[old.id]=old
    assert Evaluator(graph,project.build(graph))._eval_node(old.id).reason=='unsupported-reference-constructor'


def test_unproved_endpoint_cannot_hide_behind_other_endpoints_error(tmp_path):
    _,result,_,_=build(tmp_path,'=IFERROR(SUM(INDEX(A1:C1,1/0):INDEX(OFFSET(A1,0,0),1)),0)')
    assert isinstance(result.values['Sheet!G8'],Unresolved)
