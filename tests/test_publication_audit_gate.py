import hashlib
import json

import pytest

import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import GOOD_MODEL_TABLE, write_two_variable_fixture
from scripts.qualified_publication import validate_audit_bindings


def audited_bindings(tmp_path, monkeypatch):
    # These tests isolate historical audit integrity; current context has its own suite.
    from scripts import qualified_publication
    monkeypatch.setattr(qualified_publication, '_validate_current_audit_context', lambda *args: {})
    artifact, segments = write_two_variable_fixture(tmp_path)
    monkeypatch.setattr(audit, '_call_model', lambda *a, **k: (GOOD_MODEL_TABLE, 'stop'))
    output = tmp_path / 'audit'
    audit.generate_audit('0001', str(artifact), segments, output, 'fixture-key')
    paths = {
        'variable_source_metadata': output / '0001-inputs-variable-sources.metadata.json',
        'variable_source_inventory': output / '0001-inputs-variable-sources.inventory.json',
        'variable_source_audit': output / '0001-inputs-variable-sources.md',
    }
    return {key: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for key, path in paths.items()}


def test_complete_audit_publication_binds_coverage_artifact(tmp_path, monkeypatch):
    bindings = audited_bindings(tmp_path, monkeypatch)
    records = validate_audit_bindings(bindings)
    assert len(records) == 4
    coverage = next(path for path in records if path.endswith('.coverage.json'))
    assert records[coverage] == hashlib.sha256(open(coverage, 'rb').read()).hexdigest()


@pytest.mark.parametrize('key', [
    'variable_source_metadata', 'variable_source_inventory', 'variable_source_audit',
])
def test_missing_or_changed_audit_binding_rejects_publication(tmp_path, monkeypatch, key):
    bindings = audited_bindings(tmp_path, monkeypatch)
    record = bindings.pop(key)
    with pytest.raises(ValueError, match='missing qualified audit binding'):
        validate_audit_bindings(bindings)
    bindings[key] = {**record, 'sha256': '0' * 64}
    with pytest.raises(ValueError, match='changed qualified audit evidence'):
        validate_audit_bindings(bindings)


def test_rehashed_metadata_cannot_hide_missing_coverage(tmp_path, monkeypatch):
    from pathlib import Path
    bindings = audited_bindings(tmp_path, monkeypatch)
    record = bindings['variable_source_metadata']
    path = Path(record['path'])
    metadata = json.loads(path.read_text())
    Path(metadata['coverage_path']).unlink()
    path.write_text(json.dumps(metadata))
    record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='missing or unreadable'):
        validate_audit_bindings(bindings)


def test_current_generator_change_requires_revalidation_not_corruption_verdict(tmp_path, monkeypatch):
    from scripts.corpus_qualification import _validate_audit_proof, RevalidationRequired
    bindings = audited_bindings(tmp_path, monkeypatch)
    monkeypatch.setattr(audit, 'GENERATOR_VERSION', 'future-generator')
    with pytest.raises(RevalidationRequired, match='generator or prompt'):
        _validate_audit_proof({'bindings': bindings})
