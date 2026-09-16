import json
import pytest
from scripts.fleet import quarantine_renewal_control as K

def fixture(tmp_path):
 def put(n,v):
  p=tmp_path/n;p.write_text(json.dumps(v));return K.binding(p)
 started=put('started.json',dict(attempt_id='unknown',index=0,request_sha256='a'*64,started_at_ns=2))
 error=put('error.json',dict(category='timeout',completed_at_ns=3,delay_seconds=None,response_body_present=False,retry=False,stage='open'))
 e=dict(transport_started=started,connection_error=error)
 p=dict(attempt_id='unknown',max_transport_retries=0,uncertain_completion_policy='retain_incomplete_never_replay')
 s=dict(attempt_id='unknown',request=dict(sha256='a'*64),submitted_at_ns=1)
 o=dict(evidence={'transport-00.started.json':started['sha256'],'transport-00.connection-error.json':error['sha256']},sealed_at_ns=4)
 mapping=[dict(copy=r['path'])for r in e.values()]
 return e,p,s,o,mapping

def test_sealed_physical_timeout(tmp_path):K.timeout_evidence(*fixture(tmp_path))

@pytest.mark.parametrize('change',['body','retry','foreign','unsealed','response','replay','before_submit','not_custody'])
def test_timeout_rejects_different_transport_or_authority(tmp_path,change):
 e,p,s,o,m=fixture(tmp_path)
 if change in ('body','retry','foreign'):
  name='transport_started'if change=='foreign'else'connection_error';row=e[name];v=json.loads(open(row['path']).read());v[{'body':'response_body_present','retry':'retry','foreign':'attempt_id'}[change]]=True if change!='foreign'else'other';open(row['path'],'w').write(json.dumps(v));e[name]=K.binding(row['path']);o['evidence']['transport-00.started.json'if name=='transport_started'else'transport-00.connection-error.json']=e[name]['sha256']
 elif change=='unsealed':o['evidence']['transport-00.started.json']='b'*64
 elif change=='response':o['evidence']['response.raw.json']='b'*64
 elif change=='replay':p['max_transport_retries']=1
 elif change=='before_submit':s['submitted_at_ns']=5
 elif change=='not_custody':m=[]
 with pytest.raises(ValueError):K.timeout_evidence(e,p,s,o,m)
