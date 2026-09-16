import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture
def d(monkeypatch):
 path=Path(__file__).resolve().parents[1]/'.cursor/skills/task-disclosure/scripts/disclose.py'
 monkeypatch.syspath_prepend(str(path.parent));spec=importlib.util.spec_from_file_location('npv_disclose',path);m=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,m);spec.loader.exec_module(m);return m


def book(formula):return SimpleNamespace(formula={'Assumptions!D49':formula},row_label=lambda k:'Net Present Value')


def test_actual_0177_xnpv_uses_dates_not_periodic_npv(d):
 formula="=XNPV($E$14,'CF Schedule'!$K$17:$K$148,'CF Schedule'!$B$17:$B$148)"
 records=d.detect_npv_timing(book(formula),{'Assumptions!D49'});assert len(records)==1
 record=records[0];assert record['value']=='dated_first_date_origin'
 sentence=d.render_sentence(record);assert 'first supplied cash-flow date' in sentence and 'elapsed days' in sentence and 'full period out'not in sentence
 assert d.catalog_signature('method_discounting',dict(functions=['XNPV'],operators=[],formula=formula))is None


@pytest.mark.parametrize('formula,value',[('=NPV(A1,B1:B4)','excel_default_one_period_out'),('=NPV(A1,B1:B4)+C1','t0_added_separately'),('=npv(A1,B1:B4)','excel_default_one_period_out')])
def test_periodic_npv_conventions_remain(d,formula,value):
 rows=d.detect_npv_timing(book(formula),{'Assumptions!D49'});assert len(rows)==1 and rows[0]['value']==value


@pytest.mark.parametrize('formula',['="NPV(A1,B1:B4)"',"='NPV(test)'!B2",'=MYNPV(A1,B1:B4)','=XNPV(A1,B1:B4,C1:C4)+D1','=IF(A1,XNPV(A1,B1:B4,C1:C4),NPV(A1,B1:B4))','=XNPV(A1,B1:B4)','=npv (A1,B1:B4)'])
def test_text_foreign_function_or_mixed_dated_formula_never_gets_periodic_timing(d,formula):
 assert d.detect_npv_timing(book(formula),{'Assumptions!D49'})==[]


def test_npv_only_graded_scope_is_not_expanded(d):
 assert d.detect_npv_timing(book('=XNPV(A1,B1:B4,C1:C4)'),set())==[]


def test_retained_correction_preserves_all_unrelated_records_and_metadata(d):
 from scripts.fleet.npv_timing_repair import corrected_payload
 g=book('=XNPV(A1,B1:B4,C1:C4)');rec=d.detect_npv_timing(g,{'Assumptions!D49'})[0]
 rec.update(label='Excel NPV discounts its first argument by one full period.',note='Excel NPV discounts its first argument by one full period.',value='excel_default_one_period_out',alternatives=['excel_default_one_period_out','t0_added_separately'],disposition='disclosed')
 original={'records':[rec,{'cell_keys':['Other!A1'],'retained':'evidence'}],'metadata':{'consumed_review':1},'agent_records':d.agent_records([rec])}
 import copy
 before=copy.deepcopy(original);new,indices=corrected_payload(d,g,None,original,{'Assumptions!D49'},set(),set())
 assert original==before and indices==[0] and new['metadata']==original['metadata'] and new['records'][1]==original['records'][1]
 assert new['records'][0]['value']=='dated_first_date_origin'and new['agent_records'][0]['value']=='dated_first_date_origin'
 for closure in [{'Assumptions!D49'}]:
  with pytest.raises(ValueError):corrected_payload(d,g,None,original,{'Assumptions!D49'},closure,set())
 for field,value in [('value','t0_added_separately'),('source','foreign'),('evidence','=NPV(A1,B1:B4)')]:
  bad=copy.deepcopy(original);bad['records'][0][field]=value
  with pytest.raises(ValueError):corrected_payload(d,g,None,bad,{'Assumptions!D49'},set(),set())


def test_unlabelled_0171_periodic_series_has_exact_cell_locator(d):
 cell='PA_DCF!D29';formula='=D$26/((1+$C29)^5)+NPV($C29,$L$17:$P$17)'
 gold=SimpleNamespace(formula={cell:formula},row_label=lambda _: '')
 record=d.detect_npv_timing(gold,{cell})[0]
 assert record['fields']=={'target_cell':'`PA_DCF!D29`'}
 assert d.render_sentence(record)=='The NPV series in cell `PA_DCF!D29` treats its first cash flow as falling one full period out.'
 assert d.verified_npv_cell_timing(record,gold)
 assert record['evidence']==formula and record['cell_keys']==[cell]
 import copy
 for key,bad in [('fields',{'target_cell':'`PA_DCF!E29`'}),('evidence','=NPV(A1,B1:B4)'),('value','t0_added_separately'),('band','`PA_DCF!D29:E29`'),('cells',['PA_DCF!E29']),('source','foreign')]:
  wrong=copy.deepcopy(record);wrong[key]=bad
  assert not d.verified_npv_cell_timing(wrong,gold)
 gold.formula[cell]='=XNPV(A1,B1:B4,C1:C4)'
 assert not d.verified_npv_cell_timing(record,gold)


@pytest.mark.parametrize('formula',['=NPV(A1,B1:B4)+NPV(A1,C1:C4)','=XNPV(A1,B1:B4,C1:C4)','=NPV(A1,B1:B4)+C1','=NPV(A1,B1:B4)+XNPV(A1,B1:B4,C1:C4)','=NPV(A1)'])
def test_unlabelled_ambiguous_or_dated_timing_never_gets_cell_fallback(d,formula):
 gold=SimpleNamespace(formula={'Sheet!C5':formula},row_label=lambda _: '')
 for record in d.detect_npv_timing(gold,{'Sheet!C5'}):
  assert 'target_cell'not in record['fields'] and not d.render_sentence(record)


def test_labelled_npv_sentence_is_byte_identical(d):
 record=d.detect_npv_timing(book('=NPV(A1,B1:B4)'),{'Assumptions!D49'})[0]
 assert record['fields']=={'label':'"Net Present Value"'}
 assert d.render_sentence(record)=='The present value on the row labelled "Net Present Value" treats its first cash flow as falling one full period out.'


def test_cell_locator_quotes_source_sheet_without_inventing_label(d):
 cell="Discount sensitivity!D29";gold=SimpleNamespace(formula={cell:'=NPV(A1,B1:B4)'},row_label=lambda _: '')
 record=d.detect_npv_timing(gold,{cell})[0]
 assert record['fields']=={'target_cell':"`'Discount sensitivity'!D29`"}
 assert d.verified_npv_cell_timing(record,gold)
