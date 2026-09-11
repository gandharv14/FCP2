from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import pytest
from openpyxl import Workbook
from xl_seg.evaluate import CachedEmptyString, Unresolved, compare, workbook_expected_cache

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

def fixture(tmp_path, kind='str', present=True, formula=True, text=None, duplicate=False):
    path=tmp_path/'book.xlsx'
    book=Workbook(); book.active.title="1 O'Brien"
    book.active['A1']='=""'; book.active['B1']=17
    book.create_sheet('Other')['A1']=23
    book.save(path)
    with ZipFile(path) as source:
        parts={n:source.read(n) for n in source.namelist()}
    root=ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    cell=root.find(f'.//{{{NS}}}c[@r="A1"]'); cell.set('t',kind)
    for v in list(cell.findall(f'{{{NS}}}v')):cell.remove(v)
    if present:
        ET.SubElement(cell,f'{{{NS}}}v').text=text
        if duplicate:ET.SubElement(cell,f'{{{NS}}}v')
    if not formula:cell.remove(cell.find(f'{{{NS}}}f'))
    parts['xl/worksheets/sheet1.xml']=ET.tostring(root)
    with ZipFile(path,'w') as dest:
        for n,v in parts.items():dest.writestr(n,v)
    return path

def test_explicit_empty_cached_formula(tmp_path):
    cache=workbook_expected_cache(fixture(tmp_path))
    try:
        value=cache("1 O'Brien",1,1)
        assert isinstance(value,CachedEmptyString)
        assert compare(value,'')==('match',None)
        assert cache.reads=={"1 O'Brien!A1":1}
        assert cache("Other",1,1)==23
        assert cache("1 O'Brien",1,2)==17
    finally:cache.close()

@pytest.mark.parametrize('actual',[None,0,False,1,' ','value'])
def test_explicit_empty_never_matches_other_types_or_text(tmp_path,actual):
    cache=workbook_expected_cache(fixture(tmp_path))
    try:assert compare(cache("1 O'Brien",1,1),actual)==('mismatch',None)
    finally:cache.close()

def test_unresolved_stays_unresolved(tmp_path):
    cache=workbook_expected_cache(fixture(tmp_path))
    try:assert compare(cache("1 O'Brien",1,1),Unresolved('missing-dependency'))==('unresolved',None)
    finally:cache.close()

@pytest.mark.parametrize('options',[{'present':False},{'kind':'n'},{'kind':'b'}, {'formula':False},{'duplicate':True}])
@pytest.mark.parametrize('actual',['',None,0,False])
def test_no_authoritative_empty_string_cache_remains_unverifiable(tmp_path,options,actual):
    cache=workbook_expected_cache(fixture(tmp_path,**options))
    try:
        value=cache("1 O'Brien",1,1)
        assert not isinstance(value,CachedEmptyString)
        assert compare(value,actual)==('unverifiable',None)
    finally:cache.close()

@pytest.mark.parametrize('raw',[None,''])
@pytest.mark.parametrize('actual',['',None,0,False])
def test_untyped_missing_cache_not_upgraded(raw,actual):
    assert compare(raw,actual)==('unverifiable',None)

def test_altered_cache_content_is_not_empty_evidence(tmp_path):
    cache=workbook_expected_cache(fixture(tmp_path,text='changed'))
    try:assert compare(cache("1 O'Brien",1,1),'')==('mismatch',None)
    finally:cache.close()

def test_missing_sheet_and_cell(tmp_path):
    cache=workbook_expected_cache(fixture(tmp_path))
    try:
        assert compare(cache('Missing',1,1),'')==('unverifiable',None)
        assert compare(cache("1 O'Brien",3,3),'')==('unverifiable',None)
    finally:cache.close()

def test_ordinary_comparison_unchanged():
    assert compare(17,17)==('match',0.0)
    assert compare(17,18)[0]=='mismatch'
    assert compare('text','text')==('match',None)

@pytest.mark.parametrize('change',['duplicate_coordinate','duplicate_relationship','external_relationship'])
def test_ambiguous_cache_identity_rejected(tmp_path,change):
    from copy import deepcopy
    from xl_seg.evaluate import _empty_string_formula_caches
    path=fixture(tmp_path)
    with ZipFile(path) as source:parts={n:source.read(n) for n in source.namelist()}
    part='xl/worksheets/sheet1.xml' if change=='duplicate_coordinate' else 'xl/_rels/workbook.xml.rels'
    root=ET.fromstring(parts[part])
    if change=='duplicate_coordinate':
        row=root.find(f'.//{{{NS}}}row');row.append(deepcopy(row.find(f'{{{NS}}}c')))
    elif change=='duplicate_relationship':root.append(deepcopy(root[0]))
    else:root[0].set('TargetMode','External')
    parts[part]=ET.tostring(root)
    with ZipFile(path,'w') as dest:
        for n,v in parts.items():dest.writestr(n,v)
    with pytest.raises(ValueError):_empty_string_formula_caches(path)
