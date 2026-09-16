import pytest
from scripts.fleet import backlog_redistribution as R
from scripts.fleet.migration import retain

def fixture(tmp_path):
 cfg={'path':'/cfg','sha256':'a'*64};original=dict(schema_version='fleet-source-pool-start/v5',configs={'hhu-server-2':cfg});p=retain(tmp_path/'plan.json',original);intent=retain(tmp_path/'intent.json',dict(plan=p,automatic_retry=False,preflight={'hhu-server-2':dict(status='PASS',workers_started=0,config=cfg)}));return dict(enrollment_kind='initial-start',original_admission=p,enrollment_intent=intent),original

def test_actual_initial_start_shape_uses_own_bound_intent(tmp_path):
 s,p=fixture(tmp_path);R.enrollment_origin(s,p)

@pytest.mark.parametrize('bad',['foreign_plan','replay','missing_host','changed_config','failed_proof','started_worker','unknown_kind'])
def test_initial_enrollment_refuses_unrelated_or_unqualified_authority(tmp_path,bad):
 s,p=fixture(tmp_path);i=R.read(s['enrollment_intent'])
 if bad=='foreign_plan':i['plan']={'path':'/other','sha256':'a'*64}
 elif bad=='replay':i['automatic_retry']=True
 elif bad=='missing_host':i['preflight']={}
 elif bad=='changed_config':i['preflight']['hhu-server-2']['config']={'path':'/other','sha256':'b'*64}
 elif bad=='failed_proof':i['preflight']['hhu-server-2']['status']='FAIL'
 elif bad=='started_worker':i['preflight']['hhu-server-2']['workers_started']=1
 else:s['enrollment_kind']='invented'
 s['enrollment_intent']=retain(tmp_path/'changed.json',i)
 with pytest.raises(ValueError):R.enrollment_origin(s,p)
