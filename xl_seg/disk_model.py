"""Disposable SQLite graph indexes with bounded object caches.

CSV remains the source of truth. No database is reused between loads or treated
as verification evidence. Insertion order, duplicate identity diagnostics, typed
values, endpoints and formula-root counts match the in-memory loader exactly.
"""
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import fields
import json
from pathlib import Path
import sqlite3
import tempfile
import weakref

CACHE_ROWS = 4096
ADJACENCY_CACHE_ENTRIES = 2048
MAX_CACHED_ADJACENCY = 32


def _cleanup(connection, temporary):
    connection.close()
    temporary.cleanup()


def _readonly_type(base):
    class Stored(base):
        __slots__ = ()
        def __setattr__(self, name, value):
            raise TypeError('disk-backed graph records are read-only; rebuild CSV evidence to change them')
        def __delattr__(self, name):
            raise TypeError('disk-backed graph records are read-only')
    Stored.__name__ = 'Stored' + base.__name__
    return Stored


class Store:
    def __init__(self, directory=None):
        from .model import Node, Edge
        self.temporary = tempfile.TemporaryDirectory(prefix='fcp-seg-graph-', dir=directory)
        self.path = Path(self.temporary.name) / 'graph.sqlite'
        self.connection = sqlite3.connect(self.path)
        self._finalizer = weakref.finalize(self, _cleanup, self.connection, self.temporary)
        self.cache = OrderedDict()
        self.adjacency_cache = OrderedDict()
        self.node_fields = tuple(f.name for f in fields(Node))
        self.edge_fields = tuple(f.name for f in fields(Edge))
        self.node_type, self.edge_type = _readonly_type(Node), _readonly_type(Edge)
        self.connection.executescript('''
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=FILE;
            PRAGMA cache_size=-32768;
            CREATE TABLE nodes(n INTEGER PRIMARY KEY,id TEXT UNIQUE,kind TEXT,owner TEXT,value TEXT,
                               parse_status TEXT,parse_error TEXT,schema_version TEXT,payload TEXT);
            CREATE TABLE edges(n INTEGER PRIMARY KEY,source TEXT,target TEXT,payload TEXT,valid INTEGER DEFAULT 0);
        ''')
        self.node_count = self.edge_count = 0

    def require_open(self):
        if not self._finalizer.alive:
            raise ValueError('disk-backed graph is closed')

    def close(self):
        self.cache.clear()
        self.adjacency_cache.clear()
        self._finalizer()

    def add_node(self, ordinal, node):
        values = [getattr(node, name) for name in self.node_fields]
        cursor = self.connection.execute('INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?,?,?,?,?)',
            (ordinal,node.id,node.kind,node.owner,node.value,node.parse_status,node.parse_error,
             node.ast_schema_version,json.dumps(values,ensure_ascii=False,separators=(',',':'))))
        self.node_count += cursor.rowcount
        return cursor.rowcount == 1

    def add_edges(self, rows):
        self.connection.executemany('INSERT INTO edges(n,source,target,payload) VALUES (?,?,?,?)', rows)
        self.edge_count += len(rows)

    def seal(self):
        self.connection.executescript('''
            CREATE INDEX nodes_kind ON nodes(kind,n);
            CREATE INDEX nodes_owner ON nodes(owner,kind,n);
            CREATE INDEX edges_source ON edges(source,n);
            CREATE INDEX edges_target ON edges(target,n);
            UPDATE edges SET valid=1 WHERE EXISTS(SELECT 1 FROM nodes WHERE id=edges.source)
                AND EXISTS(SELECT 1 FROM nodes WHERE id=edges.target);
        ''')
        self.connection.commit()
        self.connection.execute('PRAGMA query_only=ON')
        # This private disposable index is immutable after sealing. Keep one
        # read transaction until close so hot lookups do not repeatedly acquire
        # filesystem locks and inspect journal files for every graph edge.
        self.connection.execute('BEGIN')

    def rows(self, query, params=()):
        self.require_open()
        cursor = self.connection.execute(query,params)
        try:
            yield from cursor
        finally:
            cursor.close()

    def node(self, payload):
        return self._record(self.node_type, self.node_fields, payload)

    def edge(self, payload, ordinal=None):
        if ordinal is None:
            return self._record(self.edge_type, self.edge_fields, payload)
        key = ('edge', ordinal)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return self.remember(key, self._record(self.edge_type, self.edge_fields, payload))

    def remember(self, key, record):
        # Stored records are immutable. Retain the decoded record rather than
        # decoding its JSON on every hot lookup. Nodes and edges share one cap.
        self.cache[key] = record
        self.cache.move_to_end(key)
        if len(self.cache) > CACHE_ROWS:
            self.cache.popitem(last=False)
        return record

    @staticmethod
    def _record(cls, names, payload):
        value = object.__new__(cls)
        for name, field in zip(names,json.loads(payload),strict=True):
            object.__setattr__(value,name,field)
        return value

    def iter_nodes(self, kinds=()):
        where = ' WHERE kind IN ('+','.join('?' for _ in kinds)+')' if kinds else ''
        for (payload,) in self.rows('SELECT payload FROM nodes'+where+' ORDER BY n',kinds):
            yield self.node(payload)

    def literal_values(self, owner):
        return tuple(row[0] for row in self.rows(
            "SELECT value FROM nodes WHERE owner=? AND kind='const' ORDER BY n",(owner,)))

    def root_of(self, cell_id):
        """Read the unique valid producer without decoding an edge payload.

        Two rows suffice to distinguish a unique root from duplicate/multiple
        edges. The sealed index and original integrity diagnostics remain the
        authority; this does not cache calculated values or repair bad graphs.
        """
        self.require_open()
        rows = self.connection.execute(
            'SELECT source FROM edges WHERE target=? AND valid=1 LIMIT 2',
            (cell_id,)).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def integrity(self):
        for n, source, target, has_source, has_target in self.rows('''
            SELECT e.n,e.source,e.target,s.id IS NOT NULL,t.id IS NOT NULL FROM edges e
            LEFT JOIN nodes s ON s.id=e.source LEFT JOIN nodes t ON t.id=e.target
            WHERE e.valid=0 ORDER BY e.n'''):
            yield {'code':'missing_edge_endpoint','edge_index':n,
                   'missing':[p for p,yes in ((source,has_source),(target,has_target)) if not yes]}
        for node_id,kind,owner,status,error,owner_kind in self.rows('''
            SELECT n.id,n.kind,n.owner,n.parse_status,n.parse_error,o.kind FROM nodes n
            LEFT JOIN nodes o ON o.id=n.owner
            WHERE n.kind IN ('op','const','range') OR (n.kind='formula' AND n.parse_status='error')
            ORDER BY n.n'''):
            if kind in ('op','const','range') and (kind in ('op','const') or owner) and owner_kind!='formula':
                yield {'code':'invalid_ast_owner','node':node_id,'owner':owner}
            if kind=='formula' and status=='error':
                yield {'code':'formula_parse_error','node':node_id,'detail':error[:240]}
        for node_id,count in self.rows('''
            SELECT n.id,COUNT(e.n) FROM nodes n LEFT JOIN edges e ON e.target=n.id AND e.valid=1
            WHERE n.kind='formula' AND n.parse_status='ok' GROUP BY n.n HAVING COUNT(e.n)!=1 ORDER BY n.n'''):
            yield {'code':'invalid_formula_root_count','node':node_id,'count':count}

    def cell_edges(self):
        """Project non-range dependencies without materializing internal edges.

        Shared range sources are returned as their original node IDs so the
        existing projection's exact range-member semantics stay in charge.
        """
        for source,target,kind in self.rows('''
            SELECT CASE WHEN s.kind='range' THEN s.id
                        WHEN s.kind IN ('op','const') AND so.kind IN ('formula','input','label') THEN so.id
                        WHEN s.kind IN ('formula','input','label') THEN s.id END AS src,
                   CASE WHEN t.kind IN ('op','const','range') THEN o.id
                        WHEN t.kind IN ('formula','input','label') THEN t.id END AS dst,
                   s.kind FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target
                   LEFT JOIN nodes so ON so.id=s.owner
                   LEFT JOIN nodes o ON o.id=t.owner AND o.kind IN ('formula','input','label')
            WHERE src IS NOT NULL AND dst IS NOT NULL AND (s.kind='range' OR src!=dst)
            ORDER BY e.n'''):
            yield source,target,kind=='range'


class Nodes(Mapping):
    def __init__(self,store): self.store=store
    def __len__(self): self.store.require_open();return self.store.node_count
    def __iter__(self):
        return (r[0] for r in self.store.rows('SELECT id FROM nodes ORDER BY n'))
    def __contains__(self,key):
        self.store.require_open()
        if isinstance(key, str) and key in self.store.cache:
            return True
        return self.store.connection.execute('SELECT 1 FROM nodes WHERE id=?',(key,)).fetchone() is not None
    def __getitem__(self,key):
        self.store.require_open()
        if key in self.store.cache:
            record=self.store.cache[key];self.store.cache.move_to_end(key)
        else:
            row=self.store.connection.execute('SELECT payload FROM nodes WHERE id=?',(key,)).fetchone()
            if row is None:raise KeyError(key)
            record=self.store.remember(key,self.store.node(row[0]))
        return record
    def values(self):return self.store.iter_nodes()
    def items(self):return ((n.id,n) for n in self.values())


class Edges(Sequence):
    def __init__(self,store,column=None,node_id=None):
        self.store=store;self.column=column;self.node_id=node_id
        self.where='' if column is None else ' WHERE '+column+'=? AND valid=1'
        self.params=() if column is None else (node_id,)
        self._length = None
        self._small_rows = None
    def __len__(self):
        self.store.require_open()
        if self.column is None:return self.store.edge_count
        if self._length is None:
            self._length = self.store.connection.execute('SELECT COUNT(*) FROM edges'+self.where,self.params).fetchone()[0]
        return self._length
    def _small(self):
        # Only the sealed graph's immutable adjacency is cached. Formula values
        # and runtime proof dependencies still execute their original readers.
        self.store.require_open()
        if self.column is None or len(self) > MAX_CACHED_ADJACENCY:return None
        if self._small_rows is None:
            self._small_rows = tuple(self.store.edge(payload,n) for n,payload in self.store.rows(
                'SELECT n,payload FROM edges'+self.where+' ORDER BY n',self.params))
        return self._small_rows
    def __iter__(self):
        rows = self._small()
        if rows is not None:return iter(rows)
        return (self.store.edge(payload,n) for n,payload in self.store.rows('SELECT n,payload FROM edges'+self.where+' ORDER BY n',self.params))
    def __getitem__(self,index):
        if isinstance(index,slice):return list(self)[index]
        if not isinstance(index,int):raise TypeError('integer edge index required')
        if index<0:index+=len(self)
        if index<0:raise IndexError(index)
        self.store.require_open()
        rows = self._small()
        if rows is not None:return rows[index]
        row=self.store.connection.execute('SELECT n,payload FROM edges'+self.where+' ORDER BY n LIMIT 1 OFFSET ?',(*self.params,index)).fetchone()
        if row is None:raise IndexError(index)
        return self.store.edge(row[1],row[0])


class Adjacency(Mapping):
    def __init__(self,store,column):self.store=store;self.column=column
    def __iter__(self):
        return (r[0] for r in self.store.rows('SELECT '+self.column+' FROM edges WHERE valid=1 GROUP BY '+self.column+' ORDER BY MIN(n)'))
    def __len__(self):
        self.store.require_open()
        return self.store.connection.execute('SELECT COUNT(DISTINCT '+self.column+') FROM edges WHERE valid=1').fetchone()[0]
    def __getitem__(self,key):
        self.store.require_open()
        cache = self.store.adjacency_cache
        identity = (self.column,key)
        if isinstance(key,str) and identity in cache:
            result=cache[identity];cache.move_to_end(identity)
        else:
            result=Edges(self.store,self.column,key)
            # Invalid key types retain SQLite's original error behavior.
            len(result)
            if isinstance(key,str):
                cache[identity]=result
                if len(cache)>ADJACENCY_CACHE_ENTRIES:cache.popitem(last=False)
        if not len(result):raise KeyError(key)
        return result


def load(ast_dir,wb,storage_dir=None):
    import csv
    from .model import Graph,_parse_node,_parse_edge,_capabilities
    store=Store(storage_dir)
    try:
        errors=[];versions=set()
        with (ast_dir/'nodes.csv').open(newline='',encoding='utf-8') as stream:
            reader=csv.DictReader(stream);node_fields=set(reader.fieldnames or ())
            for n,row in enumerate(reader):
                node=_parse_node(row)
                if not store.add_node(n,node):
                    errors.append({'code':'duplicate_node_id','node':node.id,'node_index':n})
                elif node.ast_schema_version:versions.add(node.ast_schema_version)
        with (ast_dir/'edges.csv').open(newline='',encoding='utf-8') as stream:
            reader=csv.DictReader(stream);edge_fields=set(reader.fieldnames or ());batch=[]
            for n,row in enumerate(reader):
                edge=_parse_edge(row)
                batch.append((n,edge.source,edge.target,json.dumps([getattr(edge,k) for k in store.edge_fields],ensure_ascii=False,separators=(',',':'))))
                if len(batch)>=4096:store.add_edges(batch);batch=[]
            if batch:store.add_edges(batch)
        store.seal();errors.extend(store.integrity())
        return Graph(wb,Nodes(store),Edges(store),Adjacency(store,'target'),Adjacency(store,'source'),
                     _capabilities(node_fields,edge_fields,versions),errors,
                     next(iter(versions)) if len(versions)==1 else '',_store=store)
    except BaseException:
        store.close();raise
