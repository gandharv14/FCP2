"""Native identity AST reuse ignores only dependencies that cannot affect it."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import corpus_build as build
from scripts.corpus import digest, object_hash
from test_corpus_source_checkpoint import completed, reuse
from xl_source_inventory import build_inventory_manifest, validate_inventory_manifest


def bind_checkpoint(fixture):
    result = reuse(fixture)
    assert result is not None
    previous = fixture[2]['build_history'][0]
    previous['source_checkpoint'] = result[2]
    previous['source_checkpoint_sha256'] = object_hash(result[2])
    return previous


@pytest.mark.parametrize('bound_checkpoint', [False, True])
def test_unrelated_valid_inventory_addition_and_curation_change_reuse_native_ast(
        completed, tmp_path, monkeypatch, bound_checkpoint):
    import xl_ast_graph
    args, source, ledger, generation = completed
    inventory_sources = tmp_path / 'inventory-sources'; inventory_sources.mkdir()
    (inventory_sources / '0001.xlsx').write_bytes(Path(source['path']).read_bytes())
    old = build_inventory_manifest(inventory_sources, workbook_ids=['0001'])
    old_path = tmp_path / 'old-inventory.json'; old_path.write_text(json.dumps(old))
    args.source_inventory = str(old_path); args.inventory_batch_id = 'old-global-batch'
    for attempt in (ledger['build'], ledger['build_history'][0]):
        attempt['bindings']['inventory_sha256'] = digest(old_path)
    if bound_checkpoint:
        bind_checkpoint(completed)
    original_history = copy.deepcopy(ledger['build_history'])
    (inventory_sources / '0002.xlsx').write_bytes(Path(source['path']).read_bytes())
    new = build_inventory_manifest(inventory_sources, workbook_ids=['0001', '0002'])
    validate_inventory_manifest(new)
    new_path = tmp_path / 'new-inventory.json'; new_path.write_text(json.dumps(new))
    args.source_inventory = str(new_path); args.inventory_batch_id = 'new-global-batch'
    ledger['build']['bindings'].update(inventory_sha256=digest(new_path),
                                     curation_sha256='different-output-selection')
    ast = Mock(side_effect=AssertionError('native source AST must not be rebuilt'))
    monkeypatch.setattr(xl_ast_graph, 'main', ast)
    result = reuse(completed)
    assert result is not None and result[0] == generation and not ast.called
    assert result[2]['bindings']['inventory_sha256'] == digest(new_path)
    assert result[2]['bindings']['curation_sha256'] == 'different-output-selection'
    assert ledger['build_history'] == original_history


@pytest.mark.parametrize('kind', ['normalization', 'recalculation'])
@pytest.mark.parametrize('which', ['current', 'previous', 'both'])
def test_any_attempt_transformation_evidence_prevents_native_projection(
        completed, kind, which):
    args, source, ledger, _ = completed
    previous = bind_checkpoint(completed)
    if which in {'current', 'both'}:
        ledger['build'][kind] = {}
    if which in {'previous', 'both'}:
        previous[kind] = {}
    if which == 'both':
        # Even equally empty evidence declarations cannot license the exception.
        previous['source_checkpoint']['bindings'][kind + '_sha256'] = object_hash({})
        previous['source_checkpoint_sha256'] = object_hash(previous['source_checkpoint'])
    ledger['build']['bindings']['curation_sha256'] = 'new-selection'
    assert reuse(completed) is None


def test_native_projection_still_requires_current_publication_gate(completed, monkeypatch):
    import xl_source_publication as publication
    bind_checkpoint(completed)
    completed[2]['build']['bindings']['curation_sha256'] = 'new-selection'
    gate = Mock(side_effect=publication.SourcePublicationError('current gate rejected'))
    monkeypatch.setattr(publication, 'publish_source_generation', gate)
    assert reuse(completed) is None and gate.called


def test_native_projection_cannot_hide_current_builder_change(completed, tmp_path, monkeypatch):
    import xl_source_publication as publication
    bind_checkpoint(completed)
    completed[2]['build']['bindings']['curation_sha256'] = 'new-selection'
    changed = tmp_path / 'changed_builder.py'; changed.write_text('# changed AST producer')
    monkeypatch.setattr(publication, '_default_builder_paths', lambda: (changed,))
    assert reuse(completed) is None
    assert 'current production AST builder changed' in completed[2]['build']['source_reuse_rejections'][-1]['reason']


def test_different_original_and_prepared_bytes_cannot_use_native_projection(completed, tmp_path):
    args, source, ledger, _ = completed
    original_prepared = source['path']
    different_original = tmp_path / 'original-with-different-bytes.xlsx'
    different_original.write_bytes(Path(original_prepared).read_bytes() + b'unchanged ZIP semantics, distinct bytes')
    source['path'] = str(different_original); source['sha256'] = digest(different_original)
    previous = ledger['build_history'][0]
    previous['bindings']['source_sha256'] = source['sha256']
    ledger['build']['bindings']['source_sha256'] = source['sha256']
    ledger['build']['bindings']['curation_sha256'] = 'new-selection'
    assert build.reuse_completed_source(args, source, ledger, original_prepared, {}, {}) is None


def test_original_drift_still_fails_before_reuse(completed):
    bind_checkpoint(completed)
    Path(completed[1]['path']).write_bytes(b'changed-original')
    completed[2]['build']['bindings']['curation_sha256'] = 'new-selection'
    with pytest.raises(ValueError, match='original source changed'):
        reuse(completed)


def _verified_build(fixture, tmp_path):
    from argparse import Namespace
    from xl_segment import segment
    args, source, ledger, _ = fixture
    generation = (Path(args.run_root) / 'sources/0001/generations'
                  / ledger['build_history'][0]['source_generation_id'])
    out = Path(args.run_root) / 'segments'
    curation = out / '0001/curation.toml'; curation.parent.mkdir(parents=True)
    curation.write_text('[[output]]\nband = "Sheet!B1"\ninclude = true\nname = "Result"\n')
    result = segment('0001', Namespace(source_generation_path=str(generation),
        source_generation_id=None, source_generation_root='', ast_dir='', source='',
        out=str(out), allow_legacy_ast=False, recurate=False, llm=False,
        no_auto_adjudication=True, env_file='/nonexistent', threshold=6.0, top=40,
        sample=400, lineage_max=4000, no_verify=False, model='disabled'))
    assert result['verification']['passed']
    args.curation_root = str(out); args.recalc_root = None
    args.inventory = 'unused-by-test-worker'; args.timeout = 10
    args.memory_mb = 128; args.code_root = str(tmp_path)
    source['format'] = '.xlsx'
    bindings = {'code_sha256': 'fixture-current', 'source_sha256': source['sha256'],
        'curation_sha256': digest(curation), 'inventory_sha256': None,
        'inventory_batch_id': None}
    ledger.update(state='READY', route='pass')
    ledger['build'].update(state='VERIFIED', stage='reconstruction', bindings=bindings,
        source_generation_id=generation.name, source_manifest_sha256=digest(generation/'generation-manifest.json'),
        segmentation_generation_id=result['generation']['generation_id'])
    path = Path(args.run_root) / 'workbooks/0001/ledger.json'; path.parent.mkdir(parents=True)
    path.write_text(json.dumps(ledger))
    return path


def test_verified_native_fastpath_runs_source_gate_without_ast_or_segmentation(
        completed, tmp_path, monkeypatch):
    import xl_ast_graph
    import xl_segment
    import xl_source_publication as publication
    args, source, ledger, _ = completed
    path = _verified_build(completed, tmp_path)
    prior = json.loads(path.read_text())
    ast = Mock(side_effect=AssertionError('no AST construction during valid reuse'))
    segment = Mock(side_effect=AssertionError('no segmentation during valid reuse'))
    gate = Mock(wraps=publication.publish_source_generation)
    monkeypatch.setattr(xl_ast_graph, 'main', ast)
    monkeypatch.setattr(xl_segment, 'segment', segment)
    monkeypatch.setattr(publication, 'publish_source_generation', gate)
    result = build.execute_one(args, source, 'fixture-current')
    assert result.get('reused') is True and gate.call_count == 1
    assert not ast.called and not segment.called
    assert json.loads(path.read_text()) == prior


def test_unrelated_snapshot_change_revalidates_completed_proof_without_rebuilding(
        completed, tmp_path, monkeypatch):
    import xl_segment
    args, source, _, _ = completed
    path = _verified_build(completed, tmp_path)
    before = json.loads(path.read_text())
    monkeypatch.setattr(xl_segment, 'segment', Mock(side_effect=AssertionError('no rebuild')))
    monkeypatch.setattr(build, '_run_in_process_group', Mock(side_effect=AssertionError('no worker')))
    result = build.execute_one(args, source, 'different-runtime-snapshot')
    assert result['reused'] is True
    assert result['segmentation_generation_id'] == before['build']['segmentation_generation_id']
    after = json.loads(path.read_text())
    assert after['build_history'][-1] == before['build']
    assert after['build']['bindings']['code_sha256'] == 'different-runtime-snapshot'
    proof = after['build']['checkpoint_revalidation']
    assert digest(proof['preflight']) == proof['preflight_sha256']
    assert json.loads(Path(proof['preflight']).read_text())['healthy'] is True


@pytest.mark.parametrize('changed_gate', ['preflight', 'verifier'])
def test_snapshot_change_cannot_reuse_rejected_current_proof(
        completed, tmp_path, monkeypatch, changed_gate):
    args, source, _, _ = completed
    path = _verified_build(completed, tmp_path)
    if changed_gate == 'preflight':
        monkeypatch.setattr('xl_preflight.preflight', lambda *a, **k: {'healthy': False})
    else:
        changed = tmp_path / 'new-verifier.py'
        changed.write_text('changed financial implementation')
        monkeypatch.setattr('xl_seg.publication.verifier_paths', lambda: (changed,))
    fresh = Mock(return_value=1)
    monkeypatch.setattr(build, '_run_in_process_group', fresh)
    result = build.execute_one(args, source, 'different-runtime-snapshot')
    assert result.get('reused') is not True and fresh.call_count == 1
    after = json.loads(path.read_text())
    assert after['verified_reuse_rejections']
    assert after['build_history'][-1]['state'] == 'VERIFIED'


def test_pinned_preflight_rejects_changed_verifier_on_inactive_generations(
        completed, tmp_path, monkeypatch):
    from xl_preflight import preflight
    args, source, _, _ = completed
    path = _verified_build(completed, tmp_path)
    previous = json.loads(path.read_text())['build']
    run = Path(args.run_root)
    options = dict(source_generation_root=run / 'sources',
                   source_generation_id=previous['source_generation_id'],
                   segmentation_generation_id=previous['segmentation_generation_id'])
    assert preflight('0001', Path(source['path']).parent, run / 'segments',
                     tmp_path / 'valid-preflight', **options)['healthy']
    changed = tmp_path / 'changed-verifier.py'
    changed.write_text('new financial implementation')
    monkeypatch.setattr('xl_seg.publication.verifier_paths', lambda: (changed,))
    with pytest.raises(ValueError, match='current evidence does not match'):
        preflight('0001', Path(source['path']).parent, run / 'segments',
                  tmp_path / 'invalid-preflight', **options)
    assert not (tmp_path / 'invalid-preflight/0001.json').exists()


def test_verified_fastpath_rejects_forged_signature_despite_consistent_artifact_hashes(
        completed, tmp_path, monkeypatch):
    import shutil
    from test_corpus_source_checkpoint import (
        test_signed_recalculation_reuse_rechecks_signature_not_just_artifact_hashes
        as make_forged_fixture,
    )
    # This helper generates/signs a real temporary receipt, then forges its
    # signature and recomputes ordinary document/manifest hashes. It proves
    # the production source helper rejects the forged signature cryptographically.
    make_forged_fixture(completed, monkeypatch, tmp_path)
    args, source, ledger, _ = completed
    path = _verified_build(completed, tmp_path)
    args.recalc_root = str(tmp_path/'recalc'); job=Path(args.recalc_root)/'0001';job.mkdir(parents=True)
    shutil.copyfile(tmp_path/'request.json',job/'request.json')
    shutil.copyfile(tmp_path/'result.json',job/'result.json')
    ledger['build']['bindings'].update(recalc_request_sha256=digest(job/'request.json'),
        recalc_result_sha256=digest(job/'result.json'),trusted_public_key_sha256=digest(args.trusted_public_key))
    path.write_text(json.dumps(ledger))
    fresh = Mock(return_value=1)
    monkeypatch.setattr(build,'_run_in_process_group',fresh)
    result = build.execute_one(args,source,'fixture-current')
    assert result.get('reused') is not True and fresh.call_count == 1
    after = json.loads(path.read_text())
    assert 'signature' in after['verified_reuse_rejections'][-1]['reason']
    assert after['build_history'][-1]['state']=='VERIFIED'


@pytest.mark.parametrize('key_mode_changed', [False, True])
def test_verified_trusted_reuse_rechecks_key_protection_without_rebuilding_valid_proofs(
        completed, tmp_path, monkeypatch, key_mode_changed):
    import base64
    import hashlib
    import shutil
    import xl_ast_graph
    import xl_segment
    import xl_source_recalc as recalc
    from test_corpus_source_checkpoint import (
        test_signed_recalculation_reuse_rechecks_signature_not_just_artifact_hashes
        as make_forged_fixture,
    )
    make_forged_fixture(completed, monkeypatch, tmp_path)
    args, source, ledger, _ = completed
    generation = (Path(args.run_root)/'sources/0001/generations'
                  /ledger['build_history'][0]['source_generation_id'])
    # Restore the genuine cryptographic signature retained by the fixture's
    # local signer, then update ordinary artifact checksums consistently.
    result = json.loads((generation/'result.json').read_text())
    result['engine_evidence']['runner_receipt']['signature_base64'] = base64.b64encode(
        (tmp_path/'signature.bin').read_bytes()).decode()
    result['result_sha256'] = hashlib.sha256(recalc._canonical_bytes(
        {k:v for k,v in result.items() if k!='result_sha256'})).hexdigest()
    (generation/'result.json').write_bytes(recalc._canonical_bytes(result))
    (tmp_path/'result.json').write_bytes(recalc._canonical_bytes(result))
    manifest_path = generation/'generation-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['artifacts']['result.json'].update(sha256=digest(generation/'result.json'),
        size_bytes=(generation/'result.json').stat().st_size)
    manifest['bindings']['result_sha256'] = digest(generation/'result.json')
    manifest_path.write_bytes(recalc._canonical_bytes(manifest))
    for attempt in (ledger['build'],ledger['build_history'][0]):
        attempt['recalculation']['result_sha256'] = digest(tmp_path/'result.json')
    path = _verified_build(completed,tmp_path)
    args.recalc_root = str(tmp_path/'recalc');job=Path(args.recalc_root)/'0001';job.mkdir(parents=True)
    shutil.copyfile(tmp_path/'request.json',job/'request.json')
    shutil.copyfile(tmp_path/'result.json',job/'result.json')
    ledger['build']['bindings'].update(recalc_request_sha256=digest(job/'request.json'),
        recalc_result_sha256=digest(job/'result.json'),trusted_public_key_sha256=digest(args.trusted_public_key))
    path.write_text(json.dumps(ledger))
    if key_mode_changed:
        # The bytes/hash are identical: only the current trust protection changed.
        Path(args.trusted_public_key).chmod(0o666)
    ast = Mock(side_effect=AssertionError('unexpected AST rebuild'))
    segmentation = Mock(side_effect=AssertionError('unexpected segmentation rebuild'))
    fresh = Mock(return_value=1)
    monkeypatch.setattr(xl_ast_graph,'main',ast)
    monkeypatch.setattr(xl_segment,'segment',segmentation)
    monkeypatch.setattr(build,'_run_in_process_group',fresh)
    resumed=build.execute_one(args,source,'fixture-current')
    if key_mode_changed:
        assert resumed.get('reused') is not True and fresh.called
        assert 'not protected' in json.loads(path.read_text())['verified_reuse_rejections'][-1]['reason']
    else:
        assert resumed.get('reused') is True and not fresh.called
        assert not ast.called and not segmentation.called
