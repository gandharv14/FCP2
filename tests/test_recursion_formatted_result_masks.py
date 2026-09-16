from pathlib import Path
from zipfile import ZipFile
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.recursion_formatted_result_masks import mask_formatted_workbook
from scripts.recursion_hardening_workbooks import sha256
from scripts.recursion_formatted_result_census import is_financial_result_text

def make_book(p):
 with ZipFile(p,'w') as z:
  z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Model" sheetId="1" r:id="rId1"/></sheets></workbook>')
  z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
  z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" s="7" t="inlineStr"><is><t>10% / 2x</t></is></c><c r="B1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c><c r="D1" t="s"><v>1</v></c><c r="E1"><v>100</v></c></row></sheetData></worksheet>')
  z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>11% / 3x</t></si><si><t>25%</t></si><si><t>12% / 4x</t></si></sst>')
  z.writestr('custom/preserved.bin',b'input artifacts')

def test_mask_inline_shared_and_orphan_preserves_live_shared_use(tmp_path):
 p=tmp_path/'in.xlsx';out=tmp_path/'out.xlsx';make_book(p)
 receipt=mask_formatted_workbook(p,out,{'Model!A1':'10% / 2x','Model!B1':'11% / 3x','Model!C1':'25%'},sha256(p),['12% / 4x'])
 assert receipt['maskedCellCount']==3
 assert receipt['blankedSharedStringIndices']==[0,2]
 assert receipt['preservedLiveSharedStringIndices']==[1]
 with ZipFile(out) as z:
  assert b'<c r="A1" s="7"></c>' in z.read('xl/worksheets/sheet1.xml')
  assert z.read('custom/preserved.bin')==b'input artifacts'
  assert z.read('xl/sharedStrings.xml').count(b'<si></si>')==2

def test_wrong_source_or_value_fails_closed(tmp_path):
 p=tmp_path/'in.xlsx';make_book(p)
 with pytest.raises(ValueError,match='hash'):mask_formatted_workbook(p,tmp_path/'out.xlsx',{},'bad')
 with pytest.raises(ValueError,match='text mismatch'):mask_formatted_workbook(p,tmp_path/'out.xlsx',{'Model!A1':'wrong'},sha256(p))
 with pytest.raises(ValueError,match='Never mutate'):mask_formatted_workbook(p,p,{},sha256(p))

def test_numeric_financial_string_screen_does_not_match_labels_dates():
 for t in ['10.2% / 1.4x','$4.20 / -3.2%','-5.6%','2.5x']:assert is_financial_result_text(t)
 for t in ['Q1 2024','2024-01-01','IRR / MOIC','Project 5% growth']:assert not is_financial_result_text(t)


def test_mixed_label_replacement_retains_label(tmp_path):
 p=tmp_path/'in.xlsx';out=tmp_path/'out.xlsx';make_book(p)
 receipt=mask_formatted_workbook(p,out,{'Model!A1':'10% / 2x'},sha256(p),text_replacements={'Model!A1':{'before':'10% / 2x','after':'NPV'}})
 with ZipFile(out) as z:
  assert b'<c r="A1" s="7" t="inlineStr"><is><t>NPV</t></is></c>' in z.read('xl/worksheets/sheet1.xml')
 assert receipt['textReplacements']['Model!A1']['after']=='NPV'


def test_shared_mixed_label_replacement_scrubs_old_entry(tmp_path):
 p=tmp_path/'in.xlsx';out=tmp_path/'out.xlsx';make_book(p)
 receipt=mask_formatted_workbook(p,out,{'Model!B1':'11% / 3x'},sha256(p),text_replacements={'Model!B1':{'before':'11% / 3x','after':'NPV'}})
 with ZipFile(out) as z:
  assert b'<c r="B1" t="inlineStr"><is><t>NPV</t></is></c>' in z.read('xl/worksheets/sheet1.xml')
 assert receipt['blankedSharedStringIndices']==[0]


def test_exact_error_mask_preserves_other_errors_and_input_values(tmp_path):
 p=tmp_path/'in.xlsx';out=tmp_path/'out.xlsx';make_book(p)
 # Add explicit error-typed result cells in a fresh package, without duplicate ZIP entries.
 with ZipFile(p) as z:entries=[(info,z.read(info.filename)) for info in z.infolist()]
 with ZipFile(p,'w') as z:
  for info,raw in entries:
   if info.filename=='xl/worksheets/sheet1.xml':
    raw=raw.replace(b'</row>',b'<c r="F1" s="9" t="e"><v>#NUM!</v></c><c r="G1" t="e"><v>#N/A</v></c></row>')
   z.writestr(info,raw)
 with ZipFile(p) as z:before=z.read('xl/worksheets/sheet1.xml')
 receipt=mask_formatted_workbook(p,out,{'Model!F1':'#NUM!'},sha256(p))
 with ZipFile(out) as z:
  assert z.read('xl/worksheets/sheet1.xml')==before.replace(b'<c r="F1" s="9" t="e"><v>#NUM!</v></c>',b'<c r="F1" s="9"></c>')
 assert receipt['maskedCells']==['Model!F1']
 assert receipt['blankedSharedStringIndices']==[]
 with pytest.raises(ValueError,match='text mismatch'):
  mask_formatted_workbook(p,tmp_path/'wrong.xlsx',{'Model!F1':'#REF!'},sha256(p))
