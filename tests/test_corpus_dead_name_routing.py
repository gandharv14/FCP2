"""Dead literal-name recovery retains the original source and every publication gate."""
import copy
import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile
import pytest
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName
from scripts import corpus_build as build
from scripts.corpus import digest
from xl_volatile_formula_remediation import build_plan,verify_manifest

@pytest.fixture
def source_case(tmp_path,monkeypatch):
    raw=tmp_path/'0001.xlsx';book=Workbook();book.calculation.fullCalcOnLoad=False
    sheet=book.active;sheet['A1']=3;sheet['B1']='=A1*2'
    book.defined_names.add(DefinedName('OldLabel',attr_text='"[old.xls]Sheet!$A$1"',hidden=True));book.save(raw)
    with ZipFile(raw) as z:parts={n:z.read(n) for n in z.namelist()}
    part='xl/worksheets/sheet1.xml';parts[part]=parts[part].replace(b'<f>A1*2</f><v></v>',b'<f>A1*2</f><v>6</v>').replace(b'<f>A1*2</f><v />',b'<f>A1*2</f><v>6</v>')
    with ZipFile(raw,'w') as z:
        for n,v in parts.items():z.writestr(n,v)
    run=tmp_path/'run';directory=run/'workbooks/0001';directory.mkdir(parents=True)
    curation=tmp_path/'curation.toml';curation.write_text('# retained exact selection\n')
    source={'workbook_id':'0001','path':str(raw),'sha256':digest(raw),'format':'.xlsx'}
    args=Namespace(run_root=str(run),source_inventory=None,inventory_batch_id=None,recalc_root=None)
    ledger={'route':'restricted_pass','state':'READY','failure':None,'reason_codes':['confirmed_false_external_detection'],
            'build':{'attempts':1,'curation_path':str(curation),'bindings':{'code_sha256':'frozen','curation_sha256':digest(curation),'inventory_sha256':None,'inventory_batch_id':None}}}
    path=directory/'ledger.json';path.write_text(json.dumps(ledger));monkeypatch.setattr(build,'code_hash',lambda:'frozen')
    return args,source,ledger,path

@pytest.mark.parametrize('change',['ineligible','unresolved','empty','other_action','mixed_action','missing_actions','wrong_route'])
def test_optional_route_requires_complete_dead_name_only_plan(tmp_path,monkeypatch,change):
    plan={'status':'eligible','unresolved':[],'before_health':{'route':'restricted_pass'},'actions':[{'kind':'remove_dead_defined_name'}]}
    if change=='ineligible':plan['status']='ineligible'
    elif change=='unresolved':plan['unresolved']=[{'kind':'live_restricted_defined_name'}]
    elif change=='empty':plan['actions']=[]
    elif change=='other_action':plan['actions']=[{'kind':'replace_formula'}]
    elif change=='mixed_action':plan['actions'].append({'kind':'replace_formula'})
    elif change=='missing_actions':del plan['actions']
    elif change=='wrong_route':plan['before_health']['route']='pass'
    monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',lambda p:plan)
    assert build.dead_name_only_plan(tmp_path/'source.xlsx','restricted_pass') is None

@pytest.mark.parametrize('route,recalculated,extension',[('pass',False,'.xlsx'),('reject',False,'.xlsx'),('restricted_recalc_pass',False,'.xlsx'),('restricted_pass',True,'.xlsx'),('restricted_pass',False,'.xls')])
def test_ineligible_source_does_not_even_plan(tmp_path,monkeypatch,route,recalculated,extension):
    plan=Mock(side_effect=AssertionError('planning must not run'))
    monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',plan)
    assert build.dead_name_only_plan(tmp_path/('source'+extension),route,recalculated=recalculated) is None
    assert not plan.called

def test_actual_dead_name_selected_and_live_name_preserved(source_case):
    _,source,_,_=source_case
    plan=build.dead_name_only_plan(source['path'],'restricted_pass')
    assert len(plan['actions'])==1 and plan['actions'][0]['kind']=='remove_dead_defined_name'
    from xl_volatile_formula_remediation import _defined_name_plan
    xml=b'<workbook><definedNames><definedName name="OldLabel">"[old.xls]Sheet!$A$1"</definedName></definedNames></workbook>'
    assert _defined_name_plan(xml,['=OldLabel'])==( [], [] )

class SourceGateReached(RuntimeError):pass

def intercept_source_gate(monkeypatch):
    calls=[]
    def prepare(path,*args,**kwargs):
        calls.append((Path(path),kwargs));raise SourceGateReached('production source gate reached')
    monkeypatch.setattr('xl_source_recalc.prepare_source_generation',prepare)
    return calls

def test_worker_applies_verifies_reinspects_before_unchanged_source_gate(source_case,monkeypatch):
    args,source,_,path=source_case;calls=intercept_source_gate(monkeypatch)
    monkeypatch.setattr(build,'reuse_completed_source',lambda *a,**k:None)
    original=Path(source['path']).read_bytes()
    with pytest.raises(SourceGateReached):build.worker(args,source)
    ledger=json.loads(path.read_text());normal=ledger['build']['normalization']
    candidate=Path(normal['candidate']);plan=json.loads(Path(normal['plan']).read_text());receipt=json.loads(Path(normal['manifest']).read_text())
    verify_manifest(source['path'],candidate,plan,receipt)
    assert calls==[(candidate,{})]  # fresh reinspection changed restricted source to native pass
    assert normal['original_sha256']==source['sha256'] and normal['candidate_sha256']==digest(candidate)
    assert normal['manifest_sha256']==digest(normal['manifest']) and normal['plan_sha256']==digest(normal['plan'])
    assert Path(source['path']).read_bytes()==original
    assert ledger['route']=='restricted_pass' and ledger['reason_codes']==['confirmed_false_external_detection']
    assert ledger['build']['stage']=='source'

@pytest.mark.parametrize('change',['no_actions','other_actions','unresolved','signed_recalc'])
def test_worker_preserves_original_restricted_path_when_not_eligible(source_case,monkeypatch,change):
    args,source,ledger,path=source_case;calls=intercept_source_gate(monkeypatch)
    monkeypatch.setattr(build,'reuse_completed_source',lambda *a,**k:None)
    if change=='signed_recalc':
        ledger['build']['recalculation']={'result_sha256':'old-signed-receipt'};path.write_text(json.dumps(ledger))
        monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',Mock(side_effect=AssertionError('must not transform signed evidence')))
    else:
        plan=build_plan(source['path'])
        if change=='no_actions':plan['actions']=[]
        elif change=='other_actions':plan['actions'][0]['kind']='replace_formula'
        else:plan['unresolved']=[{'kind':'live_restricted_defined_name'}]
        monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',lambda p:plan)
    with pytest.raises(SourceGateReached):build.worker(args,source)
    assert calls==[(Path(source['path']),{'inventory':None,'inventory_batch_id':None})]
    assert 'normalization' not in json.loads(path.read_text())['build']

def test_altered_candidate_fails_before_source_publication(source_case,monkeypatch):
    args,source,_,path=source_case;calls=intercept_source_gate(monkeypatch)
    import xl_volatile_formula_remediation as remediation
    apply=remediation.apply_plan
    def altered(*a,**k):
        receipt=apply(*a,**k);Path(a[2]).write_bytes(b'altered candidate');return receipt
    monkeypatch.setattr(remediation,'apply_plan',altered)
    with pytest.raises(ValueError):build.worker(args,source)
    assert calls==[]
    assert json.loads(path.read_text())['build']['stage']=='normalization'
