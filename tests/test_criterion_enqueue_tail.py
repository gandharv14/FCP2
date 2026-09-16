import json
from pathlib import Path
import pytest
from scripts.fleet import criterion_enqueue_tail as M

@pytest.fixture
def boundary(tmp_path,monkeypatch):
 def keep(name,value):
  p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return M.binding(p)
 source=dict(path='/original.xlsx',sha256='a'*64)
 job=keep('job.json',dict(workbook='example',version='b'*64,source=source,run='/old/exact-worker',result='/old/exact-result.json'))
 intent=keep('intent.json',dict(job=job))
 outer=keep('outer.json',dict(intent=intent,status='RECONCILIATION_REQUIRED',automatic_retry=False,returncode=1,failure_type=None))
 checkpoint=dict(path='/commit/result.json',sha256='c'*64);value=dict(descriptor=dict(path='/descriptor.json',sha256='d'*64))
 log=tmp_path/'ops/001/step.log';log.parent.mkdir(parents=True);log.write_text('historical reader refused')
 guard=keep('ops/001/intent.json',dict(action='step',arguments=dict(descriptor=value['descriptor'],checkpoint=checkpoint)))
 resource=keep('ops/001/resource.json',dict(returncode=1,failure=None,intent=guard,log=M.binding(log)))
 failed=keep('ops/001/outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,result=None,resource=resource))
 process=keep('process.json',dict(job=job,automatic_retry=False,pid=12345678,start_ticks='123',command=['python','--job',job['path'],'--job-sha256',job['sha256']]))
 p=dict(schema_version=M.SCHEMA,prior_process=process,workbook='example',source=source,version='b'*64,prior_worker_job=job,prior_worker_outcome=outer,failed_outcome=failed,completed_outcome=dict(path=str(tmp_path/'ops/000/outcome.json'),sha256='e'*64))
 original=Path.iterdir
 monkeypatch.setattr(Path,'iterdir',lambda self:iter(())if str(self)=='/proc' else original(self))
 return p,checkpoint,value,keep

def test_exact_stopped_pre_enqueue_boundary(boundary):
 p,c,v,_=boundary;M.stopped(p,{},c,v)

@pytest.mark.parametrize('field,new',[('returncode',None),('returncode',0),('automatic_retry',True),('failure_type','TimeoutError'),('status','COMPLETED')])
def test_unrelated_outer_refused(boundary,field,new):
 p,c,v,keep=boundary;x=M.read(p['prior_worker_outcome']);x[field]=new;p['prior_worker_outcome']=keep('changed-outer.json',x)
 with pytest.raises(ValueError):M.stopped(p,{},c,v)

@pytest.mark.parametrize('field,new',[('result',dict(path='/uncertain/result',sha256='f'*64)),('status','COMPLETED_OPERATION'),('automatic_retry',True)])
def test_uncertain_or_completed_envelope_refused(boundary,field,new):
 p,c,v,keep=boundary;x=M.read(p['failed_outcome']);x[field]=new;p['failed_outcome']=keep('ops/001/outcome.json',x)
 with pytest.raises(ValueError):M.stopped(p,{},c,v)

def test_guard_cannot_select_another_completed_stage(boundary):
 p,c,v,_=boundary
 with pytest.raises(ValueError):M.stopped(p,{},dict(path='/other/commit',sha256='0'*64),v)

def test_unbound_result_prevents_enqueue_replay(boundary):
 p,c,v,keep=boundary;keep('ops/001/result.json',dict(status='PUBLICATION_READY'))
 with pytest.raises(ValueError):M.stopped(p,{},c,v)

def test_changed_prior_source_refused(boundary):
 p,c,v,_=boundary;p['source']=dict(path='/foreign.xlsx',sha256='a'*64)
 with pytest.raises(ValueError):M.stopped(p,{},c,v)

@pytest.mark.parametrize('needle',['run','result','job','same-pid'])
def test_actual_related_process_and_pid_identity_block(boundary,tmp_path,monkeypatch,needle):
 p,c,v,_=boundary;proc=tmp_path/('12345678'if needle=='same-pid'else'34567890');proc.mkdir()
 fields=['S']+['0']*18+['123'];(proc/'stat').write_text(proc.name+' (python) '+' '.join(fields))
 job=M.read(p['prior_worker_job']);arg={'run':job['run'],'result':job['result'],'job':p['prior_worker_job']['path'],'same-pid':'unrelated-looking-argv'}[needle]
 (proc/'cmdline').write_bytes(arg.encode());original=Path.iterdir
 monkeypatch.setattr(Path,'iterdir',lambda self:iter([proc])if str(self)=='/proc'else original(self))
 with pytest.raises(ValueError,match='still active'):M.stopped(p,{},c,v)

def test_process_permission_error_is_not_stopped_evidence(boundary,tmp_path,monkeypatch):
 p,c,v,_=boundary;proc=tmp_path/'777777';proc.mkdir();original=Path.iterdir;oldread=Path.read_text
 monkeypatch.setattr(Path,'iterdir',lambda self:iter([proc])if str(self)=='/proc'else original(self))
 def denied(self,*args,**kw):
  if self==proc/'stat':raise PermissionError('cannot inspect process')
  return oldread(self,*args,**kw)
 monkeypatch.setattr(Path,'read_text',denied)
 with pytest.raises(PermissionError):M.stopped(p,{},c,v)

def test_original_failed_envelope_omits_result(boundary):
 p,c,v,keep=boundary;x=M.read(p['failed_outcome']);x.pop('result');x.update(source_invalidity_claimed=False,terminal_verdict=None);p['failed_outcome']=keep('ops/001/outcome.json',x);M.stopped(p,{},c,v)

def test_original_dispatcher_terminal_ready_never_calls_resumable_prior(tmp_path):
 import ast
 from types import SimpleNamespace
 source=Path('scripts/fleet/advance_authoring_v8.py').read_text();node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)and n.name=='successful')
 calls=[]
 def keep(name,value):
  p=tmp_path/name;p.write_text(json.dumps(value));return M.binding(p)
 code=tmp_path/'campaign.py';code.write_text('# exact original campaign fixture')
 descriptor=keep('descriptor.json',{})
 checkpoint=dict(path='/exact/commit/result.json',sha256='a'*64)
 intent=keep('intent.json',dict(schema_version='campaign/v1',code=M.binding(code),controls={},memory_limit_bytes=8*1024**3,timeout_seconds=3300,action='step',arguments=dict(descriptor=descriptor,checkpoint=checkpoint)))
 result=keep('result.json',dict(schema_version='campaign/v1',status='PUBLICATION_READY',descriptor=descriptor,evidence=[checkpoint]))
 log=tmp_path/'step.log';log.write_text('completed enqueue')
 resource=keep('resource.json',dict(returncode=0,failure=None,intent=intent,log=M.binding(log)))
 outcome=keep('outcome.json',dict(status='COMPLETED_OPERATION',automatic_retry=False,resource=resource,result=result))
 def no_prior(*a):raise AssertionError('terminal READY is not a resumable checkpoint')
 C=SimpleNamespace(SCHEMA='campaign/v1',__file__=str(code),guard_seconds=lambda *a:3300,selected=lambda d:calls.append('selected'),_prior=no_prior)
 base=SimpleNamespace(encode=lambda x:x,MEMORY=8*1024**3)
 ns=dict(checked=M.read,binding=M.binding,require=M.require,Path=Path,publication_request=lambda env,value:calls.append('publication_request'))
 exec(compile(ast.Module(body=[node],type_ignores=[]),'<original successful>','exec'),ns)
 assert ns['successful'](outcome,(base,C,None,None,None,None,None,{}))==(result,M.read(result))
 assert calls==['selected','publication_request']

@pytest.fixture
def enqueue_retry(boundary,tmp_path):
 p,c,v,keep=boundary
 old_failed=p['failed_outcome'];old_outer=p['prior_worker_outcome']
 entry=tmp_path/'old-entry.py';entry.write_text('# original frozen enqueue implementation')
 previous=keep('previous-plan.json',dict(schema_version=M.SCHEMA,entry=M.binding(entry),completed_outcome=p['completed_outcome'],prior_plan=dict(path='/original-plan.json',sha256='1'*64),owner='native-owner',workbook=p['workbook'],source=p['source'],version=p['version'],output='/original-enqueue/output',operations=str(tmp_path/'retry-ops'),failed_outcome=old_failed,prior_worker_outcome=old_outer))
 old=M.read(p['prior_worker_job']);old.update(inputs=[previous],result='/original-enqueue/output/result.json');job=keep('retry-job.json',old)
 intent=keep('retry-intent.json',dict(job=job));outer=keep('retry-outer.json',{**M.read(old_outer),'intent':intent})
 process=keep('retry-process.json',{**M.read(p['prior_process']),'job':job,'command':['python','--job',job['path'],'--job-sha256',job['sha256']]})
 log=tmp_path/'retry-ops/000/step.log';log.parent.mkdir(parents=True);log.write_text('original captured route refused before first write')
 guard=keep('retry-ops/000/intent.json',dict(action='step',arguments=dict(descriptor=v['descriptor'],checkpoint=c)))
 resource=keep('retry-ops/000/resource.json',dict(returncode=1,failure=None,intent=guard,log=M.binding(log)))
 failed=keep('retry-ops/000/outcome.json',dict(status='RECONCILIATION_REQUIRED',automatic_retry=False,resource=resource))
 p.update(schema_version=M.RETRY,previous_enqueue=previous,prior_plan=M.read(previous)['prior_plan'],owner='native-owner',prior_worker_job=job,prior_worker_outcome=outer,prior_process=process,failed_outcome=failed)
 return p,c,v,keep

def test_exact_failed_enqueue_preserves_original_commit(enqueue_retry):
 p,c,v,_=enqueue_retry;M.stopped(p,{},c,v)

@pytest.mark.parametrize('field,new',[('completed_outcome',dict(path='/different-commit',sha256='7'*64)),('prior_plan',dict(path='/foreign-plan',sha256='9'*64)),('owner','foreign-owner')])
def test_retry_cannot_change_original_authority(enqueue_retry,field,new):
 p,c,v,_=enqueue_retry;p[field]=new
 with pytest.raises(ValueError):M.stopped(p,{},c,v)

def test_retry_rejects_recursive_or_uncertain_predecessor(enqueue_retry):
 p,c,v,keep=enqueue_retry;previous=M.read(p['previous_enqueue']);previous['schema_version']=M.RETRY;p['previous_enqueue']=keep('nested-previous.json',previous)
 with pytest.raises(ValueError):M.stopped(p,{},c,v)
