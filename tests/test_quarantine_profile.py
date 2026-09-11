from pathlib import Path
import base64
import copy
import importlib.util
import json
import shutil
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import xl_table_recalc_quarantine as q
spec=importlib.util.spec_from_file_location('table_fixture',Path(__file__).with_name('test_table_recalc_observation.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

@pytest.fixture
def case(tmp_path,monkeypatch):
    private=Ed25519PrivateKey.generate();public=tmp_path/'public.pem'
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));public.chmod(0o600)
    protected=q.recalc._trusted_public_key
    # Only synthetic key ownership is relaxed. Real Ed25519 verification stays intact.
    monkeypatch.setattr(q.recalc,'_trusted_public_key',lambda p:protected(p,require_root_owner=False))
    source=f.fixture(tmp_path/'original.xlsx');stage=tmp_path/'staged.xlsx';output=tmp_path/'output.xlsx'
    producer='a'*64;now=time.time_ns()-10**9
    request=q.make_request(source,workbook='synthetic',output=output,producer_sha256=producer,public_key=public,engine_version='16.test',created_at_ns=now)
    shutil.copyfile(source,stage)
    f.edit(stage,lambda tree,parts:[f.set_value(f.cells(tree)[c],v) for c,v in {'B1':'7','D3':'15','E3':'19','D4':'20','E4':'21'}.items()])
    shutil.copyfile(source,output);q.observer.graft_observed_data_tables(stage,output)
    args=dict(source=source,staged=stage,output=output,producer_sha256=producer,public_key=public)
    claims=q.prepare_unsigned_claims(request,**args,engine_version='16.test',started_at_ns=now+1,completed_at_ns=now+2,isolation_controls={k:True for k in q.observer.REQUIRED_CONTROLS})
    def signed(payload=claims):
        receipt={'schema_version':q.RECEIPT,'signature_algorithm':'ed25519','signed_payload':copy.deepcopy(payload),'signature_base64':base64.b64encode(private.sign(q.canonical(payload))).decode()}
        evidence=q._validated_evidence(request,**args,engine_version='16.test',started_at_ns=now+1,completed_at_ns=now+2,isolation_controls={k:True for k in q.observer.REQUIRED_CONTROLS})
        _,derived=q._split_evidence(evidence,**{k:args[k] for k in ('source','staged','output')})
        result={'schema_version':q.RESULT,'request_sha256':request['request_sha256'],'receipt':receipt,'derived_evidence':derived};result['result_sha256']=q.digest(result)
        return result
    return dict(request=request,args=args,claims=claims,sign=signed,result=signed(),private=private)

def rehash_request(r):r['request_sha256']=q.digest({k:v for k,v in r.items() if k!='request_sha256'})
def rehash_result(r):r['result_sha256']=q.digest({k:v for k,v in r.items() if k!='result_sha256'})

def test_signed_quarantine_validates_but_is_never_an_ordinary_receipt(case):
    evidence=q.validate_result(case['result'],case['request'],**case['args'])
    assert evidence['scope']=='quarantine_only' and evidence['source_approval'] is False and evidence['task_approval'] is False
    assert evidence['original_sha256']!=evidence['intermediate_sha256']
    with pytest.raises(q.recalc.RecalculationError,match='schema'):
        q.recalc.verify_signed_runner_receipt(case['result']['receipt'],case['args']['public_key'],request_sha256=case['request']['request_sha256'],source_sha256=evidence['original_sha256'],output_sha256=evidence['intermediate_sha256'])
    assert q.health.inspect_workbook(case['args']['output'])['route']=='unsupported'

@pytest.mark.parametrize('field,value',[('source_approval',1),('task_approval',True),('scope','source'),('schema_version','source-recalc-request/v2'),('source_sha256','b'*64),('source_size_bytes',1),('output_path','/tmp/unbound.xlsx'),('original_snapshot_sha256','c'*64),('original_health_sha256','d'*64),('created_at_ns',True),('engine_version',''),('request_id','forged'),('workbook','../escape')])
def test_rehashed_forged_request_rejected(case,field,value):
    r=copy.deepcopy(case['request']);r[field]=value;rehash_request(r)
    with pytest.raises((q.QuarantineError,q.recalc.RecalculationError)):
        q.validate_request(r,**{k:v for k,v in case['args'].items() if k!='staged'})

@pytest.mark.parametrize('field',['calculation_complete','isolation_enforced','source_approval','task_approval','source_sha256','output_sha256','staging_sha256','original_observation_sha256','staging_observation_sha256','output_observation_sha256','source_path','staging_path','output_path','public_key_sha256','request_sha256','profile','schema_version','network_isolation_mechanism','network_isolation_rules_sha256','selected_policy_code'])
def test_even_valid_signature_cannot_bless_altered_claims(case,field):
    claims=copy.deepcopy(case['claims']);claims[field]=1 if isinstance(claims[field],bool) else 'forged'
    with pytest.raises(q.QuarantineError):q.validate_result(case['sign'](claims),case['request'],**case['args'])

@pytest.mark.parametrize('control',q.observer.REQUIRED_CONTROLS)
@pytest.mark.parametrize('change',['missing','integer','false'])
def test_every_signed_control_is_required_true_boolean(case,control,change):
    claims=copy.deepcopy(case['claims'])
    if change=='missing':del claims['isolation_controls'][control]
    else:claims['isolation_controls'][control]=1 if change=='integer' else False
    with pytest.raises(q.QuarantineError):q.validate_result(case['sign'](claims),case['request'],**case['args'])

@pytest.mark.parametrize('role',['source','staged','output'])
@pytest.mark.parametrize('change',['value','missing'])
def test_altered_or_missing_actual_file_rejects(case,role,change):
    p=case['args'][role]
    if change=='missing':p.unlink()
    else:f.edit(p,lambda t,parts:f.set_value(f.cells(t)['E3'],'123'))
    with pytest.raises((q.QuarantineError,q.observer.RunnerError)):
        q.validate_result(case['result'],case['request'],**case['args'])

@pytest.mark.parametrize('field',['primitive','formula','geometry','incomplete','format','calculation'])
def test_staging_mutations_cannot_hide_behind_restored_original_output(case,field):
    p=case['args']['staged']
    def alter(tree,parts):
        cells=f.cells(tree)
        if field=='primitive':f.set_value(cells['A1'],'17')
        elif field=='formula':cells['B1'].find('{*}f').text='A1*9'
        elif field=='geometry':cells['D3'].find('{*}f').set('ref','D3:F4')
        elif field=='incomplete':cells['E3'].remove(cells['E3'].find('{*}v'))
        elif field=='format':
            root=ET.fromstring(parts['xl/styles.xml']);root.find('.//{*}numFmt').set('formatCode','0.000 hours');parts['xl/styles.xml']=ET.tostring(root)
        else:
            root=ET.fromstring(parts['xl/workbook.xml']);root.find('{*}calcPr').set('iterateDelta','0.5');parts['xl/workbook.xml']=ET.tostring(root)
    f.edit(p,alter)
    with pytest.raises((q.QuarantineError,q.observer.RunnerError,q.recalc.RecalculationError)):
        q.validate_result(case['result'],case['request'],**case['args'])

def test_cached_original_table_body_cannot_be_reused_as_observed_answers(case):
    shutil.copyfile(case['args']['source'],case['args']['output'])
    with pytest.raises(q.QuarantineError,match='table results'):
        q.validate_result(case['result'],case['request'],**case['args'])

@pytest.mark.parametrize('change',['signature','ordinary_domain','extra','wrong_producer','wrong_key'])
def test_trust_and_domain_boundaries(case,tmp_path,change):
    result=copy.deepcopy(case['result']);args=dict(case['args'])
    if change=='signature':result['receipt']['signature_base64']=base64.b64encode(b'X'*64).decode()
    elif change=='ordinary_domain':result['receipt']['schema_version']='excel-runner-receipt/v1'
    elif change=='extra':result['receipt']['unapproved_field']=True
    elif change=='wrong_producer':args['producer_sha256']='b'*64
    else:
        key=Ed25519PrivateKey.generate();p=tmp_path/'other.pem';p.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));p.chmod(0o600);args['public_key']=p
    rehash_result(result)
    with pytest.raises(q.QuarantineError):q.validate_result(result,case['request'],**args)

def test_expiry_blocks_new_execution_without_expiring_valid_retained_evidence(case):
    request=case['request'];args={k:v for k,v in case['args'].items() if k!='staged'}
    with pytest.raises(q.QuarantineError,match='execution now'):
        q.validate_request(request,**args,for_execution=True,now_ns=request['expires_at_ns']+1)
    assert q.validate_result(case['result'],request,**case['args'])['scope']=='quarantine_only'

@pytest.mark.parametrize('field,offset',[('started_at_ns',-1),('completed_at_ns',86400*10**9+1),('completed_at_ns',True)])
def test_signed_execution_time_must_match_request(case,field,offset):
    claims=copy.deepcopy(case['claims']);claims[field]=offset if offset is True else case['request']['created_at_ns']+offset
    with pytest.raises(q.QuarantineError):q.validate_result(case['sign'](claims),case['request'],**case['args'])

@pytest.mark.parametrize('kind,value',[('n','0'),('b','1'),('str','result text'),('e','#NUM!')])
def test_actual_typed_observation_is_bound_without_treating_it_as_primitive(case,kind,value):
    args=case['args'];f.edit(args['staged'],lambda t,p:f.set_value(f.cells(t)['E3'],value,kind))
    shutil.copyfile(args['source'],args['output']);q.observer.graft_observed_data_tables(args['staged'],args['output'])
    # A faithfully observed Excel error is evidence, not a source approval.
    claims=q.prepare_unsigned_claims(case['request'],**args,engine_version='16.test',started_at_ns=case['claims']['started_at_ns'],completed_at_ns=case['claims']['completed_at_ns'],isolation_controls=case['claims']['isolation_controls'])
    assert q.validate_result(case['sign'](claims),case['request'],**args)['source_approval'] is False
    assert 'E3' not in q.recalc.semantic_workbook_snapshot(args['output'])['semantics']['inputs']["3 O'Brien"]

def test_unchanged_observed_caches_are_not_a_stale_cache_claim(case):
    args=case['args'];shutil.copyfile(args['source'],args['staged']);shutil.copyfile(args['source'],args['output'])
    claims=q.prepare_unsigned_claims(case['request'],**args,engine_version='16.test',started_at_ns=case['claims']['started_at_ns'],completed_at_ns=case['claims']['completed_at_ns'],isolation_controls=case['claims']['isolation_controls'])
    assert 'proven_stale_cache' not in claims
    assert q.validate_result(case['sign'](claims),case['request'],**args)['source_approval'] is False

@pytest.mark.parametrize('change',['code','observation','health','request_hash','extra'])
def test_altered_request_evidence_never_rebinds(case,change):
    request=copy.deepcopy(case['request'])
    if change=='code':request['code_identity']['observation_sha256']='b'*64
    elif change=='observation':request['original_observation']['tables'][0]['results'].pop()
    elif change=='health':request['original_health_sha256']='b'*64
    elif change=='extra':request['approved']=True
    else:request['request_sha256']='b'*64
    if change!='request_hash':rehash_request(request)
    with pytest.raises(q.QuarantineError):q.validate_request(request,**{k:v for k,v in case['args'].items() if k!='staged'})

def test_no_table_workbook_rejects_and_execution_cannot_overwrite(case,tmp_path):
    source=f.fixture(tmp_path/'no-tables.xlsx',table=False)
    with pytest.raises(q.QuarantineError,match='only the otherwise unsupported'):
        q.make_request(source,workbook='synthetic',output=tmp_path/'new.xlsx',producer_sha256=case['args']['producer_sha256'],public_key=case['args']['public_key'],engine_version='16.test')
    with pytest.raises(q.QuarantineError,match='overwrite'):
        q.validate_request(case['request'],**{k:v for k,v in case['args'].items() if k!='staged'},for_execution=True)

def test_extra_health_blocker_not_authorized_by_table_profile(case,monkeypatch):
    actual=q.health.inspect_workbook
    def inspect(path):
        result=actual(path);result['reason_codes'].append('macros_present');return result
    monkeypatch.setattr(q.health,'inspect_workbook',inspect)
    with pytest.raises(q.QuarantineError,match='only the otherwise unsupported'):
        q.validate_result(case['result'],case['request'],**case['args'])

def test_joint_evidence_race_rejected(case,monkeypatch):
    actual=q.inspect
    def inspect(path):
        result=actual(path)
        if Path(path)==case['args']['output']:
            f.edit(case['args']['source'],lambda t,p:f.set_value(f.cells(t)['A1'],'999'))
        return result
    monkeypatch.setattr(q,'inspect',inspect)
    with pytest.raises(q.QuarantineError,match='changed during joint'):
        q.validate_result(case['result'],case['request'],**case['args'])

def test_result_factory_requires_existing_valid_signature(case):
    result=q.assemble_result(case['request'],case['result']['receipt'],**case['args'])
    assert result==case['result']
    receipt=copy.deepcopy(result['receipt']);receipt['signature_base64']=''
    with pytest.raises(q.QuarantineError):q.assemble_result(case['request'],receipt,**case['args'])
