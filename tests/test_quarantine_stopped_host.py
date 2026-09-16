from types import SimpleNamespace
import pytest
from scripts.fleet import quarantine_scope as Q,quarantine_renewal_control as K
from scripts.fleet.coordinator import Coordinator,Conflict,digest
from scripts.fleet.capacity_policy import model_admission_barriers,validate_host_capacities
from test_quarantine_scope import setup

def fixture(tmp_path):
 c,now,claims,args=setup(tmp_path);Q.isolate(c,**args)
 c.end('model','healthy',evidence='submitted without response',outcome='uncertain',**claims['0007'])
 c.finish(**claims['0020'],evidence='stopped without model',disposition='hold')
 op=next(o for o in c.status()['operations']if o['operation_id']=='healthy')
 a=dict(operation_id='healthy',operation_sha256=digest(op),workbook='0007',owner=claims['0007']['owner'],fence=1,source_sha256='a'*64,version='b'*64,host='hhu-server-2',authorization=args['authorization'],evidence=args['evidence'],change_id='second',expected_revision=1,expires_at=1900)
 return c,now,claims,dict(scope_operation='fleet-stopped-host-quarantine/v1',arguments=a)

def test_original_api_second_unknown_blocks_host_preserves_all_effects(tmp_path):
 c,now,claims,p=fixture(tmp_path);before=c.status();r=K.apply_operation(p,c,Q);after=c.status()
 assert before['operations']==after['operations'] and before['workbooks']==after['workbooks']
 assert after['quarantines']==before['quarantines']+[r]
 assert after['quarantine_host_capacity']==before['quarantine_host_capacity']
 assert len(model_admission_barriers(after,'0020',claims['0020']['owner']))==2
 assert not model_admission_barriers(after,'0010',claims['0010']['owner'])
 assert c.begin('model','healthy-other-host',request_sha256='f'*64,**claims['0010'])['execute']

def test_full_database_copy_uses_original_api_without_live_changes(tmp_path):
 c,now,claims,p=fixture(tmp_path);before=c.status();O=SimpleNamespace(Coordinator=lambda path:Coordinator(path,clock=lambda:now[0]))
 proof=K.copied_proof((p,dict(database=c.database),O,Q,before),tmp_path/'proof')
 assert proof['status']=='FULL_DATABASE_COPY_SCOPE_PASS' and c.status()==before

@pytest.mark.parametrize('change',[{'operation_id':'foreign'},{'owner':'hhu-server-2:foreign'},{'fence':2},{'operation_sha256':'f'*64},{'expires_at':2100}])
def test_changed_second_unknown_cannot_change_existing_authority(tmp_path,change):
 c,now,claims,p=fixture(tmp_path);before=c.status();p['arguments'].update(change)
 with pytest.raises((ValueError,Conflict)):K.apply_operation(p,c,Q)
 assert c.status()==before
