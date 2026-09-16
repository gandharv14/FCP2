import pytest
from xml.etree import ElementTree as E
import xl_volatile_formula_remediation as M
from test_dead_external_package_remediation import members, write

RULE='unreferenced-global-external-print-area/v1'


def workbook(formula='[5]Model!$D$204:$X$251', local=None, dependency=None):
    root=E.Element('workbook');names=E.SubElement(root,'definedNames')
    attrs={'name':'_xlnm.Print_Area'}
    if local is not None:attrs['localSheetId']=local
    E.SubElement(names,'definedName',attrs).text=formula
    E.SubElement(names,'definedName',{'name':'_xlnm.Print_Area','localSheetId':'0'}).text='Model!$A$1:$B$2'
    if dependency:E.SubElement(names,'definedName',{'name':'Dependent'}).text=dependency
    return E.tostring(root)


def test_only_unreferenced_global_rectangle_is_removed():
    actions, unresolved=M._defined_name_plan(workbook(),[])
    assert [(a['index'],a['rule_id'])for a in actions]==[(0,RULE)]
    assert unresolved==[]


@pytest.mark.parametrize('formula,local,uses,dependency',[
    ('[5]Model!$D$204:$X$251','0',[],None),
    ('[5]Model!$D$204:$X$251',None,['_xlnm.Print_Area'],None),
    ('[5]Model!$D$204:$X$251',None,[], '_xlnm.Print_Area'),
    ('[5]Model!$D$204:$X$251',None,['INDIRECT(A1)'],None),
    ('[5]Model!$D$204:$X$251',None,[], 'EVALUATE(A1)'),
    ('[5]Model!$D$204:$X$251',None,['OFFSET(A1,0,0)'],None),
    ('[5]Model!$D$204:$X$251',None,['GET.CELL(1,A1)'],None),
    ('Model!$D$204:$X$251',None,[],None),
    ('[5]Model!$D$204:$X$251+1',None,[],None),
    ('[5]Model!$D$204:$XFE$251',None,[],None),
    ('[5]Model!$D$204:$X$1048577',None,[],None),
])
def test_local_live_dynamic_and_nonliteral_settings_remain(formula,local,uses,dependency):
    actions,_=M._defined_name_plan(workbook(formula,local,dependency),uses)
    assert not any(a['rule_id']==RULE for a in actions)


def test_package_proof_preserves_source_cells_and_local_print_area(tmp_path):
    parts=members();book=parts['xl/workbook.xml']
    book=book.replace(b'</definedNames>',b'<definedName name="_xlnm.Print_Area">[1]Other!$A$1:$B$2</definedName><definedName name="_xlnm.Print_Area" localSheetId="0">Model!$A$1:$B$2</definedName></definedNames>')
    parts['xl/workbook.xml']=book;source=tmp_path/'source.xlsx';candidate=tmp_path/'candidate.xlsx';write(source,parts);before=M.sha256_file(source)
    plan=M.build_plan(source);assert plan['status']=='eligible';assert plan['action_counts'][RULE]==1
    manifest=M.apply_plan(source,plan,candidate)
    assert M.verify_manifest(source,candidate,plan,manifest)['status']=='verified'
    assert M.sha256_file(source)==before
    import zipfile
    with zipfile.ZipFile(candidate)as z:
        assert z.read('xl/worksheets/sheet1.xml')==parts['xl/worksheets/sheet1.xml']
        assert b'localSheetId="0">Model!$A$1:$B$2' in z.read('xl/workbook.xml')
        assert not any(p.startswith('xl/externalLinks/') for p in z.namelist())
