import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

import xl_source_recalc as recalc
from scripts.batch.harbor_source_evidence import validate_source_lineage
from scripts.corpus import digest
from test_source_recalc import _workbook
from test_corpus_ready_recalculation import case, stop_at_segmentation, ReachedSegmentation


@pytest.fixture
def completed(tmp_path, monkeypatch):
    original = tmp_path / 'original.xlsx'; _workbook(original, cache='99')
    generation = tmp_path / 'generation'; generation.mkdir()
    candidate = generation / '0001.xlsx'; _workbook(candidate, cache='2')
    private = Ed25519PrivateKey.generate(); public = tmp_path / 'public.pem'
    public.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    public.chmod(0o600)
    protected = recalc._trusted_public_key
    monkeypatch.setattr(recalc, '_trusted_public_key',
        lambda path, **kw: protected(path, require_root_owner=False))
    request = recalc.create_recalc_request(original, tmp_path / 'output' / '0001.xlsx',
        allowed_root=tmp_path, engine_constraints={'permitted_versions': ['16.112.2']},
        trusted_runner_public_key=public)
    # A completed historical request can be inspected long after its expiry.
    request.update(created_at_ns=10**9, expires_at_ns=5 * 10**9)
    observed = recalc.semantic_workbook_diff(original, candidate)
    observed['snapshot_supported_equivalent_semantics'] = observed['supported_equivalent_semantics']
    observed['supported_equivalent_semantics'] = observed['equivalent_semantics']
    controls = {k: True for k in ('dedicated_session', 'network_disabled',
        'macros_disabled', 'add_ins_disabled', 'link_updates_disabled', 'prompts_suppressed')}
    result = dict(schema_version='source-recalc-result/v2', policy_version=recalc.POLICY_VERSION,
        request_id=request['request_id'], source_sha256=digest(original),
        output_sha256=digest(candidate), output_size_bytes=candidate.stat().st_size,
        destination=str(tmp_path / 'output' / '0001.xlsx'), authoritative=True,
        engine='excel-macos', engine_version='16.112.2', started_at_ns=2 * 10**9,
        completed_at_ns=3 * 10**9, semantic_diff=observed,
        engine_evidence={'controls': controls})
    (generation / 'generation-manifest.json').write_text(json.dumps({
        'layout': {'source_workbook': '0001.xlsx'},
        'bindings': {'source_sha256': digest(candidate)}}))
    def seal():
        request['request_sha256'] = recalc._unsigned_hash(request, 'request_sha256')
        result['request_sha256'] = request['request_sha256']
        payload = {k: result[k] for k in ('source_sha256', 'output_sha256',
            'request_sha256', 'engine', 'engine_version', 'completed_at_ns')}
        payload.update(calculation_complete=True, isolation_enforced=True,
            isolation_controls=controls, network_isolation_mechanism='macos-pf-anchor',
            network_isolation_rules_sha256=recalc.PF_NETWORK_RULES_SHA256)
        result['engine_evidence']['runner_receipt'] = {
            'schema_version': 'excel-runner-receipt/v1', 'signature_algorithm': 'ed25519',
            'signed_payload': payload,
            'signature_base64': base64.b64encode(private.sign(recalc._canonical_bytes(payload))).decode()}
        result['result_sha256'] = recalc._unsigned_hash(result, 'result_sha256')
        (generation / 'request.json').write_text(json.dumps(request))
        (generation / 'result.json').write_text(json.dumps(result))
    return original, generation, public, request, result, seal


def check(case):
    original, generation, public, request, result, seal = case
    seal()
    return validate_source_lineage(original, generation, trusted_public_key=public)


def test_completed_expired_request_rechecks_real_signature_and_constraints(completed):
    assert check(completed)['completed_request_constraints'] is True


@pytest.mark.parametrize('target,key,value', [
    ('constraints', 'allowed_engines', ['libreoffice']),
    ('constraints', 'required_engine', 'libreoffice'),
    ('constraints', 'require_authoritative', 1),
    ('constraints', 'require_capability_check', False),
    ('constraints', 'permitted_versions', ['*']),
    ('constraints', 'permitted_versions', ['16.112.2', '16.112.2']),
    ('constraints', 'permitted_versions', ['16.999']),
    ('constraints', 'permitted_versions', ['16.112.2', '']),
    ('request', 'source_size_bytes', 1),
    ('request', 'max_source_size_bytes', 0),
    ('request', 'policy', {'allow_cache_only_changes': True}),
    ('request', 'policy', {'allow_cache_only_changes': 1, 'require_semantic_equivalence': True}),
    ('request', 'destination_relative', '../0001.xlsx'),
    ('request', 'destination_relative', '0001.xlsx/../0001.xlsx'),
    ('request', 'expires_at_ns', 10**18),
    ('request', 'policy_version', 'obsolete'),
    ('result', 'started_at_ns', 4 * 10**9),
    ('result', 'started_at_ns', True),
    ('result', 'output_size_bytes', 1),
    ('result', 'request_id', '00000000-0000-0000-0000-000000000000'),
    ('result', 'destination', '/different.xlsx'),
    ('result', 'schema_version', 'source-recalc-result/v1'),
])
def test_signed_and_rehashed_execution_still_must_obey_constraints(completed, target, key, value):
    original, generation, public, request, result, seal = completed
    {'request': request, 'result': result, 'constraints': request['engine_constraints']}[target][key] = value
    with pytest.raises(ValueError): check(completed)


def test_unsigned_controls_cannot_contradict_signed_controls(completed):
    result = completed[4]
    result['engine_evidence']['controls'] = dict(result['engine_evidence']['controls'], network_disabled=1)
    with pytest.raises(ValueError): check(completed)


def test_signature_is_not_replaced_by_well_formed_constraints(completed):
    original, generation, public, request, result, seal = completed
    seal()
    result['engine_evidence']['runner_receipt']['signature_base64'] = base64.b64encode(bytes(64)).decode()
    result['result_sha256'] = recalc._unsigned_hash(result, 'result_sha256')
    (generation / 'result.json').write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate_source_lineage(original, generation, trusted_public_key=public)


def test_request_source_record_cannot_nominate_unrelated_bytes(completed):
    original, generation, public, request, result, seal = completed
    other = original.parent / 'other.xlsx'; _workbook(other, cache='42')
    request['source']['path'] = str(other)
    with pytest.raises(ValueError): check(completed)


def test_admission_rejects_real_signed_generation_with_nonconcrete_version(case, monkeypatch):
    """A signed source can pass old lineage while violating the admission profile."""
    from pathlib import Path
    from types import SimpleNamespace
    from scripts import corpus_build
    from scripts.batch.harbor_admission import DispatcherAdmission, record
    args, source, ledger, job, _ = case
    stop_at_segmentation(monkeypatch)
    with pytest.raises(ReachedSegmentation):
        corpus_build.worker(args, source)
    build = json.loads(ledger.read_text())['build']
    generation = Path(args.run_root) / 'sources/0001/generations' / build['source_generation_id']
    facade = object.__new__(DispatcherAdmission)
    facade.policy = SimpleNamespace(trusted_public_key=args.trusted_public_key)
    facade.sources = {'0001': dict(source, size_bytes=Path(source['path']).stat().st_size)}
    # The fixture runs actual publication and Ed25519 verification, but its
    # invented engine version must never be admitted as a concrete Excel build.
    with pytest.raises(ValueError, match='completed version'):
        facade._source('0001', record(job / '0001.xlsx'),
                       record(generation / 'generation-manifest.json'), ())
