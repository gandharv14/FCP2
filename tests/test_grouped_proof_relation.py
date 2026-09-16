import copy, json, random, tracemalloc
import pytest
from xl_seg import proof_graph as p
from xl_seg.active_graph import Pool, Sources
from xl_seg.group_adjacency import Neighbors
from xl_seg.condense import strongly_connected


def test_random_exact_roundtrip_signature_cone_scc_and_exclusions():
    for seed in range(30):
        r=random.Random(seed);keys=[str(i)for i in range(16)];pool=Pool();groups=[pool.sources(r.sample(keys,r.randrange(16)))for _ in range(5)]
        rows={k:Neighbors(k,{r.choice(keys)},tuple(r.sample(groups,r.randrange(5))))for k in keys}
        relation=p.freeze(rows);plain={k:set(v)for k,v in rows.items()}
        assert dict(relation)==plain
        wire=json.loads(json.dumps(p.encode(relation)));decoded=p.decode(wire)
        assert dict(decoded)==plain and p.Signature(decoded)==p.Signature(relation)
        expanded=p.freeze(plain);assert p.Signature(expanded)==p.Signature(decoded)
        assert strongly_connected(sorted(keys),relation)==strongly_connected(sorted(keys),{k:sorted(v)for k,v in plain.items()})
        chosen=set(keys[::2]);filtered=p.freeze(rows,keys=chosen,members=chosen)
        assert dict(filtered)=={k:plain[k]&chosen for k in chosen}


def test_110_million_edges_roundtrip_stays_bounded_and_exact():
    sources=frozenset('s'+str(i)for i in range(9587));targets=['t'+str(i)for i in range(11466)];shared=Sources((sources,))
    tracemalloc.start();relation=p.freeze(dict.fromkeys(targets,shared));wire=p.encode(relation);decoded=p.decode(json.loads(json.dumps(wire)))
    assert len(p.Signature(decoded))==109924542
    assert p.Signature(decoded)==p.Signature(relation)
    assert len(wire['groups'])==1 and len(wire['groups'][0])==9587
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();assert peak<32*1024**2
    assert set(decoded[targets[-1]])==sources


def test_many_self_exclusions_preserve_shared_group():
    nodes=frozenset(str(i)for i in range(2000));rows={k:Neighbors(k,(),(nodes,))for k in nodes}
    relation=p.freeze(rows);wire=p.encode(relation)
    assert relation.edge_count==2000*1999 and len(wire['groups'])==1
    assert all(k not in relation[k]for k in nodes)
    assert p.Signature(relation)==p.Signature(p.decode(wire))


def test_rows_are_snapshots_and_different_edges_never_equal():
    original={'a':{'a','b'}};relation=p.freeze(original);original['a'].add('c')
    assert set(relation['a'])=={'a','b'}
    assert p.Signature(relation)!=p.Signature(p.freeze({'a':{'a','c'}}))
    with pytest.raises(TypeError):relation['a'].terms=()


@pytest.mark.parametrize('change',[
    lambda v:v.update(extra=True),lambda v:v['groups'].append(['unused']),
    lambda v:v['rows']['t'].append([0,[]]),lambda v:v['rows']['t'][0].__setitem__(0,True),
    lambda v:v['rows']['t'][0].__setitem__(0,9),lambda v:v['rows']['t'][0].__setitem__(1,['foreign']),
    lambda v:v['groups'][0].append(v['groups'][0][0]),lambda v:v['groups'][0].__setitem__(0,False),
])
def test_malformed_or_ambiguous_grouped_proofs_rejected(change):
    value=p.encode(p.freeze({'t':{'a','b'}}));change(value)
    with pytest.raises(ValueError):p.decode(value)


def test_actual_strict_closure_grouped_wire_and_restriction_cone_match_legacy(monkeypatch):
    import xl_segment as s
    from test_runtime_proof_closure import _cell, _ast, _edge, _graph, CALC
    from xl_seg.restriction_cone import _proof_cone
    inputs=[_cell('Sheet!A'+str(i),'input',i)for i in range(1,4)]
    output=_cell('Sheet!B1','formula',6,'=SUM(A1,A2,A3)')
    op=_ast('Sheet!B1#0:SUM',output.id,'op',op='SUM',arity=3)
    graph,cg=_graph([*inputs,output,op],[_edge(cell.id,op.id,i,op='SUM')for i,cell in enumerate(inputs)]+[_edge(op.id,output.id)])
    _,_,_,legacy=s.stabilize_runtime_proof(graph,cg,{i.id for i in inputs},{output.id},calculation=CALC)
    monkeypatch.setattr(s,'MAX_MATERIALIZED_PROOF_EDGES',2)
    result,seeds,radj,proof=s.stabilize_runtime_proof(graph,cg,{i.id for i in inputs},{output.id},calculation=CALC)
    assert result.values[output.id]==6 and proof['closure']==legacy['closure'] and proof['schema_version']==2
    wire=json.loads(json.dumps(proof));decoded=p.read(wire['runtime_radj'])
    assert {k:set(v)for k,v in decoded.items()}=={k:set(v)for k,v in legacy['runtime_radj'].items()}
    assert _proof_cone([output.id],wire['runtime_radj'])==_proof_cone([output.id],legacy['runtime_radj'])
    assert {k:set(v)for k,v in p.read(wire['attempted_reads']).items()}=={k:set(v)for k,v in legacy['attempted_reads'].items()}
    assert s._active_cycle_groups(set(decoded),decoded)==[]


def test_large_grouped_scc_keeps_self_cycles_and_matches_expanded_oracle(monkeypatch):
    import xl_segment as s
    monkeypatch.setattr(s,'MAX_MATERIALIZED_PROOF_EDGES',2)
    graph={'a':Sources((frozenset(('a','b')),)), 'b':Sources((frozenset(('a','c')),)), 'c':Sources((frozenset(('b',)),)), 'solo':Sources((frozenset(('solo',)),))}
    assert s._active_cycle_groups(set(graph),graph)==[('a','b','c'),('solo',)]
    assert isinstance(s._proof_edge_signature(graph,set(graph)),p.Signature)
