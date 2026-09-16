import copy,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import recursion_result_recovery as r

class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.directory=self.root/'recovery'
  self.request={'name':'historical evaluation','description':'exact','metadata':{'schemaVersion':1,'attemptsPerProblem':3,'successThreshold':1,'retryPolicy':{'maxInfraRetries':2}},'tags':['audit'],'solvers':[{'displayName':s,'runConfigVersionId':'old-'+s,'concurrency':50} for s in ['opus','gemini']],'problems':[{'problemId':p,'problemVersionId':'old-'+p,'environmentId':'environment'} for p in ['p1','p2']]}
  self.detail={'id':'eval','kind':'solve_and_grade','name':self.request['name'],'description':'exact','createdAt':'2026-09-15T00:00:00Z','metadata':self.request['metadata'],'nAttemptsPerProblem':3,'tags':['audit'],'graderRunConfigVersionId':None,'totalSolvers':2,'totalProblems':2,'solvers':[{'id':s['displayName'],'evaluationId':'eval',**s,'metadata':{'concurrency':50}} for s in self.request['solvers']],'problems':[{'id':'slot-'+p['problemId'],'evaluationId':'eval',**p} for p in self.request['problems']]}
  self.matrix={'evaluationId':'eval','cells':[]};self.runs={}
  for s in self.detail['solvers']:
   for p in self.detail['problems']:
    attempts=[]
    for n in range(1,4):
     rid=f"{p['problemId']}-{s['id']}-{n}";attempts.append({'problemRunId':rid,'attemptNumber':n,'score':None,'status':'pending'})
     self.runs[rid]={'id':rid,'problemId':p['problemId'],'problemVersionId':p['problemVersionId'],'attemptNumber':n,'solverRunConfigVersionId':s['runConfigVersionId'],'source':'evaluation','status':'completed','finalScore':1.0,'startedAt':'2026-09-15T00:01:00Z','completedAt':'2026-09-15T00:02:00Z','executionTimeMs':60000,'prompt':'SECRET_PROMPT','transcript':'SECRET_TRANSCRIPT','gradingConfig':{'archive':'SECRET_ARCHIVE'}}
    self.matrix['cells'].append({'solverId':s['id'],'problemVersionId':p['problemVersionId'],'attempts':attempts})
  for name,value in [('request',self.request),('detail',self.detail),('matrix',self.matrix)]:r.c.save(self.root/f'{name}.json',value)
  self.seed()
 def seed(self):return r.prepare_seed(self.directory,*(self.root/f'{x}.json' for x in ('request','detail','matrix')))
 def cli(self,*args,**kwargs):
  self.assertEqual(args[0],'problem-runs');self.assertIn(args[1],['list-for-problem','get']);self.assertFalse(kwargs)
  if args[1]=='get':return copy.deepcopy(self.runs[args[-1]])
  return {'items':[copy.deepcopy(x) for x in self.runs.values() if x['problemId']==args[-1]],'truncated':False}
 def recover(self,cli=None,**kwargs):
  with patch.object(r,'read_call',side_effect=cli or self.cli),patch.object(r.c,'mutation') as mutation:
   try:return r.recover(self.directory,reads_authorized=True,**kwargs)
   finally:mutation.assert_not_called()
 def test_full_known_id_recovery_and_private_fields_discarded(self):
  result=self.recover();self.assertEqual(result['runCount'],12);self.assertEqual(result['cellCount'],4)
  for path in (self.directory/'problems').glob('*.json'):
   self.assertNotIn('SECRET_',path.read_text());self.assertNotIn('gradingConfig',path.read_text())
  original=(self.directory/'recovered-slots.json').read_bytes()
  with patch.object(r,'read_call') as cli:r.recover(self.directory,reads_authorized=True);cli.assert_not_called()
  self.assertEqual((self.directory/'recovered-slots.json').read_bytes(),original)
 def test_truncated_history_uses_exact_get_for_every_known_slot(self):
  calls=[]
  def cli(*args):
   calls.append(args);x=self.cli(*args)
   if args[1]=='list-for-problem':x['truncated']=True
   return x
  self.recover(cli);self.assertEqual(sum(x[1]=='get' for x in calls),12)
 def test_missing_history_ids_use_get_and_unrelated_history_ignored(self):
  calls=[]
  def cli(*args):
   calls.append(args);x=self.cli(*args)
   if args[1]=='list-for-problem':x['items']=x['items'][1:]+[{'id':'unrelated-'+args[-1],'gradingConfig':'SECRET_ARCHIVE'}]
   return x
  self.recover(cli);self.assertEqual(sum(x[1]=='get' for x in calls),2)
 def test_null_score_preserved_without_claiming_pass(self):
  self.runs['p1-opus-1']['finalScore']=None;result=self.recover();d=r.read(result['artifactPath'])
  a=next(a for c in d['cells'] for a in c['attempts'] if a['problemRunId']=='p1-opus-1');self.assertIsNone(a['score']);self.assertEqual(a['status'],'completed')
 def test_foreign_versions_attempts_and_models_rejected(self):
  for field,value in [('problemVersionId','foreign'),('attemptNumber',4),('solverRunConfigVersionId','foreign'),('problemId','foreign')]:
   run=copy.deepcopy(self.runs['p1-opus-1']);run[field]=value
   slot=next(s for s in r.validate_seed(self.directory)['slots'] if s['problemRunId']==run['id'])
   with self.assertRaisesRegex(ValueError,'binding'):r.clean_run(run,slot)
 def test_wrong_get_id_and_duplicate_history_fail_closed(self):
  def wrong_get(*args):
   if args[1]=='list-for-problem':return {'items':[],'truncated':False}
   x=self.cli(*args);x['id']='foreign';return x
  with self.assertRaisesRegex(ValueError,'incomplete'):self.recover(wrong_get)
  self.assertFalse((self.directory/'recovered-slots.json').exists())
  def duplicate(*args):
   x=self.cli(*args)
   if args[1]=='list-for-problem':x['items'].append(copy.deepcopy(x['items'][0]))
   return x
  with self.assertRaisesRegex(ValueError,'incomplete'):self.recover(duplicate)
 def test_missing_get_fails_and_resume_reads_only_failed_problem(self):
  def failed(*args):
   if args[1]=='list-for-problem' and args[-1]=='p2':return {'items':[],'truncated':False}
   if args[1]=='get':raise RuntimeError('SECRET_ARCHIVE')
   return self.cli(*args)
  with self.assertRaisesRegex(ValueError,'incomplete'):self.recover(failed)
  self.assertFalse((self.directory/'recovered-slots.json').exists())
  for p in (self.directory/'failures').glob('*.json'):self.assertNotIn('SECRET',p.read_text())
  calls=[]
  def resumed(*args):calls.append(args);return self.cli(*args)
  result=self.recover(resumed);self.assertEqual(result['runCount'],12);self.assertEqual([x[-1] for x in calls],['p2'])
 def test_explicit_cached_terminal_provenance_has_no_fresh_read_timestamp(self):
  seed=r.validate_seed(self.directory)
  # A new separately seeded recovery directory is required to change frozen input.
  for c in self.matrix['cells']:
   for a in c['attempts']:a.update(status='completed',score=1.0)
  r.c.save(self.root/'matrix.json',self.matrix);self.directory=self.root/'terminal-recovery';self.seed()
  with patch.object(r,'read_call') as cli:result=r.recover(self.directory,reads_authorized=True,cache_seed_terminal=True);cli.assert_not_called()
  self.assertEqual(result['cachedSeedTerminalRunCount'],12)
  for c in r.read(result['artifactPath'])['cells']:
   for a in c['attempts']:self.assertIsNone(a['provenance']['readAt']);self.assertIn('sourceMatrixSha256',a['provenance'])
 def test_no_read_permission_and_more_than_four_workers_rejected(self):
  with patch.object(r,'read_call') as cli:
   with self.assertRaisesRegex(ValueError,'not_authorized'):r.recover(self.directory)
   with self.assertRaisesRegex(ValueError,'exceeds_four'):r.recover(self.directory,reads_authorized=True,max_workers=5)
   cli.assert_not_called()
 def test_seed_and_receipt_tampering_rejected(self):
  self.recover();path=self.directory/'problems/p1.json';d=r.read(path);d['runs'][0]['outcome']['score']=0;r.c.save(path,d)
  with self.assertRaisesRegex(ValueError,'seal'):r.assemble(self.directory)
  path=self.directory/'seed-artifacts/matrix.json';d=r.read(path);d['cells'].pop();r.c.save(path,d)
  with self.assertRaisesRegex(ValueError,'snapshot_changed'):r.validate_seed(self.directory)
 def test_seed_rejects_duplicate_or_missing_matrix_slots(self):
  for change in ['duplicate','missing']:
   m=copy.deepcopy(self.matrix)
   if change=='duplicate':m['cells'][0]['attempts'][1]=copy.deepcopy(m['cells'][0]['attempts'][0])
   else:m['cells'].pop()
   with self.assertRaises(ValueError):r.membership(self.request,self.detail,m)
 def test_seed_survives_advancing_original_collector_files(self):
  original=r.validate_seed(self.directory);r.c.save(self.root/'matrix.json',{'new':'collector snapshot'})
  self.assertEqual(r.validate_seed(self.directory),original)
 def test_read_adapter_allows_only_documented_reads_and_has_timeout(self):
  from types import SimpleNamespace
  with patch.object(r.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"items":[],"truncated":false}')) as run:
   r.read_call('problem-runs','list-for-problem','--problem-id','p1')
   self.assertEqual(run.call_args.kwargs['timeout'],60)
   self.assertNotIn('--api-key',run.call_args.args[0])
  with patch.object(r.subprocess,'run') as run:
   with self.assertRaisesRegex(ValueError,'non_read_route'):r.read_call('problem-runs','regrade','--problem-run-id','run')
   run.assert_not_called()
 def test_prefix_seed_accepts_old_versions_without_active_membership(self):
  r.c.save(self.root/'evaluation-1.request.json',self.request)
  r.c.save(self.root/'results/evaluation-1-detail.json',self.detail)
  r.c.save(self.root/'results/evaluation-1-matrix.json',self.matrix)
  seed=r.seed_from_prefix(self.root,'evaluation-1',self.root/'old-eval-seed')
  self.assertEqual({s['solverRunConfigVersionId'] for s in seed['slots']},{'old-opus','old-gemini'})

if __name__=='__main__':unittest.main()
