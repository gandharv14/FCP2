import importlib.util
import sys
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from scripts.fleet import mechanical_checkpoint_takeover as M,completed_checkpoint_takeover as B
from test_completed_checkpoint_takeover import SOURCE,Lease


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m


def test_preserved_wrapper_keeps_original_checks_and_restores_on_error(tmp_path):
    base=tmp_path/'worker.py';base.write_text('from dataclasses import asdict\nfrom types import SimpleNamespace\ncodec=SimpleNamespace(encode=lambda x:x)\ndef require(ok,message):\n if not ok:raise ValueError(message)\n'+SOURCE)
    W=load('test_original_mechanical_step',base);W._step=W.step
    core=tmp_path/'core.py';core.write_text('''import ast,inspect,textwrap
from contextlib import contextmanager
@contextmanager
def instruction_scope(W):
 old_step=W._step
 tree=ast.parse(textwrap.dedent(inspect.getsource(old_step)))
 class Change(ast.NodeTransformer):
  pass
 tree=Change().visit(tree)
 scope=dict(old_step.__globals__)
 exec(compile(ast.fix_missing_locations(tree),old_step.__code__.co_filename,'exec'),scope)
 def step(*args,**kwargs):
  W.wrapper_calls+=1
  return scope[old_step.__name__](*args,**kwargs)
 W._step=step
 try:yield
 finally:W._step=old_step
''');K=load('test_preserved_mechanical_core',core)
    api=SimpleNamespace(binding=B.binding,require=B.require,module=lambda n,r:K)
    old=Lease('old','one',1,'a');new=Lease('new','two',2,'b');calls=[];worker=SimpleNamespace(checkpoint='exact',operation=lambda *a:calls.append(a));prior=dict(selection='source',lease=asdict(old));W.wrapper_calls=0
    with K.instruction_scope(W):
        outer=W._step
        with pytest.raises(RuntimeError):
            with M.preserved_step_scope(api,B,W,lambda *a:True,B.binding(core)):
                W._step(worker,'source',new,'exact',prior)
                with pytest.raises(ValueError,match='original checkpoint'):W._step(worker,'source',new,'foreign',prior)
                raise RuntimeError('caller failed')
        assert W._step is outer
        with pytest.raises(ValueError,match='previous worker checkpoint'):W._step(worker,'source',new,'exact',prior)
    assert calls==[('source',new,'exact')]and W.wrapper_calls==3
