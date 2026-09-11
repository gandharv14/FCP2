"""Partial dialogue grouping requires original facts and complete claim coverage."""
import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from openpyxl import Workbook


@pytest.fixture
def scope_case(tmp_path, monkeypatch):
    scripts=Path(__file__).resolve().parents[1]/'.cursor/skills/additional-assumptions-dialogue/scripts'
    monkeypatch.syspath_prepend(str(scripts));scope=importlib.import_module('claim_scope');extract=importlib.import_module('extract_claims')
    wb=Workbook();wb.active.title='Model';wb.create_sheet('Inputs')
    for sheet in wb:
        sheet['B1']='Year';sheet['C3']=2023;sheet['E1']='=$C$3';sheet['F1']='=E1+1';sheet['G1']='=F1+1'
    claims=[];rows=[]
    for i,col in enumerate(('E','F'),1):
        target=f'Model!{col}20';source=f'Inputs!{col}30';formula=f'= Inputs!{col}30';wb['Model'][f'{col}20']=formula
        claim=extract.build_claim({'entry':'source_selection','value':'source','cells':[target],
            'fields':{'label':'"Revenue"','ingredient':f'cell {source} on the row labelled "Operating revenue"'}},i)
        claims.append(claim);rows.append({'claim_id':claim['record_id'],'target_reference':target,'source_reference':source,'target_period_header':f'Model!{col}1','source_period_header':f'Inputs!{col}1','target_period_label':'Model!B1','source_period_label':'Inputs!B1','target_year_axis_start':'Model!E1','source_year_axis_start':'Inputs!E1','period_kind':'year','period':2023+i-1,'original_formula':formula})
    # An unrelated method remains unmodified and individually covered.
    other={'record_id':'unrelated-method','sheet':'Model','row_label':'Expense','spoken':'Multiply the input by two.','must_say':['multiply','two'],'reviewer_only':{'entry':'method_operating_expense','cells':['Model!H40']}}
    claims.insert(1,other);source=tmp_path/'source.xlsx';wb.save(source);disclosure=tmp_path/'disclosure.json';disclosure.write_text('{}');evidence=tmp_path/'scope.json'
    payload={'schema_version':'dialogue-source-link-scope/v2','claim_ids':[x['record_id']for x in claims],'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'disclosure_sha256':hashlib.sha256(disclosure.read_bytes()).hexdigest(),'rows':rows,'year_evidence_cells':['Model!C3','Inputs!C3']}
    def prove():
        wb.save(source);payload['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest();evidence.write_text(json.dumps(payload));return scope.prove_groups(copy.deepcopy(claims),disclosure,source,evidence)
    return scope,wb,claims,payload,prove


def test_mixed_claims_keep_exact_once_coverage(scope_case,monkeypatch):
    scope,wb,claims,payload,prove=scope_case;original=copy.deepcopy(claims);result=prove()
    assert result['groups']==[[claims[0]['record_id'],claims[2]['record_id']],['unrelated-method']]
    assert sorted(i for group in result['groups']for i in group)==sorted(payload['claim_ids'])
    assert claims==original


@pytest.mark.parametrize('fault',['dropped_claim','reordered_claim','unknown_claim','duplicated_row','wrong_target','unrelated_method'])
def test_reject_forged_or_missing_claim_coverage(scope_case,fault):
    scope,wb,claims,payload,prove=scope_case
    if fault=='dropped_claim':payload['claim_ids'].pop()
    elif fault=='reordered_claim':payload['claim_ids'].reverse()
    elif fault=='unknown_claim':payload['rows'][0]['claim_id']='unknown'
    elif fault=='duplicated_row':payload['rows'].append(copy.deepcopy(payload['rows'][0]))
    elif fault=='wrong_target':payload['rows'][0]['target_reference']='Model!F20'
    else:payload['rows'][0]['claim_id']='unrelated-method'
    with pytest.raises(ValueError):prove()


@pytest.mark.parametrize('fault',['price_label','intervening_label','intervening_formula_label','intervening_nonyear','wrong_row_label','missing_dependency','cycle','unsupported_formula','wrong_period','bool_year','string_year','nonconsecutive_axis','literal_formula_header','literal_formula_target'])
def test_numeric_equality_is_not_year_proof(scope_case,fault):
    scope,wb,claims,payload,prove=scope_case
    if fault=='price_label':wb['Inputs']['B1']='Price'
    elif fault=='intervening_label':wb['Inputs']['D1']='Units sold'
    elif fault=='intervening_formula_label':wb['Inputs']['E1']='="Units sold"'
    elif fault=='intervening_nonyear':wb['Inputs']['E1']=12
    elif fault=='wrong_row_label':payload['rows'][0]['source_period_label']='Inputs!B2';wb['Inputs']['B2']='Year'
    elif fault=='missing_dependency':payload['year_evidence_cells'].remove('Inputs!C3')
    elif fault=='cycle':wb['Inputs']['C3']='=E1'
    elif fault=='unsupported_formula':wb['Inputs']['E1']='=C3*1'
    elif fault=='wrong_period':payload['rows'][0]['period']=2024
    elif fault=='bool_year':wb['Inputs']['C3']=True
    elif fault=='string_year':wb['Inputs']['C3']='2023'
    elif fault=='nonconsecutive_axis':wb['Inputs']['G1']=2026
    elif fault=='literal_formula_header':wb['Inputs']['E1'].data_type='s'
    else:wb['Model']['E20'].data_type='s'
    with pytest.raises(ValueError):prove()


def test_unproved_source_links_remain_individual(scope_case):
    scope,wb,claims,payload,prove=scope_case;payload['rows']=[];payload['year_evidence_cells']=[];result=prove()
    assert result['groups']==[[c['record_id']]for c in claims]
    assert result['proven_claim_ids']==[]


def test_mixed_groups_compose_and_map_every_original_claim(scope_case):
    scope,wb,claims,payload,prove=scope_case
    # Reproduce the successful proof mutation on a separate claim copy.
    result=prove();proved=copy.deepcopy(claims)
    for claim in proved:
        if claim['record_id']in result['proven_claim_ids']:
            claim['spoken']+=' Use the matching period for this link.'
            claim['must_say'].append('matching period')
    composer=importlib.import_module('compose_draft');validator=importlib.import_module('validate_dialogue');lib=importlib.import_module('aa_lib')
    document={'claims':proved,'shared_source_links':result,'junior_titles':['Analyst'],'senior_titles':['VP']}
    template,slots=composer.compose(document,document)
    assert len(slots['slots'])==4
    filled=composer.assemble_slots(template,slots,{'schema_version':'dialogue-slot-prose/v1','slots':{
        'c001-junior':'Which source should I use?',
        'c001-senior':'Use Operating revenue on Inputs for Revenue, matching the period.',
        'c002-junior':'How should I calculate Expense?',
        'c002-senior':'Multiply the input by two.'}})
    mapped,faults=validator.map_turns_to_claims(lib.parse_turns(composer.strip_slot_scaffold(filled)),proved)
    assert not faults and set(mapped)==set(payload['claim_ids'])
    assert all(len(turns)==2 for turns in mapped.values())
