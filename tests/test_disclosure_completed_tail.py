from pathlib import Path
from types import SimpleNamespace as S
import hashlib,json
import pytest
from scripts.fleet import disclosure_completed_tail as T

def case(tmp):
 def save(path,value):
  p=tmp/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return T.binding(p)
 files=[]
 for name in ('I.py','C.py'):
  p=tmp/name;p.write_text('# original');files.append(T.binding(p))
 I=S(__file__=files[0]['path'],guard_seconds=lambda action,args:1350 if args['checkpoint']==last else 3300)
 C=S(__file__=files[1]['path'],encode=lambda x:x);env=(C,I,None,None,None,{},tmp,{})
 originalcp=save('permit/result.json',{});cache=save('cache.json',dict(checkpoint=originalcp));old=dict(output=str(tmp/'old'),schema_version='old-schema',helper={'path':'/old','sha256':'h'},source_cache_resume=cache,owner='old-owner')
 oldrow=save('plan.json',old);ops=tmp/'ops';save('old/authoring-job.json',dict(operations=str(ops)))
 def completed(path,prior,next_step):
  intent=save(path+'/intent.json',dict(code=files[0],base_code=files[1],controls={},action='step',timeout_seconds=3300,arguments=dict(checkpoint=prior,descriptor='descriptor')))
  save(path+'/disclosure-bootstrap.json',dict(schema_version='old-schema',plan=oldrow,helper=old['helper'],kind='instruction',intent=intent,original_command=['python','-B',files[0]['path'],'_child',intent['path'],intent['sha256']],seconds=3300,memory_bytes=8589934592))
  result=save(path+'/result.json',dict(status='STEP_COMPLETE',next_step=next_step,descriptor='descriptor',evidence=[prior]));guard=save(path+'/resource.json',dict(intent=intent));save(path+'/outcome.json',dict(resource=guard,result=result));return result
 first=completed('old/first-step',originalcp,'disclosure-commit');last=completed('ops/000',first,'final-runtime')
 directory=ops/'001';intent=save('ops/001/intent.json',dict(code=files[0],base_code=files[1],controls={},action='step',timeout_seconds=1350,memory_limit_bytes=8589934592,arguments=dict(descriptor='descriptor',checkpoint=last)))
 (directory/'step.log').write_bytes(b'');guard=save('ops/001/resource.json',dict(returncode=None,metrics={},failure=dict(type='ValueError',message_sha256=hashlib.sha256(b'original child guard changed').hexdigest()),intent=intent,log=T.binding(directory/'step.log')));failure=save('ops/001/outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=guard))
 job=save('worker-job.json',dict(driver=old['helper'],result=str(tmp/'old/result.json')));wi=save('worker-intent.json',dict(owner='old-owner',job=job));outer=save('worker-outcome.json',dict(returncode=1,failure_type=None,result=None,intent=wi));p=dict(previous=oldrow,failure=failure,worker_outcome=outer)
 D=S(successful=lambda row,env:(T.read(row)['result'],T.read(T.read(row)['result'])))
 U=S(retained_command=lambda command,*args:('instruction',dict(path=command[-2],sha256=command[-1])))
 return p,old,U,D,env,last,directory

def test_exact_completed_prefix_preserves_last_original_checkpoint(tmp_path):
 p,old,U,D,env,last,_=case(tmp_path);assert T.prefix(p,old,U,D,env)[0]==last

@pytest.mark.parametrize('name',['disclosure-bootstrap.json','result.json','nested-request.json'])
def test_any_effect_after_original_refused_guard_blocks_tail(tmp_path,name):
 p,old,U,D,env,last,directory=case(tmp_path);(directory/name).write_text('{}')
 with pytest.raises(ValueError,match='pre-child guard refusal'):T.prefix(p,old,U,D,env)

def test_changed_stopped_log_is_not_prechild_proof(tmp_path):
 p,old,U,D,env,last,directory=case(tmp_path);(directory/'step.log').write_text('transport may have started')
 with pytest.raises(ValueError):T.prefix(p,old,U,D,env)

@pytest.mark.parametrize('kind,changed',[('renewal',False),('instruction',True)])
def test_original_command_reader_must_authenticate_instruction_intent(tmp_path,kind,changed):
 p,old,U,D,env,last,directory=case(tmp_path);seen=[]
 def retained(command,selected,dispatcher,environment):
  assert selected is old and dispatcher is D and environment is env
  seen.append(command)
  return kind,dict(path=command[-2],sha256='foreign'if changed else command[-1])
 U.retained_command=retained
 with pytest.raises(ValueError,match='completed original guard'):T.prefix(p,old,U,D,env)
 assert len(seen)==1
