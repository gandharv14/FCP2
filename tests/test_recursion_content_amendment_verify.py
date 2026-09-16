import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_content_amendment_verify as amendment
import recursion_content_successor_image as image
from test_recursion_content_successor_image import inputs, amendment_inputs, write


@pytest.fixture
def prepared(tmp_path, amendment_inputs, monkeypatch):
    assets, patches, base, runtime_path, activation_path = amendment_inputs
    work = tmp_path
    canonical_runtime = work / 'runtime/overlay-manifest.json'
    canonical_runtime.parent.mkdir(); canonical_runtime.write_bytes(runtime_path.read_bytes())
    # The predecessor fixture's image receipts have the same runtime hash.
    new_base = work / 'content-successor/images'
    for model in ('opus', 'gemini'):
        (new_base / (model + '-image.json')).write_bytes((base / (model + '-image.json')).read_bytes())
    first_runtime_path = new_base / 'runtime-manifest.json'
    first_runtime = image.read(first_runtime_path)
    first_proof_path = Path(first_runtime['payloadProofPath'])
    first_proof = image.read(first_proof_path)
    descriptor_path = new_base / 'non-workbook-payload-descriptor.json'
    write(descriptor_path, {'files': [{'name': 'trusted-runtime', 'sha256': 'frozen'}]})
    digest = amendment.verify.registry.digest(amendment.verify.registry.canonical(image.read(descriptor_path))).split(':')[1]
    first_proof.update(status='verified', nonWorkbookDescriptorPath=str(descriptor_path),
        nonWorkbookDescriptorFileSha256=image.sha(descriptor_path), nonWorkbookPayloadSuccessorSha256=digest)
    write(first_proof_path, first_proof)
    first_runtime['payloadProofSha256'] = image.sha(first_proof_path)
    write(first_runtime_path, first_runtime)
    folder = work / 'content-amendments/0002/images'
    layer = image.prepare(folder, assets, patches, new_base, canonical_runtime, activation_path)
    review_path = folder / 'payload-review.json'
    write(review_path, {'status': 'approved', 'scope': 'workbook-only-content-successor',
        'payloadManifestSha256': image.sha(folder / 'workbook-layer.json'),
        'patchManifestSha256': image.sha(patches), 'layerDigest': layer['digest'], 'reviewer': 'independent test reviewer'})
    gate_path = work / 'gate.json'
    write(gate_path, {'status': 'complete', 'taskIds': ['SampleACLtd_model'],
        'affectedTaskIds': ['SampleACLtd_model'],
        'approvedMasks': {'SampleACLtd_model': image.file_receipt(image.read(patches)[0]['maskSpecificationPath'])},
        'evidenceHashes': {str(review_path): image.sha(review_path)}})
    return work, folder, gate_path


def test_proof_pins_all_three_workbook_generations_and_remains_inert(prepared):
    work, folder, gate = prepared
    proof = amendment.prepare(work, folder, gate)
    book = proof['workbooks']['SampleACLtd_model']
    assert len(set(book.values())) == 3
    assert proof['nonWorkbookPayloadPredecessorSha256'] == proof['nonWorkbookPayloadSuccessorSha256']
    assert proof['probeResults'] == {} and proof['status'] == 'prepared'
    assert not (folder / 'runtime-manifest.json').exists()
    assert not (folder.parent / 'activation.json').exists()
    with pytest.raises(FileExistsError): amendment.prepare(work, folder, gate)


@pytest.mark.parametrize('change', ['pending', 'missing-task', 'missing-delta', 'changed-evidence'])
def test_proof_rejects_incomplete_or_stale_review_gate(prepared, change):
    work, folder, gate = prepared
    data = image.read(gate)
    if change == 'pending': data['status'] = 'pending'
    elif change == 'missing-task': data['taskIds'] = []
    elif change == 'missing-delta': data['affectedTaskIds'] = []
    else: data['evidenceHashes'][str(folder / 'payload-review.json')] = '0' * 64
    write(gate, data)
    with pytest.raises(AssertionError): amendment.prepare(work, folder, gate)
    assert not (folder / 'payload-proof.prepared.json').exists()


def test_finalization_cannot_reuse_old_probe_absence(prepared, monkeypatch):
    work, folder, gate = prepared
    amendment.prepare(work, folder, gate)
    monkeypatch.setattr(amendment.verify, 'verify_registry_pair', lambda *args: pytest.fail('Registry before new config evidence'))
    with pytest.raises(FileNotFoundError): amendment.finalize(work, folder)
    assert not (folder / 'payload-proof.json').exists()
