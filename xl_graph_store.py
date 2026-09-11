"""Temporary, bounded-memory AST graph storage with exact ordered CSV views.

SQLite owns identities, payloads and deduplication. SCC work arrays are mmap
backed; no Python dictionary/set/list is allocated per graph node or edge.
This store is disposable construction state, never verification authority.
"""
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping, Sequence, Set
import json
import mmap
import os
from pathlib import Path
import sqlite3
import tempfile
import zlib

AUTO_FORMULA_THRESHOLD = 100_000
STORAGE_POLICY_VERSION = 'ast-disk-storage/v1'
CACHE_RECORDS = 4096


def _encode(value):
    return zlib.compress(json.dumps(value,separators=(',',':'),allow_nan=False).encode(),1)


def _decode(value):
    return json.loads(zlib.decompress(value))


class _Array:
    def __init__(self,path,length,code):
        self.path=path;self.file=path.open('x+b');self.view=None;self.mapping=None
        try:
            width={'B':1,'I':4,'Q':8,'q':8}[code]
            self.file.truncate(max(1,length*width))
            self.mapping=mmap.mmap(self.file.fileno(),0)
            self.view=memoryview(self.mapping)[:length*width].cast(code)
        except BaseException:
            self.close()
            raise
    def __getitem__(self,index):return self.view[index]
    def __setitem__(self,index,value):self.view[index]=value
    def __len__(self):return len(self.view)
    def close(self):
        if self.view is not None:self.view.release();self.view=None
        if self.mapping is not None:self.mapping.close();self.mapping=None
        self.file.close()


class _Record(MutableMapping):
    __slots__=('store','kind','ordinal')
    def __init__(self,store,kind,ordinal):self.store,self.kind,self.ordinal=store,kind,ordinal
    def _value(self):return self.store._get_record(self.kind,self.ordinal)
    def __getitem__(self,key):return self._value()[key]
    def __iter__(self):return iter(self._value())
    def __len__(self):return len(self._value())
    def __setitem__(self,key,value):self.update({key:value})
    def __delitem__(self,key):
        value=dict(self._value());del value[key];self.store._put_record(self.kind,self.ordinal,value)
    def update(self,*args,**kwargs):
        value=dict(self._value());value.update(*args,**kwargs)
        self.store._put_record(self.kind,self.ordinal,value)
    def __repr__(self):return repr(dict(self))


class NodeMap(MutableMapping):
    def __init__(self,store):self.store=store
    def __len__(self):return self.store.node_count
    def __iter__(self):
        for row in self.store.db.execute('SELECT id FROM nodes ORDER BY ordinal'):yield row[0]
    def __getitem__(self,key):return _Record(self.store,'node',self.store.node_ordinal(key))
    def __setitem__(self,key,value):
        value=dict(value)
        if value.get('id')!=key:raise ValueError('node identity differs')
        try:ordinal=self.store.node_ordinal(key)
        except KeyError:
            ordinal=self.store.node_count
            self.store.db.execute('INSERT INTO nodes(ordinal,id,payload) VALUES(?,?,?)',(ordinal,key,b''))
            self.store.node_count+=1;self.store._remember_id(key,ordinal)
            self.store.dirty.add(('node',ordinal));self.store._remember(('node',ordinal),value);self.store._written()
        else:self.store._put_record('node',ordinal,value)
    def __delitem__(self,key):raise TypeError('construction node identities cannot be deleted')
    def __contains__(self,key):
        try:self.store.node_ordinal(key);return True
        except KeyError:return False
    def iter_records(self):
        self.store.flush()
        for ordinal,payload in self.store.db.execute('SELECT ordinal,payload FROM nodes ORDER BY ordinal'):
            yield self.store._decorate_node(ordinal,_decode(payload))
    def values(self):
        # A consumer may update a later row during iteration. Never replace its
        # current cache/database value with a payload prefetched by this cursor.
        for row in self.store.db.execute('SELECT ordinal FROM nodes ORDER BY ordinal'):
            yield _Record(self.store,'node',row[0])
    def items(self):
        for ordinal,node_id in self.store.db.execute('SELECT ordinal,id FROM nodes ORDER BY ordinal'):
            yield node_id,_Record(self.store,'node',ordinal)


class EdgeSequence(Sequence):
    def __init__(self,store):self.store=store
    def __len__(self):return self.store.edge_count
    def __getitem__(self,index):
        if isinstance(index,slice):return [self[i] for i in range(*index.indices(len(self))) ]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        return _Record(self.store,'edge',index)
    def iter_records(self):
        query=('SELECT e.ordinal,e.payload,s.id,t.id,e.source,e.target FROM edges e '
               'JOIN nodes s ON s.ordinal=e.source JOIN nodes t ON t.ordinal=e.target ORDER BY e.ordinal')
        for ordinal,payload,source,target,si,ti in self.store.db.execute(query):
            value=_decode(payload);value={'source':source,'target':target,**value}
            yield self.store._decorate_edge(si,ti,value)
    def __iter__(self):
        for row in self.store.db.execute('SELECT ordinal FROM edges ORDER BY ordinal'):
            yield _Record(self.store,'edge',row[0])
    def append(self,value):self.store.add_edge(dict(value))


class _Component(Sequence):
    def __init__(self,store,component,size):self.store,self.component,self.size=store,component,size
    def __len__(self):return self.size
    def __iter__(self):
        for row in self.store.db.execute('SELECT n.id FROM cycle_members m JOIN nodes n ON n.ordinal=m.node '
                                         'WHERE m.component=? ORDER BY m.position',(self.component,)):yield row[0]
    def __getitem__(self,index):
        if isinstance(index,slice):return list(self)[index]
        if index<0:index+=self.size
        if not 0<=index<self.size:raise IndexError(index)
        row=self.store.db.execute('SELECT n.id FROM cycle_members m JOIN nodes n ON n.ordinal=m.node '
                                 'WHERE m.component=? AND m.position=?',(self.component,index)).fetchone()
        return row[0]


class CycleSequence(Sequence):
    def __init__(self,store):self.store=store
    def __len__(self):return self.store.cycle_count
    def __iter__(self):
        for component,size in self.store.db.execute('SELECT component,size FROM cycles ORDER BY component'):
            yield _Component(self.store,component,size)
    def __getitem__(self,index):
        if isinstance(index,slice):return list(self)[index]
        if index<0:index+=len(self)
        row=self.store.db.execute('SELECT size FROM cycles WHERE component=?',(index,)).fetchone()
        if row is None:raise IndexError(index)
        return _Component(self.store,index,row[0])


class CyclicNodes(Set):
    def __init__(self,store):self.store=store
    def __len__(self):return self.store.cyclic_count
    def __contains__(self,key):
        try:ordinal=self.store.node_ordinal(key)
        except KeyError:return False
        return bool(self.store.cyclic[ordinal])
    def __iter__(self):
        for row in self.store.db.execute('SELECT n.id FROM cycle_members m JOIN nodes n ON n.ordinal=m.node '
                                         'ORDER BY m.component,m.position'):yield row[0]


class GraphStore:
    def __init__(self,directory=None,*,cache_records=CACHE_RECORDS):
        if type(cache_records) is not int or cache_records<1:raise ValueError('positive cache capacity required')
        self.temporary=tempfile.TemporaryDirectory(prefix='fcp-ast-',dir=directory)
        self.root=Path(self.temporary.name);self.db=None;self.arrays=[];self.closed=False
        self.node_count=self.edge_count=self.cycle_count=self.cyclic_count=0
        self.cache_records=cache_records;self.cache=OrderedDict();self.ids=OrderedDict();self.dirty=set();self.writes=0
        self.indegree=self.outdegree=self.cyclic=None
        try:
            self.db=sqlite3.connect(self.root/'graph.sqlite')
            self.db.executescript('''PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
                PRAGMA temp_store=FILE; PRAGMA cache_size=-16384; PRAGMA mmap_size=0;
                CREATE TABLE nodes(ordinal INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,payload BLOB NOT NULL);
                CREATE TABLE edges(ordinal INTEGER PRIMARY KEY,source INTEGER NOT NULL,target INTEGER NOT NULL,
                    role TEXT NOT NULL,arg_index INTEGER NOT NULL,ref TEXT NOT NULL,cell INTEGER,payload BLOB NOT NULL,
                    UNIQUE(source,target,role,arg_index,ref));
                CREATE TABLE cycles(component INTEGER PRIMARY KEY,size INTEGER NOT NULL);
                CREATE TABLE cycle_members(component INTEGER NOT NULL,position INTEGER NOT NULL,node INTEGER NOT NULL,
                    PRIMARY KEY(component,position));''')
        except BaseException:self.close();raise
        self.nodes=NodeMap(self);self.edges=EdgeSequence(self)
    def _check(self):
        if self.closed:raise ValueError('graph store is closed')
    def _remember(self,key,value):
        self.cache[key]=value;self.cache.move_to_end(key)
        while len(self.cache)>self.cache_records:
            oldest=next(iter(self.cache));self._flush_record(oldest,self.cache[oldest]);self.cache.pop(oldest)
        return value
    def _flush_record(self,key,value):
        if key in self.dirty:
            self.db.execute('UPDATE nodes SET payload=? WHERE ordinal=?',(_encode(value),key[1]))
            self.dirty.remove(key)
    def flush(self):
        self._check()
        for key in tuple(self.dirty):self._flush_record(key,self.cache[key])
        self.db.commit()
    def _remember_id(self,key,value):
        self.ids[key]=value;self.ids.move_to_end(key)
        while len(self.ids)>self.cache_records:self.ids.popitem(last=False)
        return value
    def _written(self):
        self.writes+=1
        if self.writes%10000==0:self.db.commit()
    def node_ordinal(self,key):
        self._check()
        if key in self.ids:self.ids.move_to_end(key);return self.ids[key]
        row=self.db.execute('SELECT ordinal FROM nodes WHERE id=?',(key,)).fetchone()
        if row is None:raise KeyError(key)
        return self._remember_id(key,row[0])
    def _decorate_node(self,ordinal,value):
        if self.cyclic is not None:
            value.update(in_degree=self.indegree[ordinal],out_degree=self.outdegree[ordinal],
                         in_cycle=bool(self.cyclic[ordinal]))
        return value
    def _decorate_edge(self,source,target,value):
        if self.cyclic is not None:value['in_cycle']=bool(self.cyclic[source] and self.cyclic[target])
        return value
    def _get_record(self,kind,ordinal):
        self._check();key=(kind,ordinal)
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        if kind=='node':
            row=self.db.execute('SELECT payload FROM nodes WHERE ordinal=?',(ordinal,)).fetchone()
            if row is None:raise KeyError(ordinal)
            value=self._decorate_node(ordinal,_decode(row[0]))
        else:
            row=self.db.execute('SELECT e.payload,s.id,t.id,e.source,e.target FROM edges e JOIN nodes s ON s.ordinal=e.source '
                                'JOIN nodes t ON t.ordinal=e.target WHERE e.ordinal=?',(ordinal,)).fetchone()
            if row is None:raise IndexError(ordinal)
            value=self._decorate_edge(row[3],row[4],{'source':row[1],'target':row[2],**_decode(row[0])})
        return self._remember(key,value)
    def _put_record(self,kind,ordinal,value):
        self._check()
        if kind=='node':
            old=self._get_record('node',ordinal)
            if old.get('id')!=value.get('id'):raise ValueError('node identity cannot change')
            if self.cyclic is not None:
                self.indegree[ordinal]=value['in_degree'];self.outdegree[ordinal]=value['out_degree'];self.cyclic[ordinal]=bool(value['in_cycle'])
            self.dirty.add((kind,ordinal))
        else:
            source=self.node_ordinal(value['source']);target=self.node_ordinal(value['target'])
            payload={k:v for k,v in value.items() if k not in ('source','target')}
            self.db.execute('UPDATE edges SET source=?,target=?,role=?,arg_index=?,ref=?,cell=?,payload=? WHERE ordinal=?',
                            (source,target,value['role'],value['arg_index'],value['ref'],
                             self.node_ordinal(value['cell']) if value['cell'] else None,_encode(payload),ordinal))
        self._remember((kind,ordinal),value);self._written()
    def add_edge(self,value):
        self._check()
        if self.cyclic is not None:raise ValueError('cannot extend postprocessed disk graph')
        source=self.node_ordinal(value['source']);target=self.node_ordinal(value['target'])
        payload={k:v for k,v in value.items() if k not in ('source','target')}
        cursor=self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source,target,role,arg_index,ref) DO NOTHING',
                              (self.edge_count,source,target,value['role'],value['arg_index'],value['ref'],
                               self.node_ordinal(value['cell']) if value['cell'] else None,_encode(payload)))
        if cursor.rowcount:
            self._remember(('edge',self.edge_count),value);self.edge_count+=1;self._written()
    def array(self,name,length,code):
        value=_Array(self.root/name,length,code);self.arrays.append(value);return value
    def postprocess(self):
        """Insertion-ordered iterative Tarjan with file-backed integer state."""
        self._check();self.flush();n=self.node_count;m=self.edge_count
        if n>=2**32:raise ValueError('graph exceeds exact uint32 node addressing')
        if self.cyclic is not None:raise ValueError('graph already postprocessed')
        self.indegree=self.array('in-degree',n,'Q');self.outdegree=self.array('out-degree',n,'Q')
        self.cyclic=self.array('cyclic',n,'B');self.cache.clear()
        temporary=[]
        def arr(name,length,code):
            a=self.array(name,length,code);temporary.append(a);return a
        try:
            for source,target in self.db.execute('SELECT source,target FROM edges ORDER BY ordinal'):
                self.outdegree[source]+=1;self.indegree[target]+=1
            offsets=arr('offsets',n+1,'Q');positions=arr('positions',n,'Q')
            for node in range(n):offsets[node+1]=offsets[node]+self.outdegree[node];positions[node]=offsets[node]
            successors=arr('successors',m,'I')
            for source,target in self.db.execute('SELECT source,target FROM edges ORDER BY ordinal'):
                position=positions[source];successors[position]=target;positions[source]=position+1
            index=arr('index',n,'Q');low=arr('low',n,'Q');onstack=arr('on-stack',n,'B')
            stack=arr('stack',n,'I');work=arr('work',n,'I');work_pos=arr('work-pos',n,'Q')
            counter=0;stack_len=0
            for root in range(n):
                if index[root]:continue
                work_len=1;work[0]=root;work_pos[0]=offsets[root]
                while work_len:
                    depth=work_len-1;node=work[depth];position=work_pos[depth]
                    if not index[node]:
                        counter+=1;index[node]=low[node]=counter
                        stack[stack_len]=node;stack_len+=1;onstack[node]=1
                    recursed=False
                    while position<offsets[node+1]:
                        nxt=successors[position];position+=1;work_pos[depth]=position
                        if not index[nxt]:
                            work[work_len]=nxt;work_pos[work_len]=offsets[nxt];work_len+=1
                            recursed=True;break
                        if onstack[nxt]:low[node]=min(low[node],index[nxt])
                    if recursed:continue
                    if low[node]==index[node]:
                        start=stack_len-1
                        while stack[start]!=node:start-=1
                        size=stack_len-start
                        self_loop=(size==1 and any(successors[i]==node for i in range(offsets[node],offsets[node+1])))
                        if size>1 or self_loop:
                            component=self.cycle_count;self.cycle_count+=1;self.cyclic_count+=size
                            self.db.execute('INSERT INTO cycles VALUES(?,?)',(component,size))
                            for position_in_component,i in enumerate(range(stack_len-1,start-1,-1)):
                                member=stack[i];self.cyclic[member]=1
                                self.db.execute('INSERT INTO cycle_members VALUES(?,?,?)',(component,position_in_component,member))
                        for i in range(start,stack_len):onstack[stack[i]]=0
                        stack_len=start
                    work_len-=1
                    if work_len:
                        parent=work[work_len-1];low[parent]=min(low[parent],low[node])
            self.db.commit();self.cache.clear()
            return CycleSequence(self),CyclicNodes(self)
        finally:
            for a in temporary:
                a.close();self.arrays.remove(a);a.path.unlink(missing_ok=True)
    def default_focus(self):
        row=self.db.execute('SELECT n.id,COUNT(*) AS amount FROM edges e JOIN nodes n ON n.ordinal=e.cell '
                            'GROUP BY e.cell ORDER BY amount DESC,n.id ASC LIMIT 1').fetchone()
        if row is not None:return row[0]
        for node_id,node in self.nodes.items():
            if node.get('kind')=='formula':return node_id
        return next(iter(self.nodes),'')
    def close(self):
        if self.closed:return
        self.closed=True;self.cache.clear();self.ids.clear();self.dirty.clear()
        for a in self.arrays:a.close()
        self.arrays.clear()
        if self.db is not None:self.db.close();self.db=None
        self.temporary.cleanup()
    def __enter__(self):self._check();return self
    def __exit__(self,*args):self.close()
