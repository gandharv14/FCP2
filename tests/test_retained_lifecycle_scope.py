from pathlib import Path
from types import ModuleType
import importlib.util
import sys
import pytest
from scripts.fleet import retained_lifecycle_scope as L


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def setup(tmp_path, monkeypatch):
    old, new = tmp_path/'old', tmp_path/'new'
    failure = ModuleType('scripts.batch.harbor_failure_snapshot')
    failure.FailureSnapshots = type('FailureSnapshots', (), {'preserved': 1})
    monkeypatch.setitem(sys.modules, failure.__name__, failure)
    for root in (old, new):
        d = root/'scripts/batch'; d.mkdir(parents=True)
        (d/'harbor_ready_transitions.py').write_text(
            'from pathlib import Path\nROOT=Path('+repr(str(root))+')\n'
            'class ReadyTransitions:\n def transition(self, passed):\n'
            '  if not passed:raise ValueError("quality failure")\n  return str(ROOT)\n')
        (d/'harbor_lane_lifecycle.py').write_text(
            'from scripts.batch.harbor_ready_transitions import ReadyTransitions\n'
            'from scripts.batch.harbor_failure_snapshot import FailureSnapshots\n'
            'class HarborLaneLifecycle(ReadyTransitions, FailureSnapshots):pass\n')
        (d/'harbor_instruction_worker.py').write_text('# selected worker\n')
    name = 'scripts.batch.harbor_ready_transitions'
    a = module(name, old/'scripts/batch/harbor_ready_transitions.py')
    monkeypatch.setitem(sys.modules, name, a)
    prior = module('old_facade', old/'scripts/batch/harbor_lane_lifecycle.py').HarborLaneLifecycle
    b = module(name, new/'scripts/batch/harbor_ready_transitions.py')
    monkeypatch.setitem(sys.modules, name, b)
    w = ModuleType('selected_worker'); w.ROOT = new
    w.__file__ = str(new/'scripts/batch/harbor_instruction_worker.py')
    w.HarborLaneLifecycle = prior
    return old, new, w, a, b


def test_retained_inheritance_reproduced_fixed_and_restored(setup):
    old, new, w, a, b = setup
    prior = w.HarborLaneLifecycle
    assert prior().transition(True) == str(old)
    with L.installed(w, original=old, selected=new):
        current = w.HarborLaneLifecycle
        assert current().transition(True) == str(new)
        assert current().preserved == 1
        with pytest.raises(ValueError, match='quality failure'):
            current().transition(False)
        with L.installed(w, original=old, selected=new):
            assert w.HarborLaneLifecycle is current
        assert w.HarborLaneLifecycle is current
        assert prior().transition(True) == str(old)
    assert w.HarborLaneLifecycle is prior


@pytest.mark.parametrize('defect', ['worker', 'facade', 'canonical', 'origin'])
def test_undeclared_changes_rejected(setup, defect):
    old, new, w, a, b = setup
    if defect == 'worker':w.ROOT = old
    elif defect == 'facade':(new/'scripts/batch/harbor_lane_lifecycle.py').write_text('class HarborLaneLifecycle:pass\n')
    elif defect == 'canonical':b.ROOT = old
    else:w.HarborLaneLifecycle = type('Foreign', (), {'transition': lambda self: None})
    with pytest.raises((ValueError, IndexError)):
        with L.installed(w, original=old, selected=new):pass


def adoption_selection(tmp_path, setup):
    import json
    from types import SimpleNamespace
    from scripts.fleet import disclosure_adoption_continue as S
    old, new, worker, _, _ = setup
    def keep(name, value):
        path=tmp_path/name;path.write_text(json.dumps(value));return S.binding(path)
    snapshot=keep('snapshot.json',dict(root=str(old)))
    config=dict(workbook='sample',code=snapshot)
    job=dict(config=keep('config.json',config))
    profile=dict(workbook='sample',lifecycle_scope=S.binding(Path(L.__file__).resolve()),
                 owning_profile=dict(worker=S.binding(worker.__file__)))
    return S,profile,(None,None,None,worker,None,config,old,None),job,SimpleNamespace(root=new)


def test_adoption_context_uses_real_scoped_facade_and_restores_on_error(tmp_path, setup):
    S,p,env,job,worker=adoption_selection(tmp_path,setup)
    prior=env[3].HarborLaneLifecycle
    with pytest.raises(RuntimeError,match='after current operation'):
        with S.lifecycle_runtime(p,env,job,worker):
            assert env[3].HarborLaneLifecycle().transition(True)==str(worker.root)
            assert prior().transition(True)==str(env[6])
            with pytest.raises(ValueError,match='quality failure'):
                env[3].HarborLaneLifecycle().transition(False)
            raise RuntimeError('after current operation')
    assert env[3].HarborLaneLifecycle is prior


@pytest.mark.parametrize('defect',['config','source_root','worker','helper'])
def test_adoption_facade_rejects_foreign_boundaries(tmp_path,setup,defect):
    S,p,env,job,worker=adoption_selection(tmp_path,setup)
    if defect=='config':p['workbook']='foreign'
    elif defect=='source_root':env=(*env[:6],tmp_path/'foreign',env[7])
    elif defect=='worker':p['owning_profile']['worker']=job['config']
    else:p['lifecycle_scope']=dict(p['lifecycle_scope'],sha256='0'*64)
    prior=env[3].HarborLaneLifecycle
    with pytest.raises(ValueError):
        with S.lifecycle_runtime(p,env,job,worker):pytest.fail('foreign facade admitted')
    assert env[3].HarborLaneLifecycle is prior


def test_adoption_default_does_not_select_a_new_facade():
    from scripts.fleet import disclosure_adoption_continue as S
    with S.lifecycle_runtime({},None,None,None):pass
