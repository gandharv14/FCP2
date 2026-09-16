import zipfile

import pytest
from openpyxl.formula.translate import Translator

from test_volatile_formula_remediation import _workbook
from xl_volatile_formula_remediation import (
    _rewrite_identity_offset, apply_plan, build_plan, verify_manifest,
)


@pytest.mark.parametrize('reference', ['A1', '$A1', 'A$1', '$A$1', 'XFD1048576'])
def test_identity_preserves_reference_and_shared_translation(reference):
    formula = f'=SUM(A1:OFFSET({reference},0,0))'
    result, count = _rewrite_identity_offset(formula)
    assert count == 1
    assert result == f'=SUM(A1:{reference})'
    if reference != 'XFD1048576':
        # openpyxl cannot translate the left endpoint of A1:OFFSET(...).
        # Check the preserved addressing flags independently, then the repaired
        # ordinary range, which Excel and the downstream translator can copy.
        target = Translator('='+reference, origin='B2').translate_formula('D4')[1:]
        assert Translator(result, origin='B2').translate_formula('D4') == f'=SUM(C3:{target})'


@pytest.mark.parametrize('formula', [
    'OFFSET(A1,1,0)', 'OFFSET(A1,0,1)', 'OFFSET(A1,B2,0)',
    'OFFSET(A1,0,0,2)', 'OFFSET(A1,0,0,1,2)', 'OFFSET(A1:B2,0,0)',
    'OFFSET(XFE1,0,0)', 'OFFSET(A1048577,0,0)', 'MYOFFSET(A1,0,0)',
    '"OFFSET(A1,0,0)"', '"a ""OFFSET(A1,0,0)"" b"',
])
def test_identity_does_not_admit_dynamic_sized_invalid_or_text_calls(formula):
    assert _rewrite_identity_offset(formula) == (formula, 0)


def test_identity_package_preserves_original_caches_and_member_bytes(tmp_path):
    source = tmp_path / 'source.xlsx'
    _workbook(source)
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members['xl/worksheets/sheet1.xml'] = members['xl/worksheets/sheet1.xml'].replace(
        b'OFFSET(C$3,$A$3,)', b'SUM(C$1:OFFSET(C$2,0,0))')
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items(): archive.writestr(name, value)
    original_bytes = source.read_bytes()
    plan = build_plan(source)
    assert plan['status'] == 'eligible'
    assert plan['tool_version'] == 'volatile-formula-remediation/v13'
    assert plan['action_counts']['offset-scalar-identity-and-blank-column/v2'] == 1
    output = tmp_path / 'candidate.xlsx'
    manifest = apply_plan(source, plan, output)
    assert verify_manifest(source, output, plan, manifest)['status'] == 'verified'
    assert source.read_bytes() == original_bytes
    with zipfile.ZipFile(output) as archive:
        sheet = archive.read('xl/worksheets/sheet1.xml')
        assert b'SUM(C$1:C$2)' in sheet
        assert b'<c r="D3"><f t="shared" si="2"/><v>6</v></c>' in sheet
        for name, value in members.items():
            if name not in manifest['changed_members']:
                assert archive.read(name) == value
