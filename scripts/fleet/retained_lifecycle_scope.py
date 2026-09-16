"""Select the current lifecycle facade after an authenticated code continuation.

Replacing a module does not change a class's existing base classes. Re-import
the unchanged facade under the selected transition module, for this worker only.
The original historical facade and every original file remain unchanged.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import importlib.util
import sys


def binding(path):
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('canonical lifecycle dependency required')
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


@contextmanager
def installed(worker, *, original, selected):
    """Use roots already selected by the calling continuation's code proof."""
    original, selected = Path(original), Path(selected)
    if worker.ROOT != selected or Path(worker.__file__) != selected/'scripts/batch/harbor_instruction_worker.py':
        raise ValueError('current selected instruction worker required')
    paths = [root/'scripts/batch'/name for root in (original, selected)
             for name in ('harbor_lane_lifecycle.py', 'harbor_ready_transitions.py')]
    records = [binding(p) for p in paths]
    if records[0]['sha256'] != records[2]['sha256']:
        raise ValueError('facade source must be byte-identical')
    name = 'scripts.batch.harbor_ready_transitions'
    transitions = sys.modules[name]
    if binding(transitions.__file__) != records[3] or transitions.ROOT != selected:
        raise ValueError('canonical selected transition validator required')
    prior = worker.HarborLaneLifecycle
    if prior.__bases__[0] is transitions.ReadyTransitions:
        yield records
        return
    old_method = prior.transition
    if binding(old_method.__code__.co_filename) != records[1]:
        raise ValueError('retained facade origin differs')
    namespace = '_retained_current_lifecycle_' + records[3]['sha256']
    spec = importlib.util.spec_from_file_location(namespace, paths[2])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    current = module.HarborLaneLifecycle
    if (current.__bases__ != (transitions.ReadyTransitions, prior.__bases__[1])
            or set(current.__dict__) != set(prior.__dict__)
            or current.transition is not transitions.ReadyTransitions.transition):
        raise ValueError('facade changes more than selected transition inheritance')
    worker.HarborLaneLifecycle = current
    try:
        yield records
    finally:
        worker.HarborLaneLifecycle = prior
