import copy,math
import pytest
from scripts.fleet import lease_runway as R

def fixture():
 now=1789279000000000000
 t=dict(workbook_id='0017',revision=11,leases=dict(task_agent=dict(owner='owner',token='private-lease-token',expires_at=now+3601*10**9)))
 p=dict(schema_version='fleet-native-lease-runway/v1',workbook='0017',tracker=dict(path='/native/tracker.json',sha256='a'*64),revision=11,lease_identity_sha256=R.identity(t['leases']['task_agent']),guard_seconds=3300,memory_bytes=8589934592,launch_buffer_seconds=270)
 return p,t,now

def test_sufficient_runway_preserves_guard_and_redacts_token():
 p,t,n=fixture();v=R.validate(p,t,p['tracker'],now_ns=n);assert v['remaining_ns']==3601*10**9 and v['required_ns']==3600*10**9
 assert 'private-lease-token' not in str(v) and v['guard_seconds']==3300 and v['renewal_performed'] is False

@pytest.mark.parametrize('case',['boundary','less','expired','clock_nan','clock_float','clock_bool','clock_negative','owner','token','revision','hash','workbook','seconds','memory','buffer_small','buffer_large','expiry_float','expiry_bool','extra'])
def test_insufficient_or_wrong_native_custody_rejected(case):
 p,t,n=fixture();b=p['tracker'].copy();a=t['leases']['task_agent']
 if case=='boundary':a['expires_at']=n+3600*10**9
 if case=='less':a['expires_at']=n+3599*10**9
 if case=='expired':a['expires_at']=n-1
 if case=='clock_nan':n=float('nan')
 if case=='clock_float':n=float(n)
 if case=='clock_bool':n=True
 if case=='clock_negative':n=-1
 if case=='owner':a['owner']='other'
 if case=='token':a['token']='other'
 if case=='revision':t['revision']=12
 if case=='hash':b['sha256']='b'*64
 if case=='workbook':t['workbook_id']='0029'
 if case=='seconds':p['guard_seconds']=6600
 if case=='memory':p['memory_bytes']*=2
 if case=='buffer_small':p['launch_buffer_seconds']=0
 if case=='buffer_large':p['launch_buffer_seconds']=601
 if case=='expiry_float':a['expires_at']=float(a['expires_at'])
 if case=='expiry_bool':a['expires_at']=True
 if case=='extra':p['renew']=True
 with pytest.raises(ValueError):R.validate(p,t,b,now_ns=n)
