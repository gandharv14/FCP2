import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from scripts.fleet import normalization_completed_disclosure_context as M,undispatched_campaign as A

class CompletedChildLineage(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
  def file(name,text='{}'):
   p=self.root/name;p.write_text(text);return M.binding(p)
  self.base=SimpleNamespace(__file__=file('base.py')['path'],SECONDS=3300,MEMORY=8*1024**3)
  self.producer=SimpleNamespace(__file__=file('instruction.py')['path'],SCHEMA='original-instruction',guard_seconds=lambda *a:3300)
  self.previous=file('previous.json');self.row=file('plan.json');self.wrapper=file('wrapper.py');self.q=dict(helper=self.wrapper,previous=self.previous)
  self.intent=file('intent.json',json.dumps(dict(schema_version=self.producer.SCHEMA,action='step',arguments=dict(checkpoint=file('checkpoint.json'),descriptor=file('descriptor.json')),controls={'original':True},models=True,commands=True,containers=True,code=M.binding(self.producer.__file__),base_code=M.binding(self.base.__file__),executable=M.binding(Path(sys.executable).resolve()),memory_limit_bytes=8*1024**3,timeout_seconds=3300)))
  command=[sys.executable,'-B',self.producer.__file__,'_child',self.intent['path'],self.intent['sha256']]
  self.evidence=[file('source.json')];self.reader=SimpleNamespace(evidence=lambda q,p:self.evidence)
  self.side=dict(schema_version=A.SCHEMA,adapter=M.binding(A.__file__),producer_code=M.binding(self.producer.__file__),base_code=M.binding(self.base.__file__),kind='instruction',reader_plan=self.row,child_wrapper=self.wrapper,reader_evidence=self.evidence,intent=self.intent,original_command=command,selected_command=[sys.executable,'-I','-B',self.wrapper['path'],'owning-child','--plan',self.row['path'],'--plan-sha256',self.row['sha256'],'--kind','instruction','--intent',self.intent['path'],'--intent-sha256',self.intent['sha256']],timeout_seconds=3300,memory_limit_bytes=8*1024**3,original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False)
 def run_side(self,side):
  (self.root/'declared-runtime-adapter.json').write_text(json.dumps(side));return M.child_lineage(A,self.reader,self.q,self.row,(self.base,self.producer),None,self.intent)
 def test_actual_original_contract(self):self.assertEqual(self.run_side(self.side)[0],'instruction')
 def test_changed_lineage_fields(self):
  for key,val in [('reader_plan',self.previous),('reader_evidence',[]),('child_wrapper',self.previous),('timeout_seconds',3301),('memory_limit_bytes',16*1024**3),('automatic_retry',True),('semantic_budget_reset',True),('original_snapshot_unchanged',False),('unknown',True)]:
   with self.subTest(key=key),self.assertRaises(ValueError):self.run_side(dict(self.side,**{key:val}))
 def test_selected_command_not_same_wrapper(self):
  side=copy.deepcopy(self.side);side['selected_command'][3]=self.base.__file__
  with self.assertRaises(ValueError):self.run_side(side)
 def test_original_argv_changed(self):
  side=copy.deepcopy(self.side);side['original_command'][3]='run'
  with self.assertRaises(ValueError):self.run_side(side)
 def test_original_intent_tampered(self):
  Path(self.intent['path']).write_text('{}')
  with self.assertRaises(ValueError):self.run_side(self.side)
 def test_source_evidence_multiplicity(self):
  with self.assertRaises(ValueError):self.run_side(dict(self.side,reader_evidence=self.evidence*2))
 def test_binding_parent_symlink(self):
  link=self.root/'alias';link.symlink_to(self.root,target_is_directory=True)
  with self.assertRaises(ValueError):M.binding(link/'intent.json')
 def test_source_evidence_byte_mutation(self):
  # Original contract checks retained implementation, and exact sidecar evidence
  # must be rehashed by the original imported context before this reader runs.
  changed=copy.deepcopy(self.evidence);changed[0]['sha256']='0'*64
  with self.assertRaises(ValueError):self.run_side(dict(self.side,reader_evidence=changed))

if __name__=='__main__':unittest.main()

class CompletedPrefix(unittest.TestCase):
 def setUp(self):
  from unittest.mock import patch
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name).resolve();self.home=self.root/'continuation';self.home.mkdir();self.run=self.root/'run';self.ops=self.run/'dispatcher-v2/continuation-authoring';self.ops.mkdir(parents=True)
  def put(path,value):
   path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
  self.put=put;self.source=put(self.root/'source.json',{});self.helper=put(self.root/'helper.py',{});self.row=put(self.root/'selected.json',{});self.completed=put(self.root/'completed.json',dict(result=put(self.root/'old-result.json',{})));self.original=dict(driver=put(self.root/'driver.py',{}),publish=False,models=True,commands=True,containers=True);single=put(self.root/'single.json',dict(job=put(self.root/'old-job.json',self.original)));previous=put(self.root/'previous.json',dict(single_author=single));self.q=dict(home=str(self.home),previous=previous,completed_package=put(self.root/'package.json',dict(completed=self.completed)),owner='owner',fence=9,source=self.source,workbook='X',helper=self.helper)
  self.job=dict(self.original,outcome=self.completed,operations=str(self.ops));jb=put(self.home/'authoring-job.json',self.job);put(self.home/'intent.json',dict(plan=self.row));put(self.ops/'job.json',self.job)
  worker=self.root/'worker';wj=put(self.root/'worker-job.json',dict(driver=self.helper,inputs=[self.row],source=self.source,workbook='X',version='v'))
  wi=put(worker/'intent.json',dict(job=wj,owner='owner',claim_id='claim'));self.claim=dict(owner='owner',claim_id='claim',fence=9,version='v',source_sha256=self.source['sha256'],workbook='X');put(worker/'claim.json',self.claim)
  outer=put(worker/'outcome.json',dict(intent=wi,returncode=1,failure_type=None,result=None,automatic_retry=False,publication_performed=False));self.p=dict(original_job=jb,worker_outcome=outer)
  self.env=(SimpleNamespace(encode=lambda x:x),SimpleNamespace(__file__=put(self.root/'I.py',{})['path']),None,None,None,dict(run_root=str(self.run)),self.root/'v',None)
  prior=M.read(self.completed)['result'];descriptor=put(self.root/'descriptor.json',{});build=None
  for i in range(4):
   d=self.ops/('%03d'%i);intent=put(d/'intent.json',dict(action='step',arguments=dict(checkpoint=prior,descriptor=descriptor),controls=None,models=True,commands=True,containers=True))
   guard=put(d/'resource.json',dict(intent=intent,returncode=0,failure=None));value=dict(status='STEP_COMPLETE',next_step='next',evidence=[prior],descriptor=descriptor,selection={'source':'same'},publication_performed=False)
   if i==2:value['next_step']='disclosure-review'
   if i==3:
    value.update(status='MODEL_RECONCILIATION_REQUIRED',next_step='disclosure-review',checkpoint=put(self.run/'instruction/steps/disclosure-review/blocked.json',dict(status='MODEL_RECONCILIATION_REQUIRED',step='disclosure-review',selection=value['selection'])))
   cp=put(d/'result.json',value);out=put(d/'outcome.json',dict(resource=guard,result=cp))
   if i==2:build=dict(outcome=out,result=cp,descriptor=descriptor,dispatcher=self.job['driver'],instruction=M.binding(self.env[1].__file__))
   prior=cp
  self.p.update(build=build,stopped_outcome=out,stopped_result=cp);put(self.ops/'hold.json',dict(outcome=out));self.D=SimpleNamespace(successful=lambda row,env=None:(M.read(row)['result'],M.read(M.read(row)['result'])))
  self.patch=patch.object(M,'child_lineage',side_effect=lambda *args:('instruction',M.read(args[-1])));self.patch.start();self.addCleanup(self.patch.stop)
 def run_prefix(self):return M.prefix(self.p,None,self.q,self.row,self.D,self.env,None)
 def test_complete_contiguous_prefix(self):self.assertEqual(self.run_prefix(),self.job)
 def test_skipped_predecessor(self):
  d=self.ops/'001';ci=M.read(M.binding(d/'intent.json'));ci['arguments']['checkpoint']=self.source;ib=self.put(d/'intent.json',ci);g=M.read(M.binding(d/'resource.json'));g['intent']=ib;gb=self.put(d/'resource.json',g);o=M.read(M.binding(d/'outcome.json'));o['resource']=gb;self.put(d/'outcome.json',o)
  with self.assertRaises(ValueError):self.run_prefix()
 def test_missing_guard_directory(self):
  (self.ops/'001').rename(self.ops/'009')
  with self.assertRaises(ValueError):self.run_prefix()
 def test_foreign_claim(self):
  self.put(self.root/'worker/claim.json',dict(self.claim,owner='other'))
  with self.assertRaises(ValueError):self.run_prefix()
 def test_foreign_claim_version(self):
  self.put(self.root/'worker/claim.json',dict(self.claim,version='other'))
  with self.assertRaises(ValueError):self.run_prefix()
 def test_wrong_terminal(self):
  self.p['stopped_result']=self.p['build']['result']
  with self.assertRaises(ValueError):self.run_prefix()
 def test_old_build_selected(self):
  self.p['build']=dict(self.p['build'],outcome=M.binding(self.ops/'001/outcome.json'))
  with self.assertRaises(ValueError):self.run_prefix()

class TerminalControls(CompletedPrefix):
 def test_changed_terminal_controls_and_capabilities(self):
  for field,value in [('controls',{'foreign':True}),('models',False),('commands',False),('containers',False)]:
   d=self.ops/'003';original=(d/'intent.json').read_bytes();outer=(d/'outcome.json').read_bytes();resource=(d/'resource.json').read_bytes()
   ci=json.loads(original);ci[field]=value;ib=self.put(d/'intent.json',ci);g=json.loads(resource);g['intent']=ib;gb=self.put(d/'resource.json',g);o=json.loads(outer);o['resource']=gb;self.p['stopped_outcome']=self.put(d/'outcome.json',o);self.put(self.ops/'hold.json',dict(outcome=self.p['stopped_outcome']))
   with self.subTest(field=field),self.assertRaises(ValueError):self.run_prefix()
   (d/'intent.json').write_bytes(original);(d/'resource.json').write_bytes(resource);(d/'outcome.json').write_bytes(outer);self.p['stopped_outcome']=M.binding(d/'outcome.json');self.put(self.ops/'hold.json',dict(outcome=self.p['stopped_outcome']))
