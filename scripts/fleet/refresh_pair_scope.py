"""Select the original pair cache for one original refresh prefix traversal."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
from types import CodeType


def require(ok, message):
    if not ok:
        raise ValueError(message)


def checked(row):
    path = Path(row['path'])
    require(set(row) == {'path', 'sha256'} and path.is_absolute()
            and not any(p.is_symlink() for p in (path, *path.parents))
            and hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'],
            'refresh pair scope code binding changed')
    return path


def original_function(fn, module, path):
    code = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)
    matches = [part for part in code.co_consts
               if isinstance(part, CodeType) and part.co_name == fn.__name__]
    return (len(matches) == 1 and fn.__code__ == matches[0]
            and fn.__globals__ is vars(module)
            and fn.__code__.co_filename == str(path))


@contextmanager
def installed(F, *, refresh, lanes):
    """Keep every original gate; reuse its authenticated pair proof per call.

    This scope never spans separate ``checks`` invocations or persists a verdict.
    The original cache rechecks evidence and generation membership on every hit.
    """
    refresh_path, lanes_path = checked(refresh), checked(lanes)
    M = F.W.lanes
    require(refresh_path.name == 'harbor_environment_refresh.py'
            and lanes_path.name == 'harbor_completed_lanes.py'
            and Path(F.__file__) == refresh_path and Path(M.__file__) == lanes_path,
            'foreign refresh pair scope modules')
    original = F.checks
    scope = M.pair_validation_scope
    functions = (original, scope.__wrapped__, M.pair_and_audit,
                 M._pair_and_audit_uncached)
    require(original.__name__ == 'checks'
            and original_function(original, F, refresh_path)
            and all(original_function(fn, M, lanes_path)
                    for fn in functions[1:]),
            'refresh pair scope callbacks are not original module functions')

    def selected(*args, **kwargs):
        checked(refresh)
        checked(lanes)
        require(F.W.lanes is M and M.pair_validation_scope is scope
                and (M.pair_and_audit, M._pair_and_audit_uncached) == functions[2:],
                'refresh pair scope callbacks changed')
        with scope():
            return original(*args, **kwargs)

    F.checks = selected
    try:
        yield
    finally:
        F.checks = original
