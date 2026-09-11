import random,tracemalloc
import pytest
from xl_seg.active_graph import Pool,Builder,Rows,transpose,Signature,OrderedRows
from xl_seg.condense import strongly_connected

def test_random_graphs_exact_union_transpose_signature_and_scc():
 for seed in range(30):
  r=random.Random(seed);p=Pool();keys=tuple('n'+str(i) for i in range(18));chunks=[p.sources(r.sample(keys,r.randrange(10))) for _ in range(10)];rows={};old={}
  for target in keys:
   b=Builder(p)
   for chunk in r.sample(chunks,r.randrange(5)):b.update(chunk)
   rows[target]=b.freeze();old[target]=set(rows[target])
  forward=transpose(rows,p);expected={}
  for t,sources in old.items():
   for s in sources:expected.setdefault(s,set()).add(t)
  assert dict(forward)==expected
  assert len(Signature(forward))==sum(map(len,expected.values()))
  plain=Rows({k:p.sources(v) for k,v in expected.items()})
  assert Signature(forward)==Signature(plain) and hash(Signature(forward))==hash(Signature(plain))
  assert strongly_connected(keys,OrderedRows(forward,str))==strongly_connected(keys,{k:tuple(sorted(v)) for k,v in expected.items()})

def test_self_edges_empty_rows_overlaps_and_collisions():
 p=Pool();a=p.union((p.chunk(['x','y']),p.chunk(['y','z'])));b=p.sources(['x','y','z'])
 assert a==b and hash(a)==hash(b)
 assert a!=p.sources(['x','y','q'])
 rows=Rows({'x':p.sources(['x']),'empty':p.sources()});f=transpose(rows,p)
 assert set(f['x'])=={'x'} and list(rows['empty'])==[]
 assert Signature(f)!=Signature(Rows({'x':p.sources(['y'])}))
 with pytest.raises(TypeError):a.chunks=()
 with pytest.raises(TypeError):rows['x']=b

def test_shared_ordered_spans_preserve_blanks_positions_and_are_immutable():
 p=Pool();calls=[]
 def factory():calls.append(1);return ['s!A1','s!A2','s!A3']
 a=p.span(('s','A1:A3'),factory);b=p.span(('s','A1:A3'),factory)
 assert a is b and a==('s!A1','s!A2','s!A3') and calls==[1]
 with pytest.raises(TypeError):a[1]='removed'

def test_110_million_edges_have_bounded_retained_storage():
 p=Pool();members=tuple('s'+str(i) for i in range(9587));targets=tuple('t'+str(i) for i in range(11466));tracemalloc.start()
 shared=p.sources(members);reverse=Rows({t:shared for t in targets});forward=transpose(reverse,p);signature=Signature(forward)
 assert len(signature)==109924542
 ordered=OrderedRows(forward,str);assert list(ordered[members[0]])==sorted(targets)
 assert all(forward[s] is forward[members[0]] for s in members)
 _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();assert peak<20*1024**2

def test_grouped_read_journal_matches_scalar_reads_and_snapshot_is_stable():
 from xl_seg.active_graph import ReadRelation
 p=Pool();journal=ReadRelation(p);journal.add_group(['a','b','a'],'t1');old=journal.snapshot()
 journal.setdefault('a',set()).add('t2');journal.add_group(['a','b'],'t1');journal.add_group(['b','c'],'t3')
 assert dict(journal)=={'a':{'t1','t2'},'b':{'t1','t3'},'c':{'t3'}}
 assert dict(old)=={'a':{'t1'},'b':{'t1'}}
 assert set(journal.setdefault('b',{'ignored-default'}))=={'t1','t3'}
 assert journal.setdefault('b',set()) | {'extra'}==frozenset({'t1','t3','extra'})

def test_110_million_read_pairs_remain_grouped():
 from xl_seg.active_graph import ReadRelation
 p=Pool();members=tuple('s'+str(i) for i in range(9587));targets=tuple('t'+str(i) for i in range(11466));journal=ReadRelation(p);tracemalloc.start()
 for t in targets:journal.add_group(members,t)
 rows=journal.snapshot();assert sum(map(len,rows.values()))==109924542
 assert set(rows[members[0]])==set(targets)
 _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();assert peak<20*1024**2

def test_range_read_batch_preserves_values_missing_reads_and_failure_prefix():
 from types import SimpleNamespace
 from xl_seg.evaluate import Evaluator
 graph=SimpleNamespace(nodes={'a':object(),'formula':object()})
 first=Evaluator(graph,None,strict_proof=True);second=Evaluator(graph,None,strict_proof=True)
 for e in (first,second):e._current_cell='target';e.values={'a':7}
 refs=['a','blank','formula','a']
 left=first._read_span(refs);right=[second._read(x) for x in refs]
 assert [repr(x) for x in left]==[repr(x) for x in right]
 assert first.read_attempts==second.read_attempts and first.missing_reads==second.missing_reads
 def partial():
  yield 'a'
  raise RuntimeError('after first visit')
 first._current_cell='after'
 with pytest.raises(RuntimeError):first._read_span(partial())
 assert set(first.read_attempts['a'])=={'target','after'}

def test_full_110_million_component_plan_without_edge_materialization():
 from types import SimpleNamespace
 from xl_seg.evaluate import Evaluator
 p=Pool();members=tuple('S!A'+str(i) for i in range(1,9588));targets=tuple('T!B'+str(i) for i in range(1,11467))
 reverse=Rows({t:p.sources(members) for t in targets});forward=transpose(reverse,p)
 evaluator=Evaluator(SimpleNamespace(nodes={}),None,strict_proof=True)
 groups,topo,cyclic=evaluator._component_plan((*members,*targets),forward)
 assert len(groups)==21053 and not cyclic
 assert [groups[i][0] for i in topo]==list(members)+list(targets)
 assert all(len(group)==1 for group in groups)
 assert len(evaluator._graph_signature(forward))==109924542
 assert evaluator._component_plan((*members,*targets),forward)==(groups,topo,cyclic)
 assert evaluator._benchmark['component_plan']['cache_hits']==1

def test_endpoint_diagnostic_budget_precedes_materialization(monkeypatch):
 import xl_seg.active_graph as a
 assert a.MAX_DIAGNOSTIC_EDGES==4_000_000
 monkeypatch.setattr(a,'MAX_DIAGNOSTIC_EDGES',2)
 assert a.endpoint_equations({'x':{'b'}},{'x':{'a'}})==({'x':('b',)},{'x':('a',)})
 class CountOnly:
  def __len__(self):return 3
  def __iter__(self):pytest.fail('must not materialize over-budget diagnostic')
 with pytest.raises(a.DiagnosticResourceExhaustion) as exc:a.endpoint_equations({'x':CountOnly()})
 assert exc.value.reason_code=='resource_exhaustion' and exc.value.report['proof_truncated'] is False
 assert exc.value.report['observed_edge_count_lower_bound']==3

def test_lookup_grouped_journal_stops_at_same_first_bad_search_value():
 from types import SimpleNamespace
 from xl_seg.evaluate import Evaluator,RangeValues,Unresolved
 refs=tuple('S!'+col+str(row) for row in (1,2) for col in ('A','B','C'))
 e=Evaluator(SimpleNamespace(nodes={}),None,strict_proof=True);e._current_cell='T!A1'
 e._arg_specs=lambda node:[('scalar','key'),('span',refs),('scalar','index')]
 e._reference_arg=lambda *a,**k:RangeValues([None]*6,2,3,refs=refs)
 e._arg=lambda node,index:2
 e.values={'S!A1':1,'S!B1':Unresolved('unknown'),'S!C1':3}
 assert e._lookup_reference_plan(SimpleNamespace(op='HLOOKUP',range_truncated=False)) is None
 assert dict(e.read_attempts)=={'S!A1':{'T!A1'},'S!B1':{'T!A1'}}
 assert not e.read_attempts.direct and len(e.read_attempts.groups)==1
