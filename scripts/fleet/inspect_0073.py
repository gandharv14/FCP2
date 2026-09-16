"""Read-only native successor prefix, expired custody and full partition probe."""
import argparse,hashlib,json,os,sys
from pathlib import Path
HOME=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z/authoring/0073-generated-01')
def record(p):return dict(path=str(Path(p).absolute()),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def read(p):return json.loads(Path(p).read_bytes())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',required=True);p.add_argument('--snapshot-sha256',required=True);p.add_argument('--output',required=True);p.add_argument('--check-runtime',action='store_true');a=p.parse_args()
 assert record(a.snapshot)['sha256']==a.snapshot_sha256,'selected successor changed'
 snapshot=read(a.snapshot);root=Path(snapshot['root'])
 for f in snapshot['files']:assert record(root/f['relative'])['sha256']==f['sha256'],'successor file changed'
 sys.path.insert(0,str(root));os.environ['HARBOR_FLEET_CONTINUATION_READ_ONLY']='1';os.environ.setdefault('HARBOR_FLEET_URL','http://127.0.0.1:8765')
 from scripts import harbor_campaign as C
 from scripts.batch import harbor_environment_refresh as F,harbor_lease_takeover as T,harbor_normalization_partitioned as P
 from scripts.fleet import legacy_0073 as L
 from scripts.batch.harbor_normalization_recovery import selection_from
 L.fleet_profile()
 def forbidden(*a,**k):raise AssertionError('inspection cannot call models')
 C.executor._open=forbidden;C.executor.execute=forbidden
 prior=record(HOME/'dispatcher-v2/first/002/result.json');cp=read(prior['path']);descriptor=cp['descriptor'];worker=C.decode(read(descriptor['path'])['worker']);current=C.content_codec.decode(cp['selection'])
 tracker=record(HOME/'trackers/workbooks/0073.json')
 assert tracker['sha256']=='ef131a5274287ffce8519907616456647a9bee07a8b755864df17528197e07a4','tracker changed; root reconciliation needed'
 takeover=T.Selection(campaign_root=HOME,workbook='0073',tracker=tracker,worker_kind='environment',guard=record(HOME/'dispatcher-v2/first/003/resource.json'),checkpoint=prior,owner='fleet-0073-inspection',config=record(HOME/'config.json'))
 before,old,run,evidence=T._recipe(takeover)
 checks=[C.worker.lanes.evaluate_completed_gate(before,C.worker.lanes.CompletionEvidence('research',current),C.worker._context(worker))];assert all(x['passed'] for x in checks),'owning research prefix differs'
 plan=record(HOME/'environment/normalization/0073-variable-sources/production-selection.json');value=read(plan['path']);s=selection_from({k:value[k] for k in ('workbook','bindings','run','policy','budget','first_normalization')})
 assert s.budget==worker.normalization_budget and s.budget.ordinal==1 and s.budget.limit==2 and s.budget.attempt_id=='3973add1-8769-4a3c-b1d4-bcbf3564b79f'
 P.unused(s,worker.campaign)
 assert not any((s.run/name).exists() for name in ('prepared.json','review-request.json','normalization-review.json','phase1-result.json'))
 body=P.read(record(s.run/'review-evidence.json'));rows=P.read(record(s.run/'analysis.json'));parts=P.K.partitions(body,lambda compact,refs:P.request(s,compact,refs))
 refs=[r for group,_ in parts for r in group];assert len(refs)==len(set(refs))==242 and set(refs)=={r['reference'] for r in rows if r['clean']}
 result=dict(status='READ_ONLY_PREFIX_AND_PARTITIONS_VERIFIED',snapshot=record(a.snapshot),original_plan=plan,original_budget=value['budget'],tracker=tracker,takeover_recipe=T._selection_value(takeover),prefix_checks=checks,partitions=len(parts),request_bytes=[P.K.encoded_size(x) for _,x in parts],candidate_references=len(refs),atomic_rows=len(rows),legacy_calls=3,models_dispatched=0,tracker_modified=False,semantic_budget_reset=False)
 out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
 if a.check_runtime:result['dependency']=C._dependency(worker,out)
 C.keep(out/'inspection.json',result);print(json.dumps(record(out/'inspection.json')))
if __name__=='__main__':main()
