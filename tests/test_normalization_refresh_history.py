import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from scripts.fleet import normalization_refresh_history as H

class HistoryTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
 def keep(self,path,value):
  p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return H.binding(p)
 def module(self):
  def read(r):
   H.require(H.binding(r['path'])==r,'changed live record');return json.loads(Path(r['path']).read_bytes())
  return SimpleNamespace(binding=H.binding,read=read)
 def test_historical_scope_restores_live_on_exception(self):
  old=self.keep('tracker',dict(revision=1));copy=self.keep('before.json',dict(revision=1));current=self.keep('tracker',dict(revision=2));m=self.module();b,r=m.binding,m.read
  with self.assertRaisesRegex(RuntimeError,'stop'):
   with H.readers([m],[dict(original=old,copy=copy)]):
    self.assertEqual(m.binding(old['path']),old);self.assertEqual(m.read(old),dict(revision=1));self.assertEqual(m.read(current),dict(revision=2));raise RuntimeError('stop')
  self.assertIs(m.binding,b);self.assertIs(m.read,r);self.assertEqual(m.binding(old['path']),current)
 def test_changed_copy_and_duplicate_refused(self):
  old=self.keep('tracker',dict(revision=1));copy=self.keep('before.json',dict(revision=1));row=dict(original=old,copy=copy)
  with self.assertRaises(ValueError),H.readers([self.module()],[row,row]):pass
  Path(copy['path']).write_text('changed')
  with self.assertRaises(ValueError),H.readers([self.module()],[row]):pass
 def test_copy_rechecked_each_read(self):
  old=self.keep('tracker',dict(revision=1));copy=self.keep('before.json',dict(revision=1));m=self.module()
  with H.readers([m],[dict(original=old,copy=copy)]):
   Path(copy['path']).write_text('changed')
   with self.assertRaises(ValueError):m.read(old)
 def test_unrelated_live_data_never_projected(self):
  old=self.keep('tracker',dict(revision=1));copy=self.keep('before.json',dict(revision=1));other=self.keep('other',dict(value='before'));m=self.module()
  with H.readers([m],[dict(original=old,copy=copy)]):
   Path(other['path']).write_text('changed')
   with self.assertRaises(ValueError):m.read(other)
 def test_witness_locations_and_keys_closed(self):
  tracker=self.keep('tracker',dict(revision=1));before=self.keep('refresh/before.json',dict(revision=1));original=self.keep('normalization/normalized.json',dict(value=1));copy=self.keep('refresh/generated-before/normalized.json',dict(value=1));r=dict(key='normalized_final',original=original,copy=copy);decode=lambda _:SimpleNamespace(content=SimpleNamespace(normalization_run=self.root/'normalization'))
  def prep(rows):return self.keep('refresh/prepared.json',dict(before=before,selection={},mutable_witnesses=rows))
  self.assertEqual(len(H.witnesses(prep([r]),tracker,decode)),2)
  for rows in [[r,r],[dict(r,key='other')],[dict(r,copy=before)]]:
   with self.subTest(rows=rows),self.assertRaises(ValueError):H.witnesses(prep(rows),tracker,decode)

if __name__=='__main__':unittest.main()

class DeclaredReaderTests(unittest.TestCase):
 setUp=HistoryTests.setUp
 keep=HistoryTests.keep
 module=HistoryTests.module
 def test_only_declared_validators_receive_projection(self):
  old=self.keep('tracker',dict(revision=1));copy=self.keep('before.json',dict(revision=1));current=self.keep('tracker',dict(revision=2));rows=[dict(original=old,copy=copy)]
  bindings=[]
  for name in ['old/normalization_prepared_refresh.py','new/normalization_prepared_refresh.py','normalization_completed_emission.py','other.py']:
   p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# exact selected fixture');bindings.append(H.binding(p))
  previous=self.keep('previous.json',dict(home='old'));ancestry=self.keep('ancestry.json',dict(previous=previous));plan=dict(prepared_helper=bindings[0],completed_helper=bindings[2],prepared_refresh=ancestry)
  created=[]
  def module(name,row):
   m=self.module();m.selected=row
   def validate(reader,p,*,committed):return reader.binding(old['path']),committed
   def validate_single(reader,p):return m.binding(old['path'])
   m.validate=validate;m.validate_single=validate_single;created.append(m);return m
  N=self.module();N.module=module;original=N.module;commit=dict(checkpoint='authentic',committed='authentic')
  with H.declared_readers(N,plan,bindings[1],rows,commit):
   m=N.module('prepared',bindings[0]);self.assertEqual(m.selected,bindings[1]);self.assertEqual(m.validate(N,plan),(old,commit))
   with self.assertRaises(ValueError):m.validate(N,dict(plan,extra=True))
   c=N.module('completed',bindings[2]);self.assertEqual(c.validate_single(N,N.read(previous)),old)
   u=N.module('unrelated',bindings[3]);self.assertEqual(u.binding(old['path']),current)
  self.assertIs(N.module,original);self.assertEqual(N.binding(old['path']),current)

class TakeoverPriorTests(unittest.TestCase):
 def fixture(self):
  old=SimpleNamespace(owner='old',token='old-token',revision=4);new=SimpleNamespace(owner='new',token='new-token',revision=5)
  cp={'path':'/cp','sha256':'a'*64};descriptor={'path':'/descriptor','sha256':'b'*64};value=dict(status='IMPORTED',descriptor=descriptor,lease='unchanged-original');before=dict(workbook_id='0316',revision=4,bindings={'original_raw_source':'exact'},leases={'task_agent':dict(owner=old.owner,token=old.token)});after=dict(workbook_id='0316',revision=5,bindings={'original_raw_source':'exact'});seen=[]
  C=SimpleNamespace(_prior=lambda b,d:(value,old),worker=SimpleNamespace(_active=lambda w,l:seen.append(l)))
  T=SimpleNamespace(read=lambda r:dict(stopped_owner_proof='stop')if r=='done'else dict(selection={'checkpoint':cp}),validate_completion=lambda r:(before,after,new));w=SimpleNamespace(bootstrap=SimpleNamespace(workbook='0316'))
  return C,T,w,cp,descriptor,value,old,new,before,seen
 def test_exact_checkpoint_changes_only_returned_lease(self):
  C,T,w,cp,d,v,old,new,before,seen=self.fixture();original=C._prior
  with H.takeover_prior(C,w,cp,'done',T):
   actual,lease=C._prior(cp,d);self.assertIs(actual,v);self.assertIs(lease,new)
   self.assertIs(C._prior({'path':'/other'},d)[1],old)
  self.assertEqual(seen,[new]);self.assertIs(C._prior,original)
 def test_foreign_previous_owner_refused(self):
  C,T,w,cp,d,v,old,new,before,seen=self.fixture();before['leases']['task_agent']['owner']='foreign'
  with H.takeover_prior(C,w,cp,'done',T),self.assertRaises(ValueError):C._prior(cp,d)
  self.assertEqual(seen,[])

 def test_exact_prior_takeover_archive_links_second_replacement(self):
  C,T,w,cp,d,v,old,new,before,seen=self.fixture();original_before=dict(before);middle=dict(before,revision=5,leases={'task_agent':dict(owner='middle',token='middle-token')});after=dict(middle,revision=6);latest=SimpleNamespace(owner='latest',token='latest-token',revision=6)
  rows={'done':dict(stopped_owner_proof='stop'),'stop':dict(selection=dict(checkpoint='prior',prior_completion='prior'),before={'path':'new-before','sha256':'c'*64}),'prior':dict(stopped_owner_proof='prior-stop',after={'path':'old-after','sha256':'c'*64}),'prior-stop':dict(selection=dict(checkpoint=cp))}
  T.read=lambda r:rows[r];T.validate_completion=lambda r:(middle,after,latest)if r=='done'else(original_before,middle,new)
  with H.takeover_prior(C,w,cp,'done',T,previous_completion='prior'):
   actual,lease=C._prior(cp,d);self.assertIs(actual,v);self.assertIs(lease,latest)
  rows['stop']['before']['sha256']='changed'
  with H.takeover_prior(C,w,cp,'done',T,previous_completion='prior'),self.assertRaises(ValueError):C._prior(cp,d)

class PhaseOneProjectionTests(unittest.TestCase):
 setUp=HistoryTests.setUp
 keep=HistoryTests.keep
 def fixture(self):
  from types import ModuleType,SimpleNamespace
  source=self.keep('source.json',{'source':'unchanged'})
  old=self.keep('normalized.json',{'phase':1});archive=self.keep('refresh/generated-before/normalized.json',{'phase':1})
  current=self.keep('normalized.json',{'phase':2})
  lanes=ModuleType('original_lanes');path=self.root/'original_lanes.py'
  text="def normalization_projection(s,*,phase1=False):\n return [checked_record(s.bindings['normalized_final']),checked_record(s.bindings['source'])]\n"
  path.write_text(text);lanes.__file__=str(path)
  def checked(row):H.require(H.binding(row['path'])==row,'actual current evidence changed');return dict(row)
  lanes.checked_record=checked;exec(compile(text,str(path),'exec'),lanes.__dict__)
  F=ModuleType('original_refresh');fp=self.root/'original_refresh.py'
  text="def unchanged(old,new):\n b=new.content\n return W.lanes.normalization_projection(b,phase1=True)\n"
  fp.write_text(text);F.__file__=str(fp);F.W=ModuleType('original_worker');F.W.lanes=lanes
  expected=SimpleNamespace(bindings=dict(normalized_final=old,source=source))
  F.decode=lambda value:SimpleNamespace(content=expected);exec(compile(text,str(fp),'exec'),F.__dict__)
  witness=dict(key='normalized_final',original=old,copy=archive)
  prepared=self.keep('refresh/prepared.json',dict(code=H.binding(fp),selection={},mutable_witnesses=[witness]))
  return F,prepared,[dict(original=old,copy=archive)],expected,current,source
 def test_only_original_unchanged_call_uses_archive_and_restores(self):
  F,p,rows,expected,current,source=self.fixture();old=F.unchanged;projection=F.W.lanes.normalization_projection
  with self.assertRaises(ValueError):F.unchanged(None,SimpleNamespace(content=expected))
  with self.assertRaisesRegex(RuntimeError,'consumer stopped'):
   with H.phase1_projection(F,p,rows):
    self.assertEqual(F.unchanged(None,SimpleNamespace(content=expected)),[expected.bindings['normalized_final'],source])
    with self.assertRaises(ValueError):projection(expected,phase1=True)
    actual=SimpleNamespace(bindings=dict(normalized_final=current,source=source))
    self.assertEqual(projection(actual),[current,source])
    raise RuntimeError('consumer stopped')
  self.assertIs(F.unchanged,old);self.assertIs(F.W.lanes.normalization_projection,projection)
 def test_archive_changed_during_projection_refused(self):
  F,p,rows,expected,current,source=self.fixture()
  with H.phase1_projection(F,p,rows):
   Path(rows[0]['copy']['path']).write_text('{}')
   with self.assertRaises(ValueError):F.unchanged(None,SimpleNamespace(content=expected))
 def test_foreign_selection_does_not_get_archive(self):
  F,p,rows,expected,current,source=self.fixture()
  with H.phase1_projection(F,p,rows):
   foreign=SimpleNamespace(bindings=dict(expected.bindings,unrelated='changed'))
   with self.assertRaises(ValueError):F.unchanged(None,SimpleNamespace(content=foreign))
 def test_current_source_never_projected(self):
  F,p,rows,expected,current,source=self.fixture()
  with H.phase1_projection(F,p,rows):
   Path(source['path']).write_text('{}')
   with self.assertRaises(ValueError):F.unchanged(None,SimpleNamespace(content=expected))
 def test_foreign_archive_or_refresh_code_refused(self):
  for defect in ('archive','code'):
   F,p,rows,expected,current,source=self.fixture()
   if defect=='archive':rows[0]['copy']=current
   else:Path(F.__file__).write_text('# changed')
   with self.subTest(defect=defect),self.assertRaises(ValueError),H.phase1_projection(F,p,rows):pass
