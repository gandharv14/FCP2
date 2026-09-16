"""Retained deterministic repair never re-enters arbitration or a spent step."""
from dataclasses import replace,asdict
from pathlib import Path
from types import SimpleNamespace
import json,pytest
from scripts.batch import harbor_disclosure_command_repair as Q,harbor_instruction_worker as W
from scripts import harbor_instruction_campaign as I
from test_instruction_worker import case,instruction_setup


def test_actual_worker_selects_new_permit_and_keeps_failed_original(case,monkeypatch):
 worker,selection,lease,*_=case
 def fail(*a,**k):raise ValueError('retained original failure')
 monkeypatch.setattr(W,'_operation',fail)
 with pytest.raises(ValueError):W.step(worker,selection,lease)
 old=Q.tree(worker.run/'steps/disclosure-build')
 p=W.keep(worker.run/'repair/permit.json',dict(schema_version=Q.SCHEMA,phase='disclosure-build',predecessor=None,selection=W.codec.encode(selection),lease=asdict(lease)))
 selected=replace(worker,code_repair=p);seen=[]
 monkeypatch.setattr(Q,'validate',lambda w,b:W.read(p))
 def op(w,s,l,phase,directory,**kw):seen.append((phase,directory));return s,l,None
 monkeypatch.setattr(W,'_operation',op)
 with pytest.raises(ValueError,match='preceding checkpoint'):W.step(selected,selection,lease)
 result=W.step(selected,selection,lease,checkpoint=p)
 assert result.status=='STEP_COMPLETE'and result.next_step=='disclosure-review'
 assert seen==[('disclosure-build',worker.run/'repair/steps/disclosure-build')]
 assert Q.tree(worker.run/'steps/disclosure-build')==old
 assert W.read(result.evidence)['intent']==W.record(worker.run/'repair/steps/disclosure-build/intent.json')
 # Same permit cannot dispatch the completed suffix again.
 with pytest.raises(ValueError,match='preceding checkpoint'):W.step(selected,selection,lease,checkpoint=p)
 assert len(seen)==1


@pytest.mark.parametrize('start',[0,2,3])
def test_only_selected_suffix_executes_with_original_guard_and_immutable_producer(case,monkeypatch,start):
 worker,selection,lease,t,*_=case;worker.run.mkdir();origin=worker.run/'producer';origin.mkdir();(origin/'actual-role-response.txt').write_text('original reviewed arbitration')
 before=Q.tree(origin);profile=W.keep(worker.run/'profile.json',dict(start_after=start,producer_tree=before,command_plan={'path':'actual-plan','sha256':'a'*64}))
 permit=W.keep(worker.run/'repair/permit.json',dict(schema_version=Q.SCHEMA,phase='disclosure-build',profile=profile));worker=replace(worker,code_repair=permit)
 monkeypatch.setattr(Q,'validate',lambda *a:W.read(permit));commands=['detect','context','write','faithcheck','verify'];seen=[]
 def plan(task):
  # Exercise the real producer's binding/type/lifecycle boundary. Passing the
  # decoded tracker value here used to survive a stubbed command-plan test.
  before,identity=W.producer.task_context(task)
  assert task.tracker==W.record(worker.context.tracker_root/(before['workbook_id']+'.json'))
  assert identity['workbook']==before['workbook_id']
  return dict(after_zero_or_validated_roles=[[name]for name in commands])
 monkeypatch.setattr(W.producer,'disclosure_command_plan',plan)
 monkeypatch.setattr(W,'_guarded',lambda w,t,d,name,argv,fd:seen.append((name,argv,fd)))
 monkeypatch.setattr(W,'_model',lambda *a,**k:pytest.fail('arbitration/model replay'))
 # COMMANDS_REQUIRED does not consume the new producer namespace.
 assert Q.remaining_operation(worker,selection,lease,worker.run/'repair/step',commands=False,lock_fd=42)[2]=='COMMANDS_REQUIRED'
 assert not Q.producer_run(worker).exists()
 assert Q.remaining_operation(worker,selection,lease,worker.run/'repair/step',commands=True,lock_fd=42)[2]is None
 assert seen==[('after-%02d'%i,[name],42)for i,name in enumerate(commands)if i>=start]
 assert Q.tree(origin)==before
 assert all(Q.tree(Q.producer_run(worker))[name]==sha for name,sha in before.items())
 with pytest.raises(ValueError,match='already started'):Q.remaining_operation(worker,selection,lease,worker.run/'repair/step',commands=True,lock_fd=42)
 assert len(seen)==5-start


def test_direct_retry_codec_requires_exact_configured_permit(case,monkeypatch):
 worker,s,l,*_=case;permit=W.keep(worker.run/'repair/permit.json',dict(schema_version=Q.SCHEMA,selection=W.codec.encode(s),lease=asdict(l)))
 w=replace(worker,code_repair=permit);d=W.keep(worker.run/'descriptor.json',dict(worker=I.worker_encode(w)))
 value=dict(schema_version=I.SCHEMA,descriptor=d,status='RETRY_READY',publication_performed=False,evidence=[permit],selection=W.codec.encode(s),lease=I.base.encode(l),checkpoint=permit)
 p=W.keep(worker.run/'result.json',value);assert I._prior(p,d)[1]==s
 foreign=W.keep(worker.run/'foreign.json',dict(schema_version=Q.SCHEMA,selection=W.codec.encode(s),lease=asdict(l)))
 p=W.keep(worker.run/'bad.json',{**value,'checkpoint':foreign})
 with pytest.raises(ValueError,match='detached'):I._prior(p,d)


@pytest.mark.parametrize('change',['removed','foreign','changed_original','duplicate'])
def test_snapshot_allowance_never_accepts_unrelated_or_rewritten_history(tmp_path,change):
 old=tmp_path/'old';new=tmp_path/'new';name='.cursor/skills/task-disclosure/scripts/disclose.py'
 for root,text in [(old,'old'),(new,'new')]:
  p=root/name;p.parent.mkdir(parents=True);p.write_text(text);(root/'retained.txt').write_text('same original')
 def snapshot(root,path):return W.keep(path,dict(root=str(root),files=[dict(relative=n,sha256=h)for n,h in Q.tree(root).items()]))
 a=snapshot(old,tmp_path/'old.json');b=snapshot(new,tmp_path/'new.json')
 assert Q.snapshot_changes(a,b)==[name]
 if change=='removed':(new/name).unlink();b=snapshot(new,tmp_path/'removed.json')
 if change=='foreign':(new/'unknown.py').write_text('bad');b=snapshot(new,tmp_path/'foreign.json')
 if change=='changed_original':(old/name).write_text('different old')
 if change=='duplicate':v=W.read(b);v['files']*=2;b=W.keep(tmp_path/'duplicate.json',v)
 with pytest.raises(ValueError):Q.snapshot_changes(a,b)


@pytest.mark.parametrize('rc',[1,2])
def test_sealed_cli_failure_status_is_retained(rc):
 Q.command_status({'returncode':rc},failed=True)
 with pytest.raises(ValueError):Q.command_status({'returncode':rc},failed=False)

@pytest.mark.parametrize('rc',[0,-9,None,True,124,255,'2'])
def test_unknown_or_successful_command_is_not_a_failed_validation(rc):
 with pytest.raises(ValueError):Q.command_status({'returncode':rc},failed=True)


@pytest.mark.parametrize('mutation',[None,'predicate','unrelated','guard','extra_file'])
def test_indexed_method_successor_is_exact(tmp_path,mutation):
 import ast
 name='scripts/batch/harbor_method_items.py'
 current=(Path(__file__).resolve().parents[1]/name).read_text()
 before=ast.parse(current)
 before.body=[n for n in before.body if not(isinstance(n,ast.FunctionDef)and n.name=='top_labelled_vertical_family')]
 for n in ast.walk(before):
  if isinstance(n,ast.If)and 'top_labelled_vertical_family' in ast.unparse(n.test):
   n.test=ast.parse('len(cells)>1 and len(set(labels))>1',mode='eval').body
 old=tmp_path/'old';new=tmp_path/'new'
 for root,text in [(old,ast.unparse(before)),(new,current)]:
  f=root/name;f.parent.mkdir(parents=True);f.write_text(text)
 if mutation=='predicate':(new/name).write_text(current.replace('len(cells)<=32','len(cells)<=3200'))
 if mutation=='unrelated':(new/name).write_text(current.replace('range(row-6,row+7)','range(row-60,row+70)'))
 if mutation=='guard':(new/name).write_text(current.replace('and not top_labelled_vertical_family','or not top_labelled_vertical_family'))
 if mutation=='extra_file':(new/'unknown.py').write_text('pass')
 def snap(root):return W.keep(tmp_path/(root.name+'.json'),dict(root=str(root),files=[dict(relative=k,sha256=v)for k,v in Q.tree(root).items()]))
 a,b=snap(old),snap(new)
 if mutation is None:assert Q.snapshot_changes(a,b)==[name]
 else:
  with pytest.raises(ValueError):Q.snapshot_changes(a,b)
