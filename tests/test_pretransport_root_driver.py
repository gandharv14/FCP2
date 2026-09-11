from pathlib import Path
import importlib.util,sys,fcntl,os
from contextlib import contextmanager
import pytest
from scripts import harbor_pretransport_recovery
ROOT=Path(__file__).resolve().parents[1]
# Root driver is separate from installed batch-code closures; fixtures execute
# only the simulated guard, never a real workbook/container/model.
fixture_dir=os.environ.get('HARBOR_PRETRANSPORT_DRIVER_FIXTURE')
if not fixture_dir:
    pytest.skip('Configure HARBOR_PRETRANSPORT_DRIVER_FIXTURE for private root-driver integration tests',allow_module_level=True)
HERE=Path(fixture_dir).resolve()
if not (HERE/'driver.py').is_file() or not (HERE/'recovery0042.py').is_file():
    raise ValueError('Configured pretransport driver fixture is incomplete')

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
sys.path.insert(0,str(HERE))
G=load('recovery0042',HERE/'recovery0042.py');D=load('pretransport_root_driver',HERE/'driver.py')
O=load('original_guard_fixture',HERE.parent/'0066-dialogue-continuation-driver/test_driver.py');O.D=D
old_case=O.case
@pytest.fixture
def case(old_case,monkeypatch):
    p,saved,s,w,l,plan,lease,calls,guards=old_case
    p.update(pretransport_exception={'path':'exception','sha256':'a'*64},code_review={'path':'review','sha256':'b'*64},parallel={'contract':{},'activation':{}})
    @contextmanager
    def lane(c,a,wb):
        assert wb=='0042'
        with (Path(p['campaign_root'])/'actual-workbook-slot.lock').open('r+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);yield lock.fileno()
    monkeypatch.setattr(D.P,'lane_lock',lane);monkeypatch.setattr(G,'validate_effective',lambda _:None)
    return old_case
for name in dir(O):
    if name.startswith('test_') and name not in {'test_authorization_needs_exact_fresh_runtime_then_owning_CAS','test_remaining_model_step_forwards_only_selected_capabilities'}:globals()[name]=getattr(O,name)

def test_recovery_authorize_forwards_only_exact_failed_exception_review_and_fresh_runtime(case):
    p,saved,s,w,l,plan,lease,calls,guards=case
    D.run('authorize',plan,lease,Path(p['root_run'])/'00',previous={'path':'current','sha256':'e'*64})
    assert [x[0] for x in calls]==['validate-runtime','authorize']
    assert calls[1][2]==dict(exception=p['pretransport_exception'],review=p['code_review'])

def test_same_prepared_remaining_writer_is_only_selected_effect(case,monkeypatch):
    p,saved,s,w,l,plan,lease,calls,guards=case;saved['status']='PRETRANSPORT_RECOVERED'
    import xl_task_build
    monkeypatch.setattr(xl_task_build,'read_env_key',lambda _: 'fixture')
    out=D.run('step',plan,lease,Path(p['root_run'])/'00',previous={'path':'permit','sha256':'e'*64},models=True)
    assert D.read(out)['status']=='COMPLETED_OPERATION' and len(calls)==1
    assert calls[0][2]['phase']=='dialogue-writer' and calls[0][2]['checkpoint']==saved['evidence']

def test_driver_does_not_reauthorize_a_semantic_round():
    source=(HERE/'driver.py').read_text()
    assert "import_module('scripts.harbor_pretransport_recovery')" in source
    assert 'scope_evidence=' not in source and 'blocked=' not in source
    assert 'guardian_lock_fds=(fd,)' in source and 'pretransport_recovery=' in source
