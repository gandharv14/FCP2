"""Verify a workbook-only amendment over the immutable first content successor.

Local proof preparation is inert. Finalization reads the registry and six fresh
probe receipts; neither operation publishes, provisions, or activates anything.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import recursion_content_successor_image as image
import recursion_content_successor_verify as verify
import recursion_hardening_content as content


def local_proof(work, folder, gate_path):
    work, folder, gate_path = Path(work), Path(folder), Path(gate_path)
    layer, _ = image.verify_payload(folder)
    assert layer['schemaVersion'] == 2 and layer.get('predecessor')
    activation_path = work / 'content-successor/activation.json'
    activation = content.activation(work)
    previous_runtime = image.read(activation['runtimeManifestPath'])
    previous_proof_path = Path(previous_runtime['payloadProofPath'])
    previous_proof = image.read(previous_proof_path)
    assert layer['predecessor']['activation'] == image.file_receipt(activation_path)
    assert layer['predecessor']['payloadProof'] == image.file_receipt(previous_proof_path)
    assert previous_runtime['payloadProofSha256'] == image.sha(previous_proof_path)
    assert previous_proof['status'] == 'verified'
    root = {r['taskId']: r for r in image.read(work / 'final-assets.json')}
    current, _ = image.predecessor_assets(work / 'final-assets.json', activation_path,
        work / 'content-successor/images', work / 'runtime/overlay-manifest.json')
    patches = image.read(layer['patchManifest']['path'])
    if isinstance(patches, dict): patches = patches['tasks']
    patches = {r['taskId']: r for r in patches}
    gate = image.read(gate_path)
    assert gate['status'] == 'complete'
    assert len(gate['taskIds']) == len(set(gate['taskIds'])) and set(gate['taskIds']) == set(root)
    assert len(gate['affectedTaskIds']) == len(set(gate['affectedTaskIds']))
    assert set(gate['affectedTaskIds']) == set(patches)
    assert set(gate['approvedMasks']) == set(patches)
    for task, row in patches.items():
        assert gate['approvedMasks'][task] == image.file_receipt(row['maskSpecificationPath'])
        assert row['maskSpecificationSha256'] == gate['approvedMasks'][task]['sha256']
    assert gate['evidenceHashes']
    hashes = dict(gate['evidenceHashes'])
    for path, digest in hashes.items(): assert image.sha(path) == digest
    descriptor_path = Path(previous_proof['nonWorkbookDescriptorPath'])
    assert image.sha(descriptor_path) == previous_proof['nonWorkbookDescriptorFileSha256']
    descriptor_digest = verify.registry.digest(json.dumps(image.read(descriptor_path),
        sort_keys=True, separators=(',', ':')).encode()).split(':')[1]
    assert descriptor_digest == previous_proof['nonWorkbookPayloadSuccessorSha256']
    for path in (gate_path, activation_path, previous_proof_path, descriptor_path,
                 folder / 'workbook-layer.json', Path(layer['patchManifest']['path'])):
        hashes[str(path.resolve())] = image.sha(path)
    review_path = folder / 'payload-review.json'
    review = image.read(review_path)
    assert review['status'] == 'approved' and review['scope'] == 'workbook-only-content-successor'
    assert review['payloadManifestSha256'] == image.sha(folder / 'workbook-layer.json')
    assert review['patchManifestSha256'] == layer['patchManifest']['sha256']
    assert review['layerDigest'] == layer['digest'] and review['reviewer']
    hashes[str(review_path.resolve())] = image.sha(review_path)
    books = {t: {'rootSha256': r['workbookSha256'],
                 'predecessorSha256': current[t]['workbookSha256'],
                 'successorSha256': patches.get(t, current[t])['workbookSha256']}
             for t, r in root.items()}
    return {'schemaVersion': 2, 'kind': 'content_amendment_payload', 'status': 'prepared',
            'baseAssetsSha256': image.sha(work / 'final-assets.json'),
            'frozenRuntimeSha256': image.sha(work / 'runtime/overlay-manifest.json'),
            'predecessorActivationPath': str(activation_path.resolve()),
            'predecessorActivationSha256': image.sha(activation_path),
            'predecessorPayloadProofPath': str(previous_proof_path.resolve()),
            'predecessorPayloadProofSha256': image.sha(previous_proof_path),
            'censusGatePath': str(gate_path.resolve()), 'censusGateSha256': image.sha(gate_path),
            'nonWorkbookDescriptorPath': str(descriptor_path.resolve()),
            'nonWorkbookDescriptorFileSha256': image.sha(descriptor_path),
            'nonWorkbookPayloadPredecessorSha256': descriptor_digest,
            'nonWorkbookPayloadSuccessorSha256': descriptor_digest,
            'workbooks': books, 'probeResults': {}, 'evidenceHashes': hashes,
            'preservationArgument': 'The verified layer contains only the approved workbook replacements. '
                'Every predecessor image layer and runtime configuration is preserved; no directory, '
                'research, grader, symlink, whiteout, or runtime entry is added.'}


def prepare(work, folder, gate_path):
    proof = local_proof(work, folder, gate_path)
    image.save_new(Path(folder) / 'payload-proof.prepared.json', proof)
    return proof


def finalize(work, folder):
    work, folder = Path(work), Path(folder)
    assert not (folder / 'payload-proof.json').exists()
    assert not (folder / 'runtime-manifest.json').exists()
    prepared = image.read(folder / 'payload-proof.prepared.json')
    assert prepared == local_proof(work, folder, prepared['censusGatePath'])
    layer, _ = image.verify_payload(folder)
    proof = copy.deepcopy(prepared)
    proof.update(status='verified', registryResults={})
    normalized = {key: proof[key] for key in ('schemaVersion', 'baseAssetsSha256',
        'frozenRuntimeSha256', 'predecessorActivationPath', 'predecessorActivationSha256',
        'predecessorPayloadProofPath', 'predecessorPayloadProofSha256')}
    normalized.update(kind='content_amendment_runtime', models={})
    probe_ids = set()
    parent_probe_ids = {image.read(work / 'content-successor/images/configs' / model /
        (stage + '-status.json'))['id'] for model in ('opus', 'gemini') for stage in verify.STAGES}
    for model in ('opus', 'gemini'):
        old_path = work / 'content-successor/images/configs' / model / 'verified.json'
        config_path = folder / 'configs' / model / 'verified.json'
        old, config = image.read(old_path), image.read(config_path)
        published_path = folder / (model + '-image.json')
        published = image.read(published_path)
        assert old['id'] != config['id'] and old['runConfigId'] != config['runConfigId']
        assert config['lockedAt'] and config['verifiedAt']
        assert config['config']['harnessImageUrl'] == published['image']
        assert published['parent'] == old['config']['harnessImageUrl'] == layer['baseImages'][model]['image']
        assert {k:v for k,v in config['config'].items() if k != 'harnessImageUrl'} == {
            k:v for k,v in old['config'].items() if k != 'harnessImageUrl'}
        stages, hashes = {}, {}
        for stage in verify.STAGES:
            base = config_path.parent
            status_path, request_path = base / (stage + '-status.json'), base / (stage + '-update.request.json')
            response_path = base / (stage + '-probe.response.json')
            status, request = image.read(status_path), image.read(request_path)
            assert status['id'] == image.read(response_path)['probeRunId']
            assert status['id'] not in probe_ids, 'Each model/stage needs a distinct fresh probe'
            assert status['id'] not in parent_probe_ids, 'Predecessor probes cannot verify a new image'
            probe_ids.add(status['id'])
            commands = frozenset()
            review_path = base / (stage + '-shell-review.json')
            if review_path.exists():
                commands = verify.reviewed_probe_commands(image.read(review_path), status, request)
                hashes[str(review_path.resolve())] = image.sha(review_path)
            stages[stage] = verify.verify_probe(status, request, config, model, stage, commands)
            for path in (status_path, request_path, response_path): hashes[str(path.resolve())] = image.sha(path)
        proof['probeResults'][model] = {'status': 'succeeded', 'runConfigVersionId': config['id'], 'stages': stages}
        proof['registryResults'][model] = verify.verify_registry_pair(published['parent'], published['image'], layer)
        for path in (old_path, config_path, published_path): hashes[str(path.resolve())] = image.sha(path)
        proof['evidenceHashes'].update(hashes)
        normalized['models'][model] = {'configPath': str(config_path.resolve()),
            'configSha256': image.sha(config_path), 'image': published['image'], 'evidenceHashes': hashes}
    assert len(probe_ids) == 6
    proof_path = folder / 'payload-proof.json'
    image.save_new(proof_path, proof)
    normalized.update(payloadProofPath=str(proof_path.resolve()), payloadProofSha256=image.sha(proof_path))
    image.save_new(folder / 'runtime-manifest.json', normalized)
    return normalized


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'finalize'))
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--gate', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        assert args.gate
        prepare(args.work, args.folder, args.gate)
    else: finalize(args.work, args.folder)
