import ast,copy,json
from pathlib import Path
import pytest
from scripts.fleet import launch_job as OLD,launch_job_v2 as L
from scripts.fleet.resource_continue import binding

def test_mutating_intent_and_spawn_functions_unchanged():
 for name in ['launch','remote_spawn','ssh']:
  assert ast.dump(ast.parse(OLD.function_source(name)))==ast.dump(ast.parse(L.function_source(name)))

def fixture():
 p=Path('diagnostics/vm-fleet-20260912/control/resource-continuation/0145/launch-plan.json').resolve()
 return binding(p),L.checked(binding(p))

def test_actual_pool_completion_load():
 b,p=fixture();loaded,job=L.load(b);assert loaded['prior_kind']=='pool-completion'and job['workbook']=='0145'

@pytest.mark.parametrize('change',['completion_job','completion_outcome','runtime_python','runtime_worker','resource_probe','owner'])
def test_actual_pool_custody_and_runtime_tamper_refused(monkeypatch,change):
 b,p=fixture();original=L.checked
 def checked(row):
  v=copy.deepcopy(original(row))
  if row==p['prior_process_copy']:
   if change=='completion_job':v['job']['sha256']='f'*64
   if change=='completion_outcome':v['outcome']['sha256']='f'*64
  if row==p['runtime']:
   if change=='runtime_python':v['python']='/other/python'
   if change=='runtime_worker':v['files']['scripts/fleet/worker.py']['sha256']='f'*64
  if row==p['probe']and change=='resource_probe':v['result']['plan']['sha256']='f'*64
  if row==b and change=='owner':v['expected_owner']='foreign'
  return v
 monkeypatch.setattr(L,'checked',checked)
 with pytest.raises(ValueError):L.load(b)

def test_truthful_none_policy_keeps_global_uncertainty_barrier():
 from scripts.fleet.capacity_policy import AUTHORIZATION_SHA256,CHANGE_ID
 b,p=fixture();job=L.checked(p['job'])
 row=dict(workbook=job['workbook'],state='hold',fence=p['expected_fence'],owner=p['expected_owner'],source_sha256=job['source']['sha256'],version=p['expected_version'],reason=json.dumps(p['prior_outcome']))
 status=dict(model_limit=None,publication_limit=1,operations=[],workbooks=[row],model_policy=dict(schema_version='fleet-concurrency-policy/v1',revision=1,model_limit=None,publication_limit=1,per_workbook_model_limit=1,unknown_model_hold_barrier='global',change_id=CHANGE_ID,authorization=dict(path='/root/authorization.json',sha256=AUTHORIZATION_SHA256)))
 assert L.central_gate(status,p,job)==row
 status['operations']=[dict(kind='model',state='hold',workbook='0006')]
 with pytest.raises(ValueError):L.central_gate(status,p,job)

def test_other_active_model_can_overlap_launch_but_target_and_holds_cannot(monkeypatch):
 b,p=fixture();job=L.checked(p['job']);row=dict(workbook=job['workbook'],state='hold',fence=p['expected_fence'],owner=p['expected_owner'],source_sha256=job['source']['sha256'],version=p['expected_version'],reason=json.dumps(p['prior_outcome']))
 seen=[]
 monkeypatch.setattr('scripts.fleet.capacity_policy.validate_occupancy',lambda s:seen.append(s))
 status=dict(workbooks=[row],operations=[dict(kind='model',state='active',workbook='OTHER')])
 assert L.central_gate(status,p,job)==row and seen==[status]
 for operation in [dict(kind='model',state='hold',workbook='OTHER'),dict(kind='model',state='active',workbook=job['workbook']),dict(kind='publication',state='active',workbook=job['workbook'])]:
  with pytest.raises(ValueError):L.central_gate(dict(status,operations=[operation]),p,job)
