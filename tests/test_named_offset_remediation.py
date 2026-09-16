from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile,copy,re
import openpyxl,pytest
from xl_volatile_formula_remediation import _package,_scalar_named_rows,_rewrite_offset,build_plan,apply_plan,verify_manifest


def workbook(tmp,names,formula='OFFSET(A1,CASE,)',case=1):
    path=tmp/'source.xlsx';b=openpyxl.Workbook();m=b.active;m.title='Model';i=b.create_sheet('Inputs');i['B1']=case;i['B2']=2
    m['A1']=10;m['A2']=20;m['A3']=30;m['C1']='='+formula;b.save(path)
    with zipfile.ZipFile(path)as z:parts={n:z.read(n)for n in z.namelist()}
    items=''.join('<definedName name="'+name+'"'+(' localSheetId="'+str(scope)+'"'if scope is not None else'')+'>'+text+'</definedName>'for name,scope,text in names)
    parts['xl/workbook.xml']=re.sub(rb'<definedNames\s*/>',lambda m:('<definedNames>'+items+'</definedNames>').encode(),parts['xl/workbook.xml'])
    assert items.encode()in parts['xl/workbook.xml']
    with zipfile.ZipFile(path,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    return path,parts


def test_local_name_overrides_global_and_only_selected_scope_is_used(tmp_path):
    path,_=workbook(tmp_path,[('CASE',None,'Inputs!$B$1'),('case',0,'Inputs!$B$2')]);members=_package(path)[1]
    local=_scalar_named_rows(members,'Model')['case'];global_=_scalar_named_rows(members,'Inputs')['case']
    assert local['local_sheet_id']=='0'and local['cell']=='$B$2'
    assert global_['local_sheet_id']is None and global_['cell']=='$B$1'

@pytest.mark.parametrize('names',[
 [('CASE',None,'Inputs!$B$1'),('case',None,'Inputs!$B$2')],
 [('CASE',None,'Inputs!$B$1'),('case',0,'SUM(Inputs!B1:B2)')],
 [('CASE',None,'Inputs!$B$1'),('case',0,'Inputs!$B$1'),('CASE',0,'Inputs!$B$2')],
 [('CASE',1,'Inputs!$B$1')],
 [('CASE',None,'[1]Inputs!$B$1')],
 [('CASE',None,"'[1]Inputs'!$B$1")],
 [('CASE',None,'Inputs!$B$1:$B$2')],
 [('CASE',None,'Inputs!$B$1,Inputs!$B$2')],
 [('CASE',None,'OFFSET(Inputs!$B$1,0,0)')],
 [('CASE',None,'3')],
 [('CASE',None,'Missing!$B$1')],
 [('CASE',None,'Inputs!XFE1')],
 [('CASE',None,'Inputs!A1048577')],
])
def test_ambiguous_foreign_non_scalar_and_formula_names_stay_blocked(tmp_path,names):
    path,_=workbook(tmp_path,names);assert 'case'not in _scalar_named_rows(_package(path)[1],'Model')
    assert build_plan(path)['status']=='ineligible'

@pytest.mark.parametrize('formula',['MYOFFSET(A1,CASE,)','"OFFSET(A1,CASE,)"','OFFSET(A1,UNKNOWN,)','OFFSET(A1,CASE,0)','OFFSET(A1,CASE,,2)','OFFSET(A1:A2,CASE,)'])
def test_only_the_exact_existing_scalar_blank_column_rule_is_extended(formula):
    assert _rewrite_offset(formula,named_rows={'case':{}})==(formula,0)


def test_dynamic_name_dependency_and_all_original_values_survive_package_rewrite(tmp_path):
    path,parts=workbook(tmp_path,[('CASE',None,"'Inputs'!$B$1")]);original=path.read_bytes();plan=build_plan(path);assert plan['status']=='eligible'
    action=next(a for a in plan['actions']if a['kind']=='replace_formula')
    assert action['after_formula']=='INDEX(A:A,ROW(A1)+(CASE))'
    assert action['named_row_operands']['case']==dict(name='CASE',local_sheet_id=None,definition="'Inputs'!$B$1",sheet='Inputs',cell='$B$1')
    target=tmp_path/'candidate.xlsx';manifest=apply_plan(path,plan,target);assert verify_manifest(path,target,plan,manifest)['status']=='verified'
    assert path.read_bytes()==original
    with zipfile.ZipFile(target)as z:
        assert z.read('xl/workbook.xml')==parts['xl/workbook.xml']
        assert z.read('xl/worksheets/sheet2.xml')==parts['xl/worksheets/sheet2.xml']
        assert b'CASE'in z.read('xl/worksheets/sheet1.xml')
    # A changed source selector is still a live operand, never frozen to either value.
    other=tmp_path/'other';other.mkdir();changed,_=workbook(other,[('CASE',None,"'Inputs'!$B$1")],case=2)
    a=next(a for a in build_plan(changed)['actions']if a['kind']=='replace_formula')
    assert a['after_formula']==action['after_formula']and a['named_row_operands']==action['named_row_operands']


@pytest.mark.parametrize('formula',['OFFSET(XFE1,CASE,)','OFFSET(A1048577,CASE,)'])
def test_named_row_cannot_repair_an_invalid_original_base(formula):
    assert _rewrite_offset(formula,named_rows={'case':{}})==(formula,0)


def test_named_shared_copy_preserves_original_grid_boundary(tmp_path):
    path,parts=workbook(tmp_path,[('CASE',None,'Inputs!$B$1')],formula='OFFSET(XFD1,CASE,)')
    xml=parts['xl/worksheets/sheet1.xml'].decode()
    xml=re.sub(r'<f>OFFSET\(XFD1,CASE,\)</f>', '<f t="shared" si="0" ref="C1:D1">OFFSET(XFD1,CASE,)</f>',xml)
    xml=xml.replace('</row>', '<c r="D1"><f t="shared" si="0"/><v>0</v></c></row>',1)
    parts['xl/worksheets/sheet1.xml']=xml.encode()
    with zipfile.ZipFile(path,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    plan=build_plan(path);assert plan['status']=='ineligible'
    assert any(x['kind']=='offset_shared_boundary_unresolved'and x['location']=='Model!D1'for x in plan['unresolved'])
