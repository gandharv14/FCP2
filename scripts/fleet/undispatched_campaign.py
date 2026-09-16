"""Declared guarded-child dependency for retained normalization continuation.

Original campaign functions, intent schemas and task snapshot bytes are not
changed. A separately bound child wrapper installs the approved partition reader
before calling the original campaign child. Every altered launch retains a new
sidecar containing both argument vectors and all reader/implementation bindings.
"""
from pathlib import Path
from types import FunctionType, SimpleNamespace
import hashlib
import json
import os

SCHEMA='fleet-declared-owning-child-adapter/v1'


def require(value,message):
 if not value:raise ValueError(message)


def selected_operation_namespace(config,value):
 """Validate location for both parent and cold child, without granting replay.

 The original dispatcher still requires a fresh namespace when run starts.
 Cold children instead authenticate their exact existing intent with
 validate_child; loading their dependencies must not require an absent parent.
 """
 path=Path(value);base=Path(config['run_root'])/'dispatcher-v2'
 require(path.is_absolute() and path.parent==base and path.name not in {'.','..'},
  'selected operations must stay in dedicated workbook namespace')
 require(path.resolve()==path and base.resolve()==base and not any(x.is_symlink()for x in(path,*path.parents)),
  'symlink or parent escape in selected operation namespace')
 require(not path.exists() or path.is_dir(),'selected operations is not a directory')
 return path


def binding(path):
 p=Path(path).absolute();require(not any(x.is_symlink()for x in(p,*p.parents)),'symlink in owning adapter evidence')
 return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def checked(row):
 require(binding(row['path'])==row,'owning adapter evidence changed')
 return json.loads(Path(row['path']).read_bytes())


def retain(path,value):
 path=Path(path);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
 fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
 return binding(path)


def contract(producer, base, kind, intent, command):
 """Read each producer's original closed launch contract, without effects."""
 value=checked(intent)
 if kind=='environment':
  require(set(value)=={'schema_version','action','arguments','controls','dispatch','code','executable',
   'memory_limit_bytes','timeout_seconds'} and value['schema_version']=='harbor-coordinator-step/v1',
   'closed environment intent required')
  require(value['action']in {'refresh','step'} and type(value['dispatch'])is bool
   and (value['action']=='step' or value['dispatch']is False),'environment capability differs')
  seconds=3300;verb='_child'
 elif kind=='instruction':
  require(set(value)=={'schema_version','action','arguments','controls','models','commands','containers',
   'code','base_code','executable','memory_limit_bytes','timeout_seconds'} and value['schema_version']==producer.SCHEMA,
   'closed instruction intent required')
  require(value['action']in {'start','step','import-retry'}
   and all(type(value[k])is bool for k in ('models','commands','containers'))
   and (value['action']=='step' or not any(value[k]for k in ('models','commands','containers')))
   and value['base_code']==binding(base.__file__),'instruction capability/producer differs')
  seconds=producer.guard_seconds(value['action'],value['arguments']) if hasattr(producer,'guard_seconds') else 3300
  require(seconds in {3300,1350},'instruction guard changed');verb='_child'
 elif kind=='renewal':
  require(set(value)=={'job','checkpoint','seconds','driver'} and value['driver']==binding(producer.__file__)
   and type(value['seconds'])is int and 60<=value['seconds']<=900,'closed ordinary renewal intent required')
  seconds=value['seconds'];verb='renew-child'
 else:raise ValueError('unsupported owning producer')
 plain=[command[0],'-B',str(Path(producer.__file__).absolute()),verb,intent['path'],intent['sha256']]
 require(command==plain or (kind=='renewal' and command==[plain[0],'-I',*plain[1:]]),
  'original owning argv differs')
 if kind!='renewal':
  require(value['code']==binding(producer.__file__) and value['executable']==binding(Path(command[0]).resolve())
   and value['memory_limit_bytes']==8*1024**3 and value['timeout_seconds']==seconds,'owning intent producer/guard differs')
 require(base.SECONDS==3300 and base.MEMORY==8*1024**3,'original resource guards changed')
 return seconds


def select_run(producer, *, base, kind, reader_plan, child_wrapper, producer_code, reader_evidence):
 """Clone original run/renew bytecode with one explicit launch dependency."""
 require(binding(producer.__file__)==producer_code,'original owning producer changed')
 checked(reader_plan)
 for row in [child_wrapper,*reader_evidence]:require(binding(row['path'])==row,'declared reader dependency changed')
 original_runner=base._run_in_process_group
 function=producer.renew if kind=='renewal' else producer.run
 namespace=dict(function.__globals__)

 def guarded_child(command,**options):
  require(isinstance(command,list)and (len(command)==6 or (kind=='renewal' and len(command)==7)), 'unexpected original owning argv')
  intent=dict(path=command[-2],sha256=command[-1]);seconds=contract(producer,base,kind,intent,command)
  require(options.get('timeout_seconds')==seconds and options.get('memory_limit_bytes')==8*1024**3
    and options.get('grace_seconds')==3 and Path(options.get('cwd'))==Path(base.ROOT),
    'owning process-group guards changed')
  for row in [reader_plan,child_wrapper,producer_code,*reader_evidence]:require(binding(row['path'])==row,'reader dependency changed before spawn')
  selected=[command[0],'-I','-B',child_wrapper['path'],'owning-child','--plan',reader_plan['path'],
    '--plan-sha256',reader_plan['sha256'],'--kind',kind,'--intent',intent['path'],'--intent-sha256',intent['sha256']]
  retain(Path(intent['path']).parent/'declared-runtime-adapter.json',dict(schema_version=SCHEMA,
    adapter=binding(__file__),producer_code=producer_code,base_code=binding(base.__file__),kind=kind,
    reader_plan=reader_plan,child_wrapper=child_wrapper,reader_evidence=list(reader_evidence),intent=intent,
    original_command=command,selected_command=selected,timeout_seconds=seconds,memory_limit_bytes=8*1024**3,
    original_snapshot_unchanged=True,semantic_budget_reset=False,automatic_retry=False))
  return original_runner(selected,**options)

 if kind=='environment':namespace['_run_in_process_group']=guarded_child
 else:
  proxy=SimpleNamespace(**base.__dict__);proxy._run_in_process_group=guarded_child;namespace['base']=proxy
 selected=FunctionType(function.__code__,namespace,function.__name__,function.__defaults__,function.__closure__)
 selected.__kwdefaults__=function.__kwdefaults__
 selected.declared_runner=guarded_child
 return selected


def validate_child(producer, *, base, kind, reader_plan, child_wrapper, intent, reader_evidence):
 """Validate actual wrapper invocation before calling the unchanged child."""
 sidecar_path=Path(intent['path']).parent/'declared-runtime-adapter.json';sidecar=checked(binding(sidecar_path))
 require(set(sidecar)=={'schema_version','adapter','producer_code','base_code','kind','reader_plan','child_wrapper',
   'reader_evidence','intent','original_command','selected_command','timeout_seconds','memory_limit_bytes',
   'original_snapshot_unchanged','semantic_budget_reset','automatic_retry'}and sidecar['schema_version']==SCHEMA,
   'closed declared runtime adapter required')
 require(sidecar['adapter']==binding(__file__) and sidecar['producer_code']==binding(producer.__file__)
   and sidecar['base_code']==binding(base.__file__) and sidecar['kind']==kind
   and sidecar['reader_plan']==reader_plan and sidecar['child_wrapper']==child_wrapper
   and sidecar['reader_evidence']==list(reader_evidence) and sidecar['intent']==intent,'child reader lineage differs')
 for row in [reader_plan,child_wrapper,*reader_evidence]:require(binding(row['path'])==row,'child dependency changed')
 seconds=contract(producer,base,kind,intent,sidecar['original_command'])
 require(sidecar['timeout_seconds']==seconds and sidecar['memory_limit_bytes']==8*1024**3
   and sidecar['original_snapshot_unchanged']is True and sidecar['semantic_budget_reset']is False
   and sidecar['automatic_retry']is False,'child guard or budget declaration differs')
 import sys
 expected=[sys.executable,'-I','-B',child_wrapper['path'],'owning-child','--plan',reader_plan['path'],
    '--plan-sha256',reader_plan['sha256'],'--kind',kind,'--intent',intent['path'],'--intent-sha256',intent['sha256']]
 require(sidecar['selected_command']==expected and sidecar['original_command'][0]==sys.executable,
  'child command identity differs')
 require(sys.argv==expected[3:],'actual child argv differs from declared adapter')
 return binding(sidecar_path)
