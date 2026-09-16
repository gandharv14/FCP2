import pytest
from scripts.fleet import quarantine_renewal_control as K

def fixture(tmp_path):
 def put(name,v):return K.retain(tmp_path/name,v)
 args=dict(workbook='0059',host='hhu-server-3',owner='hhu-server-3:slot-1:0059',fence=2,source_sha256='a'*64,version='v')
 job=put('job.json',dict(workbook='0059',source=dict(sha256='a'*64),version='v',run='/actual/worker'))
 plan=put('plan.json',dict(job=job,host=args['host'],new_owner=args['owner'],expected_fence=1))
 intent=put('root-intent.json',dict(plan=plan,remote=dict(job=job),automatic_retry=False))
 launch=put('launched.json',dict(intent=intent,automatic_retry=False,remote=dict(job=job,root_intent=intent,automatic_retry=False,pid=42,start_ticks='12',created_at_ns=2*10**9,command=['worker','--job',job['path'],'--owner',args['owner']])))
 worker=put('worker-intent.json',dict(job=job,owner=args['owner']))
 stopped=dict(at=3,boot_time=1,matches=[],processes=[],copies=[dict(native=job,copy=job),dict(native=dict(path='/actual/worker/intent.json',sha256=worker['sha256']),copy=worker)])
 return dict(schema_version='fleet-oneoff-quarantine-prior/v1',launch=launch),stopped,args

def test_authentic_oneoff_uses_its_own_launch_and_stopped_census(tmp_path):
 p,s,a=fixture(tmp_path);K.oneoff_stopped(p,s,a)

@pytest.mark.parametrize('bad',['pid','command','boot','owner','fence','source','version','missing_job','missing_worker'])
def test_foreign_live_or_incomplete_oneoff_witness_rejected(tmp_path,bad):
 p,s,a=fixture(tmp_path)
 if bad=='pid':s['processes']=[dict(pid=42,start='12')]
 elif bad=='command':s['matches']=[dict(pid=44)]
 elif bad=='boot':s['boot_time']=3
 elif bad in ('owner','source','version'):a[{'source':'source_sha256'}.get(bad,bad)]='foreign'
 elif bad=='fence':a['fence']=3
 elif bad=='missing_job':s['copies']=s['copies'][1:]
 elif bad=='missing_worker':s['copies']=s['copies'][:1]
 with pytest.raises(ValueError):K.oneoff_stopped(p,s,a)
