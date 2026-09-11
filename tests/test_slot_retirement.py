"""Real tracker/terminal projections with synthetic owning-review fixture.

No model or campaign operations. Real flock contention is tested separately.
"""
from pathlib import Path
import copy
import fcntl
import importlib.util
import multiprocessing
import os
import sys
import subprocess
REAL_RUN = subprocess.run
from types import SimpleNamespace as NS
import pytest
from test_harbor_admission import setup
from test_dialogue_terminal import transaction, commit, T

from scripts import harbor_slot_retirement as R
FILE = Path(R.__file__)


@pytest.fixture
def released(transaction, monkeypatch):
    t = transaction; commit(t)
    stopped = T.read(T.record(t.path)); lease = stopped['leases']['task_agent']
    def failure(before, after, reasons):
        assert reasons == frozenset({'stop','lease_release'})
        assert before == stopped
        T.N.validate_notes_pair(before,Path(before['agent_notes']['path']).read_bytes())
        R.after_releases(before, after)
        return True
    T.L.lease_operation(t.path, authority=t.authority, operation='release',lease_kind='task_agent',
                       owner=lease['owner'],token=lease['token'],scope=lease['scope'],
                       expected_revision=stopped['revision'],expected_sha256=T.record(t.path)['sha256'],
                       failure_validator=failure)
    monkeypatch.setattr(R.subprocess, 'run', REAL_RUN)
    root=t.path.parent.parent
    (root/'parallel-policy').mkdir(exist_ok=True)
    for name in ('publication.lock','lane.lock'):(root/name).touch()
    lane=dict(lane=2,workbook='0001',lock=str(root/'lane.lock'))
    policy=dict(campaign_root=str(root),publication_queue_lock=str(root/'publication.lock'))
    monkeypatch.setattr(R,'_policy',lambda *a:(policy,lane))
    # Fixture's code map must be archived as production inspect does.
    old=T.inspect
    def with_code(*a):
        proof=old(*a)
        proof['evidence']=T.N.evidence_list(proof['evidence']+[T.record(T.__file__)])
        return proof
    # Existing transaction cannot change. Its synthetic code was not included
    # by inspect, unlike production: archive collector closes descriptor code.
    args=dict(contract={},activation={},transaction=t.tx,tracker=T.record(t.path),workbook='0001',
              original=T.read(T.record(t.path))['bindings']['original_raw_source'])
    monkeypatch.setattr(R.shutil,'disk_usage',lambda _:NS(free=100*1024**3))
    return t,args,root


def create(fixture):
    t,args,root=fixture
    return R.seal(**args,output=root/'parallel-policy/retirements/first')


def valid(fixture,binding):
    _,args,_=fixture
    return R.validate(binding,**{k:v for k,v in args.items() if k!='transaction'})


def test_real_terminal_and_released_chain_archived_without_tracker_mutation(released):
    t,_,_=released;before=t.path.read_bytes();b=create(released)
    assert valid(released,b)['terminal']=='STOPPED'
    assert t.path.read_bytes()==before
    s=R.read(b)
    for pair in s['copies']:
        assert pair['original']['sha256']==pair['retained']['sha256']
        assert os.stat(pair['original']['path']).st_ino!=os.stat(pair['retained']['path']).st_ino


def test_owning_current_failure_cannot_be_sealed(released,monkeypatch):
    _,args,root=released
    def reject(*a):raise ValueError('current review changed')
    monkeypatch.setattr(T,'_material',reject)
    with pytest.raises(ValueError,match='current review'):
        R.seal(**args,output=root/'parallel-policy/retirements/first')
    assert not (root/'parallel-policy/retirements/first').exists()


def test_old_claim_code_change_does_not_execute_archived_code(released,monkeypatch):
    b=create(released)
    monkeypatch.setattr(T,'_material',lambda *a:pytest.fail('historical owning code executed'))
    assert valid(released,b)['terminal']=='STOPPED'


@pytest.mark.parametrize('change',['archive','tracker','original','notes','seal','duplicate','foreign','extra','code_binding'])
def test_changed_material_rejects(released,change):
    t,args,root=released;b=create(released);s=R.read(b)
    if change=='archive':Path(s['copies'][0]['retained']['path']).write_bytes(b'changed')
    elif change=='tracker':t.path.write_bytes(t.path.read_bytes()+b' ')
    elif change=='original':Path(args['original']['path']).write_bytes(b'changed')
    elif change=='notes':Path(T.read(T.record(t.path))['agent_notes']['path']).write_bytes(b'changed')
    elif change=='seal':Path(b['path']).write_bytes(b'{}')
    else:
        if change=='duplicate':s['copies'].append(s['copies'][0])
        elif change=='foreign':s['lane']['workbook']='0042'
        elif change=='extra':s['approved']=True
        elif change=='code_binding':s['implementation']['sha256']='0'*64
        Path(b['path']).write_bytes(R.P.canonical(s));b=R.record(b['path'])
    with pytest.raises((ValueError,KeyError)):valid(released,b)


@pytest.mark.parametrize('point',['copy','validated'])
def test_interruption_never_emits_seal_or_changes_tracker(released,point):
    t,args,root=released;before=t.path.read_bytes();output=root/'parallel-policy/retirements/first'
    def crash(p):
        if p==point:raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError):R.seal(**args,output=output,_fault=crash)
    assert not (output/'seal.json').exists() and t.path.read_bytes()==before
    with pytest.raises(ValueError,match='retained retirement attempt'):
        R.seal(**args,output=output)


def test_no_remaining_lease_allowed(transaction):
    t=transaction;commit(t);value=T.read(T.record(t.path))
    with pytest.raises(ValueError,match='still holds'):
        R.inspect(transaction=t.tx,tracker=T.record(t.path),workbook='0001',
                  original=value['bindings']['original_raw_source'],tracker_root=t.path.parent)


def hold(path,ready,release):
    with open(path,'r+b') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ready.set();release.wait(15)


@pytest.mark.parametrize('name',['lane.lock','publication.lock'])
def test_real_process_contention_blocks_seal(released,name):
    _,args,root=released;ctx=multiprocessing.get_context('spawn');ready=ctx.Event();release=ctx.Event()
    process=ctx.Process(target=hold,args=(root/name,ready,release));process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(BlockingIOError):R.seal(**args,output=root/'parallel-policy/retirements/first')
        assert not (root/'parallel-policy/retirements/first').exists()
    finally:
        release.set();process.join(15)
        if process.is_alive():process.kill();process.join()
    assert process.exitcode==0


def test_archive_projection_rejects_even_rehashed_after(released):
    b=create(released);s=R.read(b);d=R.read(s['transaction'])
    pair=next(p for p in s['copies'] if p['original']==d['after'])
    # Changing a retained artifact cannot be hidden by updating its mapping:
    # the original terminal transaction and failure projection still bind it.
    dest=Path(pair['retained']['path']);v=R.S.strict_json(dest.read_bytes());v['repairs']['count']+=1
    raw=R.P.canonical(v);newhash=R.S.sha(raw);newpath=dest.with_name(newhash);newpath.write_bytes(raw)
    pair['retained']=R.record(newpath);pair['original']=dict(pair['original'],sha256=newhash)
    Path(b['path']).write_bytes(R.P.canonical(s));b=R.record(b['path'])
    with pytest.raises(ValueError):valid(released,b)


def test_duplicate_bytes_share_one_retained_file(released):
    b=create(released);s=R.read(b)
    hashes=[p['original']['sha256'] for p in s['copies']]
    assert len(set(hashes))<len(hashes)
    assert len(list((Path(b['path']).parent/'files').iterdir()))==len(set(hashes))


def test_low_disk_reserve_never_emits_seal(released,monkeypatch):
    _,args,root=released;monkeypatch.setattr(R.shutil,'disk_usage',lambda _:NS(free=8*1024**3))
    with pytest.raises(ValueError,match='reserve'):
        R.seal(**args,output=root/'parallel-policy/retirements/first')
    assert not (root/'parallel-policy/retirements/first/seal.json').exists()


def test_credential_record_never_copied(tmp_path):
    source=tmp_path/'private.env';source.write_bytes(b'synthetic secret')
    with pytest.raises(ValueError,match='credential'):
        R._copy(R.record(source),tmp_path/'retained')
    assert not (tmp_path/'retained').exists()


def test_native_corrupt_copy_rejects(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'exact synthetic original');target=tmp_path/'retained'
    monkeypatch.setattr(R.sys,'platform','darwin')
    monkeypatch.setattr(R.shutil,'disk_usage',lambda _:NS(free=100*1024**3))
    def corrupt(argv,**kw):target.write_bytes(b'wrong');return NS(returncode=0)
    monkeypatch.setattr(R.subprocess,'run',corrupt)
    with pytest.raises(ValueError,match='copy differs'):R._copy(R.record(source),target)
    assert source.read_bytes()==b'exact synthetic original'


def test_native_copy_failure_uses_exact_independent_fallback(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'exact synthetic original');target=tmp_path/'retained'
    monkeypatch.setattr(R.sys,'platform','darwin')
    monkeypatch.setattr(R.shutil,'disk_usage',lambda _:NS(free=100*1024**3))
    monkeypatch.setattr(R.subprocess,'run',lambda *a,**k:NS(returncode=1))
    result=R._copy(R.record(source),target)
    assert result['sha256']==R.record(source)['sha256'] and source.stat().st_ino!=target.stat().st_ino
