"""Rename-gap recovery uses fresh capture, never orphan self-approval.

Custody and coordinator fixtures mock financial boundaries explicitly. These
tests establish crash recovery mechanics, not a promoted financial task.
"""
import json
from pathlib import Path
import tempfile

import pytest

from scripts import harbor_finalize as finalizer
from scripts import harbor_retention as retention
from scripts import harbor_verification_capture as capture
from scripts import harbor_verification_package as package
from test_harbor_finalize import setup
from test_harbor_verification_package import binding, custody


def snapshot(root):
    return {str(path.relative_to(root)): package.digest(path) for path in root.rglob('*') if path.is_file()}


def recover(c):
    return package.recover_package(binding(c.source), c.tracker, c.key, c.bundle)


def test_rename_gap_recaptures_identical_native_evidence_without_touching_existing_bundle(custody):
    c = custody
    before = snapshot(c.root)
    assert not c.bundle.with_name(c.bundle.name + '.association.json').exists()
    assert recover(c) == c.expected
    assert len(c.capture_calls) == 2
    assert snapshot(c.root) == before
    assert not list(c.root.glob('.verification-recapture-*'))
    package.inspect_package(c.bundle, c.expected)


@pytest.mark.parametrize('changed', ['manifest', 'object', 'extra_member'])
def test_unknown_or_corrupt_orphan_rejects_and_is_preserved(custody, changed):
    c = custody
    if changed == 'manifest':
        (c.bundle / 'manifest.json').write_text('{}')
    elif changed == 'object':
        obj = next((c.bundle / 'objects').iterdir())
        obj.chmod(0o600)
        obj.write_bytes(b'corrupt retained evidence')
    else:
        (c.bundle / 'unknown').write_text('not part of current native capture')
    before = snapshot(c.root)
    with pytest.raises(package.PackageError):
        recover(c)
    assert snapshot(c.root) == before
    assert len(c.capture_calls) == 2
    assert not list(c.root.glob('.verification-recapture-*'))


def test_fresh_qualification_failure_never_trusts_the_existing_bundle(custody, monkeypatch):
    c = custody
    before = snapshot(c.root)
    def rejected(*args):
        raise ValueError('current owning financial gate rejected')
    monkeypatch.setattr(capture, 'collect', rejected)
    with pytest.raises(ValueError, match='owning financial gate rejected'):
        recover(c)
    assert snapshot(c.root) == before
    assert not list(c.root.glob('.verification-recapture-*'))


@pytest.mark.parametrize('changed', ['source', 'code', 'tracker'])
def test_changed_freshly_captured_context_cannot_adopt_old_package(custody, changed):
    c = custody
    path = {'source': c.source, 'code': c.verifier, 'tracker': c.tracker}[changed]
    path.write_bytes(b'changed currently captured bytes')
    # Simulate a newly qualified native snapshot for the changed context. Even
    # coherent new evidence cannot legitimize the older orphan's manifest.
    for row in c.captured['files']:
        if row['path'] == str(path):
            row['sha256'] = package.digest(path)
    if changed in ('source', 'tracker'):
        c.captured[changed] = binding(path)
    before = snapshot(c.root)
    with pytest.raises(package.PackageError, match='manifest changed'):
        recover(c)
    assert snapshot(c.root) == before
    assert not list(c.root.glob('.verification-recapture-*'))


def test_missing_original_evidence_requires_no_guess_or_adoption(custody):
    c = custody
    c.first.unlink()
    before = snapshot(c.root)
    with pytest.raises(package.PackageError, match='bound file changed'):
        recover(c)
    assert snapshot(c.root) == before
    assert not list(c.root.glob('.verification-recapture-*'))


def test_associated_package_cannot_enter_orphan_recovery(custody):
    c = custody
    c.bundle.with_name(c.bundle.name + '.association.json').write_text('{}')
    before = snapshot(c.root)
    with pytest.raises(package.PackageError, match='associated package'):
        recover(c)
    assert snapshot(c.root) == before and len(c.capture_calls) == 1


def test_finalizer_recovers_after_package_rename_before_association(setup, monkeypatch):
    request, state, scratch, synthetic = setup
    atomic = retention._atomic_json
    interrupted = False
    def crash(parent_fd, name, value):
        nonlocal interrupted
        if name.endswith('.association.json') and not interrupted:
            interrupted = True
            raise InterruptedError('simulated crash after durable package rename')
        return atomic(parent_fd, name, value)
    monkeypatch.setattr(retention, '_atomic_json', crash)
    with pytest.raises(InterruptedError):
        finalizer.finalize(request)
    bundle = Path(request.bundle)
    assert bundle.is_dir() and not bundle.with_name(bundle.name + '.association.json').exists()
    assert (scratch / 'graph').exists() and 'authorize' not in state['calls']
    old_bytes = (bundle / 'manifest.json').read_bytes()
    # This coordinator fixture replaces the package authority boundary, so its
    # recovery adapter mirrors fresh build + exact comparison. The tests above
    # exercise the actual recover_package implementation separately.
    def fresh(source, tracker, *, trusted_public_key, bundle):
        state['calls'].append('recover')
        with tempfile.TemporaryDirectory(dir=Path(bundle).parent) as temporary:
            digest = synthetic.build_package(source, tracker, trusted_public_key=trusted_public_key,
                                             destination=str(Path(temporary) / 'fresh'))
        assert retention.canonical(state['manifest']) == old_bytes
        return digest
    synthetic.recover_package = fresh
    result = finalizer.finalize(request)
    assert result['deleted_files'] == 1 and not (scratch / 'graph').exists()
    assert state['calls'].count('build') == 2 and state['calls'].count('recover') == 1
    association = json.loads(bundle.with_name(bundle.name + '.association.json').read_bytes())
    assert association['manifest_sha256'] == package.digest(bundle / 'manifest.json')
