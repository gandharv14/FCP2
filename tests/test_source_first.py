import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.fleet import source_first as S,launch_source_initial as L
class InitialTests(unittest.TestCase):
 def test_history_is_not_catalog_label(self):
  v=dict(schema_version='fleet-source-initial-history/v1',roots=['/actual'],known_ids=['0010'],excluded=[],histories={})
  S.history(v,'0010',['/actual'])
  for changed in [dict(v,excluded=['0010']),dict(v,histories={'0010':['retained ledger']}),dict(v,roots=['/fake']),dict(v,known_ids=[])]:
   with self.subTest(changed=changed),self.assertRaises(ValueError):S.history(changed,'0010',['/actual'])
 def test_existing_initial_hold_cannot_be_reenrolled(self):
  status=dict(operations=[],workbooks=[])
  with patch('scripts.fleet.capacity_policy.validate_occupancy'):
   self.assertEqual(L.central_gate(status,{},dict(workbook='0010',source={'sha256':'a'*64}))['state'],'ABSENT')
   for state in ['hold','active','complete','ready']:
    with self.subTest(state=state),self.assertRaises(ValueError):L.central_gate(dict(status,workbooks=[dict(workbook='0010',state=state)]),{},dict(workbook='0010'))
 def test_no_history_of_calls_can_start_fresh(self):
  with patch('scripts.fleet.capacity_policy.validate_occupancy'):
   for op in [dict(workbook='0010',state='confirmed'),dict(workbook='OTHER',state='hold')]:
    with self.assertRaises(ValueError):L.central_gate(dict(workbooks=[],operations=[op]),{},dict(workbook='0010'))
 def test_unrelated_active_model_is_not_global_cap(self):
  with patch('scripts.fleet.capacity_policy.validate_occupancy'):
   L.central_gate(dict(workbooks=[],operations=[dict(workbook='OTHER',state='active')]),{},dict(workbook='0010',source={'sha256':'a'*64}))
 def test_reservation_is_exclusive(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'ordinal.json';S.keep(p,dict(ordinal=1))
   with self.assertRaises(FileExistsError):S.keep(p,dict(ordinal=1))
 def test_original_spawn_and_ssh_unchanged(self):
  import ast
  from scripts.fleet import launch_job_v2 as V
  def node(module,name):return ast.dump(next(n for n in ast.parse(Path(module.__file__).read_text()).body if isinstance(n,ast.FunctionDef)and n.name==name),include_attributes=False)
  for name in ['remote_spawn','ssh']:
   with self.subTest(name=name):self.assertEqual(node(L,name),node(V,name))
if __name__=='__main__':unittest.main()

class OwningBoundaryTests(unittest.TestCase):
 def test_initial_counter_and_builder_route(self):
  import os,json,types
  with tempfile.TemporaryDirectory()as d:
   root=Path(d).resolve();home=root/'attempt';source=dict(workbook_id='0010',sha256='a'*64,path=str(root/'original.xlsx'));p=dict(schema_version='fleet-source-first/v1',next_attempt=1,workbook='0010',source=source,home=str(home),allowance={'path':str(root/'allowance/auth.json')},inventory={'path':str(root/'inventory.json')},seconds=7200,memory_bytes=16*1024**3);snap=dict(root=str(root),fingerprint='b'*64);calls=[]
   def screen(source,run,fingerprint,timeout):
    self.assertEqual(timeout,300);return dict(state='STOPPED',reason_codes=['data_tables_present'])
   def build(args,source,fingerprint):
    calls.append(args);path=Path(args.run_root)/'workbooks/0010/ledger.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(dict(build={'attempts':1},build_history=[])));return dict(state='VERIFIED',attempts=1)
   client=types.SimpleNamespace(call=lambda *a,**k:dict(source_sha256='a'*64,version='b'*64,fence=1))
   env=dict(HARBOR_FLEET_WORKBOOK='0010',HARBOR_FLEET_URL='unused',HARBOR_FLEET_TOKEN_FILE='unused',HARBOR_FLEET_OWNER='owner',HARBOR_FLEET_CLAIM_TOKEN='token',HARBOR_FLEET_FENCE='1')
   old=os.getcwd()
   try:
    with patch.object(S,'load',return_value=(p,snap,types.SimpleNamespace(screen_one=screen),types.SimpleNamespace(execute_one=build))),patch('scripts.fleet.coordinator.Client',return_value=client),patch.dict(os.environ,env):
     S.run(dict(path='selected',sha256='c'*64))
    self.assertEqual(len(calls),1);self.assertEqual((calls[0].timeout,calls[0].memory_mb),(7200,16384));self.assertIsNone(calls[0].source_inventory)
    self.assertTrue((home/'result.json').exists());self.assertTrue((root/'allowance/reconstruction-0010-000001.json').exists())
   finally:os.chdir(old)

class RetainedCurationTests(unittest.TestCase):
 def fixture(self,root):
  a=root/'old/curation.toml';a.parent.mkdir();a.write_bytes(b'# retained\r\n[outputs]\r\ncells = ["Model!A1"]\r\n')
  proof=S.keep(root/'history.json',dict(workbook='0289',curations=[S.binding(a)]))
  return a,proof
 def test_exact_bytes_and_actual_builder_selection(self):
  from types import SimpleNamespace
  from scripts.corpus_build import retained_curation
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();a,proof=self.fixture(root);dest=root/'new/initial-curations'
   row=S.retain_legacy_curation(proof,'0289',dest);out=dest/'0289/curation.toml'
   self.assertEqual(a.read_bytes(),out.read_bytes());self.assertEqual(S.read(row)['selected'],S.binding(out))
   args=SimpleNamespace(curation_root=str(dest),run_root=str(root/'new'),cache_equivalence_policy=None)
   self.assertEqual(retained_curation(args,{'workbook_id':'0289'},{}),out)
   with self.assertRaises(FileExistsError):S.retain_legacy_curation(proof,'0289',dest)
 def test_mutated_old_bytes_rejected_before_copy(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();a,p=self.fixture(root);a.write_text('changed')
   with self.assertRaises(ValueError):S.retain_legacy_curation(p,'0289',root/'new')
   self.assertFalse((root/'new').exists())
 def test_symlink_destination_and_foreign_workbook_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();a,p=self.fixture(root);dest=root/'alias';dest.symlink_to(a.parent,target_is_directory=True)
   for wb,target in [('other',root/'new'),('0289',dest)]:
    with self.assertRaises(ValueError):S.retain_legacy_curation(p,wb,target)
