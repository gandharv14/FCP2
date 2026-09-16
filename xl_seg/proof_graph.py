"""Exact immutable grouped proof relations, including per-row exclusions.

Each shared set is stored once. Equality compares actual sets, never digests;
serialization is a closed, lossless alternative to materialized edge lists.
"""
from collections.abc import Mapping, Set
from heapq import merge
from types import MappingProxyType
from xl_seg.active_graph import Sources
from xl_seg.group_adjacency import Neighbors

SCHEMA = 'runtime-proof-relation/v1'


class Row(Set):
    __slots__ = ('terms', '_ordered', '_size')
    def __init__(self, terms, ordered):
        object.__setattr__(self, 'terms', terms)
        object.__setattr__(self, '_ordered', ordered)
        object.__setattr__(self, '_size', None)
    def __setattr__(self, name, value):
        raise TypeError('proof rows are immutable')
    @classmethod
    def _from_iterable(cls, values):
        return frozenset(values)
    def __iter__(self):
        def stream(group, excluded):
            return (v for v in self._ordered[group] if v not in excluded)
        streams = (stream(group, excluded) for group, excluded in self.terms)
        previous = None
        for value in merge(*streams):
            if value != previous:
                yield value
                previous = value
    def __contains__(self, value):
        return any(value in group and value not in excluded for group, excluded in self.terms)
    def __len__(self):
        if self._size is None:
            size = len(self.terms[0][0]) - len(self.terms[0][1]) if len(self.terms) == 1 else sum(1 for _ in self)
            object.__setattr__(self, '_size', size)
        return self._size
    def __eq__(self, other):
        if isinstance(other, Row) and self.terms == other.terms:
            return True
        if not isinstance(other, Set):
            return NotImplemented
        return len(self) == len(other) and all(v in other for v in self)
    def __hash__(self):
        return 0


class Relation(Mapping):
    __slots__ = ('_rows', '_ordered')
    def __init__(self, rows, ordered):
        self._rows = MappingProxyType(rows)
        self._ordered = MappingProxyType(ordered)
    def __getitem__(self, key):
        return self._rows[key]
    def __iter__(self):
        return iter(self._rows)
    def __len__(self):
        return len(self._rows)
    @property
    def edge_count(self):
        return sum(map(len, self._rows.values()))


class Signature:
    __slots__ = ('rows',)
    def __init__(self, relation):
        object.__setattr__(self, 'rows', tuple((key, relation[key]) for key in sorted(relation) if relation[key]))
    def __setattr__(self, name, value):
        raise TypeError('proof signature is immutable')
    def __eq__(self, other):
        return isinstance(other, Signature) and self.rows == other.rows
    def __len__(self):
        return sum(len(row) for _, row in self.rows)


def freeze(rows, *, keys=None, members=None):
    """Snapshot known immutable groups without expanding their cross products."""
    selected = set(rows) if keys is None else set(keys)
    allowed = None if members is None else frozenset(members)
    groups, filtered, ordered, pool, result = {}, {}, {}, {}, {}
    def chunk(values):
        key = values if isinstance(values, frozenset) else frozenset(values)
        if key not in filtered:
            value = key if allowed is None else key & allowed
            if any(not isinstance(v, str) for v in value):
                raise ValueError('proof relation members must be strings')
            value = groups.setdefault(value, value)
            filtered[key] = value
            ordered.setdefault(value, tuple(sorted(value)))
        return filtered[key]
    def terms(values, excluded=frozenset()):
        if isinstance(values, Row):
            for group, omitted in values.terms:
                yield from atom(group, excluded | omitted)
        elif isinstance(values, Sources):
            for group in values.chunks:
                yield from atom(group, excluded)
        elif isinstance(values, Neighbors):
            omitted = excluded | ({values.key} if values.key is not None else set())
            yield from terms(values.direct, omitted)
            for group in values.groups:
                yield from terms(group, omitted)
        elif isinstance(values, (set, frozenset, list, tuple)):
            yield from atom(values, excluded)
        else:
            raise TypeError('unsupported proof row representation')
    def atom(values, excluded):
        group = chunk(values)
        omitted = frozenset(excluded) & group
        if len(group) > len(omitted):
            yield group, omitted
    for key in sorted(selected):
        if not isinstance(key, str):
            raise ValueError('proof relation keys must be strings')
        value = tuple(dict.fromkeys(terms(rows.get(key, ()))))
        result[key] = pool.setdefault(value, Row(value, ordered))
    return Relation(result, ordered)


def encode(relation):
    groups = set(group for row in relation.values() for group, _ in row.terms)
    groups = sorted(groups, key=lambda group: relation._ordered[group])
    index = {group: i for i, group in enumerate(groups)}
    return dict(schema_version=SCHEMA, groups=[list(relation._ordered[g]) for g in groups],
                rows={key: sorted([[index[group], sorted(omitted)] for group, omitted in row.terms])
                      for key, row in sorted(relation.items())})


def decode(value):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'groups', 'rows'} or value['schema_version'] != SCHEMA:
        raise ValueError('closed grouped proof relation required')
    if not isinstance(value['groups'], list) or not isinstance(value['rows'], dict):
        raise ValueError('invalid grouped proof relation')
    groups, ordered = [], {}
    for cells in value['groups']:
        if not isinstance(cells, list) or not cells or any(not isinstance(x, str) for x in cells) or cells != sorted(set(cells)):
            raise ValueError('canonical nonempty proof group required')
        group = frozenset(cells)
        if group in ordered:
            raise ValueError('duplicate proof group')
        groups.append(group);ordered[group] = tuple(cells)
    if value['groups'] != sorted(value['groups']):
        raise ValueError('proof groups reordered')
    rows, pool, used = {}, {}, set()
    for key, records in value['rows'].items():
        if not isinstance(key, str) or not isinstance(records, list):
            raise ValueError('invalid grouped proof row')
        terms = []
        for record in records:
            if not isinstance(record, list) or len(record) != 2:
                raise ValueError('invalid proof group reference')
            index, omitted = record
            if type(index) is not int or not 0 <= index < len(groups):
                raise ValueError('unknown proof group')
            group = groups[index]
            if (not isinstance(omitted, list) or any(not isinstance(x, str) for x in omitted)
                    or omitted != sorted(set(omitted)) or not set(omitted) < group):
                raise ValueError('invalid proof exclusion')
            term = group, frozenset(omitted)
            if term in terms:
                raise ValueError('duplicate proof term')
            terms.append(term);used.add(index)
        if records != sorted(records):
            raise ValueError('proof terms reordered')
        terms = tuple(terms);rows[key] = pool.setdefault(terms, Row(terms, ordered))
    if used != set(range(len(groups))):
        raise ValueError('unused proof group')
    return Relation(rows, ordered)


def read(value):
    """Read either the original JSON adjacency or its explicit grouped form."""
    if isinstance(value, Relation):
        return value
    if isinstance(value, dict) and 'schema_version' in value:
        return decode(value)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, list)
            or any(not isinstance(x, str) for x in v) for k, v in value.items()):
        raise ValueError('complete runtime proof relation required')
    return value
