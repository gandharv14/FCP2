import importlib.util
from pathlib import Path
from unittest import TestCase,mock
R=Path(__file__).resolve().parents[1]
def mod(n):
 s=importlib.util.spec_from_file_location(n,R/'scripts/fleet'/f'{n}.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
C=mod('retained_environment_continue');N=mod('retained_environment_namespace')
class ImportBoundary(TestCase):
 def fixture(self,w='0190'):
  b=lambda name:dict(path='/'+name,sha256='a'*64)
  plan=dict(workbook=w,version='v',source_sha256='s',prior_worker_outcome=b('outer'),prior_worker_job=b('job'),import_result=b('import'),config=b('config'))
  desc=b('descriptor');checkpoint=b('checkpoint')
  v=dict(status='IMPORTED',step='normalization_phase1' if w=='0190' else 'source',descriptor=desc,model_calls=0,semantic_budget_reset=False,emission_replayed=False,publication_ready=False,new_admission=False,config=b('config'),checkpoint=checkpoint)
  rows={'/outer':dict(schema_version='harbor-fleet-worker-outcome/v1',intent=b('intent'),returncode=0,failure_type=None,automatic_retry=False,result=b('import')),'/intent':dict(job=b('job')),'/job':dict(workbook=w,source=dict(sha256='s'),version='v'),'/import':v,'/checkpoint':v}
  return plan,rows
 def check(self,p,r):
  with mock.patch.object(C,'checked',side_effect=lambda b:r[b['path']]):return C.completed_import(p)
 def test_real_0190_import_preserves_remaining_boundary(self):
  p,r=self.fixture();self.assertEqual(self.check(p,r)[0],p['import_result'])
 def test_real_0006_source_import_distinct_checkpoint(self):
  p,r=self.fixture('0006');self.assertEqual(self.check(p,r)[0]['path'],'/checkpoint')
 def test_failed_or_uncertain_outer_not_ordinary_success(self):
  for k,v in [('returncode',1),('failure_type','Timeout'),('automatic_retry',True)]:
   p,r=self.fixture();r['/outer'][k]=v
   with self.assertRaises(ValueError):self.check(p,r)
 def test_consumed_model_or_budget_reset_fails(self):
  for k,v in [('model_calls',1),('semantic_budget_reset',True),('emission_replayed',True)]:
   p,r=self.fixture();r['/import'][k]=v
   with self.assertRaises(ValueError):self.check(p,r)
 def test_detached_worker_result_fails(self):
  p,r=self.fixture();r['/outer']['result']=dict(path='/other',sha256='b'*64)
  with self.assertRaises(ValueError):self.check(p,r)
 def test_wrong_original_source_code_or_workbook_fails(self):
  for mutate in [lambda j:j.update(workbook='0270'),lambda j:j.update(version='new'),lambda j:j.update(source=dict(sha256='other'))]:
   p,r=self.fixture();mutate(r['/job'])
   with self.assertRaises(ValueError):self.check(p,r)
 def test_wrong_import_stage_fails(self):
  p,r=self.fixture();r['/import']['step']='normalization_phase2'
  with self.assertRaises(ValueError):self.check(p,r)
 def test_probe_never_calls_run(self):
  with mock.patch.dict(C.os.environ,{},clear=True),mock.patch.object(C,'load') as load:
   with self.assertRaises(ValueError):C.probe({})
   load.assert_not_called()
 def test_readonly_cannot_execute(self):
  with mock.patch.dict(C.os.environ,{'HARBOR_FLEET_CONTINUATION_READ_ONLY':'1'}),mock.patch.object(C,'load') as load:
   with self.assertRaises(ValueError):C.run({})
   load.assert_not_called()
class ClosedHistory(TestCase):
 def test_only_reviewed_two_workbooks(self):self.assertEqual(set(N.PROFILES),{'0006','0190'})
 def test_source_code_or_publisher_change_fails_before_loading(self):
  for key in ['source_sha256','version','publisher']:
   p=dict(schema_version='harbor-fleet-retained-environment-continuation/v1',workbook='0190',source_sha256=N.PROFILES['0190']['source'],version=N.PROFILES['0190']['version'],publisher=N.PROFILES['0190']['publisher']);p[key]='wrong'
   with mock.patch.dict(N.os.environ,{'HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN':'/p','HARBOR_FLEET_RETAINED_ENVIRONMENT_PLAN_SHA256':'a'}),mock.patch.object(N,'checked',return_value=p),mock.patch.object(N,'module') as loader:
    with self.assertRaises(ValueError):N.context()
    loader.assert_not_called()
class OriginalDriverPreservation(TestCase):
 def test_frozen_adapter_preserves_every_owning_driver_function(self):
  import ast,json
  old=R/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py'
  home=R/'diagnostics/vm-fleet-20260912/control/retained-environment-authoring-001/adapter-001'
  tree=lambda p:{n.name:ast.dump(n,include_attributes=False) for n in ast.parse(p.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  a,b=tree(old),tree(home/'dispatcher.py');self.assertEqual(set(a),set(b))
  for name in a:
   if name!='history_authority':self.assertEqual(a[name],b[name],name)
  self.assertNotEqual(a['history_authority'],b['history_authority'])
 def test_same_3300_and_8gib_contract_and_zero_model_first_bridge(self):
  import ast
  source=(R/'scripts/fleet/retained_environment_continue.py').read_text();tree=ast.parse(source)
  calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='base' and n.func.attr=='run']
  self.assertEqual(len(calls),1);self.assertEqual(next(k.value.value for k in calls[0].keywords if k.arg=='dispatch'),False)
  self.assertIn('env[0].SECONDS==3300',source);self.assertIn('env[0].MEMORY==8*1024**3',source)
