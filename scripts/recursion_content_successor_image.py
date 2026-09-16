"""Prepare/publish a reviewed workbook-only layer over frozen audit images.

Preparation is local. Publication is a separate explicit command requiring the
independent payload-review receipt; it never edits the original campaign files.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import recursion_registry_image as registry


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save_new(path, value):
    with Path(path).open('x') as handle:
        handle.write(json.dumps(value, indent=2) + '\n')


def file_receipt(path):
    return {'path': str(Path(path).resolve()), 'sha256': sha(path)}


def predecessor_assets(assets_path, predecessor_path, base_folder, runtime_path):
    """Validate the first immutable successor before composing a later delta."""
    import recursion_hardening_content as content
    work = Path(assets_path).resolve().parent
    assert Path(predecessor_path).resolve() == work / 'content-successor/activation.json'
    activation = content.activation(work)
    assert activation['baseAssetsSha256'] == sha(assets_path)
    assert activation['baseRuntimeManifestSha256'] == sha(runtime_path)
    runtime = read(activation['runtimeManifestPath'])
    proof_path = Path(runtime['payloadProofPath'])
    assert sha(proof_path) == runtime['payloadProofSha256']
    proof = read(proof_path)
    assets = {row['taskId']: dict(row) for row in read(assets_path)}
    for row in activation['revisions']:
        assets[row['taskId']].update({k: row[k] for k in ('workbookPath', 'workbookSha256')})
    assert set(proof['workbooks']) == set(assets)
    for task, row in assets.items():
        assert proof['workbooks'][task]['successorSha256'] == row['workbookSha256']
        assert sha(row['workbookPath']) == row['workbookSha256']
    for model in ('opus', 'gemini'):
        parent = read(Path(base_folder) / (model + '-image.json'))
        assert parent['image'] == activation['configs'][model]['image']
        assert parent['image'] == runtime['models'][model]['image']
    return assets, {'activation': file_receipt(predecessor_path),
                    'payloadProof': file_receipt(proof_path)}


def validate_inputs(assets_path, patches_path, base_folder, runtime_path, predecessor_path=None):
    assets = {row['taskId']: row for row in read(assets_path)}
    if predecessor_path is not None:
        assets, _ = predecessor_assets(assets_path, predecessor_path, base_folder, runtime_path)
    patches = read(patches_path)
    if isinstance(patches, dict):
        patches = patches['tasks']
    assert patches, 'Empty workbook patch set'
    seen = set()
    for row in patches:
        task = row['taskId']
        assert re.fullmatch(r'[A-Za-z0-9_.-]+', task) and task not in ('.', '..')
        assert task not in seen, 'Duplicate patched task'
        seen.add(task)
        original = assets[task]
        assert row['workbookSourceSha256'] == original['workbookSha256']
        assert Path(row['workbookSourcePath']).resolve() == Path(original['workbookPath']).resolve()
        assert sha(row['workbookSourcePath']) == original['workbookSha256']
        patch = Path(row['workbookPath'])
        assert patch.is_file() and not patch.is_symlink()
        assert patch.resolve() != Path(original['workbookPath']).resolve()
        assert patch.name == Path(original['workbookPath']).name == task + '-inputs.xlsx'
        assert sha(patch) == row['workbookSha256'] != original['workbookSha256']
        assert isinstance(row['maskedCellCount'], int) and row['maskedCellCount'] > 0
        assert row.get('validation'), 'Missing mask validation evidence'
        if predecessor_path is not None:
            import recursion_amendment_mask_validation as masks
            spec_path = Path(row['maskSpecificationPath'])
            assert sha(spec_path) == row['maskSpecificationSha256']
            spec = read(spec_path)
            assert spec['taskId'] == task
            assert Path(spec['workbookPath']).resolve() == Path(original['workbookPath']).resolve()
            assert spec['workbookSha256'] == original['workbookSha256']
            assert spec['answerKeySha256'] == original['answerKeySha256']
            assert Path(spec['answerKeyPath']).resolve() == Path(original['answerKeyPath']).resolve()
            validation_path = Path(row['validationPath'])
            assert sha(validation_path) == row['validationSha256']
            fresh = masks.verify_mask(spec_path, patch, expected_spec_sha256=row['maskSpecificationSha256'],
                                      expected_output_sha256=row['workbookSha256'])
            assert read(validation_path) == fresh, 'Mask validation receipt is stale or false'
            assert row['validation'] == fresh['validation'], 'Patch validation flags differ'
    images = {}
    for model in ('opus', 'gemini'):
        receipt_path = Path(base_folder) / (model + '-image.json')
        receipt = read(receipt_path)
        assert receipt.get('verifiedAt')
        assert receipt['assetsManifestSha256'] == sha(assets_path)
        assert receipt['runtimeManifestSha256'] == sha(runtime_path)
        assert re.fullmatch(re.escape(registry.HOST) + r'/lb-ml-dev/fde-runners/runner-fcp-[^@]+@sha256:[0-9a-f]{64}', receipt['image'])
        images[model] = {'receipt': file_receipt(receipt_path), 'image': receipt['image']}
    return sorted(patches, key=lambda row: row['taskId']), images


def prepare(folder, assets_path, patches_path, base_folder, runtime_path, predecessor_path=None):
    folder = Path(folder)
    assert not folder.resolve().is_relative_to(Path(base_folder).resolve()), 'Do not write inside original image receipts'
    # Fail before writing if an existing payload could be replaced.
    for name in ('workbook-layer.tar.gz', 'workbook-layer.json'):
        assert not (folder / name).exists(), 'Use a fresh successor folder'
    patches, images = validate_inputs(assets_path, patches_path, base_folder, runtime_path, predecessor_path)
    folder.mkdir(parents=True, exist_ok=True)
    payload = io.BytesIO()
    entries = []
    with tarfile.open(fileobj=payload, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for row in patches:
            data = Path(row['workbookPath']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row['workbookSha256']
            name = 'opt/fcp/tasks/' + row['taskId'] + '/' + Path(row['workbookPath']).name
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
            entries.append({'taskId': row['taskId'], 'name': name, 'sha256': row['workbookSha256'],
                            'bytes': len(data), 'mode': '0o600', 'uid': 0, 'gid': 0})
    uncompressed = payload.getvalue()
    target = folder / 'workbook-layer.tar.gz'
    with target.open('xb') as handle:
        handle.write(gzip.compress(uncompressed, mtime=0))
    result = {'schemaVersion': 1, 'kind': 'workbook-only-content-successor',
              'baseAssets': file_receipt(assets_path), 'patchManifest': file_receipt(patches_path),
              'runtimeManifest': file_receipt(runtime_path), 'baseImages': images,
              'digest': 'sha256:' + sha(target), 'diffId': registry.digest(uncompressed),
              'size': target.stat().st_size, 'entries': entries}
    if predecessor_path is not None:
        _, result['predecessor'] = predecessor_assets(assets_path, predecessor_path, base_folder, runtime_path)
        result['schemaVersion'] = 2
    save_new(folder / 'workbook-layer.json', result)
    return result


def verify_payload(folder):
    folder = Path(folder)
    receipt = read(folder / 'workbook-layer.json')
    for key in ('baseAssets', 'patchManifest', 'runtimeManifest'):
        assert sha(receipt[key]['path']) == receipt[key]['sha256'], key + ' changed'
    for base in receipt['baseImages'].values():
        assert sha(base['receipt']['path']) == base['receipt']['sha256'], 'Base image receipt changed'
        assert read(base['receipt']['path'])['image'] == base['image']
    predecessor_path = None
    if receipt.get('predecessor'):
        assert receipt['schemaVersion'] == 2
        for entry in receipt['predecessor'].values():
            assert sha(entry['path']) == entry['sha256'], 'Predecessor evidence changed'
        predecessor_path = receipt['predecessor']['activation']['path']
    patches, images = validate_inputs(receipt['baseAssets']['path'], receipt['patchManifest']['path'],
                                      Path(receipt['baseImages']['opus']['receipt']['path']).parent,
                                      receipt['runtimeManifest']['path'], predecessor_path)
    if predecessor_path:
        _, expected_predecessor = predecessor_assets(receipt['baseAssets']['path'], predecessor_path,
            Path(receipt['baseImages']['opus']['receipt']['path']).parent, receipt['runtimeManifest']['path'])
        assert receipt['predecessor'] == expected_predecessor
    assert images == receipt['baseImages']
    raw = (folder / 'workbook-layer.tar.gz').read_bytes()
    assert registry.digest(raw) == receipt['digest'] and len(raw) == receipt['size']
    payload = gzip.decompress(raw)
    assert registry.digest(payload) == receipt['diffId']
    expected = {row['taskId']: row for row in patches}
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        members = archive.getmembers()
        assert len(members) == len(expected) == len(receipt['entries'])
        for member, entry in zip(members, receipt['entries']):
            row = expected.pop(entry['taskId'])
            name = 'opt/fcp/tasks/' + row['taskId'] + '/' + row['taskId'] + '-inputs.xlsx'
            assert member.name == entry['name'] == name
            assert member.isfile() and member.uid == member.gid == 0 and member.mode == 0o600
            assert member.size == entry['bytes']
            data = archive.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == entry['sha256'] == row['workbookSha256']
    assert not expected
    return receipt, raw


def append_layer(manifest, config, layer):
    """Pure composition, preserving all model/runtime image configuration."""
    assert config['architecture'] == 'amd64' and config['os'] == 'linux'
    manifest, config = copy.deepcopy(manifest), copy.deepcopy(config)
    assert len(config['rootfs']['diff_ids']) == len(manifest['layers'])
    config['rootfs']['diff_ids'].append(layer['diffId'])
    config.setdefault('history', []).append({'created': '2026-09-15T00:00:00Z',
        'created_by': 'FCP reviewed workbook-only patch ' + layer['digest']})
    config_bytes = registry.canonical(config)
    manifest['config'] = {**manifest['config'], 'digest': registry.digest(config_bytes), 'size': len(config_bytes)}
    media = ('application/vnd.oci.image.layer.v1.tar+gzip' if 'oci' in manifest['mediaType']
             else 'application/vnd.docker.image.rootfs.diff.tar.gzip')
    manifest['layers'].append({'mediaType': media, 'digest': layer['digest'], 'size': layer['size']})
    return manifest, config_bytes


def publish(folder, review_path):
    folder = Path(folder)
    layer, raw = verify_payload(folder)
    review = read(review_path)
    assert review['status'] == 'approved' and review['scope'] == 'workbook-only-content-successor'
    assert review['payloadManifestSha256'] == sha(folder / 'workbook-layer.json')
    assert review['patchManifestSha256'] == layer['patchManifest']['sha256']
    assert review['layerDigest'] == layer['digest']
    assert review.get('reviewer'), 'Independent payload reviewer must be named'
    if layer.get('predecessor'):
        import recursion_content_amendment_verify as amendment
        prepared = read(folder / 'payload-proof.prepared.json')
        work = Path(layer['baseAssets']['path']).resolve().parent
        assert prepared == amendment.local_proof(work, folder, prepared['censusGatePath']), 'Amendment approval proof changed'
    for model, base in layer['baseImages'].items():
        image = base['image']
        repository, digest = image.removeprefix(registry.HOST + '/').split('@')
        client = registry.Registry(repository)
        manifest = client.manifest(digest)
        config = json.loads(client.blob(manifest['config']['digest']))
        successor, config_bytes = append_layer(manifest, config, layer)
        manifest_bytes = registry.canonical(successor)
        result_digest = registry.digest(manifest_bytes)
        receipt_path = folder / (model + '-image.json')
        if receipt_path.exists():
            # A second-model transport failure can be resumed without changing
            # the first model's already verified immutable receipt.
            previous = read(receipt_path)
            assert previous['image'] == registry.HOST + '/' + repository + '@' + result_digest
            assert previous['parent'] == image and previous['layerDigest'] == layer['digest']
            assert previous['payloadManifestSha256'] == sha(folder / 'workbook-layer.json')
            assert previous['patchManifestSha256'] == layer['patchManifest']['sha256']
            assert previous['runtimeManifestSha256'] == layer['runtimeManifest']['sha256']
            assert previous['assetsManifestSha256'] == layer['baseAssets']['sha256']
            assert previous['payloadReview'] == file_receipt(review_path)
            assert previous.get('verifiedAt')
            check, _ = client.request('GET', f'/v2/{repository}/manifests/{result_digest}')
            assert registry.digest(check) == result_digest
            continue
        assert client.push_blob(raw) == layer['digest']
        assert client.push_blob(config_bytes) == successor['config']['digest']
        tag = 'audit-content-' + result_digest.split(':')[1][:20]
        client.request('PUT', f'/v2/{repository}/manifests/{tag}', manifest_bytes, successor['mediaType'])
        check, _ = client.request('GET', f'/v2/{repository}/manifests/{result_digest}')
        assert registry.digest(check) == result_digest
        save_new(receipt_path, {
            'image': registry.HOST + '/' + repository + '@' + result_digest, 'parent': image,
            'layerDigest': layer['digest'], 'assetsManifestSha256': layer['baseAssets']['sha256'],
            'patchManifestSha256': layer['patchManifest']['sha256'],
            'payloadManifestSha256': sha(folder / 'workbook-layer.json'),
            'runtimeManifestSha256': layer['runtimeManifest']['sha256'],
            'payloadReview': file_receipt(review_path), 'verifiedAt': datetime.now(timezone.utc).isoformat()})
        print(model, 'published', result_digest, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    for argument in ('folder', 'assets', 'patches', 'base-folder', 'runtime'):
        prep.add_argument('--' + argument, type=Path, required=True)
    pub = sub.add_parser('publish')
    pub.add_argument('--folder', type=Path, required=True)
    pub.add_argument('--review', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.folder, args.assets, args.patches, args.base_folder, args.runtime)
    else:
        publish(args.folder, args.review)
