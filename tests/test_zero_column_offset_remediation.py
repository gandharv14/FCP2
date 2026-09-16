"""The existing scalar-cell dynamic row may spell its zero column explicitly."""
import re,zipfile
import pytest
from test_named_offset_remediation import workbook
from xl_volatile_formula_remediation import _rewrite_offset,build_plan,apply_plan,verify_manifest

@pytest.mark.parametrize('row',['B4','$B$4','B$4','$B4'])
def test_explicit_zero_keeps_exact_live_cell_operand(row):
    assert _rewrite_offset(f'OFFSET(F43,{row},0)') == _rewrite_offset(f'OFFSET(F43,{row},)')
    assert _rewrite_offset(f'OFFSET(F43,{row},0)') == (f'INDEX(F:F,ROW(F43)+({row}))',1)

@pytest.mark.parametrize('formula',[
 'OFFSET(A1,CASE,0)','OFFSET(A1,B1-1,0)','OFFSET(A1,(B1),0)',
 'OFFSET(A1,B1,0,1)','OFFSET(A1,B1,1)','OFFSET(A1,B1,-0)',
 'OFFSET(A1,B1,0.0)','OFFSET(A1,B1,00)','OFFSET(A1:B2,B1,0)',
 'OFFSET(Sheet2!A1,B1,0)','OFFSET(A1,Sheet2!B1,0)',
 'OFFSET(XFE1,B1,0)','OFFSET(A1048577,B1,0)',
 'OFFSET(A1,XFE1,0)','OFFSET(A1,B1048577,0)',
 'MYOFFSET(A1,B1,0)','"OFFSET(A1,B1,0)"',
])
def test_extra_dynamic_scope_stays_unchanged(formula):
    assert _rewrite_offset(formula,named_rows={'case':{}})==(formula,0)


def test_original_package_inputs_names_and_caches_survive(tmp_path):
    source,parts=workbook(tmp_path,[],formula='OFFSET(A1,B1,0)')
    original=source.read_bytes();plan=build_plan(source);assert plan['status']=='eligible'
    action=next(a for a in plan['actions']if a['kind']=='replace_formula')
    assert action['rule_id']=='offset-scalar-cell-row-zero-column/v1'
    assert action['after_formula']=='INDEX(A:A,ROW(A1)+(B1))'
    target=tmp_path/'candidate.xlsx';manifest=apply_plan(source,plan,target)
    assert verify_manifest(source,target,plan,manifest)['status']=='verified'
    assert source.read_bytes()==original
    with zipfile.ZipFile(target) as z:
        for name,raw in parts.items():
            if name!='xl/worksheets/sheet1.xml':assert z.read(name)==raw
        before=re.sub(rb'<f>.*?</f>',b'',parts['xl/worksheets/sheet1.xml'])
        after=re.sub(rb'<f>.*?</f>',b'',z.read('xl/worksheets/sheet1.xml'))
        assert before==after

@pytest.mark.parametrize('kind,ref',[('shared','C1:D1'),('array','C1:D1')])
def test_shared_invalid_copy_or_multicell_array_stays_blocked(tmp_path,kind,ref):
    source,parts=workbook(tmp_path,[],formula='OFFSET(XFD1,B1,0)')
    xml=parts['xl/worksheets/sheet1.xml'].decode()
    attrs=f't="{kind}" ref="{ref}"'+(' si="0"'if kind=='shared'else'')
    xml=xml.replace('<f>OFFSET(XFD1,B1,0)</f>',f'<f {attrs}>OFFSET(XFD1,B1,0)</f>')
    if kind=='shared':xml=xml.replace('</row>','<c r="D1"><f t="shared" si="0"/><v>0</v></c></row>',1)
    parts['xl/worksheets/sheet1.xml']=xml.encode()
    with zipfile.ZipFile(source,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    plan=build_plan(source);assert plan['status']=='ineligible'
    assert any(x['kind']==('offset_shared_boundary_unresolved'if kind=='shared'else'offset_array_scope_unresolved')for x in plan['unresolved'])
