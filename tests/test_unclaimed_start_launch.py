import base64,copy,json,time
from pathlib import Path
import pytest
from scripts.fleet import launch_job_v3 as L
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.migration import binding


def fixture(tmp):
 def put(name,value):
  p=tmp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return binding(p)
 source=put('source.json',{});task=put('task.json',dict(workbook='0001'));worker=tmp/'workers/first';remote=tmp/'processes/first.json'
 job=dict(workbook='0001',source=source,version='b'*64,run=str(worker),result=str(tmp/'unused-task/result.json'));jb=put('old-job.json',job)
 old=dict(job=jb,root_plan=task,new_owner='vm1:new',expected_fence=1,expected_version='b'*64,remote_receipt=str(remote),offline_bindings=None)
 oldrow=put('old-plan.json',old);ri=put('root-intent.json',dict(plan=oldrow));process=dict(job=jb,root_intent=ri,pid=123,start_ticks='456',command=['python','--job',jb['path'],'--owner','vm1:new']);pr=put('processes/first.json',process)
 launched=put('launched.json',dict(intent=ri,remote=process));wi=put('workers/first/intent.json',dict(job=jb,owner='vm1:new',claim_id='unused-uuid',automatic_retry=False,created_at_ns=1))
 log=remote.with_suffix('.log');log.write_text('claim = client.call("claim", workbook=job["workbook"]\nRuntimeError: coordinator HTTP 500: coordinator error; reconcile uncertain mutations before retry\n')
 files=[dict(binding(r['path']),base64=base64.b64encode(Path(r['path']).read_bytes()).decode())for r in[wi,pr,binding(log)]]
 native=put('native.json',dict(at=time.time(),processes=[],boundary=dict(worker_files=['intent.json'],author_output_exists=False,pid_exists=False),files=files))
 c=Coordinator(tmp/'coordinator.sqlite');c.enroll('0001',source['sha256'],'source');c.reconcile('0001','first');claim=c.claim('0001','vm1:old','old-uuid','b'*64);auth={k:claim[k]for k in('workbook','owner','token','fence')};c.finish(**auth,evidence='prior',disposition='hold');c.reconcile('0001',json.dumps(ri),disposition='ready',checkpoint={'continuation':task});state=c.status()['workbooks'][0]
 proof=dict(database=str(tmp/'coordinator.sqlite'),event_high_water=1,schema_version='fleet-unclaimed-start-absence/v1',status='UNCLAIMED_START_ABSENCE_VERIFIED',at=time.time(),claim_id='unused-uuid',claims=[],events=[],automatic_retry=False,claims_changed=0,models_dispatched=0,native_inventory=native,prior_launch=launched,worker_intent=wi,workbook=state)
 proofrow=put('proof.json',proof);newjob=dict(job,run=str(tmp/'workers/second'));p=dict(old,job=put('new-job.json',newjob),remote_receipt=str(tmp/'processes/second.json'),unclaimed_start=proofrow)
 return c,p,newjob,proof,put


def test_real_ready_transition_is_preserved_and_unclaimed_effects_checked(tmp_path):
 c,p,job,proof,_=fixture(tmp_path);status=copy.deepcopy(c.status());old,process=L.unclaimed_start(p,job,live=status['workbooks'][0],native=True)
 assert old['run']!=job['run']and process['pid']==123
 assert L.central_gate(status,p,job)==status['workbooks'][0]
 assert {k:v for k,v in c.status().items()if k!='at'}=={k:v for k,v in status.items()if k!='at'}

@pytest.mark.parametrize('fault',['claim','event','source','fence','owner','checkpoint','result','run','extra_file','changed_log','changed_intent','other_task'])
def test_rejects_claim_effect_or_changed_authority(tmp_path,fault):
 c,p,job,proof,put=fixture(tmp_path);live=c.status()['workbooks'][0]
 if fault in('claim','event'):proof['claims'if fault=='claim'else'events']=[{'changed':True}]
 elif fault in('source','fence','owner','checkpoint'):
  key={'source':'source_sha256'}.get(fault,fault);live[key]=99 if fault=='fence'else'foreign'
 elif fault=='result':job['result']=str(tmp_path/'other/result.json')
 elif fault=='run':job['run']=str(tmp_path/'workers/first')
 elif fault=='extra_file':(tmp_path/'workers/first/claim.json').write_text('{}')
 elif fault=='changed_log':(tmp_path/'processes/first.log').write_text('other failure')
 elif fault=='changed_intent':(tmp_path/'workers/first/intent.json').write_text('{}')
 else:p['root_plan']=put('foreign-task.json',dict(workbook='0002'))
 p['unclaimed_start']=put('changed-proof.json',proof)
 with pytest.raises(ValueError):L.unclaimed_start(p,job,live=live,native=True)

@pytest.mark.parametrize('message,ok',[(b'TimeoutError: timed out\n',False),(b'TimeoutError: other timeout\n',False),(b'scripts.fleet.coordinator.Conflict: host author capacity occupied; await finish or exact stopped reconciliation\n',True),(b'scripts.fleet.coordinator.Conflict: target active\n',False),(b'RuntimeError: coordinator HTTP 403\n',False)])
def test_exact_capacity_denial_has_same_closed_absence_boundary(tmp_path,message,ok):
 c,p,job,proof,put=fixture(tmp_path)
 n=json.loads(Path(proof['native_inventory']['path']).read_bytes());log=tmp_path/'processes/first.log'
 log.write_bytes(b'claim = client.call("claim", workbook=job["workbook"]\n'+message)
 for row in n['files']:
  if row['path']==str(log):row.update(binding(log),base64=base64.b64encode(log.read_bytes()).decode())
 proof['native_inventory']=put('capacity-native.json',n);p['unclaimed_start']=put('capacity-proof.json',proof)
 if ok:L.unclaimed_start(p,job,live=c.status()['workbooks'][0],native=True)
 else:
  with pytest.raises(ValueError):L.unclaimed_start(p,job,live=c.status()['workbooks'][0],native=True)

@pytest.mark.parametrize('fault',[None,'source','tracker','minimum','maximum','extra_input','command'])
def test_unclaimed_only_bounded_renewal_window_may_change(tmp_path,fault):
 c,p,job,proof,put=fixture(tmp_path)
 launch=json.loads(Path(proof['prior_launch']['path']).read_bytes());intent=json.loads(Path(launch['intent']['path']).read_bytes());old=json.loads(Path(intent['plan']['path']).read_bytes());oldjob=json.loads(Path(old['job']['path']).read_bytes())
 previous=dict(schema_version='fleet-native-job-lease-runway/v1',workbook='0001',source_sha256='a'*64,tracker={'path':'/actual/tracker','sha256':'b'*64},required_seconds=3600)
 oldrun=put('old-runway.json',previous);oldjob['inputs']=[oldrun];old['native_lease_runway']=oldrun
 oldjb=put('old-job.json',oldjob);old['job']=oldjb;oldrow=put('old-plan.json',old);intent['plan']=oldrow;ri=put('root-intent.json',intent);launch['intent']=ri;launch['remote'].update(job=oldjb,root_intent=ri);proof['prior_launch']=put('launched.json',launch)
 n=json.loads(Path(proof['native_inventory']['path']).read_bytes())
 for r in n['files']:
  if r['path'].endswith('/intent.json'):
   v=json.loads(base64.b64decode(r['base64']));v['job']=oldjb;Path(r['path']).write_text(json.dumps(v));proof['worker_intent']=binding(r['path'])
  elif r['path'].endswith('/first.json'):Path(r['path']).write_text(json.dumps(launch['remote']))
  r.update(binding(r['path']),base64=base64.b64encode(Path(r['path']).read_bytes()).decode())
 proof['native_inventory']=put('updated-native.json',n);proof['workbook']['reason']=json.dumps(ri)
 selected=dict(previous,required_seconds=1500,maximum_seconds=3330)
 if fault=='source':selected['source_sha256']='c'*64
 elif fault=='tracker':selected['tracker']={'path':'/other','sha256':'b'*64}
 elif fault=='minimum':selected['required_seconds']=599
 elif fault=='maximum':selected['maximum_seconds']=3601
 newrun=put('new-runway.json',selected);job=dict(oldjob,run=job['run'],inputs=[newrun]);p=dict(old,job=put('new-job.json',job),remote_receipt=str(tmp_path/'processes/second.json'),native_lease_runway=newrun,unclaimed_start=put('updated-proof.json',proof))
 if fault=='extra_input':job['inputs'].append(put('unbound.json',{}))
 elif fault=='command':job['command']=['other']
 if fault:
  with pytest.raises(ValueError):L.unclaimed_start(p,job,native=True)
 else:L.unclaimed_start(p,job,native=True)


@pytest.mark.parametrize('fault',[None,'missing','foreign_owner','wrong_evidence','changed_current','missing_current','future_inventory'])
def test_timeout_requires_actual_durable_tombstone_and_live_authority(tmp_path,fault):
 c,p,job,proof,put=fixture(tmp_path)
 n=json.loads(Path(proof['native_inventory']['path']).read_bytes());log=tmp_path/'processes/first.log'
 log.write_bytes(b'claim = client.call("claim", workbook=job["workbook"]\nTimeoutError: timed out\n')
 for row in n['files']:
  if row['path']==str(log):row.update(binding(log),base64=base64.b64encode(log.read_bytes()).decode())
 proof['native_inventory']=put('timeout-native.json',n)
 receipt=c.abandon_claim(workbook='0001',owner=p['new_owner'],claim_id=proof['claim_id'],version=job['version'],evidence=proof['native_inventory'])
 if fault=='foreign_owner':receipt['owner']='foreign'
 elif fault=='wrong_evidence':receipt['evidence']=binding(log)
 elif fault=='future_inventory':receipt['abandoned_at']=n['at']-1
 if fault!='missing':proof['abandoned_claim']=put('retired.json',receipt)
 p['unclaimed_start']=put('timeout-proof.json',proof)
 status=c.status()
 if fault=='changed_current':status['abandoned_claims'][0]['version']='foreign'
 elif fault=='missing_current':status['abandoned_claims']=[]
 if fault:
  with pytest.raises(ValueError):L.central_gate(status,p,job)
 else:
  assert L.central_gate(status,p,job)==status['workbooks'][0]
  L.unclaimed_start(p,job,native=True)
  from scripts.fleet.coordinator import Conflict
  with pytest.raises(Conflict,match='permanently abandoned'):
   c.claim('0001',p['new_owner'],proof['claim_id'],job['version'])
