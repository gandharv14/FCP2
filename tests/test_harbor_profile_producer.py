import urllib.error
"""Synthetic retained captures and in-memory HTTP only; real executor journals."""
import copy
from dataclasses import asdict, replace
from datetime import datetime,timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import uuid

import pytest

from scripts.batch import harbor_profile_readiness as R
from scripts.batch import harbor_profile_producer as P
E=P.executor


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E.canonical(value));return E.record(path)


def timestamp(when=None):
    return datetime.fromtimestamp(time.time() if when is None else when,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@pytest.fixture
def environment(tmp_path,monkeypatch):
    tmp_path=tmp_path.resolve()
    def forbidden(*args,**kwargs):raise AssertionError('No actual HTTP allowed in profile tests')
    monkeypatch.setattr(E,'_open',forbidden)
    campaign=E.Campaign(tmp_path/'journals',tmp_path/'slots')
    monkeypatch.setenv('HARBOR_MODEL_SLOT_ROOT',str(campaign.slot_root));monkeypatch.setenv('HARBOR_MODEL_SLOTS','2')
    def fixture(kind='public',change=None):
        run=tmp_path/'0001-variable-sources';run.mkdir(exist_ok=True)
        raw=tmp_path/'0001.xlsx';raw.write_bytes(b'Synthetic original identity; never opened as workbook')
        source={'id':'audit-source-private-opaque','name':'Example Statistics','kind':'statistics','url':'https://www.example.com/','audit_notes':'Sensitive private derivation','role':'primary'}
        if kind=='internal':source.update(id='internal-source',name='Supplied workbook',kind='internal',url='internal://book')
        spec={'environment_id':'fcp-0001','variables':[{'sources':[source],'value':987654321,'question':'Private Sheet1!B4'}]}
        phase=write(run/'normalized.phase1.json',spec);inv=write(tmp_path/'inventory.json',{'private':'retained only'})
        profile=R.modules()[0].valid_fixture()['profiles'][0];profile['capture']['evidence_sha256']=None
        when=int(time.time())-60;stamp=timestamp(when)
        profile['capture']['attempted_at']=stamp
        for attr in profile['evidence']['attributions']:attr['accessed_at']=stamp
        body=tmp_path/'page.html';body.write_text('<html><title>Monthly indicators</title><p>Updated each month after the scheduled release.</p><a href="/next">Next</a></html>')
        capture={'schema_version':'harbor-public-capture/v1','requested_url':'https://www.example.com/','final_url':'https://www.example.com/','redirects':[],'status':200,'content_type':'text/html','started_at_ns':when*10**9,'completed_at_ns':(when+1)*10**9,'accessed_at':stamp,'authentication':'none','body':E.record(body)}
        if change=='401':capture['status']=401
        if change=='403':capture['status']=403
        if change=='unreachable':capture['status']=None
        if change=='unsupported_content':capture['content_type']='application/pdf'
        if change in {'bot_challenge','paywall','auth_login_sso_password'}:
            body.write_text({'bot_challenge':'captcha','paywall':'subscriber-only','auth_login_sso_password':'Please sign in'}[change]);capture['body']=E.record(body)
        if change=='foreign':capture['final_url']='https://other.example.org/'
        if change=='private':body.write_text('Leaked Sheet1!B4');capture['body']=E.record(body)
        if change=='value':body.write_text('Private 987654321');capture['body']=E.record(body)
        if change=='malformed':capture.pop('redirects')
        cap=write(tmp_path/'capture.json',capture)
        captures=write(tmp_path/'captures.json',{'schema_version':'harbor-profile-captures/v1','sources':[] if kind=='internal' else [{'canonical_url':source['url'],'captures':[cap]}]})
        policy=P.Policy('fixture-project')
        inp=write(tmp_path/'input.json',{'schema_version':P.INPUT_SCHEMA,'workbook_id':'0001','original_source':E.record(raw),'phase1':phase,'inventory':inv,'captures':captures,'policy':asdict(policy),'campaign':{'root':str(campaign.root),'slot_root':str(campaign.slot_root),'model_slots':2}})
        return inp,profile,campaign
    return fixture,tmp_path


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def transport(monkeypatch,profile,change=None):
    seen=[]
    def opened(request,timeout):
        payload=json.loads(request.data);context=json.loads(payload['messages'][1]['content']);seen.append(payload)
        if 'fragment' not in context:
            value=copy.deepcopy(profile)
            if change=='producer_invalid':value['review']['status']='accepted'
            if change=='producer_private':value['terminology']=['Sheet1!B4']
            if change=='producer_excerpt':value['evidence']['excerpts'][0]['text']='Not on page'
        else:
            value=copy.deepcopy(context['fragment'])
            value['review']={'status':'rejected' if change=='rejected' else 'accepted','reviewer':'Independent fixture reviewer','reviewed_at':timestamp(),'notes':'Terminology and evidence match the retained synthetic public page.'}
            if change=='review_prose':value['terminology']=['Unreviewed edit']
            if change=='review_schema':value.pop('review')
        raw={'id':str(uuid.uuid4()),'model':payload['model'],'object':'chat.completion','created':int(time.time()),'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(value)}}]}
        return Response(json.dumps(raw).encode())
    monkeypatch.setattr(E,'_open',opened)
    return seen


def test_internal_zero_calls_owning_assembly_and_validation(environment):
    fixture,root=environment;inp,_,_=fixture('internal');plan=P.prepare(inp,root/'prepared')
    result=P.run(plan)
    assert result['status']=='COMPLETE' and result['model_calls']==0
    assert R.validate_profiles(result['selection'])['confidence']=='high'
    assert not (root/'journals').exists()
    assert P.run(plan)['receipt']==result['receipt']


def test_public_preparation_has_no_private_context_or_dispatch(environment):
    fixture,root=environment;inp,_,_=fixture();plan=P.prepare(inp,root/'prepared');value=P.read(plan)
    request=P.read(value['jobs'][0]['producer_request'])
    assert set(json.loads(request['messages'][1]['content']))=={'source','public_pages'}
    text=json.dumps(request)
    for private in ['audit-source-private-opaque','Sensitive private derivation','Sheet1!B4','987654321']:
        assert private not in text
    assert P.run(plan)['status']=='PENDING_PRODUCER'
    assert not (root/'journals').exists()


def test_public_real_executor_journals_and_distinct_mapping(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='COMPLETE',result
    assert len(seen)==2 and result['model_calls']==2
    selection=result['selection'];assert R.validate_profiles(selection)['confidence']=='high'
    row=P.read(selection.mapping)['mappings'][0]
    assert row['normalized_source_id']=='audit-source-private-opaque' and row['profile_id']=='example-statistics'
    assert P.run(plan,dispatch=True,credential='synthetic-test-token')['receipt']==result['receipt'] and len(seen)==2
    for call in (selection.calls[0].producer,selection.calls[0].reviewer):
        assert Path(call.submitted['path']).exists() and Path(call.completed['path']).exists()
        assert b'synthetic-test-token' not in Path(call.submitted['path']).read_bytes()


@pytest.mark.parametrize('change,expected',[('401','http_401'),('403','http_403'),('unreachable','unreachable'),('unsupported_content','unsupported_content'),('bot_challenge','bot_challenge'),('paywall','paywall'),('auth_login_sso_password','auth_login_sso_password'),('foreign','invalid_capture_or_redaction'),('private','invalid_capture_or_redaction'),('value','invalid_capture_or_redaction'),('malformed','invalid_capture_or_redaction')])
def test_access_or_invalid_capture_stops_before_requests(environment,change,expected):
    fixture,root=environment;inp,_,_=fixture(change=change);plan=P.prepare(inp,root/'prepared')
    value=P.read(plan);assert value['jobs']==[] and value['blocked'][0]['code']==expected
    assert P.run(plan,dispatch=True,credential='synthetic-test-token')['status']=='STOPPED'
    assert not (root/'journals').exists()


@pytest.mark.parametrize('change,calls',[('producer_invalid',1),('producer_private',1),('producer_excerpt',2),('review_prose',2),('review_schema',2),('rejected',2)])
def test_bad_producer_or_reviewer_no_acceptance_or_automatic_semantic_retry(environment,monkeypatch,change,calls):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile,change)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='STOPPED' and len(seen)==calls,result
    again=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert again==result and len(seen)==calls
    assert not (root/'0001-variable-sources/source_profiles.json').exists()


@pytest.mark.parametrize('changed',['capture','phase','inventory','source','request','code'])
def test_changed_evidence_never_dispatches(environment,changed):
    fixture,root=environment;inp,_,_=fixture();plan=P.prepare(inp,root/'prepared');value=P.read(plan);inputs=P.read(inp)
    paths={'capture':root/'page.html','phase':Path(inputs['phase1']['path']),'inventory':Path(inputs['inventory']['path']),'source':Path(inputs['original_source']['path']),'request':Path(value['jobs'][0]['producer_request']['path'])}
    if changed=='code':
        value['code']=value['code'][:-1];plan=write(root/'changed-plan.json',value)
    else:paths[changed].write_text('changed')
    with pytest.raises(ValueError):P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert not (root/'journals').exists()


def test_new_output_directory_cannot_reset_spent_attempt(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile,'producer_invalid')
    assert P.run(plan,dispatch=True,credential='synthetic-test-token')['status']=='STOPPED'
    new=P.prepare(inp,root/'new-preparation')
    with pytest.raises(ValueError,match='identity/configuration/budget'):P.run(new,dispatch=True,credential='synthetic-test-token')
    assert len(seen)==1


def test_unknown_completion_never_replayed(environment,monkeypatch):
    fixture,root=environment;inp,_,_=fixture();plan=P.prepare(inp,root/'prepared');seen=[]
    def fail(*args,**kwargs):seen.append(1);raise urllib.error.URLError('opaque synthetic delivery state')
    monkeypatch.setattr(E,'_open',fail)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='STOPPED'
    assert P.run(plan,dispatch=True,credential='synthetic-test-token')['status']=='STOPPED' and len(seen)==1


@pytest.mark.parametrize('where',['merge','second_output'])
def test_interrupted_assembly_or_two_file_publish_resumes_exactly(environment,monkeypatch,where):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    if where=='second_output':
        original=E.retain
        def interrupted(path,raw):
            if Path(path)==root/'0001-variable-sources/source_profile_mapping.json':raise KeyboardInterrupt()
            return original(path,raw)
        monkeypatch.setattr(E,'retain',interrupted)
    else:
        original=R.modules
        validator,assembler=original();merge=assembler.cmd_merge
        def stop(*args):raise KeyboardInterrupt()
        monkeypatch.setattr(assembler,'cmd_merge',stop)
        monkeypatch.setattr(R,'modules',lambda *args:(validator,assembler))
    with pytest.raises(KeyboardInterrupt):P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert not (root/'prepared/complete.json').exists()
    if where=='second_output':monkeypatch.setattr(E,'retain',original)
    else:monkeypatch.setattr(assembler,'cmd_merge',merge)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='COMPLETE' and len(seen)==2


def test_ambiguous_dataset_mapping_is_closed(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();profile['dataset_names']=['Monthly indicators','Separate dataset'];plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='STOPPED' and result['code']=='profile_mapping_or_final_validation'
    assert len(seen)==2 and not (root/'0001-variable-sources/source_profiles.json').exists()


def test_foreign_mapping_and_duplicate_ids_reject(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token');selected=result['selection']
    value=P.read(selected.mapping);value['mappings'][0]['canonical_url']='https://other.example.org/'
    wrong=write(root/'wrong.json',value)
    with pytest.raises(ValueError):R.validate_profiles(replace(selected,mapping=wrong))
    spec=P.read(selected.phase1);source=copy.deepcopy(spec['variables'][0]['sources'][0]);source['url']='https://other.example.org/';spec['variables'][0]['sources'].append(source)
    doc=P.read(selected.profiles);other=copy.deepcopy(doc['profiles'][0]);other['canonical_url']=source['url'];other['source_id']='second-profile';doc['profiles'].append(other)
    with pytest.raises(ValueError,match='ambiguous normalized'):P.mapping(spec,doc,'a'*64,'b'*64)


def test_deduplicated_public_url_retains_multiple_private_source_ids(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();value=P.read(inp);spec=P.read(value['phase1'])
    other=copy.deepcopy(spec['variables'][0]['sources'][0]);other['id']='other-private-source-id';other['audit_notes']='Separate private provenance'
    spec['variables'][0]['sources'].append(other);value['phase1']=write(Path(value['phase1']['path']),spec);inp=write(Path(inp['path']),value)
    plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert result['status']=='COMPLETE' and len(seen)==2
    rows=P.read(result['selection'].mapping)['mappings'];assert len(rows)==2
    assert {r['profile_id'] for r in rows}=={'example-statistics'}
    assert 'other-private-source-id' not in json.dumps(seen)


@pytest.mark.parametrize('change',['endpoint','model','reasoning','foreign_workbook','extra_capture','missing_capture'])
def test_bad_prepared_root_context_is_rejected_without_any_request(environment,change):
    fixture,root=environment;inp,_,_=fixture();value=P.read(inp)
    if change in {'endpoint','model','reasoning'}:value['policy'][{'reasoning':'reasoning_effort'}.get(change,change)]='changed'
    elif change=='foreign_workbook':value['workbook_id']='0042'
    else:
        caps=P.read(value['captures'])
        if change=='extra_capture':caps['sources'].append(caps['sources'][0])
        else:caps['sources']=[]
        value['captures']=write(Path(value['captures']['path']),caps)
    inp=write(Path(inp['path']),value)
    with pytest.raises(ValueError):P.prepare(inp,root/'prepared')
    assert not (root/'prepared').exists() and not (root/'journals').exists()


def test_arbitrary_workbook_identity_and_private_artifact_root(environment):
    fixture,root=environment;inp,_,_=fixture('internal');value=P.read(inp)
    old=Path(value['phase1']['path']);run=root/'custom-root'/'0473-variable-sources';run.mkdir(parents=True)
    spec=P.read(value['phase1']);spec['environment_id']='fcp-0473'
    value['phase1']=write(run/'frozen-phase1.json',spec);value['workbook_id']='0473';inp=write(Path(inp['path']),value)
    result=P.run(P.prepare(inp,root/'prepared'))
    assert result['status']=='COMPLETE' and result['model_calls']==0
    assert Path(result['selection'].profiles['path']).parent==run


def test_changed_retained_response_rejected_on_resume(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token')
    response=Path(result['selection'].calls[0].producer.response['path']);response.write_text('{}')
    with pytest.raises(ValueError):P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert len(seen)==2


def test_unrelated_partial_assembler_profile_not_repaired(environment,monkeypatch):
    fixture,root=environment;inp,profile,_=fixture();plan=P.prepare(inp,root/'prepared');seen=transport(monkeypatch,profile)
    result=P.run(plan,dispatch=True,credential='synthetic-test-token');candidate=root/'prepared/assembled-source-profiles.json'
    doc=json.loads(candidate.read_bytes());doc['profiles'][0]['terminology']=['Unreviewed partial contents']
    validator,_=R.modules();validator.stamp_hashes(doc,Path(result['selection'].inventory['path']),Path(result['selection'].phase1['path']));write(candidate,doc)
    with pytest.raises(ValueError,match='unrelated'):P.run(plan,dispatch=True,credential='synthetic-test-token')
    assert len(seen)==2
