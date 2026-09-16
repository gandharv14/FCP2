from pathlib import Path
import copy,json
from unittest.mock import patch
import pytest
from scripts.batch import harbor_legacy_research_history as L,harbor_legacy_staged_history as S
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'diagnostics/vm-fleet-20260912/control/source-pool-legacy-staged-001'

@pytest.fixture(params=['0040','0161'])
def actual(request):return json.loads((H/(request.param+'-history.json')).read_text())

def test_actual_staged_source_preserves_every_namespace(actual):
 row=L.binding(H/(actual['workbook']+'-history.json'))
 assert S.validate(row)==actual and L.validate(row)==actual
 assert actual['staged_history']['naturalization_reservations_upper_bound']==2
 for name,files in actual['staged_history']['namespaces'].items():assert S.inventory(name)==files

@pytest.mark.parametrize('defect',['baseline','source','namespace','nat_reset','dialogue_reset','authoring','terminal','model_terminal','new_file','curation'])
def test_staged_source_rejects_changed_history(actual,tmp_path,defect):
 p=copy.deepcopy(actual);real=L.read
 if defect=='baseline':p['baseline']['sha256']='0'*64
 elif defect=='source':p['original']['sha256']='0'*64
 elif defect=='namespace':p['staged_history']['namespaces'].pop(next(iter(p['staged_history']['namespaces'])))
 elif defect=='nat_reset':p['staged_history']['naturalization_reservations_upper_bound']=0
 elif defect=='dialogue_reset':p['staged_history']['dialogue_rounds_upper_bound']=0
 elif defect=='authoring':p['staged_history']['new_authoring_authorized']=True
 elif defect=='new_file':p['audit_files']['invented']={'path':'/absent','sha256':'0'*64}
 elif defect=='curation':p['curations'][0]['sha256']='0'*64
 selected=tmp_path/'proof.json';selected.write_text(json.dumps(p));bound=L.binding(selected)
 def read(row):
  v=real(row)
  if defect=='terminal'and row==p['tracker']:v={**v,'status':'running'}
  if defect=='model_terminal'and row['path'].endswith('.metadata.json'):v={**v,'finish_reasons':['length']}
  return v
 with patch.object(L,'read',side_effect=read),pytest.raises(ValueError):S.validate(bound)

def test_staged_source_has_no_model_authority(actual):
 with patch.object(L,'validate',return_value=actual),pytest.raises(ValueError,match='source reconstruction only'):
  L.budget({},[],{},workbook=actual['workbook'],inherited_budget=None)
 with pytest.raises(ValueError,match='source-only'):L.author_survey(actual)

@pytest.fixture
def author(actual):
 p=copy.deepcopy(actual);wb=p['workbook'];mapping=json.loads((H/(wb+'-source-custody')/'mapping.json').read_text());result=next(r['local']for r in mapping if r['original']['path'].endswith('/'+wb+'/source/result.json'))
 p['schema_version']=S.AUTHOR_SCHEMA;p['authoring']=dict(source_authority=L.binding(H/(wb+'-history.json')),source_result=result,snapshot=L.binding(H/'implementation/snapshot.json'),new_nat_maximum_attempts=2,cumulative_nat_upper_bound=p['staged_history']['naturalization_reservations_upper_bound']+2,new_dialogue_reviews=2,cumulative_dialogue_reviews_upper_bound=p['staged_history']['dialogue_rounds_upper_bound']+2)
 return p

def test_actual_source_completion_allows_distinct_author_with_all_consumption(author,tmp_path):
 path=tmp_path/'author.json';path.write_text(json.dumps(author));assert L.validate(L.binding(path))==author
 excluded,histories=L.author_survey(author)
 assert excluded=={k:v for k,v in author['excluded'].items()if k!=author['workbook']}
 assert histories==author['histories']
 assert author['authoring']['cumulative_nat_upper_bound']==4
 assert author['authoring']['cumulative_dialogue_reviews_upper_bound']in(3,4)

@pytest.mark.parametrize('defect',['reset_nat','reset_dialogue','extra_nat','extra_review','old_bytes','old_calls','old_authority','foreign_completion','nonterminal','source_plan','snapshot'])
def test_staged_author_requires_completed_new_source_and_append_only_budget(author,defect):
 p=copy.deepcopy(author);a=p['authoring'];real=L.read
 if defect=='reset_nat':a['cumulative_nat_upper_bound']=2
 elif defect=='reset_dialogue':a['cumulative_dialogue_reviews_upper_bound']=2
 elif defect=='extra_nat':a['new_nat_maximum_attempts']=3
 elif defect=='extra_review':a['new_dialogue_reviews']=3
 elif defect=='old_bytes':p['staged_history']['namespaces'][p['staged_history']['package']]['instruction.md']['sha256']='0'*64
 elif defect=='old_calls':p['retained_opaque_research_calls']=0
 elif defect=='old_authority':a['source_authority']['sha256']='0'*64
 def changed(row):
  v=real(row)
  if row==a['source_result']:
   if defect=='foreign_completion':v={**v,'legacy_research':{'path':'/other','sha256':'0'*64}}
   if defect=='nonterminal':v={**v,'status':'RECONSTRUCTION_HELD'}
  if defect=='source_plan'and row['path'].endswith('source-plan.json'):v={**v,'home':'/another/source'}
  if defect=='snapshot'and row==a['snapshot']:v={**v,'fingerprint':'0'*64}
  return v
 with patch.object(L,'read',side_effect=changed),pytest.raises(ValueError):S.authoring(p)
