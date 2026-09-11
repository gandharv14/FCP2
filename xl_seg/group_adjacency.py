"""Exact cell adjacency with shared range products kept as immutable groups.

A range feeding many formulas stores its members and destinations once. Public
mapping values have read-only Set semantics; enumeration materializes at most
one cell's unique neighborhood, never the whole cartesian dependency graph.
"""
from collections.abc import Mapping, Set
from types import MappingProxyType


class Neighbors(Set):
    __slots__ = ('key', 'direct', 'groups')

    def __init__(self, key, direct, groups):
        self.key, self.direct, self.groups = key, direct, groups

    def __contains__(self, value):
        return value != self.key and (value in self.direct or any(value in group for group in self.groups))

    def __iter__(self):
        seen = {self.key}
        for values in (self.direct, *self.groups):
            for value in values:
                if value not in seen:
                    seen.add(value)
                    yield value

    def __len__(self):
        return sum(1 for _ in self)

    @classmethod
    def _from_iterable(cls, values):
        return frozenset(values)


class Adjacency(Mapping):
    __slots__ = ('_keys', '_direct', '_index', '_groups')

    def __init__(self, keys, direct, index, groups):
        self._keys = MappingProxyType(dict.fromkeys(keys))
        self._direct = MappingProxyType({k: frozenset(v) for k, v in direct.items()})
        self._index = MappingProxyType({k: tuple(v) for k, v in index.items()})
        self._groups = groups

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __getitem__(self, key):
        if key not in self._keys:
            raise KeyError(key)
        return Neighbors(key, self._direct.get(key, ()),
                         tuple(self._groups[i] for i in self._index.get(key, ())))


class RangeProducts:
    """Mutable builder, sealed once before it is exposed to graph consumers."""
    def __init__(self):
        self.direct = {}; self.reverse = {}
        self.source_keys = {}; self.target_keys = {}
        self.groups = {}; self.source_groups = {}; self.target_groups = {}
        self._sealed = False

    def add_edge(self, source, target):
        if self._sealed:
            raise TypeError('sealed cell adjacency')
        if source == target:
            return
        self.source_keys.setdefault(source, None); self.target_keys.setdefault(target, None)
        self.direct.setdefault(source, set()).add(target)
        self.reverse.setdefault(target, set()).add(source)

    def add_range(self, identity, members, target):
        if self._sealed:
            raise TypeError('sealed cell adjacency')
        group = self.groups.get(identity)
        if group is None:
            members = tuple(dict.fromkeys(members))
            if not members:
                return
            group = (members, {})
            self.groups[identity] = group
            for source in members:
                self.source_groups.setdefault(source, []).append(identity)
        members, targets = group
        if target in targets:
            return
        # The first destination registers every non-self source in original
        # row-major order. A second distinct destination introduces at most
        # the one source previously excluded by the self-edge rule.
        if not targets:
            for source in members:
                if source != target:
                    self.source_keys.setdefault(source, None)
        elif len(targets) == 1:
            first = next(iter(targets))
            if first in members:
                self.source_keys.setdefault(first, None)
        targets[target] = None
        if len(members) > 1 or members[0] != target:
            self.target_keys.setdefault(target, None)
            self.target_groups.setdefault(target, []).append(identity)

    def seal(self):
        if self._sealed:
            raise TypeError('cell adjacency already sealed')
        self._sealed = True
        members = {k: frozenset(v[0]) for k, v in self.groups.items()}
        targets = {k: frozenset(v[1]) for k, v in self.groups.items()}
        self.adj = Adjacency(self.source_keys, self.direct, self.source_groups, MappingProxyType(targets))
        self.radj = Adjacency(self.target_keys, self.reverse, self.target_groups, MappingProxyType(members))
        self.direct = self.adj._direct
        self.reverse = self.radj._direct
        self.groups = MappingProxyType({k: (v[0], tuple(v[1])) for k, v in self.groups.items()})
        return self.adj, self.radj

    def band_pairs(self, of_cell):
        """Collapse each range product before enumerating its band edges."""
        if not self._sealed:
            raise TypeError('unsealed cell adjacency')
        for source, targets in self.direct.items():
            source_band = of_cell.get(source)
            if source_band is None:
                continue
            for target in targets:
                target_band = of_cell.get(target)
                if target_band is not None and source_band != target_band:
                    yield source_band, target_band
        for members, targets in self.groups.values():
            sources = dict.fromkeys(of_cell[c] for c in members if c in of_cell)
            destinations = dict.fromkeys(of_cell[c] for c in targets if c in of_cell)
            for source in sources:
                for target in destinations:
                    if source != target:
                        yield source, target
