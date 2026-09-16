"""Continue an accepted mechanical dialogue from its original review checkpoint.

Only the old mechanical certificate reads the submitted instruction archive.
The original dispatcher and current task gates remain live; no model is replayed.
"""
from contextlib import contextmanager
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,sys
SCHEMA='fleet-completed-mechanical-dialogue-tail/v1'
FIELDS={'schema_version','helper','original','instruction_archive','review_outcome','review_result','failed_outcome','tracker','workbook','source','owner','fence','capacity_policy','output'}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and p.is_file()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical mechanical tail evidence required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):require(binding(row['path'])==row,'mechanical tail evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(row):
 require(binding(row['path'])==row,'mechanical tail helper changed');s=importlib.util.spec_from_file_location('mechanical_tail_original',row['path']);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m

@contextmanager
def instruction_history(K,certificate,h,V):
 require(set(h)=={'context','submitted','original','archive','current'},'closed submitted instruction archive required')
 proof=read(certificate);old=h['original'];archive=h['archive'];current=h['current']
 require(old in proof['evidence']and old['path']==current['path']and Path(old['path']).name=='instruction.md'
  and old['sha256']==archive['sha256']and old['sha256']!=current['sha256']and binding(archive['path'])==archive
  and binding(current['path'])==current,'exact changed instruction and submitted bytes required')
 context=read(h['context']);submitted=read(h['submitted'])
 calls=[call for call in proof['author_calls']if call['submitted']==h['submitted']]
 require(h['context']in proof['evidence']and len(calls)==1
  and context['kind']=='dialogue-writer'and calls[0]['request']==submitted['request']
  and binding(context['request']['path'])==context['request']and binding(submitted['request']['path'])==submitted['request']
  and context['request']['sha256']==submitted['request']['sha256']
  and context['budget_evidence']==submitted['budget_evidence'],'archive detached from authentic original author')
 indices=[i for i,r in enumerate(context['evidence'])if r==old]
 require(len(indices)==1 and context['budget_evidence'][indices[0]+1]==archive,'archive is not exact submitted instruction input')
 claims=read(proof['claims']);stage=Path(old['path']).parent
 require(V.rewrite_instruction(Path(archive['path']).read_text(),stage,claims)==Path(current['path']).read_text(),
  'current instruction differs from original accepted dialogue rewrite')
 marker=read(binding(stage/V.APPLIED_MARKER))
 require(marker['skill']=='additional-assumptions-dialogue'and marker['applied']is True and marker['review_passed']is True
  and marker['draft_passed']is True and marker['instruction_sha256']==current['sha256'],'actual applied dialogue marker changed')
 require([r for r in proof['evidence']if binding(r['path'])!=r]==[old],'additional mechanical history changed')
 original_binding=K.binding;original_checked=K.checked
 def historical_binding(path):
  if str(Path(path))==old['path']:
   require(binding(current['path'])==current and binding(archive['path'])==archive,'instruction archive or current task changed');return old
  return original_binding(path)
 def historical_checked(row):
  if row==old:return json.loads(Path(archive['path']).read_bytes())
  return original_checked(row)
 K.binding=historical_binding;K.checked=historical_checked
 try:K.validate(certificate);yield
 finally:K.binding=original_binding;K.checked=original_checked

@contextmanager
def context(row,*,child=False):
 p=read(row);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed mechanical tail required')
 old=read(p['original']);M=module(old['helper']);require(old['workbook']==p['workbook']and old['source']==p['source']
  and old['owner']==p['owner']and p['fence']==old['fence']+1 and p['output']!=old['output'],'mechanical tail changed original source or custody')
 # This is the exact immutable T after-state, used only by the old pre-authority
 # failure reader. The instruction worker's current tracker reads stay live.
 completion=read(old['completed_takeover']['completion']);archive=completion['after']
 require(archive['sha256']==old['tracker']['sha256'],'historical takeover tracker differs')
 previous_read=M.read
 def historical_read(r):return read(archive)if r==old['tracker']else previous_read(r)
 M.read=historical_read
 try:
  with M.context(p['original'])as(_,U,D,env,w,s,l,K,V,L):
   authorization=read(binding(Path(old['output'])/'authorization/result.json'));certificate=read(authorization['checkpoint'])['mechanical_requalification']
   with instruction_history(K,certificate,p['instruction_archive'],V):
    guard=env[0]._run_in_process_group
    M.install_completed(p['original'],old,U,D,env,K)
    env[0]._run_in_process_group=guard
    result,value=D.successful(p['review_outcome'],env)
    require(result==p['review_result']and value['status']=='STEP_COMPLETE'and value['next_step']=='final-runtime'
     and value['evidence'][0]==binding(Path(old['output'])/'authorization/result.json'),'actual accepted first review required')
    I=env[1];actual,selection,lease=I._prior(result,value['descriptor']);worker,_,_=I.selected(value['descriptor'])
    require(actual==value and read(value['checkpoint'])['step']=='dialogue-review','completed dialogue checkpoint differs')
    failure(p,old,env,worker,value,check_pending=not child)
    from scripts.batch import harbor_model_executor as E
    def no_models(*a,**k):raise ValueError('completed dialogue tail cannot dispatch a model')
    E.execute=E._open=no_models
    execution={**old,**p,'original_job':old['original_job']}
    U.install_retained_commands(row,execution,D,env)
    yield p,M,U,D,env,worker,selection,lease,K,V,execution
 finally:M.read=previous_read

def failure(p,old,env,worker,review,*,check_pending=True):
 out=read(p['failed_outcome']);resource=read(out['resource']);intent=read(resource['intent']);home=Path(env[5]['run_root'])/'dispatcher-v2'/Path(old['output']).name/'001'
 require(p['failed_outcome']==binding(home/'outcome.json')and out['status']=='RECONCILIATION_REQUIRED'and out.get('automatic_retry',False)is False
  and resource['returncode']==1 and resource['failure']is None and resource['intent']==binding(home/'intent.json')
  and resource['log']==binding(home/'step.log'),'exact stopped post-review guard required')
 require(intent['action']=='step'and intent['arguments']==dict(descriptor=review['descriptor'],checkpoint=p['review_result'])
  and intent['code']==binding(env[1].__file__)and intent['base_code']==binding(env[0].__file__)
  and intent['controls']==env[0].encode(env[7])
  and intent['timeout_seconds']==env[1].guard_seconds('step',intent['arguments'])and intent['memory_limit_bytes']==8589934592,'post-review guard detached from original checkpoint')
 require(Path(resource['log']['path']).read_text().rstrip().endswith('ValueError: mechanical retained bytes changed')
  and not(home/'result.json').exists(),'post-review operation made unsupported progress')
 if check_pending:require(not env[3].step_directory(worker,'final-runtime').exists(),'final-runtime already owns task effects')

def probe(row):
 with context(row)as(p,M,U,D,env,w,s,l,K,V,execution):
  require(not Path(p['output']).exists(),'tail namespace already used')
  return dict(status='PASS',models_dispatched=0,result=dict(plan=row,review=p['review_result'],current_instruction=p['instruction_archive']['current'],actual_task_mutations=0))

def run(row):
 with context(row)as(p,M,U,D,env,w,s,l,K,V,execution):
  U.custody(p,w.root);require(binding(p['tracker']['path'])==p['tracker'],'tail entry tracker changed');home=Path(p['output']);operations=U.operations_for(D,env[5],home);require(not home.exists(),'tail namespace consumed');home.mkdir()
  job=M.keep(home/'authoring-job.json',dict(read(execution['original_job']),outcome=p['review_outcome'],operations=str(operations)))
  D.run(job);held=read(binding(operations/'hold.json'));result,value=D.successful(held['outcome'],env)
  return M.keep(home/'result.json',dict(status=value['status'],workbook=p['workbook'],continuation=row,job=job,outcome=held['outcome'],checkpoint=result,models_dispatched=0,publication_performed=False,automatic_retry=False))

def child(row,intent,sidecar):
 with context(row,child=True)as(p,M,U,D,env,w,s,l,K,V,execution):
  U.custody(p,w.root);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],execution,D,env)
  require(actual==intent and side==dict(schema_version='fleet-retained-semantic-child/v1',plan=row,helper=p['helper'],kind=kind,intent=intent,
   original_command=side['original_command'],seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592,automatic_retry=False)
   and Path(sidecar['path'])==Path(intent['path']).parent/'semantic-bootstrap.json','exact original tail child required')
  require(sys.argv==[p['helper']['path'],'retained-child','--plan',row['path'],'--sha256',row['sha256'],'--intent',intent['path'],'--intent-sha256',intent['sha256'],'--bootstrap',sidecar['path'],'--bootstrap-sha256',sidecar['sha256']],'actual cold tail argv differs')
  return env[1].child(intent)if kind=='instruction'else D.renewal_child(intent)

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['probe','run','retained-child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--intent');a.add_argument('--intent-sha256');a.add_argument('--bootstrap');a.add_argument('--bootstrap-sha256');v=a.parse_args();row=dict(path=v.plan,sha256=v.sha256)
 print(json.dumps(probe(row)if v.action=='probe'else run(row)if v.action=='run'else child(row,dict(path=v.intent,sha256=v.intent_sha256),dict(path=v.bootstrap,sha256=v.bootstrap_sha256))))
