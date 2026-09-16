"""Authentic nested custody cannot reinterpret a numerical failure as transport."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.fleet import resource_continue as R

class Interrupted(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.oldhome=self.root/'attempt2';self.run=self.root/'worker2';self.run.mkdir();self.oldhome.mkdir()
  self.source=dict(path=str(self.root/'source.xlsx'),sha256='a'*64,workbook_id='0203');self.snap=self.put('snapshot.json',dict(fingerprint='v2'))
  self.priorledger=dict(workbook_id='0203',source=self.source,state='READY',route='pass',build_history=[],build=dict(state='STOPPED',attempts=1))
  ledger1=self.put('ledger1.json',self.priorledger)
  self.old=dict(schema_version='fleet-resource-continuation/v1',workbook='0203',source=self.source,generation='g',next_attempt=2,models=0,automatic_retry=False,home=str(self.oldhome),snapshot=self.snap,helper=self.put('driver.json',{}),ledger=ledger1,allowance=self.put('allowance/old.json',{}),cumulative_reconstruction_limit=2)
  self.oldrow=self.put('plan2.json',self.old)
  self.ledger=dict(workbook_id='0203',source=self.source,state='READY',route='pass',build_history=[self.priorledger['build']],build=dict(state='RUNNING',stage='segmentation',attempts=2,source_generation_id='g',source_checkpoint=dict(state='complete')))
  ledger=self.put('attempt2/corpus/workbooks/0203/ledger.json',self.ledger)
  job=dict(driver=self.old['helper'],inputs=[self.oldrow],source={k:self.source[k]for k in('path','sha256')},workbook='0203',version='v2',command=['python','-I','-B',self.old['helper']['path'],'run','--plan',self.oldrow['path'],'--sha256',self.oldrow['sha256']],result=str(self.oldhome/'result.json'),run=str(self.run))
  jb=self.put('job2.json',job);intent=self.put('worker2/intent.json',dict(job=jb,owner='host:old',claim_id='claim'))
  log=self.run/'worker.log';log.write_bytes(b'')
  self.out=dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',failure_type='TimeoutError',returncode=None,result=None,automatic_retry=False,publication_performed=False,intent=intent,log=R.binding(log))
  outcome=self.put('worker2/outcome.json',self.out)
  self.process=dict(job=jb,pid=765432,start_ticks='1234',command=['python',jb['path'],'--owner','host:old'])
  process=self.put('process.json',self.process)
  self.claim=dict(workbook='0203',owner='host:old',claim_id='claim',source_sha256=self.source['sha256'],version='v2',fence=1)
  claim=self.put('claim.json',self.claim)
  self.put('allowance/reconstruction-0203-000002.json',dict(plan=self.oldrow,previous=ledger1,ordinal=2,cumulative_limit=2,automatic_retry=False))
  self.put('attempt2/intent.json',dict(plan=self.oldrow,previous=ledger1,allowance=self.old['allowance'],next_attempt=2,automatic_retry=False))
  self.p=dict(workbook='0203',source=self.source,generation='g',previous_attempt=2,next_attempt=3,cumulative_reconstruction_limit=3,models=0,automatic_retry=False,corpus=str(self.oldhome/'corpus'),ledger=ledger,home=str(self.root/'attempt3'),allowance=self.put('allowance/new.json',{}),seconds=7200,memory_bytes=8*1024**3,interrupted=dict(plan=self.oldrow,outcome=outcome,process=process,claim=claim))
  self.policy=dict(limits=dict(stage_seconds=7200,memory_bytes=8*1024**3))
 def put(self,name,value):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,sort_keys=True)+'\n');return R.binding(p)
 def check(self):
  with patch.object(R,'stopped_process_absence')as native:
   R.validate_boundary(self.p,self.ledger,self.policy);native.assert_called_once_with(self.process,(str(self.oldhome),str(self.run),str(self.root/'job2.json')))
 def test_preserves_running_attempt_and_appends_distinct_allowance(self):
  before=copy.deepcopy(self.ledger);self.check();self.assertEqual(self.ledger,before)
 def test_completed_numerical_or_resource_failure_is_not_transport(self):
  for key,value in [('state','STOPPED'),('exit_code',1),('resources',{'limit_reason':'wall_time'}),('stage','source')]:
   with self.subTest(key=key):
    old=copy.deepcopy(self.ledger);self.ledger['build'][key]=value
    with self.assertRaises(ValueError):self.check()
    self.ledger=old
 def test_worker_cannot_have_result_or_ambiguous_failure(self):
  for key,value in [('failure_type','RuntimeError'),('returncode',1),('result',{'path':'pass'}),('automatic_retry',True),('publication_performed',True)]:
   with self.subTest(key=key):
    value=dict(self.out,**{key:value});self.p['interrupted']['outcome']=self.put('worker2/outcome.json',value)
    with self.assertRaises(ValueError):self.check()
 def test_never_replay_after_any_model_journal(self):
  d=self.run/'model-operations';d.mkdir();(d/'unknown.json').write_text('{}')
  with self.assertRaisesRegex(ValueError,'model operation'):self.check()
 def test_sealed_supervisor_interruption_keeps_unknown_cause_and_consumed_history(self):
  self.p['interrupted']['outcome']=self.put('worker2/outcome.json',dict(self.out,failure_type='RuntimeError'))
  self.p['interrupted']['finish_uncertain']=self.put('worker2/finish-uncertain.json',dict(
   outcome=self.p['interrupted']['outcome'],failure_type='RuntimeError',automatic_retry=False,reconciliation_required=True))
  before=copy.deepcopy(self.ledger);self.check();self.assertEqual(self.ledger,before)
 def test_untied_final_update_does_not_authorize_supervisor_reconstruction(self):
  self.p['interrupted']['outcome']=self.put('worker2/outcome.json',dict(self.out,failure_type='RuntimeError'))
  self.p['interrupted']['finish_uncertain']=self.put('worker2/finish-uncertain.json',dict(
   outcome=self.put('foreign.json',{}),failure_type='RuntimeError',automatic_retry=False,reconciliation_required=True))
  with self.assertRaisesRegex(ValueError,'final-update'):self.check()
 def test_completed_result_not_ignored(self):
  (self.oldhome/'result.json').write_text('{}')
  with self.assertRaises(ValueError):self.check()
 def test_preserved_attempt_counters_and_new_allowance(self):
  for key,value in [('previous_attempt',1),('next_attempt',2),('cumulative_reconstruction_limit',2),('allowance',self.old['allowance']),('home',self.old['home'])]:
   with self.subTest(key=key):
    old=copy.deepcopy(self.p);self.p[key]=value
    with self.assertRaises(ValueError):self.check()
    self.p=old
 def test_claim_owner_source_and_fence_cannot_change(self):
  for key,value in [('owner','foreign'),('claim_id','other'),('source_sha256','b'),('version','other'),('fence',0)]:
   with self.subTest(key=key):
    self.p['interrupted']['claim']=self.put('claim.json',dict(self.claim,**{key:value}))
    with self.assertRaises(ValueError):self.check()
 def test_original_reservation_cannot_be_reassigned(self):
  self.put('allowance/reconstruction-0203-000002.json',dict(plan=self.oldrow,previous=self.old['ledger'],ordinal=1,cumulative_limit=2,automatic_retry=False))
  with self.assertRaisesRegex(ValueError,'reservation'):self.check()
 def test_original_history_cannot_disappear(self):
  self.ledger['build_history']=[];self.p['ledger']=self.put('attempt2/corpus/workbooks/0203/ledger.json',self.ledger)
  with self.assertRaisesRegex(ValueError,'historical attempts'):self.check()
 def test_live_native_process_blocks_successor(self):
  with patch.object(R,'stopped_process_absence',side_effect=ValueError('previous source process remains active')):
   with self.assertRaisesRegex(ValueError,'remains active'):R.validate_boundary(self.p,self.ledger,self.policy)
