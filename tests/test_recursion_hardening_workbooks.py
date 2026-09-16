import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.recursion_hardening_workbooks import mask_workbook, mask_xml


XML = b'''<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" s="4" t="n"><v>123.25</v></c><c r="B1" t="inlineStr"><is><t>Preserve label</t></is></c><c r="C1" t="e"><v>#N/A</v></c><c r="D1"><v>123.25</v></c><c r="E1" s="8"/></row><row r="2"><c r="A2"><f>D1*2</f><v>246.5</v></c></row></sheetData><extLst><ext uri="keep-me"/></extLst></worksheet>'''


class WorkbookMaskTests(unittest.TestCase):
    def test_only_numeric_values_in_audited_scope_change(self):
        patched, changes = mask_xml(XML, ["A1:C1", "E1"])
        self.assertEqual(patched, XML.replace(b'<v>123.25</v>', b'', 1))
        self.assertEqual([c['cell'] for c in changes], ['A1'])
        self.assertIn(b'<c r="D1"><v>123.25</v></c>', patched)  # Identical input is kept.
        self.assertEqual(mask_xml(patched, ["A1:C1"]), (patched, []))

    def test_formula_in_scope_requires_adjudication(self):
        with self.assertRaisesRegex(ValueError, "still contains a formula"):
            mask_xml(XML, ["A2"])

    def test_empty_cell_before_numeric_cell_does_not_consume_it(self):
        fixture = XML.replace(b'<c r="A1"', b'<c r="Z9" s="2"/><c r="A1"')
        patched, changes = mask_xml(fixture, ["A1"])
        self.assertEqual([x['cell'] for x in changes], ['A1'])
        self.assertEqual(patched, fixture.replace(b'<v>123.25</v>', b'', 1))

    def test_reject_reversed_and_invalid_ranges(self):
        for bad in ["C1:A1", "A0", "A1:B2:C3"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                mask_xml(XML, [bad])

    def test_native_parts_and_all_unrelated_bytes_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder)/'source.xlsx', Path(folder)/'masked.xlsx'
            with ZipFile(source, 'w') as z:
                z.comment = b'preserve archive comment'
                z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Trailing space " sheetId="1" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml', XML)
                z.writestr('xl/vbaProject.bin', bytes(range(256)))
                z.writestr('customXml/item1.xml', b'<privatePart preserve="yes"/>')
            before = source.read_bytes()
            result = mask_workbook(source, destination, {'Trailing space ': ['A1:C1']})
            self.assertEqual(before, source.read_bytes())
            self.assertEqual(result['changed_cell_count'], 1)
            self.assertEqual(result['changed_entries'], ['xl/worksheets/sheet1.xml'])
            with ZipFile(source) as old, ZipFile(destination) as new:
                self.assertEqual(old.comment, new.comment)
                for name in old.namelist():
                    if name != 'xl/worksheets/sheet1.xml':
                        self.assertEqual(old.read(name), new.read(name))
            with self.assertRaisesRegex(ValueError, "never be edited"):
                mask_workbook(source, source, {'Trailing space ': ['A1']})
            with self.assertRaisesRegex(ValueError, "hash differs"):
                mask_workbook(source, destination, {'Trailing space ': ['A1']}, 'wrong')


if __name__ == '__main__':
    unittest.main()
