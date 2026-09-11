"""Historical authority uses immutable bytes, never execution of old modules."""
import importlib.util,json,sys
from dataclasses import replace
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'diagnostics/requested-fixes-20260910'

@pytest.fixture
def actual(monkeypatch):
    import scripts.batch as package
    modules={}
    for name in ('harbor_instruction_local_restart','harbor_instruction_local_command'):
        path=BASE/'0184-partition-history-overlay/scripts/batch'/f'{name}.py'
        spec=importlib.util.spec_from_file_location('scripts.batch.'+name,path)
        m=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,m);monkeypatch.setattr(package,name,m,raising=False);spec.loader.exec_module(m);modules[name]=m
    M=modules['harbor_instruction_local_command'];R=modules['harbor_instruction_local_restart']
    from scripts import harbor_instruction_campaign as C
    path=BASE/'0184-local-command-authorization/descriptor.json'
    if not path.exists():pytest.skip('actual local authority unavailable')
    d=M.read(M.record(path));worker=C.worker_decode(d['worker'])
    tracker=M.read(M.record(worker.context.tracker_root/'0184.json'))
    snapshot=M.record(BASE/'0184-local-command-snapshot-v3.json')
    return M,R,worker,tracker,snapshot

def test_actual_historical_command_accepts_relocated_current_reader(actual):
    M,R,w,b,s=actual
    p=M.validate_historical(replace(w,root=ROOT),b,s)
    assert p['root']!=str(ROOT) and p['maximum_continuations']==1
    assert len(p['completed'])==15 and p['failed']

@pytest.mark.parametrize('change',['root','inventory','implementation','initial','old_steps','source','restart','registry'])
def test_historical_command_rejects_detached_evidence(actual,monkeypatch,change):
    M,R,w,b,s=actual;p=dict(M._bound(w))
    if change=='root':p['root']='/unrelated'
    elif change=='inventory':p['current_files']={}
    elif change=='implementation':p['implementation']={'path':'/wrong','sha256':'0'*64}
    elif change=='initial':p['initial']={'path':'/wrong','sha256':'0'*64}
    elif change=='old_steps':p['old_step_files']={}
    elif change=='source':p['original']={'path':'/wrong','sha256':'0'*64}
    elif change=='restart':p['restart']={'path':'/wrong','sha256':'0'*64}
    else:monkeypatch.setattr(R,'registry',lambda *a:Path('/missing-registry'))
    monkeypatch.setattr(M,'_bound',lambda *a:p)
    with pytest.raises((ValueError,AssertionError,OSError,RuntimeError)):
        M.validate_historical(w,b,s)

def test_new_root_cannot_dispatch_without_explicit_partition_authority(actual):
    M,R,w,b,s=actual
    with pytest.raises((ImportError,ValueError,OSError,RuntimeError)):
        M.validate(replace(w,root=ROOT),b)

def test_valid_partition_callback_keeps_original_command_proof(actual,monkeypatch):
    M,R,w,b,s=actual
    import scripts.batch as package
    from types import SimpleNamespace
    current=replace(w,root=ROOT);old=M._bound(w)
    binding=M.read(M.record(M.marker(w)))['permit']
    successor=dict(old_snapshot=s,local_command=binding,current_files={'new':'code'})
    calls=[]
    def callback(worker,p):
        assert worker==current and p==old;calls.append(True);return successor
    module=SimpleNamespace(validate_code_successor=callback)
    monkeypatch.setitem(sys.modules,'scripts.batch.harbor_disclosure_partition_continuation',module)
    monkeypatch.setattr(package,'harbor_disclosure_partition_continuation',module,raising=False)
    assert M.validate(current,b)==old and calls==[True]
    d=dict(worker=dict(campaign=old['campaign'],context=old['context']))
    assert M.validate_restart_successor(current,d,M.read(old['restart']))==successor


def test_historical_validation_does_not_execute_old_code(actual,monkeypatch):
    M,R,w,b,s=actual
    def forbidden(*a,**k):raise AssertionError('historical module execution attempted')
    monkeypatch.setattr(importlib.util,'spec_from_file_location',forbidden)
    assert M.validate_historical(replace(w,root=ROOT),b,s)['maximum_continuations']==1
