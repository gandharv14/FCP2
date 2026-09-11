from pathlib import Path
import copy,fcntl,importlib.util,json,multiprocessing,os,queue,time
import pytest
FILE=Path(__file__).resolve().parents[1]/'scripts/harbor_parallel_policy.py'
spec=importlib.util.spec_from_file_location('parallel_policy',FILE);P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)


def held(binding,activation,workbook,ready,release):
    try:
        with P.lane_lock(binding,activation,workbook) as fd:
            ready.put(('held',workbook,os.fstat(fd).st_ino));release.wait(10)
    except BaseException as e:ready.put(('error',workbook,type(e).__name__))


@pytest.fixture
def prepared(tmp_path):
    root=tmp_path/'campaign';root.mkdir()
    for n in ['parallel-policy','parallel-workbook-locks','model-journal','model-slots']:(root/n).mkdir()
    (root/'actual-workbook-slot.lock').touch();(root/'model-slots/capacity').write_bytes(b'1\n')
    binding=P.put(root/'parallel-policy/selected.json',P.proposal(root))
    return root,binding


def test_activation_and_existing_controls(prepared):
    root,b=prepared;a=P.activate(b);policy=P.validate(b,a)
    assert policy['model_capacity']==1 and len(policy['lanes'])==4
    assert P.validate(b,a,'0042')==root/'parallel-workbook-locks/0042.lock'
    c=P.controls(b,a,'0042');assert c.slot_lock==P.validate(b,a,'0042')
    assert policy['publication_queue_lock'] not in [str(c.slot_lock),policy['singleton_lock']]
    with pytest.raises(ValueError,match='already activated'):P.activate(b)
    with pytest.raises(ValueError,match='outside fixed roster'):P.validate(b,a,'0066')


def test_four_distinct_lanes_overlap_and_same_workbook_is_excluded(prepared):
    root,b=prepared;a=P.activate(b);ctx=multiprocessing.get_context('spawn');ready=ctx.Queue();release=ctx.Event()
    workers=[ctx.Process(target=held,args=(b,a,wb,ready,release)) for wb in P.WORKBOOKS]
    duplicate=None
    try:
        for w in workers:w.start()
        entries=[ready.get(timeout=15) for _ in workers]
        assert {x[1] for x in entries}==set(P.WORKBOOKS) and all(x[0]=='held' for x in entries)
        assert len({x[2] for x in entries})==4
        duplicate=ctx.Process(target=held,args=(b,a,'0042',ready,release));duplicate.start()
        assert ready.get(timeout=15)==('error','0042','BlockingIOError')
    finally:
        release.set()
        for w in workers+([duplicate] if duplicate else []):
            w.join(timeout=15)
            if w.is_alive():w.kill();w.join()
    assert all(w.exitcode==0 for w in workers)


@pytest.mark.parametrize('field',['duplicate_workbook','unknown_workbook','duplicate_lock','model_capacity','model_root','publication_lock','implementation','extra','float_timeout'])
def test_malformed_or_shifted_contract_rejects(prepared,field):
    root,b=prepared;p=P.read(b)
    if field=='duplicate_workbook':p['lanes'][1]['workbook']='0035'
    elif field=='unknown_workbook':p['lanes'][1]['workbook']='0066'
    elif field=='duplicate_lock':p['lanes'][1]['lock']=p['lanes'][0]['lock']
    elif field=='model_capacity':p['model_capacity']=2
    elif field=='model_root':p['model_slot_root']=str(root/'other')
    elif field=='publication_lock':p['publication_queue_lock']=p['singleton_lock']
    elif field=='implementation':p['implementation']['sha256']='0'*64
    elif field=='extra':p['allow_fifth_workbook']=True
    else:p['timeout_seconds']=900.0
    changed=P.put(root/'parallel-policy/bad.json',p)
    with pytest.raises(ValueError):P.activate(changed)
    assert not (root/'parallel-policy/activation.json').exists()


def test_old_global_must_be_quiescent(prepared):
    root,b=prepared
    with (root/'actual-workbook-slot.lock').open('r+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):P.activate(b)
    assert not (root/'parallel-policy/activation.json').exists()


def test_existing_lane_cannot_be_stolen_at_activation(prepared):
    root,b=prepared;path=root/'parallel-workbook-locks/0035.lock';path.touch()
    with path.open('r+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):P.activate(b)
    assert not (root/'parallel-policy/activation.json').exists()


@pytest.mark.parametrize('which',['contract','activation','model-capacity','inode','symlink','hardlink'])
def test_changed_activated_evidence_rejects(prepared,which):
    root,b=prepared
    if which=='hardlink':
        os.link(root/'actual-workbook-slot.lock',root/'parallel-workbook-locks/0042.lock')
        with pytest.raises((ValueError,BlockingIOError)):P.activate(b)
        return
    a=P.activate(b)
    if which=='contract':Path(b['path']).write_bytes(b'{}')
    elif which=='activation':Path(a['path']).write_bytes(b'{}')
    elif which=='model-capacity':(root/'model-slots/capacity').write_bytes(b'2\n')
    else:
        lock=root/'parallel-workbook-locks/0042.lock';lock.rename(lock.with_suffix('.retained'))
        if which=='inode':lock.touch()
        else:lock.symlink_to(lock.with_suffix('.retained'))
    with pytest.raises((ValueError,OSError)):P.validate(b,a,'0042')


def test_foreign_activation_or_contract_root_rejects(prepared,tmp_path):
    root,b=prepared;a=P.activate(b);value=P.read(a);value['contract']['sha256']='0'*64
    wrong=P.put(root/'parallel-policy/other-activation.json',value)
    with pytest.raises(ValueError):P.validate(b,wrong)
    outside=P.put(tmp_path/'elsewhere.json',P.proposal(root))
    with pytest.raises(ValueError):P.contract(outside)
