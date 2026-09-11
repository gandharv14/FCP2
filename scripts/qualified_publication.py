"""Publish a qualified task with an explicit prior-release CAS and safe resume.

The caller must validate qualification evidence and serialize its tracker and
publication queue. This helper never establishes task quality by itself.
"""
from pathlib import Path
import hashlib
import os
import tempfile
import json
import shutil

import xl_release_publication as release
import xl_source_publication as source_publication


def validate_audit_bindings(bindings, *, expected_generations=None):
    """Revalidate the complete audit before any qualified publication mutation.

    Return every checked file so the publication evidence includes the coverage
    artifact, even when the tracker schema only names its metadata parent.
    """
    from xl_variable_source_audit import validate_saved_audit_coverage
    if expected_generations is not None:
        for component in ('source', 'segmentation'):
            expected = expected_generations.get(component)
            if (not expected or bindings.get(component + '_generation', {}).get('id') != expected):
                raise ValueError(f'audit differs from publication {component} generation')
    keys = ('variable_source_metadata', 'variable_source_inventory', 'variable_source_audit')
    records = {}
    paths = []
    for key in keys:
        record = bindings.get(key)
        if not isinstance(record, dict) or not record.get('path') or not record.get('sha256'):
            raise ValueError(f'missing qualified audit binding: {key}')
        path = Path(record['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError(f'changed qualified audit evidence: {key}')
        records[str(path.resolve())] = record['sha256']
        paths.append(path)
    upgrade = bindings.get('audit_validator_upgrade')
    original_auditor = bindings.get('audit_upgrade_original_code')
    if (upgrade is None) != (original_auditor is None):
        raise ValueError('upgrade and original generator must be selected together')
    equivalence = bindings.get('audit_equivalence')
    if equivalence is not None:
        if not isinstance(equivalence, dict) or not equivalence.get('path') or not equivalence.get('sha256'):
            raise ValueError('missing qualified audit equivalence binding')
        receipt = Path(equivalence['path'])
        if (receipt.is_symlink() or not receipt.is_file()
                or hashlib.sha256(receipt.read_bytes()).hexdigest() != equivalence['sha256']):
            raise ValueError('changed qualified audit equivalence evidence')
        document = json.loads(receipt.read_text())
        if any(document.get('old_bindings', {}).get(key) != bindings[key] for key in keys):
            raise ValueError('equivalence does not cover the selected audit artifacts')
        from xl_audit_equivalence import validate_audit_equivalence
        records.update(validate_audit_equivalence(receipt, current_bindings=bindings,
            validator_upgrade=upgrade, original_auditor=original_auditor))
        return records
    if upgrade is not None:
        from xl_audit_validator_upgrade import validate_upgrade
        records.update(validate_upgrade(upgrade, bindings=bindings, original_code=original_auditor))
        records.update(_validate_current_audit_context(bindings, *paths[:2]))
        return records
    result = validate_saved_audit_coverage(*paths)
    records[str(Path(result['coverage_path']).resolve())] = result['coverage_sha256']
    records.update(_validate_current_audit_context(bindings, *paths[:2]))
    return records


def _validate_current_audit_context(bindings, metadata_path, inventory_path):
    """Saved response integrity does not establish applicability to a new pair."""
    from xl_variable_source_audit import AuditRevalidationRequired, build_inventory
    from xl_seg.publication import inputs_sidecar_path, validate_inputs_sidecar, resolve_generation_by_id

    metadata = json.loads(Path(metadata_path).read_text())
    saved = json.loads(Path(inventory_path).read_text())
    workbook = saved.get('metadata', {}).get('workbook')
    if (not isinstance(workbook, str) or not workbook or Path(workbook).name != workbook
            or workbook in ('.', '..') or metadata.get('workbook') != workbook):
        raise ValueError('qualified audit workbook identity mismatch')

    records = {}
    def checked(key, *, generation=False):
        record = bindings.get(key)
        hash_key = 'manifest_sha256' if generation else 'sha256'
        if (not isinstance(record, dict) or not record.get('path') or not record.get(hash_key)
                or (generation and not record.get('id'))):
            raise ValueError(f'missing qualified current audit binding: {key}')
        path = Path(record['path'])
        evidence = path / 'generation-manifest.json' if generation else path
        if (evidence.is_symlink() or not evidence.is_file()
                or hashlib.sha256(evidence.read_bytes()).hexdigest() != record[hash_key]):
            raise ValueError(f'changed qualified current audit evidence: {key}')
        if generation and (path.name != record['id'] or path.parent.name != 'generations'):
            raise ValueError(f'noncanonical qualified audit generation: {key}')
        records[str(evidence.resolve())] = record[hash_key]
        return path, record

    source_dir, source_record = checked('source_generation', generation=True)
    seg_dir, seg_record = checked('segmentation_generation', generation=True)
    source_file, source_file_record = checked('source_file')
    baseline, _ = checked('baseline_inputs')
    sidecar, _ = checked('baseline_segmentation')
    if sidecar.resolve() != inputs_sidecar_path(baseline).resolve():
        raise ValueError('qualified audit baseline sidecar path mismatch')
    # Use current production validators, never the historical audit metadata to
    # select or certify the source/segmentation generations.
    try:
        context = release.resolve_build_context(
            workbook, source_root=source_dir.parent.parent,
            source_generation_id=source_record['id'],
            segmentation_root=seg_dir.parent.parent,
            segmentation_generation_id=seg_record['id'])
        recorded_builder = context['source_manifest']['ast_provenance']['builder']['code']
        expected_builder = sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_size)
                                  for p in source_publication._default_builder_paths())
        if sorted((r['name'], r['sha256'], r['size_bytes']) for r in recorded_builder) != expected_builder:
            raise ValueError('current production AST builder changed')
        resolve_generation_by_id(seg_dir.parent.parent, seg_record['id'],
            source_path=context['source_path'], ast_dir=context['ast_dir'],
            source_generation_dir=source_dir, require_pass=True, validate_live_evidence=True)
    except ValueError as exc:
        raise AuditRevalidationRequired(f'qualified audit current source/segmentation proof requires refresh: {exc}') from exc
    current = context['bindings']
    if (Path(context['source_dir']).resolve() != source_dir.resolve()
            or Path(context['segmentation_dir']).resolve() != seg_dir.resolve()
            or current['source_manifest_sha256'] != source_record['manifest_sha256']
            or current['segmentation_manifest_sha256'] != seg_record['manifest_sha256']
            or Path(context['source_path']).resolve() != source_file.resolve()
            or context['source_sha256'] != source_file_record['sha256']):
        raise ValueError('qualified audit current source/segmentation binding mismatch')
    validate_inputs_sidecar(baseline, expected_generation_id=seg_record['id'],
                           generation_dir=seg_dir, expected_pipeline_bindings=current)
    if (not isinstance(metadata.get('artifact'), str)
            or Path(metadata['artifact']).resolve() != baseline.resolve()):
        raise AuditRevalidationRequired('qualified audit baseline artifact context requires refresh')
    segmentation = {
        'generation_id': current['segmentation_generation_id'],
        'inventory_approval_sha256': current['inventory_approval_sha256'],
        'manifest_sha256': current['segmentation_manifest_sha256'],
        'recalc_signals_sha256': current['recalc_signals_sha256'],
        'source_generation_id': current['source_generation_id'],
    }
    fresh = build_inventory(workbook, baseline, seg_dir, segmentation=segmentation)
    if fresh != saved:
        raise AuditRevalidationRequired('qualified audit inventory/current source/segmentation context requires refresh')
    # All bytes returned here become part of the publication evidence inventory.
    return records


def stable_source_root(publication_root, candidate_root, generation_id, *,
                       segmentation_root, expected_current_release_id):
    """Retain a prior release's path-bound root while adding a validated candidate."""
    root, candidate_root = Path(publication_root), Path(candidate_root)
    pointer = release._current_pointer(root, absent_ok=True)
    if pointer is None or expected_current_release_id is None:
        return candidate_root
    # Publication paths are identity-bound. Copying the old generation to a
    # different root cannot make its old release validate at that new root.
    prior = json.loads((root / pointer['release_path'] / 'release-manifest.json').read_text())
    path = Path(prior['source_generation']['generation_path'])
    stable = path.parent.parent
    if pointer['release_id'] == expected_current_release_id:
        release.resolve_current_release_integrity(root, source_root=stable,
            segmentation_root=segmentation_root, task_root=root,
            expected_release_id=expected_current_release_id)
    else:
        # A post-CAS candidate resume must still satisfy current policy.
        release.resolve_current_release(root, source_root=stable,
            segmentation_root=segmentation_root, task_root=root,
            expected_release_id=pointer['release_id'])
    if pointer['release_id'] != expected_current_release_id:
        # An interrupted post-CAS resume already uses the new release's root;
        # publish_or_resume independently checks its full tuple/predecessor.
        if prior.get('prior_release_id') != expected_current_release_id:
            raise release.ReleasePublicationError('qualified publication prior release changed')
    if stable.resolve() == candidate_root.resolve():
        return candidate_root
    original = candidate_root / 'generations' / generation_id
    expected = source_publication.validate_source_generation(original,
        expected_generation_id=generation_id)
    destination = stable / 'generations' / generation_id
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='.qualified-source-', dir=stable) as temporary:
            copied = Path(temporary) / generation_id
            shutil.copytree(original, copied)
            actual = source_publication.validate_source_generation(copied,
                expected_generation_id=generation_id)
            if actual != expected:
                raise ValueError('copied source generation changed')
            os.rename(copied, destination)
    if source_publication.validate_source_generation(destination,
            expected_generation_id=generation_id) != expected:
        raise ValueError('stable source generation differs from qualified candidate')
    return stable


def archive_publication_evidence(path):
    """Preserve the previous evidence inventory before a serialized replacement."""
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError('publication evidence must be a regular file')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    directory = path.parent / 'publication-evidence-history'
    directory.mkdir(exist_ok=True)
    if directory.is_symlink():
        raise ValueError('publication evidence history must not be a symlink')
    archived = directory / f'{path.stem}-{digest}.json'
    descriptor, temporary = tempfile.mkstemp(prefix='.evidence-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, archived)
        except FileExistsError:
            if archived.is_symlink() or archived.read_bytes() != data:
                raise ValueError('archived publication evidence changed')
    finally:
        os.unlink(temporary)
    return {'path': str(archived), 'sha256': digest}


def publish_or_resume(publication_root, workbook_id, *, source_root,
                      source_generation_id, segmentation_root,
                      segmentation_generation_id, task_generation_id,
                      expected_current_release_id, output_path, resuming=False):
    root = Path(publication_root)
    pointer = release._current_pointer(root, absent_ok=True)
    current = None
    if pointer is not None:
        if pointer['release_id'] == expected_current_release_id:
            current_dir, current = release.resolve_current_release_integrity(root,
                source_root=source_root, segmentation_root=segmentation_root,
                task_root=root, expected_release_id=expected_current_release_id)
        else:
            current_dir, current = release.resolve_current_release(root,
                source_root=source_root, segmentation_root=segmentation_root, task_root=root)
    observed = None if current is None else current['release_id']
    if observed != expected_current_release_id:
        # A crash can occur after pointer CAS but before the tracker commits.
        # Only that exact qualified tuple with the expected predecessor resumes.
        if (resuming and current is not None
                and current.get('prior_release_id') == expected_current_release_id
                and current['source_generation']['generation_id'] == source_generation_id
                and current['segmentation_generation']['generation_id'] == segmentation_generation_id
                and current['task_generation']['generation_id'] == task_generation_id):
            return current_dir, current
        raise release.ReleasePublicationError('qualified publication prior release changed')
    options = {}
    if current is None:
        present=[(Path(path)/'current.json').exists() for path in (source_root,segmentation_root)]
        if sum(present)==1:
            _,snapshot=release.freeze_partial_legacy_snapshot(root,workbook_id,
                source_root=source_root,source_generation_id=source_generation_id,
                segmentation_root=segmentation_root,segmentation_generation_id=segmentation_generation_id)
        else:
            _, snapshot = release.freeze_absent_legacy_snapshot(root, workbook_id,
                source_root=source_root, segmentation_root=segmentation_root)
        options['legacy_snapshot_hash'] = snapshot['snapshot_hash']
    return release.publish_release(root, workbook_id,
        source_root=source_root, source_generation_id=source_generation_id,
        segmentation_root=segmentation_root, segmentation_generation_id=segmentation_generation_id,
        task_generation_id=task_generation_id,
        expected_current_release_id=expected_current_release_id,
        compatibility_paths={'task': output_path}, **options)
