import zipfile

import pytest
from openpyxl.formula.translate import Translator

from test_volatile_formula_remediation import _workbook
from xl_volatile_formula_remediation import (
    _rewrite_constant_offset, apply_plan, build_plan, verify_manifest,
)


@pytest.mark.parametrize('reference,target', [
    ('C20','C19'), ('$C20','$C19'), ('C$20','C$19'), ('$C$20','$C$19'),
])
def test_row_predecessor_preserves_copy_semantics(reference,target):
    original = f'=OFFSET({reference},-1,0)'
    rewritten, count = _rewrite_constant_offset(original)
    assert (rewritten,count) == ('='+target,1)
    for destination in ['E3','B5','M17']:
        shifted = Translator(original, origin='D2').translate_formula(destination)
        assert _rewrite_constant_offset(shifted)[0] == Translator(
            rewritten,origin='D2').translate_formula(destination)


@pytest.mark.parametrize('formula,expected', [
    ('SUM(D146:OFFSET(AH146,0,-1))','SUM(D146:AG146)'),
    ('OFFSET(Z9,+2,+1)','AA11'),
    ('MIN(OFFSET($AA$8,-2,-1),OFFSET(B7,3,2))','MIN($Z$6,D10)'),
    ('OFFSET(B2,-1,-1)','A1'),
    ('OFFSET(XFC1048575,1,1)','XFD1048576'),
])
def test_constant_scalar_references_resolve_in_expression_and_range(formula,expected):
    assert _rewrite_constant_offset(formula)[0] == expected


@pytest.mark.parametrize('formula', [
    'OFFSET(A1,-1,0)', 'OFFSET(A1,0,-1)', 'OFFSET(XFD1,0,1)',
    'OFFSET(A1048576,1,0)', 'OFFSET(XFE1,0,-1)',
    'OFFSET(C20,-1,0,1)', 'OFFSET(C20,-1,0,,)',
    'OFFSET(C20,-1.5,0)', 'OFFSET(C20,A1,0)', 'OFFSET(C20,-1,CASE)',
    'OFFSET(Sheet1!C20,-1,0)', 'OFFSET(C20:D20,-1,0)',
    'MYOFFSET(C20,-1,0)', '"OFFSET(C20,-1,0)"',
])
def test_non_scalar_dynamic_external_size_and_invalid_targets_remain_blocked(formula):
    assert _rewrite_constant_offset(formula) == (formula,0)


def test_constant_displacement_full_package_retains_cache_and_source(tmp_path):
    source=tmp_path/'original.xlsx';_workbook(source)
    with zipfile.ZipFile(source) as z:members={name:z.read(name) for name in z.namelist()}
    members['xl/worksheets/sheet1.xml']=members['xl/worksheets/sheet1.xml'].replace(
        b'OFFSET(C$3,$A$3,)',b'SUM(C$1:OFFSET(C$3,-1,0))')
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in members.items():z.writestr(name,raw)
    original=source.read_bytes();plan=build_plan(source)
    assert plan['status']=='eligible'
    assert plan['action_counts']['offset-constant-scalar-and-blank-column/v3']==1
    output=tmp_path/'candidate.xlsx';manifest=apply_plan(source,plan,output)
    assert verify_manifest(source,output,plan,manifest)['status']=='verified'
    assert source.read_bytes()==original
    with zipfile.ZipFile(output) as z:
        assert b'SUM(C$1:C$2)</f><v>5</v>' in z.read('xl/worksheets/sheet1.xml')
        for name,raw in members.items():
            if name not in manifest['changed_members']:assert z.read(name)==raw


def test_shared_copy_cannot_repair_an_invalid_original_base(tmp_path):
    source=tmp_path/'original.xlsx';_workbook(source)
    with zipfile.ZipFile(source) as z:members={name:z.read(name) for name in z.namelist()}
    # Master C3 has a valid reference, but copying to D3 moves XFD to XFE.
    # Blindly translating the rewritten XFC1 would disguise that invalid base.
    members['xl/worksheets/sheet1.xml']=members['xl/worksheets/sheet1.xml'].replace(
        b'OFFSET(C$3,$A$3,)',b'OFFSET(XFD1,0,-1)')
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in members.items():z.writestr(name,raw)
    plan=build_plan(source)
    assert plan['status']=='ineligible'
    assert any(x['kind']=='offset_shared_boundary_unresolved'
               and x['location']=='Model!D3' for x in plan['unresolved'])
    with pytest.raises(ValueError):apply_plan(source,plan,tmp_path/'candidate.xlsx')
