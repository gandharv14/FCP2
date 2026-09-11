"""Real byte snapshots and notes transactions; no semantic success fixtures."""
import copy
from pathlib import Path
import pytest
from test_harbor_ready_transitions import fixture,kw,Interrupted,stopped
from scripts.batch import harbor_failure_snapshot as a
from scripts.batch import harbor_lane_notes as n
from scripts.batch import harbor_tracker_lifecycle as l
from scripts.batch import harbor_tracker as s


def test_exact_snapshot_preserves_mutable_history_and_budgets(tmp_path,monkeypatch):
    _,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True)
    f=a.FailureSnapshots(n.RootContext(tmp_path));result=f.snapshot_failure('0245',**kw(start))
    snapshot=result['tracker']['handoffs'][-1]['snapshot'];data=n.read(snapshot)
    assert data['original_evidence']==start['tracker']['promotion_failure']['evidence_paths']
    assert all(r['original']['sha256']==r['retained']['sha256'] for r in data['copies'])
    assert all(Path(r['original']['path']).stat().st_ino!=Path(r['retained']['path']).stat().st_ino for r in data['copies'])
    Path(proof['evidence'][0]['path']).write_text('legitimate subsequent owning-state change')
    assert f.require_synchronized('0245')
    assert result['tracker']['repairs']==start['tracker']['repairs']
    assert result['tracker']['failure_history'][:-1]==start['tracker']['failure_history']


@pytest.mark.parametrize('where',['prepared','notes_replaced','tracker_replaced'])
def test_snapshot_interruption_and_expiry_recovery(tmp_path,monkeypatch,where):
    _,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);f=a.FailureSnapshots(n.RootContext(tmp_path))
    def stop(point):
        if point==where:raise Interrupted()
    with pytest.raises(Interrupted):f.snapshot_failure('0245',**kw(start),_fault=stop)
    journal=n.record(next((tmp_path/'0245-notes-transactions').glob('*/transaction.json')))
    monkeypatch.setattr(a.time,'time_ns',lambda:999)
    result=f.recover_snapshot('0245',journal,owner='agent1',token=kw(start)['token'])
    assert result['tracker']['leases']==start['tracker']['leases'] and f.require_synchronized('0245')
    assert f.recover_snapshot('0245',journal,owner='agent1',token=kw(start)['token'])==result


@pytest.mark.parametrize('change',['already_changed','source_race','corrupt_copy','omission','wrong_signature','reset_budget','escaped_copy','inode_alias','extra_field','wrong_owner'])
def test_snapshot_rejects_changed_or_incomplete_history(tmp_path,monkeypatch,change):
    _,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);f=a.FailureSnapshots(n.RootContext(tmp_path));raw=p.read_bytes()
    if change=='already_changed':
        Path(proof['evidence'][0]['path']).write_text('too late')
        with pytest.raises(ValueError):f.snapshot_failure('0245',**kw(start))
    elif change=='source_race':
        old=a.shutil.copyfileobj
        def race(src,dst,length):
            old(src,dst,length);Path(proof['evidence'][0]['path']).write_text('raced')
        monkeypatch.setattr(a.shutil,'copyfileobj',race)
        with pytest.raises(ValueError):f.snapshot_failure('0245',**kw(start))
    elif change=='wrong_owner':
        args=kw(start);args['owner']='other'
        with pytest.raises(ValueError):f.snapshot_failure('0245',**args)
    else:
        binding=a.capture(start['tracker'],tmp_path/'archive');data=n.read(binding)
        if change=='corrupt_copy':
            q=Path(data['copies'][0]['retained']['path']);q.chmod(0o600);q.write_text('corrupt')
        elif change=='omission':data['copies']=[]
        elif change=='wrong_signature':data['diagnostic_signature']='f'*64
        elif change=='reset_budget':data['original_repairs']['by_signature']['old']=0
        elif change=='escaped_copy':data['copies'][0]['retained']=data['copies'][0]['original']
        elif change=='extra_field':data['approved']=True
        elif change=='inode_alias':
            import os
            target=Path(data['copies'][0]['retained']['path']);target.unlink();os.link(data['copies'][0]['original']['path'],target)
        Path(binding['path']).write_bytes(l.canonical(data));binding=n.record(Path(binding['path']))
        with pytest.raises(ValueError):a.validate(start['tracker'],binding)
    assert p.read_bytes()==raw


def test_snapshot_then_full_clear_keeps_exact_original_observation(tmp_path,monkeypatch):
    ready,p,start,selection,proof=fixture(tmp_path,monkeypatch,failure=True);snap=a.FailureSnapshots(n.RootContext(tmp_path)).snapshot_failure('0245',**kw(start))
    old_snapshot=snap['tracker']['handoffs'][-1]['snapshot']
    # New proof belongs to a fresh immutable file, while failed mutable state may
    # advance. The retained historical snapshot continues to validate exactly.
    Path(proof['evidence'][0]['path']).write_text('new owning state')
    current=tmp_path/'new-proof';current.write_text('current proof fixture');proof['evidence']=[n.record(current)]
    cleared=ready.transition('0245','clear',selection,**kw(snap))
    assert cleared['tracker']['promotion_failure'] is None and ready.require_synchronized('0245')
    assert old_snapshot in [h.get('snapshot') for h in cleared['tracker']['handoffs']]
    assert cleared['tracker']['repairs']==start['tracker']['repairs']
