"""Round-trip every evidence field; no numeric, absent/null or version conflation."""
import copy,json
import pytest
from tests.test_disclosure_partition_evidence import body_fixture,ROOT
from scripts.batch import harbor_disclosure_partition_evidence as E,harbor_disclosure_partition_wire as W
from scripts.batch.harbor_admission import record

def pair(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch);p=E.plan(body,*paths,root=ROOT)
 return p['batches'][0]['workbooks']

def test_complete_distinct_workbook_shards_roundtrip(tmp_path,monkeypatch):
 p=pair(tmp_path,monkeypatch);p['delivered']['sheets'][0]['present_rows'][0][1]=99
 wire=W.encode(p);assert W.canonical(W.decode(wire))==W.canonical(p)
 assert len(wire['shared_cell_rows'])<sum(len(s['present_rows'])for b in p.values()for s in b['sheets'])
 assert W.decode(wire)['golden']!=W.decode(wire)['delivered']
 for name,b in W.decode(wire).items():assert E.decode_workbook(b)==E.decode_workbook(p[name])

@pytest.mark.parametrize('bad',['index','row','format','unused','edge','duplicate','schema'])
def test_invalid_wire_is_rejected(tmp_path,monkeypatch,bad):
 wire=W.encode(pair(tmp_path,monkeypatch))
 if bad=='index':wire['workbooks']['golden']['sheets'][0]['cell_row_ids'][0]=10**9
 elif bad=='row':wire['shared_cell_rows'][0].append(None);wire['shared_cell_rows'][0].append(None)
 elif bad=='format':wire['shared_cell_rows'][0][2]=10**9
 elif bad=='unused':wire['shared_cell_rows'].append(['ZZ1','unused',0,'s'])
 elif bad=='edge':wire['workbooks']['golden']['dependency_edge_runs']=[[0,0,2000001]]
 elif bad=='duplicate':wire['shared_cell_rows'].append(copy.deepcopy(wire['shared_cell_rows'][0]))
 else:wire['semantics']='drop unknown source cells'
 with pytest.raises(ValueError):W.decode(wire)

def test_edge_ranges_preserve_dense_and_sparse_order_without_duplicate_targets():
 edges=[[0,i]for i in range(1000)]+[[1,1],[1,3],[1,4],[8,99]]
 assert W.edge_runs(edges)==[[0,0,1000],[1,1,1,3,2],[8,99,1]]
 assert W.expand_edges(W.edge_runs(edges))==edges
 for bad in ([[1,1,1],[1,1,1]],[[0,3,1,2,1]],[[True,1,1]]):
  with pytest.raises(ValueError):W.expand_edges(bad)

def test_source_bound_plan_preserves_all_sentence_record_and_dependency_coverage(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch);original=E.plan(body,*paths,root=ROOT,max_sentences=2)
 auth=ROOT/'diagnostics/bulk-production/authorization-20260913.md';policy=tmp_path/'wire-policy.json'
 policy.write_text(json.dumps(dict(schema_version=E.WIRE_BOUNDS_SCHEMA,authorization=record(auth),full_body_sha256=E.digest(body),sources=dict(golden=record(paths[0]),delivered=record(paths[1])),max_bytes=750000,max_sentences=100,max_batches=64,wire_encoding=W.SCHEMA)))
 selected=E.plan(body,*paths,root=ROOT,max_sentences=2,bounds_policy=record(policy))
 assert selected['schema_version']==E.WIRE_SCHEMA and selected['coverage']==original['coverage']
 for a,b in zip(selected['batches'],original['batches']):
  expanded={**a,'schema_version':E.SCHEMA,'workbooks':W.decode(a['workbooks'])};assert E.canonical(expanded)==E.canonical(b)
 assert E.validate_plan(selected,body,*paths,root=ROOT)==selected
 selected['batches'][0]['workbooks']['shared_cell_rows'][0][1]='changed'
 with pytest.raises(ValueError):E.validate_plan(selected,body,*paths,root=ROOT)

def test_native_null_cached_value_and_absence_stay_different(tmp_path,monkeypatch):
 p=pair(tmp_path,monkeypatch);w=W.encode(p);d=W.decode(w)
 rows=[r for b in d.values()for s in b['sheets']for r in s['present_rows']]
 assert any(len(r)==4 for r in rows)and any(len(r)==5 and r[4]is None for r in rows)
 assert W.canonical(d)==W.canonical(p)
