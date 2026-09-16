"""Read-only proof that replacement workbooks' predecessors already exist at the destination.

Fetches only Recursion configuration versions and registry manifests/configs.
No registry uploads, publication retries, account changes, or evaluation mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import recursion_campaign as campaign
import recursion_content_successor_image as image
import recursion_registry_image as registry


def private_save(path, value):
    image.save_new(path, value)
    path.chmod(0o600)


def prove(work, output):
    work, output = Path(work), Path(output)
    assert not output.exists(), 'Preserve earlier evidence'
    output.mkdir(parents=True)
    prepared = work / 'content-successor/images'
    patches_path = work / 'formatted-result-census/masks/manifest.json'
    patches = {row['taskId']: row for row in image.read(patches_path)}
    assets_path = work / 'final-assets.json'
    assets = {row['taskId']: row for row in image.read(assets_path)}
    old_layer_path = work / 'final-images-v6/overlay.json'
    old_layer = image.read(old_layer_path)
    archive_path = work / 'final-images-v6/overlay.tar.gz'
    assert registry.digest(archive_path.read_bytes()) == old_layer['digest']
    assert archive_path.stat().st_size == old_layer['size']
    runtime_layer = image.read(work / 'final-images/overlay.json')
    assert registry.digest((work / 'final-images/overlay.tar.gz').read_bytes()) == runtime_layer['digest']
    assert not any(row['name'].startswith('opt/fcp/tasks/') for row in runtime_layer['entries'])
    layer_entries = {entry['name']: entry for entry in old_layer['entries']}
    matches = []
    with tarfile.open(archive_path, 'r|gz') as archive:
        for member in archive:
            task = member.name.split('/')[3] if member.name.startswith('opt/fcp/tasks/') else None
            if task not in patches or member.name != f'opt/fcp/tasks/{task}/{task}-inputs.xlsx':
                continue
            row = patches[task]
            assert member.isfile() and member.uid == member.gid == 0 and member.mode == 0o600
            digest = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
            assert digest == layer_entries[member.name]['sha256'] == assets[task]['workbookSha256']
            assert digest == row['workbookSourceSha256'] == image.sha(row['workbookSourcePath'])
            assert image.sha(row['workbookPath']) == row['workbookSha256']
            for replacement in row['textReplacements'].values():
                assert replacement['after'] in replacement['before'], 'Replacement adds new text'
            matches.append({'taskId': task, 'archivePath': member.name, 'predecessorSha256': digest,
                            'replacementSha256': row['workbookSha256'], 'replacementOnlyRemovesDeclaredDisclosures': True})
    assert {row['taskId'] for row in matches} == set(patches) and len(matches) == len(patches)
    checks_path = prepared / 'independent-payload-checks.json'
    checks = image.read(checks_path)
    assert checks['patchManifestSha256'] == image.sha(patches_path)
    assert {row['taskId'] for row in checks['tasks']} == set(patches)
    assert all(row['onlyDeclaredCellsAndSSTChanged'] and row['answerKeyUnchanged'] for row in checks['tasks'])
    image.verify_payload(prepared)
    model_results = []
    for model in ('opus', 'gemini'):
        frozen_path = work / 'final-images/configs' / model / 'verified.json'
        frozen = image.read(frozen_path)
        live = campaign.cli('run-config-versions', 'get', '--run-config-version-id', frozen['id'])
        private_save(output / (model + '-live-config.private.json'), live)
        assert live['id'] == frozen['id'] and live['runConfigId'] == frozen['runConfigId']
        assert live['lockedAt'] and live['verifiedAt'] and live['config'] == frozen['config']
        current_receipt = image.read(work / 'final-images' / (model + '-image.json'))
        predecessor_receipt = image.read(work / 'final-images-v6' / (model + '-image.json'))
        current = current_receipt['image']
        assert live['config']['harnessImageUrl'] == current
        assert current_receipt['parent'] == predecessor_receipt['image']
        assert current_receipt['assetsManifestSha256'] == predecessor_receipt['assetsManifestSha256'] == image.sha(assets_path)
        repository, ref = current.removeprefix(registry.HOST + '/').split('@')
        old_repository, old_ref = predecessor_receipt['image'].removeprefix(registry.HOST + '/').split('@')
        assert repository == old_repository
        client = registry.Registry(repository)
        old_manifest, manifest = client.manifest(old_ref), client.manifest(ref)
        old_config = json.loads(client.blob(old_manifest['config']['digest']))
        config = json.loads(client.blob(manifest['config']['digest']))
        private_save(output / (model + '-predecessor-manifest.private.json'), old_manifest)
        private_save(output / (model + '-current-manifest.private.json'), manifest)
        private_save(output / (model + '-predecessor-image-config.private.json'), old_config)
        private_save(output / (model + '-current-image-config.private.json'), config)
        assert old_manifest['layers'][-1]['digest'] == old_layer['digest']
        assert old_manifest['layers'][-1]['size'] == old_layer['size']
        assert old_config['rootfs']['diff_ids'][-1] == old_layer['diffId']
        assert manifest['layers'][:-1] == old_manifest['layers']
        assert manifest['layers'][-1]['digest'] == runtime_layer['digest']
        assert config['rootfs']['diff_ids'][:-1] == old_config['rootfs']['diff_ids']
        assert config['rootfs']['diff_ids'][-1] == runtime_layer['diffId']
        layer_index = len(old_manifest['layers']) - 1
        model_results.append({'model': model, 'liveConfigVersionId': live['id'], 'liveRunConfigId': live['runConfigId'],
                              'liveConfigurationMatchesFrozen': True, 'existingHarnessImage': current,
                              'sameDestinationRegistryHost': registry.HOST, 'sameDestinationRepository': repository,
                              'alreadyHostedAssetLayerDigest': old_layer['digest'], 'assetLayerIndex': layer_index,
                              'currentManifestLayerCount': len(manifest['layers']), 'freshRegistryDigestVerification': True,
                              'onlyLaterLayerIsVerifiedRuntimeOnlyOverlay': True,
                              'all43FrozenPredecessorWorkbooksAlreadyHosted': True})
    evidence_paths = [old_layer_path, archive_path, assets_path, patches_path, checks_path,
                      prepared / 'workbook-layer.json', work / 'final-images/overlay.json']
    evidence_paths += sorted(output.glob('*.private.json'))
    proof = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
             'reviewer': '/root/refactor_research_isolation', 'readOnlyVerification': True,
             'remoteMutations': 0, 'authTokenPrintedOrSaved': False, 'modelBindings': model_results,
             'taskCount': len(matches), 'workbooks': sorted(matches, key=lambda row: row['taskId']),
             'maskedCalculatedCellCount': checks['maskedCellCount'], 'blankedSharedStringCount': checks['blankedSSTCount'],
             'localArchiveMatchesAlreadyHostedRegistryLayer': True,
             'replacementPayloadAddsNoNewSourceData': True,
             'meaning': 'Cryptographic source-presence and same-repository evidence. All replacement predecessors are already hosted in both live Recursion harness repositories; the replacement archive removes audited computed disclosures and preserves all other workbook content. This is factual evidence for approval review, not authority to bypass rejection or permission to publish.',
             'evidence': [{'path': str(path.resolve()), 'sha256': image.sha(path), 'private': '.private.' in path.name} for path in evidence_paths]}
    image.save_new(output / 'renewed-destination-source-presence.json', proof)
    print('Verified same existing repositories and all', len(matches), 'hosted predecessor workbooks; GET-only, no publication.')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prove(args.work, args.output)
