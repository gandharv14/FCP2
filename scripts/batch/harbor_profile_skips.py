"""Verify a stopped public HTTP access barrier without extracting source details.

This narrow path accepts one canonical unauthenticated 401/403 exchange. Other
failures remain stopped. The original capture journal and false/absent model
review are retained; a skip is permission for generic rendering only.
"""
from pathlib import Path
import hashlib
from scripts.batch.harbor_admission import read,record,require
from scripts.batch import harbor_public_capture as capture


def display_name(source):
    """Keep long source descriptions in provenance, not the short display field."""
    from urllib.parse import urlsplit
    name='; '.join(source['reviewed_source_names'])if 'reviewed_source_names'in source else source['source_name']
    require(isinstance(name,str),'source name must be text')
    if name.strip() and len(name)<=160:return name
    host=urlsplit(source['canonical_url']).hostname or ''
    return 'Skipped public source: '+host if host and len(host)<=137 else 'Skipped public source'


def validate(source,bindings,forbidden,*,phase1,inventory,reader=None):
    require(isinstance(bindings,(tuple,list)) and len(bindings)==1,'one completed barrier capture required')
    row=bindings[0];cap=read(row);root=Path(row['path']).parent
    require(set(cap)=={'schema_version','requested_url','final_url','redirects','status','content_type','started_at_ns','completed_at_ns','accessed_at','authentication','body','projection','projection_policy_sha256','http_exchanges'},'closed barrier capture required')
    url=source['canonical_url'];require(cap['schema_version']==capture.CAPTURE and cap['authentication']=='none' and cap['projection_policy_sha256']==capture.policy_hash(),'barrier capture policy differs')
    require(cap['requested_url']==cap['final_url']==capture.allowed(url,url,forbidden) and cap['redirects']==[],'only original canonical access barrier supported')
    require(type(cap['status'])is int and cap['status']in(401,403),'observed terminal HTTP access barrier required')
    require(type(cap['started_at_ns'])is int and type(cap['completed_at_ns'])is int and 0<cap['started_at_ns']<=cap['completed_at_ns'] and cap['accessed_at']==capture.stamp(cap['started_at_ns']),'barrier chronology differs')
    reason='http_'+str(cap['status']);outcome_binding=record(root/'outcome.json');outcome=read(outcome_binding)
    require(set(outcome)=={'schema_version','status','reason','captures','http_exchanges_used','automatic_retry','artifacts'} and outcome['schema_version']=='harbor-public-capture-outcome/v1' and outcome['status']=='STOPPED' and outcome['reason']==reason and outcome['automatic_retry']is False,'actual stopped capture required')
    require(outcome['captures']==list(bindings) and type(outcome['http_exchanges_used'])is int and outcome['http_exchanges_used']==1,'barrier read budget differs')
    actual={p.name:record(p)['sha256']for p in root.iterdir()if p.is_file()and p.name not in{'scope.lock','outcome.json'}}
    require(outcome['artifacts']==actual and {p.name for p in root.glob('http-*.intent.json')}=={'http-01.intent.json'} and {p.name for p in root.glob('http-*.result.json')}=={'http-01.result.json'},'capture attempt has omitted or changed evidence')
    plan_binding=record(root/'plan.json');plan=read(plan_binding)
    if reader is None:
        from scripts.batch import harbor_profile_readiness as reader
    require(plan.get('schema_version')=='harbor-public-capture-plan/v1' and reader.same_captured_source(source,plan['source']) and plan['policy_sha256']==capture.policy_hash(),'barrier source differs')
    require(plan['selection']['phase1']==phase1 and plan['selection']['inventory']==inventory and read(phase1).get('environment_id')=='fcp-'+plan['selection']['workbook_id'],'barrier capture belongs to another source selection')
    receipt_binding=record(root/'http-01.result.json');require(cap['http_exchanges']==[receipt_binding],'foreign HTTP response')
    response=read(receipt_binding);intent_binding=record(root/'http-01.intent.json');require(response['intent']==intent_binding,'foreign HTTP intent')
    intent=read(intent_binding)
    require(set(intent)=={'schema_version','url','method','started_at_ns','policy_sha256','authentication','cookies'} and intent==dict(schema_version='harbor-public-http-intent/v1',url=url,method='GET',started_at_ns=intent['started_at_ns'],policy_sha256=capture.policy_hash(),authentication='none',cookies=False),'HTTP request not original public GET')
    require(set(response)=={'schema_version','status','content_type','content_encoding','body_complete','content_length','location','addresses','connected_address','intent','body','completed_at_ns'} and response['schema_version']=='harbor-public-http-result/v1','closed actual response required')
    require(cap['started_at_ns']<=intent['started_at_ns']<=response['completed_at_ns']==cap['completed_at_ns'] and response['status']==cap['status'] and response['body']==cap['body'],'HTTP response differs from capture')
    require(response['addresses'] and response['connected_address']in response['addresses'] and all(capture.public_address(a)for a in response['addresses']),'HTTP address is not public')
    raw=Path(cap['body']['path']).read_bytes();require(record(cap['body']['path'])==cap['body'] and response['body_complete']is True and capture.complete_body(raw,response['content_length']),'incomplete or changed barrier response')
    projection=capture.project(raw,content_type=response['content_type'],forbidden=forbidden,status=response['status'],content_encoding=response['content_encoding'])
    require(read(cap['projection'])==projection and cap['content_type']==projection['content_type'] and projection['disposition']==reason and projection['text']=='' and projection['links']==[],'barrier projection extracted content or changed')
    fragment=dict(source_id='skipped-'+hashlib.sha256(url.encode()).hexdigest()[:24],source_name=display_name(source),canonical_url=url,status='skipped',skip_reason=reason,capture=dict(attempted_at=cap['accessed_at'],read_count=1,http_status=cap['status'],final_url=url,content_type=cap['content_type'],evidence_sha256=None),terminology=[],dataset_names=[],field_conventions=[],document_types=[],release_cadence=None,evidence=dict(attributions=[],excerpts=[]),review=dict(status='pending',reviewer=None,reviewed_at=None,notes=None))
    return dict(profile=fragment,reason=reason,model_calls=0,evidence=[outcome_binding,plan_binding,row,receipt_binding,intent_binding,cap['body'],cap['projection'],record(Path(__file__)),record(Path(capture.__file__))])


MARKER='harbor-verified-public-profile-skip/v1'
def marker(tracker,selection):
    """Typed evidence for a medium public-profile lane; no financial PASS."""
    from scripts.batch import harbor_profile_readiness as profiles,harbor_transition_codec as codec
    value=dict(schema_version=MARKER,workbook=tracker['workbook_id'],source=tracker['raw_source'],
        policy=[record(Path(__file__)),*[record(Path(m.__file__))for m in (capture,profiles,codec)]],selection=codec.encode(selection))
    validate_marker(tracker,value)
    return value


def validate_marker(tracker,value):
    from scripts.batch import harbor_profile_readiness as profiles,harbor_transition_codec as codec
    require(isinstance(value,dict)and set(value)=={'schema_version','workbook','source','policy','selection'}
        and value['schema_version']==MARKER and value['workbook']==tracker['workbook_id']
        and value['source']==tracker['raw_source'],'foreign verified profile skip marker')
    require(value['policy']==[record(Path(__file__)),*[record(Path(m.__file__))for m in (capture,profiles,codec)]],'profile skip policy bytes differ')
    selected=codec.decode(value['selection']);require(isinstance(selected,profiles.ProfileSelection) and selected.skips,'typed actual skipped profile selection required')
    for attribute,key in (('phase1','normalized_phase1'),('inventory','variable_source_inventory'),('profiles','source_profiles'),('mapping','source_profile_mapping')):
        require(getattr(selected,attribute)==tracker['bindings'][key],'profile skip marker differs from tracker: '+key)
    phase=read(selected.phase1);require(phase.get('environment_id')=='fcp-'+tracker['workbook_id'],'profile skip belongs to another workbook')
    result=profiles.validate_profiles(selected)
    require(result['confidence']=='medium' and result['skipped_profiles']>0 and result['accepted_profiles']+result['skipped_profiles']==result['public_sources'],'verified skip must preserve medium generic confidence')
    require(tracker['lane_state']['harbor-source-profiler']['current_confidence']=='medium'
        and all(row['state']=='passed' and row['current_confidence']==('medium'if lane=='harbor-source-profiler'else'high')for lane,row in tracker['lane_state'].items()),'only public-profile confidence may be medium after all lanes pass')
    return True
