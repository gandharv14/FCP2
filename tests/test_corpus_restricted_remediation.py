import copy,json
from pathlib import Path
from unittest.mock import Mock
import pytest
from openpyxl import Workbook
from zipfile import ZipFile
from scripts import corpus_build as B
from test_corpus_dead_name_routing import source_case,intercept_source_gate,SourceGateReached
from xl_volatile_formula_remediation import verify_manifest


def ledger():return dict(route='restricted_recalc_pass',failure='trusted_recalculation_required',reason_codes=['mixed_restricted_recalc','worksheet_offset'])
def eligible():return dict(status='eligible',unresolved=[],before_health=dict(route='restricted_recalc_pass',reason_codes=ledger()['reason_codes']),actions=[dict(kind='replace_formula',rule_id='offset-scalar-identity-and-blank-column/v2')])


@pytest.mark.parametrize('change',['today','manual','external','route','failure','signed','format'])
def test_unrelated_recalc_never_enters_new_route(tmp_path,monkeypatch,change):
 row=ledger();p=tmp_path/'source.xlsx';signed=False
 if change in('today','manual','external'):row['reason_codes'].append(dict(today='worksheet_today',manual='manual_calculation_mode',external='external_links_present')[change])
 elif change=='route':row['route']='recalc_candidate'
 elif change=='failure':row['failure']='source_policy_unsupported'
 elif change=='signed':signed=True
 else:p=tmp_path/'source.xls'
 planner=Mock(side_effect=AssertionError('must not plan'));monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',planner)
 assert B.restricted_remediation_plan(p,row,recalculated=signed)is None
 assert not planner.called


@pytest.mark.parametrize('change',['ineligible','unresolved','empty','indirect','metadata','foreign_health'])
def test_only_complete_original_offset_or_sheet_name_proof_selected(tmp_path,monkeypatch,change):
 p=eligible()
 if change=='ineligible':p['status']='ineligible'
 elif change=='unresolved':p['unresolved']=[dict(kind='dynamic-input')]
 elif change=='empty':p['actions']=[]
 elif change=='indirect':p['actions'][0]['rule_id']='indirect-static-concat-to-a1/v1'
 elif change=='metadata':p['actions'][0]['kind']='remove_dead_defined_name'
 else:p['before_health']['reason_codes']=[]
 monkeypatch.setattr('xl_volatile_formula_remediation.build_plan',lambda path:p)
 assert B.restricted_remediation_plan(tmp_path/'source.xlsx',ledger())is None


def test_real_offset_candidate_delegates_full_original_source_gate(source_case,monkeypatch):
 args,source,row,path=source_case;raw=Path(source['path'])
 book=Workbook();book.calculation.fullCalcOnLoad=False;sheet=book.active;sheet['A1']=3;sheet['B1']='=OFFSET(A1,0,0)*2';book.save(raw);book.close()
 with ZipFile(raw)as z:parts={n:z.read(n)for n in z.namelist()}
 part='xl/worksheets/sheet1.xml';parts[part]=parts[part].replace(b'<f>OFFSET(A1,0,0)*2</f><v></v>',b'<f>OFFSET(A1,0,0)*2</f><v>6</v>')
 with ZipFile(raw,'w')as z:
  for n,data in parts.items():z.writestr(n,data)
 source['sha256']=B.digest(raw)
 from xl_source_health import inspect_workbook
 health=inspect_workbook(raw);assert health['route']=='restricted_recalc_pass'
 row.update(route=health['route'],failure='trusted_recalculation_required',reason_codes=health['reason_codes']);path.write_text(json.dumps(row))
 assert B.restricted_remediation_plan(raw,row)is not None
 original=raw.read_bytes();calls=intercept_source_gate(monkeypatch);monkeypatch.setattr(B,'reuse_completed_source',lambda *a,**k:None)
 with pytest.raises(SourceGateReached):B.worker(args,source)
 result=json.loads(path.read_text())['build']['normalization'];candidate=Path(result['candidate'])
 verify_manifest(raw,candidate,json.loads(Path(result['plan']).read_text()),json.loads(Path(result['manifest']).read_text()))
 assert calls==[(candidate,{})]and raw.read_bytes()==original
 assert json.loads(path.read_text())['route']=='restricted_recalc_pass'
