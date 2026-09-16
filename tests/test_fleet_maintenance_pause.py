import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import pytest
from scripts.fleet import maintenance_pause as M


def identity(pid,start='10',parent=1,state='S'):
    return dict(pid=pid,start_ticks=start,ppid=parent,state=state,command=['fixture'])


def test_descendants_span_new_sessions_and_keep_discovered_orphans():
    root=identity(10);child=identity(11,parent=10);grand=identity(12,parent=11)
    known=M.descendants({12:grand,11:child,10:root},{10:root})
    assert list(known)==[10,11,12]
    orphan=dict(grand,ppid=1)
    assert M.descendants({10:root,12:orphan},known)[12]==grand


def test_reused_parent_pid_does_not_acquire_foreign_children():
    assert set(M.descendants({10:identity(10,'new'),11:identity(11,parent=10)},{10:identity(10)}))=={10}


@pytest.mark.parametrize('live,sent',[(identity(99),True),(None,False),(identity(99,'reused'),False)])
def test_pidfd_signal_never_targets_reused_identity(monkeypatch,live,sent):
    calls=[];monkeypatch.setattr(M.os,'pidfd_open',lambda pid:7,raising=False)
    monkeypatch.setattr(M.os,'close',lambda fd:calls.append(('close',fd)))
    monkeypatch.setattr(M.signal,'pidfd_send_signal',lambda fd,n:calls.append(('signal',fd,n)),raising=False)
    monkeypatch.setattr(M,'process',lambda pid:live)
    assert M.signal_identity(identity(99),signal.SIGCONT)==sent
    assert any(c[0]=='signal' for c in calls)==sent
    assert calls[-1]==('close',7)


def test_thaw_only_owned_and_idempotent(tmp_path,monkeypatch):
    (tmp_path/'stops').mkdir();(tmp_path/'resumed').mkdir()
    M.retain(tmp_path/'stops/0001.json',dict(identity=identity(11),initial_state='S'))
    calls=[];monkeypatch.setattr(M,'signal_identity',lambda i,s:calls.append((i,s)) or True)
    first=M.thaw(tmp_path,'fixture');assert M.thaw(tmp_path,'repeat')==first
    assert calls==[(identity(11),signal.SIGCONT)]
    assert (tmp_path/'thaw-intent.json').exists()


def test_preexisting_stopped_state_cannot_be_resumed(tmp_path,monkeypatch):
    (tmp_path/'stops').mkdir();(tmp_path/'resumed').mkdir()
    M.retain(tmp_path/'stops/0001.json',dict(identity=identity(11),initial_state='T'))
    monkeypatch.setattr(M,'signal_identity',lambda *a:pytest.fail('must not signal'))
    with pytest.raises(ValueError):M.thaw(tmp_path,'fixture')


def make_plan(tmp_path,pid=123,start='10',command=None,seconds=4):
    job=M.retain(tmp_path/'job.json',dict(fixture_only=True,workbook='9999'))
    command=command or ['python','--job',job['path'],'--job-sha256',job['sha256'],'--owner','fixture-owner']
    receipt=M.retain(tmp_path/'launch.json',dict(remote=dict(pid=pid,start_ticks=start,command=command,job=job)))
    return M.retain(tmp_path/'plan.json',dict(schema_version='fleet-maintenance-pause/v1',helper=M.binding(Path(M.__file__).absolute()),directory=str(tmp_path/'pause'),maximum_pause_seconds=seconds,targets=[receipt]))


@pytest.mark.parametrize('seconds',[0,46,60,True,float('nan')])
def test_pause_bound_is_not_relaxable(tmp_path,seconds):
    with pytest.raises(ValueError):M.load(make_plan(tmp_path,seconds=seconds))


def test_existing_freeze_namespace_cannot_replay(tmp_path,monkeypatch):
    row=make_plan(tmp_path);(tmp_path/'pause').mkdir()
    monkeypatch.setattr(M.os,'pidfd_open',lambda *a:None,raising=False)
    monkeypatch.setattr(M.signal,'pidfd_send_signal',lambda *a:None,raising=False)
    with pytest.raises(ValueError,match='never replay'):M.freeze(row)


@pytest.mark.skipif(not hasattr(os,'pidfd_open'),reason='Linux pidfd integration')
@pytest.mark.parametrize('pre_stopped',[False,True])
def test_actual_separate_session_tree_auto_thaws_after_freezer_exits(tmp_path,pre_stopped):
    fixture=tmp_path/'fixture.py';fixture.write_text('import subprocess,sys,time\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],start_new_session=True)\nprint(p.pid,flush=True)\ntime.sleep(30)\n')
    # This is an isolated synthetic process tree, never a fleet worker.
    job=M.retain(tmp_path/'fixture-job.json',dict(fixture_only=True,workbook='9999'))
    command=[sys.executable,'-B',str(fixture),'--job',job['path'],'--job-sha256',job['sha256'],'--owner','fixture-owner']
    child=subprocess.Popen(command,stdout=subprocess.PIPE,text=True,start_new_session=True)
    grand=int(child.stdout.readline());root=M.process(child.pid)
    if pre_stopped:
        os.kill(grand,signal.SIGSTOP)
        while M.process(grand)['state']!='T':time.sleep(.01)
    try:
        receipt=M.retain(tmp_path/'launch.json',dict(remote=dict(root,job=job)))
        row=M.retain(tmp_path/'plan.json',dict(schema_version='fleet-maintenance-pause/v1',helper=M.binding(Path(M.__file__).absolute()),directory=str(tmp_path/'pause'),maximum_pause_seconds=4,targets=[receipt]))
        result=subprocess.run([sys.executable,'-I','-B',M.__file__,'freeze','--plan',row['path'],'--sha256',row['sha256']],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        assert M.process(child.pid)['state']=='T' and M.process(grand)['state']=='T'
        end=time.monotonic()+7
        while not (tmp_path/'pause/thawed.json').exists() and time.monotonic()<end:time.sleep(.05)
        assert (tmp_path/'pause/thawed.json').exists()
        assert M.process(child.pid)['state']!='T'
        assert (M.process(grand)['state']=='T')==pre_stopped
        # Root invocation exited; its independent watchdog resumed both sessions.
        assert len(list((tmp_path/'pause/resumed').glob('*.json')))==(1 if pre_stopped else 2)
    finally:
        for pid in [grand,child.pid]:
            try:os.kill(pid,signal.SIGCONT);os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        child.wait(timeout=5)


def test_matching_owner_orphan_blocks_freeze():
    root=dict(identity(10),owner='selected',workbook='0200')
    orphan=dict(identity(11),fleet={'HARBOR_FLEET_OWNER':'selected','HARBOR_FLEET_WORKBOOK':'0200'})
    with pytest.raises(ValueError,match='orphan'):M.owner_coverage({10:root,11:orphan},{10:root},[root])
    M.owner_coverage({10:root,11:orphan},{10:root,11:orphan},[root])


def test_foreign_owner_does_not_expand_signal_scope():
    root=dict(identity(10),owner='selected',workbook='0200')
    other=dict(identity(11),fleet={'HARBOR_FLEET_OWNER':'other','HARBOR_FLEET_WORKBOOK':'0200'})
    M.owner_coverage({10:root,11:other},{10:root},[root])
