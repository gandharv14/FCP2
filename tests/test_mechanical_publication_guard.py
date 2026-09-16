from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from scripts.fleet import fresh_publication as F,instruction_semantic_continue as U
from scripts.batch import hardened_runner as G


def setup(tmp_path,monkeypatch):
    original=G._run_in_process_group
    env=(SimpleNamespace(_run_in_process_group=original),SimpleNamespace(__file__='/original-I.py'),None,None,None,
         dict(run_root=str(tmp_path/'run')),Path(G.__file__).resolve().parents[2])
    job=U.keep(tmp_path/'job.json',dict(driver='same'))
    selected=dict(output=str(tmp_path/'tail'));old=dict(original_job=job,utilities=U.binding(U.__file__))
    execution={**old,**selected,'original_job':job};row=U.keep(tmp_path/'plan.json',selected);D=SimpleNamespace()
    U.install_retained_commands(row,execution,D,env)
    pub=SimpleNamespace(binding=U.binding,require=U.require)
    return pub,old,selected,execution,U,D,env,row


def test_exact_author_wrapper_removed_original_guard_runs_real_process(tmp_path,monkeypatch):
    args=setup(tmp_path,monkeypatch);env=args[-2]
    command=[sys.executable,'-c','pass']
    with pytest.raises(ValueError,match='unselected retained child command'):
        env[0]._run_in_process_group(command)
    F.mechanical_publication_guard(*args)
    assert env[0]._run_in_process_group is G._run_in_process_group
    with (tmp_path/'log').open('wb')as log:
        assert env[0]._run_in_process_group(command,stdout=log,timeout_seconds=60,grace_seconds=3,
            memory_limit_bytes=8*1024**3 if sys.platform=='linux' else None,metrics={},cwd=tmp_path)==0


@pytest.mark.parametrize('kind',['utility','plan','environment','execution','raw'])
def test_foreign_guard_context_cannot_be_unwrapped(tmp_path,monkeypatch,kind):
    args=list(setup(tmp_path,monkeypatch));guard=args[-2][0]._run_in_process_group
    if kind=='utility':args[1]={**args[1],'utilities':{'path':'foreign','sha256':'bad'}}
    if kind=='plan':args[-1]=dict(path='/other',sha256='a'*64)
    if kind=='environment':args[-2]=tuple([*args[-2][:-1],tmp_path/'foreign-root'])
    if kind=='execution':args[3]={**args[3],'output':'/other'}
    if kind=='raw':
        cell=dict(zip(guard.__code__.co_freevars,guard.__closure__))['original']
        cell.cell_contents=lambda *a,**k:0
    with pytest.raises(ValueError):F.mechanical_publication_guard(*args)
    assert args[-2][0]._run_in_process_group is guard
