"""Original source custody remains verifiable after metadata-only cleanup."""
import copy
import re
import zipfile

import pytest

from test_volatile_formula_remediation import _workbook
from xl_inventory_approval import original_source_ledger
from xl_source_inventory import (
    SourceInventoryError, _object_hash, _validate_transformation,
    assemble_inventory_manifest, metadata_remediated_workbook_record,
)
from xl_volatile_formula_remediation import build_plan, apply_plan


def case(tmp_path, *, mixed=False):
    old = tmp_path / 'old.xlsx'
    source = tmp_path / 'original' / '0492.xlsx'
    target = tmp_path / 'derived' / '0492.xlsx'
    source.parent.mkdir(); target.parent.mkdir()
    _workbook(old, today=not mixed)
    with zipfile.ZipFile(old) as a, zipfile.ZipFile(source, 'w') as b:
        for info in a.infolist():
            raw = a.read(info.filename)
            if not mixed and info.filename == 'xl/worksheets/sheet1.xml':
                raw = re.sub(r'<f(?:\s[^>]*)?>.*?</f>|<f[^>]*/>',
                    lambda m: '<f>TODAY()</f>' if 'TODAY()' in m.group() else '', raw.decode()).encode()
            b.writestr(info, raw)
    plan = build_plan(source)
    receipt = apply_plan(source, plan, target)
    return source, target, plan, receipt


def test_inventory_retains_original_source_and_unresolved_clock(tmp_path):
    source, target, plan, receipt = case(tmp_path)
    record = metadata_remediated_workbook_record('0492', source, target, plan, receipt)
    inventory = assemble_inventory_manifest([record])
    row = original_source_ledger(inventory)[0]
    assert row['original_source']['sha256'] == record['transformation']['original_source']['sha256']
    assert row['source_sha256'] == record['sha256'] != row['original_source']['sha256']
    assert record['route'] == 'restricted_pass'
    assert record['reason_codes'] == ['worksheet_today']


@pytest.mark.parametrize('field', ['original_bytes', 'candidate_bytes', 'plan', 'receipt', 'identity'])
def test_lineage_rejects_changed_input_even_if_outer_hash_recomputed(tmp_path, field):
    source, target, plan, receipt = case(tmp_path)
    record = metadata_remediated_workbook_record('0492', source, target, plan, receipt)
    changed = copy.deepcopy(record)
    lineage = changed['transformation']
    if field == 'original_bytes': source.write_bytes(source.read_bytes() + b'changed')
    elif field == 'candidate_bytes': target.write_bytes(target.read_bytes() + b'changed')
    elif field == 'identity': changed['workbook_id'] = '0493'
    elif field == 'plan':
        lineage['plan']['preserved_worksheet_restrictions'][0]['formula_sha256'] = '0' * 64
        lineage['plan_sha256'] = _object_hash(lineage['plan'])
    else:
        lineage['receipt']['changed_members'] = []
        lineage['receipt_sha256'] = _object_hash(lineage['receipt'])
    with pytest.raises(SourceInventoryError): _validate_transformation(changed)


def test_lineage_does_not_admit_formula_rewrites(tmp_path):
    source, target, plan, receipt = case(tmp_path, mixed=True)
    with pytest.raises(SourceInventoryError, match='only proven dead-name cleanup'):
        metadata_remediated_workbook_record('0492', source, target, plan, receipt)
