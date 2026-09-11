"""Exact reviewed historical modules for closed-profile compatibility tests.

These tests describe the archived c5→a0 contract, not arbitrary future source.
Reconstruct its pinned bytes from committed archives and reviewed literal edits;
never replace a production file or relax the runtime profile allowlist.
"""
from pathlib import Path
from types import FunctionType
import pytest
from scripts.batch import harbor_partition_code_applicability as A
from scripts.batch import harbor_partition_transport_applicability as T
from scripts.batch import harbor_model_executor as E
from scripts.batch import harbor_normalization_partitioned as P


def restored(raw,edits,expected):
    for before,after in edits:
        assert raw.count(before)==1
        raw=raw.replace(before,after,1)
    assert E.sha(raw)==expected
    return raw


def install(module,path,raw,monkeypatch):
    path.write_bytes(raw)
    namespace={'__name__':module.__name__,'__file__':str(path),'__package__':module.__package__}
    exec(compile(raw,str(path),'exec'),namespace)
    for name,value in namespace.items():
        if isinstance(value,FunctionType) and value.__globals__ is namespace:
            rebound=FunctionType(value.__code__,module.__dict__,value.__name__,value.__defaults__,value.__closure__)
            rebound.__kwdefaults__=value.__kwdefaults__;rebound.__annotations__=value.__annotations__
            value=rebound
        if not name.startswith('__') or name=='__file__':monkeypatch.setattr(module,name,value,raising=False)


@pytest.fixture(autouse=True)
def historical_profile_modules(tmp_path,monkeypatch):
    """Restore module objects after each test, including shared imported aliases."""
    executor=restored(T.archives()[0].read_bytes(),T.EDITS,T.NEW_E)
    producer=restored(A.archives()[1].read_bytes(),A.EDITS,T.OLD_P)
    install(E,tmp_path/'reviewed_executor.py',executor,monkeypatch)
    install(P,tmp_path/'reviewed_partition.py',producer,monkeypatch)
    yield
