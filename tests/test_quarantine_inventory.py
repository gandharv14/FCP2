from pathlib import Path
import copy
import importlib.util
import json

import pytest
import xl_table_quarantine_inventory as adapter
import xl_source_inventory as inventory
import xl_inventory_approval as approval

spec=importlib.util.spec_from_file_location('quarantine_inventory_fixture',Path(__file__).with_name('quarantine_inventory_fixture.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
fixture_factory=f.fixture_factory
case=f.case
q=adapter.q

@pytest.fixture
def prepared(case,tmp_path,monkeypatch):
    config=tmp_path/'producer-config.json';base=tmp_path/'base-config.json'
    producer=Path(__file__).with_name('quarantine_synthetic_producer.py').absolute()
    monkeypatch.setattr(adapter,'PRODUCER_CONFIG',config)
    monkeypatch.setattr(adapter,'BASE_CONFIG',base)
    monkeypatch.setattr(adapter,'PRODUCER',producer)
    # Only root ownership/parent permissions are relaxed for synthetic fixtures.
    # Real file hashes, policy, signatures, proof and health validators remain.
    def check(path):
        if path.is_symlink() or not path.is_file():raise ValueError('unsafe synthetic protected file')
    monkeypatch.setattr(adapter.runner,'validate_root_file',check)
    observer=str(Path(q.observer.__file__).absolute());producer_hash=q.file_hash(producer)
    config_data={'schema_version':'excel-table-quarantine-config/v1','base_config':str(base),
                 'base_module':observer,'base_module_sha256':q.file_hash(observer),
                 'dispatcher_sha256':producer_hash,'selected_policy_code':q.code_identity(producer_hash),
                 'permitted_engine_versions':['16.112.2']}
    base_data={'schema_version':'excel-runner-config/v1','runner_implementation':observer,
               'runner_implementation_sha256':q.file_hash(observer),
               'receipt_public_key':str(case['bindings']['public_key']),
               'receipt_public_key_sha256':q.file_hash(case['bindings']['public_key']),
               'isolation_controls':{k:True for k in adapter.runner.REQUIRED_CONTROLS}}
    f.write(config,config_data);f.write(base,base_data)
    lineage=tmp_path/'lineage.json';f.write(lineage,case['lineage'])
    bindings={k:v for k,v in case['bindings'].items() if k not in {'producer_sha256','public_key'}}
    record=adapter.quarantine_workbook_record(lineage_path=lineage,**bindings)
    return dict(case=case,record=record,bindings=bindings,lineage=lineage,config=config,base=base)


def test_actual_chain_inventory_and_original_ledger_preserved(prepared):
    record=prepared['record']; checked=adapter.validate_quarantine_transformation(record)
    assert checked['source_approval'] is False and checked['task_approval'] is False
    assert record['route']=='pass'
    manifest=inventory.assemble_inventory_manifest([record])
    ledger=approval.original_source_ledger(manifest)
    assert ledger[0]['original_source']['sha256']==prepared['bindings']['expected_original_sha256']
    assert ledger[0]['original_source']['sha256']!=record['sha256']


def registry(path,claims):
    core={'schema_version':'source-inventory-approval-registry/v1','approvals':claims}
    f.write(path,{**core,'registry_sha256':approval.object_hash(core)})


def test_actual_commit_pinned_approval_required_even_final_pass(prepared,tmp_path,monkeypatch):
    manifest=inventory.assemble_inventory_manifest([prepared['record']]);pinned=tmp_path/'pinned.json'
    registry(pinned,[]);monkeypatch.setattr(approval,'DEFAULT_REGISTRY',pinned)
    with pytest.raises(approval.InventoryApprovalError):
        adapter.resolve_approved_quarantine_record(manifest,workbook_id='0001',batch_id='synthetic')
    registry(pinned,[approval.approval_claims(manifest,batch_id='synthetic')])
    result=adapter.resolve_approved_quarantine_record(manifest,workbook_id='0001',batch_id='synthetic')
    assert result['task_approval'] is False
    assert result['required_next_gates']==['source_publication','strict_segmentation','Harbor_qualification']
    with pytest.raises(approval.InventoryApprovalError):
        adapter.resolve_approved_quarantine_record(manifest,workbook_id='0001',batch_id='other')


@pytest.mark.parametrize('name',['producer_sha256','public_key','producer_config','registry_path'])
def test_no_record_selected_trust_or_approval_parameters(prepared,name):
    forged=copy.deepcopy(prepared['record']);forged['transformation']['bindings'][name]='attacker'
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(forged)


@pytest.mark.parametrize('name',['source_approval','task_approval','policy_version','protected_policy','adapter_sha256','original_source'])
def test_rehash_does_not_authorize_forged_record(prepared,name):
    forged=copy.deepcopy(prepared['record']);forged['transformation'][name]='forged'
    forged['transformation']['manifest_sha256']=q.digest({k:v for k,v in forged['transformation'].items() if k!='manifest_sha256'})
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(forged)


@pytest.mark.parametrize('name',['sha256','route','health_report_sha256','conversion'])
def test_final_record_must_match_fresh_health_and_bytes(prepared,name):
    forged=copy.deepcopy(prepared['record']);forged[name]='forged'
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(forged)


@pytest.mark.parametrize('name',['original','staged','intermediate','candidate','curation','request_path','result_path','materialization_path'])
def test_all_retained_evidence_required(prepared,name):
    prepared['bindings'][name].unlink()
    with pytest.raises((ValueError,OSError)):adapter.validate_quarantine_transformation(prepared['record'])


@pytest.mark.parametrize('name',['dispatcher_sha256','selected_policy_code','base_config','base_module_sha256','permitted_engine_versions'])
def test_changed_protected_configuration_rejects(prepared,name):
    data=f.chain.load(prepared['config']);data[name]='forged';f.write(prepared['config'],data)
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(prepared['record'])


@pytest.mark.parametrize('name',['receipt_public_key','receipt_public_key_sha256','runner_implementation_sha256','isolation_controls'])
def test_trusted_key_and_observer_come_only_from_protected_base(prepared,name):
    data=f.chain.load(prepared['base']);data[name]='forged';f.write(prepared['base'],data)
    with pytest.raises((ValueError,OSError)):adapter.validate_quarantine_transformation(prepared['record'])


def test_protected_ownership_validator_cannot_be_bypassed(prepared,monkeypatch):
    def reject(path):raise ValueError('not root-protected')
    monkeypatch.setattr(adapter.runner,'validate_root_file',reject)
    with pytest.raises(ValueError,match='root-protected'):adapter.validate_quarantine_transformation(prepared['record'])


def test_current_proof_required_not_historical_pass(prepared,tmp_path,monkeypatch):
    changed=tmp_path/'new-verifier.py';changed.write_text('changed')
    monkeypatch.setattr(f.chain.publication,'verifier_paths',lambda:(changed,))
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(prepared['record'])


def test_ordinary_signature_cannot_authorize_chain(prepared):
    path=prepared['bindings']['result_path'];result=f.chain.load(path)
    result['receipt']['schema_version']='excel-runner-receipt/v1'
    result['result_sha256']=q.digest({k:v for k,v in result.items() if k!='result_sha256'});f.write(path,result)
    with pytest.raises(ValueError):adapter.validate_quarantine_transformation(prepared['record'])


@pytest.mark.parametrize('reason',['in_cone','leak','nonunique'])
def test_actual_chain_producer_rejects_invalid_financial_proof(fixture_factory,reason):
    with pytest.raises((ValueError,SystemExit)):fixture_factory(**{reason:True})

@pytest.mark.parametrize('versions',[[],[''],[' '],['*'],['16.*'],['16.112.2','16.112.2'],['16.112.2 '],['16'],['16.test']])
def test_concrete_unique_engine_versions_only(prepared,versions):
    config=f.chain.load(prepared['config']);config['permitted_engine_versions']=versions;f.write(prepared['config'],config)
    with pytest.raises(ValueError,match='unique concrete'):
        adapter.protected_policy()


@pytest.fixture
def controller(prepared,tmp_path,monkeypatch):
    from argparse import Namespace
    from scripts import corpus_build as build
    manifest=inventory.assemble_inventory_manifest([prepared['record']])
    inv=tmp_path/'inventory.json';f.write(inv,manifest)
    pinned=tmp_path/'pinned.json';registry(pinned,[approval.approval_claims(manifest,batch_id='synthetic')])
    monkeypatch.setattr(approval,'DEFAULT_REGISTRY',pinned)
    run=tmp_path/'run';directory=run/'workbooks/0001';directory.mkdir(parents=True)
    curation=prepared['bindings']['curation']
    args=Namespace(run_root=str(run),source_inventory=str(inv),inventory_batch_id='synthetic',
        recalc_root=None,trusted_public_key='unused',curation_root=str(tmp_path/'curation'),
        inventory='unused',timeout=10,memory_mb=256,code_root=str(tmp_path))
    source={'workbook_id':'0001','path':str(prepared['bindings']['original']),
            'sha256':prepared['bindings']['expected_original_sha256'],'format':'.xlsx'}
    normalization=build.configured_quarantine(args,source,curation)
    bindings={'code_sha256':'frozen','source_sha256':source['sha256'],'curation_sha256':q.file_hash(curation),
              'inventory_sha256':q.file_hash(inv),'inventory_batch_id':'synthetic',
              'quarantine_adoption_sha256':build.object_hash(normalization)}
    ledger={'state':'STOPPED','route':'unsupported','failure':'source_policy_unsupported',
            'reason_codes':['data_tables_present'],
            'build':{'attempts':1,'state':'RUNNING','stage':'source','curation_path':str(curation),'bindings':bindings}}
    path=directory/'ledger.json';f.write(path,ledger);monkeypatch.setattr(build,'code_hash',lambda:'frozen')
    monkeypatch.setattr(build,'retained_curation',lambda *a:curation)
    return args,source,path,normalization,pinned


def test_controller_full_synthetic_qualification_and_original_lineage(controller,prepared):
    from scripts import corpus_build as build,corpus_lineage
    from xl_source_publication import validate_source_generation
    args,source,path,normalization,pinned=controller
    raw=Path(source['path']).read_bytes();curation=prepared['bindings']['curation'].read_bytes()
    assert build.worker(args,source)==0
    ledger=f.chain.load(path);done=ledger['build']
    assert ledger['state']=='STOPPED' and ledger['route']=='unsupported'
    assert done['state']=='VERIFIED' and done['outputs']['match']==1
    generation=Path(args.run_root)/'sources/0001/generations'/done['source_generation_id']
    validate_source_generation(generation)
    assert f.chain.load(generation/'request.json')['schema_version']=='source-identity-request/v2'
    lineage=corpus_lineage.validate_generation_lineage(source['path'],generation,[done['normalization']])
    assert lineage['original_sha256']==source['sha256']
    assert lineage['normalization']['inventory_approval_sha256']==normalization['inventory_approval_sha256']
    assert done['source_checkpoint']['bindings']['normalization_sha256']==build.object_hash(normalization)
    assert Path(source['path']).read_bytes()==raw and prepared['bindings']['curation'].read_bytes()==curation
    # Both VERIFIED reuse and source-checkpoint reuse revalidate current approval.
    assert build.execute_one(args,source,'frozen')['reused'] is True
    registry(pinned,[])
    with pytest.raises(ValueError):corpus_lineage.validate_generation_lineage(source['path'],generation,[normalization])
    with pytest.raises(ValueError):build.execute_one(args,source,'frozen')


@pytest.mark.parametrize('change',['approval','staging','source','curation','checkpoint','mix'])
def test_controller_fails_before_source_publication(controller,prepared,monkeypatch,change):
    from scripts import corpus_build as build
    args,source,path,normalization,pinned=controller
    if change=='approval':registry(pinned,[])
    elif change=='staging':prepared['bindings']['staged'].unlink()
    elif change=='source':source['sha256']='b'*64
    elif change=='curation':prepared['bindings']['curation'].write_text('changed')
    elif change=='checkpoint':
        ledger=f.chain.load(path);ledger['build']['bindings']['quarantine_adoption_sha256']='b'*64;f.write(path,ledger)
    else:
        args.recalc_root=str(Path(args.run_root)/'ordinary');job=Path(args.recalc_root)/'0001';job.mkdir(parents=True)
        (job/'request.json').write_text('{}')
    def forbidden(*a,**kw):raise AssertionError('must not publish')
    monkeypatch.setattr('xl_source_recalc.prepare_source_generation',forbidden)
    with pytest.raises((ValueError,OSError)):build.worker(args,source)
    assert not (Path(args.run_root)/'sources').exists()


def test_identical_copied_code_root_preserves_actual_chain(prepared,tmp_path,monkeypatch):
    import shutil
    chain=adapter.chain
    original=chain.current_code();copied=tmp_path/'code-snapshot';copied.mkdir()
    root=Path(chain.publication.__file__).resolve().parent.parent
    for path in chain.publication.verifier_paths():
        relative=Path(path).resolve().relative_to(root);target=copied/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    for module,name in [(chain,'xl_table_quarantine_lineage.py'),(q,'xl_table_recalc_quarantine.py'),
                        (chain.presentation,'xl_presentation_tables.py'),(chain.health,'xl_source_health.py')]:
        target=copied/name;shutil.copyfile(module.__file__,target);monkeypatch.setattr(module,'__file__',str(target))
    monkeypatch.setattr(chain.publication,'__file__',str(copied/'xl_seg/publication.py'))
    assert chain.current_code()==original
    chain.validate_lineage(prepared['case']['lineage'],**prepared['case']['bindings'])
    (copied/'xl_seg/evaluate.py').write_text((copied/'xl_seg/evaluate.py').read_text()+'\n# changed verifier\n')
    assert chain.current_code()!=original
    with pytest.raises(ValueError):chain.validate_lineage(prepared['case']['lineage'],**prepared['case']['bindings'])


@pytest.mark.parametrize('mode',['outside','unexpected','duplicate','missing_publication'])
def test_invalid_code_identity_sets_rejected(prepared,tmp_path,monkeypatch,mode):
    chain=adapter.chain;paths=list(chain.publication.verifier_paths())
    if mode=='outside':
        extra=tmp_path/'outside.py';extra.write_text('untrusted');paths.append(extra)
    elif mode=='unexpected':paths.append(Path(chain.health.__file__))
    elif mode=='duplicate':paths.append(paths[0])
    else:paths=[p for p in paths if p.name!='publication.py']
    monkeypatch.setattr(chain.publication,'verifier_paths',lambda:tuple(paths))
    with pytest.raises(ValueError):chain.current_code()
