from pathlib import Path
from types import SimpleNamespace
import hashlib,inspect
import pytest
from scripts.batch import harbor_allowance_takeover as A

def binding(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def fixture(tmp_path):
 p=tmp_path/'retained.py';p.write_text("sentinel='original'\ndef _layout(selection):\n    return sentinel, selection\ndef replace_expired():\n    raise AssertionError('unselected old owning function')\n")
 module=A.module('fixture_layout',binding(p));row=dict(module=binding(p),function_sha256=hashlib.sha256(inspect.getsource(module._layout).encode()).hexdigest());return p,module,row

def test_only_layout_code_is_selected_and_new_runtime_globals_are_retained(tmp_path):
 p,old,row=fixture(tmp_path);owning=object();T=SimpleNamespace(sentinel='selected',replace_expired=owning,HISTORICAL_IMPLEMENTATIONS={'retained'})
 A.select_layout(T,row)
 assert T._layout('s')==('selected','s')and old._layout('s')==('original','s')
 assert T.replace_expired is owning and T.HISTORICAL_IMPLEMENTATIONS=={'retained',row['module']['sha256']}

@pytest.mark.parametrize('change',['file','function','field'])
def test_layout_witness_rejects_changed_or_unreviewed_selection(tmp_path,change):
 p,old,row=fixture(tmp_path)
 if change=='file':p.write_text(p.read_text()+'\nchanged=True\n')
 if change=='function':row['function_sha256']='0'*64
 if change=='field':row['owning']='replace_expired'
 with pytest.raises(ValueError):A.select_layout(SimpleNamespace(HISTORICAL_IMPLEMENTATIONS=set()),row)
