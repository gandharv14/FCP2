import copy,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import baseline_continue as F
from scripts import harbor_campaign as C


def receipt(tmp):
 home=tmp/'execution';directory=home/'migration';directory.mkdir(parents=True);dest=tmp/'destination';dest.mkdir()
 entry=F.keep(tmp/'entry.json',{});boundary=F.keep(tmp/'boundary.json',{});bound=F.keep(tmp/'plan.json',{})
 p=dict(home=str(home),destination=str(dest),entry=entry,boundary=boundary)
 descriptor=F.keep(dest/'descriptor.json',{})
 cfg=F.keep(dest/'config.json',{});migration=F.keep(dest/'source-continuation.json',{})
 checkpoint=F.keep(dest/'result.json',dict(status='IMPORTED',step='baseline',descriptor=descriptor,lease=C.encode(C.bootstrap.Lease('owner','private-token',3,'0'*64)),evidence=[migration]))
 intent=F.keep(directory/'intent.json',F.migration_intent(p,bound));(directory/'step.log').write_text('actual guarded child completed')
 command=[sys.executable,'-I','-B',entry['path'],'migrate-child','--plan',bound['path'],'--plan-sha256',bound['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256']]
 resource=F.keep(directory/'resource.json',dict(intent=intent,returncode=0,failure=None,seconds=900,memory_bytes=8*1024**3,log=F.binding(directory/'step.log'),command=command))
 result=F.keep(directory/'result.json',dict(status='IMPORTED',plan=bound,config=cfg,receipt=migration,descriptor=descriptor,checkpoint=checkpoint,models=0,lease_mutations=0,semantic_budget_reset=False))
 outcome=F.keep(directory/'outcome.json',dict(status='COMPLETED_OPERATION',resource=resource,result=result,automatic_retry=False))
 return p,bound,outcome


def replace_record(row,change):
 p=Path(row['path']);v=json.loads(p.read_bytes());change(v);p.write_text(json.dumps(v));return F.binding(p)


def test_actual_original_prior_accepts_typed_imported_baseline(tmp_path):
 p,b,o=receipt(tmp_path);seen=[]
 native=SimpleNamespace(selected=lambda r:seen.append(r),_prior=C._prior)
 result=F.validate_migration(p,b,o,native)
 assert seen==[result['descriptor']]
 assert C._prior(result['checkpoint'],result['descriptor'])[0]['step']=='baseline'


@pytest.mark.parametrize('kind',['guard_failure','wrong_seconds','wrong_memory','missing_isolation','wrong_plan','model_used','reset','foreign_descriptor'])
def test_partial_or_changed_migration_cannot_enter_ordinary_dispatch(tmp_path,kind):
 p,b,o=receipt(tmp_path);out=F.checked(o)
 if kind in {'guard_failure','wrong_seconds','wrong_memory','missing_isolation'}:
  def change(v):
   if kind=='guard_failure':v['returncode']=None
   elif kind=='wrong_seconds':v['seconds']=7200
   elif kind=='wrong_memory':v['memory_bytes']=16*1024**3
   else:v['command'].remove('-I')
  out['resource']=replace_record(out['resource'],change)
 else:
  def change(v):
   if kind=='wrong_plan':v['plan']=F.keep(tmp_path/'other-plan.json',{})
   elif kind=='model_used':v['models']=1
   elif kind=='reset':v['semantic_budget_reset']=True
   else:v['descriptor']=F.keep(tmp_path/'foreign-descriptor.json',{})
  out['result']=replace_record(out['result'],change)
 Path(o['path']).write_text(json.dumps(out));o=F.binding(o['path'])
 native=SimpleNamespace(selected=lambda r:pytest.fail('invalid migration reached owning boundary'),_prior=C._prior)
 with pytest.raises(ValueError):F.validate_migration(p,b,o,native)


def test_retained_history_probe_uses_actual_original_certificate_without_heartbeat(tmp_path,monkeypatch):
 from scripts.fleet import history as H
 monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_HISTORY_PATH',raising=False)
 root=tmp_path/'retained';home=root/'authoring';code=F.keep(tmp_path/'old-code.json',{'root':str(tmp_path/'version')})
 config=F.keep(tmp_path/'config.json',{'run_root':str(home)})
 plan=F.keep(tmp_path/'fresh-plan.json',{'home':str(root)})
 history=F.keep(tmp_path/'history.json',dict(schema_version=H.SCHEMA,roots=['/actual/root'],scan_roots=['/actual/root'],known_ids=['0012'],fresh_ids=['0012'],excluded={},histories={}))
 job=F.keep(tmp_path/'old-worker.json',dict(workbook='0012',source={'sha256':'source'},version='version',result=str(root/'result.json'),inputs=[plan],history=history))
 p=dict(workbook='0012',source={'sha256':'source'},original_worker_job=job);w=dict(config=config,old_code=code)
 original=H.projected_survey;monkeypatch.setattr(H,'projected_survey',original)
 for key in ('HARBOR_FLEET_WORKBOOK','HARBOR_FLEET_HISTORY_PATH','HARBOR_FLEET_HISTORY_SHA256'):monkeypatch.setenv(key,'')
 F.retained_history(p,w,probe=True)
 assert H.projected_survey([Path('/actual/root')],{'0012'})==({}, {})
 with pytest.raises(ValueError,match='scope|namespace'):H.projected_survey([Path('/fake/root')],{'0012'})
 F.retained_history(p,w,probe=False)
 monkeypatch.setenv('HARBOR_FLEET_HISTORY_SHA256','foreign')
 with pytest.raises(ValueError,match='retain'):F.retained_history(p,w,probe=False)


def test_original_intake_changes_only_namespace_and_actual_selected_lease_owner():
 from dataclasses import dataclass
 from types import SimpleNamespace
 @dataclass(frozen=True)
 class Intake:
  bootstrap: object
  environment_run: object
  owner: str
  campaign: object
  taxonomy: object
  inherited_audit_budget: object
 old=Intake('old-bootstrap','old-run','old-owner',{'attempt_id':'retained'},'original-taxonomy',None)
 current=SimpleNamespace(bootstrap='new-bootstrap',run='new-run');new=F.continuation_intake(old,current,SimpleNamespace(owner='actual-takeover-owner'))
 assert new.owner=='actual-takeover-owner' and new.bootstrap=='new-bootstrap' and new.environment_run=='new-run'
 assert new.campaign is old.campaign and new.taxonomy==old.taxonomy and new.inherited_audit_budget is None

@pytest.mark.parametrize('remaining,passes',[(4291,True),(4290,False),(1,False)])
def test_actual_live_probe_retains_no_fake_claim_and_strict_runway(monkeypatch,remaining,passes):
 from types import SimpleNamespace as N
 seen=[];lease=N(sha256='a'*64);worker=N(bootstrap=N(policy=N(tracker_root=Path('/native')),workbook='current'))
 bootstrap=N(_tracker=lambda config,actual:(seen.append((config,actual)) or {'leases':{'task_agent':{'expires_at':remaining*10**9}}}))
 C=N(bootstrap=bootstrap)
 def load(bound,require_live):
  assert require_live is False
  return {},{},None,None,worker,lease,C,None
 monkeypatch.setattr(F,'load',load);monkeypatch.setattr(F.time,'time_ns',lambda:0)
 if passes:
  value=F.probe_live({'selected':'actual'});assert value['status']=='BASELINE_LIVE_BOUNDARY_VALIDATED' and value['models']==value['lease_mutations']==0
 else:
  with pytest.raises(ValueError,match='insufficient'):F.probe_live({})
 assert seen==[(worker.bootstrap,lease)]
