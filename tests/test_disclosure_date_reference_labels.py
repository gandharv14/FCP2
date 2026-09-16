from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import pytest

@pytest.fixture
def renderer():
    path=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
    spec=importlib.util.spec_from_file_location('date_reference_renderer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_date_series_does_not_inherit_neighboring_column_heading(renderer):
    book=SimpleNamespace(value={f'S!{c}336':datetime(2020+i,12,31)for i,c in enumerate('LMNOP')},
        formula={},row_label=lambda _: 'Annual Expense')
    text=renderer.describe_ref(book,'L$336','S','Debt')
    assert text=='cell S!L336 with its row fixed when copied containing a date value'
    assert renderer.describe_ref(book,'$L$336','S','Debt').startswith('fixed cell S!L336 ')
    assert 'Annual Expense'not in renderer.describe_ref(book,'L336:N336','S','Debt')


def test_isolated_date_input_keeps_its_semantic_row_label(renderer):
    book=SimpleNamespace(value={'S!H113':datetime(2023,3,31)},formula={},row_label=lambda _: 'Recap Date')
    assert renderer.describe_ref(book,'$H$113','S','')=='fixed cell S!H113 on the row labelled "Recap Date"'


def test_numeric_series_and_two_dates_keep_existing_behavior(renderer):
    book=SimpleNamespace(value={'S!L1':datetime(2020,1,1),'S!M1':datetime(2021,1,1),'S!N1':123},formula={},row_label=lambda _: 'Revenue')
    assert 'on the row labelled "Revenue"'in renderer.describe_ref(book,'L1','S','')
    assert 'on the row labelled "Revenue"'in renderer.describe_ref(book,'N1','S','')


@pytest.fixture
def source_correction(renderer,tmp_path):
    import copy,openpyxl
    from scripts.batch.harbor_admission import record
    book=openpyxl.Workbook();sheet=book.active;sheet.title='S'
    sheet['K3']='Annual Expense'
    for col,year in zip('LMN',(2020,2021,2022)):sheet[f'{col}3']=datetime(year,12,31)
    sheet['K4']='Revolver';sheet['L4']='=YEAR(L$3)'
    source=tmp_path/'source.xlsx';book.save(source);book.close();gold=renderer.Book(source)
    profile=renderer.formula_profile(gold,dict(band='S!L4',cell_keys=['S!L4'],label='Revolver',pattern=''))
    fields=renderer.custom_sentence_fields(profile,gold,lock_cells=['S!L4']);assert fields.pop('_coverage_complete')
    row=dict(source='custom_method_detector',coverage_complete=True,disposition='disclosed',leak_flag=False,
        cell_keys=['S!L4'],cells=['S!L4'],method_profile={k:v for k,v in profile.items()if k!='ast'},fields=fields)
    old=copy.deepcopy(row);old['fields']['steps']=old['fields']['steps'].replace(' containing a date value',' on the row labelled "Annual Expense"')
    return old,row,dict(golden=str(source),golden_sha256=record(source)['sha256'])


def test_reference_revision_rederives_source_formula_and_preserves_locks(source_correction):
    from scripts.batch.harbor_disclosure_revision import date_reference_steps
    date_reference_steps(*source_correction)


@pytest.mark.parametrize('fault',['operator','lock','profile','source'])
def test_reference_revision_rejects_nonlabel_changes(source_correction,fault):
    from scripts.batch.harbor_disclosure_revision import date_reference_steps
    old,new,source=source_correction
    if fault=='operator':new['fields']['steps']='add one to '+new['fields']['steps']
    elif fault=='lock':new['fields']['steps']=new['fields']['steps'].replace(' with its row fixed when copied','')
    elif fault=='profile':old['method_profile']['formula']='=MONTH(L$3)'
    else:source['golden_sha256']='0'*64
    with pytest.raises(ValueError):date_reference_steps(old,new,source)
