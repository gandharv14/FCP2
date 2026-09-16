"""Unique-root lookup preserves damaged-graph behavior and original values."""
import csv
import pytest
from xl_seg import model
from test_disk_seg_model import fixture


@pytest.mark.parametrize('mutation', ['none', 'duplicate_root', 'missing_source', 'two_roots'])
def test_unique_producer_matches_memory_and_original_edge_reader(tmp_path, mutation):
    fixture(tmp_path)
    path = tmp_path / 'edges.csv'
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream); fields = reader.fieldnames; rows = list(reader)
    target = next(r for r in rows if r['target'].endswith('!B1'))
    if mutation == 'duplicate_root': rows.append(dict(target))
    if mutation == 'missing_source': rows.append(dict(target, source='missing-source'))
    if mutation == 'two_roots':
        other = next(r for r in rows if r['target'].endswith('!C1'))
        rows.append(dict(target, source=other['source']))
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    with model.load(tmp_path, 'book', storage='memory') as memory, model.load(tmp_path, 'book', storage='disk') as disk:
        for key in [*memory.nodes, 'absent']:
            edges = disk.in_edges.get(key, ())
            original = edges[0].source if len(edges) == 1 else None
            assert disk.root_of(key) == memory.root_of(key) == original
        assert disk.integrity_errors == memory.integrity_errors
    with pytest.raises(ValueError, match='closed'): disk.root_of(target['target'])


def test_direct_root_read_is_one_bounded_query_without_edge_decode(tmp_path, monkeypatch):
    fixture(tmp_path)
    with model.load(tmp_path, 'book', storage='disk') as graph:
        cell = next(n.id for n in graph.iter_nodes('formula'))
        expected = graph.in_edges[cell][0].source
        def forbidden(*args): raise AssertionError('root lookup decoded an edge payload')
        monkeypatch.setattr(graph._store, 'edge', forbidden)
        statements = []; graph._store.connection.set_trace_callback(statements.append)
        assert graph.root_of(cell) == expected
        assert len(statements) == 1 and 'LIMIT 2' in statements[0]
