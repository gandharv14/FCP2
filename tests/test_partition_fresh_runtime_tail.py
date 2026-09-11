from pathlib import Path
from types import SimpleNamespace
import importlib.util,json,ast
import pytest
PATH=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/continue_0184_after_fresh_runtime_v1.py'
@pytest.mark.parametrize('fault',[None,'status','phase','foreign_initial'])
def test_first_renewal_uses_only_exact_completed_runtime(tmp_path,monkeypatch,fault):
    spec=importlib.util.spec_from_file_location('fresh_runtime_tail',PATH);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    value=dict(status='RETRY_READY' if fault=='status' else 'STEP_COMPLETE',next_step='dialogue-review' if fault=='phase' else 'dialogue-commit',descriptor={},checkpoint={'completed':'runtime'})
    p=tmp_path/'initial.json';p.write_text(json.dumps(value));initial=d.binding(p)
    monkeypatch.setattr(d,'INITIAL',{} if fault=='foreign_initial' else initial)
    worker=object();selection=object();lease=object();calls=[]
    monkeypatch.setattr(d,'selected',lambda *a:(worker,'recipe'))
    C=SimpleNamespace(_prior=lambda *a:(value,selection,lease));W=SimpleNamespace(active=lambda *a:calls.append(a))
    env=(None,None,None,C,W,None,None,None,None,None)
    if fault is None:
        result=d.held_context(env,initial);assert result==(value,worker,'recipe',selection,lease) and calls==[(worker,selection,lease)]
    else:
        with pytest.raises(AssertionError):d.held_context(env,initial)
        assert not calls

def test_tail_has_no_writer_replay_entrypoint():
    tree=ast.parse(PATH.read_text());names={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}
    assert not names.intersection({'consume_initial','initial_child','initial_consume'})
    run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
    assert 'checkpoint = renew(env, INITIAL, 0, code)' in ast.unparse(run)
