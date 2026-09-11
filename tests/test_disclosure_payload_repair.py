import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import urllib.error
import pytest
from test_harbor_model_executor import setup,Response,response_bytes,TOKEN
from scripts.batch import harbor_model_executor as E,harbor_disclosure_payload_repair as R


def original(setup):
    campaign,make=setup;p=make(ordinal=1,limit=1)
    p=replace(p,scope=replace(p.scope,lane='harbor-disclosure-writer',budget_id='disclosure-review'))
    request=R.read(p.request)
    record={'id':'a','cell_keys':['S!A1'],'formula':'=B1*C1'}
    body={'manifest':{'sentences':[{'record_ids':['a'],'text':'multiply B1 by C1'}]},'manifest_sha256':'b'*64,
          'workbooks':{'golden':{'cells':['complete']},'delivered':{'cells':['complete']}},'instruction':'multiply B1 by C1',
          'disclosure':{'records':[record],'agent_records':[record], 'method_assessments':[
              {'cell_keys':['S!A1'],'proof':'=B1*C1'}, {'cell_keys':['S!Z9'],'proof':'irrelevant'},
              {'cell_keys':[],'proof':'unknown scope retained'}]}}
    request['messages'][1]['content']=json.dumps(body)
    Path(p.request['path']).write_bytes(E.canonical(request));p=replace(p,request=E.record(p.request['path']))
    return campaign,p


def reject(p,c,monkeypatch,*,status=400):
    def failed(*args):raise urllib.error.HTTPError(p.policy.endpoint,status,'fixture',{},io.BytesIO(b'{"error":{"message":"explicit rejection"}}'))
    monkeypatch.setattr(E,'_open',failed)
    assert E.execute(p,c,credential=TOKEN).status=='http_rejected'


def test_one_physical_retry_keeps_original_budget_uuid_and_all_evidence(setup,monkeypatch,tmp_path):
    c,p=original(setup);reject(p,c,monkeypatch)
    before={q.name:q.read_bytes() for q in E.location(p,c).iterdir() if q.is_file()}
    repaired=R.prepare(p,c,tmp_path/'repair');assert repaired.attempt_id==p.attempt_id
    assert (repaired.scope,repaired.ordinal,repaired.limit,repaired.budget_evidence)==(p.scope,1,1,p.budget_evidence)
    calls=[]
    def success(*args):calls.append(1);return Response(response_bytes(p.policy))
    monkeypatch.setattr(E,'_open',success)
    result=E.execute(repaired,c,credential=TOKEN);assert result.status=='completed'
    assert E.inspect(repaired,c).status=='completed';assert E.execute(repaired,c,credential=TOKEN).status=='completed';assert len(calls)==1
    assert {q.name:q.read_bytes() for q in E.location(p,c).iterdir() if q.is_file()}==before
    assert E.inspect(p,c).status=='http_rejected'
    with pytest.raises(ValueError):R.prepare(p,c,tmp_path/'another-namespace')
    with pytest.raises(ValueError):R.prepare(repaired,c,tmp_path/'third')


@pytest.mark.parametrize('kind',['completed','uncertain','partial','error_completion'])
def test_never_repair_completed_or_uncertain(setup,monkeypatch,tmp_path,kind):
    c,p=original(setup)
    def opened(*args):
        if kind=='completed':return Response(response_bytes(p.policy))
        if kind=='uncertain':raise TimeoutError('uncertain')
        if kind=='error_completion':raise urllib.error.HTTPError(p.policy.endpoint,400,'fixture',{},io.BytesIO(response_bytes(p.policy)))
        class Partial(Response):
            def read(self,n):
                if self.tell():raise OSError('partial')
                return super().read(n)
        return Partial(b'{')
    monkeypatch.setattr(E,'_open',opened);E.execute(p,c,credential=TOKEN)
    with pytest.raises(ValueError):R.prepare(p,c,tmp_path/'repair')


@pytest.mark.parametrize('kind',['record','manifest','workbook','assessment','budget','outcome'])
def test_tamper_or_missing_coverage_rejected(setup,monkeypatch,tmp_path,kind):
    c,p=original(setup);reject(p,c,monkeypatch);r=R.prepare(p,c,tmp_path/'repair')
    if kind=='budget':r=replace(r,ordinal=2,limit=2)
    elif kind=='outcome':(E.location(p,c)/'transport-00.body.raw').write_text('{}')
    else:
        payload=R.read(r.request);body=json.loads(payload['messages'][1]['content'])
        if kind=='record':body['disclosure']['records']=[]
        elif kind=='manifest':body['manifest']['sentences']=[]
        elif kind=='workbook':body['workbooks']['golden']={}
        else:body['disclosure']['method_assessments']=[]
        payload['messages'][1]['content']=json.dumps(body)
        altered=tmp_path/'altered.json';altered.write_bytes(E.canonical(payload))
        value=R.read(r.physical_repair);value['request']=E.record(altered)
        forged=tmp_path/'forged-permit.json';forged.write_bytes(E.canonical(value))
        r=replace(r,request=E.record(altered),physical_repair=E.record(forged))
    with pytest.raises(ValueError):E.execute(r,c,credential=TOKEN)


@pytest.mark.parametrize('failure',['400','503','uncertain'])
def test_repair_failure_sealed_without_any_second_transport(setup,monkeypatch,tmp_path,failure):
    c,p=original(setup);reject(p,c,monkeypatch);r=R.prepare(p,c,tmp_path/'repair');calls=[]
    def failed(*args):
        calls.append(1)
        if failure=='uncertain':raise ConnectionError('uncertain')
        raise urllib.error.HTTPError(p.policy.endpoint,int(failure),'fixture',{},io.BytesIO(b'{"error":{}}'))
    monkeypatch.setattr(E,'_open',failed)
    result=E.execute(r,c,credential=TOKEN)
    assert result.status in {'http_rejected','incomplete'}
    assert E.execute(r,c,credential=TOKEN).status==result.status and len(calls)==1
    with pytest.raises(ValueError):R.prepare(p,c,tmp_path/'new-repair')


def test_projection_keeps_every_clause_and_relevant_or_unknown_assessment(setup):
    _,p=original(setup);old=R.read(p.request);new=R.project(old)
    a=json.loads(old['messages'][1]['content']);b=json.loads(new['messages'][1]['content'])
    assert b['disclosure']['method_assessments']==[a['disclosure']['method_assessments'][0],a['disclosure']['method_assessments'][2]]
    b['disclosure']['method_assessments']=a['disclosure']['method_assessments'];assert a==b


def test_two_offline_proposals_cannot_dispatch_twice(setup,monkeypatch,tmp_path):
    c,p=original(setup);reject(p,c,monkeypatch)
    one=R.prepare(p,c,tmp_path/'one');two=R.prepare(p,c,tmp_path/'two')
    seen=[]
    def success(*args):seen.append(1);return Response(response_bytes(p.policy))
    monkeypatch.setattr(E,'_open',success);E.execute(one,c,credential=TOKEN)
    with pytest.raises(ValueError,match='reserved'):E.execute(two,c,credential=TOKEN)
    assert len(seen)==1


def test_scope_marker_omission_does_not_reopen_retained_repair(setup,monkeypatch,tmp_path):
    c,p=original(setup);reject(p,c,monkeypatch);r=R.prepare(p,c,tmp_path/'repair')
    monkeypatch.setattr(E,'_open',lambda *a:Response(response_bytes(p.policy)))
    E.execute(r,c,credential=TOKEN);(E.location(p,c).parent/'physical-repair.json').unlink()
    with pytest.raises(ValueError,match='reservation'):E.inspect(r,c)


@pytest.mark.parametrize('key',["S!row 1","S!A1:C1","S!A:A","S!$A$1","S!XFE1","S!A1048577","'S'!A1"])
def test_unknown_assessment_scope_is_retained_and_unknown_record_scope_rejects(setup,key):
    _,p=original(setup);payload=R.read(p.request);body=json.loads(payload['messages'][1]['content'])
    body['disclosure']['method_assessments'].append({'cell_keys':[key],'proof':'uncertain'})
    payload['messages'][1]['content']=json.dumps(body)
    projected=json.loads(R.project(payload)['messages'][1]['content'])
    assert projected['disclosure']['method_assessments'][-1]['cell_keys']==[key]
    body['disclosure']['records'][0]['cell_keys']=[key];payload['messages'][1]['content']=json.dumps(body)
    with pytest.raises(ValueError,match='uncertain disclosed'):R.project(payload)


def test_projection_rejects_unfamiliar_message_shape(setup):
    _,p=original(setup);payload=R.read(p.request);payload['messages'].reverse()
    with pytest.raises(ValueError,match='system/user'):R.project(payload)


def test_checked_identical_implementation_relocation_preserves_physical_permit(setup,monkeypatch,tmp_path):
    c,p=original(setup);reject(p,c,monkeypatch);repaired=R.prepare(p,c,tmp_path/'repair')
    relocated=tmp_path/'relocated.py';relocated.write_bytes(Path(R.__file__).read_bytes())
    monkeypatch.setattr(R,'__file__',str(relocated))
    R.validate_prepared(repaired,c)
    relocated.write_text('# unrelated implementation\n')
    with pytest.raises(ValueError,match='implementation changed'):
        R.validate_prepared(repaired,c)
