"""Source-grounded staffing-unit repair never changes curated task identity."""
import copy
import hashlib
import zipfile
import xml.etree.ElementTree as ET

import openpyxl
import pytest

import xl_output_task as task


@pytest.fixture
def example(tmp_path):
    wb=openpyxl.Workbook();sheet=wb.active;sheet.title='Model'
    sheet['C4']='Shift (hours)'
    sheet['B20']='Equity IRR';sheet['C20']='=IRR(E18:G18)';sheet['D20']='%';sheet['C20'].number_format='0.00%'
    sheet['B21']='MOIC';sheet['C21']='=E19/F19';sheet['D21']='x';sheet['C21'].number_format='0.00'
    entries=[{'band':f'Model!C{row}','sheet':'Model','label':f'{label} / Shift (hours)','name':f'{label} / Shift (hours)','include':True}for row,label in [(20,'Equity IRR'),(21,'MOIC')]]
    source=tmp_path/'sample.xlsx'
    def repair():
        wb.save(source)
        with source.open('rb')as handle:gold=openpyxl.load_workbook(handle,data_only=True)
        try:return task.source_display_repairs(source,entries,gold)
        finally:gold.close()
    return wb,entries,source,repair


def test_original_row_units_replace_proved_stale_header(example):
    wb,entries,source,repair=example;before=copy.deepcopy(entries);proof=repair()
    assert entries==before
    assert proof['Model!C20']['after']=='Equity IRR (%)'
    assert proof['Model!C21']['after']=='MOIC (x)'
    assert proof['Model!C20']['stale_column_header']['reference']=='Model!C4'
    assert proof['Model!C20']['row_label']['reference']=='Model!B20'
    assert proof['Model!C20']['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert 'numeric_value'not in proof['Model!C20']


@pytest.mark.parametrize('fault',['custom_name','period_qualifier','scenario_qualifier','extra_qualifier','different_row_label','missing_unit','different_unit','formula_unit','wrong_format','quoted_percent','escaped_percent','formula_like_literal','different_header','newer_header','non_irr_formula','non_scalar'])
def test_unsupported_display_repair_is_not_inferred(example,fault):
    wb,entries,source,repair=example;s=wb['Model']
    if fault=='custom_name':entries[0]['name']='Custom investor return / Shift (hours)'
    elif fault in ['period_qualifier','scenario_qualifier','extra_qualifier']:
        suffix={'period_qualifier':'FY2025','scenario_qualifier':'Downside','extra_qualifier':'Shift (hours) / Downside'}[fault]
        entries[0]['name']=entries[0]['label']='Equity IRR / '+suffix
    elif fault=='different_row_label':s['B20']='Return'
    elif fault=='missing_unit':s['D20']=None
    elif fault=='different_unit':s['D20']='hours'
    elif fault=='formula_unit':s['D20']='="%"'
    elif fault=='wrong_format':s['C20'].number_format='0.00'
    elif fault=='quoted_percent':s['C20'].number_format='0.00"%"'
    elif fault=='escaped_percent':s['C20'].number_format='0.00\\%'
    elif fault=='formula_like_literal':s['C20'].data_type='s'
    elif fault=='different_header':s['C4']='Tenor'
    elif fault=='newer_header':s['C10']='Forecast'
    elif fault=='non_irr_formula':s['C20']='=SUM(E18:G18)'
    else:entries[0]['band']='Model!C20:D20'
    assert 'Model!C20'not in repair()


def test_number_format_must_confirm_multiple_dimension(example):
    wb,entries,source,repair=example;wb['Model']['C21'].number_format='0.00%'
    assert 'Model!C21'not in repair()


def test_collect_preserves_curation_targets_and_raw_labels(example,tmp_path):
    wb,entries,source,repair=example;repair()
    # Synthetic cached answer values are fixture inputs, never pipeline evidence.
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(source)as z:members={name:z.read(name)for name in z.namelist()}
    xml=ET.fromstring(members['xl/worksheets/sheet1.xml'])
    for cell in xml.findall('.//s:c',ns):
        if cell.attrib['r']in ['C20','C21']:cell.find('s:v',ns).text={'C20':'0.14','C21':'2.3'}[cell.attrib['r']]
    members['xl/worksheets/sheet1.xml']=ET.tostring(xml)
    with zipfile.ZipFile(source,'w')as z:
        for name,content in members.items():z.writestr(name,content)
    masked=tmp_path/'sample-inputs.xlsx';wb['Model']['C20']=None;wb['Model']['C21']=None;wb.save(masked)
    seg=tmp_path/'seg';seg.mkdir();curation=seg/'curation.toml';curation.write_text('\n'.join('[[output]]\n'+ '\n'.join(f'{key} = {value!r}'if isinstance(value,str)else f'{key} = true'for key,value in entry.items())for entry in entries))
    original=curation.read_bytes();outputs,targets=task.collect('sample',tmp_path,tmp_path,tmp_path,seg_dir=seg)
    assert curation.read_bytes()==original
    assert targets=={'Model!C20':0.14,'Model!C21':2.3}
    assert [o['refs']for o in outputs]==[['Model!C20'],['Model!C21']]
    assert [o['raw_label']for o in outputs]==[entry['label']for entry in entries]
    assert [o['name']for o in outputs]==['Equity IRR (%)','MOIC (x)']
    assert all(o['display_name_evidence']['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()for o in outputs)
