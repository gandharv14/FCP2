"""Packaging must consume current applicability evidence without another audit."""
import json
from pathlib import Path

import pytest
import xl_output_task as task
import xl_audit_equivalence as eq
import xl_variable_source_audit as audit
from test_audit_equivalence import case, binding


@pytest.fixture
def packaging(case, tmp_path, monkeypatch):
    old, new, original_run, _ = case
    run = tmp_path / 'audits' / '0001-variable-sources'
    saved = json.loads(Path(old['variable_source_inventory']['path']).read_text())
    audit.generate_audit('0001', old['baseline_inputs']['path'],
        old['segmentation_generation']['path'], run, 'fixture',
        segmentation=saved['metadata']['segmentation'])
    for key, suffix in zip(eq.AUDIT_KEYS, ('.metadata.json', '.inventory.json', '.md')):
        old[key] = binding(run / ('0001-inputs-variable-sources' + suffix))
    artifact = Path(new['baseline_inputs']['path'])
    sidecar = task.inputs_sidecar_path(artifact)
    sidecar.write_bytes(Path(new['baseline_segmentation']['path']).read_bytes())
    new['baseline_segmentation'] = binding(sidecar)
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(eq.propose_audit_equivalence(old, new)))
    context = {'workbook_id': '0001', 'source_path': new['source_file']['path'],
        'source_dir': new['source_generation']['path'],
        'segmentation_dir': new['segmentation_generation']['path'],
        'bindings': {component + suffix: new[component + '_generation'][field]
            for component in ('source', 'segmentation')
            for suffix, field in (('_generation_id', 'id'), ('_manifest_sha256', 'manifest_sha256'))}}
    def no_model(*args, **kwargs):
        raise AssertionError('packaging must not submit another audit')
    monkeypatch.setattr(audit, '_call_model', no_model)
    return artifact, run, receipt, context


def load(packaging, **overrides):
    artifact, run, receipt, context = packaging
    args = dict(workbook='0001', artifact=artifact, audit_root=run.parent,
        receipt_path=receipt, pipeline_context=context,
        model=audit.DEFAULT_MODEL, project_id=audit.DEFAULT_PROJECT_ID)
    args.update(overrides)
    return task.load_applicable_audit(**args)


def test_current_audit_reused_without_journal_or_content_mutation(packaging):
    _, run, receipt, _ = packaging
    before = {p: p.read_bytes() for p in run.rglob('*') if p.is_file()}
    result = load(packaging)
    assert result['cache_hit'] is True
    assert result['applicability_sha256'] == eq.digest(receipt)
    assert result['inventory_sha256'] == json.loads(receipt.read_text())['old_inventory_sha256']
    assert result['current_inventory_sha256'] == json.loads(receipt.read_text())['current_inventory_sha256']
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('kind', ['baseline', 'sidecar', 'receipt', 'audit'])
def test_changed_evidence_never_falls_back_to_model(packaging, kind):
    artifact, run, receipt, _ = packaging
    path = {'baseline': artifact, 'sidecar': task.inputs_sidecar_path(artifact),
            'receipt': receipt, 'audit': run / '0001-inputs-variable-sources.md'}[kind]
    if kind == 'receipt':
        value = json.loads(path.read_text()); value['semantic_attempts_added'] = 1
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        load(packaging)


@pytest.mark.parametrize('component', ['source', 'segmentation'])
def test_receipt_cannot_select_a_different_generation(packaging, component):
    packaging[3]['bindings'][component + '_generation_id'] = 'd' * 64
    with pytest.raises(ValueError, match='binding mismatch'):
        load(packaging)


@pytest.mark.parametrize('overrides', [dict(pipeline_context=None), dict(workbook='0002'),
    dict(model='different'), dict(project_id='different')])
def test_unpinned_or_changed_configuration_rejected(packaging, overrides):
    with pytest.raises(ValueError):
        load(packaging, **overrides)


def test_symlink_and_wrong_audit_root_rejected(packaging, tmp_path):
    link = tmp_path / 'link.json'; link.symlink_to(packaging[2])
    with pytest.raises(ValueError, match='symlink'):
        load(packaging, receipt_path=link)
    with pytest.raises((ValueError, FileNotFoundError)):
        load(packaging, audit_root=tmp_path / 'wrong')


@pytest.mark.parametrize('extra', [[], ['--no-variable-source-audit'],
    ['--refresh-variable-source-audit'], ['--segmentation-mode', 'shadow']])
def test_cli_rejects_receipt_without_strict_audit_contract(extra):
    with pytest.raises(SystemExit) as exc:
        task.main(['0001', '--audit-equivalence-receipt', 'receipt.json'] + extra)
    assert exc.value.code == 2


def test_separate_normalization_root_and_receipt_are_retained(packaging, tmp_path):
    artifact, run, receipt, _ = packaging
    normalized = tmp_path / 'normalization' / '0001-variable-sources'
    normalized.mkdir(parents=True)
    (normalized / 'normalization_report.json').write_text(json.dumps({'draft_rows': 3, 'excluded_rows': 3}))
    (normalized / 'plain_eligibility.json').write_text(json.dumps({'plain_reason': 'proved exclusions'}))
    (normalized / 'exclusions.json').write_text('[{"reason":"retained"}]')
    out = tmp_path / 'task'
    task.emit(out, '0001', 'model', artifact, 'instruction', {'Sheet!B1': 1},
        [{'name': 'Output', 'band': 'Sheet!B1', 'refs': ['Sheet!B1']}], {},
        audit_meta=load(packaging), audit_root=run.parent,
        normalization_root=normalized.parent)
    assert (out / 'tests/normalization_exclusions.json').read_bytes() == (normalized / 'exclusions.json').read_bytes()
    assert (out / 'tests/audit_applicability.json').read_bytes() == receipt.read_bytes()
    assert 'proved exclusions' in (out / 'task.toml').read_text()
