"""Workbook labels stay within their table cell; actual supplied values persist."""
import copy,json
import pytest
import xl_variable_source_audit as audit
from grader.tests.test_variable_source_audit import write_two_variable_fixture,GOOD_MODEL_TABLE


@pytest.mark.parametrize('separator',['\n','\r\n','\r','\v','\f','\x85','\u2028','\u2029'])
def test_multiline_inventory_label_preserves_complete_three_column_coverage(tmp_path,separator):
    artifact,segment=write_two_variable_fixture(tmp_path)
    inventory=audit.build_inventory('0001',artifact,segment)
    inventory['variables'][0]['label']='First label'+separator+'Second | label'
    original=copy.deepcopy(inventory)
    by_id={row['row_id']:row for row in inventory['variables']}
    resolved=audit.resolve_table(GOOD_MODEL_TABLE,by_id)
    assert 'First label<br>Second \\| label' in resolved
    assert len(resolved.splitlines())==len(audit._table_data_rows(GOOD_MODEL_TABLE))+2
    coverage={'schema_version':'variable-source-audit-coverage/v1',
        'inventory_sha256':audit._canonical_hash(inventory),
        'batches':[{'row_ids':list(by_id),'model_table':GOOD_MODEL_TABLE}]}
    assert audit.validate_audit_coverage(resolved,inventory,coverage)==[]
    assert inventory==original
    broken=resolved.replace('First label<br>Second','First label\nSecond')
    assert 'final audit table must contain exactly three columns' in audit.validate_audit_coverage(broken,inventory,coverage)


def test_single_line_rendering_and_raw_values_are_unchanged():
    row={'band':'Sheet!A1','label':'Revenue | growth','value_summary':{'count':1,'value':0.125}}
    assert audit._resolved_cells_text(row)=='`Sheet!A1` Revenue \\| growth: `0.125`'
