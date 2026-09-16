import copy,json
import pytest
from scripts.batch import harbor_disclosure_partition_wire as W,harbor_disclosure_partition_evidence as E
from scripts.batch.harbor_admission import record
from tests.test_disclosure_partition_wire import pair
from tests.test_disclosure_partition_evidence import body_fixture,ROOT


def test_opt_in_roundtrip_preserves_both_sources_and_every_evidence_field(tmp_path,monkeypatch):
    original=pair(tmp_path,monkeypatch)
    legacy=W.encode(original)
    encoded=W.encode(original,encoding=W.COLUMNAR_SCHEMA)
    assert legacy['schema_version']==W.SCHEMA and 'shared_cell_rows'in legacy
    assert W.canonical(W.decode(encoded))==W.canonical(original)
    for name,book in W.decode(encoded).items():assert E.decode_workbook(book)==E.decode_workbook(original[name])
    rows=W._expand_columns(encoded['shared_cell_columns'])
    assert rows==legacy['shared_cell_rows']
    assert any(len(r)==4 for r in rows)and any(len(r)==5 and r[4]is None for r in rows)


@pytest.mark.parametrize('bad',['count','bool_count','cache_flag','missing_cache','extra_cache','value_length','format','fields','semantics','split_runs'])
def test_columnar_corruption_rejected(tmp_path,monkeypatch,bad):
    encoded=W.encode(pair(tmp_path,monkeypatch),encoding=W.COLUMNAR_SCHEMA);c=encoded['shared_cell_columns']
    if bad=='count':c['attribute_runs'][0][0]=2000001
    elif bad=='bool_count':c['attribute_runs'][0][0]=True
    elif bad=='cache_flag':c['attribute_runs'][0][3]=1
    elif bad=='missing_cache':c['cached_values'].clear()
    elif bad=='extra_cache':c['cached_values'].append(None)
    elif bad=='value_length':c['values'].append(0)
    elif bad=='format':c['attribute_runs'][0][1]=10**9
    elif bad=='fields':c['omitted']=True
    elif bad=='semantics':encoded['column_semantics']='cached null is absent'
    else:
        rows=[['A1',0,0,'n'],['A2',0,0,'n']];c=W._columns(rows);c['attribute_runs']=[[1,0,'n',False],[1,0,'n',False]]
        with pytest.raises(ValueError):W._expand_columns(c)
        return
    with pytest.raises(ValueError):W.decode(encoded)


def test_source_bound_columnar_plan_keeps_full_global_coverage_and_rejects_cell_change(tmp_path,monkeypatch):
    body,paths=body_fixture(tmp_path,monkeypatch);old=E.plan(body,*paths,root=ROOT,max_sentences=2)
    policy=tmp_path/'policy.json';policy.write_text(json.dumps(dict(schema_version=E.WIRE_BOUNDS_SCHEMA,authorization=record(ROOT/'diagnostics/bulk-production/authorization-20260913.md'),full_body_sha256=E.digest(body),sources=dict(golden=record(paths[0]),delivered=record(paths[1])),max_bytes=750000,max_sentences=100,max_batches=64,wire_encoding=W.COLUMNAR_SCHEMA)))
    new=E.plan(body,*paths,root=ROOT,max_sentences=2,bounds_policy=record(policy))
    assert new['coverage']==old['coverage']
    for a,b in zip(new['batches'],old['batches']):assert E.canonical({**a,'schema_version':E.SCHEMA,'workbooks':W.decode(a['workbooks'])})==E.canonical(b)
    assert E.validate_plan(new,body,*paths,root=ROOT)==new
    new['batches'][0]['workbooks']['shared_cell_columns']['values'][0]='changed'
    with pytest.raises(ValueError):E.validate_plan(new,body,*paths,root=ROOT)
