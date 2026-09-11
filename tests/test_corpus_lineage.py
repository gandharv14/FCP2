import json
import shutil
from pathlib import Path

import pytest

from scripts.corpus import digest
from scripts.corpus_lineage import validate_generation_lineage
from xl_volatile_formula_remediation import build_plan, apply_plan
from test_volatile_formula_remediation import _workbook


def fixture(tmp_path):
    original = tmp_path / 'original.xlsx'
    _workbook(original)
    plan = build_plan(original)
    candidate = tmp_path / 'candidate.xlsx'
    receipt = apply_plan(original, plan, candidate)
    pp, rp = tmp_path / 'plan.json', tmp_path / 'manifest.json'
    pp.write_text(json.dumps(plan)); rp.write_text(json.dumps(receipt))
    record = {'original_sha256': digest(original), 'candidate': str(candidate),
        'candidate_sha256': digest(candidate), 'plan': str(pp), 'plan_sha256': digest(pp),
        'manifest': str(rp), 'manifest_sha256': digest(rp)}
    generation = tmp_path / 'generation'
    generation.mkdir()
    shutil.copyfile(candidate, generation / 'source.xlsx')
    (generation / 'generation-manifest.json').write_text(json.dumps({
        'layout': {'source_workbook': 'source.xlsx'},
        'bindings': {'source_sha256': digest(candidate)}}))
    (generation / 'request.json').write_text('{"schema_version":"source-identity-request/v2"}')
    (generation / 'result.json').write_text('{}')
    return original, generation, record


def test_real_transform_is_bound_to_original_and_replayed(tmp_path):
    original, generation, record = fixture(tmp_path)
    result = validate_generation_lineage(original, generation, [record])
    assert result['mode'] == 'normalization'
    assert result['original_sha256'] != result['source_sha256']


@pytest.mark.parametrize('alter', ['missing', 'manifest', 'plan', 'candidate', 'original', 'policy'])
def test_declarations_cannot_replace_lineage_evidence(tmp_path, alter):
    original, generation, record = fixture(tmp_path)
    records = [record]
    if alter == 'missing':
        records = []
    elif alter == 'original':
        original.write_bytes(b'changed original')
    elif alter == 'policy':
        record.pop('plan')
    else:
        Path(record[alter]).write_bytes(b'changed evidence')
    with pytest.raises((ValueError, KeyError)):
        validate_generation_lineage(original, generation, records)


def test_original_bytes_need_no_transformation_declaration(tmp_path):
    original, generation, _ = fixture(tmp_path)
    shutil.copyfile(original, generation / 'source.xlsx')
    manifest = json.loads((generation / 'generation-manifest.json').read_text())
    manifest['bindings']['source_sha256'] = digest(original)
    (generation / 'generation-manifest.json').write_text(json.dumps(manifest))
    assert validate_generation_lineage(original, generation)['mode'] == 'original'


def recalculated(tmp_path, monkeypatch):
    import xl_source_recalc as recalc
    from test_source_recalc import _workbook as cached_workbook
    original = tmp_path / 'original.xlsx'; cached_workbook(original, cache='99')
    generation = tmp_path / 'generation'; generation.mkdir()
    candidate = generation / 'source.xlsx'; cached_workbook(candidate, cache='2')
    key = tmp_path / 'trusted.pem'; key.write_text('independently configured trust anchor')
    request = {'schema_version': 'source-recalc-request/v2', 'source_sha256': digest(original),
        'created_at_ns': 1, 'expires_at_ns': 3,
        'engine_constraints': {'trusted_runner_public_key_sha256': digest(key)}}
    request['request_sha256'] = recalc._unsigned_hash(request, 'request_sha256')
    diff = recalc.semantic_workbook_diff(original, candidate)
    diff['snapshot_supported_equivalent_semantics'] = diff['supported_equivalent_semantics']
    diff['supported_equivalent_semantics'] = diff['equivalent_semantics']
    result = {'source_sha256': digest(original), 'output_sha256': digest(candidate),
        'request_sha256': request['request_sha256'], 'authoritative': True,
        'engine': 'excel-macos', 'engine_version': 'test', 'completed_at_ns': 2,
        'engine_evidence': {'runner_receipt': {'fixture': 'signed evidence'}},
        'semantic_diff': diff}
    result['result_sha256'] = recalc._unsigned_hash(result, 'result_sha256')
    (generation / 'generation-manifest.json').write_text(json.dumps({
        'layout': {'source_workbook': 'source.xlsx'}, 'bindings': {'source_sha256': digest(candidate)}}))
    (generation / 'request.json').write_text(json.dumps(request))
    (generation / 'result.json').write_text(json.dumps(result))
    calls = []
    def verified(receipt, trust, **bindings):
        calls.append((receipt, trust, bindings))
        return {k: result[k] for k in ('engine', 'engine_version', 'completed_at_ns')}
    monkeypatch.setattr(recalc, 'verify_signed_runner_receipt', verified)
    return original, generation, key, result, calls


def test_recalculation_rechecks_signature_bindings_and_semantics(tmp_path, monkeypatch):
    original, generation, key, _, calls = recalculated(tmp_path, monkeypatch)
    result = validate_generation_lineage(original, generation, trusted_public_key=key)
    assert result['mode'] == 'recalculation'
    assert len(calls) == 1
    assert calls[0][2]['source_sha256'] == digest(original)
    assert calls[0][2]['request_expires_at_ns'] == 3
    assert calls[0][2]['request_created_at_ns'] == 1


@pytest.mark.parametrize('failure', ['signature', 'semantic_diff', 'trust_anchor', 'result_hash'])
def test_recalculation_claims_do_not_substitute_independent_proof(tmp_path, monkeypatch, failure):
    import xl_source_recalc as recalc
    original, generation, key, result, _ = recalculated(tmp_path, monkeypatch)
    if failure == 'signature':
        def reject(*a, **k):
            raise ValueError('forged signature')
        monkeypatch.setattr(recalc, 'verify_signed_runner_receipt', reject)
    elif failure == 'trust_anchor':
        key.write_text('different key')
    else:
        result['semantic_diff']['formula_cache_changes'] = 999
        if failure == 'semantic_diff':
            result['result_sha256'] = recalc._unsigned_hash(result, 'result_sha256')
        (generation / 'result.json').write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate_generation_lineage(original, generation, trusted_public_key=key)


@pytest.mark.parametrize('tamper', [None, 'signature', 'signed_metadata', 'trust_anchor'])
def test_unchanged_recalculation_bytes_still_require_real_signature(tmp_path, monkeypatch, tamper):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    import xl_source_recalc as recalc
    # Build the same nontrivial workbook fixture, then retain identical output
    # bytes. Restore the real signature verifier after the fixture's test double.
    verifier = recalc.verify_signed_runner_receipt
    original, generation, public, result, _ = recalculated(tmp_path, monkeypatch)
    monkeypatch.setattr(recalc, 'verify_signed_runner_receipt', verifier)
    protected = recalc._trusted_public_key
    monkeypatch.setattr(recalc, '_trusted_public_key',
                        lambda p, **kw: protected(p, require_root_owner=False))
    key = Ed25519PrivateKey.generate()
    public.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    public.chmod(0o600)
    candidate = generation / 'source.xlsx'
    shutil.copyfile(original, candidate)
    manifest = json.loads((generation / 'generation-manifest.json').read_text())
    manifest['bindings']['source_sha256'] = digest(candidate)
    (generation / 'generation-manifest.json').write_text(json.dumps(manifest))
    request = json.loads((generation / 'request.json').read_text())
    request['engine_constraints']['trusted_runner_public_key_sha256'] = digest(public)
    request['request_sha256'] = recalc._unsigned_hash(request, 'request_sha256')
    (generation / 'request.json').write_text(json.dumps(request))
    observed = recalc.semantic_workbook_diff(original, candidate)
    observed['snapshot_supported_equivalent_semantics'] = observed['supported_equivalent_semantics']
    observed['supported_equivalent_semantics'] = observed['equivalent_semantics']
    result.update(output_sha256=digest(candidate), request_sha256=request['request_sha256'],
                  semantic_diff=observed)
    payload = {k: result[k] for k in ('source_sha256', 'output_sha256', 'request_sha256',
                                    'engine', 'engine_version', 'completed_at_ns')}
    payload.update(calculation_complete=True, isolation_enforced=True,
        isolation_controls={k: True for k in ('dedicated_session', 'network_disabled',
            'macros_disabled', 'add_ins_disabled', 'link_updates_disabled', 'prompts_suppressed')},
        network_isolation_mechanism='macos-pf-anchor',
        network_isolation_rules_sha256=recalc.PF_NETWORK_RULES_SHA256)
    result['engine_evidence']['runner_receipt'] = {
        'schema_version': 'excel-runner-receipt/v1', 'signature_algorithm': 'ed25519',
        'signed_payload': payload,
        'signature_base64': base64.b64encode(key.sign(recalc._canonical_bytes(payload))).decode()}
    if tamper == 'signature':
        result['engine_evidence']['runner_receipt']['signature_base64'] = base64.b64encode(bytes(64)).decode()
    elif tamper == 'signed_metadata':
        result['completed_at_ns'] = 3
    elif tamper == 'trust_anchor':
        public.write_bytes(b'changed key')
    result['result_sha256'] = recalc._unsigned_hash(result, 'result_sha256')
    (generation / 'result.json').write_text(json.dumps(result))
    if tamper:
        with pytest.raises(ValueError):
            validate_generation_lineage(original, generation, trusted_public_key=public)
    else:
        assert validate_generation_lineage(original, generation,
            trusted_public_key=public)['mode'] == 'recalculation'
