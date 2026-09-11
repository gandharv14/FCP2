"""Legacy self-assertions must never masquerade as independent semantics proof."""
from pathlib import Path

import pytest
from openpyxl import Workbook
import xl_source_inventory as inventory


def certificate(original='a'*64,converted='b'*64,**extra):
    value={'schema_version':inventory.CONVERSION_CERTIFICATE_SCHEMA_VERSION,
        'workbook_id':'0001','original_source_sha256':original,
        'converted_source_sha256':converted,'equivalent':True,
        'verification':{'deterministic':True,'method':'arbitrary self-assertion'},**extra}
    value['certificate_sha256']=inventory._object_hash(value)
    return value


def test_self_hashed_equivalence_flags_do_not_establish_trusted_proof():
    with pytest.raises(inventory.SourceInventoryError,match='unsupported.*proof|proof.*unsupported'):
        inventory._validate_equivalence_certificate(certificate(),workbook_id='0001',
            original_sha256='a'*64,converted_sha256='b'*64)


@pytest.mark.parametrize('extra',[
    {'verification':{'deterministic':True,'method':'trusted Excel'}},
    {'verification':{'deterministic':True,'method':'binary snapshot','passed':True}},
    {'receipt':{'signed':True,'signature_base64':'self-declared','engine':'excel-macos'}},
    {'original_snapshot_sha256':'c'*64,'converted_snapshot_sha256':'c'*64},
])
def test_unverified_receipt_or_snapshot_claims_do_not_enable_conversion(extra):
    with pytest.raises(inventory.SourceInventoryError):
        inventory._validate_equivalence_certificate(certificate(**extra),workbook_id='0001',
            original_sha256='a'*64,converted_sha256='b'*64)


def fixtures(tmp_path):
    sources=tmp_path/'sources';legacy=tmp_path/'legacy';certs=tmp_path/'certificates'
    for directory in (sources,legacy,certs):directory.mkdir()
    wb=Workbook();wb.active['A1']=3;wb.save(sources/'0001.xlsx')
    (legacy/'0001.xls').write_bytes(b'original binary bytes are not established semantics')
    return sources,legacy,certs


def test_inventory_builder_rejects_untrusted_equivalence_certificate(tmp_path):
    sources,legacy,certs=fixtures(tmp_path)
    doc=certificate(inventory.sha256_file(legacy/'0001.xls'),inventory.sha256_file(sources/'0001.xlsx'))
    (certs/'0001.json').write_bytes(inventory._canonical_bytes(doc))
    with pytest.raises(inventory.SourceInventoryError):
        inventory.build_inventory_manifest(sources,workbook_ids=['0001'],
            legacy_source_roots=[legacy],equivalence_certificate_roots=[certs])


def test_native_and_unverified_conversion_inventory_behavior_is_preserved(tmp_path):
    sources,legacy,certs=fixtures(tmp_path)
    native=inventory.build_inventory_manifest(sources,workbook_ids=['0001'])
    assert native['workbooks'][0]['classification']=='native_source'
    assert inventory.validate_inventory_manifest(native,source_root=sources)==native
    candidate=inventory.build_inventory_manifest(sources,workbook_ids=['0001'],legacy_source_roots=[legacy])
    assert candidate['workbooks'][0]['classification']=='conversion_unverified'
    assert candidate['workbooks'][0]['conversion']['equivalence_certificate'] is None
    assert inventory.validate_inventory_manifest(candidate,source_root=sources)==candidate


def test_existing_manifest_cannot_replay_a_self_asserted_equivalence(tmp_path):
    sources,legacy,certs=fixtures(tmp_path)
    baseline=inventory.build_inventory_manifest(sources,workbook_ids=['0001'],legacy_source_roots=[legacy])
    row=baseline['workbooks'][0]
    doc=certificate(row['conversion']['original_source']['sha256'],row['sha256'])
    payload=inventory._canonical_bytes(doc)
    row['conversion'].update(status='conversion_equivalent',equivalence_certificate={
        'record':{'root_index':0,'path':'0001.json','sha256':inventory._object_hash(doc),'size_bytes':len(payload)},
        'document':doc,'document_sha256':inventory._object_hash(doc)})
    row['classification']='conversion_equivalent'
    forged=baseline
    forged['classifications']={'conversion_equivalent':1}
    forged['classification_cohorts']={'conversion_equivalent':['0001'], 'conversion_unverified':[], 'native_source':[]}
    forged.pop('inventory_sha256')
    forged['inventory_sha256']=inventory._object_hash(forged)
    with pytest.raises(inventory.SourceInventoryError,match='certificate'):
        inventory.validate_inventory_manifest(forged,source_root=sources)
