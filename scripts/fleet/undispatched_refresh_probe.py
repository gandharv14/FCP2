"""Read-only native applicability probe for a real recovered phase-one result.

Run this under the original 3300-second / 8-GiB process-group guard. The selected
owning refresh prepares immutable evidence but cannot commit a tracker or call a
model. A later actual continuation uses its own fresh preparation and lease.
"""
from pathlib import Path
from dataclasses import replace
import argparse,hashlib,importlib.util,json,os,sys,time


def require(ok,message):
 if not ok:raise ValueError(message)

def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'probe symlink')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(row):
 require(binding(row['path'])==row,'probe evidence changed');return json.loads(Path(row['path']).read_bytes())

def module(name,row):
 require(binding(row['path'])==row,'probe code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def run(driver,bound,directory):
 require(os.environ.get('HARBOR_FLEET_CONTINUATION_READ_ONLY')=='1','explicit nonexecuting probe required')
 tail=module('selected_actual_phase1_reader',driver)
 p,D,n,helper,env,worker,P,recovery,A,dispatcher,phase,emitted=tail.load(bound)
 root=Path(directory).absolute();require(not root.exists(),'probe already exists; retain and reconcile')
 base=env[0];before=binding(p['tracker']['path']);require(before==p['tracker'],'actual stopped tracker differs')
 lease=base.decode(phase['lease']);base.worker._active(worker,lease)
 require(base.SECONDS==3300 and base.MEMORY==8*1024**3,'original guards differ')
 def forbidden(*a,**k):raise ValueError('owning mutation or model call forbidden in refresh applicability probe')
 base.executor.execute=forbidden;base.executor._open=forbidden
 from scripts.batch import harbor_environment_refresh as F
 from scripts.batch import harbor_tracker_lifecycle as life
 F.commit=forbidden;F.reconcile=forbidden;life.lease_operation=forbidden
 base.worker.HarborLaneLifecycle.complete=forbidden;base.worker.HarborLaneLifecycle.record_failure=forbidden
 oldplan=read(n['old_plan']);old=base.content_codec.decode(read(oldplan['research'])['selection']);content=old.content;r=P.selection(n['prepared']).run
 bindings={**content.bindings,'normalization_partitioned_review':emitted['normalization_partitioned_review'],
  'normalization_draft':binding(r/'draft.json'),'normalizer':binding(r/f'normalize_{p["workbook"]}.py'),
  'normalization_prepared_manifest':n['prepared'],'normalized_phase1':emitted['phase1'],
  'normalized_final':binding(r/'normalized.json'),'normalization_report':binding(r/'normalization_report.json'),
  'normalization_exclusions':binding(r/'exclusions.json')}
 current=replace(old,content=replace(content,bindings=bindings,normalization_run=r,normalization_prepared=n['prepared'],normalization_call=None))
 original,last,_=F.predecessor(worker,oldplan['research'],current=current)
 last='normalization_phase1'if F.unchanged(original,current)else last
 require(last=='normalization_phase1','actual completed phase-one predecessor required')
 root.mkdir(parents=True,mode=0o700);A.retain(root/'intent.json',dict(driver=driver,plan=bound,tracker=before,timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=False,owning_operations=False,automatic_retry=False))
 start=time.monotonic();prepared=F.prepare(worker,lease,oldplan['research'],current,last,root/'prepared')
 proof,validated=F.validate(worker,prepared)
 require(proof['last']=='normalization_phase1'and base.content_codec.encode(validated)==base.content_codec.encode(current),'native refresh selected a different prefix')
 require(binding(p['tracker']['path'])==before,'read-only refresh changed tracker')
 return A.retain(root/'result.json',dict(status='ACTUAL_RECOVERED_PHASE1_REFRESH_APPLICABILITY_PASSED',workbook=p['workbook'],plan=bound,driver=driver,prepared=prepared,tracker=before,elapsed_seconds=time.monotonic()-start,models_dispatched=0,owning_operations=0,semantic_budget_reset=False,actual_commit_validated=False,automatic_retry=False))

def main():
 p=argparse.ArgumentParser();p.add_argument('--driver',required=True);p.add_argument('--driver-sha256',required=True);p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--output',required=True)
 a=p.parse_args();print(json.dumps(run(dict(path=a.driver,sha256=a.driver_sha256),dict(path=a.plan,sha256=a.plan_sha256),a.output)))
if __name__=='__main__':main()
