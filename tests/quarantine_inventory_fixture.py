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
        request=q.make_request(original,workbook='0001',output=intermediate,producer_sha256=q.file_hash(Path(__file__).with_name('quarantine_synthetic_producer.py')),public_key=public,engine_version='16.112.2',created_at_ns=start)
        shutil.copyfile(original,staging);shutil.copyfile(original,intermediate)
        # For valid case change only calculated, non-graded table output.
        if not leak and not in_cone:
            edit(staging,lambda parts:parts.__setitem__('xl/worksheets/sheet1.xml',parts['xl/worksheets/sheet1.xml'].replace(b'<v>9</v>',b'<v>19</v>')))
        q.observer.graft_observed_data_tables(staging,intermediate)
        args=dict(source=original,staged=staging,output=intermediate,producer_sha256=q.file_hash(Path(__file__).with_name('quarantine_synthetic_producer.py')),public_key=public)
        claims=q.prepare_unsigned_claims(request,**args,engine_version='16.112.2',started_at_ns=start+1,completed_at_ns=start+2,isolation_controls={x:True for x in q.observer.REQUIRED_CONTROLS})
        receipt={'schema_version':q.RECEIPT,'signature_algorithm':'ed25519','signed_payload':claims,'signature_base64':base64.b64encode(key.sign(q.canonical(claims))).decode()}
        result=q.assemble_result(request,receipt,**args)
        request_path=root/'request.json';result_path=root/'result.json';write(request_path,request);write(result_path,result)
        candidate,materialization=chain.presentation.materialize(intermediate,'0001',curation,root/'materialization')
        bindings=dict(original=original,staged=staging,intermediate=intermediate,candidate=candidate,curation=curation,
                      request_path=request_path,result_path=result_path,materialization_path=candidate.parent/'manifest.json',
                      expected_workbook='0001',expected_original_sha256=q.file_hash(original),expected_curation_sha256=q.file_hash(curation),expected_ordered_outputs=['S!B1'],producer_sha256=q.file_hash(Path(__file__).with_name('quarantine_synthetic_producer.py')),public_key=public)
        lineage=chain.propose_lineage(**bindings)
        return {'bindings':bindings,'lineage':lineage,'materialization':materialization,'request':request,'result':result}
    return build

@pytest.fixture
def case(fixture_factory):return fixture_factory()
