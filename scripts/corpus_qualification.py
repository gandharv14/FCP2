"""Revalidate terminal Harbor evidence and bind it to each original's ledger."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import re

from scripts.corpus import read_inventory, digest
from scripts.corpus_lineage import validate_generation_lineage, DEFAULT_TRUSTED_PUBLIC_KEY
from scripts.batch.hardened_runner import atomic_write_json
from xl_inventory_approval import object_hash
import xl_release_publication as release
import xl_source_publication as source_publication
from xl_seg import publication as segmentation_publication

REQUIRED_LANES = {
    'harbor-source-warden', 'harbor-segmentation-analyst', 'harbor-research-auditor',
    'harbor-normalization-engineer', 'harbor-source-profiler', 'harbor-environment-packager',
    'harbor-disclosure-writer', 'harbor-instruction-naturalizer',
    'harbor-independent-verifier', 'harbor-dialogue-author',
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _verify_hash(path, expected):
    path = Path(path)
    actual = release._tree_hash(path)[0] if path.is_dir() else digest(path)
    _require(actual == expected, f"changed qualification evidence: {path}")


class RevalidationRequired(ValueError):
    """A valid historical promotion lacks proof for the running code."""


def _validate_current_proof(tracker, source_root):
    ids = tracker['generation_ids']
    source_dir = Path(source_root) / 'generations' / ids['source']
    # The caller has checked immutable artifact integrity. Compare the builder
    # before current-code validation, which correctly rejects historical ASTs.
    source_manifest = json.loads((source_dir / 'generation-manifest.json').read_text())
    recorded = source_manifest['ast_provenance']['builder']['code']
    _require(isinstance(recorded, list) and bool(recorded), 'missing AST builder evidence')
    current_paths = source_publication._default_builder_paths()
    old = sorted((r['name'], r['sha256'], r['size_bytes']) for r in recorded)
    current = sorted((p.name, digest(p), p.stat().st_size) for p in current_paths)
    if old != current:
        raise RevalidationRequired('AST builder code changed; restart source AST generation')
    source_publication.validate_source_generation(
        source_dir, expected_generation_id=ids['source'])
    layout = source_manifest['layout']
    segroot = Path(tracker['bindings']['segmentation_workbook_root']['path'])
    try:
        segmentation_publication.resolve_generation_by_id(
            segroot, ids['segmentation'],
            source_path=source_dir / layout['source_workbook'],
            ast_dir=source_dir / layout['ast_directory'],
            source_generation_dir=source_dir,
            require_pass=True, validate_live_evidence=True)
    except ValueError as exc:
        raise RevalidationRequired(f'strict segmentation live proof requires refresh: {exc}') from exc



def _validate_audit_proof(tracker):
    bindings = tracker['bindings']
    required = ('variable_source_audit', 'variable_source_inventory', 'variable_source_metadata')
    if any(not isinstance(bindings.get(key), dict) for key in required):
        raise RevalidationRequired('audit inventory coverage evidence requires refresh')
    for key in required:
        _verify_hash(bindings[key]['path'], bindings[key]['sha256'])
    metadata = json.loads(Path(bindings['variable_source_metadata']['path']).read_text())
    if not metadata.get('coverage_path') or not metadata.get('coverage_sha256'):
        raise RevalidationRequired('complete audit inventory dispositions require revalidation')
    from scripts.qualified_publication import validate_audit_bindings
    from xl_variable_source_audit import AuditRevalidationRequired
    try:
        validate_audit_bindings(bindings, expected_generations=tracker.get('generation_ids'))
    except AuditRevalidationRequired as exc:
        raise RevalidationRequired(str(exc)) from exc



class HarborTrackerConflict(ValueError):
    def __init__(self, candidates):
        self.candidates = candidates
        super().__init__('multiple selected Harbor trackers for one original workbook')


def selected_harbor_roots(run, harbor_roots=()):
    """Default root plus explicitly selected, distinct namespaces within this run."""
    run = Path(run).resolve()
    base = run / 'harbor'
    result = [base]
    for requested in harbor_roots:
        raw = Path(requested)
        _require('..' not in raw.parts, 'Harbor root traversal is not permitted')
        raw = raw if raw.is_absolute() else run / raw
        chosen = raw.resolve()
        _require(base in chosen.parents, 'additional Harbor root must be within RUN/harbor')
        _require(chosen not in result, 'duplicate selected Harbor root')
        _require(raw.is_dir() and (raw / 'workbooks').is_dir(), 'selected Harbor root/workbooks is absent')
        for ancestor in (raw, *raw.parents):
            if ancestor.resolve() == run:
                break
            _require(not ancestor.is_symlink(), 'Harbor root symlink aliases are not permitted')
        _require(not (raw / 'workbooks').is_symlink(), 'tracker directory symlink is not permitted')
        result.append(chosen)
    _require(not base.is_symlink() and not (base / 'workbooks').is_symlink(), 'default Harbor root symlink is not permitted')
    _require(not base.exists() or base.is_dir(), 'default Harbor root is not a directory')
    _require(not (base / 'workbooks').exists() or (base / 'workbooks').is_dir(),
             'default tracker root is not a directory')
    return tuple(result)


def tracker_candidates(source, roots):
    """Take a bounded exact-name snapshot, never discover/promote by output scans."""
    wb = source['workbook_id']
    _require(isinstance(wb, str) and wb not in ('', '.', '..') and Path(wb).name == wb,
             'invalid original workbook identity')
    candidates = []
    for root in roots:
        _require(root.resolve() == root and not (root / 'workbooks').is_symlink(),
                 'selected Harbor namespace changed or became a symlink')
        path = root / 'workbooks' / (wb + '.json')
        _require(not path.is_symlink(), 'tracker symlink aliases are not permitted')
        if path.exists():
            _require(path.is_file(), 'tracker path is not a file')
            candidates.append({'harbor_root': str(root), 'path': str(path), 'sha256': digest(path)})
    return candidates


def _unique_sources(manifest):
    ids = [row['workbook_id'] for row in manifest['sources']]
    paths = [str(Path(row['path']).resolve()) for row in manifest['sources']]
    _require(len(ids) == len(set(ids)) and len(paths) == len(set(paths)),
             'original inventory identities/paths must be unique; denominator cannot be deduplicated silently')


PRIVATE_SELECTION_SCHEMA = 'harbor-report-private-verifications/v1'


def _private_path(value):
    _require(type(value) is str and value and Path(value).is_absolute()
             and str(Path(value)) == value and '..' not in Path(value).parts
             and not value.startswith('//') and not any(ord(c) < 32 for c in value),
             'private verification path must be canonical and absolute')
    path = Path(value)
    _require(not any(p.is_symlink() for p in (path, *path.parents)),
             'private verification path symlink is not permitted')
    return path


def _private_digest(value):
    _require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value),
             'private verification digest must be exact SHA256')


def _private_document(binding):
    from scripts.batch.harbor_tracker import strict_json
    _require(type(binding) is dict and set(binding) == {'path', 'sha256'},
             'private verification selection requires a path/SHA256 binding')
    path = _private_path(binding['path']); _private_digest(binding['sha256'])
    _require(path.is_file(), 'private verification selection file missing')
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == binding['sha256'],
             'private verification selection file changed')
    return strict_json(raw)


def _load_private_verifications(binding, manifest, roots):
    """Closed caller-pinned selection; never discover sidecars or fallback."""
    from scripts.batch.harbor_promotion_evidence import PrivateVerificationSelection
    document = _private_document(binding)
    _require(type(document) is dict and set(document) == {'schema_version', 'inventory_sha256', 'workbooks'}
             and document['schema_version'] == PRIVATE_SELECTION_SCHEMA
             and document['inventory_sha256'] == manifest['inventory_sha256'],
             'private verification selection inventory/schema differs')
    entries = document['workbooks']
    _require(type(entries) is list and entries, 'private verification selection must name workbooks')
    originals = {row['workbook_id']: row for row in manifest['sources']}
    result = {}
    fields = {'workbook_id', 'original_sha256', 'harbor_root', 'tracker',
              'bundle', 'manifest_sha256', 'verifier_image'}
    for row in entries:
        _require(type(row) is dict and set(row) == fields, 'private verification workbook fields differ')
        workbook = row['workbook_id']
        _require(type(workbook) is str and workbook in originals and workbook not in result,
                 'private verification workbook is foreign or duplicated')
        _require(row['original_sha256'] == originals[workbook]['sha256'],
                 'private verification original workbook differs')
        root = _private_path(row['harbor_root'])
        _require(root in roots, 'private verification Harbor namespace was not explicitly selected')
        tracker = row['tracker']
        _require(type(tracker) is dict and set(tracker) == {'path', 'sha256'},
                 'private verification tracker binding is required')
        _require(_private_path(tracker['path']) == root / 'workbooks' / (workbook + '.json'),
                 'private verification tracker belongs to another workbook or namespace')
        _private_digest(tracker['sha256']); _private_digest(row['manifest_sha256'])
        bundle = _private_path(row['bundle'])
        _require(type(row['verifier_image']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', row['verifier_image']),
                 'private verification image must be an immutable local ID')
        result[workbook] = {'tracker': tracker,
            'selection': PrivateVerificationSelection(bundle, row['manifest_sha256'], row['verifier_image'])}
    _require(_private_document(binding) == document, 'private verification selection changed during loading')
    return result


def validate_terminal(source, run, *, trusted_public_key=DEFAULT_TRUSTED_PUBLIC_KEY, harbor_roots=(), historical_retirement=None,
                      private_verification=None):
    _require(historical_retirement is None or private_verification is None,
             'historical retirement and private promotion selections cannot overlap')
    roots = selected_harbor_roots(run, harbor_roots)
    candidates = tracker_candidates(source, roots)
    if len(candidates) > 1:
        raise HarborTrackerConflict(candidates)
    if not candidates:
        _require(historical_retirement is None and private_verification is None, 'selected evidence tracker is missing')
        return {'state': 'NOT_RUN'}
    chosen = candidates[0]
    tracker = Path(chosen['path']); root = Path(chosen['harbor_root'])
    # No report-only lock creation. Inspect the same selected namespace throughout.
    lock_path = Path(str(tracker) + '.lock')
    _require(not lock_path.is_symlink(), 'tracker lock symlink is not permitted')
    def checked():
        result = _validate_terminal(source, run, trusted_public_key=trusted_public_key, harbor_root=root,
            historical_retirement=historical_retirement,
            **({'private_verification': private_verification} if private_verification is not None else {}))
        _require(tracker_candidates(source, roots) == candidates,
                 'selected tracker set changed during read-only qualification')
        if harbor_roots:
            result = {**result, 'harbor_root': str(root)}
        return result
    if lock_path.is_file():
        with lock_path.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return checked()
    return checked()


def _validate_terminal(source, run, *, trusted_public_key=DEFAULT_TRUSTED_PUBLIC_KEY, harbor_root=None, historical_retirement=None,
                       private_verification=None):
    """A tracker status alone is never sufficient for a yield numerator."""
    wb = source['workbook_id']
    root = Path(harbor_root) if harbor_root is not None else Path(run) / 'harbor'
    tracker_path = root / 'workbooks' / f'{wb}.json'
    if not tracker_path.is_file():
        return {'state': 'NOT_RUN'}
    tracker = json.loads(tracker_path.read_text())
    state = tracker.get('execution_state')
    _require(tracker.get('workbook_id') == wb, 'tracker workbook mismatch')
    if private_verification is not None:
        from scripts.batch.harbor_promotion_evidence import PrivateVerificationSelection, validate
        _require(type(private_verification) is PrivateVerificationSelection,
                 'typed private verification selection required')
        _require(historical_retirement is None and state == 'PROMOTED' and any(
            event.get('schema_version') == 'harbor-tracker-created/v1' for event in tracker.get('handoffs', [])),
            'private verification requires the selected strict promoted tracker')
        return validate(source, tracker_path, trusted_public_key=trusted_public_key,
                        private_verification=private_verification)
    if historical_retirement is not None:
        _require(state == 'STOPPED', 'historical selection requires the exact terminal tracker')
        from scripts.harbor_retirement_reporting import validate as validate_retirement
        return validate_retirement(source, tracker_path, root, historical_retirement)
    if state not in {'PROMOTED', 'STOPPED'}:
        return {'state': 'RUNNING'}
    if state == 'PROMOTED' and any(event.get('schema_version') == 'harbor-legacy-requalification-chain/v2'
            for event in tracker.get('handoffs', [])):
        from scripts.harbor_legacy_refresh import validate
        return validate(source, tracker_path, trusted_public_key=trusted_public_key)
    if state == 'PROMOTED' and any(event.get('schema_version') == 'harbor-legacy-requalification/v1'
            for event in tracker.get('handoffs', [])):
        from scripts.batch.harbor_legacy_requalification import validate
        return validate(source, tracker_path, trusted_public_key=trusted_public_key)
    if state == 'PROMOTED' and any(event.get('schema_version') == 'harbor-tracker-created/v1'
            for event in tracker.get('handoffs', [])):
        from scripts.batch.harbor_promotion_evidence import validate
        return validate(source, tracker_path, trusted_public_key=trusted_public_key)
    if state == 'STOPPED':
        raw = tracker.get('raw_source', {})
        if raw.get('sha256') != source['sha256']:
            # Intake may bind a candidate before a later lane stops. Retain the
            # original denominator without treating that stop as transformation
            # approval or requiring the failed lane to have produced a release.
            original = tracker.get('bindings', {}).get('original_raw_source', {})
            _require(original.get('sha256') == source['sha256'],
                     'tracker original source mismatch')
            _verify_hash(original['path'], source['sha256'])
            _require(bool(raw.get('sha256')), 'missing candidate source identity')
            _verify_hash(raw['path'], raw['sha256'])
    _verify_hash(source['path'], source['sha256'])
    notes = tracker.get('agent_notes') or {}
    _verify_hash(notes['path'], notes['sha256'])
    failure = tracker.get('promotion_failure')
    common = {'state': state, 'tracker': str(tracker_path.resolve()),
              'tracker_sha256': digest(tracker_path), 'failure': failure,
              'generation_ids': tracker.get('generation_ids')}
    if state == 'STOPPED':
        _require(isinstance(failure, dict) and bool(failure.get('subclass')),
                 'stopped workbook has no classified failure')
        _require(notes.get('diagnostic_signature') == failure.get('diagnostic_signature')
                 and bool(failure.get('diagnostic_signature')), 'failure notes signature mismatch')
        evidence = failure.get('evidence_paths')
        _require(isinstance(evidence, list) and bool(evidence), 'failure has no evidence')
        _require(notes.get('evidence_set_sha256') == object_hash(evidence),
                 'failure notes evidence mismatch')
        for record in evidence:
            _verify_hash(record['path'], record['sha256'])
        return common

    _require(failure is None, 'promoted workbook retains a failure')
    _require(isinstance(tracker.get('current_confidence'), dict)
             and tracker['current_confidence'].get('level') == 'high',
             'promotion confidence is incomplete')
    lanes = tracker.get('lane_state', {})
    _require(set(lanes) == REQUIRED_LANES and all(v.get('state') == 'passed'
        and v.get('current_confidence') == 'high' for v in lanes.values()),
        'promotion lanes are incomplete')
    evidence_path = root / f'{wb}-publication-evidence.json'
    records = json.loads(evidence_path.read_text())
    _require(isinstance(records, dict) and bool(records), 'publication evidence is empty')
    current_verifier_paths = set(segmentation_publication.verifier_paths())
    changed_verifier_paths = []
    import xl_variable_source_audit as auditor
    auditor_path = Path(auditor.__file__).resolve()
    changed_auditor = False
    for path, expected in records.items():
        try:
            _verify_hash(path, expected)
        except ValueError:
            # Publication may bind the checkout's verifier as well as the
            # immutable release. A later code edit requires reconstruction;
            # it does not imply that the retained task artifact was corrupted.
            # Only exact registered verifier paths get this classification.
            if Path(path).is_symlink():
                raise
            if Path(path) == auditor_path:
                changed_auditor = True
            elif Path(path) in current_verifier_paths:
                changed_verifier_paths.append(path)
            else:
                raise
    handoff_paths = []
    for path in records:
        if Path(path).name in {'handoff.json', 'handoff-final.json'}:
            candidate = json.loads(Path(path).read_text())
            # Complete audit evidence may itself bind preparation handoffs.
            # Their filename does not make them qualification certificates.
            if isinstance(candidate, dict) and candidate.get('verdict') == 'PUBLICATION_READY':
                handoff_paths.append(Path(path))
    _require(len(handoff_paths) == 1, 'publication must bind one qualification handoff')
    handoff = json.loads(handoff_paths[0].read_text())
    _require(handoff.get('workbook_id') == wb and handoff.get('verdict') == 'PUBLICATION_READY'
        and handoff.get('promotion_failure') is None
        and handoff.get('docker_smoke') is True and handoff.get('grader_smoke_score') == 1.0
        and handoff.get('curation_preserved') is True and handoff.get('solve_rollouts') == 0,
        'qualification acceptance gates are incomplete')
    pubroot = root / 'release_out' / wb
    source_root = Path(tracker['bindings']['source_publication_root']['path'])
    segroot = root / 'published-segments' / wb
    release_dir, manifest = release.resolve_current_release_integrity(pubroot,
        source_root=source_root, segmentation_root=segroot, task_root=pubroot,
        expected_release_id=tracker['promotion_ids']['release_id'])
    _require(manifest['release_id'] == tracker['promotion_ids']['release_id'],
             'current release differs from recorded promotion')
    for component, key in [('source_generation', 'source'), ('segmentation_generation', 'segmentation'),
                           ('task_generation', 'task')]:
        _require(manifest[component]['generation_id'] == tracker['generation_ids'][key],
                 f'{component} differs from qualification')
    output = root / 'tasks_outputs' / f'{wb}-outputs'
    release.validate_task_generation(output,
        expected_generation_id=tracker['promotion_ids']['task_generation_id'])
    output_hash, artifacts = release._tree_hash(output)
    artifacts.pop('generation-manifest.json')
    _require(release.canonical_hash(artifacts) == handoff['staged_tree_sha256'],
             'published task differs from qualified content')
    try:
        if changed_verifier_paths:
            raise RevalidationRequired('publication verifier code changed; restart reconstruction: '
                                       + ', '.join(changed_verifier_paths))
        if changed_auditor:
            raise RevalidationRequired('publication audit generator changed; restart audit evidence validation')
        _validate_current_proof(tracker, source_root)
        _validate_audit_proof(tracker)
    except RevalidationRequired as exc:
        return {**common, 'state': 'STALE', 'prior_promotion': True,
                'verified_promoted': False, 'revalidation_required': True,
                'error': str(exc), 'release_id': manifest['release_id'],
                'release_path': str(release_dir), 'output_path': str(output),
                'output_sha256': output_hash, 'evidence_sha256': digest(evidence_path)}
    source_dir = source_root / 'generations' / tracker['generation_ids']['source']
    source_manifest = json.loads((source_dir / 'generation-manifest.json').read_text())
    _require(tracker.get('raw_source', {}).get('sha256') in {source['sha256'],
             source_manifest['bindings']['source_sha256']}, 'tracker source identity mismatch')
    ledger_path = Path(run) / 'workbooks' / wb / 'ledger.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    normalizations = [attempt['normalization']
        for attempt in ledger.get('build_history', []) + [ledger.get('build', {})]
        if isinstance(attempt.get('normalization'), dict)]
    lineage = validate_generation_lineage(source['path'], source_dir, normalizations,
        trusted_public_key=trusted_public_key)
    # Historical integrity is sufficient only for the STALE branch. A current
    # promotion must still satisfy the full live release validator.
    release.validate_release(release_dir, source_root=source_root,
        segmentation_root=segroot, task_root=pubroot,
        expected_release_id=tracker['promotion_ids']['release_id'])
    return {**common, 'prior_promotion': True, 'revalidation_required': False,
            'source_lineage': lineage,
            'verified_promoted': True, 'release_id': manifest['release_id'],
            'release_path': str(release_dir), 'output_path': str(output),
            'output_sha256': output_hash, 'evidence_sha256': digest(evidence_path)}


def build_report(manifest, run, *, bind_ledgers=False, trusted_public_key=DEFAULT_TRUSTED_PUBLIC_KEY, harbor_roots=(),
                 historical_retirements=None, private_verifications=None):
    run = Path(run)
    _unique_sources(manifest)
    roots = selected_harbor_roots(run, harbor_roots)
    selections = {}
    if historical_retirements is not None:
        _require(not bind_ledgers, 'historical retirement reporting is read-only; ledger binding is unsupported')
        from scripts.harbor_retirement_reporting import load as load_retirements
        selections = load_retirements(historical_retirements, manifest, roots)
    private_selections = {} if private_verifications is None else _load_private_verifications(private_verifications, manifest, roots)
    _require(not set(selections) & set(private_selections), 'historical and private workbook selections overlap')
    rows = []
    for source in manifest['sources']:
        try:
            private = private_selections.get(source['workbook_id'])
            if private is not None:
                _private_document(private_verifications)
                _verify_hash(private['tracker']['path'], private['tracker']['sha256'])
            result = validate_terminal(source, run, trusted_public_key=trusted_public_key, harbor_roots=roots[1:],
                historical_retirement=selections.get(source['workbook_id']),
                **({'private_verification': private['selection']} if private is not None else {}))
            if private is not None:
                _private_document(private_verifications)
        except HarborTrackerConflict as exc:
            result = {'state': 'EVIDENCE_INVALID', 'error': str(exc),
                      'failure_class': 'ambiguous_original_tracker', 'candidates': exc.candidates}
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            result = {'state': 'EVIDENCE_INVALID', 'error': f'{type(exc).__name__}: {exc}'}
        row = {'workbook_id': source['workbook_id'], 'original_sha256': source['sha256'], **result}
        rows.append(row)
        if bind_ledgers and (result['state'] in {'PROMOTED', 'STOPPED', 'STALE', 'RUNNING'} or
                             result.get('failure_class') == 'ambiguous_original_tracker'):
            directory = run / 'workbooks' / source['workbook_id']
            try:
                with (directory / '.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    path = directory / 'ledger.json'
                    ledger = json.loads(path.read_text())
                    _require(ledger['source']['sha256'] == source['sha256'], 'ledger original changed')
                    old = ledger.get('harbor')
                    if old != result:
                        if old:
                            ledger.setdefault('harbor_history', []).append(old)
                        ledger['harbor'] = result
                        ledger['promoted'] = result.get('verified_promoted') is True
                        atomic_write_json(path, ledger)
            except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
                row.update(state='EVIDENCE_INVALID', error=f'ledger binding failed: {exc}')
                row.pop('verified_promoted', None)
    promoted = sum(row.get('verified_promoted') is True for row in rows)
    report = {'schema_version': 'corpus-qualified-yield/v1',
            'inventory_sha256': manifest['inventory_sha256'], 'raw_denominator': len(rows),
            'prior_promotions': sum(row.get('prior_promotion') is True for row in rows),
            'revalidation_required': sum(row.get('revalidation_required') is True for row in rows),
            'verified_promoted': promoted, 'harbor_yield': promoted / len(rows) if rows else None,
            'measurement_state': 'complete' if all(r['state'] in {'PROMOTED', 'STOPPED'} for r in rows)
                                 else 'in_progress',
            'states': dict(Counter(row['state'] for row in rows)), 'workbooks': rows}
    if historical_retirements is not None:
        report['historical_retirements_selection'] = historical_retirements
        report['historical_terminals'] = sum(row.get('historical_terminal') is True for row in rows)
    if private_verifications is not None:
        _private_document(private_verifications)
        report['private_verifications_selection'] = dict(private_verifications)
        report['private_verified_promotions'] = sum(row.get('verified_promoted') is True
            and row['workbook_id'] in private_selections for row in rows)
    if harbor_roots:
        report['harbor_roots'] = [str(root) for root in roots]
        report['run_root'] = str(run.resolve())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--run-root', required=True)
    parser.add_argument('--bind-ledgers', action='store_true')
    parser.add_argument('--harbor-root', action='append', default=[],
                        help='Additional explicit Harbor namespace beneath RUN/harbor; repeatable. Default root remains selected.')
    parser.add_argument('--historical-retirements', help='Explicit root-selected historical retirement manifest; no discovery.')
    parser.add_argument('--historical-retirements-sha256', help='Exact SHA256 of the root-selected manifest; required with its path.')
    parser.add_argument('--private-verifications', help='Explicit private verification selection manifest; no sidecar discovery.')
    parser.add_argument('--private-verifications-sha256', help='Exact SHA256 of the selected private verification manifest.')
    parser.add_argument('--trusted-public-key', default=DEFAULT_TRUSTED_PUBLIC_KEY)
    args = parser.parse_args()
    run = Path(args.run_root).resolve()
    if bool(args.historical_retirements) != bool(args.historical_retirements_sha256):
        parser.error('historical retirement path and SHA256 must be supplied together')
    if bool(args.private_verifications) != bool(args.private_verifications_sha256):
        parser.error('private verification path and SHA256 must be supplied together')
    historical = None if args.historical_retirements is None else dict(path=str(Path(args.historical_retirements).resolve()),sha256=args.historical_retirements_sha256)
    private = None if args.private_verifications is None else dict(path=str(Path(args.private_verifications).absolute()),sha256=args.private_verifications_sha256)
    report = build_report(read_inventory(args.inventory), run, bind_ledgers=args.bind_ledgers,
        trusted_public_key=args.trusted_public_key, harbor_roots=args.harbor_root,
        historical_retirements=historical, private_verifications=private)
    atomic_write_json(run / 'qualified-yield-report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'workbooks'}, indent=2))
    return int(bool(report['states'].get('EVIDENCE_INVALID')))


if __name__ == '__main__':
    raise SystemExit(main())
