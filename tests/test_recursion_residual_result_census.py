from scripts.recursion_residual_result_census import candidate,MIXED,STATUS

def test_nonnumeric_and_error_halves_are_not_missed():
 for value in ['NM/0.8x','N/A / 12.5%','#DIV/0! / 1.2x','— /0.7x','MOIC / 1.5x','7.1% /1.17x']:
  assert candidate(value) and MIXED.search(value)

def test_plain_labels_and_dates_are_not_composite_results():
 for value in ['12/31/2025','Price / bbl','Revenue ($mm)','IRR / MOIC','1 / 2','NM scenario']:
  assert not candidate(value)

def test_status_screen_is_exact_and_not_a_disposition():
 assert STATUS.fullmatch('NM') and STATUS.fullmatch('#N/A')
 assert not STATUS.fullmatch('NM input assumption')
 # candidate() deliberately detects text only; source proof must determine removal.
 assert candidate('N/A') and not candidate(1.2)

def test_native_and_converted_table_scopes_are_explicit(tmp_path):
 from zipfile import ZipFile
 from scripts.recursion_residual_result_census import all_source_tables,TargetSource
 p=tmp_path/'source.xlsx'
 with ZipFile(p,'w') as z:
  z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Model" sheetId="1" r:id="rId1"/></sheets></workbook>')
  z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
  z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="2"><c r="D2"><f t="dataTable" ref="D2:E3"/><v>3.5</v></c><c r="G2" t="str"><f>TABLE($C$1,$A$1,$F2,$B$1,G$1)</f><v>NM/0.8x</v></c><c r="H2" t="b"><v>1</v></c></row></sheetData></worksheet>')
 tables=all_source_tables(p)
 assert {r['ref'] for r in tables['Model']}=={'D2:E3','G2'}
 source=TargetSource(p,{'Model!D2','Model!G2','Model!H2'})
 assert source.cells['Model!D2']==3.5 and source.cells['Model!G2']=='NM/0.8x'

 # Disposition matches against the complete scope map, not TargetSource's auxiliary XML-only map.
 from scripts.recursion_hardening_workbooks import in_ranges
 assert any(in_ranges('G2',[t['ref']]) for t in tables['Model'])
 assert not any(in_ranges('F2',[t['ref']]) for t in tables['Model'])
 assert source.cells['Model!H2'] is True

def test_generic_pairs_and_dates_have_distinct_dispositions():
 from scripts.recursion_residual_generic_census import generic,date_text
 for v in ['0.80 / 158','($0.00) / (0.8%)','1.2 | 3.4','1 / 2']:
  assert generic(v) and not date_text(v)
 assert generic('12/31/2025') and date_text('12/31/2025')
 assert not date_text('99/99/9999')
 assert not generic('IRR / MOIC')
