"""Explicit workbook/image successor planning, provisioning and activation.

Inputs use normalized manifests; adapters may translate producer manifests into
this schema without altering the frozen base assets. Importing is read-only.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from pathlib import Path
import recursion_hardening_revisions as r


def directory(work):
    return Path(work) / 'content-successor'


def pin(path, expected=None):
    digest = r.sha(path)
    if expected is not None:
        r.require(digest == expected, 'Content artifact hash mismatch: ' + str(path))
    return {str(Path(path).resolve()): digest}


def _base(work):
    work = Path(work)
    rows = r.read(work / 'final-assets.json')
    campaign = r.read(work / 'campaign.json')
    r.require(campaign['tasks'] == rows and campaign['assetsSha256'] == r.sha(work / 'final-assets.json'), 'Frozen campaign assets differ')
    return rows, campaign


def _runtime(runtime_path, patches, work):
    """Normalized runtime schema: config paths plus whole-roster payload proof."""
    rows, campaign = _base(work)
    data = r.read(runtime_path)
    r.require(data['baseAssetsSha256'] == campaign['assetsSha256'], 'Wrong runtime base assets')
    r.require(data['baseRuntimeManifestSha256'] == campaign['runtimeManifestSha256'], 'Wrong runtime predecessor')
    r.require(set(data['models']) == set(r.MODELS), 'Both successor models required')
    hashes = pin(runtime_path)
    proof_path = data['payloadProofPath']
    hashes.update(pin(proof_path, data['payloadProofSha256']))
    proof = r.read(proof_path)
    r.require(proof['baseAssetsSha256'] == campaign['assetsSha256'], 'Payload proof uses wrong base')
    r.require(proof['baseRuntimeManifestSha256'] == campaign['runtimeManifestSha256'], 'Payload proof uses wrong runtime')
    r.require(proof['nonWorkbookPayloadBaseSha256'] == proof['nonWorkbookPayloadSuccessorSha256'], 'Non-workbook task payload changed')
    r.require(len(proof['nonWorkbookPayloadBaseSha256']) == 64, 'Invalid payload digest')
    r.require(set(proof['workbooks']) == {x['taskId'] for x in rows}, 'Payload proof must cover entire roster')
    for row in rows:
        item = proof['workbooks'][row['taskId']]
        wanted = patches.get(row['taskId'], row)['workbookSha256']
        r.require(item == {'baseSha256': row['workbookSha256'], 'successorSha256': wanted}, 'Unexpected task workbook payload')
    configs = {}
    for model in r.MODELS:
        spec = data['models'][model]
        hashes.update(pin(spec['configPath'], spec['configSha256']))
        basepath = Path(work) / 'final-images/configs' / model / 'verified.json'
        hashes.update(pin(basepath))
        old, new = r.read(basepath), r.read(spec['configPath'])
        r.require(new.get('lockedAt') and new.get('verifiedAt'), 'Successor configuration not locked/verified')
        r.require(old.get('lockedAt') and old.get('verifiedAt'), 'Base configuration not locked/verified')
        r.require(new['id'] != old['id'], 'Successor configuration must be distinct')
        r.require(new['config']['harnessImageUrl'] == spec['image'], 'Config/image mismatch')
        r.require('@sha256:' in spec['image'] and len(spec['image'].rsplit('@sha256:', 1)[1]) == 64,
                  'Successor image must be pinned to a digest')
        r.require(new['config']['harnessImageUrl'] != old['config']['harnessImageUrl'], 'Successor image did not change')
        before = {k: v for k, v in old['config'].items() if k != 'harnessImageUrl'}
        after = {k: v for k, v in new['config'].items() if k != 'harnessImageUrl'}
        r.require(before == after, 'Model, reasoning or other solver behavior changed')
        probe = proof['probeResults'][model]
        r.require(probe['status'] == 'succeeded' and probe['runConfigVersionId'] == new['id'], 'Wrong/failed successor probe')
        for path, digest in spec.get('evidenceHashes', {}).items():
            hashes.update(pin(path, digest))
        configs[model] = {'baseId': old['id'], 'successorId': new['id'],
                          'basePath': str(basepath.resolve()), 'successorPath': str(Path(spec['configPath']).resolve()),
                          'image': spec['image']}
    return configs, hashes


def prepare_content_successor(workbook_manifest_path, runtime_manifest_path, work=r.WORK):
    work = Path(work)
    r.require(not (directory(work) / 'activation.json').exists(), 'Content activation already exists')
    rows, campaign = _base(work)
    bases = {x['taskId']: x for x in rows}
    _, prompts = r.validate_staged(work)
    prompt_map = {x['taskId']: x for x in prompts}
    raw = r.read(workbook_manifest_path)
    patches = raw if isinstance(raw, list) else raw['repairs']
    r.require(len({x['taskId'] for x in patches}) == len(patches), 'Duplicate workbook patch')
    patch_map = {x['taskId']: x for x in patches}
    r.require(set(patch_map) <= set(bases), 'Workbook patch outside roster')
    hashes = {**pin(workbook_manifest_path), **pin(work / 'validity-fixes/manifest.json'),
              **pin(work / 'final-assets.json')}
    if (work / 'runtime/overlay-manifest.json').exists():
        hashes.update(pin(work / 'runtime/overlay-manifest.json', campaign['runtimeManifestSha256']))
    for task, patch in patch_map.items():
        base = bases[task]
        r.require(patch['baseWorkbookSha256'] == base['workbookSha256'], 'Patch base mismatch')
        hashes.update(pin(base['workbookPath'], base['workbookSha256']))
        hashes.update(pin(patch['workbookPath'], patch['workbookSha256']))
        r.require(patch['workbookSha256'] != base['workbookSha256'], 'Asset repair has unchanged content')
        hashes.update(pin(patch['validationPath'], patch['validationSha256']))
        validation = r.read(patch['validationPath'])
        if 'validation' in validation:
            r.require(validation['taskId'] == task and validation['workbookSha256'] == patch['workbookSha256']
                      and validation['baseWorkbookSha256'] == base['workbookSha256']
                      and validation['answerKeySha256'] == base['answerKeySha256'],
                      'Workbook validation receipt has wrong task or content binding')
            hashes.update(pin(validation['sourceEvidencePath'], validation['sourceEvidenceSha256']))
            validation = validation['validation']
        r.require(all(validation[name] is True for name in ('inputsPreserved', 'calculatedDisclosuresOnly', 'solvable')),
                  'Workbook repair lacks required validation')
    configs, runtime_hashes = _runtime(runtime_manifest_path, patch_map, work)
    hashes.update(runtime_hashes)
    legacy_path = work / 'validity-fixes/activation.json'
    legacy = r.activation(work) if legacy_path.exists() else None
    legacy_versions = {x['taskId']: x for x in legacy['revisions']} if legacy else {}
    if legacy:
        hashes.update(pin(legacy_path))
        r.require(set(legacy_versions) <= set(prompt_map), 'Predecessor prompt activation omitted from union')
    union = []
    for task in sorted(set(prompt_map) | set(patch_map)):
        base = bases[task]
        basepath = work / 'problems' / task / 'verified.json'
        original = r.read(basepath)
        predecessor_path = Path(legacy_versions[task]['verifiedPath']) if task in legacy_versions else basepath
        predecessor = r.read(predecessor_path)
        r.require(predecessor.get('lockedAt'), 'Predecessor version not locked')
        hashes.update(pin(basepath))
        hashes.update(pin(predecessor_path))
        row = copy.deepcopy(prompt_map.get(task, {}))
        row.update({'taskId': task, 'originalProblemVersionId': original['id'],
                    'baseProblemVersionId': predecessor['id'], 'baseVersionPath': str(predecessor_path.resolve()),
                    'oldVersionIds': sorted({original['id'], predecessor['id']}),
                    'baseWorkbookPath': base['workbookPath'], 'baseWorkbookSha256': base['workbookSha256'],
                    'answerKeyPath': base['answerKeyPath'], 'answerKeySha256': base['answerKeySha256'],
                    'assetRepaired': task in patch_map, 'promptRepaired': task in prompt_map})
        for field in ('promptPath', 'promptSha256', 'workbookPath', 'workbookSha256'):
            owner = patch_map[task] if task in patch_map and field.startswith('workbook') else prompt_map.get(task, base)
            row[field] = owner.get(field, base[field])
        row['defaultSolverRunConfigVersionId'] = configs['opus']['successorId'] if row['assetRepaired'] else predecessor['solverRunConfigVersionId']
        r.require(row['promptRepaired'] or row['assetRepaired'], 'Empty content revision')
        for field, digest in [('promptPath', 'promptSha256'), ('workbookPath', 'workbookSha256'), ('answerKeyPath', 'answerKeySha256')]:
            hashes.update(pin(row[field], row[digest]))
        if task in prompt_map:
            p = prompt_map[task]
            for path in [p['basePromptPath'], p['nativeSourcePath'], p['validationPath'], *p['sourceEvidencePaths']]:
                hashes.update(pin(path))
        union.append(row)
    plan = {'schemaVersion': 1, 'kind': 'content_successor', 'status': 'prepared',
            'baseAssetsSha256': campaign['assetsSha256'], 'baseRuntimeManifestSha256': campaign['runtimeManifestSha256'],
            'workbookManifestPath': str(Path(workbook_manifest_path).resolve()), 'workbookManifestSha256': r.sha(workbook_manifest_path),
            'runtimeManifestPath': str(Path(runtime_manifest_path).resolve()), 'configs': configs,
            'predecessorPromptActivationSha256': r.sha(legacy_path) if legacy else None,
            'artifactHashes': hashes, 'revisions': union}
    path = directory(work) / 'plan.json'
    if path.exists():
        r.require(r.read(path) == plan, 'Existing content plan differs; reconcile before replacement')
    else:
        r.c.save(path, plan)
    return plan


def validate_plan(work=r.WORK):
    plan = r.read(directory(work) / 'plan.json')
    r.require(plan['status'] == 'prepared' and plan['kind'] == 'content_successor', 'Invalid content plan')
    for path, digest in plan['artifactHashes'].items():
        pin(path, digest)
    _, campaign = _base(work)
    r.require(plan['baseAssetsSha256'] == campaign['assetsSha256'], 'Content plan base changed')
    r.require(len({x['taskId'] for x in plan['revisions']}) == len(plan['revisions']), 'Duplicate content task')
    legacy = Path(work) / 'validity-fixes/activation.json'
    r.require((r.sha(legacy) if legacy.exists() else None) == plan['predecessorPromptActivationSha256'],
              'Content plan has an unlinked legacy activation')
    return plan


def _same_behavior(base, revised, row):
    r.require(revised.get('lockedAt'), 'Corrected content version not locked')
    expected = copy.deepcopy(base)
    expected['solverRunConfigVersionId'] = row['defaultSolverRunConfigVersionId']
    r.same_behavior(expected, revised)
    r.require(revised['prompt'] == Path(row['promptPath']).read_text(), 'Content prompt mismatch')
    r.require(revised['id'] not in row['oldVersionIds'], 'Content version must be distinct')


def provision_content_successor(work=r.WORK):
    plan = validate_plan(work)
    r.require(not (directory(work) / 'activation.json').exists(), 'Already activated')
    for row in plan['revisions']:
        base = r.read(row['baseVersionPath'])
        remote = r.c.cli('problem-versions', 'get', '--problem-version-id', base['id'])
        r.require(remote == base, 'Remote content predecessor differs')
        folder = directory(work) / 'versions' / row['taskId']
        created = r.c.mutation(folder, 'create', ['problem-versions', 'create', '--problem-id', base['problemId'], '--copy-from-version-id', base['id']])
        payload = {}
        prompt = Path(row['promptPath']).read_text()
        if prompt != base['prompt']: payload['prompt'] = prompt
        if row['defaultSolverRunConfigVersionId'] != base['solverRunConfigVersionId']:
            payload['solverRunConfigVersionId'] = row['defaultSolverRunConfigVersionId']
        request = folder / 'update.request.json'
        if request.exists(): r.require(r.read(request) == payload, 'Changed provision payload')
        vid = created['id']
        if payload:
            r.c.mutation(folder, 'update', ['problem-versions', 'update', '--problem-version-id', vid], payload)
        r.c.mutation(folder, 'lock', ['problem-versions', 'lock', '--problem-version-id', vid])
        verified = r.c.cli('problem-versions', 'get', '--problem-version-id', vid)
        _same_behavior(base, verified, row)
        r.c.save(folder / 'verified.json', verified)
    return {'status': 'provisioned_not_activated', 'tasks': [x['taskId'] for x in plan['revisions']]}


def activate_content_successor(work=r.WORK, *, confirmed=False):
    r.require(confirmed, 'Content activation requires explicit confirmation')
    plan = validate_plan(work)
    destination = directory(work) / 'activation.json'
    r.require(not destination.exists(), 'Immutable content activation already exists')
    activated = copy.deepcopy(plan)
    for row in activated['revisions']:
        folder = directory(work) / 'versions' / row['taskId']
        path = folder / 'verified.json'
        revised = r.read(path)
        _same_behavior(r.read(row['baseVersionPath']), revised, row)
        row['verifiedPath'] = str(path.resolve())
        row['newProblemVersionId'] = revised['id']
        for artifact in folder.glob('*.json'): activated['artifactHashes'].update(pin(artifact))
    activated.update(status='ready', activatedAt=datetime.now(timezone.utc).isoformat(),
                     planSha256=r.sha(directory(work) / 'plan.json'), replacementPolicy='all_three_attempts_both_models')
    r.c.save(destination, activated)
    return activated


def activation(work=r.WORK):
    data = r.read(directory(work) / 'activation.json')
    r.require(data['status'] == 'ready' and data['kind'] == 'content_successor', 'Content activation not ready')
    r.require(data['planSha256'] == r.sha(directory(work) / 'plan.json'), 'Content plan changed after activation')
    plan = validate_plan(work)
    for key, value in plan.items():
        if key not in {'status', 'artifactHashes', 'revisions'}:
            r.require(data.get(key) == value, 'Activated content plan field diverged: ' + key)
    r.require(all(data['artifactHashes'].get(path) == digest for path, digest in plan['artifactHashes'].items()),
              'Activation omitted or changed a planned artifact hash')
    projected = [{k: value for k, value in row.items() if k not in {'verifiedPath', 'newProblemVersionId'}}
                 for row in data['revisions']]
    r.require(projected == plan['revisions'], 'Activated content revision membership or behavior diverged')
    for path, digest in data['artifactHashes'].items(): pin(path, digest)
    legacy = Path(work) / 'validity-fixes/activation.json'
    r.require((r.sha(legacy) if legacy.exists() else None) == data['predecessorPromptActivationSha256'], 'Unlinked legacy activation')
    for row in data['revisions']:
        revised = r.read(row['verifiedPath'])
        _same_behavior(r.read(row['baseVersionPath']), revised, row)
        r.require(revised['id'] == row['newProblemVersionId'], 'Activated content version ID mismatch')
    return data


def planned_configs(work=r.WORK):
    path = directory(work) / 'plan.json'
    return validate_plan(work)['configs'] if path.exists() else {}
