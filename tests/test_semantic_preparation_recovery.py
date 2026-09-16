import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.fleet import semantic_preparation_recovery as R
from scripts.batch import harbor_dialogue_semantic_retry as S

class IntentRecovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.run=self.root/'instruction';self.out=self.run/'dialogue-semantic-retry-02';self.out.mkdir(parents=True)
  self.source=dict(path='/original.xlsx',sha256='raw');self.blocked=self.put('blocked.json',dict(status='DIALOGUE_REPAIR_REQUIRED'))
  self.before=self.put('before.json',dict(bindings=dict(original_raw_source=self.source),handoffs=[]))
  self.tracker=dict(path='/original/tracker.json',sha256=self.before['sha256']);self.selection=dict(tracker=self.tracker,workbook='0291')
  self.stop=self.put('stop.json',dict(selection=self.selection,before=self.before))
  self.takeover=self.put('complete.json',dict(stopped_owner_proof=self.stop));self.tp=self.put('takeover-profile.json',dict(selection=self.selection))
  self.original=self.put('instruction/dialogue-semantic-retry-02/intent.json',dict(blocked=self.blocked,tracker=self.tracker,code={'original':'bytes'}))
  auth=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/authorization-20260913.md'
  self.p=dict(schema_version=R.SCHEMA,helper=R.binding(R.__file__),original_module=R.binding(S.__file__),intent=self.original,takeover_profile=self.tp,takeover_completion=self.takeover,workbook='0291',source=self.source,authorization=R.binding(auth))
  self.worker=SimpleNamespace(run=self.run);self.patcher=patch.object(S,'_worker',return_value=SimpleNamespace(code=lambda w:{'original':'bytes'}));self.patcher.start();self.addCleanup(self.patcher.stop)
 def put(self,name,value):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,sort_keys=True)+'\n');return R.binding(p)
 def check(self):return R.validate(self.put('profile.json',self.p),S,self.worker,blocked=self.blocked,takeover=self.takeover)
 def test_original_intent_only_is_read_only(self):
  before=(self.out/'intent.json').read_bytes();self.check();self.assertEqual((self.out/'intent.json').read_bytes(),before);self.assertEqual(len(list(self.out.iterdir())),1)
 def test_owning_takeover_omits_only_null_controls_migration(self):
  self.p['takeover_profile']=self.put('takeover-profile.json',dict(selection=dict(self.selection,controls_migration=None)));self.check()
  self.p['takeover_profile']=self.put('takeover-profile.json',dict(selection=dict(self.selection,controls_migration={'path':'other'})))
  with self.assertRaises(ValueError):self.check()
 def test_any_later_artifact_forbids_replay(self):
  for name in ('permit.json','complete.json','archive','recovery-intent-001.json','unexpected'):
   with self.subTest(name=name):
    p=self.out/name;p.write_bytes(b'{}')
    with self.assertRaises(ValueError):self.check()
    p.unlink()
 def test_wrong_module_workbook_source_or_completion(self):
  for key,value in [('original_module',self.original),('workbook','other'),('source',dict(path='/other',sha256='raw')),('takeover_completion',self.tp)]:
   with self.subTest(key=key):
    old=copy.deepcopy(self.p);self.p[key]=value
    with self.assertRaises(ValueError):self.check()
    self.p=old
 def test_intent_cannot_select_a_different_review(self):
  self.p['intent']=self.put('instruction/dialogue-semantic-retry-02/intent.json',dict(blocked=self.tp,tracker=self.tracker,code={'original':'bytes'}))
  with self.assertRaises(ValueError):self.check()
 def test_original_code_cannot_change(self):
  self.p['intent']=self.put('instruction/dialogue-semantic-retry-02/intent.json',dict(blocked=self.blocked,tracker=self.tracker,code={'modified':'bytes'}))
  with self.assertRaises(ValueError):self.check()
 def test_stopped_tracker_must_be_actual_pre_takeover(self):
  self.put('before.json',dict(bindings=dict(original_raw_source=self.source),handoffs=[{'schema_version':S.SCHEMA}]))
  with self.assertRaises(ValueError):self.check()
 def test_changed_loaded_function_rejected(self):
  def substituted():pass
  with self.assertRaises(ValueError):R.adapted(substituted,{})
 def test_exact_owning_authorize_is_retained(self):
  fn=R.adapted(S.authorize,dict(_recovery_check=lambda x:None,_recovery_directory=lambda x:None,_recovery_intent=lambda *x:None))
  self.assertEqual(fn.__kwdefaults__,S.authorize.__kwdefaults__)
  for name in ('_failure','_archive','cas_mutate','_seal','_project'):
   def names(c):return set(c.co_names)|set().union(*(names(v)for v in c.co_consts if hasattr(v,'co_names')))
   self.assertIn(name,names(fn.__code__))
  self.assertIs(fn.__globals__['schema'],S.schema);self.assertIs(fn.__globals__['producer'],S.producer)
