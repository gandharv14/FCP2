"""Select runtime stdlib modules before a legacy dispatcher changes sys.path.

Call during a fresh process bootstrap. This is a prospective import helper,
not permission to replace an already imported module or alter frozen code.
"""
from pathlib import Path
import importlib.util
import sys
import sysconfig


def preload_inspect():
    """Load this interpreter's inspect.py without searching a task directory."""
    expected = (Path(sysconfig.get_path("stdlib")) / "inspect.py").resolve(strict=True)
    cached = sys.modules.get("inspect")
    if cached is not None:
        origin = getattr(getattr(cached, "__spec__", None), "origin", None)
        filename = getattr(cached, "__file__", None)
        if (not isinstance(origin, str) or not isinstance(filename, str)
                or Path(origin).resolve() != expected or Path(filename).resolve() != expected
                or not callable(getattr(cached, "isgenerator", None))):
            raise ValueError("foreign cached inspect module; restart with clean bootstrap")
        return cached
    spec = importlib.util.spec_from_file_location("inspect", expected)
    if spec is None or spec.loader is None:
        raise ImportError("runtime standard-library inspect loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules["inspect"] = module
    try:
        spec.loader.exec_module(module)
        if not callable(getattr(module, "isgenerator", None)):
            raise ImportError("runtime standard-library inspect is incomplete")
    except BaseException:
        if sys.modules.get("inspect") is module:
            del sys.modules["inspect"]
        raise
    return module
