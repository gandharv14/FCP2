"""A certified unique equilibrium does not replace Excel's finite-stop values."""
import pytest
from openpyxl import Workbook
from xl_ast_graph import AstGraph, write_csv
from xl_seg import model, project
from xl_seg.evaluate import CalculationMetadata
from xl_segment import stabilize_runtime_proof


@pytest.mark.parametrize('delta,iterations,endpoint,rounded', [
    (.001, 11, 1.9990234375, 1.999),
    (.01, 8, 1.9921875, 1.992),
])
@pytest.mark.parametrize('formula,kind', [
    ('=(A1>=2)*1', 'threshold'),
    ('=ROUND(A1,3)', 'rounding'),
])
@pytest.mark.parametrize('cached_answer', ['', '0', '987654321'])
def test_downstream_values_honor_original_finite_iteration_stop(
    tmp_path, delta, iterations, endpoint, rounded, formula, kind, cached_answer,
):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Sheet'
    sheet['A1'] = '=.5*A1+1'
    sheet['A2'] = formula
    source = tmp_path / 'fixture.xlsx'
    workbook.save(source)
    workbook.close()
    ast = AstGraph(source, verbose=False).build()
    write_csv(tmp_path, ast.nodes, ast.edges)
    graph = model.load(tmp_path, 'fixture')
    # These formula caches are never supplied primitives or solver seeds.
    graph.nodes['Sheet!A1'].value = cached_answer
    graph.nodes['Sheet!A2'].value = cached_answer
    calculation = CalculationMetadata(
        available=True, iterate=True, iterate_count=100, iterate_delta=delta,
        iterate_origin='explicit', iterate_count_origin='explicit',
        iterate_delta_origin='explicit',
    )
    result, supplied, _, proof = stabilize_runtime_proof(
        graph, project.build(graph), set(), {'Sheet!A2'},
        calculation=calculation,
    )
    cycle, = result.iterated
    assert cycle['certified'] and cycle['converged']
    assert cycle['iteration_limit'] == 100
    assert cycle['iteration_delta'] == delta
    assert cycle['iterations'] == iterations
    assert result.values['Sheet!A1'] == endpoint
    assert result.values['Sheet!A2'] == (0.0 if kind == 'threshold' else rounded)
    # The mathematical fixed point is exactly2, but replacing the original
    # finite-stop endpoint would flip the predicate or alter the rounded value.
    assert result.values['Sheet!A1'] != 2.0
    assert not result.oracle_reads
    assert not supplied
    assert 'Sheet!A1' in proof['runtime_radj']['Sheet!A2']
