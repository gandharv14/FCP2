"""Read-only owning prefix, original budgets, completed Calls and native custody."""
import argparse,json,hashlib,sys,os
from pathlib import Path
def record(p):return dict(path=str(Path(p).absolute()),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def checked(b):
 if record(b['path'])!=b:raise ValueError('selected evidence changed')
 return json.loads(Path(b['path']).read_bytes())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--output',required=True);p.add_argument('--check-runtime',action='store_true');a=p.parse_args()
 plan_binding=dict(path=str(Path(a.plan).absolute()),sha256=a.plan_sha256);plan=checked(plan_binding);assert plan['schema_version']=='harbor-size-hold-inspection/v1' and plan['workbook'] in {'0173','0303','0077'}
 snap=checked(plan['snapshot']);root=Path(snap['root'])
 for row in snap['files']:assert record(root/row['relative'])['sha256']==row['sha256']
 sys.path.insert(0,str(root));os.environ.update(HARBOR_FLEET_URL='http://127.0.0.1:8765',HARBOR_FLEET_CONTINUATION_READ_ONLY='1')
 from scripts import harbor_campaign as C
 from scripts.batch import harbor_model_executor as E,harbor_audit_bridge as A,harbor_lease_takeover as T,harbor_normalization_partitioned as P
 from scripts.batch.harbor_normalization_recovery import selection_from
 import importlib
 L=importlib.import_module('scripts.fleet.legacy_'+plan['workbook']);profile=L.fleet_profile();assert record(Path(L.PROFILE['path']))==plan['profile']
 home=Path(plan['home']);wb=plan['workbook'];prior=record(home/'dispatcher-v2/first/002/result.json');cp=checked(prior);descriptor=checked(cp['descriptor']);s=C.decode(descriptor['worker']);current=C.content_codec.decode(cp['selection']);C._campaign(s.campaign)
 assert record(plan['source']['path'])==plan['source']
 def forbidden(*a,**k):raise AssertionError('model dispatch forbidden in read-only probe')
 E._open=forbidden;E.execute=forbidden
 audited=[]
 for path in sorted((home/'environment/audit-bridge').glob('batch-*/prepared.json')):
  prepared=A.prepared_from(checked(record(path)));location=E.location(prepared,s.campaign);before={p.name:record(p)['sha256'] for p in location.iterdir() if p.is_file()};result=E.inspect(prepared,s.campaign)
  assert result.status=='completed' and before=={p.name:record(p)['sha256'] for p in location.iterdir() if p.is_file()}
  audited.append(dict(prepared=record(path),outcome=result.outcome))
 assert len(audited)==plan['expected_completed_calls']
 takeover=T.Selection(campaign_root=home,workbook=wb,tracker=plan['tracker'],worker_kind='environment',guard=record(home/'dispatcher-v2/first/003/resource.json'),checkpoint=prior,owner='fleet-'+wb+'-readonly-probe',config=record(home/'config.json'))
 before,old,run,evidence=T._recipe(takeover)
 gate=C.worker.lanes.evaluate_completed_gate(before,C.worker.lanes.CompletionEvidence('research',current),C.worker._context(s));assert gate['passed'],'retained research does not pass owning current gate'
 oldplan=checked(plan['original_plan']);selected=selection_from({k:oldplan[k] for k in ('workbook','bindings','run','policy','budget','first_normalization')})
 assert oldplan['budget']==plan['budget'] and selected.budget==s.normalization_budget and selected.budget.ordinal==1 and selected.budget.limit==2
 P.unused(selected,s.campaign);assert not any((selected.run/n).exists() for n in ('prepared.json','review-request.json','normalization-review.json','phase1-result.json'))
 body=P.read(record(selected.run/'review-evidence.json'));rows=P.read(record(selected.run/'analysis.json'));parts=P.K.partitions(body,lambda compact,refs:P.request(selected,compact,refs));refs=[r for group,_ in parts for r in group]
 assert len(refs)==len(set(refs)) and set(refs)=={r['reference'] for r in rows if r['clean']}
 out=Path(a.output);out.mkdir(parents=True,exist_ok=False);dependency=C._dependency(s,out) if a.check_runtime else None
 result=dict(status='READ_ONLY_COMPLETE_PREFIX_AND_CUSTODY_VALIDATED',plan=plan_binding,workbook=wb,snapshot=plan['snapshot'],source=plan['source'],tracker=plan['tracker'],takeover_recipe=T._selection_value(takeover),research_gate=gate,actual_completed_calls=audited,original_budget=plan['budget'],partitions=len(parts),request_bytes=[P.K.encoded_size(x) for _,x in parts],candidates=len(refs),atomic_rows=len(rows),native_dependency=dependency,models_dispatched=0,claims_acquired=0,tracker_modified=False,original_journals_modified=False,semantic_budget_reset=False)
 C.keep(out/'inspection.json',result);print(json.dumps(record(out/'inspection.json')))
if __name__=='__main__':main()
