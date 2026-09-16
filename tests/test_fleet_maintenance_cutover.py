"""Synthetic root-orchestration failure paths never signal live processes."""
import json
from pathlib import Path
import pytest
from scripts.fleet import maintenance_cutover as C


def save(path,value):
    path.write_text(json.dumps(value));return C.binding(path)


@pytest.mark.parametrize('failure',['freeze','upgrade','thaw',None])
def test_every_selected_host_is_thawed_once_even_if_freeze_is_uncertain(tmp_path,monkeypatch,failure):
    h=tmp_path.resolve();upgrade=h/'upgrade.py'
    upgrade.write_text('def execute(*args):\n '+("raise ValueError('synthetic upgrade failure')" if failure=='upgrade' else "return {'synthetic': True}")+'\n')
    hosts=[dict(host=x,plan=save(h/(x+'.json'),dict(synthetic=True))) for x in ['hhu-server-2','hhu-server-4']]
    plan=save(h/'plan.json',dict(schema_version='fleet-maintenance-cutover/v1',driver=C.binding(Path(C.__file__).absolute()),upgrade_driver=C.binding(upgrade),upgrade_plan=save(h/'upgrade-plan.json',{}),hosts=hosts))
    calls=[]
    def remote(selected,action,home):
        calls.append((selected['host'],action))
        if selected['host']=='hhu-server-2' and ((failure=='freeze' and action=='freeze') or (failure=='thaw' and action=='thaw')):raise TimeoutError('synthetic lost response')
        return save(home/(selected['host']+'-'+action+'.json'),dict(synthetic=True))
    monkeypatch.setattr(C,'remote',remote)
    if failure in ('freeze','upgrade'):
        with pytest.raises(ValueError):C.execute(plan,h/'journal')
    else:C.execute(plan,h/'journal')
    assert sorted(calls)==sorted([(r['host'],a) for r in hosts for a in ('freeze','thaw')])
    summary=json.loads((h/'journal/thaw-summary.json').read_text())
    assert len(summary)==2
    if failure=='thaw':assert sum(x.get('watchdog_required',False) for x in summary)==1
    if failure=='freeze':assert not (h/'journal/policy-active.json').exists()
    with pytest.raises(ValueError,match='fresh'):C.execute(plan,h/'journal')
    assert len(calls)==4


def test_no_http_barrier_blocks_original_begin_without_changing_rows(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from scripts.fleet.coordinator import Coordinator
    h=tmp_path.resolve();c=Coordinator(h/'fleet.sqlite',clock=lambda:1000.);c.enroll('0001','a'*64,'source');c.reconcile('0001','unused')
    claim=c.claim('0001','hhu-server-4:slot-0:a','claim','b'*64,3600);args={k:claim[k]for k in ('workbook','owner','token','fence')}
    before=c.status();started=threading.Event();finished=threading.Event()
    def begin():
        started.set();r=Coordinator(c.database,clock=c.clock).begin('model','request',request_sha256='c'*64,**args);finished.set();return r
    with ThreadPoolExecutor(max_workers=1)as pool:
        with C.no_http_barrier(c.database,h):
            future=pool.submit(begin);assert started.wait(1)
            assert not finished.wait(.1)
            # Read-only access still proves no request was granted.
            import sqlite3
            with sqlite3.connect('file:'+str(c.database)+'?mode=ro',uri=True)as read:
                assert read.execute("SELECT COUNT(*) FROM operations WHERE state='active'").fetchone()[0]==0
        assert future.result(timeout=3)['execute']
    assert json.loads((h/'no-http-barrier-released.json').read_text())['database_changes']==0
    assert len([x for x in c.status()['operations']if x['state']=='active'])==1


def test_active_http_refuses_before_any_freeze(tmp_path):
    from scripts.fleet.coordinator import Coordinator
    h=tmp_path.resolve();c=Coordinator(h/'fleet.sqlite',clock=lambda:1000.);c.enroll('0001','a'*64,'source');c.reconcile('0001','unused')
    claim=c.claim('0001','hhu-server-4:slot-0:a','claim','b'*64,3600);args={k:claim[k]for k in ('workbook','owner','token','fence')}
    c.begin('model','request',request_sha256='c'*64,**args);before=c.status();entered=False
    with pytest.raises(ValueError,match='active HTTP'):
        with C.no_http_barrier(c.database,h):entered=True
    assert not entered and c.status()==before
    assert not (h/'no-http-barrier.json').exists()


def test_failed_freeze_releases_original_write_reservation(tmp_path):
    from scripts.fleet.coordinator import Coordinator
    h=tmp_path.resolve();c=Coordinator(h/'fleet.sqlite',clock=lambda:1000.);before=c.status()
    with pytest.raises(RuntimeError,match='freeze transport'):
        with C.no_http_barrier(c.database,h):raise RuntimeError('freeze transport')
    assert c.status()==before
    c.enroll('0001','a'*64,'source')
    assert json.loads((h/'no-http-barrier-released.json').read_text())['database_changes']==0


def test_database_parent_symlink_refused(tmp_path):
    from scripts.fleet.coordinator import Coordinator
    h=tmp_path.resolve();real=h/'real';real.mkdir();Coordinator(real/'fleet.sqlite');link=h/'link';link.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError,match='canonical'):
        with C.no_http_barrier(link/'fleet.sqlite',h):pytest.fail('must not freeze')
