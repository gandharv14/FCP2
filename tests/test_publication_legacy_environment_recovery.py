"""The actual stopped legacy environment defect admits only its exact repair."""
from pathlib import Path
import json
import pytest
from scripts.fleet import publication_launch as L
H=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/0570-publication-001'

@pytest.fixture
def case(tmp_path):
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return L.binding(p)
 oldrow=L.binding(H/'root-launch-002/profile.json');old=L.checked(oldrow)
 failure=L.binding(H/'publication-progress-001/native.json');observed=L.checked(failure)
 op={'kind':'publication','state':'hold','operation_id':'fixture-stopped-operation','owner':old['owner'],'workbook':'0570','fence':old['expected_fence']+1,'evidence':json.dumps({k:next(r for r in observed['files']if r['path'].endswith('/reconciliation.json'))[k]for k in ('path','sha256')})}
 wb={'workbook':'0570','state':'hold','owner':op['owner'],'fence':op['fence']}
 intent=put('settlement-intent.json',{'operation':op,'workbook':wb,'proof':failure})
 settlement=put('settlement.json',{'intent':intent,'result':{'state':'abandoned','operation_id':op['operation_id']}})
 recovery={'schema_version':'fleet-publication-legacy-environment-recovery/v1','prior_profile':oldrow,'root_intent':L.binding(H/'root-launch-002/launch-001/intent.json'),'failure_proof':failure,'reconciliation':settlement}
 m=L.binding(H/'implementation-003/manifest.json');manifest=L.checked(m)
 p={**old,'expected_owner':old['owner'],'expected_fence':op['fence'],'owner':'next-publisher','publisher':manifest['publisher'],'implementation':m}
 plan=L.checked(L.binding(H/'plan-004.json'))
 return p,plan,recovery,put

@pytest.mark.parametrize('defect',[None,'tracker','process','owning-intent','extra-file','wrong-failure','changed-work','wrong-owner','unsettled','other-function','other-environment-branch','dependency'])
def test_exact_actual_failure_and_function_delta(case,defect,tmp_path):
 p,plan,r,put=case
 if defect in ('tracker','process','owning-intent','extra-file','wrong-failure'):
  v=L.checked(r['failure_proof'])
  if defect=='tracker':v['tracker']['sha256']='0'*64
  elif defect=='process':v['processes']=[{'pid':7}]
  elif defect=='owning-intent':v['owning_intent_exists']=True
  elif defect=='extra-file':v['files'].append({'path':'/foreign','sha256':'0'*64})
  else:next(x for x in v['files']if x['path'].endswith('/controller.log'))['text']='ValueError: semantic failure\n'
  r['failure_proof']=put('bad-failure.json',v)
 elif defect=='changed-work':plan['ready']={}
 elif defect=='wrong-owner':p['expected_owner']='foreign'
 elif defect=='unsettled':
  v=L.checked(r['reconciliation']);v['result']['state']='hold';r['reconciliation']=put('bad-settlement.json',v)
 elif defect in ('other-function','other-environment-branch'):
  source=Path(p['publisher']['path']).read_text()
  source=source.replace('def controlled(', 'def changed_controlled(')if defect=='other-function'else source.replace("prefix = 'HARBOR_FLEET_LEGACY_RESEARCH_'","prefix = 'FOREIGN_'")
  q=tmp_path/'publish.py';q.write_text(source);p['publisher']=L.binding(q);m=L.checked(p['implementation']);m['publisher']=p['publisher'];p['implementation']=put('bad-manifest.json',m)
 elif defect=='dependency':
  m=L.checked(p['implementation']);m['dependencies']['new']={'path':'/foreign','sha256':'0'*64};p['implementation']=put('bad-manifest.json',m)
 p['preowning_recovery']=put('recovery.json',r)
 if defect:
  with pytest.raises((ValueError,KeyError)):L.preowning(p,plan,L.checked,L.binding)
 else:
  old,observed,op,wb=L.preowning(p,plan,L.checked,L.binding)
  assert observed['owning_intent_exists']is False and op['state']=='hold' and wb['fence']==5

def test_transform_cannot_repeat_or_select_another_function():
 a=(H/'implementation-002/publish.py').read_text();b=L.legacy_environment_repair(a)
 import ast
 assert ast.dump(ast.parse(b),include_attributes=False)==ast.dump(ast.parse((H/'implementation-003/publish.py').read_text()),include_attributes=False)
 with pytest.raises(ValueError):L.legacy_environment_repair(b)
