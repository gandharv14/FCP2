"""Read-only qualification of a completed strict publication transaction.

The journal supplies a closed, typed selection, not a PASS flag. All owning
validators and the current immutable release resolver run again. The default
native path performs no container operation. An explicitly selected private
package can instead replay owning verification in its isolated namespace; there
is no automatic archive fallback, model call, tracker mutation or publication.
"""
import copy
from dataclasses import dataclass
from pathlib import Path

from scripts.batch import harbor_tracker as schema
from scripts.batch import harbor_tracker_lifecycle as lifecycle
from scripts.batch import harbor_lane_notes as notes
from scripts.batch import harbor_ready_transitions as transitions
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_transition_codec import decode


def after_releases(promoted, current):
    """Only exact, hash-chained lease releases may follow the promotion."""
    base = copy.deepcopy(promoted)
    prefix = len(base['handoffs'])
    require(current['handoffs'][:prefix] == base['handoffs'], 'promotion history changed')
    for event in current['handoffs'][prefix:]:
        require(event.get('schema_version') == lifecycle.EVENT_SCHEMA and
                event.get('operation') == 'release', 'non-release mutation after promotion')
        require(event['prior_tracker_sha256'] == schema.sha(lifecycle.canonical(base)),
                'post-promotion tracker chain changed')
        kind = event['lease_kind']
        require(kind in schema.LEASE_KINDS and base['leases'][kind] is not None and
                event['before'] == base['leases'][kind] and event['after'] is None,
                'post-promotion lease release differs')
        require(event['revision'] == base['revision'] + 1 and
                event['at'] >= base['updated_at'], 'post-promotion revision/time changed')
        base['leases'][kind] = None
        base['revision'] = event['revision']
        base['updated_at'] = event['at']
        base['handoffs'].append(event)
    require(base == current, 'current tracker differs from completed promotion and releases')



def historical_release(before, promoted, selection, published):
    """Establish immutable prior publication bytes, never current qualification."""
    import xl_release_publication as release
    require(isinstance(published, transitions.ReleaseSelection), 'typed release selection required')
    require(selection.content.workbook == before['workbook_id'], 'foreign historical selection')
    for key in ('original_raw_source', 'source_generation', 'source_file',
                'segmentation_generation', 'curation'):
        require(selection.content.bindings[key] == before['bindings'][key] == promoted['bindings'][key],
                'historical selected identity differs: ' + key)
    lifecycle.checked_record(published.pointer)
    lifecycle.checked_record(published.manifest)
    require(Path(published.pointer['path']) == published.publication_root / 'current-release.json',
            'foreign historical pointer')
    require(promoted['promotion_ids']['release_id'] == published.release_id,
            'historical release differs from recorded promotion')
    directory, manifest = release.resolve_current_release_integrity(
        published.publication_root, source_root=published.source_root,
        segmentation_root=published.segmentation_root, task_root=published.task_root,
        expected_release_id=published.release_id)
    require(record(directory / 'release-manifest.json') == published.manifest,
            'historical manifest binding differs')
    identity = manifest['identity']
    require(identity['workbook_id'] == before['workbook_id'], 'foreign historical release')
    require(identity.get('prior_release_id') == before['promotion_ids']['release_id']
            == promoted['promotion_ids']['previous_release_id'], 'historical release predecessor differs')
    components = {}
    for kind, root in (('source', published.source_root), ('segmentation', published.segmentation_root)):
        component = identity[kind + '_generation']
        expected = before['bindings'][kind + '_generation']
        path = root / 'generations' / component['generation_id']
        require(expected['id'] == component['generation_id'] == promoted['generation_ids'][kind]
                and Path(expected['path']) == path
                and record(path / 'generation-manifest.json')['sha256'] == expected['manifest_sha256'],
                'historical component differs from selected tuple: ' + kind)
        components[kind] = read(record(path / 'generation-manifest.json'))
    task_id = identity['task_generation']['generation_id']
    require(task_id == promoted['promotion_ids']['task_generation_id'] == promoted['generation_ids']['task']
            and promoted['generation_ids']['release'] == published.release_id, 'historical task/release identity differs')
    task = published.task_root / 'task-generations' / task_id
    stage_hash, stage_members = release._tree_hash(selection.content.stage)
    require(stage_hash == selection.content.stage_sha256, 'historical staged bytes changed')
    task_members = release._tree_hash(task)[1]
    stage_members.pop('generation-manifest.json', None)
    task_members.pop('generation-manifest.json', None)
    require(stage_members == task_members, 'historical published task differs from selected stage')
    source = components['source']
    source_path = Path(before['bindings']['source_generation']['path']) / source['layout']['source_workbook']
    require(record(source_path) == before['bindings']['source_file'], 'historical source file binding differs')
    return {'release_path': str(directory), 'source': source, 'segmentation': components['segmentation']}


def registered_code():
    """Only exact paths registered by current owning proof implementations."""
    import xl_source_publication as source
    import xl_variable_source_audit as auditor
    from xl_seg import publication as segmentation
    from scripts.batch import harbor_runtime_readiness as runtime
    paths = {runtime.ROOT / name: 'runtime' for name in runtime.code_bindings()}
    paths.update({path: 'segmentation' for path in segmentation.verifier_paths()})
    paths.update({path: 'source_AST' for path in source._default_builder_paths()})
    paths[Path(auditor.__file__).resolve()] = 'research_audit'
    return paths


def historical_evidence(event, historical, selection):
    """Validate all retained evidence before identifying explicit code drift.

    Missing or changed non-code files remain corruption, including same-named
    code copies in immutable archives. Never classify arbitrary gate failures.
    """
    import xl_source_publication as source
    from xl_seg import publication as segmentation, diagnostics
    from scripts.batch import harbor_runtime_readiness as runtime
    registered = registered_code()
    changes = []
    evidence = notes.evidence_list(event['evidence_paths'])
    require(evidence == event['evidence_paths'] and evidence, 'historical evidence inventory is not canonical')
    for binding in evidence:
        path = schema.safe_path(binding['path'])
        if path.is_dir():
            import xl_release_publication as release
            require(release._tree_hash(path)[0] == binding['sha256'], 'historical evidence tree changed')
        else:
            actual = record(path)
            if actual['sha256'] != binding['sha256']:
                require(path in registered, 'historical non-code evidence changed: ' + str(path))
                changes.append({'kind':'registered_code', 'owning_gate':registered[path], 'path':str(path),
                                'prior_sha256':binding['sha256'], 'current_sha256':actual['sha256']})
    builder = historical['source']['ast_provenance']['builder']['code']
    require(isinstance(builder,list) and builder, 'historical AST builder evidence missing')
    actual_builder = sorted((path.name,record(path)['sha256'],path.stat().st_size)
                            for path in source._default_builder_paths())
    recorded_builder = sorted((item['name'],item['sha256'],item['size_bytes']) for item in builder)
    if recorded_builder != actual_builder:
        changes.append({'kind':'AST_builder', 'recorded':recorded_builder, 'current':actual_builder})
    paths = segmentation.verifier_paths()
    records = [diagnostics.fingerprint_file(path,logical_path=path.name) for path in paths]
    require(all(item.get('available') is True for item in records), 'current verifier file is unavailable')
    live = diagnostics.fingerprint_values([
        {key:item[key] for key in ('path','sha256','size_bytes')} for item in records])
    previous = historical['segmentation']['evidence']['verifier_code']
    if live != previous:
        changes.append({'kind':'segmentation_verifier', 'recorded':previous, 'current':live})
    # This explicit typed snapshot belongs to the original root-observed runtime
    # transaction. Its files/logs are covered by the evidence inventory above.
    if selection.runtime is not None and selection.runtime.code != runtime.code_bindings():
        changes.append({'kind':'runtime_code', 'recorded':selection.runtime.code,
                        'current':runtime.code_bindings()})
    return changes

@dataclass(frozen=True)
class PrivateVerificationSelection:
    """Root-selected private evidence; never inferred from a tracker or journal."""
    bundle: Path
    manifest_sha256: str
    verifier_image: str


def validate(source, tracker_path, *, trusted_public_key, runtime_producer=None,
             private_verification=None):
    if private_verification is not None:
        require(type(private_verification) is PrivateVerificationSelection,
                'closed private verification selection required')
        require(runtime_producer is None,
                'private verification cannot combine a native runtime producer')
        from scripts.harbor_verification_package import qualify_retained_release
        return qualify_retained_release(
            private_verification.bundle,
            private_verification.manifest_sha256,
            private_verification.verifier_image,
            source=source,
            tracker_path=tracker_path,
            trusted_public_key=trusted_public_key,
        )
    tracker_path = schema.safe_path(tracker_path)
    current_raw = tracker_path.read_bytes()
    current = schema.validate_tracker(schema.strict_json(current_raw))
    lifecycle.validate_lifecycle(current)
    wb = source['workbook_id']
    require(current['workbook_id'] == wb and current['execution_state'] == 'PROMOTED',
            'not this original workbook promotion')
    original = current['bindings']['original_raw_source']
    require(record(source['path']) == original and original['sha256'] == source['sha256'],
            'original corpus identity changed')
    notes.validate_notes_pair(current, Path(current['agent_notes']['path']).read_bytes())
    events = [e for e in current['handoffs'] if e.get('schema_version') == transitions.SCHEMA
              and e.get('operation') == 'promoted']
    require(len(events) == 1, 'exactly one strict promotion transaction required')
    event = events[0]
    candidates = []
    journal_root = tracker_path.parent / (wb + '-notes-transactions')
    for path in journal_root.glob('*/transaction.json'):
        item = read(record(path))
        if item.get('schema_version') == transitions.SCHEMA and item.get('action') == 'promoted':
            candidates.append((path, item))
    require(len(candidates) == 1, 'missing or ambiguous completed promotion journal')
    path, descriptor = candidates[0]
    directory = path.parent
    require(descriptor['workbook'] == wb and descriptor['lease_kind'] == 'publication',
            'foreign promotion journal')
    for key, filename in (('before','before.json'), ('after','after.json'),
                          ('notes','notes.md'), ('prior_notes','prior-notes.md')):
        binding = descriptor[key]
        require(binding is not None and Path(binding['path']) == directory / filename,
                'promotion journal artifact escaped')
        lifecycle.checked_record(binding)
    completed = read(record(directory / 'complete.json'))
    require(completed == {'tracker_sha256': descriptor['after']['sha256'],
                          'notes_sha256': descriptor['notes']['sha256']},
            'promotion bookkeeping is incomplete')
    before, promoted = read(descriptor['before']), read(descriptor['after'])
    schema.validate_tracker(before); lifecycle.validate_lifecycle(before)
    schema.validate_tracker(promoted); lifecycle.validate_lifecycle(promoted)
    require(promoted['revision'] == event['revision'] and promoted['handoffs'][-1] == event,
            'promotion event does not match completed transaction')
    after_releases(promoted, current)
    lease = before['leases']['publication']
    require(lease is not None and descriptor['lease'] == lease and
            lease['scope'] == 'publication:' + wb and
            lease['heartbeat_at'] <= descriptor['prepared_at'] < lease['expires_at'],
            'promotion was not prepared under its bound publication lease')
    selection, published = decode(descriptor['selection']), decode(descriptor['published'])
    require(selection.content.trusted_public_key == trusted_public_key,
            'promotion selected a different trusted public key')
    seals = {
        'prior_tracker_sha256': schema.sha(lifecycle.canonical(before)),
        'selection_sha256': schema.sha(lifecycle.canonical(descriptor['selection'])),
        'published_selection_sha256': schema.sha(lifecycle.canonical(descriptor['published'])),
    }
    present = set(seals) & set(event)
    require(not present or present == set(seals), 'historical descriptor seals are incomplete')
    if present:
        require(all(event[key] == value for key,value in seals.items()), 'historical descriptor seal changed')
    require(descriptor['prepared_at'] == promoted['updated_at'] and descriptor['root_authorized'] is False,
            'historical transaction timing/authorization changed')
    historical = historical_release(before, promoted, selection, published)
    changes = historical_evidence(event, historical, selection)
    if changes:
        require(present == set(seals), 'unsealed historical descriptor requires full current proof; cannot classify as stale')
        require(tracker_path.read_bytes() == current_raw, 'tracker changed during qualification')
        return {'state':'STALE', 'prior_promotion':True, 'verified_promoted':False,
                'revalidation_required':True, 'tracker':str(tracker_path),
                'tracker_sha256':schema.sha(current_raw), 'failure':None,
                'generation_ids':current['generation_ids'], 'release_id':published.release_id,
                'release_path':historical['release_path'], 'output_path':str(selection.content.stage),
                'output_sha256':selection.content.stage_sha256, 'evidence_sha256':record(path)['sha256'],
                'error':'registered current code changed; owning gates require revalidation',
                'code_changes':changes}
    proof = transitions._proof(before, selection, **(
        {'runtime_producer': runtime_producer} if runtime_producer is not None else {}))
    rebuilt, rendered = transitions._after(before, 'promoted', selection, proof,
        descriptor['prepared_at'], Path(promoted['agent_notes']['path']), published,
        Path(descriptor['prior_notes']['path']).read_bytes())
    if not present:
        for key in seals:
            rebuilt['handoffs'][-1].pop(key, None)
        rendered = notes.render_notes(rebuilt)
        rebuilt['agent_notes']['sha256'] = schema.sha(rendered)
    require(rebuilt == promoted and rendered == Path(descriptor['notes']['path']).read_bytes(),
            'current owning proof differs from completed promotion')
    require(tracker_path.read_bytes() == current_raw, 'tracker changed during qualification')
    return {'state': 'PROMOTED', 'prior_promotion': True, 'verified_promoted': True,
            'revalidation_required': False, 'tracker': str(tracker_path),
            'tracker_sha256': schema.sha(current_raw), 'failure': None,
            'generation_ids': current['generation_ids'],
            'release_id': current['promotion_ids']['release_id'],
            'release_path': str(published.publication_root / 'releases' / published.release_id),
            'output_path': str(selection.content.stage),
            'output_sha256': selection.content.stage_sha256,
            'evidence_sha256': record(path)['sha256']}
