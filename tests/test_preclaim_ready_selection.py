import json,time,copy
from pathlib import Path
import pytest
from scripts.fleet import launch_job_v3 as L
from scripts.fleet.migration import binding

def fixture(t):
 def put(n,v):
  p=t/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return binding(p)
 tracker=put('tracker.json',{});task=put('task.json',dict(workbook='0010'));runway=put('old-runway.json',dict(schema_version='fleet-native-job-lease-runway/v1',tracker=tracker,required_seconds=600,workbook='0010',source_sha256='a'*64))
 oldjob=dict(run=str(t/'workers/old'),result=str(t/'task/result.json'),inputs=[task,runway],workbook='0010',source=dict(sha256='a'*64),version='b'*64)
 old=dict(job=put('old-job.json',oldjob),root_plan=task,native_lease_runway=runway,remote_receipt=str(t/'process/old.json'),expected_fence=1,expected_version='b'*64)
 oi=put('launch/intent.json',dict(plan=put('old-plan.json',old),automatic_retry=False));ready=dict(state='ready',owner=None,claim_id=None,expires=None,token=None,fence=1,workbook='0010',source_sha256='a'*64,version='b'*64,reason=json.dumps(oi),checkpoint=json.dumps(dict(continuation=task)),updated=time.time()-5)
 r=put('launch/ready.json',ready);u=put('launch/uncertain.json',dict(intent=oi,phase='launch',status='RECONCILIATION_REQUIRED',failure_type='RuntimeError',automatic_retry=False));a=put('absence.json',dict(status='ORIGINAL_NATIVE_PRESPAWN_BOUNDARY_UNCONSUMED',at_ns=time.time_ns(),absent={oldjob['run']:False,str(Path(oldjob['result']).parent):False,old['remote_receipt']:False,str(Path(old['remote_receipt']).with_suffix('.log')):False},processes=[],models=0,claims=0,tracker=tracker))
 proof=dict(schema_version='fleet-preclaim-ready-selection/v1',prior_intent=oi,prior_ready=r,prior_uncertain=u,native_absence=a,claims=[],events=[],event_high_water=1,at=time.time(),database='/actual/db',workbook=ready)
 newrun=put('new-runway.json',dict(json.loads(Path(runway['path']).read_bytes()),required_seconds=120));job=dict(oldjob,run=str(t/'workers/new'),inputs=[task,newrun]);p=dict(old,job=put('new-job.json',job),native_lease_runway=newrun,remote_receipt=str(t/'process/new.json'),preclaim_ready_selection=put('proof.json',proof));return p,job,proof,put

@pytest.mark.parametrize('fault',[None,'claim','event','owner','task','source','intent','effect','runway'])
def test_exact_prespawn_ready_reselection(tmp_path,fault):
 p,j,proof,put=fixture(tmp_path)
 if fault=='claim':proof['claims']=[{}]
 elif fault=='event':proof['events']=[{}]
 elif fault=='owner':proof['workbook']['owner']='foreign'
 elif fault=='task':p['root_plan']=put('other.json',{})
 elif fault=='source':j['source']={'sha256':'f'*64}
 elif fault=='intent':proof['prior_intent']=put('foreign-intent.json',{})
 elif fault=='effect':(tmp_path/'task').mkdir()
 elif fault=='runway':j['inputs'].append(put('extra.json',{}))
 p['preclaim_ready_selection']=put('proof-new.json',proof)
 if fault:
  with pytest.raises((ValueError,KeyError)):L.preclaim_ready_selection(p,j,live=proof['workbook'],native=True)
 else:assert L.preclaim_ready_selection(p,j,live=proof['workbook'],native=True)['run']!=j['run']
