import ast,os
from pathlib import Path
import pytest
from scripts import harbor_campaign as B,harbor_instruction_campaign as C,harbor_publish as P
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('module,function',[('harbor_campaign.py','run'),('harbor_instruction_campaign.py','run'),('harbor_publish.py','_run_publication')])
def test_actual_guard_call_keeps_own_and_fleet_fds(module,function):
    tree=ast.parse((ROOT/'scripts'/module).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function)
    calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='_run_in_process_group' or isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_run_in_process_group']
    assert len(calls)==1
    kw=next(k.value for k in calls[0].keywords if k.arg=='guardian_lock_fds')
    result=eval(compile(ast.fix_missing_locations(ast.Expression(kw)),'<actual-guardian-routing>','eval'),{'fd':7,'fds':(7,8),'guardian_lock_fds':(20,21)})
    assert result==((7,8,20,21) if module=='harbor_publish.py' else (7,20,21))

def test_descriptor_validation_and_ownership(tmp_path):
    path=tmp_path/'fleet.lock';fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
    try:
        assert B._guardian_fds((fd,))==(fd,)
        assert B._guardian_fds(())==()
        for bad in ([fd],(fd,fd),(True,),(-1,)):
            with pytest.raises(ValueError):B._guardian_fds(bad)
        os.fstat(fd)  # Validation never closes caller custody.
    finally:os.close(fd)
    with pytest.raises(OSError):B._guardian_fds((fd,))

def test_publisher_passes_optional_custody_preserving_legacy_call(tmp_path,monkeypatch):
    fd=os.open(tmp_path/'slot',os.O_RDWR|os.O_CREAT,0o600);calls=[]
    monkeypatch.setattr(P,'_run_publication',lambda *a,**k:calls.append(k) or 'outcome')
    try:
        assert P.run(None,None)=='outcome';assert calls[-1]=={'resume':None}
        assert P.run(None,None,guardian_lock_fds=(fd,))=='outcome';assert calls[-1]=={'resume':None,'guardian_lock_fds':(fd,)}
    finally:os.close(fd)

def test_guardian_retains_both_fleet_and_job_after_parent_death(tmp_path):
    import contextlib,fcntl,json,signal,sys
    from test_process_parent_death import controller,wait_for,running,terminate
    ready=tmp_path/'ready.json';signaled=tmp_path/'signaled'
    paths=[tmp_path/'fleet.lock',tmp_path/'job.lock']
    code=("import os,signal,time,json;from pathlib import Path;"
          f"signal.signal(signal.SIGTERM,lambda *_:Path({str(signaled)!r}).write_text('cleanup'));"
          f"Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid()}}));time.sleep(60)")
    script=f'''import fcntl,sys
from scripts.batch.hardened_runner import _run_in_process_group
from scripts.harbor_campaign import _guardian_fds
with open({str(paths[0])!r},'a') as fleet,open({str(paths[1])!r},'a') as job,open({str(tmp_path/'worker.log')!r},'wb') as log:
 fcntl.flock(fleet,fcntl.LOCK_EX);fcntl.flock(job,fcntl.LOCK_EX)
 _run_in_process_group([sys.executable,'-c',{code!r}],stdout=log,timeout_seconds=60,grace_seconds=2.0,guardian_lock_fds=_guardian_fds((fleet.fileno(),job.fileno())))
'''
    parent=controller(tmp_path,script);worker=None
    try:
        wait_for(ready.is_file);worker=json.loads(ready.read_text())['pid']
        parent.kill();parent.wait(timeout=3);wait_for(signaled.is_file)
        assert running(worker)
        for path in paths:
            with path.open('a') as lock:
                with pytest.raises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        wait_for(lambda:not running(worker))
        for path in paths:
            with path.open('a') as lock:
                def released():
                    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return True
                    except BlockingIOError:return False
                wait_for(released)
    finally:
        terminate(parent)
        if worker:
            with contextlib.suppress(ProcessLookupError):os.killpg(worker,signal.SIGKILL)
