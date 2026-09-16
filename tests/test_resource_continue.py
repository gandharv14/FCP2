import copy,tempfile,unittest
from pathlib import Path
from scripts.fleet import resource_continue as R
class Boundary(unittest.TestCase):
 def setUp(self):
  self.p=dict(workbook='0145',source={'sha256':'a'},previous_attempt=1,next_attempt=2,cumulative_reconstruction_limit=2,generation='g',seconds=7200,memory_bytes=16*1024**3,models=0,automatic_retry=False)
  self.l=dict(workbook_id='0145',source={'sha256':'a'},state='READY',route='restricted_pass',build_history=[],build=dict(state='STOPPED',stage='segmentation',exit_code=124,resources={'limit_reason':'wall_time'},attempts=1,source_generation_id='g',source_checkpoint={'state':'complete'}))
  self.a=dict(limits=dict(stage_seconds=7200,memory_bytes=16*1024**3))
 def test_bound_resource_successor(self):R.validate_boundary(self.p,self.l,self.a)
 def test_reject_other_failure_and_reset(self):
  mutations=[('p','next_attempt',1),('p','cumulative_reconstruction_limit',1),('p','models',1),('p','automatic_retry',True),('p','seconds',7201),('p','memory_bytes',17*1024**3),('p','generation','other'),('l','source',{'sha256':'b'}),('l','build_history',[{}])]
  for which,key,value in mutations:
   with self.subTest(key=key):
    p,l=copy.deepcopy(self.p),copy.deepcopy(self.l);(p if which=='p'else l)[key]=value
    with self.assertRaises(ValueError):R.validate_boundary(p,l,self.a)
  for key,value in [('state','VERIFIED'),('stage','source'),('exit_code',1),('attempts',2),('resources',{'limit_reason':'semantic'})]:
   with self.subTest(key=key):
    l=copy.deepcopy(self.l);l['build'][key]=value
    with self.assertRaises(ValueError):R.validate_boundary(self.p,l,self.a)
 def test_cumulative_reservation_no_lost_response_replay(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d).resolve()/'reconstruction-0145-000002.json';R.keep(p,dict(ordinal=2,plan='one'))
   with self.assertRaises(FileExistsError):R.keep(p,dict(ordinal=2,plan='another-home'))
 def test_clone_preserves_original_ledger_and_source(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();old=root/'old';(old/'workbooks/0145').mkdir(parents=True);(old/'workbooks/0145/ledger.json').write_text('{"attempts":1}');(old/'workbooks/0145/build.log').write_text('retained');(old/'source').write_bytes(b'ast')
   closure={'files':R.inventory(old)};p={'home':str(root/'new')};dest=R.clone(p,closure)
   self.assertEqual(R.inventory(old),closure['files']);self.assertEqual((dest/'source').read_bytes(),b'ast');self.assertFalse((dest/'workbooks/0145/build.log').exists());self.assertEqual((dest/'workbooks/0145/ledger.json').read_bytes(),(old/'workbooks/0145/ledger.json').read_bytes())
   with self.assertRaises(ValueError):R.clone(p,closure)

class MacroBoundary(Boundary):
 def setUp(self):
  super().setUp();self.p['macro_projection_policy']={'path':'selected-policy','sha256':'p'}
  self.l.update(state='STOPPED',route='unsupported',failure='source_policy_unsupported',reason_codes=['macros_present'])
  self.l['build'].update(normalization={'policy_version':'source-vba-only-projection/v1'},bindings={'macro_projection_policy_sha256':'p'})
 def test_macro_rejects_unrelated_source_barrier(self):
  self.l['reason_codes'].append('external_links_present')
  with self.assertRaises(ValueError):R.validate_boundary(self.p,self.l,self.a)
 def test_macro_rejects_changed_policy(self):
  self.p['macro_projection_policy']['sha256']='different'
  with self.assertRaises(ValueError):R.validate_boundary(self.p,self.l,self.a)
 def test_macro_requires_exact_normalized_source(self):
  self.l['build']['normalization']['policy_version']='foreign'
  with self.assertRaises(ValueError):R.validate_boundary(self.p,self.l,self.a)
