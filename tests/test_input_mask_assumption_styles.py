"""Generated numeric constants must not inherit source date/time style zero."""
import zipfile
from xml.etree import ElementTree as ET
import openpyxl
import pytest
from xl_input_mask import inject_assumptions, _assumptions_number_style, verify

@pytest.mark.parametrize('existing_general', [False, True])
def test_numeric_inputs_preserved_when_default_style_is_date(tmp_path, existing_general):
    w = openpyxl.Workbook()
    w._cell_styles[0].numFmtId = 22
    w.active['A1'] = 'Original label'
    if existing_general:
        w.active['B1'] = 123
        w.active['B1'].number_format = 'General'
    p = tmp_path / 'masked.xlsx'
    w.save(p)
    source = tmp_path / 'source.xlsx'
    source.write_bytes(p.read_bytes())
    with zipfile.ZipFile(p) as z:
        prior_sheet = z.read('xl/worksheets/sheet1.xml')
        prior_styles = ET.fromstring(z.read('xl/styles.xml'))
    entries = [{'host':'Model!A1','value':0.95}, {'host':'Model!A2','value':215.901745938155}]
    title = inject_assumptions(p, entries, ['Sheet'])
    actual = openpyxl.load_workbook(p)
    assert actual[title]['D2'].value == 0.95
    assert actual[title]['D3'].value == 215.901745938155
    assert actual[title]['D2'].number_format == 'General'
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(p) as z:
        assert z.read('xl/worksheets/sheet1.xml') == prior_sheet
        current = ET.fromstring(z.read('xl/styles.xml'))
    old_xfs = prior_styles.find(ns+'cellXfs')
    new_xfs = current.find(ns+'cellXfs')
    assert [ET.tostring(x) for x in new_xfs][:len(old_xfs)] == [ET.tostring(x) for x in old_xfs]
    faults = verify(p, source, {}, {}, {}, {}, assumptions_sheet=title, expected_assumptions=entries)
    assert not any(faults.values())
    actual[title]['D2'] = 0.94
    actual.save(p)
    assert verify(p, source, {}, {}, {}, {}, assumptions_sheet=title, expected_assumptions=entries)['changed']


def test_prefixed_styles_append_general_without_reindexing():
    raw = b'<s:styleSheet xmlns:s="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><s:cellXfs count="1"><s:xf numFmtId="22"/></s:cellXfs></s:styleSheet>'
    parts={'xl/styles.xml':raw}
    assert _assumptions_number_style(parts) == 1
    assert b'<s:xf numFmtId="22"/>' in parts['xl/styles.xml']
    assert _assumptions_number_style(parts) == 1
