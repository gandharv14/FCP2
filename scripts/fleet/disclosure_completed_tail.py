"""Continue an authentic disclosure-command tail after a pre-child guard error.

The complete original dispatcher prefix remains immutable. Only the declared
original instruction duration replaces the shell's former fixed duration.
"""
from pathlib import Path
from contextlib import contextmanager
import argparse,hashlib,importlib.util,json,os,sys
SCHEMA='fleet-disclosure-completed-tail/v1'
FIELDS={'schema_version','helper','previous','workbook','source','owner','fence','output','tracker','capacity_policy','failure','worker_outcome','shell'}
def require(ok,message):
 if not ok:raise ValueError(message)
def binding(path):
 p=Path(path);require(p.is_absolute()and not any(x.is_symlink()for x in(p,*p.parents)),'canonical tail input required');return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def read(row):require(binding(row['path'])==row,'tail evidence changed');return json.loads(Path(row['path']).read_bytes())
def module(name,row):
 require(binding(row['path'])==row,'tail code changed');s=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def prefix(p,old,U,D,env):
 home=Path(old['output']);job=read(binding(home/'authoring-job.json'));ops=Path(job['operations']);first=binding(home/'first-step/outcome.json');directories=sorted(x for x in ops.iterdir()if x.is_dir())
 require(1<=len(directories)<=40 and [x.name for x in directories]==['%03d'%i for i in range(len(directories))]
  and p['failure']==binding(directories[-1]/'outcome.json'),'exact contiguous stopped dispatcher required')
 previous=None
 for row in [first,*[binding(d/'outcome.json')for d in directories[:-1]]]:
  outcome=read(row);guard=read(outcome['resource']);intent=read(guard['intent']);side=read(binding(Path(guard['intent']['path']).parent/'disclosure-bootstrap.json'));value=read(outcome['result']);kind,actual=U.retained_command(side['original_command'],old,D,env)
  require(kind=='instruction'and actual==guard['intent']and D.successful(row,env)==(outcome['result'],value)and side==dict(schema_version=old['schema_version'],plan=p['previous'],helper=old['helper'],kind='instruction',intent=guard['intent'],original_command=side['original_command'],seconds=intent['timeout_seconds'],memory_bytes=8589934592)
   and intent['code']==binding(env[1].__file__)and intent['base_code']==binding(env[0].__file__)and intent['controls']==env[0].encode(env[7])
   and intent['action']=='step'and intent['timeout_seconds']==env[1].guard_seconds('step',intent['arguments']), 'completed original guard or declared child differs')
  if previous is not None:require(intent['arguments']['checkpoint']==previous,'completed tail predecessor changed')
  else:require(intent['arguments']['checkpoint']==read(old['source_cache_resume'])['checkpoint'],'initial completed step predecessor differs')
  previous=outcome['result']
 require(value['status']=='STEP_COMPLETE'and value['next_step']=='final-runtime','only completed dialogue review may resume final runtime')
 stopped=read(p['failure']);g=read(stopped['resource']);intent=read(g['intent']);directory=directories[-1]
 require(stopped['status']=='RECONCILIATION_REQUIRED'and g['returncode']is None and g['metrics']=={}
  and g['failure']==dict(type='ValueError',message_sha256=hashlib.sha256(b'original child guard changed').hexdigest())
  and g['intent']==binding(directory/'intent.json')and g['log']==binding(directory/'step.log')and Path(g['log']['path']).read_bytes()==b''
  and {x.name for x in directory.iterdir()}=={'intent.json','step.log','resource.json','outcome.json'}
  and intent['arguments']==dict(descriptor=value['descriptor'],checkpoint=previous)
  and intent['code']==binding(env[1].__file__)and intent['base_code']==binding(env[0].__file__)and intent['controls']==env[0].encode(env[7])
  and intent['timeout_seconds']==env[1].guard_seconds('step',intent['arguments'])==1350
  and intent['memory_limit_bytes']==8589934592,'final runtime was not a sealed pre-child guard refusal')
 outer=read(p['worker_outcome']);wi=read(outer['intent']);workerjob=read(wi['job'])
 require(outer['returncode']==1 and outer['failure_type']is None and outer['result']is None and wi['owner']==old['owner']
  and workerjob['driver']==old['helper']and workerjob['result']==str(home/'result.json'),'exact stopped tail worker required')
 return previous,value

@contextmanager
def context(row,*,invocation=None):
 p=read(row);require(set(p)==FIELDS and p['schema_version']==SCHEMA and p['helper']==binding(__file__),'closed completed tail selection required')
 old=read(p['previous']);require(old['schema_version']=='fleet-disclosure-command-continuation/v1'and 'source_cache_resume'in old
  and p['workbook']==old['workbook']and p['source']==old['source']and p['fence']==old['fence']+1 and p['owner']!=old['owner']and p['output']!=old['output'],'tail changed original source or ownership')
 M=module('completed_disclosure_original',old['helper']);S=module('completed_disclosure_guard',p['shell'])
 # Only the two duration expressions may differ in the selected dispatch shell.
 import ast,inspect,textwrap
 for name in ('install_commands','child'):
  a=textwrap.dedent(inspect.getsource(getattr(M,name)));b=textwrap.dedent(inspect.getsource(getattr(S,name)))
  expected=a.replace("3300 if kind=='instruction'else read(intent)['seconds']","U.retained_seconds(kind,intent,env)")
  require(ast.dump(ast.parse(expected))==ast.dump(ast.parse(b)),'selected shell changed beyond original duration reader')
 with M.context(p['previous'],continued=True)as(_,U,D,env,worker,selection,lease,K,raw,original_job):
  cp,value=prefix(p,old,U,D,env);I=env[1]
  if invocation is not None:
   intent,side=invocation;v=read(side);kind,actual=U.retained_command(v['original_command'],dict(old,output=p['output']),D,env);require(kind=='instruction'and actual==intent and v['plan']==row and v['helper']==p['helper'],'cold tail parent differs');args=read(intent)['arguments'];cp=args['checkpoint'];descriptor=args['descriptor']
  else:descriptor=value['descriptor']
  current,controls,_=I.selected(descriptor);_,selection,lease=I._prior(cp,descriptor);require(controls==env[7]and current.root==worker.root and current.run==worker.run,'current tail worker differs');env[3].active(current,selection,lease)
  derived=dict(old,helper=p['helper'],owner=p['owner'],fence=p['fence'],output=p['output'],capacity_policy=p['capacity_policy'])
  yield p,derived,M,S,U,D,env,current,selection,lease,K,raw,original_job,cp

def child(row,intent,sidecar):
 require(sys.flags.isolated and sys.dont_write_bytecode,'isolated tail child required')
 with context(row,invocation=(intent,sidecar))as(p,derived,M,S,U,D,env,w,s,l,K,raw,job,cp):
  U.custody(derived,w.root);side=read(sidecar);kind,actual=U.retained_command(side['original_command'],derived,D,env)
  require(actual==intent and side==dict(schema_version=M.SCHEMA,plan=row,helper=p['helper'],kind=kind,intent=intent,original_command=side['original_command'],seconds=U.retained_seconds(kind,intent,env),memory_bytes=8589934592)
   and Path(sidecar['path'])==Path(intent['path']).parent/'disclosure-bootstrap.json','cold tail sidecar differs')
  S.install_commands(row,derived,U,D,env,raw,job);return env[1].child(intent)

def run(row):
 with context(row)as(p,derived,M,S,U,D,env,w,s,l,K,raw,job,cp):
  require(binding(p['tracker']['path'])==p['tracker']and not Path(p['output']).exists(),'current tail tracker or namespace changed');U.custody(derived,w.root)
  ops=U.operations_for(D,env[5],Path(p['output']));Path(p['output']).mkdir();S.install_commands(row,derived,U,D,env,raw,job)
  prior=read(p['previous']);oldjob=read(binding(Path(prior['output'])/'authoring-job.json'));oldops=Path(oldjob['operations']);last=sorted(x for x in oldops.iterdir()if x.is_dir())[-2];outcome=binding(last/'outcome.json');require(read(outcome)['result']==cp,'last successful guard changed')
  bound=U.keep(Path(p['output'])/'authoring-job.json',dict(job,outcome=outcome,operations=str(ops)));D.run(bound);held=read(binding(ops/'hold.json'));checkpoint,value=D.successful(held['outcome'],env)
  return U.keep(Path(p['output'])/'result.json',dict(status=value['status'],workbook=p['workbook'],continuation=row,config=job['config'],job=bound,outcome=held['outcome'],checkpoint=checkpoint,publication_performed=False,automatic_retry=False))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['run','child']);a.add_argument('--plan',required=True);a.add_argument('--sha256',required=True);a.add_argument('--intent');a.add_argument('--intent-sha256');a.add_argument('--bootstrap');a.add_argument('--bootstrap-sha256');q=a.parse_args();row=dict(path=q.plan,sha256=q.sha256)
 if q.action=='run':print(json.dumps(run(row)))
 else:child(row,dict(path=q.intent,sha256=q.intent_sha256),dict(path=q.bootstrap,sha256=q.bootstrap_sha256))
