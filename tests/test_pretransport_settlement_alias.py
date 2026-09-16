import copy,json
from pathlib import Path
import pytest
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.migration import binding
from scripts.fleet import launch_job_v3 as L
from scripts.batch import harbor_model_pretransport_recovery as R


def fixture(tmp):
 def save(name,value):
  p=tmp/name;p.write_text(json.dumps(value));return binding(p)
 c=Coordinator(tmp/'db.sqlite');c.enroll('0001','a'*64,'source');c.reconcile('0001','stopped')
 claim=c.claim('0001','vm1:original','oldclaim','b'*64);owner={k:claim[k]for k in('workbook','owner','token','fence')}
 c.begin('model','oldop',request_sha256='c'*64,**owner);c.end('model','oldop',evidence='preHTTP',outcome='uncertain',**owner)
 job=dict(workbook='0001',source=dict(path='/source',sha256='a'*64),version='b'*64);jb=save('job.json',job);intent=save('worker-intent.json',dict(owner=owner['owner'],job=jb))
 outer=save('worker-outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',returncode=1,failure_type=None,result=None,automatic_retry=False,intent=intent))
 refused=save('refused.json',dict(transport_started=False));process=save('process.json',dict(job=jb,pid=123,start_ticks='456'))
 proof=dict(schema_version='fleet-pretransport-stop-proof/v1',operation='oldop',no_transport=True,pid_exists=False,matching_processes=[],worker_outcome=outer,worker_intent=intent,refused=refused,process=process,pid=123,start_ticks='456',executor='/executor',executor_inventory=[dict(path='/executor/request.json',sha256='c'*64)],executor_outcome=dict(status='incomplete'))
 other=dict(schema_version='raw4-stopped-pretransport-proof/v1',operation_id='oldop',workbook='0001',owner=owner['owner'],fence=1,source_sha256='a'*64,request_sha256='c'*64,no_transport=True,provider_calls=0,matching_processes=[],automatic_replay=False,consumed_budget_preserved=True,executor=proof['executor'],executor_inventory=proof['executor_inventory'],outcome=proof['executor_outcome'])
 for key in ('worker_intent','worker_outcome','refused'):other[key]=dict(binding=proof[key],value=json.loads(Path(proof[key]['path']).read_text()))
 alias=dict(proof=save('external-proof.json',other),before=save('before.json',c.status()))
 result=c.reconcile_operation('model','oldop',json.dumps(alias['proof']),'abandoned');alias['operation_response']=save('op-response.json',result);alias['middle']=save('middle.json',c.status())
 response=c.reconcile('0001',json.dumps(alias['proof']),'hold');alias['claim_response']=save('claim-response.json',response);after=c.status();alias['after']=save('after.json',after)
 receipt=dict(proof=save('proof.json',proof),result=result,after=after,workbook_unchanged=False,model_replay=False,original_attempt_consumed=True,evidence_alias=alias,recovery_reader=binding(Path(R.__file__).resolve()))
 p=dict(new_owner='vm1:new',expected_owner=owner['owner'],expected_fence=1,expected_version='b'*64,prior_outcome=outer,prior_process=process,prior_process_copy=process,offline_bindings=None,stopped_model_reconciliation=save('receipt.json',receipt))
 return c,after,p,job,receipt,proof,save


def test_real_two_step_settlement_retains_all_consumed_operations(tmp_path):
 c,status,p,job,receipt,proof,save=fixture(tmp_path);before=copy.deepcopy(status)
 assert R.reconciliation_evidence(proof,receipt)==receipt['evidence_alias']['proof']
 assert L.central_gate(status,p,job)['owner']is None
 assert status==before and c.status()['operations']==before['operations']

@pytest.mark.parametrize('fault',['source','operation','owner','inventory','outcome','refused','old_checkpoint','after_checkpoint','fence','policy','operation_response','claim_response','live_owner','reader','proof_alias'])
def test_rejects_changed_settlement_or_live_custody(tmp_path,fault):
 c,status,p,job,r,proof,save=fixture(tmp_path);a=r['evidence_alias']
 if fault in ('source','operation','owner','inventory','outcome','refused'):
  v=R.read(a['proof'])
  if fault=='source':v['source_sha256']='f'*64
  elif fault=='operation':v['operation_id']='other'
  elif fault=='owner':v['owner']='foreign'
  elif fault=='inventory':v['executor_inventory']=[]
  elif fault=='outcome':v['outcome']={}
  else:v['refused']['value']={}
  a['proof']=save('changed-external.json',v)
 elif fault in ('old_checkpoint','after_checkpoint','fence','policy'):
  key='before'if fault=='old_checkpoint'else'after';v=R.read(a[key])
  if fault in ('old_checkpoint','after_checkpoint'):v['workbooks'][0]['checkpoint']='changed'
  elif fault=='fence':v['workbooks'][0]['fence']=4
  else:v['publication_limit']=2
  a[key]=save('changed-status.json',v)
 elif fault in ('operation_response','claim_response'):a[fault]=save('bad-response.json',{})
 elif fault=='live_owner':status['workbooks'][0]['owner']='foreign'
 elif fault=='reader':r['recovery_reader']=dict(r['recovery_reader'],sha256='f'*64)
 else:r['proof']=save('bad-independent.json',dict(proof,executor='/other'))
 p['stopped_model_reconciliation']=save('changed-receipt.json',r)
 with pytest.raises((ValueError,KeyError)):L.central_gate(status,p,job)
