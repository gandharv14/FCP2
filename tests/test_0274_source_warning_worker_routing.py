"""Execute the actual worker routing statements in an isolated no-effect fixture.

This tests checkpoint/lease precedence, not a simulated production Q transaction.
"""
import ast,json,hashlib
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass,asdict
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'diagnostics/requested-fixes-20260910/0274-warning-development/scripts/batch/harbor_instruction_worker.py'
tree=ast.parse(SOURCE.read_text())
STEPS=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STEPS' for t in n.targets)))
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_step')
def assigns(n,name):return isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)
a=next(i for i,n in enumerate(fn.body) if assigns(n,'next_phase'))
b=next(i for i,n in enumerate(fn.body[a:],a) if assigns(n,'directory'))
ROUTE=compile(ast.fix_missing_locations(ast.Module(body=fn.body[a:b],type_ignores=[])),str(SOURCE),'exec')
@dataclass(frozen=True)
class Lease:
 owner:str='owner';token:str='token';revision:int=44;sha256:str='q44'
def record(path):
 path=Path(path);return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
def put(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return record(path)
def read(binding):
 assert record(binding['path'])==binding
 return json.loads(Path(binding['path']).read_text())
def require(condition,message):assert condition,message

def route(tmp_path,phase,*,bad_lease=False,bad_checkpoint=False):
 directory=lambda w,p:tmp_path/'steps'/p
 index=STEPS.index(phase);finished=list(STEPS[:index]);old=Lease(revision=43,sha256='r43');qlease=Lease();new=Lease(revision=45,sha256='r45')
 previous=put(directory(None,finished[-1])/'complete.json',{'selection':{'version':'prior'},'lease':asdict(old)})
 q=put(tmp_path/'q/permit.json',{'selection':{'version':'q'},'lease':asdict(qlease)})
 initial=phase=='dialogue-review'
 r=put(tmp_path/'renewal/complete.json',{'selection':{'version':'old'} if initial else {'version':'post'},'lease':asdict(old if initial else new)})
 worker=SimpleNamespace(source_warning_tail=None,boundary_recovery=None,warning_requalification=q,lease_renewal=r,disclosure_repair=None,code_repair=None,dialogue_retry=None,dialogue_semantic_retry=None,pretransport_recovery=None,retry=None)
 env=dict(STEPS=STEPS,finished=finished,worker=worker,step_directory=directory,record=record,read=read,require=require,asdict=asdict,codec=SimpleNamespace(encode=lambda v:v),saved={},phase=None,boundary=None,partial_state=None,partition=None,retry=None,semantic=None,structural=None,renewal={'checkpoint':previous},checkpoint=(r if bad_checkpoint or not initial else q),selection={'version':'q'} if initial else {'version':'post'},lease=old if bad_lease else(qlease if initial else new))
 exec(ROUTE,env)
 return env,q,r

def test_actual_old_renewal_is_for_same_initial_review_boundary():
 f=ROOT/'diagnostics/requested-fixes-20260910';blocked=json.loads((f/'0274-post-partition-authoring/017-dialogue-review/result.json').read_text());desc=read(blocked['descriptor']);renewal=read(desc['worker']['lease_renewal']);permit=read(renewal['permit'])
 assert permit['phase']=='dialogue-review'
 assert read(permit['checkpoint'])['step']=='dialogue-paraphraser'
 assert renewal['lease']['revision']==43

def test_initial_warning_wins_checkpoint_and_new_lease(tmp_path):
 env,q,r=route(tmp_path,'dialogue-review');assert env['required']==q and env['predecessor']['lease']['revision']==44
@pytest.mark.parametrize('bad', ['bad_lease','bad_checkpoint'])
def test_old_renewal_cannot_replace_initial_warning(tmp_path,bad):
 with pytest.raises(AssertionError):route(tmp_path,'dialogue-review',**{bad:True})
@pytest.mark.parametrize('phase',['final-runtime','dialogue-commit','enqueue'])
def test_later_matching_completed_boundary_renewal_wins(tmp_path,phase):
 env,q,r=route(tmp_path,phase);assert env['required']==r and env['predecessor']['lease']['revision']==45
