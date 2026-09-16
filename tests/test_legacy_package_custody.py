"""Actual retained 0043 package is source-only; no historical calls are reset."""
from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_research_history as L
ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-legacy-build-001/0043-package-history.json'

@pytest.fixture
def actual():return json.loads(PROFILE.read_text())

def test_actual_closed_package_keeps_restored_instruction_and_reserved_attempt(actual):
 before={r:L.inventory(r)for r in actual['completed_package']['namespaces']}
 assert L.completed_package(actual)['naturalization_reservations_upper_bound']==2
 package=Path(actual['completed_package']['package']);nat=next(r for r in before if r.endswith('instruction-naturalization'))
 state=L.read(before[nat]['state.json'])
 assert before[str(package)]['instruction.md']['sha256']==state['source_sha256']
 assert state['source_sha256']!=state['final_instruction_sha256']
 assert len(state['attempts'])==1 and any(n.startswith('attempt-02/')for n in before[nat])
 assert before=={r:L.inventory(r)for r in before}

@pytest.mark.parametrize('defect',['missing_namespace','new_file','changed_byte','reset_nat','authoring','foreign_generation','invalid_disclosure'])
def test_package_custody_rejects_loss_reset_and_quality_adoption(actual,defect):
 p=copy.deepcopy(actual);v=p['completed_package'];real_read=L.read
 if defect=='missing_namespace':v['namespaces'].pop(next(iter(v['namespaces'])))
 elif defect=='new_file':v['namespaces'][v['package']]['invented.json']={'path':'/absent','sha256':'0'*64}
 elif defect=='changed_byte':v['namespaces'][v['package']]['instruction.md']['sha256']='0'*64
 elif defect=='reset_nat':v['naturalization_reservations_upper_bound']=1
 elif defect=='authoring':v['new_authoring_authorized']=True
 def changed(row):
  x=real_read(row)
  if defect=='foreign_generation'and row['path'].endswith('pipeline_bindings.json'):x={**x,'source_generation_id':'other'}
  if defect=='invalid_disclosure'and '/disclosure/'in row['path']and row['path'].endswith('verify.json'):x={**x,'passed':False}
  return x
 with patch.object(L,'read',side_effect=changed),pytest.raises(ValueError):L.completed_package(p)

def test_source_only_package_cannot_dispatch_new_research(actual):
 with patch.object(L,'validate',return_value=actual),pytest.raises(ValueError,match='source reconstruction only'):
  L.budget({},[],{},workbook='0043',inherited_budget=None)

@pytest.fixture
def author(actual):
 p=copy.deepcopy(actual);p['schema_version']=L.PACKAGE_AUTHOR_SCHEMA
 h=PROFILE.parent/'0043-source-001';mapping=json.loads((h/'source-custody/mapping.json').read_text())
 result=next(x['local']for x in mapping if x['original']['path'].endswith('/0043/source/result.json'))
 p['authoring']=dict(source_authority=L.binding(PROFILE),source_result=result,snapshot=L.binding(PROFILE.parent/'implementation-package-001/snapshot.json'),new_nat_maximum_attempts=2,cumulative_nat_upper_bound=4,new_dialogue_reviews=2)
 return p

def test_author_successor_keeps_all_prior_bytes_and_appends_nat_allowance(author):
 assert L.package_authoring(author)['cumulative_nat_upper_bound']==4
 excluded,histories=L.author_survey(author)
 assert author['workbook']not in excluded and author['workbook']in author['excluded']
 assert excluded=={k:v for k,v in author['excluded'].items()if k!=author['workbook']}
 assert histories==author['histories']

@pytest.mark.parametrize('defect',['reset_nat','extra_nat','extra_review','prior_bytes','prior_research','source_result','nonterminal','source_authority'])
def test_author_successor_refuses_budget_reset_or_foreign_source(author,defect):
 p=copy.deepcopy(author);real=L.read
 if defect=='reset_nat':p['authoring']['cumulative_nat_upper_bound']=2
 elif defect=='extra_nat':p['authoring']['new_nat_maximum_attempts']=3
 elif defect=='extra_review':p['authoring']['new_dialogue_reviews']=3
 elif defect=='prior_bytes':p['completed_package']['namespaces'][p['completed_package']['package']]['instruction.md']['sha256']='0'*64
 elif defect=='prior_research':p['retained_opaque_research_calls']=0
 elif defect=='source_authority':p['authoring']['source_authority']['sha256']='0'*64
 def changed(row):
  v=real(row)
  if row==p['authoring']['source_result']:
   if defect=='source_result':v={**v,'legacy_research':{'path':'/other','sha256':'0'*64}}
   if defect=='nonterminal':v={**v,'status':'RECONSTRUCTION_HELD'}
  return v
 with patch.object(L,'read',side_effect=changed),pytest.raises(ValueError):L.package_authoring(p)
