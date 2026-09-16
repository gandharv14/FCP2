import re,zipfile
import pytest
from test_named_offset_remediation import workbook
from xl_volatile_formula_remediation import _rewrite_offset,build_plan,apply_plan,verify_manifest

@pytest.mark.parametrize('sheet,qualifier',[('Model','Model!'),('LPI_Company Model',"'LPI_Company Model'!"),("Bob's Model","'Bob''s Model'!")])
def test_same_sheet_reference_preserves_qualifier_and_live_row(sheet,qualifier):
 f=f'OFFSET({qualifier}B151,$B$149,0)'
 assert _rewrite_offset(f,sheet_name=sheet)==(f'INDEX({qualifier}B:B,ROW({qualifier}B151)+($B$149))',1)

@pytest.mark.parametrize('formula',[
 "OFFSET('Other'!B151,B149,0)","OFFSET('[1]Model'!B151,B149,0)",
 "OFFSET('Model:Other'!B151,B149,0)","OFFSET(Model!B151,CASE,0)",
 "OFFSET(Model!B151,B149,)","OFFSET(Model!B151,B149+1,0)",
 "OFFSET(Model!XFE1,B149,0)","OFFSET(Model!B151,XFE1,0)",
 "OFFSET(Model!B151,Other!B149,0)","OFFSET(Model!B151,B149,0,1)",
 '"OFFSET(Model!B151,B149,0)"',"OFFSET(Model!B151,B149,0.0)",
])
def test_no_foreign_external_range_or_extra_dynamic_scope(formula):
 assert _rewrite_offset(formula,sheet_name='Model')==(formula,0)

def test_missing_source_sheet_context_cannot_authorize_qualifier():
 f='OFFSET(Model!B151,B149,0)';assert _rewrite_offset(f)==(f,0)

def test_full_original_package_values_and_metadata_unchanged(tmp_path):
 source,parts=workbook(tmp_path,[],formula="OFFSET('Model'!A1,B1,0)");before=source.read_bytes();plan=build_plan(source);assert plan['status']=='eligible'
 action=next(a for a in plan['actions']if a['kind']=='replace_formula');assert action['rule_id']=='offset-same-sheet-scalar-cell-row-zero-column/v1';assert action['same_sheet_qualifiers']==["'Model'!"]
 target=tmp_path/'candidate.xlsx';manifest=apply_plan(source,plan,target);assert verify_manifest(source,target,plan,manifest)['status']=='verified';assert source.read_bytes()==before
 with zipfile.ZipFile(target)as z:
  for name,raw in parts.items():
   if name=='xl/worksheets/sheet1.xml':assert re.sub(rb'<f>.*?</f>',b'',raw)==re.sub(rb'<f>.*?</f>',b'',z.read(name))
   else:assert raw==z.read(name)

@pytest.mark.parametrize('kind',['shared','array'])
def test_qualified_reference_retains_existing_shared_and_array_guards(tmp_path,kind):
 source,parts=workbook(tmp_path,[],formula='OFFSET(Model!XFD1,B1,0)');xml=parts['xl/worksheets/sheet1.xml'].decode();attrs=f't="{kind}" ref="C1:D1"'+(' si="0"'if kind=='shared'else'');xml=xml.replace('<f>OFFSET(Model!XFD1,B1,0)</f>',f'<f {attrs}>OFFSET(Model!XFD1,B1,0)</f>')
 if kind=='shared':xml=xml.replace('</row>','<c r="D1"><f t="shared" si="0"/><v>0</v></c></row>',1)
 parts['xl/worksheets/sheet1.xml']=xml.encode()
 with zipfile.ZipFile(source,'w')as z:
  for n,v in parts.items():z.writestr(n,v)
 assert build_plan(source)['status']=='ineligible'
