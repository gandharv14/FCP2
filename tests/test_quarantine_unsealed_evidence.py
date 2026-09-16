import json,copy
import pytest
from scripts.fleet import quarantine_renewal_control as K

def fixture(tmp_path):
 def put(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return K.binding(p)
 request=put('request.json',dict(messages=['original']));native=dict(path='/actual/attempt-01/request.json',sha256=request['sha256']);scope=dict(workbook_id='0028',original_sha256='a'*64)
 submitted=put('submitted.json',dict(attempt_id='op',request=native,scope=scope,budget_evidence=[],submitted_at_ns=1))
 plan=put('plan.json',dict(attempt_id='op',request=native,scope=scope,budget_evidence=[],max_transport_retries=0,uncertain_completion_policy='retain_incomplete_never_replay'))
 started=put('transport-00.started.json',dict(attempt_id='op',index=0,request_sha256=request['sha256'],started_at_ns=2))
 rows=[]
 for b in [request,submitted,plan,started,put('identity.json',{}),put('prior-attempts.json',[])]:
  rows.append(dict(native=dict(path='/actual/attempt-01/'+b['path'].split('/')[-1],sha256=b['sha256']),copy=b,bytes=__import__('pathlib').Path(b['path']).stat().st_size))
 inventory=put('inventory.json',dict(root='/actual/attempt-01',at=3,files=rows));intent=put('intent.json',dict(workbook='0028',owner='host/slot/owner',automatic_retry=False));outer=put('worker-outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',failure_type='TimeoutError',returncode=None,result=None,automatic_retry=False,intent=intent))
 stopped=put('stopped.json',dict(at=4,copies=[dict(copy=outer,native=outer),dict(copy=intent,native=intent)]));mapping=[dict(native=r['native'],copy=r['copy']['path'])for r in rows]+[dict(native=outer,copy=outer['path'])]
 e=dict(schema_version='fleet-stopped-submitted-model-scope/v1',workbook='0028',operation_id='op',custody={},attempt_inventory=inventory,execution_plan=plan,submitted=submitted,transport_started=started,request=request,worker_outcome=outer,stopped=stopped,prior_observation={},current_status={});args=dict(operation_id='op',workbook='0028',source_sha256='a'*64,owner='host/slot/owner')
 return e,args,mapping,put

def test_unsealed_is_conservative_without_outcome_or_fabricated_response(tmp_path):
 e,a,m,_=fixture(tmp_path);K.unsealed_evidence(e,a,m)

@pytest.mark.parametrize('bad',['extra_response','extra_outcome','missing_start','other_attempt','changed_source','newer_inventory','wrong_outer','replay','wrong_request','foreign_file','missing_custody','wrong_owner'])
def test_unsealed_rejects_changed_or_terminal_custody(tmp_path,bad):
 e,a,m,put=fixture(tmp_path)
 if bad in ('extra_response','extra_outcome','missing_start','foreign_file'):
  i=K.read(e['attempt_inventory'])
  if bad.startswith('extra_'):r=copy.deepcopy(i['files'][0]);r['native']['path']='/actual/attempt-01/'+('response.raw.json'if bad=='extra_response'else'outcome.json');i['files'].append(r)
  elif bad=='missing_start':i['files']=[r for r in i['files']if not r['native']['path'].endswith('started.json')]
  else:i['files'][0]['native']['path']='/foreign/request.json'
  e['attempt_inventory']=put('altered-inventory.json',i)
 elif bad=='other_attempt':a['operation_id']='other'
 elif bad=='changed_source':a['source_sha256']='b'*64
 elif bad=='newer_inventory':i=K.read(e['attempt_inventory']);i['at']=5;e['attempt_inventory']=put('new-inventory.json',i)
 elif bad=='wrong_outer':o=K.read(e['worker_outcome']);o['returncode']=0;e['worker_outcome']=put('changed-outcome.json',o)
 elif bad=='replay':p=K.read(e['execution_plan']);p['max_transport_retries']=1;e['execution_plan']=put('changed-plan.json',p)
 elif bad=='wrong_request':e['request']=put('changed-request.json',{})
 elif bad=='missing_custody':m=[]
 elif bad=='wrong_owner':a['owner']='foreign'
 with pytest.raises(ValueError):K.unsealed_evidence(e,a,m)
