import sys, importlib.util, types
from pathlib import Path
import pytest
from scripts.fleet import retained_ready_root as F, retained_lifecycle_scope as L

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture
def case(tmp_path,monkeypatch):
    old,new=tmp_path/'old',tmp_path/'new'
    failure=types.ModuleType('scripts.batch.harbor_failure_snapshot');failure.FailureSnapshots=type('FailureSnapshots',(),{'preserved':1})
    monkeypatch.setitem(sys.modules,failure.__name__,failure)
    text='from pathlib import Path\nROOT=Path(__file__).resolve().parents[2]\ndef _proof(passed):\n if not passed:raise ValueError("quality failure")\n return str(ROOT)\ndef _proof_current(passed):return _proof(passed)\nclass ReadyTransitions:\n def transition(self,passed):return _proof_current(passed)\n'
    for root in(old,new):
        d=root/'scripts/batch';d.mkdir(parents=True)
        (d/'harbor_ready_transitions.py').write_text(text)
        (d/'harbor_lane_lifecycle.py').write_text('from scripts.batch.harbor_ready_transitions import ReadyTransitions\nfrom scripts.batch.harbor_failure_snapshot import FailureSnapshots\nclass HarborLaneLifecycle(ReadyTransitions,FailureSnapshots):pass\n')
        (d/'harbor_instruction_worker.py').write_text('# worker\n')
    name='scripts.batch.harbor_ready_transitions';prior=module(name,old/'scripts/batch/harbor_ready_transitions.py');monkeypatch.setitem(sys.modules,name,prior)
    W=types.ModuleType('worker');W.ROOT=new;W.__file__=str(new/'scripts/batch/harbor_instruction_worker.py');W.HarborLaneLifecycle=module('old_facade',old/'scripts/batch/harbor_lane_lifecycle.py').HarborLaneLifecycle
    return prior,W,old,new

@pytest.mark.parametrize('fail',[False,True])
def test_full_facade_rebinds_unchanged_checks_and_restores(case,fail):
    prior,W,old,new=case;facade=W.HarborLaneLifecycle
    assert facade().transition(True)==str(old)
    try:
        with F.installed(W,new,L) as current:
            assert current is not prior and current._proof(True)==str(new)
            assert W.HarborLaneLifecycle().transition(True)==str(new)
            assert W.HarborLaneLifecycle().preserved==1
            assert facade().transition(True)==str(old)
            with pytest.raises(ValueError,match='quality failure'):W.HarborLaneLifecycle().transition(False)
            if fail:raise RuntimeError('later failure')
    except RuntimeError:assert fail
    assert sys.modules[prior.__name__] is prior and W.HarborLaneLifecycle is facade

@pytest.mark.parametrize('change',['code','worker','facade','load'])
def test_foreign_changes_rejected_and_module_restored(case,change):
    prior,W,old,new=case;facade=W.HarborLaneLifecycle
    if change=='code':(new/'scripts/batch/harbor_ready_transitions.py').write_text('changed')
    elif change=='worker':W.ROOT=old
    elif change=='facade':(new/'scripts/batch/harbor_lane_lifecycle.py').write_text('changed')
    else:
        for root in(old,new):(root/'scripts/batch/harbor_ready_transitions.py').write_text('raise ValueError("load failed")')
    with pytest.raises(ValueError):
        with F.installed(W,new,L):pytest.fail('foreign root admitted')
    assert sys.modules[prior.__name__] is prior and W.HarborLaneLifecycle is facade
