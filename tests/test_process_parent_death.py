"""Real POSIX processes; no workbook, network, model, or privileged operations."""
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from scripts.batch import hardened_runner as runner


ROOT = Path(runner.__file__).resolve().parents[2]


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate(), 'bounded process checkpoint did not complete'


def running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f'/proc/{pid}/stat')
    if stat.is_file():
        try:
            return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False
    return True


def terminate(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)


def controller(tmp_path, script):
    path = tmp_path / 'controller.py'
    path.write_text(script)
    return subprocess.Popen([sys.executable, '-B', str(path)],
        env={**os.environ, 'PYTHONPATH':str(ROOT), 'PYTHONDONTWRITEBYTECODE':'1'},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_killing_controller_kills_entire_worker_group_and_not_sibling(tmp_path):
    pids = tmp_path / 'pids.json'
    launch_pids = tmp_path / 'launch-pids.json'
    grandchild = tmp_path / 'grandchild.json'
    child_code = (
        "import os,signal,time,json;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"Path({str(grandchild)!r}).write_text(json.dumps({{'pid':os.getpid(),'group':os.getpgrp()}}));"
        "time.sleep(60)"
    )
    worker_code = (
        "import os,signal,subprocess,sys,time,json;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        f"Path({str(pids)!r}).write_text(json.dumps({{'pid':os.getpid(),'child':child.pid,'group':os.getpgrp()}}));"
        "time.sleep(60)"
    )
    script = f'''import sys,json
from pathlib import Path
from scripts.batch import hardened_runner as r
real=r.subprocess.Popen
children=[]
def spawn(*a,**kw):
    p=real(*a,**kw)
    children.append(p.pid)
    Path({str(launch_pids)!r}).write_text(json.dumps(children))
    return p
r.subprocess.Popen=spawn
with open({str(tmp_path/'worker.log')!r}, 'wb') as out:
    r._run_in_process_group([sys.executable,'-c',{worker_code!r}],stdout=out,timeout_seconds=60,grace_seconds=.05)
'''
    guard = controller(tmp_path,script)
    sibling = subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
    group = None
    try:
        wait_for(lambda:pids.is_file() and grandchild.is_file())
        ids=json.loads(pids.read_text()); desc=json.loads(grandchild.read_text())
        group=ids['group']
        assert group == ids['pid'] == desc['group']
        assert ids['child'] == desc['pid']
        guard.kill(); guard.wait(timeout=3)
        launched=json.loads(launch_pids.read_text())
        assert len(launched)==2
        wait_for(lambda:all(not running(pid) for pid in [*launched,ids['child']]))
        assert sibling.poll() is None
    finally:
        terminate(guard); terminate(sibling)
        if group:
            with contextlib.suppress(ProcessLookupError): os.killpg(group,signal.SIGKILL)


@pytest.mark.parametrize('phase',['guard','launch'])
def test_parent_death_during_launch_handshake_never_executes_command(tmp_path,phase):
    checkpoint=tmp_path/'checkpoint.json'; forbidden=tmp_path/'executed'
    script=f'''import json,os,sys,time
from pathlib import Path
from scripts.batch import hardened_runner as r
real=r.subprocess.Popen
children=[]
def spawn(command,*a,**kw):
    p=real(command,*a,**kw)
    children.append(p.pid)
    if 'process_guardian.py' in str(command[2]) and command[3]=={phase!r}:
        Path({str(checkpoint)!r}).write_text(json.dumps(children))
        time.sleep(60)
    return p
r.subprocess.Popen=spawn
with open(os.devnull,'wb') as out:
    r._run_in_process_group([sys.executable,'-c',"from pathlib import Path;Path({str(forbidden)!r}).touch()"],stdout=out,timeout_seconds=60,grace_seconds=.05)
'''
    parent=controller(tmp_path,script); children=[]
    try:
        wait_for(checkpoint.is_file)
        children=json.loads(checkpoint.read_text())
        parent.kill();parent.wait(timeout=3)
        wait_for(lambda:all(not running(pid) for pid in children))
        assert not forbidden.exists()
    finally:
        terminate(parent)
        for pid in children:
            with contextlib.suppress(ProcessLookupError):os.kill(pid,signal.SIGKILL)


@pytest.mark.parametrize('code',[0,7,-signal.SIGTERM])
def test_exit_status_argv_environment_cwd_stdio_preserved(tmp_path,code):
    script="import sys,os,json;print(json.dumps([sys.argv,os.environ['GUARD_FIXTURE'],os.getcwd()]));sys.stdout.flush();"
    script += f'os.kill(os.getpid(),{-code})' if code<0 else f'sys.exit({code})'
    output=tmp_path/'out'
    with output.open('wb') as out:
        result=runner._run_in_process_group([sys.executable,'-c',script,'a b',"quote's",'$value'],stdout=out,timeout_seconds=5,grace_seconds=.05,cwd=tmp_path,env={**os.environ,'GUARD_FIXTURE':'unchanged'})
    assert result==code
    assert json.loads(output.read_text())==[['-c','a b',"quote's",'$value'],'unchanged',str(tmp_path)]


def test_missing_executable_preserves_oserror(tmp_path):
    with open(os.devnull,'wb') as out, pytest.raises(FileNotFoundError) as error:
        runner._run_in_process_group([str(tmp_path/'absent')],stdout=out,timeout_seconds=5,grace_seconds=.05)
    assert error.value.filename==str(tmp_path/'absent')


def test_exception_after_launch_kills_group(tmp_path,monkeypatch):
    real=runner._process_group_rss_bytes
    ids=[]
    def fail(pid):
        ids.append(pid)
        raise RuntimeError('observer failed')
    monkeypatch.setattr(runner,'_process_group_rss_bytes',fail)
    with open(os.devnull,'wb') as out,pytest.raises(RuntimeError,match='observer failed'):
        runner._run_in_process_group([sys.executable,'-c','import time;time.sleep(60)'],stdout=out,timeout_seconds=5,grace_seconds=.05,memory_limit_bytes=100000000)
    assert ids and all(not running(pid) for pid in ids)


def test_normal_completion_reaps_guardian_and_closes_protocol_fds(tmp_path,monkeypatch):
    processes=[]; real=runner.subprocess.Popen
    def spawn(*args,**kwargs):
        result=real(*args,**kwargs);processes.append(result);return result
    monkeypatch.setattr(runner.subprocess,'Popen',spawn)
    with open(os.devnull,'wb') as out:
        assert runner._run_in_process_group([sys.executable,'-c','pass'],stdout=out,timeout_seconds=5,grace_seconds=.05)==0
    assert len(processes)==2
    assert all(p.poll() is not None for p in processes)


def test_guardian_startup_failure_never_runs_command(tmp_path,monkeypatch):
    real=runner.subprocess.Popen;marker=tmp_path/'must-not-run';processes=[]
    def spawn(command,*args,**kwargs):
        if command[3]=='guard':
            command=[sys.executable,'-c','raise SystemExit(23)']
        result=real(command,*args,**kwargs);processes.append(result);return result
    monkeypatch.setattr(runner.subprocess,'Popen',spawn)
    with open(os.devnull,'wb') as out,pytest.raises(RuntimeError,match='did not arm'):
        runner._run_in_process_group([sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).touch()'],stdout=out,timeout_seconds=5,grace_seconds=.05)
    assert not marker.exists()
    assert all(p.poll() is not None for p in processes)


def test_guardian_retains_workbook_lock_during_parent_death_cleanup(tmp_path):
    import fcntl
    ready=tmp_path/'ready.json'; signaled=tmp_path/'signaled'; lock_path=tmp_path/'workbook.lock'
    code=("import os,signal,time,json;from pathlib import Path;"
          f"signal.signal(signal.SIGTERM,lambda *_:Path({str(signaled)!r}).write_text('cleanup'));"
          f"Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid()}}));time.sleep(60)")
    script=f'''import fcntl,sys
from scripts.batch.hardened_runner import _run_in_process_group
with open({str(lock_path)!r},'a') as lock, open({str(tmp_path/'worker.log')!r},'wb') as log:
 fcntl.flock(lock,fcntl.LOCK_EX)
 _run_in_process_group([sys.executable,'-c',{code!r}],stdout=log,timeout_seconds=60,grace_seconds=1.0,guardian_lock_fds=(lock.fileno(),))
'''
    parent=controller(tmp_path,script); worker=None
    try:
        wait_for(ready.is_file);worker=json.loads(ready.read_text())['pid']
        parent.kill();parent.wait(timeout=3);wait_for(signaled.is_file)
        with lock_path.open('a') as lock:
            # The old worker is still executing its termination grace period.
            assert running(worker)
            with pytest.raises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            wait_for(lambda:not running(worker))
            def released():
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return True
                except BlockingIOError:return False
            wait_for(released)
    finally:
        terminate(parent)
        if worker:
            with contextlib.suppress(ProcessLookupError):os.killpg(worker,signal.SIGKILL)
