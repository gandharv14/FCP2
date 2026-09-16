import json,copy
import pytest
from scripts.fleet import launch_job_v3 as L
from scripts.fleet.coordinator import Coordinator
from scripts.fleet.migration import binding


def fixture(tmp):
    def keep(name,value):
        p=tmp/name;p.write_text(json.dumps(value));return binding(p)
    c=Coordinator(tmp/'coordinator.sqlite');c.enroll('0001','a'*64,'source');c.reconcile('0001','stopped')
    claim=c.claim('0001','vm1:worker','first','b'*64);owner={k:claim[k]for k in('workbook','owner','token','fence')}
    c.begin('model','original-operation',request_sha256='c'*64,**owner)
    c.end('model','original-operation',evidence='no HTTP; parent failed',outcome='uncertain',**owner)
    source=dict(path='/source.xlsx',sha256='a'*64);job=dict(workbook='0001',source=source,version='b'*64)
    jobbind=keep('job.json',job);intent=keep('intent.json',dict(job=jobbind,owner=owner['owner']))
    prior=keep('outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',automatic_retry=False,returncode=1,failure_type=None,result=None,intent=intent))
    process=keep('process.json',dict(job=jobbind,pid=123,start_ticks='456'))
    proof=keep('proof.json',dict(schema_version='fleet-pretransport-stop-proof/v1',operation='original-operation',no_transport=True,pid_exists=False,matching_processes=[],worker_outcome=prior,worker_intent=intent,process=process,pid=123,start_ticks='456',executor='/old/executor',executor_inventory=[dict(path='/old/executor/request.json',sha256='c'*64)]))
    result=c.reconcile_operation('model','original-operation',json.dumps(proof),'abandoned');after=c.status()
    receipt=keep('receipt.json',dict(proof=proof,result=result,after=after,workbook_unchanged=True,model_replay=False,original_attempt_consumed=True))
    p=dict(new_owner='vm1:new',expected_fence=1,expected_owner=owner['owner'],expected_version='b'*64,prior_outcome=prior,prior_process=process,prior_process_copy=process,offline_bindings=None,stopped_model_reconciliation=receipt)
    return c,after,p,job


def test_real_abandoned_operation_retains_plain_hold_and_native_custody(tmp_path):
    c,status,p,job=fixture(tmp_path);before=copy.deepcopy(status)
    held=L.central_gate(status,p,job)
    assert held['reason']=='uncertain model; reconcile without replay'
    assert status==before and c.status()['workbooks']==before['workbooks']
    assert c.status()['operations']==before['operations']

@pytest.mark.parametrize('defect',['reason','fence','owner','active_operation','foreign_operation','request','proof','outcome','process','another_target','source'])
def test_refuses_changed_hold_or_unknown_effect(tmp_path,defect):
    c,status,p,job=fixture(tmp_path)
    if defect in ('reason','fence','owner'):status['workbooks'][0][defect]=42 if defect=='fence'else'foreign'
    elif defect=='active_operation':status['operations'][0]['state']='hold'
    elif defect=='foreign_operation':status['operations'][0]['owner']='foreign'
    elif defect=='another_target':status['operations'].append(dict(kind='publication',state='hold',workbook='0001'))
    elif defect=='source':job['source']['sha256']='f'*64
    else:
        receipt=L.checked(p['stopped_model_reconciliation']);proof=L.checked(receipt['proof'])
        if defect=='request':proof['executor_inventory'][0]['sha256']='f'*64
        elif defect=='proof':proof['pid_exists']=True
        elif defect=='outcome':proof['worker_outcome']={'path':'/foreign','sha256':'f'*64}
        else:proof['start_ticks']='changed'
        q=tmp_path/'changed-proof.json';q.write_text(json.dumps(proof));receipt['proof']=binding(q)
        r=tmp_path/'changed-receipt.json';r.write_text(json.dumps(receipt));p['stopped_model_reconciliation']=binding(r)
    with pytest.raises((ValueError,KeyError)):L.central_gate(status,p,job)
