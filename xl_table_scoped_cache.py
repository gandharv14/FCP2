"""Explicit selected-cache proof after an authenticated table transformation.

The original workbook remains the policy subject. Projection bytes are a
separate, fully verified edge; only their manual calculation flag may change.
"""
from pathlib import Path
import json
import time
import tomllib
import zipfile
import xl_source_cache_equivalence as C

POLICY = 'presentation-table-selected-cache-policy/v1'
SCHEMA = 'presentation-table-selected-cache-proof/v1'


def policy(row, original, *, at=None):
    p = C.read(row)
    C.require(set(p) == {'schema_version', 'source', 'projection_sha256',
        'original_curation', 'original_inspection', 'curation', 'ordered_outputs', 'excluded_bands',
        'prior_failure', 'authorization', 'created_at_ns', 'expires_at_ns',
        'max_formula_cells', 'relative_tolerance'} and p['schema_version'] == POLICY,
        'closed table-cache policy required')
    C.require(p['source'] == C.binding(original), 'table-cache original source differs')
    C.require(C.binding(p['authorization']['path']) == p['authorization']
        and p['authorization']['sha256'] == C.AUTHORIZATION_SHA256,
        'table-cache authorization differs')
    C.require(type(p['created_at_ns']) is int and type(p['expires_at_ns']) is int
        and p['created_at_ns'] < p['expires_at_ns'] <= p['created_at_ns'] + 86400 * 10**9
        and p['created_at_ns'] <= (time.time_ns() if at is None else at) < p['expires_at_ns'],
        'table-cache policy expired or unbounded')
    C.require(type(p['max_formula_cells']) is int and 0 < p['max_formula_cells'] <= 30000
        and type(p['relative_tolerance']) is float and p['relative_tolerance'] == 1e-6,
        'table-cache numerical scope differs')
    from xl_source_scoped_cache import _text
    # Authenticate the original candidate bands against their actual source
    # inspection, including a failed numerical generation. It stays failed.
    C.read(p['original_inspection'])
    generation = Path(p['original_inspection']['path']).parent
    from xl_seg.publication import resolve_generation_by_id
    from xl_presentation_tables import inspection_metadata, verify_inspection_source
    inspection = generation.parents[3]
    inspected = inspection/'inspection-source'/Path(original).name
    _, _, metadata = inspection_metadata(original)
    verify_inspection_source(original, inspected, metadata)
    _, manifest = resolve_generation_by_id(generation.parents[1], generation.name,
        source_path=inspected, ast_dir=inspection/'ast'/Path(original).stem,
        require_pass=False, validate_live_evidence=True)
    C.require(C.binding(generation/'generation-manifest.json') == p['original_inspection']
        and C.binding(generation/'curation.toml')['sha256'] == p['original_curation']['sha256'],
        'original table selection provenance differs')
    before = tomllib.loads(_text(p['original_curation']))
    after = tomllib.loads(_text(p['curation']))
    excluded = p['excluded_bands']
    C.require(isinstance(excluded, list) and excluded and len(set(excluded)) == len(excluded),
        'exact excluded whole bands required')
    found = set()
    for entry in before['output']:
        if entry['band'] in excluded:
            C.require(entry['include'] is True, 'excluded band was not selected')
            entry['include'] = False
            found.add(entry['band'])
    C.require(found == set(excluded) and before == after, 'table scope changed beyond band inclusion')
    outputs = p['ordered_outputs']
    C.require(isinstance(outputs, list) and 0 < len(outputs) <= 100
        and all(isinstance(x, str) for x in outputs) and len(set(outputs)) == len(outputs),
        'bounded distinct table outputs required')
    prior = C.read(p['prior_failure'])
    C.require(prior['models'] == prior['claims'] == 0
        and prior['result']['state'] == 'STOPPED'
        and prior['result']['stage'] == 'presentation_tables'
        and prior['result']['bindings']['source_sha256'] == p['source']['sha256'],
        'original table failure differs')
    return p


def projection_scope(original, projected, receipt_row, policy_row, *, at):
    from xl_presentation_tables import verify_projection
    p = policy(policy_row, original, at=at)
    receipt = C.read(receipt_row)
    C.require('cache_equivalence' not in receipt
        and C.digest(projected) == p['projection_sha256'], 'table projection differs')
    verify_projection(original, projected, p['curation']['path'], receipt)
    C.require(receipt['curation_sha256'] == p['curation']['sha256']
        and receipt['ordered_outputs'] == p['ordered_outputs'], 'table projection scope differs')
    return dict(schema_version=SCHEMA, original=C.binding(original),
        projection_receipt=receipt_row, policy=policy_row, authorized_at_ns=at,
        projected_sha256=p['projection_sha256'], original_curation=p['original_curation'],
        curation=p['curation'], ordered_outputs=p['ordered_outputs'])


def validate_scope(projected, scope):
    expected = projection_scope(scope['original']['path'], projected,
        scope['projection_receipt'], scope['policy'], at=scope['authorized_at_ns'])
    C.require(scope == expected, 'authenticated table proof scope changed')
    return expected


def create(original, projected, receipt_row, policy_row, candidate):
    at = time.time_ns()
    scope = projection_scope(original, projected, receipt_row, policy_row, at=at)
    p = policy(policy_row, original, at=at)
    from xl_source_scoped_cache import prove
    proof = prove(projected, scope, p['max_formula_cells'], p['relative_tolerance'])
    infos, before = C.package(projected)
    after = C.automatic_members(before)
    with zipfile.ZipFile(candidate, 'x') as archive:
        for info in infos:
            archive.writestr(info, after[info.filename])
    edge = dict(schema_version=SCHEMA, scope=scope, projected=C.binding(projected),
        candidate=C.binding(candidate), proof=proof, recalculation_performed=False,
        models_dispatched=0)
    verify(original, candidate, edge)
    return edge


def verify(original, candidate, edge):
    C.require(set(edge) == {'schema_version', 'scope', 'projected', 'candidate', 'proof',
        'recalculation_performed', 'models_dispatched'} and edge['schema_version'] == SCHEMA
        and edge['recalculation_performed'] is False and edge['models_dispatched'] == 0,
        'closed table-cache edge required')
    scope = edge['scope']
    C.require(scope['original'] == C.binding(original)
        and C.binding(edge['projected']['path']) == edge['projected']
        and C.binding(candidate) == edge['candidate'], 'table-cache lineage changed')
    projected = edge['projected']['path']
    validate_scope(projected, scope)
    p = policy(scope['policy'], original, at=scope['authorized_at_ns'])
    from xl_source_scoped_cache import prove
    C.require(prove(projected, scope, p['max_formula_cells'], p['relative_tolerance']) == edge['proof'],
        'fresh table-cache numerical proof differs')
    C.require(C.package(candidate)[1] == C.automatic_members(C.package(projected)[1]),
        'table-cache candidate changed formula/cache/package bytes')
    from xl_source_health import inspect_workbook
    C.require(inspect_workbook(candidate)['route'] == 'pass', 'table-cache source barrier remains')
    return {'status': 'verified', 'whole_workbook_equivalence': False}
