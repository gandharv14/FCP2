"""Disk indexes must preserve every graph and reconstruction decision."""
import csv
from dataclasses import asdict, fields

import pytest
from openpyxl import Workbook

from xl_ast_graph import AstGraph, write_csv
from xl_seg import model, project
from xl_seg.evaluate import CalculationMetadata, Evaluator


def fixture(path):
    source = path / 'book.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.title = "1 O'Brien"
    for address, value in {'A1': 3, 'A2': 5, 'B1': '=SUM(A1:A2)+2',
                           'C1': '=B1*2', 'D1': '=A1', 'E1': '=7',
                           'F1': '=IF(A1>0,C1,0)', 'G1': '=OFFSET(A1,1,0)',
                           'H1': '=INDIRECT("A2")'}.items():
        sheet[address] = value
    other = book.create_sheet('Other Sheet')
    other['A1'] = "='1 O''Brien'!C1"
    book.save(source)
    book.close()
    ast = AstGraph(source, verbose=False).build()
    write_csv(path, ast.nodes, ast.edges)
    if hasattr(ast, 'close'):
        ast.close()


def signature(graph):
    return {
        'nodes': [asdict(n) for n in graph.nodes.values()],
        'edges': [asdict(e) for e in graph.edges],
        'incoming': {k: [asdict(e) for e in v] for k, v in graph.in_edges.items()},
        'outgoing': {k: [asdict(e) for e in v] for k, v in graph.out_edges.items()},
        'capabilities': graph.capabilities,
        'integrity': graph.integrity_errors,
        'schema': graph.ast_schema_version,
    }


def test_exact_graph_projection_and_independent_evaluation(tmp_path):
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='memory') as memory, model.load(tmp_path, 'book', storage='disk') as disk:
        assert signature(disk) == signature(memory)
        cg1, cg2 = project.build(memory), project.build(disk)
        assert cg1.adj == cg2.adj
        assert cg1.radj == cg2.radj
        assert {k: asdict(v) for k, v in cg1.info.items()} == {k: asdict(v) for k, v in cg2.info.items()}
        inputs = {n.id for n in memory.iter_nodes('input')}
        calc = CalculationMetadata(available=True, iterate=False)
        one = Evaluator(memory, cg1, strict_proof=True, calculation=calc).run(inputs)
        two = Evaluator(disk, cg2, strict_proof=True, calculation=calc).run(inputs)
        for field in fields(one):
            if field.name not in {'endpoint_equation_checker', 'benchmark'}:
                assert getattr(one, field.name) == getattr(two, field.name), field.name
        assert not two.oracle_reads
        assert two.values['Other Sheet!A1'] == 20
        database = disk._store.path
        assert database.exists()
    assert not database.parent.exists()
    with pytest.raises(ValueError, match='closed'):
        len(disk.nodes)


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'owner', 'parse', 'roots', 'schema'])
def test_corrupt_evidence_has_identical_diagnostics(tmp_path, mutation):
    fixture(tmp_path)
    file = tmp_path / ('edges.csv' if mutation in ('missing', 'roots') else 'nodes.csv')
    with file.open(newline='') as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    if mutation == 'duplicate':
        rows.append({**rows[0], 'value': '999'})
    elif mutation == 'missing':
        rows.append({**rows[0], 'source': 'missing', 'target': 'also_missing'})
    elif mutation == 'roots':
        rows.append(next(r for r in rows if r['target'].endswith('!B1')))
    elif mutation == 'owner':
        next(r for r in rows if r['kind'] == 'op')['owner'] = 'missing'
    elif mutation == 'parse':
        next(r for r in rows if r['kind'] == 'formula').update(parse_status='error', parse_error='bad formula')
    else:
        rows[0]['ast_schema_version'] = 'alien'
    with file.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with model.load(tmp_path, 'book', storage='memory') as memory, model.load(tmp_path, 'book', storage='disk') as disk:
        assert signature(disk) == signature(memory)


def test_auto_selection_and_readonly_disk_records(tmp_path, monkeypatch):
    fixture(tmp_path)
    monkeypatch.setattr(model, 'DISK_THRESHOLD_BYTES', 0)
    with model.load(tmp_path, 'book') as graph:
        assert graph._store is not None
        node = next(iter(graph.nodes.values()))
        with pytest.raises(TypeError, match='read-only'):
            node.value = '999'
        assert graph.nodes[node.id].value != '999'


def test_failed_import_removes_disposable_database(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'edges.csv').unlink()
    storage = tmp_path / 'scratch'
    storage.mkdir()
    with pytest.raises(FileNotFoundError):
        model.load(tmp_path, 'book', storage='disk', storage_dir=storage)
    assert list(storage.iterdir()) == []


def test_cache_stays_bounded(tmp_path, monkeypatch):
    from xl_seg import disk_model
    fixture(tmp_path)
    monkeypatch.setattr(disk_model, 'CACHE_ROWS', 3)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        for key in graph.nodes:
            graph.nodes[key]
            assert len(graph._store.cache) <= 3
        assert len(list(graph.edges[:2])) == 2
        assert asdict(graph.edges[-1]) == asdict(list(graph.edges)[-1])


def test_hot_records_decode_once_and_remain_immutable(tmp_path, monkeypatch):
    from xl_seg import disk_model
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        graph._store.cache.clear()
        decode = graph._store._record
        decoded = []
        def count(cls, names, payload):
            decoded.append(cls)
            return decode(cls, names, payload)
        monkeypatch.setattr(graph._store, '_record', count)
        key = next(iter(graph.nodes))
        node = graph.nodes[key]
        edge = graph.edges[0]
        for _ in range(100):
            assert asdict(graph.nodes[key]) == asdict(node)
            assert asdict(graph.edges[0]) == asdict(edge)
        assert len(decoded) == 2
        with pytest.raises(TypeError, match='read-only'):
            graph.nodes[key].value = 'changed'
        with pytest.raises(TypeError, match='read-only'):
            graph.edges[0].source = 'changed'
        graph.close()
        assert not graph._store.cache
        with pytest.raises(ValueError, match='closed'):
            graph.nodes[key]


def test_node_edge_cache_shares_bound_and_eviction_preserves_values(tmp_path, monkeypatch):
    from xl_seg import disk_model
    fixture(tmp_path)
    monkeypatch.setattr(disk_model, 'CACHE_ROWS', 2)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        key = next(iter(graph.nodes))
        expected = asdict(graph.nodes[key])
        for edge in graph.edges:
            assert len(graph._store.cache) <= 2
        assert asdict(graph.nodes[key]) == expected
        assert len(graph._store.cache) <= 2


def test_segment_releases_disk_index_after_interrupted_projection(tmp_path, monkeypatch):
    import xl_segment
    from types import SimpleNamespace
    fixture(tmp_path)
    monkeypatch.setattr(xl_segment, '_pinned_source_generation', lambda *a: {
        'ast_dir': tmp_path, 'source': tmp_path / 'book.xlsx'})
    created = []
    original = model.load
    def load(*args, **kwargs):
        graph = original(*args, **kwargs)
        created.append(graph._store.path)
        return graph
    monkeypatch.setattr(model, 'load', load)
    def interrupt(graph):
        raise KeyboardInterrupt('interrupted projection')
    monkeypatch.setattr(project, 'build', interrupt)
    args = SimpleNamespace(out=tmp_path / 'seg', graph_storage='disk')
    with pytest.raises(KeyboardInterrupt):
        xl_segment.segment('book', args)
    assert created and all(not path.parent.exists() for path in created)


def test_sealed_graph_keeps_one_read_snapshot_and_releases_it(tmp_path):
    import sqlite3
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        connection = graph._store.connection
        assert connection.in_transaction
        expected = signature(graph)
        for _ in range(3):
            assert signature(graph) == expected
            assert connection.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute("UPDATE nodes SET value='999'")
        assert signature(graph) == expected
        database = graph._store.path
    assert not database.exists()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


def test_cached_node_membership_avoids_sql_without_accepting_edge_keys(tmp_path):
    import sqlite3
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        key = next(iter(graph.nodes)); graph.nodes[key]; graph.edges[0]
        queries = []
        graph._store.connection.set_trace_callback(queries.append)
        for _ in range(100):
            assert key in graph.nodes
        assert queries == []
        assert 'definitely-absent' not in graph.nodes
        assert len(queries) == 1
        # The shared bounded cache also stores edge tuples. Such an entry must
        # never become a positive node identity merely because it is cached.
        with pytest.raises(sqlite3.ProgrammingError):
            ('edge', 0) in graph.nodes
    with pytest.raises(ValueError, match='closed'):
        key in graph.nodes


def test_hot_adjacency_has_no_repeated_sql_and_cannot_change_records(tmp_path):
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        key = "1 O'Brien!B1"
        edges = graph.in_edges[key]
        expected = [asdict(e) for e in edges]
        assert len(expected) == 1
        queries = []
        graph._store.connection.set_trace_callback(queries.append)
        for _ in range(100):
            selected = graph.in_edges[key]
            assert len(selected) == 1
            assert asdict(selected[0]) == expected[0]
            assert asdict(selected[-1]) == expected[0]
            assert [asdict(e) for e in selected[:]] == expected
        assert queries == []
        with pytest.raises(TypeError, match='read-only'):
            edges[0].source = 'other'
        with pytest.raises(IndexError):
            edges[1]
        graph.close()
        assert not graph._store.adjacency_cache
        with pytest.raises(ValueError, match='closed'):
            list(edges)
        with pytest.raises(ValueError, match='closed'):
            graph.in_edges[key]


def test_adjacency_eviction_and_large_lists_keep_exact_original_order(tmp_path, monkeypatch):
    from xl_seg import disk_model
    fixture(tmp_path)
    monkeypatch.setattr(disk_model, 'ADJACENCY_CACHE_ENTRIES', 2)
    monkeypatch.setattr(disk_model, 'MAX_CACHED_ADJACENCY', 1)
    with model.load(tmp_path, 'book', storage='memory') as memory, model.load(tmp_path, 'book', storage='disk') as graph:
        large = []
        for mapping, reference in ((graph.in_edges,memory.in_edges), (graph.out_edges,memory.out_edges)):
            for _ in range(2):
                for key, expected in reference.items():
                    actual = mapping[key]
                    assert [asdict(e) for e in actual] == [asdict(e) for e in expected]
                    assert len(graph._store.adjacency_cache) <= 2
                    if len(expected)>1:
                        large.append(key)
                        assert actual._small_rows is None
                for key in ('absent-1','absent-2','absent-3'):
                    assert mapping.get(key, ()) == ()
                    assert len(graph._store.adjacency_cache) <= 2
        assert large
