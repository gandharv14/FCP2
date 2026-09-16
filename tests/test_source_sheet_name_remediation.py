from pathlib import Path
import zipfile,html,xml.etree.ElementTree as ET
import pytest
from xl_volatile_formula_remediation import _source_sheet_name,build_plan,apply_plan,verify_manifest,RemediationError
from test_volatile_formula_remediation import _workbook

SHORT='MID(CELL("filename",$A$1),FIND("]",CELL("filename",$A$1))+1,255)'
LONG='MID(CELL("filename",A1),FIND("]",CELL("filename",A1))+1,LEN(CELL("filename",A1))-FIND("]",CELL("filename",A1)))'

@pytest.mark.parametrize('formula',[SHORT,LONG,SHORT.lower(),'  '+LONG.replace(',',', ')+'  '])
def test_exact_metadata_extraction(formula):
    assert _source_sheet_name(formula,'Actual "Sheet"')=='"Actual ""Sheet"""'

@pytest.mark.parametrize('formula',[
    SHORT+'+1','IF(TRUE,'+SHORT+',"Other")',SHORT.replace(',255)',',2)'),
    SHORT.replace('filename','address'),SHORT.replace('$A$1','Other!$A$1'),
    SHORT.replace('$A$1','XFE1'),SHORT.replace('$A$1','A1048577'),
    SHORT.replace('CELL("filename",$A$1))+1','CELL("filename",$B$1))+1'),
    SHORT.replace('FIND("]"','FIND("[")'),SHORT.replace('+1','+2'),
    '"'+SHORT,SHORT+'"','"'+SHORT.replace('"','""')+'"',
    'CELL("filename",A1)','TODAY()','NOW()',
])
def test_partial_dynamic_or_malformed_forms_stay_unresolved(formula):
    assert _source_sheet_name(formula,'Model') is None


def source(path,formula,*,array=False,cache='Model',array_ref='E1'):
    _workbook(path)
    with zipfile.ZipFile(path)as z:parts={n:z.read(n)for n in z.namelist()}
    attrs=f' t="array" ref="{array_ref}"'if array else ''
    old=b'<c r="E1"><v>45200</v></c>'
    new=f'<c r="E1" t="str"><f{attrs}>{html.escape(formula)}</f><v>{cache}</v></c>'.encode()
    assert old in parts['xl/worksheets/sheet1.xml'];parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(old,new)
    with zipfile.ZipFile(path,'w')as z:
        for n,b in parts.items():z.writestr(n,b)

@pytest.mark.parametrize('array',[False,True])
def test_real_package_preserves_formula_cache_and_original_sheet_metadata(tmp_path,array):
    original=tmp_path/'source.xlsx';candidate=tmp_path/'candidate.xlsx';source(original,LONG,array=array,cache='stale-cache-must-survive')
    before=original.read_bytes();plan=build_plan(original)
    action=next(a for a in plan['actions']if a['rule_id']=='cell-filename-current-sheet-name/v1')
    assert action['after_formula']=='"Model"'
    manifest=apply_plan(original,plan,candidate);assert verify_manifest(original,candidate,plan,manifest)['status']=='verified'
    assert original.read_bytes()==before
    with zipfile.ZipFile(candidate)as z:
        sheet=z.read('xl/worksheets/sheet1.xml');cell=next(c for c in ET.fromstring(sheet).findall('.//{*}c')if c.attrib['r']=='E1');assert cell.find('{*}f').text=='"Model"';assert cell.find('{*}f').attrib=={}
        assert b'<v>stale-cache-must-survive</v>'in sheet
        assert b'CELL('not in sheet
    # The proof derives metadata; stale saved values are preserved for the later
    # independent full numerical gate to reject, not replaced to match it.


def test_multi_cell_array_cannot_be_replaced_by_scalar_metadata(tmp_path):
    original=tmp_path/'source.xlsx';source(original,SHORT,array=True,array_ref='E1:E2')
    plan=build_plan(original);assert plan['status']=='ineligible'
    assert not any(a['rule_id']=='cell-filename-current-sheet-name/v1'for a in plan['actions'])
    with pytest.raises(RemediationError):apply_plan(original,plan,tmp_path/'candidate.xlsx')

@pytest.mark.parametrize('location',['bracket]directory/source.xlsx','source].xlsx'])
def test_saved_path_closing_bracket_prevents_sheet_name_rewrite(tmp_path,location):
    original=tmp_path/location;original.parent.mkdir(parents=True,exist_ok=True);source(original,SHORT)
    plan=build_plan(original)
    assert plan['status']=='ineligible'
    assert not any(a['rule_id']=='cell-filename-current-sheet-name/v1'for a in plan['actions'])
