"""Optional display metadata cannot remove or relabel the curated cell identity."""
from pathlib import Path
import json
import openpyxl
import pytest
import xl_output_task as task


def fixture(tmp_path, extra='', value=40):
    book=openpyxl.Workbook();book.active.title='5 Year'
    book.active['C7']=value;book.save(tmp_path/'0001.xlsx')
    book.active['C7']=None;book.save(tmp_path/'0001-inputs.xlsx');book.close()
    seg=tmp_path/'seg';seg.mkdir();curation=seg/'curation.toml'
    curation.write_text('[[output]]\nband="5 Year!C7"\nsheet="5 Year"\ninclude=true\n'+extra)
    return seg,curation


@pytest.mark.parametrize('extra,expected', [('', '5 Year!C7'), ('label=""\n','5 Year!C7'),
    ('label="Investor return"\n','Investor return'),
    ('label="Return"\nname="Preserved custom name"\n','Preserved custom name')])
def test_optional_label_falls_back_to_exact_band_without_editing_curation(tmp_path,extra,expected):
    seg,curation=fixture(tmp_path,extra);before=curation.read_bytes()
    outputs,targets=task.collect('0001',tmp_path,tmp_path,tmp_path,seg_dir=seg)
    assert curation.read_bytes()==before
    assert targets=={"'5 Year'!C7":40} and outputs[0]['refs']==["'5 Year'!C7"]
    assert outputs[0]['band']=='5 Year!C7' and outputs[0]['raw_label']==expected
    assert outputs[0]['name']==expected


def test_unlabelled_missing_answer_still_fails_instead_of_dropping_target(tmp_path):
    seg,curation=fixture(tmp_path,value=None);before=curation.read_bytes()
    with pytest.raises(SystemExit,match='resolved to no gradable cells'):
        task.collect('0001',tmp_path,tmp_path,tmp_path,seg_dir=seg)
    assert curation.read_bytes()==before


def test_optional_sheet_is_taken_from_unchanged_qualified_band(tmp_path):
    seg,curation=fixture(tmp_path)
    curation.write_text(curation.read_text().replace('sheet="5 Year"\n',''))
    before=curation.read_bytes()
    outputs,targets=task.collect('0001',tmp_path,tmp_path,tmp_path,seg_dir=seg)
    assert outputs[0]['sheet']=='5 Year' and targets=={"'5 Year'!C7":40}
    assert curation.read_bytes()==before


def test_conflicting_optional_sheet_fails_before_packaging(tmp_path):
    seg,curation=fixture(tmp_path)
    curation.write_text(curation.read_text().replace('sheet="5 Year"','sheet="Other"'))
    with pytest.raises(SystemExit,match='sheet differs'):
        task.collect('0001',tmp_path,tmp_path,tmp_path,seg_dir=seg)
