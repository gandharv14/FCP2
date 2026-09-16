import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recursion_reconcile_evaluation as r


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name); self.prefix = 'correction-content-1-opus'
        self.request = {'name':'exact campaign','description':'source corrections','metadata':{'schemaVersion':1,'attemptsPerProblem':3,'successThreshold':1,'retryPolicy':{'maxInfraRetries':2}},'tags':['audit'],'solvers':[{'displayName':'Opus 5 thinking max','runConfigVersionId':'opus-config','concurrency':50}],'problems':[{'problemId':'problem-a','problemVersionId':'version-a','environmentId':'environment'}]}
        self.request_path = self.work / (self.prefix+'.request.json'); r.c.save(self.request_path,self.request)
        self.intent_path = self.work / (self.prefix+'.intent.json'); r.c.save(self.intent_path,{'args':['evaluations','bulk-create'],'payload':str(self.request_path),'createdAt':'2026-09-15T00:00:00Z'})
        self.detail = {'id':'evaluation-a','kind':'solve_and_grade','name':self.request['name'],'description':self.request['description'],'createdAt':'2026-09-15T00:00:02Z','metadata':self.request['metadata'],'nAttemptsPerProblem':3,'tags':['audit'],'graderRunConfigVersionId':None,'totalSolvers':1,'totalProblems':1,'solvers':[{'id':'solver-a','evaluationId':'evaluation-a','displayName':'Opus 5 thinking max','runConfigVersionId':'opus-config','metadata':{'schemaVersion':1,'concurrency':50}}],'problems':[{'id':'slot-a','evaluationId':'evaluation-a',**self.request['problems'][0]}]}

    def run_with(self, details, pages=None, **kwargs):
        byid = {d['id']:d for d in details}
        if pages is None: pages = [{'items':[{'id':d['id'],'name':d['name'],'createdAt':d['createdAt']} for d in details],'nextCursor':None}]
        pages = iter(pages)
        def cli(*args, **options):
            self.assertIn(args[:2], [('evaluations','list'),('evaluations','get')])
            self.assertFalse(options)
            return next(pages) if args[1]=='list' else copy.deepcopy(byid[args[-1]])
        with patch.object(r.c,'cli',side_effect=cli), patch.object(r.c,'mutation') as mutate:
            try: return r.reconcile(self.work,self.prefix,**kwargs)
            finally: mutate.assert_not_called()

    def test_exact_unique_candidate_recovers_hash_bound_response(self):
        result = self.run_with([self.detail]); self.assertEqual(result['evaluationId'],'evaluation-a')
        self.assertEqual(r.c.read(Path(result['responsePath'])),self.detail)
        evidence = r.c.read(Path(result['evidencePath']))
        self.assertEqual(evidence['recoveredResponseSha256'],r.sha(result['responsePath']))
        self.assertEqual(evidence['requestArtifactHashes'][str(self.request_path.resolve())],r.sha(self.request_path))
        self.assertTrue(self.intent_path.exists())

    def test_wrong_versions_models_concurrency_and_metadata_rejected(self):
        variants=[]
        for field,value in [('problemVersionId','wrong-version'),('environmentId','wrong-environment')]:
            d=copy.deepcopy(self.detail);d['problems'][0][field]=value;variants.append(d)
        for field,value in [('runConfigVersionId','wrong-model'),('displayName','Gemini')]:
            d=copy.deepcopy(self.detail);d['solvers'][0][field]=value;variants.append(d)
        d=copy.deepcopy(self.detail);d['solvers'][0]['metadata']['concurrency']=49;variants.append(d)
        d=copy.deepcopy(self.detail);d['metadata']['retryPolicy']['maxInfraRetries']=3;variants.append(d)
        for detail in variants:
            with self.subTest(detail=detail):
                with self.assertRaisesRegex(ValueError,'found 0'):self.run_with([detail])
                self.assertFalse((self.work/(self.prefix+'.response.json')).exists())

    def test_duplicate_exact_candidates_fail_closed(self):
        second=copy.deepcopy(self.detail);second['id']='evaluation-b'
        for item in second['problems']+second['solvers']:item['evaluationId']='evaluation-b'
        with self.assertRaisesRegex(ValueError,'found 2'):self.run_with([self.detail,second])
        self.assertFalse((self.work/(self.prefix+'.response.json')).exists())

    def test_duplicate_slots_fail_closed(self):
        for field in ['problems','solvers']:
            d=copy.deepcopy(self.detail);d[field].append(copy.deepcopy(d[field][0]))
            with self.assertRaisesRegex(ValueError,'found 0'):self.run_with([d])

    def test_all_pages_scanned_before_recovery(self):
        pages=[{'items':[{'id':'older','name':'different','createdAt':'2026-09-14T00:00:00Z'}],'nextCursor':'page2'}, {'items':[{'id':self.detail['id'],'name':self.detail['name'],'createdAt':self.detail['createdAt']}],'nextCursor':None}]
        result=self.run_with([self.detail],pages);evidence=r.c.read(Path(result['evidencePath']))
        self.assertEqual(evidence['listedEvaluationCount'],2)
        self.assertEqual(len(evidence['readArtifacts']),3)

    def test_old_candidate_not_recovered_without_documented_skew(self):
        d=copy.deepcopy(self.detail);d['createdAt']='2026-09-14T23:59:58Z'
        with self.assertRaisesRegex(ValueError,'found 0'):self.run_with([d])
        with self.assertRaisesRegex(ValueError,'explanation'):self.run_with([d],clock_skew_seconds=3)
        self.assertEqual(self.run_with([d],clock_skew_seconds=3,clock_skew_reason='Verified host/server offset in separate evidence')['status'],'recovered')

    def test_preexisting_response_or_wrong_intent_rejected_before_network(self):
        intent=r.c.read(self.intent_path);intent['args']=['problem-versions','create'];r.c.save(self.intent_path,intent)
        with patch.object(r.c,'cli') as cli:
            with self.assertRaisesRegex(ValueError,'not an evaluation'):r.reconcile(self.work,self.prefix)
            cli.assert_not_called()
        r.c.save(self.work/(self.prefix+'.response.json'),{'id':'already'})
        with patch.object(r.c,'cli') as cli:
            with self.assertRaisesRegex(ValueError,'already exists'):r.reconcile(self.work,self.prefix)
            cli.assert_not_called()

    def test_changed_request_during_scan_rejected(self):
        def cli(*args,**kwargs):
            if args[1]=='list':return {'items':[{'id':self.detail['id'],'name':self.detail['name'],'createdAt':self.detail['createdAt']}],'nextCursor':None}
            changed=copy.deepcopy(self.request);changed['name']='changed';r.c.save(self.request_path,changed)
            return self.detail
        with patch.object(r.c,'cli',side_effect=cli):
            with self.assertRaisesRegex(ValueError,'changed during'):r.reconcile(self.work,self.prefix)
        self.assertFalse((self.work/(self.prefix+'.response.json')).exists())


if __name__=='__main__':unittest.main()
