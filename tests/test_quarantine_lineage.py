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

import xl_table_quarantine_lineage as chain
q=chain.quarantine
spec=importlib.util.spec_from_file_location('presentation_fixture',Path(__file__).with_name('test_presentation_tables.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

def write(path,data):path.write_bytes(q.canonical(data))
def edit(path,change):
    with zipfile.ZipFile(path) as z:parts={i.filename:z.read(i.filename) for i in z.infolist()}
    change(parts)
    with zipfile.ZipFile(path,'w') as z:
        for name,content in parts.items():z.writestr(name,content)

@pytest.fixture
def fixture_factory(tmp_path,monkeypatch):
    key=Ed25519PrivateKey.generate();public=tmp_path/'key.pem';public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));public.chmod(0o600)
    protected=q.recalc._trusted_public_key
    monkeypatch.setattr(q.recalc,'_trusted_public_key',lambda p:protected(p,require_root_owner=False))
    def build(*,in_cone=False,leak=False,nonunique=False):
        root=tmp_path/'case';root.mkdir()
        original,curation=f.source_fixture(root,in_cone=in_cone,answer_in_table=leak)
        if nonunique:
            edit(original,lambda parts:parts.__setitem__('xl/worksheets/sheet1.xml',parts['xl/worksheets/sheet1.xml'].replace(b'<f>A1*3</f>',b'<f>B1</f>')))
        staging=root/'staging.xlsx';intermediate=root/'quarantine'/'0001.xlsx';intermediate.parent.mkdir()
        start=time.time_ns()-10**9
        request=q.make_request(original,workbook='0001',output=intermediate,producer_sha256='a'*64,public_key=public,engine_version='16.test',created_at_ns=start)
        shutil.copyfile(original,staging);shutil.copyfile(original,intermediate)
        # For valid case change only calculated, non-graded table output.
        if not leak and not in_cone:
            edit(staging,lambda parts:parts.__setitem__('xl/worksheets/sheet1.xml',parts['xl/worksheets/sheet1.xml'].replace(b'<v>9</v>',b'<v>19</v>')))
        q.observer.graft_observed_data_tables(staging,intermediate)
        args=dict(source=original,staged=staging,output=intermediate,producer_sha256='a'*64,public_key=public)
        claims=q.prepare_unsigned_claims(request,**args,engine_version='16.test',started_at_ns=start+1,completed_at_ns=start+2,isolation_controls={x:True for x in q.observer.REQUIRED_CONTROLS})
        receipt={'schema_version':q.RECEIPT,'signature_algorithm':'ed25519','signed_payload':claims,'signature_base64':base64.b64encode(key.sign(q.canonical(claims))).decode()}
        result=q.assemble_result(request,receipt,**args)
        request_path=root/'request.json';result_path=root/'result.json';write(request_path,request);write(result_path,result)
        candidate,materialization=chain.presentation.materialize(intermediate,'0001',curation,root/'materialization')
        bindings=dict(original=original,staged=staging,intermediate=intermediate,candidate=candidate,curation=curation,
                      request_path=request_path,result_path=result_path,materialization_path=candidate.parent/'manifest.json',
                      expected_workbook='0001',expected_original_sha256=q.file_hash(original),expected_curation_sha256=q.file_hash(curation),expected_ordered_outputs=['S!B1'],producer_sha256='a'*64,public_key=public)
        lineage=chain.propose_lineage(**bindings)
        return {'bindings':bindings,'lineage':lineage,'materialization':materialization,'request':request,'result':result}
    return build

@pytest.fixture
def case(fixture_factory):return fixture_factory()

def test_actual_validators_accept_exact_chain_and_keep_original_source_unapproved(case):
    checked=chain.validate_lineage(case['lineage'],**case['bindings'])
    assert checked['status']=='verified_chain_evidence' and checked['source_approval'] is False
    assert q.health.inspect_workbook(case['bindings']['original'])['route']=='unsupported'
    assert q.health.inspect_workbook(case['bindings']['intermediate'])['route']=='unsupported'
    assert q.health.inspect_workbook(case['bindings']['candidate'])['route']=='pass'
    assert checked['original_sha256']!=checked['candidate_sha256']
    # The existing source inventory gate does not recognize the new chain.
    from xl_source_inventory import transformed_workbook_record,SourceInventoryError
    with pytest.raises(SourceInventoryError):
        transformed_workbook_record('0001',case['bindings']['original'],case['bindings']['candidate'],case['bindings']['curation'],case['lineage'])

@pytest.mark.parametrize('name',['original','staged','intermediate','candidate','curation','request_path','result_path','materialization_path'])
def test_missing_retained_chain_evidence_rejects(case,name):
    case['bindings'][name].unlink()
    with pytest.raises((ValueError,OSError)):
        chain.validate_lineage(case['lineage'],**case['bindings'])

@pytest.mark.parametrize('field',['original_sha256','intermediate_sha256','candidate_sha256','curation_sha256','ordered_outputs','current_code','quarantine_profile','source_approval','policy_version'])
def test_rehashed_self_asserted_chain_rejects(case,field):
    lineage=copy.deepcopy(case['lineage']);lineage[field]=True if field=='source_approval' else 'forged'
    lineage['lineage_sha256']=q.digest({k:v for k,v in lineage.items() if k!='lineage_sha256'})
    with pytest.raises(ValueError):chain.validate_lineage(lineage,**case['bindings'])

@pytest.mark.parametrize('field,value',[('expected_original_sha256','b'*64),('expected_curation_sha256','b'*64),('expected_workbook','0002'),('expected_ordered_outputs',['S!A1']),('expected_ordered_outputs',[])])
def test_independent_caller_selection_is_required(case,field,value):
    bindings=dict(case['bindings']);bindings[field]=value
    with pytest.raises(ValueError):chain.validate_lineage(case['lineage'],**bindings)

@pytest.mark.parametrize('field,value',[('original_sha256','b'*64),('curation_origin','initial_selection_on_original'),('ordered_outputs',['S!A1']),('tables',[]),('leak_scan','passed')])
def test_altered_second_receipt_rejected_even_with_rehashed_claims(case,field,value):
    receipt=copy.deepcopy(case['materialization']);receipt[field]=value
    if field=='leak_scan':receipt['inspection_manifest_sha256']='b'*64
    receipt['manifest_sha256']=q.digest({k:v for k,v in receipt.items() if k!='manifest_sha256'})
    write(case['bindings']['materialization_path'],receipt)
    with pytest.raises(ValueError):chain.propose_lineage(**case['bindings'])

@pytest.mark.parametrize('mode',['verifier','parser','manifest','curation'])
def test_actual_materialization_live_validator_rejects_stale_proof(case,tmp_path,monkeypatch,mode):
    generation=Path(case['materialization']['inspection_generation'])
    if mode in {'verifier','parser'}:
        changed=tmp_path/'changed.py';changed.write_text('new code')
        if mode=='verifier':monkeypatch.setattr(chain.publication,'verifier_paths',lambda:(changed,))
        else:
            import xl_source_publication
            monkeypatch.setattr(xl_source_publication,'_default_builder_paths',lambda:(changed,))
    elif mode=='manifest':(generation/'generation-manifest.json').write_text('{}')
    else:(generation/'curation.toml').write_text('[[output]]\nband="S!A1"\ninclude=true\n')
    with pytest.raises(ValueError):chain.propose_lineage(**case['bindings'])

@pytest.mark.parametrize('reason',['in_cone','leak','nonunique'])
def test_actual_reconstruction_exclusion_and_leak_gates_fail_closed(fixture_factory,reason):
    kwargs={reason:True}
    with pytest.raises((ValueError,SystemExit)):
        fixture_factory(**kwargs)

def test_ordinary_receipt_cannot_be_self_asserted_as_profile(case):
    result=copy.deepcopy(case['result']);result['receipt']['schema_version']='excel-runner-receipt/v1'
    result['result_sha256']=q.digest({k:v for k,v in result.items() if k!='result_sha256'});write(case['bindings']['result_path'],result)
    with pytest.raises(ValueError):chain.propose_lineage(**case['bindings'])

def test_candidate_contains_only_exact_permitted_materialization(case):
    def change(parts):
        root=ET.fromstring(parts['xl/worksheets/sheet1.xml'])
        cell=next(c for c in root.findall('.//{*}c') if c.get('r')=='A1')
        cell.find('{*}v').text='3';parts['xl/worksheets/sheet1.xml']=ET.tostring(root)
    edit(case['bindings']['candidate'],change)
    receipt=copy.deepcopy(case['materialization']);receipt['candidate_sha256']=q.file_hash(case['bindings']['candidate']);receipt['manifest_sha256']=q.digest({k:v for k,v in receipt.items() if k!='manifest_sha256'});write(case['bindings']['materialization_path'],receipt)
    with pytest.raises(ValueError,match='unapproved edits'):chain.propose_lineage(**case['bindings'])

def test_proof_must_use_exact_retained_curation_even_when_receipt_is_rehashed(case):
    bindings=dict(case['bindings']);bindings['curation'].write_text(bindings['curation'].read_text().replace('Result','Renamed'))
    bindings['expected_curation_sha256']=q.file_hash(bindings['curation'])
    receipt=copy.deepcopy(case['materialization']);receipt['curation_sha256']=bindings['expected_curation_sha256'];receipt['manifest_sha256']=q.digest({k:v for k,v in receipt.items() if k!='manifest_sha256'});write(bindings['materialization_path'],receipt)
    with pytest.raises(ValueError,match='proof curation bytes'):chain.propose_lineage(**bindings)

def test_duplicate_json_contract_fields_reject(case):
    path=case['bindings']['request_path'];text=path.read_text().rstrip();path.write_text(text[:-1]+',"scope":"quarantine_only"}')
    with pytest.raises(ValueError,match='duplicate JSON'):chain.propose_lineage(**case['bindings'])

def test_no_swapped_intermediate_or_final_can_join_edges(case,tmp_path):
    bindings=dict(case['bindings']);other=tmp_path/'other';other.mkdir();replacement=other/'0001.xlsx';shutil.copyfile(bindings['original'],replacement);bindings['intermediate']=replacement
    with pytest.raises(ValueError):chain.propose_lineage(**bindings)
