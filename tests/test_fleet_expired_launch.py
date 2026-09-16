import copy,json,time
from pathlib import Path
import pytest
from scripts.fleet import launch_job_v3 as L
from scripts.fleet.migration import binding
from test_fleet_quarantine import setup

def fixture(tmp):
 c,now,claims,args=setup(tmp);c.quarantine_model(**args)
 def keep(name,value):
  p=tmp/name;p.write_text(json.dumps(value));return binding(p)
 source=dict(path='/original.xlsx',sha256='a'*64)
 job=keep('job.json',dict(workbook='0140',source=source,version='b'*64))
 intent=keep('intent.json',dict(job=job,owner=claims['0140']['owner']))
 prior=keep('outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',status='RECONCILIATION_REQUIRED',automatic_retry=False,intent=intent))
 c.heartbeat(checkpoint=dict(intent=intent,pid=123),lease_seconds=10,**claims['0140']);now[0]=1011
 status=c.status();row=next(w for w in status['workbooks']if w['workbook']=='0140')
 assert row['reason']=='lease expired; reconcile remote process and artifacts'
 p=dict(new_owner='hhu-server-7:slot-1:next',expected_fence=1,expected_owner=claims['0140']['owner'],expected_version='b'*64,prior_outcome=prior,offline_bindings=None)
 return c,status,row,p,dict(workbook='0140',source=source)

def test_actual_expiration_retains_identity_and_authenticates_stopped_intent(tmp_path):
 c,s,row,p,job=fixture(tmp_path);before=copy.deepcopy(s)
 assert L.central_gate(s,p,job)==row and s==before
 assert c.status()==before

@pytest.mark.parametrize('kind',['foreign_intent','missing_intent','pid','live','nan','reason','owner','source','version','fence','outcome','active','publication'])
def test_expired_claim_never_bypasses_stopped_identity_or_global_barriers(tmp_path,kind):
 c,s,row,p,job=fixture(tmp_path)
 if kind=='foreign_intent':row['checkpoint']=json.dumps(dict(intent=dict(path='/foreign',sha256='f'*64),pid=123))
 elif kind=='missing_intent':row['checkpoint']='{}'
 elif kind=='pid':row['checkpoint']=json.dumps(dict(intent=json.loads(row['checkpoint'])['intent'],pid=True))
 elif kind=='live':row['expires']=time.time()+1000
 elif kind=='nan':row['expires']=float('nan')
 elif kind=='reason':row['reason']='different uncertain failure'
 elif kind in ('owner','source','version','fence'):
  key='source_sha256'if kind=='source'else kind;row[key]=2 if kind=='fence'else'foreign'
 elif kind=='outcome':
  q=Path(p['prior_outcome']['path']);v=json.loads(q.read_bytes());v['automatic_retry']=True;q.write_text(json.dumps(v));p['prior_outcome']=binding(q)
 elif kind=='active':row['state']='active'
 else:s['operations'].append(dict(kind='publication',state='hold',workbook='0140'))
 with pytest.raises((ValueError,KeyError)):L.central_gate(s,p,job)
