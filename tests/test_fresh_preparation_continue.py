import json,tempfile,unittest
from pathlib import Path
from scripts.fleet import fresh_preparation_continue as F

class PreparationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
 def keep(self,name,value):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return F.binding(p)
 def test_namespace_only(self):
  old=dict(schema_version='harbor-fleet-fresh-plan/v1',reconstruction=dict(mode='existing',attempt=1),home='/native/old',owner='owner',allowance=2)
  F.validate_namespace(old,dict(old,home='/native/new'),'/native/new')
  for delta in [dict(owner='new'),dict(allowance=3),dict(reconstruction=dict(mode='new',attempt=2))]:
   with self.subTest(delta=delta),self.assertRaises(ValueError):F.validate_namespace(old,dict(old,home='/native/new',**delta),'/native/new')
  for home in ['/native','/native/old','/native/old/new','relative']:
   with self.subTest(home=home),self.assertRaises(ValueError):F.validate_namespace(old,dict(old,home=home),home)
 def test_partial_refuses_every_task_effect(self):
  p=self.root/'partial';p.mkdir()
  for name in ['frozen-code.json','survey.json','candidate.json','dependency.json']:(p/name).write_text('{}')
  F.validate_partial(p)
  for name in ['current-validation.json','bootstrap/prepared.json','trackers/workbooks/1.json','model-campaign/attempt/request.json']:
   q=p/name;q.parent.mkdir(parents=True,exist_ok=True);q.write_text('{}')
   with self.subTest(name=name),self.assertRaises(ValueError):F.validate_partial(p)
   q.unlink()
 def test_partial_refuses_symlink(self):
  p=self.root/'partial';p.mkdir()
  for name in ['frozen-code.json','survey.json','candidate.json','dependency.json']:(p/name).write_text('{}')
  (p/'link').symlink_to(p/'candidate.json')
  with self.assertRaises(ValueError):F.validate_partial(p)
 def fixture_transfer(self):
  leaf=self.keep('leaf.json',dict(source='original'));entry=dict(leaf,size=Path(leaf['path']).stat().st_size)
  manifest=self.keep('manifest.json',dict(files=[entry],historical_bytes_rewritten=False,qualification_claimed=False));response=self.keep('response.json',dict(status='IMPORTED'))
  row=self.keep('transfer.json',dict(source_host='vm4',destination_host='vm5',source_reconstruction=False,manifest=manifest,response=response,files=1));return row,leaf,entry
 def test_exact_transfer_and_changed_leaf(self):
  row,leaf,_=self.fixture_transfer();F.validate_transfer(row);Path(leaf['path']).write_text('changed')
  with self.assertRaises(ValueError):F.validate_transfer(row)
 def test_transfer_duplicate_and_missing(self):
  row,leaf,entry=self.fixture_transfer();t=F.read(row)
  for files in [[],[entry,entry]]:
   m=self.keep('manifest2.json',dict(files=files,historical_bytes_rewritten=False,qualification_claimed=False));r=self.keep('transfer2.json',dict(t,manifest=m,files=len(files)))
   with self.subTest(files=files),self.assertRaises(ValueError):F.validate_transfer(r)
 def test_parent_symlink_rejected(self):
  real=self.root/'real';real.mkdir();(real/'evidence').write_text('original');(self.root/'alias').symlink_to(real,target_is_directory=True)
  with self.assertRaises(ValueError):F.binding(self.root/'alias/evidence')

if __name__=='__main__':unittest.main()
