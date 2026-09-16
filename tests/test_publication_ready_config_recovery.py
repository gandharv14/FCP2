from pathlib import Path
import json
import pytest
from scripts.fleet import publication_launch as L,fresh_publication as F
H=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/0162-publication-001'

@pytest.fixture
def case(tmp_path):
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return L.binding(p)
 oldrow=L.binding(H/'root-launch-001/profile.json');old=L.checked(oldrow)
 r=dict(schema_version='fleet-publication-completed-ready-config-recovery/v1',prior_profile=oldrow,root_intent=L.binding(H/'root-launch-001/launch-001/intent.json'),failure_proof=L.binding(H/'preowning-reconciliation-001/native.json'),reconciliation=L.binding(H/'preowning-reconciliation-001/completed.json'))
 m=L.binding(H/'implementation-003/manifest.json');manifest=L.checked(m)
 p=dict(old,expected_owner=old['owner'],expected_fence=old['expected_fence']+1,owner='next-publisher',publisher=manifest['publisher'],implementation=m)
 return p,L.checked(L.binding(H/'plan-002.json')),r,put

@pytest.mark.parametrize('defect',[None,'tracker','process','owning-intent','extra-file','wrong-failure','changed-work','wrong-owner','unsettled','owning-code','resolver','dependency'])
def test_exact_actual_config_failure_and_closed_repair(case,defect,tmp_path):
 p,plan,r,put=case
 if defect in ('tracker','process','owning-intent','extra-file','wrong-failure'):
  v=L.checked(r['failure_proof'])
  if defect=='tracker':v['tracker']['sha256']='0'*64
  elif defect=='process':v['processes']=[{'pid':7}]
  elif defect=='owning-intent':v['owning_intent_exists']=True
  elif defect=='extra-file':v['files'].append({'path':'/foreign','sha256':'0'*64})
  else:next(x for x in v['files']if x['path'].endswith('/controller.log'))['text']='KeyError: other\n'
  r['failure_proof']=put('bad-failure.json',v)
 elif defect=='changed-work':plan['ready']={}
 elif defect=='wrong-owner':p['expected_owner']='foreign'
 elif defect=='unsettled':
  v=L.checked(r['reconciliation']);v['result']['state']='hold';r['reconciliation']=put('bad-settlement.json',v)
 elif defect in ('owning-code','resolver'):
  m=L.checked(p['implementation']);row=p['publisher']if defect=='owning-code'else m['dependencies']['fresh'];q=tmp_path/'changed.py';s=Path(row['path']).read_text();q.write_text(s.replace('def controlled(', 'def changed_controlled(')if defect=='owning-code'else s.replace("return job['config']","return {}"));new=L.binding(q)
  if defect=='owning-code':p['publisher']=m['publisher']=new
  else:m['dependencies']['fresh']=new
  p['implementation']=put('bad-manifest.json',m)
 elif defect=='dependency':
  m=L.checked(p['implementation']);m['dependencies']['new']={'path':'/foreign','sha256':'0'*64};p['implementation']=put('bad-manifest.json',m)
 p['preowning_recovery']=put('recovery.json',r)
 if defect:
  with pytest.raises((ValueError,KeyError)):L.preowning(p,plan,L.checked,L.binding)
 else:assert L.preowning(p,plan,L.checked,L.binding)[1]['owning_intent_exists']is False

def test_config_repair_refuses_second_application():
 m=L.checked(L.binding(H/'implementation-003/manifest.json'))
 with pytest.raises(ValueError):L.ready_config_repair(Path(m['publisher']['path']).read_text(),Path(m['dependencies']['fresh']['path']).read_text(),m['dependencies']['fresh'],m['dependencies']['fresh'])
