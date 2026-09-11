from pathlib import Path
import json
from types import SimpleNamespace as NS
import pytest
from xl_seg import lineage,emit
GOLDEN = Path(__file__).parent / 'fixtures' / 'lineage_streaming'

def fixture():
 ids=[f"1 O'Brien!A{i}" for i in range(1,9)];adj={ids[i]:{ids[i+1]} for i in range(7)};radj={ids[i+1]:{ids[i]} for i in range(7)}
 bands={c:NS(cells=[c],label='Repeated | € label',sheet="1 O'Brien",pattern='=$A$1+1',kind='formula',width=1) for c in ids}
 bg=NS(bands=bands,of_cell={c:c for c in ids});cd=NS(comp_of={c:c for c in ids},comp_members={c:[c] for c in ids},comp_adj=adj,comp_radj=radj,depth={c:i for i,c in enumerate(ids)})
 part=NS(bucket={c:'middle' for c in ids});cg=NS(adj=adj,radj=radj,info={c:NS(node=NS(label='Δ / Unicode',formula='=$A$1+1',kind='formula',value='saved')) for c in ids})
 vals=dict(zip(ids,[None,False,'=literal',1.5,float('nan'),float('inf'),7,8]))
 return bg,cd,part,cg,vals,[ids[7],ids[4],ids[6]],{ids[0]},3

def tree(path):return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}

def test_all_emitted_bytes_identical_including_proof_order_labels_and_truncation(tmp_path):
 args=fixture();b=lineage.build(*args)
 assert [t.output for t in b]==[args[5][0],args[5][2],args[5][1]]
 actual_dir=tmp_path/'actual';actual_dir.mkdir();spool=b._store.directory
 proof={'outputs':['é','A1'],'blank':None,'nested':{'ok':True}}
 emit.write_lineage(actual_dir,'fixture',b,args[4],proof=proof)
 assert tree(GOLDEN/'original')==tree(actual_dir)
 assert not spool.exists()

def test_empty_trace_bytes_and_unsupported_types_unchanged(tmp_path):
 emit.write_lineage(tmp_path,'fixture',[],{},proof={})
 assert tree(GOLDEN/'empty')==tree(tmp_path)
 with pytest.raises(TypeError):json.dumps(object(),cls=emit._LineageEncoder,indent=2)

def test_lazy_cell_computed_once_with_snapshot_values_and_missing_key_rejected():
 args=fixture();traces=lineage.build(*args);cell=traces[0].output;args[4][cell]='mutated after build'
 first=traces[0].cell_traces[cell];again=traces[0].cell_traces[cell]
 assert first['steps'][-1]['value']==8 and again['steps'][-1]['value']==8
 assert len(traces._store._records)==1
 with pytest.raises(KeyError):traces[0].cell_traces['not a target']
 traces.close()
 with pytest.raises(ValueError,match='closed'):traces[0].cell_traces[cell]

def test_altered_spool_rejected_and_writer_cleans_temporary_evidence(tmp_path):
 args=fixture();traces=lineage.build(*args);cell=traces[0].output;traces[0].cell_traces[cell]
 spool=traces._store.directory;path,_=traces._store._records[cell];path.write_text('{}')
 with pytest.raises(ValueError,match='spool changed'):emit.write_lineage(tmp_path,'fixture',traces,args[4])
 assert not spool.exists() and not (tmp_path/'lineage.json').exists()

def test_failed_final_write_cleans_spool_and_propagates_error(tmp_path,monkeypatch):
 args=fixture();traces=lineage.build(*args);spool=traces._store.directory;original_open=Path.open
 def fail(path,*a,**kw):
  if path.name=='lineage.json':raise OSError('simulated storage interruption')
  return original_open(path,*a,**kw)
 monkeypatch.setattr(Path,'open',fail)
 with pytest.raises(OSError,match='interruption'):emit.write_lineage(tmp_path,'fixture',traces,args[4])
 assert not spool.exists()

def test_factory_failure_never_creates_complete_spool_record():
 store=lineage.CellTraceStore()
 def fail():raise RuntimeError('trace failed')
 with pytest.raises(RuntimeError):store.get('a',fail)
 assert store._records=={} and not list(store.directory.iterdir())
 store.close()

def test_runtime_dependency_graph_emits_identical_bytes_without_static_branch(tmp_path):
 args=fixture();ids=list(args[4]);runtime={ids[-1]:{ids[0]},ids[-2]:{ids[1]},ids[4]:{ids[0]}}
 traces=lineage.build(*args,proof_radj=runtime)
 emit.write_lineage(tmp_path,'runtime',traces,args[4])
 assert tree(GOLDEN/'runtime')==tree(tmp_path)


def test_build_failure_explicitly_closes_temporary_store(monkeypatch):
 stores=[];store_type=lineage.CellTraceStore
 def store():
  result=store_type();stores.append(result);return result
 def fail(*args,**kwargs):raise RuntimeError('band construction failed')
 monkeypatch.setattr(lineage,'CellTraceStore',store)
 monkeypatch.setattr(lineage,'band_trace',fail)
 with pytest.raises(RuntimeError,match='band construction failed'):lineage.build(*fixture())
 assert len(stores)==1 and stores[0]._closed and not stores[0].directory.exists()
