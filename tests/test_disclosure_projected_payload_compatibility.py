import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_disclosure_evidence as E,harbor_disclosure_payload_repair as R
from test_disclosure_lossless_tables import workbooks


def request():
    body=dict(manifest={'sentences':[{'text':'Exact formula and complete clause','record_ids':['a']}]},
      manifest_sha256='a'*64,instruction='Exact text\nSecond line',workbooks=workbooks(),
      disclosure=dict(records=[dict(id='a',cell_keys=['S!A1'],formula='=B1*C1')],
        agent_records=[dict(id='a',cell_keys=['S!A1'])],
        method_assessments=[dict(cell_keys=['S!A1'],proof='keep'),dict(cell_keys=[],proof='uncertain keep')],
        metadata={'nested':[None,False,0,1.5,'',{'formula':'=IF(A1,"",0)'}]}))
    projected=E.project_body(body)
    return dict(model='review-model',reasoning_effort='high',messages=[
        dict(role='system',content='Every clause and record, no shortcuts'),
        dict(role='user',content=json.dumps(projected,indent=2))]),projected


def test_all_six_fields_full_tables_types_and_clauses_survive_inverse():
    original,body=request();before=copy.deepcopy(original)
    candidate=R.project(original,projection=R.TABLE_VERSION)
    decoded=json.loads(candidate['messages'][1]['content'])
    assert R.E.canonical(decoded)==R.E.canonical(body)
    assert R.E.canonical(E.decode_body(decoded))==R.E.canonical(E.decode_body(body))
    assert set(decoded)==E.FIELDS|{'evidence_projection'}
    assert original==before and candidate['messages'][0]==original['messages'][0]
    assert candidate['model']==original['model'] and candidate['reasoning_effort']==original['reasoning_effort']
    assert len(R.E.canonical(candidate))<len(R.E.canonical(original))
    assert R.project(candidate,projection=R.TABLE_VERSION)==candidate


@pytest.mark.parametrize('change',['version','extra','missing','table-version','columns','format-index','row-length','implicit'])
def test_unknown_or_damaged_protocol_is_not_silently_reinterpreted(change):
    original,body=request()
    if change=='version':body['evidence_projection']='unknown'
    elif change=='extra':body['unknown_authority']={}
    elif change=='missing':body.pop('manifest')
    elif change=='table-version':body['workbooks']['encoding']='unknown'
    elif change=='columns':body['workbooks']['workbooks']['golden']['sheets'][0]['cells']['columns'].reverse()
    elif change=='format-index':body['workbooks']['workbooks']['golden']['sheets'][0]['cells']['rows'][0][2]=-1
    elif change=='row-length':body['workbooks']['workbooks']['golden']['sheets'][0]['cells']['rows'][0].extend(['extra']*3)
    original['messages'][1]['content']=json.dumps(body)
    with pytest.raises(ValueError):R.project(original,projection=None if change=='implicit'else R.TABLE_VERSION)


from test_harbor_model_executor import setup


def test_prior_producer_hash_cannot_be_relabelled_as_new_six_field_capability(setup,tmp_path,monkeypatch):
    from dataclasses import replace
    from test_disclosure_payload_repair import original,reject
    from scripts.batch import harbor_model_executor as X
    campaign,prepared=original(setup)
    raw=R.read(prepared.request);raw['messages'][1]['content']=request()[0]['messages'][1]['content']
    Path(prepared.request['path']).write_bytes(X.canonical(raw));prepared=replace(prepared,request=X.record(prepared.request['path']))
    reject(prepared,campaign,monkeypatch)
    repaired=R.prepare(prepared,campaign,tmp_path/'new',projection=R.TABLE_VERSION)
    previous=tmp_path/'prior-producer.py';previous.write_text('# explicit historical producer fixture')
    prior=X.record(previous);monkeypatch.setattr(R,'PRIOR_IMPLEMENTATIONS',{prior['sha256']})
    value=R.read(repaired.physical_repair);value['implementation']=prior
    path=tmp_path/'relabeled.json';path.write_bytes(X.canonical(value));forged=replace(repaired,physical_repair=X.record(path))
    with pytest.raises(ValueError,match='historical physical producer'):
        R.validate_prepared(forged,campaign)
    assert not X.location(repaired,campaign).exists()
