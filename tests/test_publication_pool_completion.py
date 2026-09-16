import copy,json
from pathlib import Path
import pytest
from scripts.fleet import publication_launch as L
from scripts.fleet.migration import binding,checked

@pytest.fixture
def fixture(tmp_path):
 def put(path,value):
  path=tmp_path/path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return binding(path)
 plan=dict(workbook='0176',code_root='/code/version',source_sha256='source',ready=put('ready.json',dict(status='PUBLICATION_READY')))
 job=put('job.json',dict(workbook='0176',source=dict(sha256='source'),version='version',run=str(tmp_path/'worker'),result=plan['ready']['path']))
 owner='hhu-server-3/slot-0/generation'
 intent=put('worker/intent.json',dict(owner=owner,job=job))
 outcome=put('worker/outcome.json',dict(status='PUBLICATION_READY',intent=intent,result=plan['ready'],returncode=0,failure_type=None,automatic_retry=False,publication_performed=False))
 dispatch=put('pool/dispatches/0176.json',dict(job=job,host='hhu-server-3',slot=0))
 generation=put('pool/generations/generation/generation.json',dict(schema_version='harbor-fleet-pool/v5',host='hhu-server-3',generation='generation'))
 completion=put('pool/generations/generation/slot-0.workbook-0176.result.json',dict(job=job,outcome=outcome,dispatch=dispatch,returncode=0,failure_type=None,forced=False,automatic_retry=False,publication_performed=False))
 rows=[completion,generation,dispatch,outcome,intent,job];copies=[]
 for i,row in enumerate(rows):copies.append(dict(original=row,local=put('copies/'+str(i)+'.json',checked(row))))
 cert=dict(schema_version='fleet-publication-pool-completion/v1',completion=completion,generation=generation,dispatch=dispatch,copies=copies)
 p=dict(pool_completion=put('certificate.json',cert),worker_outcome=outcome,expected_owner=owner,host='hhu-server-3',process=None,process_copy=None)
 return p,plan,put,cert

@pytest.mark.parametrize('local',[False,True])
def test_exact_native_and_offline_completion_chain(fixture,local):
 p,plan,_,_=fixture
 assert L.pool_completed(p,plan,checked,binding,local=local)['schema_version']=='fleet-publication-pool-completion/v1'

@pytest.mark.parametrize('field,value',[('returncode',1),('failure_type','TimeoutError'),('forced',True),('automatic_retry',True),('publication_performed',True)])
def test_terminal_completion_cannot_be_relaxed(fixture,field,value):
 p,plan,put,c=fixture;v=checked(c['completion']);v[field]=value
 row=put('pool/generations/generation/slot-0.workbook-0176.result.json',v);c['copies'][0]=dict(original=row,local=row);c['completion']=row;p['pool_completion']=put('certificate.json',c)
 with pytest.raises(ValueError):L.pool_completed(p,plan,checked,binding)

@pytest.mark.parametrize('change',['source','version','owner','host','ready','duplicate','missing','copy-tamper','native-tamper','fake-process','foreign-generation'])
def test_detached_or_mutated_custody_rejected(fixture,change):
 p,plan,put,c=fixture
 if change in ('source','version','ready'):plan[{'source':'source_sha256','version':'code_root','ready':'ready'}[change]]='foreign'
 elif change=='owner':p['expected_owner']='foreign'
 elif change=='host':p['host']='hhu-server-8'
 elif change=='duplicate':c['copies'].append(c['copies'][0])
 elif change=='missing':c['copies'].pop()
 elif change=='copy-tamper':Path(c['copies'][0]['local']['path']).write_text('{}')
 elif change=='native-tamper':Path(c['completion']['path']).write_text('{}')
 elif change=='fake-process':p['process']=dict(pid=123,start_ticks='1')
 elif change=='foreign-generation':
  row=put('pool/generations/foreign/generation.json',checked(c['generation']));c['generation']=row;c['copies'][1]=dict(original=row,local=row)
 p['pool_completion']=put('certificate.json',c)
 with pytest.raises((ValueError,TypeError)):L.pool_completed(p,plan,checked,binding,local=change=='copy-tamper')

@pytest.mark.parametrize('live',['none','job','run-child','result','permission'])
def test_actual_native_census_blocks_live_or_unreadable_process(fixture,tmp_path,monkeypatch,live):
 import time
 p,plan,put,c=fixture
 # Preserve the exact certificate chain while completing the ordinary native
 # read-only publication fixture (no substituted validator).
 ready=dict(status='PUBLICATION_READY',publication_performed=False)
 plan['ready']=put('ready.json',ready)
 jobrow=c['copies'][-1]['original'];job=checked(jobrow);job['inputs']=[]
 jobrow=put('job.json',job)
 intentrow=put('worker/intent.json',dict(owner=p['expected_owner'],job=jobrow))
 out=checked(p['worker_outcome']);out.update(intent=intentrow,result=plan['ready']);p['worker_outcome']=put('worker/outcome.json',out)
 d=checked(c['dispatch']);d['job']=jobrow;c['dispatch']=put('pool/dispatches/0176.json',d)
 co=checked(c['completion']);co.update(job=jobrow,outcome=p['worker_outcome'],dispatch=c['dispatch']);c['completion']=put('pool/generations/generation/slot-0.workbook-0176.result.json',co)
 rows=[c['completion'],c['generation'],c['dispatch'],p['worker_outcome'],intentrow,jobrow];c['copies']=[dict(original=r,local=r)for r in rows];p['pool_completion']=put('certificate.json',c)
 publisher=put('publisher.py',{});p.update(publisher=publisher,implementation=put('implementation.json',dict(publisher=publisher,dependencies={})))
 plan['journal']=str(tmp_path/'publication');p['plan']=put('plan.json',plan)
 tracker=put('tracker.json',dict(leases=dict(task_agent=dict(expires_at=time.time_ns()+8000*10**9))))
 p['proof']=put('proof.json',dict(tracker=tracker,paths=dict(journal=plan['journal'],output=str(tmp_path/'output'),publication_root=str(tmp_path/'published'))))
 proc=tmp_path/'processes'/'99999';proc.mkdir(parents=True);(proc/'stat').write_text('99999 (test) S '+' '.join(['0']*23))
 args={'none':'unrelated','job':jobrow['path'],'run-child':job['run']+'/child','result':job['result'],'permission':'unrelated'}
 (proc/'cmdline').write_bytes(('python\0'+args[live]+'\0').encode())
 original_iterdir=Path.iterdir;original_read=Path.read_bytes
 monkeypatch.setattr(Path,'iterdir',lambda q:iter([proc])if str(q)=='/proc'else original_iterdir(q))
 def read(q):
  if live=='permission' and q==proc/'cmdline':raise PermissionError('census denied')
  return original_read(q)
 monkeypatch.setattr(Path,'read_bytes',read)
 from scripts.fleet import queued_publication_renewal as Q
 renewal=dict(process=None,job=jobrow,outcome=p['worker_outcome'],publication_plan=p['plan'],pool_custody=dict(reader=binding(L.__file__),certificate=p['pool_completion'],host=p['host'],owner=p['expected_owner']))
 if live=='none':
  assert L.native(dict(profile=p))['status']=='PUBLICATION_VALIDATED_NOT_STARTED'
  Q.stopped(renewal)
 else:
  with pytest.raises((ValueError,PermissionError)):L.native(dict(profile=p))
  with pytest.raises((ValueError,PermissionError)):Q.stopped(renewal)
