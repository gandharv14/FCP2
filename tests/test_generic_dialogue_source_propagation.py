"""Exercise private consumer call expressions with real bound file validation."""
from pathlib import Path
from types import SimpleNamespace
import ast,json,hashlib
import pytest
ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'diagnostics/requested-fixes-20260910/generic-dialogue-source-propagation/scripts/batch'

def record(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def checked(b):
 assert record(b['path'])==b,'changed bound bytes'
 return b
def read(b):checked(b);return json.loads(Path(b['path']).read_text())
def function(name):
 tree=ast.parse((STAGE/'harbor_instruction_producer.py').read_text())
 node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
 events=[]
 env=dict(read=read,checked_record=checked,validate_tracker=lambda b:events.append('tracker'),validate_lifecycle=lambda b:events.append('lifecycle'))
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(STAGE),'exec'),env)
 return env[name],events
@pytest.fixture
def bound(tmp_path):
 tmp_path=tmp_path.resolve();source=tmp_path/'source.xlsx';source.write_bytes(b'bound workbook fixture')
 tracker=tmp_path/'tracker.json';tracker.write_text(json.dumps({'bindings':{'source_file':record(source)}}))
 return SimpleNamespace(tracker=record(tracker),stage=tmp_path),source,tracker

def test_source_is_exact_checked_tracker_binding(bound):
 task,source,_=bound;helper,events=function('dialogue_source_workbook')
 assert helper(task)==record(source) and events==['tracker','lifecycle']
@pytest.mark.parametrize('fault',['source','tracker','missing'])
def test_source_or_tracker_tamper_rejected(bound,fault):
 task,source,tracker=bound;helper,_=function('dialogue_source_workbook')
 if fault=='source':source.write_bytes(b'changed')
 elif fault=='tracker':tracker.write_text('{}')
 else:tracker.write_text(json.dumps({'bindings':{}}));task.tracker=record(tracker)
 with pytest.raises((AssertionError,KeyError)):helper(task)

@pytest.mark.parametrize('module,expected',[('harbor_instruction_producer.py',3),('harbor_instruction_lease_renewal.py',1)])
def test_all_real_consumer_expressions_forward_checked_source(bound,module,expected):
 task,source,_=bound;helper,_=function('dialogue_source_workbook');seen=[]
 def check(*args,**kwargs):seen.append(kwargs['source_workbook']);return {'passed':True}
 tree=ast.parse((STAGE/module).read_text())
 calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='v' and n.func.attr in ('check_draft','apply')]
 assert len(calls)==expected
 draft=task.stage/'draft.md';draft.write_text('fixture prose')
 run=task.stage;directory=task.stage;(directory/'round-01').mkdir();(directory/'round-01/draft.md').write_text('fixture prose')
 env=dict(v=SimpleNamespace(check_draft=check,apply=check),task=task,t=task,claims={},review={},clean='',run=run,directory=directory,round=1,record=record,text=lambda b:Path(b['path']).read_text(),p=SimpleNamespace(text=lambda b:Path(b['path']).read_text(),dialogue_source_workbook=helper),dialogue_source_workbook=helper)
 for call in calls:eval(compile(ast.Expression(call),str(STAGE/module),'eval'),env)
 assert seen==[record(source)]*expected
 # The call sites cannot silently skip the source byte guard.
 source.write_bytes(b'changed')
 for call in calls:
  with pytest.raises(AssertionError):eval(compile(ast.Expression(call),str(STAGE/module),'eval'),env)

ROUTES=ROOT/'diagnostics/requested-fixes-20260910/generic-dialogue-source-propagation-routes/scripts/batch'
@pytest.mark.parametrize('module,count',[
 ('harbor_dialogue_semantic_retry.py',1),('harbor_dialogue_pre_review_repair.py',1),
 ('harbor_dialogue_warning_requalification.py',1),('harbor_dialogue_source_warnings.py',1),
 ('harbor_dialogue_partition_review.py',1),('harbor_dialogue_partition_continuation.py',2),
 ('harbor_readiness.py',1)])
def test_recovery_and_partition_routes_forward_exact_source(bound,module,count):
 task,source,_=bound;helper,_=function('dialogue_source_workbook');seen=[]
 def check(*args,**kwargs):
  selected=kwargs['source_workbook'];checked(selected);seen.append(selected);return {'passed':True}
 validator=SimpleNamespace(check_draft=check)
 tree=ast.parse((ROUTES/module).read_text())
 calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='check_draft']
 assert len(calls)==count
 binding=record(source);content=SimpleNamespace(stage=task.stage,bindings={'source_file':binding})
 env=dict(v=validator,validator=validator,dialogue=validator,Path=Path,body={'draft.md':''},task=task,claims={},fixed='',text='',raw_notes='',full='',stage=task.stage,read=lambda b:{},payload={},task_dir=task.stage,source=binding,a={'source':binding},p={'claims':{}},plan={'claims':{}},selection=SimpleNamespace(content=content),s=content,producer=SimpleNamespace(dialogue_source_workbook=helper))
 for call in calls:eval(compile(ast.Expression(call),str(ROUTES/module),'eval'),env)
 assert seen==[binding]*count
 source.write_bytes(b'changed source')
 for call in calls:
  with pytest.raises(AssertionError):eval(compile(ast.Expression(call),str(ROUTES/module),'eval'),env)
