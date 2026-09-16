import copy,json
import pytest
from test_disclosure_partition_evidence import body_fixture,ROOT
from scripts.batch import harbor_disclosure_partition_evidence as E
from scripts.batch.harbor_admission import record

def bounds(tmp_path,monkeypatch,body,paths,*,count=64):
 auth=tmp_path/'authorization.md';auth.write_text('explicit test authority');monkeypatch.setattr(E,'AUTHORIZATION_SHA256',record(auth)['sha256'])
 p=tmp_path/'policy.json';p.write_text(json.dumps(dict(schema_version=E.BOUNDS_SCHEMA,authorization=record(auth),full_body_sha256=E.digest(body),sources=dict(golden=record(paths[0]),delivered=record(paths[1])),max_bytes=2000000,max_sentences=100,max_batches=count)))
 return record(p)

def test_explicit_large_payload_keeps_original_default_and_exact_source(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch,1);body['instruction']='Context '+('x'*760000)+'\n'+body['instruction'];body['manifest']['input_hashes']['instruction']=E.hashlib.sha256(body['instruction'].encode()).hexdigest()
 with pytest.raises(E.ScopeDiagnosisRequired):E.plan(body,*paths,root=ROOT)
 policy=bounds(tmp_path,monkeypatch,body,paths);plan=E.plan(body,*paths,root=ROOT,max_bytes=2000000,bounds_policy=policy)
 assert plan['schema_version']==E.BOUNDED_SCHEMA and len(plan['batches'])==1
 assert plan['batches'][0]['instruction']==body['instruction']
 assert E.validate_plan(plan,body,*paths,root=ROOT)==plan
 changed=copy.deepcopy(body);changed['instruction']+='changed'
 with pytest.raises(ValueError,match='source or body'):E.validate_plan(plan,changed,*paths,root=ROOT)

@pytest.mark.parametrize('field,value',[('max_bytes',2000001),('max_bytes',True),('max_batches',65),('max_batches',True),('max_sentences',101)])
def test_bounds_reject_widening(tmp_path,monkeypatch,field,value):
 body,paths=body_fixture(tmp_path,monkeypatch);p=bounds(tmp_path,monkeypatch,body,paths);row=E.json.loads(E.Path(p['path']).read_bytes());row[field]=value;E.Path(p['path']).write_text(json.dumps(row))
 with pytest.raises(ValueError,match='limits'):E.selected_bounds(record(p['path']),body,*paths)

def test_partition_count_is_finite_and_wrong_authority_rejected(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch);p=bounds(tmp_path,monkeypatch,body,paths,count=2)
 with pytest.raises(ValueError,match='partition count'):E.plan(body,*paths,root=ROOT,max_sentences=1,max_bytes=2000000,bounds_policy=p)
 monkeypatch.setattr(E,'AUTHORIZATION_SHA256','0'*64)
 with pytest.raises(ValueError,match='authorization'):E.selected_bounds(p,body,*paths)

def test_legacy_plan_cannot_smuggle_new_limit_or_policy(tmp_path,monkeypatch):
 body,paths=body_fixture(tmp_path,monkeypatch);p=E.plan(body,*paths,root=ROOT);p['max_bytes']=2000000
 with pytest.raises(ValueError,match='unapproved'):E.validate_plan(p,body,*paths,root=ROOT)
 p['bounds_policy']=bounds(tmp_path,monkeypatch,body,paths)
 with pytest.raises(ValueError,match='fields'):E.validate_plan(p,body,*paths,root=ROOT)
