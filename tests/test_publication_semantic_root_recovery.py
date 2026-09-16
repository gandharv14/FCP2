from pathlib import Path
import json
import pytest
from scripts.fleet import publication_launch as L,fresh_publication as F
H=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/0095-held-diagnosis-001/publication-completed-003'

@pytest.fixture
def case(tmp_path):
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return L.binding(p)
 oldrow=L.binding(H/'root-launch-003/profile.json');old=L.checked(oldrow)
 r=dict(schema_version='fleet-publication-semantic-root-recovery/v1',prior_profile=oldrow,root_intent=L.binding(H/'root-launch-003/launch-001/intent.json'),failure_proof=L.binding(H/'preowning-reconciliation-001/native.json'),reconciliation=L.binding(H/'preowning-reconciliation-001/completed.json'))
 m=L.binding(H/'implementation-004/manifest.json');manifest=L.checked(m)
 p=dict(old,expected_owner=old['owner'],expected_fence=old['expected_fence']+1,owner='next-publisher',publisher=manifest['publisher'],implementation=m)
 plan=L.checked(old['plan']);plan['journal']+='/successor'
 return p,plan,r,put

@pytest.mark.parametrize('defect',[None,'tracker','process','owning-intent','extra-file','wrong-failure','changed-work','wrong-owner','unsettled','owning-code','resolver','dependency'])
def test_exact_actual_root_failure_and_closed_repair(case,defect,tmp_path):
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
  m=L.checked(p['implementation']);row=p['publisher']if defect=='owning-code'else m['dependencies']['fresh'];q=tmp_path/'changed.py';s=Path(row['path']).read_text();q.write_text(s.replace('def controlled(', 'def changed_controlled(')if defect=='owning-code'else s.replace("return root","return Path('/foreign')"));new=L.binding(q)
  if defect=='owning-code':p['publisher']=m['publisher']=new
  else:m['dependencies']['fresh']=new
  p['implementation']=put('bad-manifest.json',m)
 elif defect=='dependency':
  m=L.checked(p['implementation']);m['dependencies']['new']={'path':'/foreign','sha256':'0'*64};p['implementation']=put('bad-manifest.json',m)
 p['preowning_recovery']=put('recovery.json',r)
 if defect:
  with pytest.raises((ValueError,KeyError)):L.preowning(p,plan,L.checked,L.binding)
 else:assert L.preowning(p,plan,L.checked,L.binding)[1]['owning_intent_exists']is False

def test_semantic_root_repair_refuses_second_application():
 m=L.checked(L.binding(H/'implementation-004/manifest.json'))
 with pytest.raises(ValueError):L.semantic_root_repair(Path(m['publisher']['path']).read_text(),Path(m['dependencies']['fresh']['path']).read_text(),m['dependencies']['fresh'],m['dependencies']['fresh'])

@pytest.mark.parametrize('change',[None,'history','source','snapshot','config','schema','version','relative'])
def test_selected_root_requires_authenticated_retained_source(change):
 from types import SimpleNamespace as NS
 rows={'p':dict(schema_version='fleet-instruction-semantic-continuation/v1',retained_context=dict(kind='completed_disclosure',plan='q',snapshot='snapshot',config='config',helper='helper'),fresh_plan='q',workbook='0095',source={'sha256':'source'}),
  'q':dict(schema_version='fleet-retained-completed-disclosure/v1',snapshot='snapshot',config='config',helper='helper',workbook='0095',source={'sha256':'source'}),'snapshot':{'root':'/selected/current'}}
 plan=dict(code_root='/original/prior',semantic_continuation=dict(plan='p',completed_reader='reader'),fresh_authoring='q',workbook='0095',source_sha256='source')
 def history(pub,plan):
  if change=='history':raise ValueError('history rejected')
 if change=='source':rows['q']['source']={'sha256':'different'}
 elif change=='snapshot':rows['q']['snapshot']='foreign'
 elif change=='config':rows['q']['config']='foreign'
 elif change=='schema':rows['q']['schema_version']='foreign'
 elif change=='relative':rows['snapshot']['root']='current'
 M=NS(history=history,version=lambda p,r:'different'if change=='version'else'current')
 pub=NS(load=lambda n,r:M,checked=lambda r:rows[r],require=L.require)
 if change:
  with pytest.raises(ValueError):F.owning_root(pub,plan)
 else:assert F.owning_root(pub,plan)==Path('/selected/current')
 assert F.owning_root(pub,{'code_root':'/original/prior'})==Path('/original/prior')


def test_serialized_transform_matches_shared_resolver():
 import inspect,ast
 ns={};exec(inspect.getsource(L.semantic_root_repair),ns)
 old={'path':'/old','sha256':'old'};new={'path':'/new','sha256':'new'}
 pub='env[6] == root and job["config"] == fresh_publication.ready_config(sys.modules[__name__], plan, ready, job)\n'+repr(old)+'\n'+repr(old)
 changed,fresh=ns['semantic_root_repair'](pub,'',old,new)
 assert ast.dump(ast.parse(fresh),include_attributes=False)==ast.dump(ast.parse(inspect.getsource(F.owning_root)),include_attributes=False)
 assert changed.count('fresh_publication.owning_root')==1
