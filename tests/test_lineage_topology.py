from xl_seg import lineage
from collections import deque

def reference(cone,adj,radj):
 indeg={n:sum(1 for p in radj.get(n,()) if p in cone) for n in cone}
 queue=deque(sorted(n for n,d in indeg.items() if d==0));order=[]
 while queue:
  node=queue.popleft();order.append(node)
  for succ in sorted(adj.get(node,())):
   if succ in cone:
    indeg[succ]-=1
    if indeg[succ]==0:queue.append(succ)
 order.extend(sorted(n for n in cone if n not in set(order)))
 return order

def test_exact_order_with_ties_cycle_tail_and_excluded_cone():
 cone={'a','b','c','d','e','f'};adj={'a':{'d','c','outside'},'b':{'c'},'e':{'f'},'f':{'e'}};radj={'c':{'a','b','outside'},'d':{'a'},'e':{'f'},'f':{'e'}}
 expected=reference(cone,adj,radj)
 assert lineage._restricted_topo(cone,adj,radj)==expected==['a','b','d','c','e','f']
 assert len(expected)==len(set(expected))==len(cone)

def test_empty_and_duplicate_successor_behavior_unchanged():
 for cone,adj,radj in [(set(),{},{}),({'a'}, {'a':['outside','outside']},{}),({'a','b'},{'a':['b','b']},{'b':['a','a']})]:
  assert lineage._restricted_topo(cone,adj,radj)==reference(cone,adj,radj)

def test_long_chain_retains_every_member_exactly_once():
 cone={f'N{i:05}' for i in range(5000)};adj={f'N{i:05}':{f'N{i+1:05}'} for i in range(4999)};radj={f'N{i+1:05}':{f'N{i:05}'} for i in range(4999)}
 actual=lineage._restricted_topo(cone,adj,radj)
 assert actual==sorted(cone) and len(actual)==len(set(actual))
