"""Select the unchanged readiness facade under the authenticated worker root."""
from pathlib import Path
from contextlib import contextmanager
import importlib,importlib.util,sys


def require(ok,message):
    if not ok:raise ValueError(message)


@contextmanager
def installed(W,root,lifecycle):
    name='scripts.batch.harbor_ready_transitions';prior=importlib.import_module(name)
    root=Path(root);original=Path(W.HarborLaneLifecycle.transition.__code__.co_filename).resolve().parents[2]
    paths=[p/'scripts/batch/harbor_ready_transitions.py'for p in(original,root)]
    require(root.is_absolute()and W.ROOT==root
        and Path(W.__file__).resolve()==root/'scripts/batch/harbor_instruction_worker.py'
        and all(p.is_file()and not any(q.is_symlink()for q in(p,*p.parents))for p in paths)
        and paths[0].read_bytes()==paths[1].read_bytes(),
        'selected readiness root changed implementation bytes')
    package=importlib.import_module('scripts.batch');previous_attribute=getattr(package,'harbor_ready_transitions',prior)
    spec=importlib.util.spec_from_file_location(name,paths[1]);selected=importlib.util.module_from_spec(spec)
    sys.modules[name]=selected;setattr(package,'harbor_ready_transitions',selected)
    try:
        spec.loader.exec_module(selected)
        require(selected.ROOT==root and selected._proof.__globals__ is selected.__dict__
            and selected._proof_current.__globals__ is selected.__dict__
            and selected.ReadyTransitions.transition.__globals__ is selected.__dict__,
            'selected readiness facade has foreign dependencies')
        with lifecycle.installed(W,original=original,selected=root):
            require(W.HarborLaneLifecycle.transition is selected.ReadyTransitions.transition,
                'worker still uses historical readiness facade')
            yield selected
    finally:
        sys.modules[name]=prior;setattr(package,'harbor_ready_transitions',previous_attribute)
