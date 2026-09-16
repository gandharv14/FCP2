"""Supervisor tests: selection custody, stop boundaries, PID identity and real slots."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from scripts.fleet import pool
from scripts.fleet.coordinator import Coordinator, serve


def job(tmp_path,wb='0001'):
    return ({'path':str(tmp_path/(wb+'.json')),'sha256':'a'*64},
            {'workbook':wb,'cwd':str(tmp_path),'run':str(tmp_path/(wb+'-attempt')),
             'source':{'sha256':'b'*64},'minimum_disk_bytes':12*1024**3})


def healthy(root):
    return dict(memory_available_bytes=64*1024**3,disk_free_bytes=64*1024**3,load_average=[0,0,0],cpus=16)


def test_two_slots_reserve_same_local_job_once(tmp_path,monkeypatch):
    monkeypatch.setattr(pool,'resources',healthy)
    jobs=[job(tmp_path)];status={'workbooks':[dict(workbook='0001',state='ready',source_sha256='b'*64)]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        selected=list(executor.map(lambda slot:pool.reserve(tmp_path,jobs,status,slot,'hhu-server'),[0,1]))
    assert sum(row is not None for row in selected)==1
    assert pool.reserve(tmp_path,jobs,status,0,'hhu-server') is None
    assert json.loads((tmp_path/'dispatches/0001.json').read_text())['automatic_retry'] is False


def test_stop_prevents_dispatch_and_expired_hold_never_selected(tmp_path,monkeypatch):
    monkeypatch.setattr(pool,'resources',healthy)
    jobs=[job(tmp_path)];status={'workbooks':[dict(workbook='0001',state='hold',source_sha256='b'*64)]}
    assert pool.reserve(tmp_path,jobs,status,0,'hhu-server') is None
    status['workbooks'][0]['state']='ready'
    pool.persist(tmp_path/'stop.json',{'force':False})
    assert pool.reserve(tmp_path,jobs,status,0,'hhu-server') is None
    assert not (tmp_path/'dispatches/0001.json').exists()


@pytest.mark.parametrize('change',[{'memory_available_bytes':None},{'memory_available_bytes':11*1024**3},{'disk_free_bytes':11*1024**3},{'load_average':[25,0,0]}])
def test_resource_observability_and_headroom_are_required(tmp_path,monkeypatch,change):
    monkeypatch.setattr(pool,'resources',lambda root:{**healthy(root),**change})
    status={'workbooks':[dict(workbook='0001',state='ready',source_sha256='b'*64)]}
    assert pool.reserve(tmp_path,[job(tmp_path)],status,0,'hhu-server') is None


def test_existing_intent_directory_cannot_resume_as_fresh_job(tmp_path,monkeypatch):
    monkeypatch.setattr(pool,'resources',healthy)
    selected=job(tmp_path);Path(selected[1]['run']).mkdir()
    status={'workbooks':[dict(workbook='0001',state='ready',source_sha256='b'*64)]}
    assert pool.reserve(tmp_path,[selected],status,0,'hhu-server') is None


def test_signal_refuses_reused_pid_and_stops_exact_process():
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    try:
        identity=pool.process_identity(process.pid);assert identity
        with pytest.raises(ValueError):pool.signal_exact({**identity,'start':'wrong'},signal.SIGTERM)
        assert process.poll() is None
        pool.signal_exact(identity,signal.SIGTERM);process.wait(timeout=5)
        assert pool.process_identity(process.pid) is None
    finally:
        if process.poll() is None:process.kill();process.wait()


def wait_state(root,expected):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        state=pool.status(root)
        if len(state['slots'])==2 and all(row['state']==expected for row in state['slots']):return state
        time.sleep(0.1)
    raise AssertionError(f'states did not reach {expected}: {state}')


def test_real_two_slot_start_drain_and_safe_resume(tmp_path):
    c=Coordinator(tmp_path/'validation.sqlite');server=serve(c,'127.0.0.1',0,'w'*32,'a'*32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    repository=Path(pool.__file__).resolve().parents[2]
    token=tmp_path/'worker.token';token.write_text('w'*32)
    config={'schema_version':pool.SCHEMA,'host':'hhu-server','code_root':str(repository),
            'python':sys.executable,'worker':pool.binding(repository/'scripts/fleet/worker.py'),
            'url':f'http://127.0.0.1:{server.server_port}','token_file':str(token),'jobs':[]}
    bound=pool.persist(tmp_path/'pool.json',config,immutable=True);root=tmp_path/'pool';identities=[]
    try:
        first=pool.start(bound,root);identities.extend(r['identity'] for r in first['slots'])
        state=wait_state(root,'IDLE')
        assert all(r['supervisor_alive'] for r in state['slots'])
        assert state['dispatch_count']==0 and state['pipeline_validation_claimed'] is False
        with pytest.raises(ValueError,match='still running'):pool.start(bound,root,resume=True)
        pool.stop(root);wait_state(root,'STOPPED')
        deadline=time.monotonic()+5
        while any(pool.process_identity(i['pid'])==i for i in identities) and time.monotonic()<deadline:time.sleep(.1)
        second=pool.start(bound,root,resume=True);identities.extend(r['identity'] for r in second['slots'])
        assert second['generation']!=first['generation']
        wait_state(root,'IDLE');pool.stop(root);wait_state(root,'STOPPED')
        assert len(list((root/'control-history').glob('*.json')))==1
    finally:
        for identity in identities:
            if pool.process_identity(identity['pid'])==identity:
                pool.signal_exact(identity,signal.SIGKILL)
        server.shutdown();server.server_close();thread.join()


def test_global_uncertain_operation_prevents_consuming_more_attempts(tmp_path,monkeypatch):
    monkeypatch.setattr(pool,'resources',healthy)
    status={'workbooks':[dict(workbook='0001',state='ready',source_sha256='b'*64)],
            'operations':[dict(kind='model',state='hold')]}
    assert pool.reserve(tmp_path,[job(tmp_path)],status,0,'hhu-server') is None
    assert not (tmp_path/'dispatches/0001.json').exists()


def test_force_interrupts_exact_guarded_fixture_worker_and_retains_dispatch(tmp_path,monkeypatch):
    # Synthetic subprocess only. This validates interruption custody, not an
    # authoring result, model review or publication.
    c=Coordinator(tmp_path/'validation.sqlite')
    c.enroll('0001','b'*64,'synthetic');c.reconcile('0001','synthetic')
    server=serve(c,'127.0.0.1',0,'w'*32,'a'*32)
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    token=tmp_path/'worker.token';token.write_text('w'*32)
    worker=tmp_path/'fixture_worker.py';worker.write_text('import time\ntime.sleep(60)\n')
    selected,job_value=job(tmp_path)
    job_value['memory_limit_bytes']=8*1024**3
    selected=pool.persist(tmp_path/'job.json',job_value,immutable=True)
    value={'schema_version':pool.SCHEMA,'host':'hhu-server','code_root':str(tmp_path),
           'python':sys.executable,'worker':pool.binding(worker),'url':f'http://127.0.0.1:{server.server_port}',
           'token_file':str(token),'jobs':[selected]}
    bound=pool.persist(tmp_path/'config.json',value,immutable=True)
    root=tmp_path/'pool';generation='synthetic';directory=root/'generations'/generation
    pool.persist(directory/'generation.json',{'config':bound,'supervisor':pool.binding(Path(pool.__file__).resolve())},immutable=True)
    monkeypatch.setattr(pool,'resources',healthy)
    monkeypatch.setattr(pool.signal,'signal',lambda *args:None)
    errors=[]
    def run():
        try:pool.run_slot(bound,root,generation,0)
        except BaseException as exc:errors.append(type(exc).__name__)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    identity=None
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state_file=directory/'slot-0.state.json'
            if state_file.exists():
                state=json.loads(state_file.read_text())
                if state.get('worker_identity'):
                    identity=state['worker_identity'];break
            time.sleep(.05)
        assert identity and pool.process_identity(identity['pid'])==identity
        pool.persist(root/'stop.json',{'force':True})
        thread.join(timeout=15)
        assert not thread.is_alive() and not errors
        result=json.loads((directory/'slot-0.workbook-0001.result.json').read_text())
        assert result['forced'] is True and result['automatic_retry'] is False
        assert (root/'dispatches/0001.json').is_file()
        assert pool.process_identity(identity['pid']) is None
    finally:
        if identity and pool.process_identity(identity['pid'])==identity:pool.signal_exact(identity,signal.SIGKILL)
        pool.persist(root/'stop.json',{'force':True});thread.join(timeout=5)
        server.shutdown();server.server_close();serving.join()
