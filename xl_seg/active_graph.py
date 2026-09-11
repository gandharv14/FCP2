"""Exact immutable chunk unions for evaluator dependency graphs.

Unlike static projection, runtime graphs retain self-dependencies and empty
formula target rows. Compression changes storage only, never edge semantics.
"""
from collections.abc import Mapping, Set
from types import MappingProxyType
from heapq import merge

class Sources(Set):
    __slots__ = ('chunks', '_size')
    def __init__(self, chunks=()):
        object.__setattr__(self, 'chunks', tuple(dict.fromkeys(c for c in chunks if c)))
        object.__setattr__(self, '_size', None)
    def __setattr__(self, name, value):
        raise TypeError('active sources are immutable')
    @classmethod
    def _from_iterable(cls, values):
        return frozenset(values)
    def __iter__(self):
        if len(self.chunks) == 1:
            yield from self.chunks[0]
            return
        seen = set()
        for chunk in self.chunks:
            for value in chunk:
                if value not in seen:
                    seen.add(value)
                    yield value
    def __contains__(self, value):
        return any(value in chunk for chunk in self.chunks)
    def __len__(self):
        if self._size is None:
            object.__setattr__(self, '_size', len(self.chunks[0]) if len(self.chunks)==1 else sum(1 for _ in self))
        return self._size
    def __hash__(self):
        # All representations of equal sets must hash alike. Exact equality,
        # not this deliberately coarse hash, authenticates graph signatures.
        return 0
    def __eq__(self, other):
        if isinstance(other, Sources) and self.chunks == other.chunks:
            return True
        if not isinstance(other, Set):
            return NotImplemented
        return len(self)==len(other) and all(value in other for value in self)

class Pool:
    def __init__(self):
        self.chunks = {}
        self.unions = {}
        self.spans = {}
    def chunk(self, values):
        value = values if isinstance(values, frozenset) else frozenset(values)
        return self.chunks.setdefault(value, value)
    def sources(self, values=()):
        if isinstance(values, Sources):
            return values
        return self.union((self.chunk(values),))
    def union(self, chunks):
        key = tuple(dict.fromkeys(c for c in chunks if c))
        return self.unions.setdefault(key, Sources(key))
    def span(self, key, factory):
        if key not in self.spans:
            self.spans[key] = tuple(factory())
        return self.spans[key]

class Builder:
    def __init__(self, pool, values=()):
        self.pool = pool
        self.chunks = []
        self.atoms = set()
        self.update(values)
    def add(self, value):
        self.atoms.add(value)
    def update(self, values):
        if isinstance(values, Sources):
            self.chunks.extend(values.chunks)
        else:
            self.atoms.update(values)
    def freeze(self):
        chunks = list(self.chunks)
        if self.atoms:
            chunks.append(self.pool.chunk(self.atoms))
        return self.pool.union(chunks)
    def __iter__(self):
        return iter(self.freeze())

class Rows(Mapping):
    def __init__(self, rows):
        self._rows = MappingProxyType(dict(rows))
    def __getitem__(self, key):
        return self._rows[key]
    def __iter__(self):
        return iter(self._rows)
    def __len__(self):
        return len(self._rows)

def transpose(rows, pool):
    """Transpose shared member chunks before expanding member/target products."""
    groups = {}
    for target, sources in rows.items():
        for chunk in sources.chunks:
            groups.setdefault(chunk, set()).add(target)
    incidence = {}
    for chunk, targets in groups.items():
        frozen_targets = pool.chunk(targets)
        for source in chunk:
            incidence.setdefault(source, []).append(frozen_targets)
    return Rows({source:pool.union(chunks) for source,chunks in incidence.items()})

class Signature:
    __slots__ = ('rows',)
    def __init__(self, rows):
        object.__setattr__(self, 'rows', tuple((source, sources) for source,sources in sorted(rows.items()) if sources))
    def __setattr__(self, name, value):
        raise TypeError('graph signatures are immutable')
    def __hash__(self):
        return hash(self.rows)
    def __eq__(self, other):
        return isinstance(other, Signature) and self.rows == other.rows
    def __len__(self):
        return sum(len(sources) for _,sources in self.rows)

class OrderedRows(Mapping):
    def __init__(self, rows, key):
        self.rows, self.key, self.sorted_chunks = rows, key, {}
        self.tie_rank = {}
    def __len__(self):
        return len(self.rows)
    def __iter__(self):
        return iter(self.rows)
    def __getitem__(self, source):
        chunks = self.rows[source].chunks
        ordered = []
        for chunk in chunks:
            if chunk not in self.sorted_chunks:
                for value in chunk:
                    if value not in self.tie_rank:
                        self.tie_rank[value] = len(self.tie_rank)
                self.sorted_chunks[chunk] = tuple(sorted(chunk, key=self._order_key))
            ordered.append(self.sorted_chunks[chunk])
        return self._unique(merge(*ordered, key=self._order_key))
    def _order_key(self, value):
        # One total tie order shared by every chunk keeps duplicates adjacent
        # even when the caller's key is not injective.
        return self.key(value), self.tie_rank[value]
    @staticmethod
    def _unique(values):
        missing = object(); previous = missing
        for value in values:
            if previous is missing or value != previous:
                yield value
                previous = value

def components(rows, comp_of, count, pool):
    """Collapse chunk incidence to exact component edges without pair copies."""
    mapped = {}
    outgoing = {i:[] for i in range(count)}
    for source, targets in rows.items():
        source_comp = comp_of.get(source)
        if source_comp is None:
            continue
        for chunk in targets.chunks:
            if chunk not in mapped:
                mapped[chunk] = pool.chunk(comp_of[t] for t in chunk if t in comp_of)
            group = mapped[chunk]
            if source_comp in group:
                group = pool.chunk(group - {source_comp})
            if group:
                outgoing[source_comp].append(group)
    adjacency = Rows({i:pool.union(groups) for i,groups in outgoing.items()})
    reverse = transpose(adjacency, pool)
    return adjacency, {i:len(reverse.get(i, ())) for i in range(count)}

class ReadRelation(Mapping):
    """Append-only read journal with scalar and grouped range registration."""
    def __init__(self, pool):
        self.pool = pool
        self.direct = {}
        self.groups = {}
        self._snapshot = None
    def add_group(self, members, consumer):
        group = self.pool.chunk(members)
        if not group:
            return
        consumers = self.groups.setdefault(group, set())
        if consumer not in consumers:
            consumers.add(consumer)
            self._snapshot = None
    def setdefault(self, source, default=None):
        if source not in self.direct:
            initial = () if default and source in self else (default or ())
            self.direct[source] = set(initial)
            self._snapshot = None
        return _ReadRow(self, source)
    def snapshot(self):
        if self._snapshot is None:
            by_consumer = {}
            for members, consumers in self.groups.items():
                for consumer in consumers:
                    by_consumer.setdefault(consumer, []).append(members)
            for source, consumers in self.direct.items():
                member = self.pool.chunk((source,))
                for consumer in consumers:
                    by_consumer.setdefault(consumer, []).append(member)
            rows = transpose({consumer:self.pool.union(chunks) for consumer,chunks in by_consumer.items()}, self.pool)
            self._snapshot = Rows({**{source:self.pool.sources() for source in self.direct}, **dict(rows.items())})
        return self._snapshot
    def __len__(self):
        return len(self.snapshot())
    def __iter__(self):
        return iter(self.snapshot())
    def __getitem__(self, source):
        return self.snapshot()[source]

class _ReadRow(Set):
    @classmethod
    def _from_iterable(cls, values):
        return frozenset(values)
    def __init__(self, relation, source):
        self.relation, self.source = relation, source
    def add(self, consumer):
        row = self.relation.direct[self.source]
        if consumer not in row:
            row.add(consumer)
            self.relation._snapshot = None
    def update(self, consumers):
        for consumer in consumers:
            self.add(consumer)
    def __iter__(self):
        return iter(self.relation[self.source])
    def __len__(self):
        return len(self.relation[self.source])
    def __contains__(self, consumer):
        return consumer in self.relation[self.source]

MAX_DIAGNOSTIC_EDGES = 4_000_000

class DiagnosticResourceExhaustion(MemoryError):
    reason_code = 'resource_exhaustion'
    def __init__(self, count):
        import json
        self.report = {'reason_code':self.reason_code,'resource':'cycle_endpoint_equation_edges',
                       'observed_edge_count_lower_bound':count,'edge_limit':MAX_DIAGNOSTIC_EDGES,
                       'estimated_allocation_bytes_lower_bound':count*256,'proof_truncated':False,
                       'recovery':'streaming_equation_diagnostic_required'}
        super().__init__(json.dumps(self.report,sort_keys=True))

def endpoint_equations(*rows):
    """Bound all retained endpoint tables before allocating their sorted rows."""
    count = 0
    for graph in rows:
        for sources in graph.values():
            count += len(sources)
            if count > MAX_DIAGNOSTIC_EDGES:
                raise DiagnosticResourceExhaustion(count)
    return tuple({cell:tuple(sorted(sources)) for cell,sources in graph.items()} for graph in rows)
