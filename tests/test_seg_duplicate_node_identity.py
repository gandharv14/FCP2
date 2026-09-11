"""An AST identity cannot be redefined by later CSV rows."""
import csv
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from xl_ast_graph import AstGraph, write_csv
from xl_seg import model, partition, project
from xl_seg.evaluate import CalculationMetadata, Evaluator
from xl_segment import _verify


def fixture(tmp_path):
    source = tmp_path / 'case.xlsx'
    book = Workbook()
    book.active.title = 'Sheet'
    book.active['A1'] = 1
    book.active['B1'] = '=A1+1'
    book.save(source)
    book.close()
    ast = AstGraph(source, verbose=False).build()
    write_csv(tmp_path, ast.nodes, ast.edges)
    curation = tmp_path / 'curation.toml'
    curation.write_text('[[output]]\ncell="Sheet!B1"\n')
    return source, curation


def append_definition(tmp_path, node_id, changes):
    with (tmp_path / 'nodes.csv').open(newline='') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    row = next(row for row in rows if row['id'] == node_id)
    with (tmp_path / 'nodes.csv').open('a', newline='') as stream:
        csv.DictWriter(stream, fieldnames=fields).writerow({**row, **changes})
    return len(rows)


def verification(graph, source, curation, tmp_path, expected=2):
    cg = project.build(graph)
    calculation = CalculationMetadata(available=True, iterate=False)
    result = Evaluator(graph, cg, strict_proof=True, calculation=calculation).run({'Sheet!A1'})
    bands = SimpleNamespace(bands={
        'inputs': SimpleNamespace(cells=['Sheet!A1']),
        'outputs': SimpleNamespace(cells=['Sheet!B1']),
    })
    components = SimpleNamespace(comp_members={'i': {'inputs'}, 'o': {'outputs'}})
    buckets = SimpleNamespace(bucket={'i': partition.INPUT, 'o': partition.OUTPUT})
    return _verify(cg, result, {'Sheet!A1'}, {'Sheet!B1'}, buckets, components,
                   bands, SimpleNamespace(sample=10), expected_cache=lambda *args: expected,
                   calculation=calculation, source_path=source, ast_dir=tmp_path,
                   curation_path=curation), result


def test_unique_graph_still_independently_reconstructs(tmp_path):
    source, curation = fixture(tmp_path)
    graph = model.load(tmp_path, 'case')
    report, result = verification(graph, source, curation, tmp_path)
    assert graph.integrity_errors == []
    assert report['status'] == 'pass'
    assert result.values['Sheet!B1'] == 2
    assert not result.oracle_reads


@pytest.mark.parametrize('kind', ['input', 'formula', 'op', 'const'])
@pytest.mark.parametrize('changed', [False, True])
def test_duplicate_identity_is_retained_as_error_without_overwriting(tmp_path, kind, changed):
    fixture(tmp_path)
    clean = model.load(tmp_path, 'case')
    original = next(n for n in clean.nodes.values() if n.kind == kind)
    changes = {'value': '654321', 'parse_status': 'error', 'owner': 'Other!Z99'} if changed else {}
    index = append_definition(tmp_path, original.id, changes)
    graph = model.load(tmp_path, 'case')
    assert graph.integrity_errors == [{
        'code': 'duplicate_node_id', 'node': original.id, 'node_index': index,
    }]
    assert asdict(graph.nodes[original.id]) == asdict(original)
    assert len(graph.nodes) == len(clean.nodes)
    assert [asdict(e) for e in graph.edges] == [asdict(e) for e in clean.edges]


def test_duplicate_input_fails_verification_even_when_every_output_matches(tmp_path):
    source, curation = fixture(tmp_path)
    append_definition(tmp_path, 'Sheet!A1', {})
    graph = model.load(tmp_path, 'case')
    report, result = verification(graph, source, curation, tmp_path)
    assert result.values['Sheet!B1'] == 2
    assert not result.oracle_reads
    assert report['status'] == 'fail'
    assert 'ast_integrity_error' in report['blocking_reasons']
    assert report['ast_integrity']['errors'][0]['code'] == 'duplicate_node_id'


def test_conflicting_input_cannot_replace_reconstruction_semantics(tmp_path):
    source, curation = fixture(tmp_path)
    append_definition(tmp_path, 'Sheet!A1', {'value': '2'})
    graph = model.load(tmp_path, 'case')
    report, result = verification(graph, source, curation, tmp_path, expected=3)
    assert result.values['Sheet!B1'] == 2
    assert not result.oracle_reads
    assert report['status'] == 'fail'
    assert 'ast_integrity_error' in report['blocking_reasons']
