"""Hermetic transport/capture fixtures; no networking or actual journals."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import uuid
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from scripts.batch import harbor_profile_readiness as r
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_model_review_provenance import Call,Policy

def write(path,value):
    path.write_text(json.dumps(value));return record(path)

def journal(path,payload,value,policy,when):
    path.mkdir();q=write(path/'request.json',payload)
    response={'id':str(uuid.uuid4()),'model':policy.model,'object':'chat.completion','created':when+1,'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(value)}}]}
    raw=write(path/'response.json',response)
    submitted={'attempt_id':str(uuid.uuid4()),'submitted_at_ns':when*10**9,'endpoint':policy.endpoint,'project_id':policy.project_id,'model':policy.model,'reasoning_effort':policy.reasoning_effort,'semantic_budget_reset':False,'request':q}
    return Call(q,write(path/'submitted.json',submitted),raw,write(path/'completed.json',{'completed_at_ns':(when+2)*10**9,'response':raw}))

def fixture(tmp_path,change=None):
    validator,assembler=r.modules()
    profile=validator.valid_fixture()['profiles'][0]
    profile['capture']['evidence_sha256']=None
    timestamp=profile['capture']['attempted_at'];when=int(datetime.fromisoformat(timestamp.replace('Z','+00:00')).timestamp())
    body=tmp_path/'page.html';body.write_text('<html><title>Monthly indicators</title><p>Updated each month after the scheduled release.</p><a href="/next">Next</a></html>')
    cap={'schema_version':'harbor-public-capture/v1','requested_url':'https://www.example.com/','final_url':'https://www.example.com/','redirects':[],'status':200,'content_type':'text/html','started_at_ns':when*10**9,'completed_at_ns':(when+1)*10**9,'accessed_at':timestamp,'authentication':'none','body':record(body)}
    if change=='authentication':cap['authentication']='cookies'
    if change=='cross_origin':cap['final_url']='https://other.example.org/'
    if change=='barrier':body.write_text('Please sign in');cap['body']=record(body)
    if change=='private_context':body.write_text('Private Sheet1!B4');cap['body']=record(body)
    if change=='value_context':body.write_text('Sensitive 987654321');cap['body']=record(body)
    capture=write(tmp_path/'capture.json',cap)
    policy=Policy('profile-test-project')
    source={'source_name':'Example Statistics','source_kind':'statistics','canonical_url':'https://www.example.com/'}
    pages=[{k:cap[k] for k in ('requested_url','final_url','status','content_type','accessed_at')}|{'body':body.read_text()}]
    reviewer=copy.deepcopy(profile);reviewer['review']={'status':'accepted','reviewer':'independent reviewer','reviewed_at':datetime.fromtimestamp(when+7,timezone.utc).isoformat().replace('+00:00','Z'),'notes':'Source terminology and excerpts match the captured page.'}
    if change=='producer_accepted':profile['review']=copy.deepcopy(reviewer['review'])
    if change=='reviewer_prose':reviewer['terminology']=['invented metric']
    if change=='reviewer_identity':reviewer['source_id']='different-source'
    if change=='read_count':profile['capture']['read_count']=2;reviewer['capture']['read_count']=2
    if change=='excerpt':profile['evidence']['excerpts'][0]['text']='Not in source';reviewer['evidence']=copy.deepcopy(profile['evidence'])
    if change=='attribution':profile['evidence']['attributions'][0]['url']='https://www.example.com/unread';reviewer['evidence']=copy.deepcopy(profile['evidence'])
    producer=journal(tmp_path/'producer',r.request('producer',{'source':source,'public_pages':pages},policy),profile,policy,when+3)
    review=journal(tmp_path/'reviewer',r.request('reviewer',{'fragment':profile,'public_pages':pages},policy),reviewer,policy,when+6)
    if change=='not_independent':
        obj=json.loads(Path(review.response['path']).read_text());obj['id']=json.loads(Path(producer.response['path']).read_text())['id'];raw=write(Path(review.response['path']),obj)
        review=replace(review,response=raw,completed=write(Path(review.completed['path']),{'completed_at_ns':(when+8)*10**9,'response':raw}))
    call=r.ProfileCall(source['canonical_url'],producer,review,(capture,))
    return call,source,policy

def test_exact_public_capture_and_independent_review_pass(tmp_path):
    call,source,policy=fixture(tmp_path)
    result=r.accepted_profile(call,source,{'987654321'},policy=policy)
    assert result['accepted'] and result['model_calls']==2
    assert result['profile']['review']['status']=='accepted'
    assert len(result['evidence'])==10

@pytest.mark.parametrize('change',['authentication','cross_origin','barrier','private_context','value_context','producer_accepted','reviewer_prose','reviewer_identity','read_count','excerpt','attribution','not_independent'])
def test_invalid_capture_or_review_never_accepts(tmp_path,change):
    call,source,policy=fixture(tmp_path,change)
    with pytest.raises((ValueError,SystemExit)):
        r.accepted_profile(call,source,{'987654321'},policy=policy)

@pytest.mark.parametrize('url',['internal://supplied/source','https://user:pass@example.com/','http://localhost/','http://127.0.0.1/','https://sample.invalid/'])
def test_private_or_authenticated_urls_never_enter_worklist(url):
    assert r.worklist({'variables':[{'sources':[{'name':'Public name','kind':'statistics','url':url}]}]},set())==[]

def test_worklist_strips_private_fields_and_deduplicates():
    source={'name':'Example Statistics','kind':'statistics','url':'https://www.example.com/#fragment','audit_notes':'private','value':123,'question':'private question'}
    out=r.worklist({'variables':[{'sources':[source,source]}]},set())
    assert out==[{'source_name':'Example Statistics','source_kind':'statistics','canonical_url':'https://www.example.com/'}]

def test_empty_public_worklist_requires_empty_profiles_and_mapping(tmp_path):
    validator,_=r.modules()
    phase=write(tmp_path/'phase1.json',{'variables':[{'sources':[{'name':'Supplied workbook','kind':'internal','url':'internal://book'}]}]})
    inv=write(tmp_path/'inventory.json',{})
    doc=validator.valid_fixture();doc['profiles']=[];validator.stamp_hashes(doc,Path(inv['path']),Path(phase['path']))
    profiles=write(tmp_path/'profiles.json',doc)
    mapping=write(tmp_path/'mapping.json',{'schema_version':'harbor-source-profile-mapping/v1','phase_1_spec_sha256':phase['sha256'],'profiles_sha256':profiles['sha256'],'mappings':[]})
    selection=r.ProfileSelection(phase,inv,profiles,mapping,(),Policy('profile-test-project'))
    result=r.validate_profiles(selection)
    assert result['public_sources']==0 and result['model_calls']==0 and result['confidence']=='high'
    bad=write(tmp_path/'bad-mapping.json',{**json.loads(Path(mapping['path']).read_text()),'mappings':[{'profile_id':'fake'}]})
    with pytest.raises(ValueError):r.validate_profiles(replace(selection,mapping=bad))

@pytest.mark.parametrize('change',[None,'missing_mapping','unknown_profile','alter_profile','budget_reset','changed_body'])
def test_full_public_document_and_mapping_are_exact(tmp_path,change):
    call,source,policy=fixture(tmp_path)
    result=r.accepted_profile(call,source,set(),policy=policy)
    validator,_=r.modules()
    phase=write(tmp_path/'phase1.json',{'variables':[{'sources':[{'id':'example-statistics','name':source['source_name'],'kind':source['source_kind'],'url':source['canonical_url']}]}]})
    inv=write(tmp_path/'inventory.json',{})
    doc=validator.valid_fixture();doc['profiles']=[result['profile']]
    if change=='alter_profile':doc['profiles'][0]['terminology']=['unreviewed']
    validator.stamp_hashes(doc,Path(inv['path']),Path(phase['path']))
    profiles=write(tmp_path/'profiles.json',doc)
    rows=[{'normalized_source_id':'example-statistics','canonical_url':source['canonical_url'],'profile_id':'example-statistics','dataset_key':'monthly-indicators'}]
    if change=='missing_mapping':rows=[]
    if change=='unknown_profile':rows[0]['profile_id']='unknown'
    mapping=write(tmp_path/'mapping.json',{'schema_version':'harbor-source-profile-mapping/v1','phase_1_spec_sha256':phase['sha256'],'profiles_sha256':profiles['sha256'],'mappings':rows})
    if change=='budget_reset':
        submitted=json.loads(Path(call.producer.submitted['path']).read_text());submitted['semantic_budget_reset']=True
        call=replace(call,producer=replace(call.producer,submitted=write(Path(call.producer.submitted['path']),submitted)))
    if change=='changed_body':Path(json.loads(Path(call.captures[0]['path']).read_text())['body']['path']).write_text('changed')
    selection=r.ProfileSelection(phase,inv,profiles,mapping,(call,),policy)
    if change is None:
        actual=r.validate_profiles(selection)
        assert actual['accepted_profiles']==1 and actual['model_calls']==2 and actual['mapping']['mappings']==rows
    else:
        with pytest.raises(ValueError):r.validate_profiles(selection)

@pytest.mark.parametrize('url,allowed',[('https://www.example.com/next',True),('https://www.example.com/not-linked',False),('https://other.example.org/next',False)])
def test_secondary_public_reads_require_actual_same_origin_link(tmp_path,url,allowed):
    call,source,policy=fixture(tmp_path)
    first=json.loads(Path(call.captures[0]['path']).read_text()); second=copy.deepcopy(first)
    second.update(requested_url=url,final_url=url,started_at_ns=first['completed_at_ns']+1,completed_at_ns=first['completed_at_ns']+2)
    second['accessed_at']=datetime.fromtimestamp(first['completed_at_ns']/10**9,timezone.utc).isoformat().replace('+00:00','Z')
    second_binding=write(tmp_path/'second-capture.json',second)
    if allowed:
        pages,evidence=r.captured_pages((*call.captures,second_binding),source['canonical_url'],set())
        assert len(pages)==2 and len(evidence)==4
    else:
        with pytest.raises(ValueError):r.captured_pages((*call.captures,second_binding),source['canonical_url'],set())

def test_four_reads_rejected_before_any_profile_acceptance(tmp_path):
    call,source,policy=fixture(tmp_path)
    with pytest.raises(ValueError,match='budget'):
        r.captured_pages(call.captures*4,source['canonical_url'],set())


def composed(tmp_path,workbook):
    from scripts.batch.harbor_readiness import Selection
    from scripts.batch.harbor_complete_readiness import ReadinessSelection
    run=tmp_path/(workbook+'-variable-sources');run.mkdir()
    pipeline={kind+'_'+field: ('a' if kind=='source' else 'b')*64 for kind in ('source','segmentation') for field in ('generation_id','manifest_sha256')}
    value={'environment_id':'fcp-'+workbook,'pipeline_bindings':pipeline,'variables':[{'sources':[{'name':'Supplied workbook','kind':'internal','url':'internal://book'}]}]}
    phase=write(run/'phase1.json',value);inventory=write(run/'inventory.json',{})
    validator,_=r.modules();doc=validator.valid_fixture();doc['profiles']=[];validator.stamp_hashes(doc,Path(inventory['path']),Path(phase['path']))
    profiles=write(run/'source_profiles.json',doc)
    mapping=write(run/'mapping.json',{'schema_version':'harbor-source-profile-mapping/v1','phase_1_spec_sha256':phase['sha256'],'profiles_sha256':profiles['sha256'],'mappings':[]})
    final=write(run/'normalized.json',{**value,'source_profiles':[]})
    policy=Policy('profile-test-project');profile=r.ProfileSelection(phase,inventory,profiles,mapping,(),policy)
    bindings={'normalized_phase1':phase,'variable_source_inventory':inventory,'source_profiles':profiles,'source_profile_mapping':mapping,'normalized_final':final,
        **{kind+'_generation':{'id':pipeline[kind+'_generation_id'],'manifest_sha256':pipeline[kind+'_manifest_sha256']} for kind in ('source','segmentation')}}
    content=Selection(workbook,tmp_path/'stage','d'*64,bindings,run,tmp_path/'nat',{}, {},model_policy=policy)
    return ReadinessSelection(content,None,None,None,profile)


@pytest.mark.parametrize('change',[None,'foreign_bundle','foreign_run','foreign_workbook','policy','pipeline','catalog','detached_mapping'])
def test_composed_profile_selection_is_bound_to_exact_task(tmp_path,change):
    from scripts.batch.harbor_complete_readiness import check_profiles
    selection=composed(tmp_path,'0001');other=composed(tmp_path,'0002')
    if change=='foreign_bundle':selection=replace(selection,public_profiles=other.public_profiles)
    elif change=='foreign_run':selection=replace(selection,content=replace(selection.content,normalization_run=other.content.normalization_run))
    elif change=='foreign_workbook':selection=replace(selection,content=replace(selection.content,workbook='0002'))
    elif change=='policy':selection=replace(selection,public_profiles=replace(selection.public_profiles,policy=Policy('foreign-project')))
    elif change in {'pipeline','catalog'}:
        path=Path(selection.content.bindings['normalized_final']['path']);value=json.loads(path.read_text())
        if change=='pipeline':value['pipeline_bindings']['source_generation_id']='f'*64
        else:value['source_profiles']=[{'unreviewed':'catalog'}]
        selection.content.bindings['normalized_final']=write(path,value)
    elif change=='detached_mapping':selection.content.bindings['source_profile_mapping']=other.public_profiles.mapping
    assert r.validate_profiles(other.public_profiles)['confidence']=='high'
    if change is None:assert check_profiles(selection)['confidence']=='high'
    else:
        with pytest.raises(ValueError):check_profiles(selection)


@pytest.mark.parametrize('change',[None,'bundle','run','policy','phase1'])
def test_profile_lane_context_is_bound_before_final_spec(tmp_path,change):
    from scripts.batch.harbor_completed_lanes import profile_context
    selected=composed(tmp_path,'0001');other=composed(tmp_path,'0002')
    p=selected.public_profiles;s=selected.content
    if change=='bundle':p=other.public_profiles
    elif change=='run':s=replace(s,normalization_run=other.content.normalization_run)
    elif change=='policy':p=replace(p,policy=Policy('foreign-project'))
    elif change=='phase1':p=replace(p,phase1=other.public_profiles.phase1)
    if change is None:profile_context(p,s)
    else:
        with pytest.raises(ValueError):profile_context(p,s)
