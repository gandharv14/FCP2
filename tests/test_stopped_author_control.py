import json,time
from types import SimpleNamespace
import pytest
from scripts.fleet import stopped_author_control as K
from scripts.fleet import stopped_author_reconciliation as R
from scripts.fleet.coordinator import Coordinator
from test_stopped_author_reconciliation import fixture,args


def test_actual_full_database_copy_leaves_live_state_unchanged(tmp_path):
 c,now,_,auth,e=fixture(tmp_path);before=c.status();a=args(c,auth,e)
 O=SimpleNamespace(Coordinator=lambda p:Coordinator(p,clock=c.clock))
 proof=K.copy_proof((dict(arguments=a),dict(database=c.database),O,R),tmp_path/'proof')
 assert K.read(proof)['status']=='FULL_DATABASE_COPY_STOPPED_AUTHOR_PASS'and c.status()==before


def evidence(tmp_path,monkeypatch):
 def save(name,v):return K.keep(tmp_path/name,v)
 a=dict(workbook='0119',owner='hhu-server-3:slot-0:0119',fence=1)
 intent=save('intent.json',dict(owner=a['owner'],automatic_retry=False))
 item=dict(copy=intent,native=dict(path='/native/worker/intent.json',sha256=intent['sha256']))
 stopped=save('stopped.json',dict(copies=[item]))
 original=dict(stopped=stopped)
 called=[]
 def validated(row,args):called.append((row,args));return original
 monkeypatch.setattr(K,'module',lambda *a:SimpleNamespace(scope_evidence=validated))
 scope=save('scope.json',dict(helper=dict(path='/validator',sha256='a'*64),arguments=dict(a,host='hhu-server-3',evidence={'path':'/oldproof','sha256':'b'*64})))
 native=dict(schema_version='fleet-stopped-author-process/v1',host='hhu-server-3',at=time.time(),boot_id='boot',selection=[dict(owner=a['owner'],worker='/native/worker',intent=item['native'])],files=[item['native']],processes=[dict(pid=1,ppid=0,start=1,argv=['/sbin/init'])],matches=[])
 n=save('native.json',native);e=save('evidence.json',dict(schema_version='fleet-stopped-author-evidence/v1',scope_plan=scope,fresh_processes=n,worker_intent=item))
 return a,e,native,called,save


def test_original_scope_validation_precedes_fresh_process_check(tmp_path,monkeypatch):
 a,e,n,called,save=evidence(tmp_path,monkeypatch);assert K.stopped_evidence(e,a)['fresh_processes']and len(called)==1


@pytest.mark.parametrize('delta',[dict(at=0),dict(at=float('nan')),dict(at=time.time()+500),dict(matches=[{}]),dict(host='hhu-server-4'),dict(selection=[]),dict(files=[]),dict(processes=[dict(pid=12,ppid=1,start=55,argv=['worker.py','--run','/native/worker'])]),dict(processes=[dict(pid=12,ppid=1,start=55,argv=['worker.py','--owner','hhu-server-3:slot-0:0119'])])])
def test_stale_foreign_or_running_native_custody_rejected(tmp_path,monkeypatch,delta):
 a,e,n,called,save=evidence(tmp_path,monkeypatch);v=K.read(e);v['fresh_processes']=save('changed-native.json',dict(n,**delta));bad=save('changed-evidence.json',v)
 with pytest.raises(ValueError):K.stopped_evidence(bad,a)
 assert len(called)==1


def clean_evidence(tmp_path):
 from scripts.fleet.coordinator import digest
 copies=[]
 def item(name,value,native):
  row=K.keep(tmp_path/name,value);v=dict(copy=row,native=dict(path=native,sha256=row['sha256']));copies.append(v);return v['native']
 home='/native/workers/0316';owner='hhu-server-4:slot-0:0316'
 job=item('job.json',dict(run=home,workbook='0316',version='b'*64,source=dict(path='/source.xlsx',sha256='a'*64),driver=dict(path='/driver.py',sha256='d'*64),result='/task/home/result.json'),'/prepared/job.json')
 ir=item('intent.json',dict(job=job,owner=owner,claim_id='claim',automatic_retry=False),home+'/intent.json')
 item('claim.json',dict(workbook='0316',owner=owner,claim_id='claim',fence=7,version='b'*64,source_sha256='a'*64),home+'/claim.json')
 log=item('worker.log',b'',home+'/worker.log')
 out=item('outcome.json',dict(schema_version='harbor-fleet-worker-outcome/v1',intent=ir,status='RECONCILIATION_REQUIRED',automatic_retry=False,publication_performed=False,returncode=None,result=None,failure_type='RuntimeError',log=log),home+'/outcome.json')
 item('finish.json',dict(outcome=out,failure_type='RuntimeError',automatic_retry=False,reconciliation_required=True),home+'/finish-uncertain.json')
 launch=K.keep(tmp_path/'launch.json',dict(automatic_retry=False,remote=dict(automatic_retry=False,job=job,pid=100,command=['python','worker.py','--job',job['path'],'--job-sha256',job['sha256'],'--owner',owner])))
 w=dict(workbook='0316',owner=owner,fence=7,claim_id='claim',state='hold',source_sha256='a'*64,version='b'*64,checkpoint=json.dumps(dict(intent=ir,pid=101)))
 wr=K.keep(tmp_path/'workbook.json',w);args=dict(workbook='0316',owner=owner,fence=7,claim_id='claim',workbook_sha256=digest(w),quarantines={})
 native=dict(schema_version='fleet-stopped-author-process/v2',host='hhu-server-4',at=time.time(),boot_id='boot',selection=[dict(owner=owner,worker=home,intent=ir)],files=[r['native']for r in copies],processes=[dict(pid=1,ppid=0,pgid=1,start=1,argv=['/sbin/init'])],matches=[])
 nr=K.keep(tmp_path/'native.json',native);e=dict(schema_version='fleet-stopped-author-no-operation-evidence/v2',launch=launch,copies=copies,fresh_processes=nr,workbook=wr)
 return args,e,native


def test_no_operation_stopped_custody_accepts_exact_original_worker(tmp_path):
 args,e,_=clean_evidence(tmp_path)
 assert K.stopped_evidence(K.keep(tmp_path/'evidence.json',e),args)==e


@pytest.mark.parametrize('field,value',[('host','hhu-server-3'),('at',0),('files',[]),('matches',[{}]),('processes',[dict(pid=99,ppid=1,pgid=101,start=2,argv=['orphan-child'])]),('processes',[dict(pid=100,ppid=1,pgid=100,start=3,argv=['worker'])])])
def test_no_operation_stale_incomplete_or_live_custody_rejected(tmp_path,field,value):
 args,e,n=clean_evidence(tmp_path);e['fresh_processes']=K.keep(tmp_path/'bad-native.json',dict(n,**{field:value}))
 with pytest.raises(ValueError):K.stopped_evidence(K.keep(tmp_path/'evidence.json',e),args)


def test_no_operation_cannot_change_claim_or_hide_quarantine(tmp_path):
 args,e,n=clean_evidence(tmp_path);row=K.keep(tmp_path/'evidence.json',e)
 for delta in(dict(fence=8),dict(claim_id='other'),dict(quarantines={'unknown':'a'*64})):
  with pytest.raises(ValueError):K.stopped_evidence(row,dict(args,**delta))
