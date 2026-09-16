import copy
import gzip
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_content_successor_image as image


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'original' / 'SampleACLtd_model-inputs.xlsx'
    patch = tmp_path / 'patched' / source.name
    for path, data in ((source, b'original workbook'), (patch, b'patched workbook')):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assets = tmp_path / 'final-assets.json'
    patches = tmp_path / 'patches.json'
    runtime = tmp_path / 'runtime.json'
    write(assets, [{'taskId': 'SampleACLtd_model', 'workbookPath': str(source), 'workbookSha256': image.sha(source)}])
    write(runtime, {'files': ['frozen runtime identity']})
    write(patches, [{'taskId': 'SampleACLtd_model', 'workbookSourcePath': str(source),
                     'workbookSourceSha256': image.sha(source), 'workbookPath': str(patch),
                     'workbookSha256': image.sha(patch), 'maskedCellCount': 1, 'validation': {'passed': True}}])
    base = tmp_path / 'final-images'
    for model in ('opus', 'gemini'):
        write(base / (model + '-image.json'), {'image': image.registry.HOST + '/lb-ml-dev/fde-runners/runner-fcp-' + model + '@sha256:' + 'a' * 64,
              'verifiedAt': 'test', 'assetsManifestSha256': image.sha(assets), 'runtimeManifestSha256': image.sha(runtime)})
    return assets, patches, base, runtime


def test_layer_contains_only_exact_named_workbook_and_is_deterministic(tmp_path, inputs):
    before = {str(p): image.sha(p) for p in tmp_path.rglob('*') if p.is_file()}
    folder = tmp_path / 'content-successor' / 'images'
    first = image.prepare(folder, *inputs)
    second = image.prepare(tmp_path / 'second', *inputs)
    assert first['digest'] == second['digest']
    receipt, raw = image.verify_payload(folder)
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw))) as archive:
        assert archive.getnames() == ['opt/fcp/tasks/SampleACLtd_model/SampleACLtd_model-inputs.xlsx']
        member = archive.getmembers()[0]
        assert member.mode == 0o600 and member.uid == member.gid == 0
        assert archive.extractfile(member).read() == b'patched workbook'
    assert receipt['runtimeManifest']['sha256'] == image.sha(inputs[3])
    assert all(image.sha(path) == digest for path, digest in before.items())
    with pytest.raises(AssertionError, match='fresh successor'):
        image.prepare(folder, *inputs)


@pytest.mark.parametrize('mutation', ['source-hash', 'duplicate', 'runtime-hash', 'traversal'])
def test_input_guard_fails_before_payload_write(tmp_path, inputs, mutation):
    assets, patches, base, runtime = inputs
    rows = image.read(patches)
    if mutation == 'source-hash':
        rows[0]['workbookSourceSha256'] = '0' * 64
    elif mutation == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == 'traversal':
        rows[0]['taskId'] = '../escape'
    else:
        write(runtime, {'changed': True})
    write(patches, rows)
    with pytest.raises((AssertionError, KeyError)):
        image.prepare(tmp_path / 'output', *inputs)
    assert not (tmp_path / 'output').exists()


def test_verify_rejects_changed_patch_or_base_receipt(tmp_path, inputs):
    folder = tmp_path / 'successor'
    image.prepare(folder, *inputs)
    base_receipt = inputs[2] / 'opus-image.json'
    content = image.read(base_receipt)
    content['image'] = content['image'].replace('a' * 64, 'b' * 64)
    write(base_receipt, content)
    with pytest.raises(AssertionError, match='Base image receipt changed'):
        image.verify_payload(folder)


def test_append_preserves_runtime_env_model_and_base_layer_bytes():
    manifest = {'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                'config': {'digest': 'sha256:base', 'size': 10, 'mediaType': 'config'},
                'layers': [{'digest': 'sha256:old', 'size': 100, 'mediaType': 'old'}]}
    config = {'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:old-diff']},
              'config': {'User': 'root', 'Entrypoint': ['python', '-m', 'setup'], 'Env': ['MODEL=locked'], 'WorkingDir': '/app'},
              'history': [{'created_by': 'base'}], 'created': 'old date'}
    old_manifest, old_config = copy.deepcopy(manifest), copy.deepcopy(config)
    layer = {'diffId': 'sha256:new-diff', 'digest': 'sha256:new', 'size': 12}
    successor, config_bytes = image.append_layer(manifest, config, layer)
    new_config = json.loads(config_bytes)
    assert manifest == old_manifest and config == old_config
    assert successor['layers'][:-1] == manifest['layers']
    assert new_config['config'] == config['config']
    assert new_config['created'] == config['created']
    assert new_config['rootfs']['diff_ids'] == ['sha256:old-diff', 'sha256:new-diff']
    assert new_config['history'][:-1] == config['history']


def test_publish_rejects_unbound_review_before_network(tmp_path, inputs, monkeypatch):
    folder = tmp_path / 'successor'
    image.prepare(folder, *inputs)
    review = tmp_path / 'review.json'
    write(review, {'status': 'approved', 'scope': 'workbook-only-content-successor', 'payloadManifestSha256': 'wrong'})
    monkeypatch.setattr(image.registry, 'Registry', lambda _: pytest.fail('Network must not run'))
    with pytest.raises(AssertionError):
        image.publish(folder, review)


@pytest.fixture
def amendment_inputs(tmp_path, inputs, monkeypatch):
    import recursion_hardening_content as content
    import recursion_amendment_mask_validation as masks
    assets, patches, base, runtime = inputs
    task = 'SampleACLtd_model'
    original = image.read(assets)[0]
    key = tmp_path / 'key.json'; write(key, {'private': 'unchanged'})
    original.update(answerKeyPath=str(key), answerKeySha256=image.sha(key))
    write(assets, [original])
    for model in ('opus', 'gemini'):
        parent_path = base / (model + '-image.json')
        parent = image.read(parent_path); parent['assetsManifestSha256'] = image.sha(assets)
        write(parent_path, parent)
    current = tmp_path / 'first-patch' / (task + '-inputs.xlsx')
    current.parent.mkdir(); current.write_bytes(b'first patch removes old disclosure')
    row = {'taskId': task, 'workbookPath': str(current), 'workbookSha256': image.sha(current)}
    patch = image.read(patches)[0]
    patch.update(workbookSourcePath=str(current), workbookSourceSha256=image.sha(current))
    spec_path = tmp_path / 'mask-spec.json'
    write(spec_path, {'taskId': task, 'workbookPath': str(current), 'workbookSha256': image.sha(current),
                     'answerKeyPath': str(key), 'answerKeySha256': image.sha(key)})
    validation_path = tmp_path / 'mask-validation.json'
    validation = {'validation': {'passed': True, 'exactApprovedMasksApplied': True}}
    write(validation_path, validation)
    patch.update(maskSpecificationPath=str(spec_path), maskSpecificationSha256=image.sha(spec_path),
                 validationPath=str(validation_path), validationSha256=image.sha(validation_path),
                 validation=validation['validation'])
    # Layer/proof unit fixtures use byte strings. Real ZIP transition behavior is
    # exercised separately by test_recursion_amendment_mask_validation.py.
    monkeypatch.setattr(masks, 'verify_mask', lambda *args, **kwargs: copy.deepcopy(validation))
    write(patches, [patch])
    proof_path = tmp_path / 'content-successor/images/payload-proof.json'
    write(proof_path, {'workbooks': {task: {'baseSha256': original['workbookSha256'],
                                          'successorSha256': image.sha(current)}}})
    runtime_path = tmp_path / 'content-successor/images/runtime-manifest.json'
    models = {m: {'image': image.read(base / (m + '-image.json'))['image']} for m in ('opus', 'gemini')}
    write(runtime_path, {'payloadProofPath': str(proof_path), 'payloadProofSha256': image.sha(proof_path), 'models': models})
    activation = {'baseAssetsSha256': image.sha(assets), 'baseRuntimeManifestSha256': image.sha(runtime),
                  'runtimeManifestPath': str(runtime_path), 'revisions': [row], 'configs': models}
    activation_path = tmp_path / 'content-successor/activation.json'
    write(activation_path, activation)
    monkeypatch.setattr(content, 'activation', lambda work: activation)
    return (*inputs, activation_path)


def test_second_layer_uses_immediate_predecessor_and_pins_chain(tmp_path, amendment_inputs):
    folder = tmp_path / 'content-amendments/0002/images'
    receipt = image.prepare(folder, *amendment_inputs)
    assert receipt['schemaVersion'] == 2
    assert receipt['predecessor']['activation'] == image.file_receipt(amendment_inputs[-1])
    assert image.verify_payload(folder)[0] == receipt
    amendment_inputs[-1].write_text('{}')
    with pytest.raises(AssertionError, match='Predecessor evidence changed'):
        image.verify_payload(folder)


def test_second_layer_rejects_restoration_from_original_source(tmp_path, amendment_inputs):
    assets, patches, base, runtime, activation = amendment_inputs
    original = image.read(assets)[0]
    patch = image.read(patches)[0]
    patch.update(workbookSourcePath=original['workbookPath'], workbookSourceSha256=original['workbookSha256'])
    write(patches, [patch])
    with pytest.raises(AssertionError):
        image.prepare(tmp_path / 'output', assets, patches, base, runtime, activation)
    assert not (tmp_path / 'output').exists()


def test_second_layer_rejects_wrong_parent_image(tmp_path, amendment_inputs):
    assets, patches, base, runtime, activation = amendment_inputs
    parent = image.read(base / 'gemini-image.json')
    parent['image'] = parent['image'].replace('a' * 64, 'b' * 64)
    write(base / 'gemini-image.json', parent)
    with pytest.raises(AssertionError):
        image.prepare(tmp_path / 'output', assets, patches, base, runtime, activation)
    assert not (tmp_path / 'output').exists()
