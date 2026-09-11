"""Typed formula evidence remains deterministic across producer and revalidator."""
import copy
from types import SimpleNamespace
import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from scripts.batch.harbor_admission import record
from scripts.batch.harbor_workbook_evidence import typed_cell_value
from scripts.batch.harbor_normalization_producer import workbook_context
from scripts.batch.harbor_review_context import normalization_body


@pytest.mark.parametrize('kind',['array','data_table','ordinary'])
@pytest.mark.parametrize('tamper',[False,True])
def test_normalization_typed_formula_context_reconstructs_exactly(tmp_path,kind,tamper):
    source=tmp_path/'source.xlsx';baseline=tmp_path/'base.xlsx';book=Workbook();sheet=book.active;sheet.title='S'
    sheet['B1']=17
    if kind=='array':sheet['A1']=ArrayFormula(ref='A1:A2',text='=ROW(A1:A2)')
    elif kind=='data_table':sheet['A1']=DataTableFormula(ref='A1:A2',r1='C1')
    else:sheet['A1']='=2+3'
    book.save(source);book.save(baseline);book.close()
    draft=tmp_path/'draft.json';draft.write_text('{}');audit=tmp_path/'audit.md';audit.write_text('Synthetic audit binding')
    rows=[dict(clean=True,cells=[['S','B1',17]],reference='S!B1',duplicate_refs=[])]
    context,checks=workbook_context(source,baseline,rows)
    assert (context,checks)==workbook_context(source,baseline,rows)
    observed=context['S'][0]['value']
    if kind=='array':assert observed['text']=='=ROW(A1:A2)' and observed['attributes']['ref']=='A1:A2'
    elif kind=='data_table':assert observed['attributes']['r1']=='C1'
    else:assert observed=='=2+3'
    bindings=dict(source_generation={'id':'synthetic-source'},segmentation_generation={'id':'synthetic-segments'},variable_source_audit=record(audit))
    body=dict(source=record(source),source_sha256=record(source)['sha256'],baseline=record(baseline),baseline_sha256=record(baseline)['sha256'],draft=record(draft),draft_sha256=record(draft)['sha256'],current_source_generation=bindings['source_generation'],current_segmentation_generation=bindings['segmentation_generation'],audit=record(audit),candidates=rows,context=copy.deepcopy(context),candidate_primitive_checks=checks)
    if tamper:
        if kind=='ordinary':body['context']['S'][0]['value']='=999'
        elif kind=='array':body['context']['S'][0]['value']['attributes']['ref']='A1:A3'
        else:body['context']['S'][0]['value']['attributes']['r1']='D1'
        with pytest.raises(ValueError,match='observations'):normalization_body(body,source=record(source),baseline=record(baseline),draft=record(draft),rows=rows,bindings=bindings)
    else:assert normalization_body(body,source=record(source),baseline=record(baseline),draft=record(draft),rows=rows,bindings=bindings)


@pytest.mark.parametrize('value',[object(),ArrayFormula(ref='A1:A2',text=None)])
def test_unsupported_or_missing_formula_evidence_fails_closed(value):
    with pytest.raises(ValueError):typed_cell_value(SimpleNamespace(value=value,data_type='f'))
