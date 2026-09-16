import copy,json
import pytest
from scripts.batch import harbor_disclosure_partition_wire as W,harbor_disclosure_partition_evidence as E
from scripts.batch.harbor_admission import record
from tests.test_disclosure_partition_wire import pair
from tests.test_disclosure_partition_evidence import body_fixture,ROOT


def test_lossless_index_address_and_sheet_name_runs(tmp_path,monkeypatch):
 original=pair(tmp_path,monkeypatch);v=W.encode(original,encoding=W.RUN_SCHEMA)
 assert W.canonical(W.decode(v))==W.canonical(original)
 for name,book in W.decode(v).items():assert E.decode_workbook(book)==E.decode_workbook(original[name])
 assert W.encode(original)['schema_version']==W.SCHEMA

@pytest.mark.parametrize('values',[['A1','A2','A3','B1','B2'],['A1','B1','C1','A2','B2'],['XFD1048576','A1'],['$A$1','A1','A2'],['A1','A1','A1'],[]])
def test_address_order_exact_and_excel_bounds(values):
 assert W._expand_addresses(W._address_runs(values))==values
 with pytest.raises(ValueError):W._expand_addresses([['XFD1048576',1,1,2]])

@pytest.mark.parametrize('values',[[0,1,2,3,4],[5,4,3,2,1],[1,1,1],[1,3,8,11,14],[]])
def test_index_order_exact(values):assert W._expand_indexes(W._index_runs(values))==values

@pytest.mark.parametrize('bad',['sheet','duplicate_sheet','extra_sheet','address_count','address_bool','mixed_address','index_negative','mixed_ids','semantics'])
def test_run_corruption_fails_closed(tmp_path,monkeypatch,bad):
 v=W.encode(pair(tmp_path,monkeypatch),encoding=W.RUN_SCHEMA);b=v['workbooks']['golden'];c=v['shared_cell_columns'];sheet=b['sheets'][0]
 if bad=='sheet':b['selected_refs']['runs'][0][0]=9999
 elif bad=='duplicate_sheet':b['reference_sheet_names'].append(b['reference_sheet_names'][0])
 elif bad=='extra_sheet':b['reference_sheet_names'].append('undeclared')
 elif bad=='address_count':c['address_runs'][0][3]=2000001
 elif bad=='address_bool':c['address_runs'][0][3]=True
 elif bad=='mixed_address':c['addresses']=[]
 elif bad=='index_negative':sheet['cell_row_id_runs']=[[-1,0,1]]
 elif bad=='mixed_ids':sheet['cell_row_ids']=[]
 else:v['run_semantics']='omit blanks'
 with pytest.raises(ValueError):W.decode(v)


def test_run_bounded_plan_preserves_full_coverage_and_source_change_rejection(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch);old=E.plan(body,*paths,root=ROOT,max_sentences=2)
 policy=tmp_path/'policy.json';policy.write_text(json.dumps(dict(schema_version=E.WIRE_BOUNDS_SCHEMA,authorization=record(ROOT/'diagnostics/bulk-production/authorization-20260913.md'),full_body_sha256=E.digest(body),sources=dict(golden=record(paths[0]),delivered=record(paths[1])),max_bytes=750000,max_sentences=100,max_batches=64,wire_encoding=W.RUN_SCHEMA)))
 new=E.plan(body,*paths,root=ROOT,max_sentences=2,bounds_policy=record(policy))
 assert new['coverage']==old['coverage']
 for a,b in zip(new['batches'],old['batches']):assert E.canonical({**a,'schema_version':E.SCHEMA,'workbooks':W.decode(a['workbooks'])})==E.canonical(b)
 assert E.validate_plan(new,body,*paths,root=ROOT)==new
 new['batches'][0]['workbooks']['shared_cell_columns']['values'][0]='changed'
 with pytest.raises(ValueError):E.validate_plan(new,body,*paths,root=ROOT)
