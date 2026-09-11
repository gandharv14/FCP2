"""Narrow current-reader projection tests; no historical code is executed."""
from pathlib import Path
from types import SimpleNamespace,ModuleType
import ast,sys,hashlib
import pytest
ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'diagnostics/requested-fixes-20260910/0184-restart-metadata-development/scripts/batch'
def require(ok,message):
 if not ok:raise ValueError(message)
def record(p):return {'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}

def tail():
 tree=ast.parse((STAGE/'harbor_instruction_local_restart.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_selected')
 index=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='expected' for t in n.targets))
 return compile(ast.Module(body=fn.body[index:index+3],type_ignores=[]),str(STAGE),'exec')
@pytest.mark.parametrize('case',['ordinary','successor','metadata','detached','changed_current'])
def test_restart_expected_inventory_only_projects_authenticated_metadata(monkeypatch,case):
 old={'validator':'old'};now={'validator':'new'};successor=None if case=='ordinary' else {'current_files':old}
 current=old if case in ('ordinary','successor') else now
 seen=[];module=ModuleType('scripts.batch.harbor_dialogue_partition_continuation')
 def checked(p,root):
  seen.append(p);require(case!='detached','not approved');return '/old',old,({'validator':'other'} if case=='changed_current' else now)
 module.historical_mechanics_permit=checked;monkeypatch.setitem(sys.modules,module.__name__,module)
 env=dict(successor=successor,permit={'current_files':old},current=current,worker=SimpleNamespace(root='/new'),require=require)
 if case in ('detached','changed_current'):
  with pytest.raises(ValueError):exec(tail(),env)
 else:exec(tail(),env)
 assert bool(seen)==(case not in ('ordinary','successor'))
 assert old=={'validator':'old'}

@pytest.mark.parametrize('fault',[None,'history','duplicate','current_validator'])
def test_completed_validator_retains_exact_historical_binding(tmp_path,monkeypatch,fault):
 old=tmp_path/'old';new=tmp_path/'new';relative='.cursor/skills/additional-assumptions-dialogue/scripts/validate_dialogue.py'
 for root in (old,new):
  path=root/relative;path.parent.mkdir(parents=True);path.write_text('same reviewed validator')
 rows=[{'relative':relative,'sha256':record(old/relative)['sha256']}]
 if fault=='duplicate':rows*=2
 snapshot={'snapshot':{'root':str(old),'files':rows}}
 if fault=='current_validator':(new/relative).write_text('unreviewed')
 module=ModuleType('scripts.batch.harbor_disclosure_partition_continuation')
 module.inventory=lambda p: {relative:('changed' if fault=='history' else record(old/relative)['sha256'])}
 monkeypatch.setitem(sys.modules,module.__name__,module)
 tree=ast.parse((STAGE/'harbor_dialogue_partition_continuation.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='completed_mechanics_validator')
 def read(b):
  assert b['sha256']=='7c8a694886422e7c8622bca1736c7ef7200d3dfd29683676f07564be697f1cf1'
  return snapshot
 env=dict(Path=Path,read=read,record=record,require=require);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(STAGE),'exec'),env)
 if fault:
  with pytest.raises(ValueError):env['completed_mechanics_validator'](new)
 else:assert env['completed_mechanics_validator'](new)==record(old/relative)
