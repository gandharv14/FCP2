import json
import openpyxl
import pytest
import gen_normalizer as G
import xl_mcp_oracle as O


@pytest.mark.parametrize('value,label,excluded',[
    (0.039,'Senior notes 3.900% due 2031',True),
    (0.0685,'6.850% debt',True),(0.05625,'5.625% debt',True),
    (0.06875,'6.875% debt',True),(12345,'Capacity: 12,345 MW',True),
    (-0.025,'Change -2.5%',True),(0.04,'Senior notes 3.900%',False),
    (12345,'Identifier A12345B',False),(1.5,'Formula =1.5',False),
])
def test_visible_numeric_label_excludes_without_erasing_source(tmp_path,value,label,excluded):
    source=tmp_path/'0001.xlsx';inputs=tmp_path/'inputs';inputs.mkdir()
    book=openpyxl.Workbook();book.active.title='Model';book.active['A1']=value
    sheet=book.create_sheet('Picklist');sheet['B2']=label
    if label.startswith('Formula ='):sheet['B2']='=1.5'
    book.save(source);book.save(inputs/'0001-inputs.xlsx');book.close()
    before={p:p.read_bytes()for p in(source,inputs/'0001-inputs.xlsx')}
    run=tmp_path/'runs/0001-variable-sources';run.mkdir(parents=True)
    (run/'draft.json').write_text(json.dumps(dict(rows=[dict(draft_id='input',variable_text='Rate',value_text='Model!A1')])))
    _,rows=G.analyse('0001',source,G._sha256(source),runs_root=run.parent,inputs_root=inputs)
    assert rows[0]['clean']is not excluded
    assert rows[0]['extra']==[]
    if excluded:
        assert rows[0]['code']=='duplicate_value_leak'
        assert rows[0]['leaks']==['Picklist!B2']
        assert O.text_leaks_target(label,O.ValueTarget('input',value,value,''))
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_closed_numeric_tokens_do_not_use_identifier_fragments_or_nonfinite_numbers():
    assert list(G.visible_numeric_values('ID_A123 6.875% USD 12,345 -2.5e-2'))==[6.875,0.06875,12345,-0.025]
    assert list(G.visible_numeric_values('A123B 1e99999'))==[]
