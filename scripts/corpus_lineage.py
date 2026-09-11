"""Revalidate an original workbook's supported transformation lineage.

Declarations and cached calculated answers never establish source identity.
This supplements (and does not replace) current generation and task validators.
"""
import json
from pathlib import Path

from scripts.corpus import digest

DEFAULT_TRUSTED_PUBLIC_KEY = '/etc/harbor/excel-runner-public.pem'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bound_json(record, key):
    path = Path(record[key])
    _require(digest(path) == record[key + '_sha256'], 'lineage evidence bytes changed')
    return json.loads(path.read_text())


def validate_normalization(original, candidate, record):
    """Replay an approved kind of transformation from exact original bytes."""
    _require(record['original_sha256'] == digest(original), 'lineage original changed')
    _require(record['candidate_sha256'] == digest(candidate), 'lineage candidate changed')
    recorded_candidate = Path(record['candidate'])
    _require(digest(recorded_candidate) == digest(candidate), 'retained candidate changed')
    manifest = _bound_json(record, 'manifest')
    if record.get('policy_version') == 'quarantined-table-source-inventory/v1':
        from xl_table_quarantine_inventory import validate_adoption
        return validate_adoption(original, candidate, record)
    if record.get('policy_version') == 'presentation-data-tables/v2':
        from xl_presentation_tables import verify_materialization
        verify_materialization(original, recorded_candidate, record['curation'], manifest)
    elif record.get('plan'):
        from xl_volatile_formula_remediation import verify_manifest
        verify_manifest(original, recorded_candidate, _bound_json(record, 'plan'), manifest)
    else:
        raise ValueError('unsupported or incomplete source transformation lineage')
    return {'original_sha256': digest(original), 'candidate_sha256': digest(candidate),
            'manifest_sha256': record['manifest_sha256']}


def validate_generation_lineage(original, generation, normalization_records=(), *,
                                trusted_public_key=DEFAULT_TRUSTED_PUBLIC_KEY):
    """Bind a validated generation to an original, optionally via normalization/Excel."""
    original, generation = Path(original), Path(generation)
    manifest = json.loads((generation / 'generation-manifest.json').read_text())
    candidate = generation / manifest['layout']['source_workbook']
    original_hash, final_hash = digest(original), digest(candidate)
    _require(final_hash == manifest['bindings']['source_sha256'], 'generation source changed')
    request = json.loads((generation / 'request.json').read_text())
    result = json.loads((generation / 'result.json').read_text())
    recalc = request.get('schema_version') == 'source-recalc-request/v2'
    # Unchanged Excel output still carries a recalculation claim. Validate its
    # signature and full bindings rather than treating identical bytes as proof
    # that the recorded trusted execution happened.
    if final_hash == original_hash and not recalc:
        return {'mode': 'original', 'original_sha256': original_hash, 'source_sha256': final_hash}
    predecessor_hash = request.get('source_sha256') if recalc else final_hash
    predecessor = original
    proof = None
    if predecessor_hash != original_hash:
        matches = [r for r in normalization_records
                   if r.get('original_sha256') == original_hash
                   and r.get('candidate_sha256') == predecessor_hash]
        _require(bool(matches), 'missing independently verifiable transformation lineage')
        # The latest explicit checkpoint supersedes historical proof versions.
        # If it is corrupt, do not fall back to an older successful receipt.
        record = matches[-1]
        proof = validate_normalization(original, record['candidate'], record)
        predecessor = Path(record['candidate'])
    if recalc:
        from xl_source_recalc import (_unsigned_hash, semantic_workbook_diff,
                                     verify_signed_runner_receipt)
        _require(request.get('request_sha256') == _unsigned_hash(request, 'request_sha256'),
                 'recalculation request hash changed')
        _require(result.get('result_sha256') == _unsigned_hash(result, 'result_sha256'),
                 'recalculation result hash changed')
        _require(result.get('source_sha256') == predecessor_hash
                 and result.get('output_sha256') == final_hash
                 and result.get('request_sha256') == request['request_sha256']
                 and result.get('authoritative') is True,
                 'recalculation lineage bindings changed')
        _require(request.get('engine_constraints', {}).get('trusted_runner_public_key_sha256')
                 == digest(trusted_public_key), 'recalculation trust anchor changed')
        signed = verify_signed_runner_receipt(result['engine_evidence']['runner_receipt'],
            trusted_public_key, request_sha256=request['request_sha256'],
            source_sha256=predecessor_hash, output_sha256=final_hash,
            request_created_at_ns=request['created_at_ns'],
            request_expires_at_ns=request['expires_at_ns'])
        _require(all(signed.get(k) == result.get(k)
                     for k in ('engine', 'engine_version', 'completed_at_ns')),
                 'recalculation signed result differs')
        observed = semantic_workbook_diff(predecessor, candidate)
        observed['snapshot_supported_equivalent_semantics'] = observed['supported_equivalent_semantics']
        observed['supported_equivalent_semantics'] = observed['equivalent_semantics']
        _require(observed['equivalent_semantics'] is True and observed == result.get('semantic_diff'),
                 'recalculation semantics changed')
    return {'mode': 'recalculation' if recalc else 'normalization',
            'original_sha256': original_hash, 'source_sha256': final_hash,
            'normalization': proof}
