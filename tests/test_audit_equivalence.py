"""Hermetic contract tests. Model transport and generation resolver are doubles;
the audit journal, coverage, ZIP comparison, and receipt validator are real.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest
import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import GOOD_MODEL_TABLE, write_two_variable_fixture
import xl_audit_equivalence as eq


def binding(p):
    return {'path': str(p.resolve()), 'sha256': eq.digest(p)}


@pytest.fixture
def case(tmp_path, monkeypatch):
    artifact, segment = write_two_variable_fixture(tmp_path)
    source = tmp_path / 'source.xlsx'; shutil.copyfile(artifact, source)
    oldseg = tmp_path / 'old/generations' / ('a' * 64)
    newseg = tmp_path / 'new/generations' / ('b' * 64)
    sourcedir = tmp_path / 'source/generations' / ('c' * 64)
    for p in (oldseg, newseg, sourcedir):
        p.mkdir(parents=True)
        (p / 'generation-manifest.json').write_text(json.dumps({'id': p.name}))
        if p != sourcedir:
            (p / 'segments.json').write_bytes((segment / 'segments.json').read_bytes())
            (p / 'curation.toml').write_text('preserved')
            (p.parent.parent / 'curation.toml').write_text('preserved')
    fresh = tmp_path / 'fresh' / artifact.name; fresh.parent.mkdir(); shutil.copyfile(artifact, fresh)
    oldside, newside = artifact.with_suffix('.json'), fresh.with_suffix('.json')
    oldside.write_text('{}'); newside.write_text('{}')
    def gen(p): return {'path': str(p), 'id': p.name, 'manifest_sha256': eq.digest(p / 'generation-manifest.json')}
    old = {'source_file': binding(source), 'original_raw_source': binding(source),
        'source_generation': gen(sourcedir), 'segmentation_generation': gen(oldseg),
        'baseline_inputs': binding(artifact), 'baseline_segmentation': binding(oldside)}
    new = {**copy.deepcopy(old), 'segmentation_generation': gen(newseg),
        'baseline_inputs': binding(fresh), 'baseline_segmentation': binding(newside)}
    def inventory(b):
        return audit.build_inventory('0001', b['baseline_inputs']['path'], b['segmentation_generation']['path'], segmentation={
            'generation_id': b['segmentation_generation']['id'], 'manifest_sha256': b['segmentation_generation']['manifest_sha256'],
            'source_generation_id': sourcedir.name, 'inventory_approval_sha256': None, 'recalc_signals_sha256': None})
    output = tmp_path / 'audit'
    monkeypatch.setattr(audit, '_call_model', lambda *args: (GOOD_MODEL_TABLE, 'stop'))
    audit.generate_audit('0001', artifact, oldseg, output, 'fixture', segmentation=inventory(old)['metadata']['segmentation'])
    for key, suffix in zip(eq.AUDIT_KEYS, ('.metadata.json', '.inventory.json', '.md')):
        old[key] = binding(output / ('0001-inputs-variable-sources' + suffix))
    manifest = {'artifacts': {'curation.toml': {'sha256': 'same'}}, 'curated_output_cells': {'ordered': ['Inputs!C1']}}
    calls = []
    def context(b, *, historical):
        calls.append(historical)
        return {}, copy.deepcopy(manifest), inventory(b)
    monkeypatch.setattr(eq, '_context', context)
    return old, new, output, calls


def test_complete_audit_reused_without_mutation_or_new_attempt(case, tmp_path):
    old, new, output, calls = case
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    receipt = eq.propose_audit_equivalence(old, new)
    assert calls == [True, False]
    assert receipt['model_reexecuted'] is False and receipt['semantic_attempts_added'] == 0
    assert receipt['retained_consumption']['submitted_counts'] == {'1': 1}
    path = tmp_path / 'proposal.json'; path.write_text(json.dumps(receipt))
    assert eq.validate_audit_equivalence(path, current_bindings=new)[str(path.resolve())] == eq.digest(path)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('field', ['label', 'kind', 'value_type', 'value_summary', 'band', 'row_id', 'sheet'])
def test_inventory_field_change_rejected(case, field):
    old, new, _, _ = case
    inv = json.loads(Path(old['variable_source_inventory']['path']).read_text())
    fresh = copy.deepcopy(inv)
    fresh['metadata']['segmentation'].update(generation_id='d'*64, manifest_sha256='e'*64)
    fresh['variables'][0][field] = 'different'
    with pytest.raises(ValueError, match='beyond locator'):
        eq.inventory_equivalence(inv, fresh)


@pytest.mark.parametrize('field', ['source_generation_id', 'inventory_approval_sha256', 'recalc_signals_sha256'])
def test_other_segmentation_provenance_rejected(case, field):
    old, _, _, _ = case
    inv = json.loads(Path(old['variable_source_inventory']['path']).read_text()); fresh = copy.deepcopy(inv)
    fresh['metadata']['segmentation'].update(generation_id='d'*64, manifest_sha256='e'*64)
    fresh['metadata']['segmentation'][field] = 'f'*64
    with pytest.raises(ValueError, match='beyond locator'): eq.inventory_equivalence(inv, fresh)


def test_only_container_timestamp_difference_allowed(case):
    old, new, _, _ = case
    p = Path(new['baseline_inputs']['path']); original = Path(old['baseline_inputs']['path'])
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(p, 'w') as dst:
        for info in src.infolist():
            info.date_time = (2020, 1, 1, 0, 0, 0)
            dst.writestr(info, src.read(info))
    new['baseline_inputs'] = binding(p)
    assert eq.digest(p) != eq.digest(original)
    eq.propose_audit_equivalence(old, new)


@pytest.mark.parametrize('change', ['formula', 'format', 'property', 'extra_member', 'duplicate_member'])
def test_zip_member_changes_rejected(case, change):
    old, new, _, _ = case
    p = Path(new['baseline_inputs']['path'])
    with zipfile.ZipFile(p, 'a') as z:
        if change == 'duplicate_member': z.writestr(z.namelist()[0], b'changed')
        else: z.writestr(change + '.xml', b'changed')
    new['baseline_inputs'] = binding(p)
    with pytest.raises(ValueError, match='ZIP member'): eq.propose_audit_equivalence(old, new)


@pytest.mark.parametrize('field', ['model', 'endpoint', 'project_id'])
def test_model_config_changes_rejected(case, field):
    old, new, _, _ = case
    with pytest.raises(audit.AuditRevalidationRequired):
        eq.propose_audit_equivalence(old, new, **{field: 'changed'})


def test_stale_generator_rejected(case, monkeypatch):
    monkeypatch.setattr(audit, 'GENERATOR_VERSION', 'changed')
    with pytest.raises(audit.AuditRevalidationRequired): eq.propose_audit_equivalence(*case[:2])


@pytest.mark.parametrize('key', ['source_generation', 'original_raw_source'])
def test_source_identity_change_rejected(case, key):
    old, new, _, _ = case
    new[key] = {**new[key], 'unused_change': True}
    with pytest.raises(ValueError, match='source'): eq.propose_audit_equivalence(old, new)


def test_missing_response_and_rehashed_receipt_rejected(case, tmp_path):
    old, new, output, _ = case
    receipt = eq.propose_audit_equivalence(old, new)
    response = next(output.rglob('*.response.json')); response.unlink()
    with pytest.raises(ValueError): eq.propose_audit_equivalence(old, new)
    p = tmp_path / 'receipt.json'; p.write_text(json.dumps(receipt))
    with pytest.raises(ValueError): eq.validate_audit_equivalence(p, current_bindings=new)


def test_caller_current_bindings_cannot_be_selected_by_receipt(case, tmp_path):
    old, new, _, _ = case; receipt = eq.propose_audit_equivalence(old, new)
    p = tmp_path / 'receipt.json'; p.write_text(json.dumps(receipt)); new['baseline_inputs']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='receipt current binding'): eq.validate_audit_equivalence(p, current_bindings=new)


def test_current_proof_failure_cannot_be_waived(case, monkeypatch):
    old, new, _, _ = case; original = eq._context
    def stale(b, *, historical):
        if not historical: raise ValueError('stale live proof')
        return original(b, historical=historical)
    monkeypatch.setattr(eq, '_context', stale)
    with pytest.raises(ValueError, match='stale live proof'): eq.propose_audit_equivalence(old, new)


def test_inherited_plus_submitted_budget_preserved_at_two(case, tmp_path):
    old, new, output, _ = case
    meta = eq.read(old['variable_source_metadata']['path'])
    coverage = eq.read(meta['coverage_path']); batch = coverage['batches'][0]
    inherited = tmp_path / 'inherited.json'
    inherited.write_text(json.dumps({'schema_version': 'audit-inherited-budget/v1', 'workbook': '0001',
        'attempts': [{'request': batch['request'], 'response': batch['response']}]}))
    next_output = tmp_path / 'second-audit'
    inv = eq.read(old['variable_source_inventory']['path'])
    audit.generate_audit('0001', old['baseline_inputs']['path'], old['segmentation_generation']['path'],
        next_output, 'fixture', segmentation=inv['metadata']['segmentation'], inherited_budget=binding(inherited))
    for key, suffix in zip(eq.AUDIT_KEYS, ('.metadata.json', '.inventory.json', '.md')):
        old[key] = binding(next_output / ('0001-inputs-variable-sources' + suffix))
    receipt = eq.propose_audit_equivalence(old, new)
    assert receipt['retained_consumption'] == {'limit': 2, 'submitted_counts': {'1': 1}, 'inherited_batch_counts': {'1': 1}}
    assert receipt['semantic_attempts_added'] == 0
    assert batch['request']['path'] in receipt['checked_files']
    assert batch['response']['path'] in receipt['checked_files']


@pytest.mark.parametrize('field', ['semantic_attempts_added', 'model_reexecuted', 'adapter_sha256', 'checked_files'])
def test_receipt_self_assertions_never_override_recomputation(case, tmp_path, field):
    old, new, _, _ = case
    receipt = eq.propose_audit_equivalence(old, new); receipt[field] = 'changed'
    p = tmp_path / 'receipt.json'; p.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='changed or stale'): eq.validate_audit_equivalence(p, current_bindings=new)


def witness(case, tmp_path):
    old, _, _, _ = case
    actual = Path(old['baseline_inputs']['path']); p = tmp_path / 'retained' / actual.name
    p.parent.mkdir(); shutil.copyfile(actual, p)
    sidecar = eq.seg_pub.inputs_sidecar_path(p); sidecar.write_text('{"retained":true}')
    old['historical_baseline_witness'] = {'workbook': binding(p), 'sidecar': binding(sidecar)}
    return p, sidecar


def test_exact_historical_baseline_witness_is_bound_without_rewriting_artifact(case, tmp_path):
    old, new, _, _ = case; p, sidecar = witness(case, tmp_path)
    original = Path(old['variable_source_metadata']['path']).read_bytes()
    receipt = eq.propose_audit_equivalence(old, new)
    assert receipt['checked_files'][str(p.resolve())] == eq.digest(p)
    assert receipt['checked_files'][str(sidecar.resolve())] == eq.digest(sidecar)
    assert Path(old['variable_source_metadata']['path']).read_bytes() == original


@pytest.mark.parametrize('change', ['bytes', 'basename', 'missing_sidecar', 'changed_sidecar'])
def test_historical_witness_requires_exact_bytes_name_and_retained_sidecar(case, tmp_path, change):
    old, new, _, _ = case; p, sidecar = witness(case, tmp_path)
    if change == 'bytes':
        p.write_bytes(p.read_bytes() + b'extra'); old['historical_baseline_witness']['workbook'] = binding(p)
    elif change == 'basename':
        renamed = p.with_name('other.xlsx'); p.rename(renamed); old['historical_baseline_witness']['workbook'] = binding(renamed)
    elif change == 'missing_sidecar': sidecar.unlink()
    else: sidecar.write_text('changed')
    with pytest.raises(ValueError): eq.propose_audit_equivalence(old, new)


def test_publication_requires_explicit_receipt_and_same_selected_audit(case, tmp_path):
    from scripts.qualified_publication import validate_audit_bindings
    old, new, _, _ = case
    receipt = eq.propose_audit_equivalence(old, new)
    path = tmp_path / 'receipt.json'; path.write_text(json.dumps(receipt))
    bindings = {**new, **{key: old[key] for key in eq.AUDIT_KEYS}, 'audit_equivalence': binding(path)}
    expected = {key: new[key + '_generation']['id'] for key in ('source', 'segmentation')}
    assert str(path.resolve()) in validate_audit_bindings(bindings, expected_generations=expected)
    with pytest.raises(ValueError, match='publication segmentation'):
        validate_audit_bindings(bindings, expected_generations={**expected, 'segmentation': 'f' * 64})
    changed = tmp_path / 'copy.md'; changed.write_bytes(Path(old['variable_source_audit']['path']).read_bytes())
    bindings['variable_source_audit'] = binding(changed)
    with pytest.raises(ValueError, match='selected audit artifacts'):
        validate_audit_bindings(bindings, expected_generations=expected)


def test_receipt_cli_preserves_existing_artifacts(case, tmp_path, monkeypatch):
    old, new, _, _ = case
    old_path, new_path = tmp_path / 'old.json', tmp_path / 'new.json'
    old_path.write_text(json.dumps(old)); new_path.write_text(json.dumps(new))
    receipt = tmp_path / 'receipt.json'
    monkeypatch.setattr('sys.argv', ['audit-equivalence', 'propose', '--historical-bindings', str(old_path),
        '--current-bindings', str(new_path), '--receipt', str(receipt)])
    eq.main(); before = receipt.read_bytes()
    with pytest.raises(FileExistsError): eq.main()
    assert receipt.read_bytes() == before
    assert not list(tmp_path.glob('.audit-equivalence-*'))
    monkeypatch.setattr('sys.argv', ['audit-equivalence', 'validate', '--current-bindings', str(new_path), '--receipt', str(receipt)])
    eq.main()
