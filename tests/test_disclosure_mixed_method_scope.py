"""0315 source-derived standard/structural/custom family remains fully audited."""
import copy,json
from pathlib import Path
import openpyxl,pytest
from test_disclosure_copied_scope import disclose as D
SPAN=['NWC!D37','NWC!D38','NWC!D39'];ENTRY='method_working_capital'

@pytest.fixture
def source(tmp_path):
    p=tmp_path/'source.xlsx';w=openpyxl.Workbook();s=w.active;s.title='NWC'
    labels=['Accounts Payable (Days Outstanding)','Customer Sales Tax Payable (as % of LTM COGS)','Other Accrued Liabilities % COGS']
    for row,label in zip(range(37,40),labels):
        s[f'B{row}']=label;s[f'D{row}']=f'=AVERAGE(F{row}:K{row})'
        for col in range(6,12):s.cell(row,col,0.1)
    s['Z1']='=2+3';w.save(p);gold=D.Book(p)
    task=tmp_path/'0315-outputs';(task/'environment').mkdir(parents=True);(task/'tests').mkdir()
    for sheet in w:
        for cell in list(sheet._cells.values()):
            if cell.data_type=='f':cell.value=None
    w.save(task/'environment/0315-inputs.xlsx');w.close();delivered=D.Book(task/'environment/0315-inputs.xlsx')
    (task/'tests/answer_key.json').write_text(json.dumps({'targets':{'NWC!Z1':5}}))
    records=[];assessments=[];bands=[]
    for cell in SPAN:
        row=int(cell[5:]);band=D.make_band(gold,'NWC',row,[(4,cell)]);bands.append(band)
        base=dict(band=band['band'],cells=band['cells'],cell_keys=band['cell_keys'],label=gold.row_label(cell),roles=[ENTRY],role_context=[gold.row_label(cell)])
        r,a=D.try_custom_role(gold,band,gold.row_label(cell),base,ENTRY);assessments.append(a)
        if r:r.update(disposition='disclosed',leak_flag=False);records.append(r)
    assert [a['status']for a in assessments]==['standard','structural','out_of_catalogue']
    return gold,delivered,records,{'bands':bands},assessments,p,task

def check(v,closure=set()):
    g,d,r,s,a,*_=v;return D._mixed_method_scope_warning(g,d,SPAN,r,ENTRY,closure,s,a)

def test_real_source_categories_preserved_and_faithcheck_warns(source):
    g,d,records,selection,assessments,path,task=source;warning=check(source)
    assert warning and warning['numerical_checks_waived']is False
    assert [c['classification']['status']for c in warning['cells']]==['standard','structural','out_of_catalogue']
    runs=task.parent/'runs';where=runs/task.name;where.mkdir(parents=True)
    (where/'bands.json').write_text(json.dumps(selection));(task/'tests/disclosure.json').write_text(json.dumps(dict(records=records,method_assessments=assessments)))
    result=D.faithcheck_task(task,runs,golden=str(path));assert result['passed'] and result['warnings']==[warning],result
    assert len(records)==1 # Never relabel the two original omissions as custom.

@pytest.mark.parametrize('fault',['missing_assessment','changed_assessment','changed_formula','changed_label','changed_steps','graded','duplicate','overbroad','not_selected','supplied','unclassified','new_custom','foreign_role','missing_custom'])
def test_scope_warning_never_hides_missing_or_changed_evidence(source,fault):
    g,d,r,s,a,*_=source;closure=set()
    if fault=='missing_assessment':a.pop(0)
    elif fault=='changed_assessment':a[0]['variant']='different'
    elif fault=='changed_formula':g.formula[SPAN[0]]='=SUM(F37:K37)'
    elif fault=='changed_label':a[0]['label']='Other'
    elif fault=='changed_steps':r[0]['fields']['steps']='use a different range'
    elif fault=='graded':closure.add(SPAN[0])
    elif fault=='duplicate':r.append(copy.deepcopy(r[0]))
    elif fault=='overbroad':r[0]['cells'].append(SPAN[0])
    elif fault=='not_selected':s['bands'].pop(0)
    elif fault=='supplied':d.value[SPAN[0]]=15
    elif fault=='unclassified':a[1]['status']='unclassified'
    elif fault=='new_custom':a[1]['status']='out_of_catalogue'
    elif fault=='foreign_role':a[1]['roles']=['method_tax']
    elif fault=='missing_custom':r.clear()
    assert check(source,closure)is None

def test_exact_duplicate_historical_assessments_do_not_duplicate_claims(source):
    source[4].extend(copy.deepcopy(source[4]));assert check(source)
