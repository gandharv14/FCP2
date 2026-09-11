from pathlib import Path
import copy,hashlib,importlib.util,json,shutil,sys,zipfile
from xml.etree import ElementTree as ET
import pytest
import openpyxl
from openpyxl.worksheet.formula import DataTableFormula

from scripts.recalc import excel_runner as runner
import xl_source_recalc as recalc
GOLD = Path(__file__).with_name("fixtures") / "table-recalc-observation"
RUNNER_PATH = Path(runner.__file__).resolve()
NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def edit(path,func):
    with zipfile.ZipFile(path) as z:infos=z.infolist();parts={i.filename:z.read(i.filename) for i in infos}
    tree=ET.fromstring(parts['xl/worksheets/sheet1.xml']);func(tree,parts)
    parts['xl/worksheets/sheet1.xml']=ET.tostring(tree)
    with zipfile.ZipFile(path,'w') as z:
        for info in infos:z.writestr(info,parts.pop(info.filename))
        for name,value in parts.items():z.writestr(name,value)

def cells(tree):return {c.get('r'):c for c in tree.findall('.//{*}sheetData/{*}row/{*}c')}

def set_value(cell,value,kind='n'):
    for node in list(cell):
        if node.tag in {NS+'v',NS+'is'}:cell.remove(node)
    cell.set('t',kind)
    if kind=='inlineStr':
        node=ET.SubElement(ET.SubElement(cell,NS+'is'),NS+'t');node.text=value
    else:ET.SubElement(cell,NS+'v').text=value

def fixture(path,table=True):
    book=openpyxl.Workbook();sheet=book.active;sheet.title="3 O'Brien"
    sheet['A1']=2;sheet['A2']='retained';sheet['B1']='=A1*3';sheet['B1'].number_format='0.00%'
    sheet['D3']=DataTableFormula(ref='D3:E4',r1='A1') if table else 5
    sheet['E3']=9;sheet['D4']=10;sheet['E4']=11
    sheet['D4'].number_format='0.0x';book.calculation.iterate=True;book.calculation.iterateCount=100;book.calculation.iterateDelta=.001
    book.save(path);book.close()
    def update(tree,parts):
        c=cells(tree);set_value(c['B1'],'6')
        if table:set_value(c['D3'],'5')
    edit(path,update)
    return path

def staged(source,path):shutil.copyfile(source,path);return path

def observation(path):return runner.observe_data_tables(path)

def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complete_graft_and_snapshot_classification_preserve_source_inputs_and_conventions(tmp_path):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx')
    edit(engine,lambda t,p:[set_value(cells(t)[c],v) for c,v in {'B1':'7','D3':'15','E3':'19','D4':'20','E4':'21'}.items()])
    before=observation(source);observed=observation(engine);candidate=staged(source,tmp_path/'candidate.xlsx')
    runner.graft_observed_data_tables(engine,candidate)
    after=observation(candidate)
    assert after['results_sha256']==observed['results_sha256']!=before['results_sha256']
    assert before['primitive_inputs_sha256']==after['primitive_inputs_sha256']
    assert before['geometry_sha256']==after['geometry_sha256']
    assert before['formula_identity_sha256']==after['formula_identity_sha256']
    snap1=recalc.semantic_workbook_snapshot(source);snap2=recalc.semantic_workbook_snapshot(candidate)
    assert snap1['semantics']==snap2['semantics']
    for coordinate in ('E3','D4','E4'):
        assert coordinate not in snap1['semantics']['inputs']["3 O'Brien"]
        assert snap1['formula_caches']["3 O'Brien"][coordinate]['type']=='dataTableResult'
    diff=recalc.semantic_workbook_diff(source,candidate)
    assert diff['equivalent_semantics'] is True
    assert diff['supported_equivalent_semantics'] is False
    assert diff['formula_cache_changes']==5
    with zipfile.ZipFile(source) as a,zipfile.ZipFile(candidate) as b:
        for name in a.namelist():
            if not name.startswith('xl/worksheets/'):
                assert a.read(name)==b.read(name)


@pytest.mark.parametrize('kind,value,expected', [('n','1.00',['number',[0,[1],0]]),('b','1',['boolean',True]),('e','#NUM!',['error','#NUM!']),('inlineStr','  supplied result  ',['string','  supplied result  ']),('str','',['string',''])])
def test_typed_results_are_exact_and_copied_from_staging(tmp_path,kind,value,expected):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx')
    edit(engine,lambda t,p:set_value(cells(t)['E3'],value,kind))
    result=observation(engine)
    assert result['tables'][0]['results'][1]['value']==expected
    candidate=staged(source,tmp_path/'candidate.xlsx');runner.graft_observed_data_tables(engine,candidate)
    assert observation(candidate)['results_sha256']==result['results_sha256']


def test_shared_string_indices_are_resolved_against_staging(tmp_path):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx')
    def change(t,p):
        set_value(cells(t)['E3'],'0','s')
        p['xl/sharedStrings.xml']=f'<sst xmlns="{NS[1:-1]}"><si><t>calculated string</t></si></sst>'.encode()
    edit(engine,change)
    candidate=staged(source,tmp_path/'candidate.xlsx');runner.graft_observed_data_tables(engine,candidate)
    assert observation(candidate)['results_sha256']==observation(engine)['results_sha256']
    with zipfile.ZipFile(candidate) as z:assert 'xl/sharedStrings.xml' not in z.namelist()


@pytest.mark.parametrize('ref',['D3','E4:D3','D3:XFD1048576','D3:XFE4','Other!D3:E4','D3:E4,D6:E7'])
def test_invalid_or_unbounded_geometry_rejects_before_result_enumeration(tmp_path,monkeypatch,ref):
    source=fixture(tmp_path/'source.xlsx')
    edit(source,lambda t,p:cells(t)['D3'].find('{*}f').set('ref',ref))
    calls=[];real=runner._observed_typed_value
    def observe(*a,**kw):calls.append(1);return real(*a,**kw)
    monkeypatch.setattr(runner,'_observed_typed_value',observe)
    with pytest.raises(runner.RunnerError):observation(source)
    assert calls==[]


@pytest.mark.parametrize('change',['missing_cell','missing_value','duplicate_cell','duplicate_value','body_formula','overlap','unknown_attribute','deleted_input','input_inside','invalid_bool','nan','changed_input','changed_formula','changed_geometry'])
def test_incomplete_ambiguous_or_changed_evidence_rejects_without_mutating_candidate(tmp_path,change):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx')
    def alter(t,p):
        c=cells(t);body=c['E3'];anchor=c['D3'].find('{*}f')
        row=next(r for r in t.findall('.//{*}row') if body in list(r))
        if change=='missing_cell':row.remove(body)
        elif change=='missing_value':body.remove(body.find('{*}v'))
        elif change=='duplicate_cell':row.append(copy.deepcopy(body))
        elif change=='duplicate_value':body.append(copy.deepcopy(body.find('{*}v')))
        elif change=='body_formula':ET.SubElement(body,NS+'f').text='A1'
        elif change=='overlap':ET.SubElement(body,NS+'f',{'t':'dataTable','ref':'E3:E4','r1':'A1'})
        elif change=='unknown_attribute':anchor.set('unknown','1')
        elif change=='deleted_input':anchor.set('del1','1')
        elif change=='input_inside':anchor.set('r1','E3')
        elif change=='invalid_bool':set_value(body,'2','b')
        elif change=='nan':set_value(body,'NaN')
        elif change=='changed_input':set_value(c['A1'],'3')
        elif change=='changed_formula':c['B1'].find('{*}f').text='A1*4'
        elif change=='changed_geometry':anchor.set('ref','D3:E3')
    edit(engine,alter);candidate=staged(source,tmp_path/'candidate.xlsx');original=hashfile(candidate)
    with pytest.raises(runner.RunnerError):runner.graft_observed_data_tables(engine,candidate)
    assert hashfile(candidate)==original


def test_nontable_snapshot_and_graft_are_byte_exact_backward_compatible(tmp_path):
    import io
    source=fixture(tmp_path/'source.xlsx',table=False);engine=staged(source,tmp_path/'staged.xlsx')
    edit(engine,lambda t,p:set_value(cells(t)['B1'],'7'))
    frozen=json.loads((GOLD/'nontable-snapshots.json').read_text())
    for kind,path in [('source',source),('staged',engine)]:
        expected=copy.deepcopy(frozen[kind]);expected['source_sha256']=hashfile(path)
        assert json.loads(json.dumps(recalc.semantic_workbook_snapshot(path)))==expected
    candidate=staged(source,tmp_path/'new.xlsx')
    runner._replace_candidate_from_staging(engine,candidate)
    # Frozen legacy worksheet output, serialized using the process's namespace
    # registration just as the legacy serializer did. Every other member and
    # every ZipInfo field comes from the exact original archive.
    legacy=ET.tostring(ET.fromstring((GOLD/'nontable-graft-sheet1.xml').read_bytes()),encoding='utf-8',xml_declaration=True)
    expected=io.BytesIO()
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(expected,'w') as rebuilt:
        for info in original.infolist():
            rebuilt.writestr(info,legacy if info.filename=='xl/worksheets/sheet1.xml' else original.read(info.filename))
    assert candidate.read_bytes()==expected.getvalue()


def test_policy_still_rejects_tables_before_any_engine_call(tmp_path):
    source=fixture(tmp_path/'source.xlsx');out=tmp_path/'out/result.xlsx'
    request=recalc.create_recalc_request(source,out,allowed_root=out.parent)
    class Engine:
        name='excel-macos';authoritative=True
        def execute(self,*a):raise AssertionError('must not run Excel')
    with pytest.raises(recalc.RecalculationError,match='data_tables_present'):
        recalc.execute_recalc(request,Engine(),allowed_root=out.parent)
    assert not out.exists()


@pytest.mark.parametrize('collision',['array','shared','merge'])
def test_implicit_formula_followers_and_merges_cannot_be_table_results(tmp_path,collision):
    source=fixture(tmp_path/'source.xlsx')
    def change(t,p):
        if collision=='merge':ET.SubElement(ET.SubElement(t,NS+'mergeCells'),NS+'mergeCell',{'ref':'C3:E3'})
        else:
            formula=cells(t)['B1'].find('{*}f');formula.set('t',collision);formula.set('ref','B1:E3')
    edit(source,change)
    with pytest.raises(runner.RunnerError,match='collides'):observation(source)


def test_profile_rejects_changed_worksheet_identity(tmp_path):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx')
    def change(t,p):
        book=ET.fromstring(p['xl/workbook.xml']);book.find('{*}sheets/{*}sheet').set('name','Another sheet');p['xl/workbook.xml']=ET.tostring(book)
    edit(engine,change)
    with pytest.raises(runner.RunnerError,match='geometry'):
        runner.graft_observed_data_tables(engine,source)


def test_observation_hashes_ignore_equivalent_numeric_serialization_and_cell_order(tmp_path):
    source=fixture(tmp_path/'source.xlsx');other=staged(source,tmp_path/'other.xlsx')
    def change(t,p):
        set_value(cells(t)['D3'],'5.000E0');set_value(cells(t)['A1'],'2.000')
        for row in t.findall('.//{*}row'):row[:]=reversed(list(row))
    edit(other,change)
    a=observation(source);b=observation(other)
    assert a['source_sha256']!=b['source_sha256']
    for key in ('geometry_sha256','results_sha256','formula_identity_sha256','primitive_inputs_sha256'):assert a[key]==b[key]


@pytest.mark.parametrize('mode',['changed_row','duplicate_row','extra_cell','missing_anchor','missing_input'])
def test_unbound_or_missing_cell_geometry_rejects(tmp_path,mode):
    source=fixture(tmp_path/'source.xlsx')
    def change(t,p):
        row=t.find('{*}sheetData/{*}row')
        if mode=='changed_row':row.set('r','99')
        elif mode=='duplicate_row':t.find('{*}sheetData').append(copy.deepcopy(row))
        elif mode=='extra_cell':ET.SubElement(t,NS+'c',{'r':'Z1'})
        elif mode=='missing_anchor':cells(t)['D3'].remove(cells(t)['D3'].find('{*}f'))
        else:row.remove(cells(t)['A1'])
    edit(source,change)
    if mode=='missing_anchor':
        # Alone this is a nontable file; paired against the original its missing
        # table is rejected, rather than claiming a complete empty observation.
        original=fixture(tmp_path/'original.xlsx')
        with pytest.raises(runner.RunnerError):runner.graft_observed_data_tables(source,original)
    else:
        with pytest.raises(runner.RunnerError):observation(source)


def test_completed_calculate_cell_flag_preserves_original_formula_and_observation(tmp_path):
    source=fixture(tmp_path/'source.xlsx');edit(source,lambda t,p:cells(t)['D3'].find('{*}f').set('ca','1'))
    engine=staged(source,tmp_path/'staged.xlsx');edit(engine,lambda t,p:cells(t)['D3'].find('{*}f').attrib.pop('ca'))
    runner.graft_observed_data_tables(engine,source)
    with zipfile.ZipFile(source) as z:
        t=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));assert cells(t)['D3'].find('{*}f').get('ca')=='1'


def test_grafting_preserves_cell_child_order_and_other_metadata(tmp_path):
    source=fixture(tmp_path/'source.xlsx')
    edit(source,lambda t,p:ET.SubElement(cells(t)['E3'],NS+'extLst'))
    engine=staged(source,tmp_path/'staged.xlsx');edit(engine,lambda t,p:set_value(cells(t)['E3'],'20'))
    runner.graft_observed_data_tables(engine,source)
    with zipfile.ZipFile(source) as z:
        t=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        assert [c.tag for c in cells(t)['E3']]==[NS+'v',NS+'extLst']


def test_ordinary_runner_path_rejects_tables_without_authorizing_a_receipt(tmp_path):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx');before=hashfile(source)
    with pytest.raises(runner.RunnerError,match='separately authorized producer profile'):
        runner._replace_candidate_from_staging(engine,source)
    assert hashfile(source)==before


def test_observation_runs_in_separate_system_python_without_workspace_imports(tmp_path):
    import subprocess
    interpreter=Path('/usr/bin/python3')
    if not interpreter.is_file():pytest.skip('system Python unavailable')
    source=fixture(tmp_path/'source.xlsx')
    code='import runpy,json,pathlib; m=runpy.run_path('+repr(str(RUNNER_PATH))+'); print(json.dumps(m["observe_data_tables"](pathlib.Path('+repr(str(source))+'))))'
    result=subprocess.run([str(interpreter),'-I','-c',code],check=True,capture_output=True,text=True,timeout=10)
    assert json.loads(result.stdout)==observation(source)


def test_staging_mutation_after_observation_rejects_atomically(tmp_path,monkeypatch):
    source=fixture(tmp_path/'source.xlsx');engine=staged(source,tmp_path/'staged.xlsx');before=hashfile(source)
    real=runner.observe_data_tables
    def observe(path):
        result=real(path)
        if str(path).endswith('.excel-output.xlsx'):
            # Different bytes, even with identical interpreted result values,
            # must not silently replace the hash-bound staging observation.
            edit(engine,lambda t,p:set_value(cells(t)['E3'],'9.000'))
        return result
    monkeypatch.setattr(runner,'observe_data_tables',observe)
    with pytest.raises(runner.RunnerError,match='staging changed'):
        runner.graft_observed_data_tables(engine,source)
    assert hashfile(source)==before


@pytest.mark.parametrize('overlap',[False,True])
def test_multiple_table_rectangles_are_disjoint_before_any_approval(tmp_path,overlap):
    source=fixture(tmp_path/'source.xlsx')
    def change(t,p):
        data=t.find('{*}sheetData');start=4 if overlap else 6
        for row_number in (start,start+1):
            row=next((r for r in data.findall('{*}row') if r.get('r')==str(row_number)),None)
            if row is None:row=ET.SubElement(data,NS+'row',{'r':str(row_number)})
            for column in ('C','D'):
                coord=f'{column}{row_number}'
                if coord in cells(t):continue
                cell=ET.SubElement(row,NS+'c',{'r':coord});set_value(cell,'22')
        anchor=cells(t)[f'C{start}']
        anchor.insert(0,ET.Element(NS+'f',{'t':'dataTable','ref':f'C{start}:D{start+1}','dt2D':'1','r1':'A1','r2':'A2'}))
    edit(source,change)
    if overlap:
        with pytest.raises(runner.RunnerError,match='overlapping'):observation(source)
    else:
        result=observation(source);assert len(result['tables'])==2
        assert sum(len(t['results']) for t in result['tables'])==8


def test_nontable_paths_do_not_invoke_new_observation_profile(tmp_path,monkeypatch):
    source=fixture(tmp_path/'source.xlsx',table=False);engine=staged(source,tmp_path/'staged.xlsx')
    def forbidden(*a,**kw):raise AssertionError('non-table path invoked table observer')
    monkeypatch.setattr(runner,'observe_data_tables',forbidden)
    recalc.semantic_workbook_snapshot(source)
    runner._replace_candidate_from_staging(engine,source)


@pytest.mark.parametrize('child',['rPh','r'])
def test_nonplain_result_string_encoding_fails_closed(tmp_path,child):
    source=fixture(tmp_path/'source.xlsx')
    def change(t,p):
        cell=cells(t)['E3'];set_value(cell,'','inlineStr');inline=cell.find('{*}is');inline.clear()
        ET.SubElement(ET.SubElement(inline,NS+child),NS+'t').text='unsupported result encoding'
    edit(source,change)
    with pytest.raises(runner.RunnerError):observation(source)
