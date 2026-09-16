"""No-dispatch Linux check of explicitly selected successor audit readers."""
import argparse,json,hashlib,os,sys
from pathlib import Path
def record(p):return dict(path=str(Path(p).absolute()),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def read(p):return json.loads(Path(p).read_bytes())
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',required=True);p.add_argument('--snapshot-sha256',required=True);p.add_argument('--workbook',choices=['0073','0190'],required=True);p.add_argument('--output',required=True);a=p.parse_args()
 assert record(a.snapshot)['sha256']==a.snapshot_sha256
 snap=read(a.snapshot);root=Path(snap['root']);index=read(root/'.pipeline-code.json')
 for n,d in index.items():assert record(root/n)['sha256']==d
 sys.path.insert(0,str(root));os.environ.update(HARBOR_FLEET_URL='http://127.0.0.1:8765',HARBOR_FLEET_CONTINUATION_READ_ONLY='1')
 from scripts import harbor_campaign as C
 from scripts.batch import harbor_model_executor as E,harbor_audit_bridge as A
 if a.workbook=='0073':
  from scripts.fleet import legacy_0073 as L
  L.fleet_profile()
 else:
  from scripts.batch import harbor_normalization_existing_review as L
  L._fleet_predecessor(L.inventory(root))
 home=Path('/Users/gandharvmahajan/Documents/AlignerrProjects/xAI/FCP_2/synthetic-data-pipeline/diagnostics/authoring-queue-extension-20260911T185027Z/authoring')/(a.workbook+'-generated-'+('01' if a.workbook=='0073' else '02'))
 cp=read(home/'dispatcher-v2/first/002/result.json');s=C.decode(read(cp['descriptor']['path'])['worker']);C._campaign(s.campaign)
 def forbidden(*a,**k):raise AssertionError('no model calls authorized')
 E._open=forbidden;E.execute=forbidden
 results=[]
 for p in sorted((home/'environment/audit-bridge').glob('batch-*/prepared.json')):
  prepared=A.prepared_from(read(p));location=E.location(prepared,s.campaign);before={p.name:record(p)['sha256'] for p in location.iterdir() if p.is_file()}
  observed=E.inspect(prepared,s.campaign);assert observed.status=='completed'
  assert before=={p.name:record(p)['sha256'] for p in location.iterdir() if p.is_file()}
  results.append(dict(prepared=record(p),outcome=observed.outcome,status=observed.status))
 assert len(results)==(3 if a.workbook=='0073' else 1)
 out=Path(a.output);out.mkdir(parents=True,exist_ok=False);dependency=C._dependency(s,out)
 result=dict(status='CURRENT_LEGACY_AUDITS_VALIDATED',snapshot=record(a.snapshot),workbook=a.workbook,actual_audit_calls=results,dependency=dependency,models_dispatched=0,tracker_modified=False,historical_journals_modified=False)
 C.keep(out/'inspection.json',result);print(json.dumps(record(out/'inspection.json')))
if __name__=='__main__':main()
