"""Create-once second workbook amendment; imports and resolution are read-only.

No work is staged until a hash-bound complete-roster census gate is supplied.
Remote operations are isolated in provision(); activation is a separate action.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from pathlib import Path
import recursion_hardening_revisions as r
import recursion_hardening_content as content

AMENDMENT_ID = '0002'
MASK_CHECKS = ('exactApprovedMasksApplied', 'unrelatedZipAndXmlBytesPreserved', 'targetMetadataPreserved',
               'noFormulasAdded', 'sharedStringIndicesPreserved', 'legitimateLiveSharedStringsPreserved',
               'removedOrphanPayloadsScrubbed', 'noUnapprovedCellsRestored', 'sourceAndAnswerKeyUnchanged')


def directory(work=r.WORK):
    return Path(work) / 'content-amendments' / AMENDMENT_ID


def _new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        import json
        stream.write(json.dumps(value, indent=2) + '\n')


def _pin(path, expected=None):
    return content.pin(path, expected)


def _parent(work):
    """Direct first-generation validation avoids recursive active-state lookup."""
    return content.activation(work)


def _assets(work, parent):
    rows, campaign = content._base(work)
    indexed = {x['taskId']: copy.deepcopy(x) for x in rows}
    for row in parent['revisions']:
        for key in ('promptPath', 'promptSha256', 'workbookPath', 'workbookSha256'):
            indexed[row['taskId']][key] = row[key]
    return rows, campaign, indexed


def _versions(work, parent):
    paths = {x['taskId']: Path(work) / 'problems' / x['taskId'] / 'verified.json'
             for x in r.read(Path(work) / 'final-assets.json')}
    paths.update({x['taskId']: Path(x['verifiedPath']) for x in parent['revisions']})
    return paths


def _runtime(path, patches, work, parent):
    roots, campaign, current = _assets(work, parent)
    data = r.read(path)
    r.require(data.get('schemaVersion') == 2 and data.get('kind') == 'content_amendment_runtime', 'Invalid amendment runtime schema')
    hashes = _pin(path)
    parent_path = Path(work) / 'content-successor/activation.json'
    prior_runtime = r.read(parent['runtimeManifestPath'])
    prior_proof_path = Path(prior_runtime['payloadProofPath'])
    fields = {'baseAssetsSha256': campaign['assetsSha256'],
              'frozenRuntimeSha256': campaign['runtimeManifestSha256'],
              'predecessorActivationPath': str(parent_path.resolve()),
              'predecessorActivationSha256': r.sha(parent_path),
              'predecessorPayloadProofPath': str(prior_proof_path.resolve()),
              'predecessorPayloadProofSha256': r.sha(prior_proof_path)}
    for key, value in fields.items():
        r.require(data.get(key) == value, 'Wrong amendment predecessor/root: ' + key)
    hashes.update(_pin(parent_path)); hashes.update(_pin(prior_proof_path, prior_runtime['payloadProofSha256']))
    hashes.update(_pin(data['payloadProofPath'], data['payloadProofSha256']))
    proof = r.read(data['payloadProofPath'])
    r.require(proof.get('schemaVersion') == 2 and proof.get('kind') == 'content_amendment_payload' and proof.get('status') == 'verified', 'Unverified amendment payload proof')
    for key, value in fields.items():
        r.require(proof.get(key) == value, 'Wrong payload predecessor/root: ' + key)
    expected = {x['taskId']: {'rootSha256': x['workbookSha256'],
                'predecessorSha256': current[x['taskId']]['workbookSha256'],
                'successorSha256': patches.get(x['taskId'], current[x['taskId']])['workbookSha256']} for x in roots}
    r.require(proof.get('workbooks') == expected, 'Incomplete or wrong whole-roster payload transition')
    import recursion_content_successor_image as image_tools
    image_folder = Path(data['payloadProofPath']).parent
    layer_path = image_folder / 'workbook-layer.json'
    r.require(proof.get('evidenceHashes', {}).get(str(layer_path.resolve())) == r.sha(layer_path), 'Missing payload layer binding')
    layer, _ = image_tools.verify_payload(image_folder)
    r.require(layer.get('schemaVersion') == 2 and layer['baseAssets']['sha256'] == campaign['assetsSha256']
              and layer['runtimeManifest']['sha256'] == campaign['runtimeManifestSha256'], 'Wrong verified layer roots')
    r.require(layer['predecessor']['activation'] == {'path': str(parent_path.resolve()), 'sha256': r.sha(parent_path)}
              and layer['predecessor']['payloadProof'] == {'path': str(prior_proof_path.resolve()), 'sha256': r.sha(prior_proof_path)}, 'Wrong layer predecessor')
    r.require({x['taskId']: x['sha256'] for x in layer['entries']} == {t: x['workbookSha256'] for t, x in patches.items()}
              and len(layer['entries']) == len(patches), 'Layer patch cohort/hash mismatch')
    prior_proof = r.read(prior_proof_path)
    digest = prior_proof['nonWorkbookPayloadSuccessorSha256']
    r.require(len(digest) == 64 and proof.get('nonWorkbookPayloadPredecessorSha256') == digest
              and proof.get('nonWorkbookPayloadSuccessorSha256') == digest, 'Non-workbook payload changed')
    r.require(proof.get('evidenceHashes'), 'Payload evidence hashes required')
    for source, digest in proof['evidenceHashes'].items(): hashes.update(_pin(source, digest))
    r.require(set(data['models']) == set(r.MODELS) and set(proof['probeResults']) == set(r.MODELS)
              and set(proof.get('registryResults', {})) == set(r.MODELS), 'Both model registry/probe proofs required')
    parent_probe_ids = set()
    for item in prior_proof['probeResults'].values():
        stages = item.get('stages', [])
        for stage in (stages.values() if isinstance(stages, dict) else stages):
            parent_probe_ids.add(stage['probeRunId'])
    r.require(len(parent_probe_ids) == 6, 'Six distinct predecessor probe identities required')
    configs = {}; actual_probe_ids = set()
    for model in r.MODELS:
        spec = data['models'][model]
        r.require(Path(spec['configPath']).resolve() == (image_folder / 'configs' / model / 'verified.json').resolve(), 'Config is outside amendment image evidence')
        old_path = Path(parent['configs'][model]['successorPath'])
        old = r.read(old_path); new = r.read(spec['configPath'])
        hashes.update(_pin(old_path)); hashes.update(_pin(spec['configPath'], spec['configSha256']))
        r.require(old.get('lockedAt') and old.get('verifiedAt') and new.get('lockedAt') and new.get('verifiedAt'), 'Unverified/unlocked config')
        r.require(old['id'] != new['id'] and old['runConfigId'] != new['runConfigId'], 'New independent config identity required')
        before = {k: v for k, v in old['config'].items() if k != 'harnessImageUrl'}
        after = {k: v for k, v in new['config'].items() if k != 'harnessImageUrl'}
        r.require(before == after, 'Amendment changed model/runtime behavior')
        image = new['config']['harnessImageUrl']
        r.require(image == spec['image'] and image != old['config']['harnessImageUrl'], 'Wrong amendment image')
        repository, separator, digest = image.partition('@sha256:')
        r.require(separator and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'Unpinned image')
        r.require(repository == old['config']['harnessImageUrl'].split('@sha256:')[0], 'Image repository changed')
        registered = proof['registryResults'][model]
        r.require(registered.get('baseImage') == old['config']['harnessImageUrl'] and registered.get('successorImage') == image, 'Registry image pair mismatch')
        r.require(all(registered.get(k) is True for k in ('allBaseLayersPreserved', 'onlyWorkbookLayerAppended', 'allRuntimeImageConfigPreserved', 'registryDigestVerified')), 'Registry preservation proof failed')
        count = registered.get('baseLayerCount')
        r.require(type(count) is int and count > 0 and registered.get('successorLayerCount') == count + 1, 'Unexpected image layer transition')
        published_path = image_folder / (model + '-image.json')
        r.require(spec['evidenceHashes'].get(str(published_path.resolve())) == r.sha(published_path), 'Published image receipt not pinned')
        published = r.read(published_path)
        r.require(published['image'] == image and published['parent'] == old['config']['harnessImageUrl'] == layer['baseImages'][model]['image']
                  and published['layerDigest'] == layer['digest'], 'Published image/layer mismatch')
        probe = proof['probeResults'][model]
        r.require(probe.get('status') == 'succeeded' and probe.get('runConfigVersionId') == new['id'], 'Wrong/failed amendment probes')
        r.require(set(probe.get('stages', {})) == {'research', 'plain', 'named'}, 'All three actual probe stages required')
        r.require(spec.get('evidenceHashes'), 'Config/probe evidence hashes required')
        for source, digest in spec['evidenceHashes'].items(): hashes.update(_pin(source, digest))
        import recursion_content_successor_verify as verifier
        stage_ids = set()
        for stage in ('research', 'plain', 'named'):
            folder = Path(spec['configPath']).parent
            status_path, request_path = folder / (stage + '-status.json'), folder / (stage + '-update.request.json')
            response_path = folder / (stage + '-probe.response.json')
            for artifact in (status_path, request_path, response_path):
                r.require(str(artifact.resolve()) in spec['evidenceHashes'], 'Missing actual probe evidence pin')
            status, request = r.read(status_path), r.read(request_path)
            r.require(status['id'] == r.read(response_path)['probeRunId'], 'Probe receipt mismatch')
            r.require(status['id'] not in actual_probe_ids and status['id'] not in parent_probe_ids, 'Repeated or predecessor probe ID')
            stage_ids.add(status['id']); actual_probe_ids.add(status['id'])
            reviewed = frozenset(); review_path = folder / (stage + '-shell-review.json')
            if review_path.exists():
                r.require(str(review_path.resolve()) in spec['evidenceHashes'], 'Unpinned shell review')
                reviewed = verifier.reviewed_probe_commands(r.read(review_path), status, request)
            actual = verifier.verify_probe(status, request, new, model, stage, reviewed)
            r.require(actual == probe['stages'][stage], 'Probe summary differs from actual evidence')
        configs[model] = {'baseId': old['id'], 'successorId': new['id'], 'basePath': str(old_path.resolve()),
                          'successorPath': str(Path(spec['configPath']).resolve()), 'image': image}
    return configs, hashes, fields


def _build_plan(workbook_manifest_path, runtime_manifest_path, census_gate_path, work):
    work = Path(work); folder = directory(work)
    parent = _parent(work)
    roots, campaign, current = _assets(work, parent)
    raw = r.read(workbook_manifest_path)
    if isinstance(raw, list): patches = raw
    else:
        r.require(len({'tasks', 'repairs'} & set(raw)) == 1, 'Ambiguous amendment manifest')
        patches = raw['tasks'] if 'tasks' in raw else raw['repairs']
    r.require(patches and len({x['taskId'] for x in patches}) == len(patches), 'Empty/duplicate amendment patch')
    patch_map = {x['taskId']: x for x in patches}
    r.require(set(patch_map) <= set(current), 'Amendment task outside roster')
    gate = r.read(census_gate_path)
    r.require(gate.get('status') == 'complete', 'Full census gate incomplete')
    r.require(len(gate.get('taskIds', [])) == len(roots) and set(gate['taskIds']) == set(current), 'Census must cover exact full roster')
    r.require(len(gate.get('affectedTaskIds', [])) == len(patches) and set(gate['affectedTaskIds']) == set(patch_map), 'Census/patch cohort mismatch')
    r.require(gate.get('evidenceHashes'), 'Census evidence hashes required')
    r.require(set(gate.get('approvedMasks', {})) == set(patch_map), 'Gate-approved mask cohort mismatch')
    hashes = {**_pin(census_gate_path), **_pin(workbook_manifest_path), **_pin(work / 'final-assets.json'),
              **_pin(work / 'content-successor/activation.json')}
    for path, digest in gate['evidenceHashes'].items(): hashes.update(_pin(path, digest))
    configs, runtime_hashes, fields = _runtime(runtime_manifest_path, patch_map, work, parent)
    proof = r.read(r.read(runtime_manifest_path)['payloadProofPath'])
    r.require(proof.get('censusGatePath') == str(Path(census_gate_path).resolve()) and proof.get('censusGateSha256') == r.sha(census_gate_path), 'Runtime/census gate mismatch')
    hashes.update(runtime_hashes)
    predecessors = _versions(work, parent); delta = []
    for task in sorted(patch_map):
        patch = patch_map[task]; base = current[task]; predecessor_path = predecessors[task]; predecessor = r.read(predecessor_path)
        r.require(predecessor.get('lockedAt'), 'Unlocked task predecessor')
        approved = gate['approvedMasks'][task]
        r.require(Path(approved['path']).resolve() == Path(patch['maskSpecificationPath']).resolve()
                  and approved['sha256'] == patch['maskSpecificationSha256'], 'Unapproved mask specification')
        hashes.update(_pin(patch['maskSpecificationPath'], patch['maskSpecificationSha256']))
        spec = r.read(patch['maskSpecificationPath'])
        r.require(spec['taskId'] == task and spec['activeProblemVersionId'] == predecessor['id']
                  and Path(spec['workbookPath']).resolve() == Path(base['workbookPath']).resolve()
                  and spec['workbookSha256'] == base['workbookSha256']
                  and Path(spec['answerKeyPath']).resolve() == Path(base['answerKeyPath']).resolve()
                  and spec['answerKeySha256'] == base['answerKeySha256'], 'Mask source/version/key binding mismatch')
        r.require(spec.get('sourceEvidenceHashes'), 'Missing semantic mask evidence')
        for source, digest in spec['sourceEvidenceHashes'].items(): hashes.update(_pin(source, digest))
        from recursion_amendment_mask_validation import verify_mask
        verified_mask = verify_mask(patch['maskSpecificationPath'], patch['workbookPath'],
            expected_spec_sha256=patch['maskSpecificationSha256'], expected_output_sha256=patch['workbookSha256'])
        r.require(all(verified_mask.get('validation', {}).get(k) is True for k in MASK_CHECKS), 'Exact mask verification failed')
        r.require(patch['baseWorkbookSha256'] == base['workbookSha256'], 'Patch not based on current workbook')
        r.require(patch['workbookSha256'] != base['workbookSha256'], 'Amendment workbook unchanged')
        r.require(Path(patch['workbookPath']).resolve() != Path(base['workbookPath']).resolve(), 'Do not overwrite predecessor workbook')
        hashes.update(_pin(predecessor_path)); hashes.update(_pin(base['workbookPath'], base['workbookSha256']))
        hashes.update(_pin(patch['workbookPath'], patch['workbookSha256']))
        hashes.update(_pin(patch['validationPath'], patch['validationSha256']))
        validation = r.read(patch['validationPath'])
        r.require(validation == verified_mask, 'Stored mask validation differs from fresh exact verification')
        old = next((x for x in parent['revisions'] if x['taskId'] == task), None)
        original_id = r.read(work / 'problems' / task / 'verified.json')['id']
        row = {'taskId': task, 'oldVersionIds': sorted({original_id, predecessor['id'], *(old.get('oldVersionIds', []) if old else [])}), 'baseProblemVersionId': predecessor['id'], 'baseVersionPath': str(predecessor_path.resolve()),
               'baseWorkbookPath': base['workbookPath'], 'baseWorkbookSha256': base['workbookSha256'],
               'workbookPath': patch['workbookPath'], 'workbookSha256': patch['workbookSha256'],
               'promptPath': base['promptPath'], 'promptSha256': base['promptSha256'],
               'answerKeyPath': base['answerKeyPath'], 'answerKeySha256': base['answerKeySha256'],
               'assetRepaired': True, 'promptRepaired': False, 'defaultSolverRunConfigVersionId': configs['opus']['successorId']}
        for key, digest in [('promptPath', 'promptSha256'), ('answerKeyPath', 'answerKeySha256')]: hashes.update(_pin(row[key], row[digest]))
        r.require(predecessor['prompt'] == Path(row['promptPath']).read_text(), 'Current prompt/version mismatch')
        delta.append(row)
    plan = {'schemaVersion': 2, 'kind': 'content_amendment', 'amendmentId': AMENDMENT_ID, 'status': 'prepared',
            **fields, 'runtimeManifestPath': str(Path(runtime_manifest_path).resolve()),
            'workbookManifestPath': str(Path(workbook_manifest_path).resolve()), 'censusGatePath': str(Path(census_gate_path).resolve()),
            'configs': configs, 'revisions': delta, 'artifactHashes': hashes, 'dispatchPrefix': 'correction-content-2'}
    return plan


def prepare(workbook_manifest_path, runtime_manifest_path, census_gate_path, work=r.WORK):
    r.require(not (directory(work) / 'activation.json').exists(), 'Amendment already activated')
    plan = _build_plan(workbook_manifest_path, runtime_manifest_path, census_gate_path, work)
    destination = directory(work) / 'plan.json'
    if destination.exists(): r.require(r.read(destination) == plan, 'Existing amendment plan differs')
    else: _new(destination, plan)
    return plan


def validate_plan(work=r.WORK):
    plan = r.read(directory(work) / 'plan.json')
    r.require(plan.get('kind') == 'content_amendment' and plan.get('schemaVersion') == 2
              and plan.get('amendmentId') == AMENDMENT_ID and plan.get('status') == 'prepared', 'Invalid amendment plan')
    for path, digest in plan['artifactHashes'].items(): _pin(path, digest)
    rebuilt = _build_plan(plan['workbookManifestPath'], plan['runtimeManifestPath'], plan['censusGatePath'], work)
    r.require(rebuilt == plan, 'Amendment plan semantics changed')
    return plan


def _same(base, revised, row):
    expected = copy.deepcopy(base); expected['solverRunConfigVersionId'] = row['defaultSolverRunConfigVersionId']
    r.same_behavior(expected, revised)
    r.require(revised['id'] not in row['oldVersionIds'] and revised['prompt'] == base['prompt'] == Path(row['promptPath']).read_text(), 'Amendment changed prompt/reused historical version')


def provision(work=r.WORK):
    plan = validate_plan(work)
    r.require(not (directory(work) / 'activation.json').exists(), 'Amendment already active')
    for row in plan['revisions']:
        base = r.read(row['baseVersionPath'])
        r.require(r.c.cli('problem-versions', 'get', '--problem-version-id', base['id']) == base, 'Remote predecessor differs')
        folder = directory(work) / 'versions' / row['taskId']
        created = r.c.mutation(folder, 'create', ['problem-versions', 'create', '--problem-id', base['problemId'], '--copy-from-version-id', base['id']])
        vid = created['id']; payload = {'solverRunConfigVersionId': row['defaultSolverRunConfigVersionId']}
        r.c.mutation(folder, 'update', ['problem-versions', 'update', '--problem-version-id', vid], payload)
        r.c.mutation(folder, 'lock', ['problem-versions', 'lock', '--problem-version-id', vid])
        verified = r.c.cli('problem-versions', 'get', '--problem-version-id', vid); _same(base, verified, row)
        path = folder / 'verified.json'
        if path.exists(): r.require(r.read(path) == verified, 'Verified amendment receipt differs')
        else: _new(path, verified)
    return {'status': 'provisioned_not_activated', 'tasks': [x['taskId'] for x in plan['revisions']]}


def _check_partition_counterparts(work, parent):
    mapping = {}
    for model in r.MODELS:
        mapping[r.read(Path(work) / 'final-images/configs' / model / 'verified.json')['id']] = model
        mapping[parent['configs'][model]['successorId']] = model
    for intent in Path(work).glob('evaluation-*.intent.json'):
        r.require(intent.with_name(intent.name.replace('.intent.json', '.response.json')).exists(), 'Reconcile original uncertain dispatch before activation')
    expected = {frozenset(x['problemId'] for x in r.read(Path(work) / 'final-assets.json')[part::3]) for part in range(3)}
    cohorts = {}; pins = {}
    for response in Path(work).glob('evaluation-*.response.json'):
        request_path = response.with_name(response.name.replace('.response.json', '.request.json'))
        request = r.read(request_path); pins.update(_pin(response)); pins.update(_pin(request_path))
        tasks = frozenset(x['problemId'] for x in request['problems'])
        r.require(tasks in expected and len(tasks) == len(request['problems']), 'Unexpected original partition cohort')
        versions = {x['problemId']: x['problemVersionId'] for x in request['problems']}
        models = [mapping[x['runConfigVersionId']] for x in request['solvers']]
        r.require(len(models) == len(set(models)), 'Duplicate original partition model')
        previous = cohorts.setdefault(tasks, {'versions': versions, 'models': set()})
        r.require(previous['versions'] == versions and not previous['models'] & set(models), 'Original partition counterpart mismatch')
        previous['models'].update(models)
    r.require(set(cohorts) == expected and all(x['models'] == set(r.MODELS) for x in cohorts.values()), 'Original partition counterpart not dispatched; preserve its frozen versions first')
    return pins


def activate(work=r.WORK, *, confirmed=False):
    r.require(confirmed, 'Amendment activation requires explicit confirmation')
    plan = validate_plan(work); destination = directory(work) / 'activation.json'
    dispatch_pins = _check_partition_counterparts(work, _parent(work))
    r.require(not destination.exists(), 'Immutable amendment already active')
    data = copy.deepcopy(plan); data['artifactHashes'].update(dispatch_pins); new_ids = set()
    for row in data['revisions']:
        folder = directory(work) / 'versions' / row['taskId']; path = folder / 'verified.json'; revised = r.read(path)
        _same(r.read(row['baseVersionPath']), revised, row)
        r.require(revised['id'] not in new_ids, 'Duplicate amendment version ID'); new_ids.add(revised['id'])
        row.update(verifiedPath=str(path.resolve()), newProblemVersionId=revised['id'])
        for artifact in folder.glob('*.json'): data['artifactHashes'].update(_pin(artifact))
    data.update(status='ready', activatedAt=datetime.now(timezone.utc).isoformat(),
                planSha256=r.sha(directory(work) / 'plan.json'), replacementPolicy='all_three_attempts_both_models')
    _new(destination, data)
    return data


def activation(work=r.WORK):
    data = r.read(directory(work) / 'activation.json'); plan = validate_plan(work)
    r.require(data.get('status') == 'ready' and data.get('planSha256') == r.sha(directory(work) / 'plan.json'), 'Invalid amendment activation')
    for key, value in plan.items():
        if key not in {'status', 'artifactHashes', 'revisions'}: r.require(data.get(key) == value, 'Activated amendment plan drift')
    r.require(all(data['artifactHashes'].get(p) == h for p, h in plan['artifactHashes'].items()), 'Activation omitted plan pins')
    projected = [{k: v for k, v in row.items() if k not in {'verifiedPath', 'newProblemVersionId'}} for row in data['revisions']]
    r.require(projected == plan['revisions'], 'Amendment delta changed after planning')
    for path, digest in data['artifactHashes'].items(): _pin(path, digest)
    for row in data['revisions']:
        revised = r.read(row['verifiedPath']); _same(r.read(row['baseVersionPath']), revised, row)
        r.require(revised['id'] == row['newProblemVersionId'], 'Amendment version binding drift')
    return data


def resolved_activation(work=r.WORK):
    """Accumulated view for consumers; immutable delta stays separately exposed."""
    parent = _parent(work); amendment = activation(work); result = copy.deepcopy(parent)
    indexed = {x['taskId']: x for x in result['revisions']}
    for delta in amendment['revisions']:
        task = delta['taskId']; old = indexed.get(task)
        row = copy.deepcopy(delta)
        original = r.read(Path(work) / 'problems' / task / 'verified.json')['id']
        row['oldVersionIds'] = sorted({original, delta['baseProblemVersionId'], *(old.get('oldVersionIds', []) if old else [])})
        row['originalProblemVersionId'] = original
        # promptRepaired is accumulated provenance, not an added amendment edit.
        row['promptRepaired'] = bool(old and old.get('promptRepaired'))
        indexed[task] = row
    result.update(revisions=[indexed[k] for k in sorted(indexed)], configs=amendment['configs'],
                  amendmentDelta=amendment['revisions'], amendmentId=AMENDMENT_ID,
                  dispatchPrefix=amendment['dispatchPrefix'], amendmentActivationPath=str((directory(work) / 'activation.json').resolve()))
    return result


def run_binding_context(work=r.WORK):
    parent = _parent(work); amendment = activation(work)
    roots, _, _ = _assets(work, parent)
    original_payload = {x['taskId']: x['workbookSha256'] for x in roots}
    runtime = r.read(parent['runtimeManifestPath']); first_proof = r.read(runtime['payloadProofPath'])
    next_runtime = r.read(amendment['runtimeManifestPath']); next_proof = r.read(next_runtime['payloadProofPath'])
    payloads = [original_payload, {t: x['successorSha256'] for t, x in first_proof['workbooks'].items()},
                {t: x['successorSha256'] for t, x in next_proof['workbooks'].items()}]
    mapping = {}; configs = {}
    for model in r.MODELS:
        paths = [Path(work) / 'final-images/configs' / model / 'verified.json',
                 Path(parent['configs'][model]['successorPath']), Path(amendment['configs'][model]['successorPath'])]
        for path, payload in zip(paths, payloads):
            cfg = r.read(path); r.require(cfg['id'] not in mapping, 'Duplicate generation config identity')
            mapping[cfg['id']] = model; configs[cfg['id']] = payload
    expected = {x['taskId']: {r.read(Path(work) / 'problems' / x['taskId'] / 'verified.json')['id']: x['workbookSha256']} for x in roots}
    for row in parent['revisions']:
        for vid in row.get('oldVersionIds', []): expected[row['taskId']][vid] = original_payload[row['taskId']]
        expected[row['taskId']][row['newProblemVersionId']] = payloads[1][row['taskId']]
    for row in amendment['revisions']: expected[row['taskId']][row['newProblemVersionId']] = payloads[2][row['taskId']]
    bindings = {t: {v: {cfg for cfg, payload in configs.items() if payload[t] == digest} for v, digest in versions.items()} for t, versions in expected.items()}
    return {'bindings': bindings, 'knownConfigs': mapping}
