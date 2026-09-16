import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from scripts.fleet import publication_finalization as F

ROOT=Path(__file__).resolve().parents[1]
VERIFIER=ROOT/'diagnostics/authoring-resume-20260910/runner/verify_published.py'
INVENTORY=ROOT/'scripts/fleet/snapshot_inventory.py'
def binding(p):return dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
def checked(row):
 assert binding(row['path'])==row
 return json.loads(Path(row['path']).read_bytes())
def keep(p,value):
 p.write_text(json.dumps(value));return binding(p)

def test_original_verifier_main_reaches_qualification_boundary_only_for_lossless_order(tmp_path,monkeypatch):
 actual=dict(root='/exact/root',files=[dict(relative='lineage/item',sha256='a'*64),dict(relative='lineage.json',sha256='b'*64)])
 selected={**actual,'files':list(reversed(actual['files']))}
 snapshot=keep(tmp_path/'snapshot.json',selected)
 config=keep(tmp_path/'config.json',dict(code=snapshot))
 seen=[];loaded=[]
 def original_snapshot(root):assert str(root)==actual['root'];return actual
 def imports(root):seen.append(str(root));raise LookupError('remaining original verifier reached')
 monkeypatch.setitem(sys.modules,'author_workbook',SimpleNamespace(checked=checked,binding=binding,imports=imports,code_snapshot=original_snapshot,retain=lambda *a:None))
 def load(name,row):
  assert binding(row['path'])==row
  spec=importlib.util.spec_from_file_location(name,row['path']);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);loaded.append(m);return m
 pub=SimpleNamespace(load=load,checked=checked)
 D=SimpleNamespace(VERIFIER=binding(VERIFIER));argv=sys.argv
 with pytest.raises(LookupError,match='remaining original'):
  F.independent_verify(pub,D,config,tmp_path,binding(INVENTORY))
 assert seen==['/exact/root'] and sys.argv is argv
 assert loaded[-1].code_snapshot is original_snapshot
 # A byte change reaches the original immutable-code rejection, never imports.
 actual['files'][0]['sha256']='c'*64;seen.clear()
 with pytest.raises(ValueError,match='Selected immutable code changed'):
  F.independent_verify(pub,D,config,tmp_path,binding(INVENTORY))
 assert not seen and sys.argv is argv and loaded[-1].code_snapshot is original_snapshot


def test_verification_entrypoint_never_calls_owning_finalizer():
 import ast
 tree=ast.parse(Path(F.__file__).read_text())
 f=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='verification_export')
 forbidden={'finalize','run','execute','controlled','renew','claim','begin'}
 assert not [x for x in ast.walk(f) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr in forbidden]
 assert 'verification_continuation' in Path(F.__file__).read_text()
