"""Explicit receipts reach existing source gates without changing READY intake."""
import base64
import json
import time
from argparse import Namespace
from pathlib import Path
from zipfile import ZipFile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from openpyxl import Workbook
from scripts import corpus_build as build
from scripts.corpus import digest
import xl_source_recalc as recalc
from xl_source_health import inspect_workbook
from xl_source_publication import validate_source_generation


def set_cache(path, cache):
    with ZipFile(path) as archive:
        members = [(i, archive.read(i)) for i in archive.infolist()]
    with ZipFile(path, 'w') as archive:
        for info, data in members:
            if info.filename == 'xl/worksheets/sheet1.xml':
                import re
                data = re.sub(rb'(<f>A1\*2</f>)(?:<v>[^<]*</v>|<v\s*/>)',
                              lambda m: m[1] + b'<v>' + str(cache).encode() + b'</v>', data)
            archive.writestr(info, data)


@pytest.fixture
def case(tmp_path, monkeypatch):
    raw = tmp_path/'0001.xlsx';book=Workbook();book.calculation.fullCalcOnLoad=False
    book.active['A1']=3;book.active['B1']='=A1*2';book.save(raw);book.close();set_cache(raw,5)
    assert inspect_workbook(raw)['route']=='pass'
    key=Ed25519PrivateKey.generate();public=tmp_path/'public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo));public.chmod(0o600)
    # Hermetic key is user-owned, not an installed root-owned trust anchor.
    # Keep actual key parsing, hash binding, protected mode and Ed25519 checks.
    protected=recalc._trusted_public_key
    monkeypatch.setattr(recalc,'_trusted_public_key',lambda p,**kw: protected(p,require_root_owner=False))
    job=tmp_path/'recalc/0001';job.mkdir(parents=True);destination=job/'0001.xlsx'
    request=recalc.create_recalc_request(raw,destination,allowed_root=job,
        trusted_runner_public_key=public,engine_constraints={'permitted_versions':['fixture-v1']})
    class Engine:
        name='excel-macos';version='fixture-v1';authoritative=True
        def execute(self, original, candidate, req):
            set_cache(candidate,6)
            payload={'request_sha256':req['request_sha256'],'source_sha256':digest(original),
                'output_sha256':digest(candidate),'engine':self.name,'engine_version':self.version,
                'calculation_complete':True,'isolation_enforced':True,
                'isolation_controls':{x:True for x in ('dedicated_session','network_disabled','macros_disabled',
                    'add_ins_disabled','link_updates_disabled','prompts_suppressed')},
                'network_isolation_mechanism':'macos-pf-anchor',
                'network_isolation_rules_sha256':recalc.PF_NETWORK_RULES_SHA256,'completed_at_ns':time.time_ns()}
            self.evidence={'controls':{x:True for x in ('dedicated_session','network_disabled','macros_disabled',
                'add_ins_disabled','link_updates_disabled','prompts_suppressed')},
                'runner_receipt':{'schema_version':'excel-runner-receipt/v1','signature_algorithm':'ed25519',
                    'signed_payload':payload,'signature_base64':base64.b64encode(key.sign(recalc._canonical_bytes(payload))).decode()}}
    result=recalc.execute_recalc(request,Engine(),allowed_root=job)
    (job/'request.json').write_text(json.dumps(request));(job/'result.json').write_text(json.dumps(result))
    run=tmp_path/'run';directory=run/'workbooks/0001';directory.mkdir(parents=True)
    curation=tmp_path/'curation/0001/curation.toml';curation.parent.mkdir(parents=True)
    curation.write_text('# retained curation\n')
    args=Namespace(run_root=str(run),source_inventory=None,inventory_batch_id=None,recalc_root=str(job.parent),
        trusted_public_key=str(public),curation_root=str(curation.parents[1]),inventory='unused',timeout=10,memory_mb=256,code_root=str(tmp_path))
    source={'workbook_id':'0001','path':str(raw),'sha256':digest(raw),'format':'.xlsx'}
    bindings={'code_sha256':'frozen','source_sha256':source['sha256'],'curation_sha256':digest(curation),
        'inventory_sha256':None,'inventory_batch_id':None,'recalc_request_sha256':digest(job/'request.json'),
        'recalc_result_sha256':digest(job/'result.json'),'trusted_public_key_sha256':digest(public)}
    ledger={'state':'READY','route':'pass','failure':None,'reason_codes':[],
        'build':{'attempts':1,'state':'RUNNING','stage':'source','curation_path':str(curation),'bindings':bindings}}
    path=directory/'ledger.json';path.write_text(json.dumps(ledger));monkeypatch.setattr(build,'code_hash',lambda:'frozen')
    return args,source,path,job,curation


class ReachedSegmentation(RuntimeError): pass


def stop_at_segmentation(monkeypatch):
    def stop(*a,**kw): raise ReachedSegmentation('validated source reached segmentation')
    monkeypatch.setattr('xl_segment.segment',stop)


def test_ready_pass_receipt_publishes_through_real_existing_gates(case,monkeypatch):
    args,source,path,job,curation=case;original=Path(source['path']).read_bytes();selected=curation.read_bytes()
    stop_at_segmentation(monkeypatch)
    with pytest.raises(ReachedSegmentation): build.worker(args,source)
    ledger=json.loads(path.read_text());b=ledger['build']
    assert ledger['state']=='READY' and ledger['route']=='pass' and ledger['failure'] is None
    assert b['recalculation']['result_sha256']==digest(job/'result.json')
    generation=Path(args.run_root)/'sources/0001/generations'/b['source_generation_id']
    manifest=validate_source_generation(generation)
    assert (generation/'request.json').read_text() and json.loads((generation/'request.json').read_text())['schema_version']=='source-recalc-request/v2'
    assert manifest['bindings']['source_sha256']==digest(job/'0001.xlsx')!=source['sha256']
    assert Path(source['path']).read_bytes()==original and curation.read_bytes()==selected


@pytest.mark.parametrize('which',['request','result'])
def test_changed_documents_after_checkpoint_rejected(case,monkeypatch,which):
    args,source,path,job,_=case;p=job/(which+'.json');p.write_text(p.read_text()+' ')
    with pytest.raises(ValueError,match='trusted evidence changed'):build.worker(args,source)
    assert not (Path(args.run_root)/'sources/0001/generations').exists()


def test_mismatched_request_original_rejected(case):
    args,source,path,job,_=case;p=job/'request.json';request=json.loads(p.read_text());request['source_sha256']='0'*64;p.write_text(json.dumps(request))
    ledger=json.loads(path.read_text());ledger['build']['bindings']['recalc_request_sha256']=digest(p);path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError,match='original corpus source'):build.worker(args,source)


@pytest.mark.parametrize('change',['signature','semantic_diff','request_self_hash','candidate'])
def test_checkpoint_rebinding_does_not_bypass_publication_validation(case,change):
    args,source,path,job,_=case
    if change=='candidate':set_cache(job/'0001.xlsx',999)
    else:
        name='request' if change=='request_self_hash' else 'result';p=job/(name+'.json');value=json.loads(p.read_text())
        if change=='signature':value['engine_evidence']['runner_receipt']['signature_base64']=base64.b64encode(bytes(64)).decode()
        elif change=='semantic_diff':value['semantic_diff']['formula_cache_changes']=123
        else:value['created_at_ns']+=1
        if name=='result':value['result_sha256']=recalc._unsigned_hash(value,'result_sha256')
        p.write_text(json.dumps(value));ledger=json.loads(path.read_text());ledger['build']['bindings'][f'recalc_{name}_sha256']=digest(p);path.write_text(json.dumps(ledger))
    with pytest.raises((ValueError,recalc.RecalculationError)):build.worker(args,source)
    assert not (Path(args.run_root)/'sources/0001/generations').exists()


@pytest.mark.parametrize('missing',['request','result'])
def test_partial_explicit_job_fails_before_controller_dispatch(case,monkeypatch,missing):
    args,source,path,job,_=case;(job/(missing+'.json')).unlink()
    monkeypatch.setattr(build,'_run_in_process_group',lambda *a,**k:pytest.fail('must not dispatch incomplete job'))
    with pytest.raises(ValueError,match='evidence is incomplete'):build.execute_one(args,source,'frozen')
    ledger=json.loads(path.read_text());assert ledger['state']=='READY' and ledger['failure'] is None


def test_missing_result_after_binding_rejected_by_worker(case):
    args,source,_,job,_=case;(job/'result.json').unlink()
    with pytest.raises(FileNotFoundError):build.worker(args,source)


def test_shared_root_without_this_workbook_job_does_not_select_recalc(case):
    args,_,_,_,_=case
    assert not build.recalc_evidence_present(args,'0002')
    assert build.recalc_evidence_present(args,'0001')


def test_existing_recalculation_required_route_retains_receipt_path(case,monkeypatch):
    args,source,path,job,_=case;ledger=json.loads(path.read_text());ledger.update(state='STOPPED',failure='trusted_recalculation_required');path.write_text(json.dumps(ledger))
    stop_at_segmentation(monkeypatch)
    with pytest.raises(ReachedSegmentation):build.worker(args,source)
    assert json.loads(path.read_text())['build']['recalculation']['request_sha256']==digest(job/'request.json')


@pytest.mark.parametrize('entry',['worker','controller'])
def test_no_new_recalculation_transformation_chain(case,entry):
    args,source,path,_,_=case;ledger=json.loads(path.read_text());ledger.update(state='STOPPED',route='unsupported',failure='source_policy_unsupported',reason_codes=['data_tables_present']);path.write_text(json.dumps(ledger))
    with pytest.raises(ValueError,match='requires READY/pass'):
        build.worker(args,source) if entry=='worker' else build.execute_one(args,source,'frozen')
    assert json.loads(path.read_text())['state']=='STOPPED'
    assert not (Path(args.run_root)/'normalization').exists()


def test_bound_job_cannot_disappear_and_fall_back_to_identity(case):
    args,source,_,job,_=case
    (job/'request.json').unlink();(job/'result.json').unlink()
    with pytest.raises(FileNotFoundError):build.worker(args,source)


def test_old_verified_build_that_ignored_bound_result_is_not_reused(case,monkeypatch):
    args,source,path,_,_=case;ledger=json.loads(path.read_text());ledger['build'].update(state='VERIFIED',source_generation_id='old-source',segmentation_generation_id='old-seg');path.write_text(json.dumps(ledger))
    monkeypatch.setattr('xl_release_publication.resolve_build_context',lambda *a,**k:{'source_path':source['path']})
    monkeypatch.setattr(build,'reuse_completed_source',lambda *a,**k:pytest.fail('identity reuse must not ignore configured result'))
    def stopped(*a,**k):
        current=json.loads(path.read_text());current['build'].update(state='STOPPED',failure='fixture stopped before new worker');path.write_text(json.dumps(current));return 1
    monkeypatch.setattr(build,'_run_in_process_group',stopped)
    result=build.execute_one(args,source,'frozen')
    assert not result.get('reused')
    current=json.loads(path.read_text())
    assert current['verified_reuse_rejections'][-1]['reason']=='configured trusted result was not consumed by previous build'
    assert current['build_history'][-1]['state']=='VERIFIED'
