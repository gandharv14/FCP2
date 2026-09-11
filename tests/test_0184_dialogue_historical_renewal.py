"""Current reader routing fixtures; no tracker CAS or campaign writes."""
from pathlib import Path
from types import SimpleNamespace,ModuleType
import sys,ast,json,hashlib
from dataclasses import dataclass,asdict
import pytest
ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'diagnostics/requested-fixes-20260910'
R=FIX/'0184-renewal-metadata-development/scripts/batch/harbor_instruction_lease_renewal.py'
def require(x,msg):
 if not x:raise ValueError(msg)
@pytest.mark.parametrize('case',['current','historical','no_marker','changed_role','missing_role','changes','foreign_role'])
def test_exact_current_or_historical_code_projection(monkeypatch,case):
 old=Path('/old');root=Path('/current');names=['scripts/batch/a.py','scripts/batch/b.py'];current={str(root/n):'new'+n for n in names};historical={str(old/n):'old'+n for n in names};files={n:'old'+n for n in names}
 W=ModuleType('scripts.batch.harbor_instruction_worker');W.code=lambda _:current
 D=ModuleType('scripts.batch.harbor_dialogue_partition_continuation');D.exists=lambda _:case!='no_marker';D.permit=lambda _:(None,{'exact':'permit'});D.historical_mechanics_permit=lambda p,r:(old,files,{})
 for m in (W,D):monkeypatch.setitem(sys.modules,m.__name__,m)
 permit={'current_code':dict(current if case=='current' else historical),'code_changes':[],'old_snapshot':{}}
 if case=='changed_role':permit['current_code'][str(old/names[0])]='forged'
 if case=='missing_role':permit['current_code'].pop(str(old/names[0]))
 if case=='foreign_role':current['/outside/module.py']='unknown'
 if case=='changes':permit['code_changes']=['unreviewed']
 node=next(x for x in ast.parse(R.read_text()).body if isinstance(x,ast.FunctionDef) and x.name=='implementation')
 env=dict(Path=Path,require=require,changes=lambda b,r:[]);exec(compile(ast.Module(body=[node],type_ignores=[]),str(R),'exec'),env)
 if case in ('current','historical'):env['implementation'](SimpleNamespace(root=root),permit)
 else:
  with pytest.raises(ValueError):env['implementation'](SimpleNamespace(root=root),permit)

WFILE=FIX/'code-0184-source-metadata-v3/scripts/batch/harbor_instruction_worker.py';tree=ast.parse(WFILE.read_text());STEPS=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STEPS' for t in n.targets)));fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_step')
def assigns(n,name):return isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)
a=next(i for i,n in enumerate(fn.body) if assigns(n,'next_phase'));b=next(i for i,n in enumerate(fn.body[a:],a) if assigns(n,'directory'));ROUTE=compile(ast.Module(body=fn.body[a:b],type_ignores=[]),str(WFILE),'exec')
@dataclass
class Lease:
 revision:int

def record(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v));return record(p)
def read(b):assert record(b['path'])==b;return json.loads(Path(b['path']).read_text())
@pytest.mark.parametrize('phase',['dialogue-writer','dialogue-paraphraser','dialogue-review','final-runtime','dialogue-commit','enqueue'])
@pytest.mark.parametrize('bad',[False,True])
def test_initial_dialogue_and_later_matching_renewal_precedence(tmp_path,phase,bad):
 index=STEPS.index(phase);finished=list(STEPS[:index]);directory=lambda w,p:tmp_path/'steps'/p
 previous=put(directory(None,finished[-1])/'complete.json',dict(selection={},lease=asdict(Lease(48))))
 d=put(tmp_path/'dialogue/complete.json',dict(selection={},lease=asdict(Lease(116))))
 r=put(tmp_path/'renewal/complete.json',dict(selection={},lease=asdict(Lease(117))))
 initial=phase=='dialogue-writer';worker=SimpleNamespace(boundary_recovery=None,warning_requalification=None,lease_renewal=r,disclosure_repair=None,code_repair=None,dialogue_retry=None,dialogue_semantic_retry=None,pretransport_recovery=None,retry=None)
 env=dict(STEPS=STEPS,finished=finished,worker=worker,step_directory=directory,record=record,read=read,require=require,asdict=asdict,codec=SimpleNamespace(encode=lambda x:x),saved={},phase=None,boundary=None,partition=None,retry=None,semantic=None,structural=None,renewal={'checkpoint':previous},dialogue_partition={},D=SimpleNamespace(completion=lambda w:(d,read(d))),checkpoint=r if not initial or bad else d,selection={},lease=Lease(48 if bad else 116 if initial else 117))
 if bad:
  with pytest.raises(ValueError):exec(ROUTE,env)
 else:
  exec(ROUTE,env);assert env['required']==(d if initial else r)

def test_final_closed_profile_contains_exact_eight_reviewed_files():
 source=FIX/'code-0184-source-metadata-v6/scripts/batch/harbor_dialogue_partition_continuation.py'
 nodes=ast.parse(source.read_text()).body;env={}
 for node in nodes:
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('SELF','ALLOWED','MECHANICS_FILES') for t in node.targets):
   exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env)
 assert len(env['MECHANICS_FILES'])==8
 assert {'scripts/batch/harbor_instruction_worker.py','scripts/batch/harbor_instruction_lease_renewal.py','scripts/batch/harbor_instruction_local_restart.py'}<=env['MECHANICS_FILES']
 overlay=json.loads((FIX/'0184-mechanics-reviewed-overlay-v5.json').read_text())
 assert {r['relative'] for r in overlay['files']}==env['MECHANICS_FILES']-{env['SELF']}
 for relative in ['scripts/batch/harbor_dialogue_partition_review.py','.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py']:
  assert (FIX/'code-0184-source-metadata-v6'/relative).read_bytes()==(FIX/'code-0184-source-metadata-v3'/relative).read_bytes()
