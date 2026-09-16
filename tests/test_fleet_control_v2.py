"""Explicit smoke identity keeps the same independent qualification boundary."""
import json
from copy import deepcopy
import pytest
from scripts.fleet import fleet_control_v2 as f
from scripts.fleet import publish as p


def fixture(tmp_path, workbook='0029'):
    output=tmp_path/'output';output.mkdir()
    (output/'generation-manifest.json').write_text(json.dumps({'identity':{'workbook_id':workbook}}))
    verification=p.retain(tmp_path/'verification.json',{
        'schema_version':'independent-published-task-copy-check/v1','workbook':workbook,
        'passed':True,'byte_identical_to_immutable_generation':True,
        'current_qualification_verified':True,'model_calls':0,'runtime_operations':0,
        'task_manifest':p.binding(output/'generation-manifest.json')})
    collection=p.retain(tmp_path/'collection.json',{
        'schema_version':'harbor-fleet-collection/v1','status':'COLLECTED','workbook':workbook,
        'local_byte_integrity_verified':True,'independent_remote_verification':verification,
        'output':str(output),'files':p.files(output)})
    plan={'schema_version':f.SELECTED_SMOKE_SCHEMA,'smoke_workbook':workbook,
          'smoke_verification':verification,'smoke_collection':collection}
    central={'workbooks':[{'workbook':workbook,'state':'complete'},{'workbook':'0270' if workbook=='0029' else '0029','state':'hold'}], 'operations':[]}
    return plan,central


@pytest.mark.parametrize('workbook',['0029','0270'])
def test_either_selected_smoke_requires_its_own_completed_publication(tmp_path,workbook):
    plan,central=fixture(tmp_path,workbook);f.smoke_gate(plan,central)
    central['workbooks'][0]['state']='hold';central['workbooks'][1]['state']='complete'
    with pytest.raises(ValueError,match='not complete'):f.smoke_gate(plan,central)


@pytest.mark.parametrize('change',['report_workbook','collection_workbook','failed','model','runtime','unverified','reference','generation','bytes','duplicate_central','unknown_operation','active_operation'])
def test_mismatched_or_unqualified_smoke_is_rejected(tmp_path,change):
    plan,central=fixture(tmp_path)
    report=p.checked(plan['smoke_verification']);collection=p.checked(plan['smoke_collection'])
    if change=='report_workbook':report['workbook']='0270'
    elif change=='collection_workbook':collection['workbook']='0270'
    elif change=='failed':report['passed']=False
    elif change=='model':report['model_calls']=1
    elif change=='runtime':report['runtime_operations']=1
    elif change=='unverified':report['current_qualification_verified']=False
    elif change=='reference':collection['independent_remote_verification']['sha256']='f'*64
    elif change=='generation':
        output=tmp_path/'output';(output/'generation-manifest.json').write_text(json.dumps({'identity':{'workbook_id':'0270'}}))
        report['task_manifest']=p.binding(output/'generation-manifest.json');collection['files']=p.files(output)
    elif change=='bytes':(tmp_path/'output/generation-manifest.json').write_text('tampered')
    elif change=='duplicate_central':central['workbooks'].append(deepcopy(central['workbooks'][0]))
    else:central['operations']=[{'state':'hold' if change=='unknown_operation' else 'active','kind':'model'}]
    plan['smoke_verification']=p.retain(tmp_path/'changed-verification.json',report)
    if change!='reference':collection['independent_remote_verification']=plan['smoke_verification']
    plan['smoke_collection']=p.retain(tmp_path/'changed-collection.json',collection)
    with pytest.raises(ValueError):f.smoke_gate(plan,central)


def test_legacy_controller_contract_keeps_fixed_smoke(tmp_path):
    plan,central=fixture(tmp_path,'0270');plan['schema_version']=f.SCHEMA;del plan['smoke_workbook']
    f.smoke_gate(plan,central)
    plan['smoke_workbook']='0029'
    with pytest.raises(ValueError,match='legacy'):f.smoke_gate(plan,central)


def test_schema_is_closed_and_does_not_infer_smoke_from_receipts():
    fields={'schema_version':f.SCHEMA,**{k:None for k in ['validation','custody','smoke_verification','smoke_collection','coordinator_receipt','supervisor','remote_supervisor','remote_run_root']}}
    assert f.validate_plan_schema(fields)=='0270'
    fields['schema_version']=f.SELECTED_SMOKE_SCHEMA
    with pytest.raises(ValueError):f.validate_plan_schema(fields)
    fields['smoke_workbook']='0029';assert f.validate_plan_schema(fields)=='0029'
    for value in ['0006','../0029',None,29]:
        invalid={**fields,'smoke_workbook':value}
        with pytest.raises(ValueError):f.validate_plan_schema(invalid)
    with pytest.raises(ValueError):f.validate_plan_schema({**fields,'skip_review':True})
