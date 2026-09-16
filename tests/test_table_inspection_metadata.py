import copy,zipfile
from pathlib import Path
import pytest,openpyxl
from test_table_remediation_composition import fixture,seal
from xl_presentation_tables import materialize,verify_materialization,inspection_metadata,verify_inspection_source


def broken(tmp,**kwargs):
    source,curation=fixture(tmp,**kwargs)
    with zipfile.ZipFile(source)as z:parts={n:z.read(n)for n in z.namelist()}
    parts['xl/workbook.xml']=parts['xl/workbook.xml'].replace(b'</definedNames>',b'<definedName name="_xlnm.Print_Titles" localSheetId="0">#N/A</definedName></definedNames>')
    with zipfile.ZipFile(source,'w')as z:
        for n,v in parts.items():z.writestr(n,v)
    return source,curation,parts


def test_actual_full_table_gates_use_bound_metadata_edge_and_preserve_original(tmp_path):
    source,curation,parts=broken(tmp_path);original=source.read_bytes()
    with pytest.raises(ValueError,match='Unable to read workbook'):openpyxl.load_workbook(source,read_only=True)
    candidate,receipt=materialize(source,'0001',curation,tmp_path/'chain',remediate=True)
    assert receipt['inspection_metadata']['schema_version']=='unreferenced-error-print-settings-inspection/v1'
    assert source.read_bytes()==original and receipt['ordered_outputs']==['S!B1']
    assert verify_materialization(source,candidate,curation,receipt)['status']=='verified'
    inspected=tmp_path/'chain/inspection-source/0001.xlsx'
    with zipfile.ZipFile(inspected)as z:
        assert all(z.read(k)==v for k,v in parts.items()if k!='xl/workbook.xml')
    altered=copy.deepcopy(receipt);altered['inspection_metadata']['actions']=[];seal(altered)
    with pytest.raises(ValueError,match='metadata edge'):verify_materialization(source,candidate,curation,altered)
    altered=copy.deepcopy(receipt);del altered['inspection_metadata'];seal(altered)
    with pytest.raises(ValueError,match='original workbook'):verify_materialization(source,candidate,curation,altered)


def test_inspection_edge_cannot_change_any_cell_or_other_package_member(tmp_path):
    from xl_volatile_formula_remediation import _write_package
    source,_,parts=broken(tmp_path);infos,after,edge=inspection_metadata(source);after['xl/worksheets/sheet1.xml']=after['xl/worksheets/sheet1.xml'].replace(b'<v>',b'<v>9',1)
    assert after['xl/worksheets/sheet1.xml']!=parts['xl/worksheets/sheet1.xml']
    output=tmp_path/'modified.xlsx';_write_package(infos,after,output)
    with pytest.raises(ValueError,match='package content'):verify_inspection_source(source,output,edge)


@pytest.mark.parametrize('kwargs',[{'answer_in_table':True},{'in_cone':True}])
def test_metadata_edge_never_waives_table_targets_or_leaks(tmp_path,kwargs):
    source,curation,_=broken(tmp_path,**kwargs)
    with pytest.raises(ValueError):materialize(source,'0001',curation,tmp_path/'chain',remediate=True)
    assert not(tmp_path/'chain/0001.xlsx').exists()
