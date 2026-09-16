from pathlib import Path
import importlib.util,json,subprocess,sys
import pytest
from scripts.fleet import retained_initial_continue as R
from scripts.fleet import undispatched_campaign as A

def original(tmp_path,body='return dispatcher.operation_namespace(config,value)'):
 p=tmp_path/'original.py';p.write_text('def load(config,value):\n '+body+'\n')
 s=importlib.util.spec_from_file_location('initial_test_original',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 class Dispatcher:
  @staticmethod
  def operation_namespace(config,value):
   p=A.selected_operation_namespace(config,value)
   if p.exists():raise ValueError('dispatcher namespace exists; no replay')
   return p
 m.dispatcher=Dispatcher;return m

def test_existing_cold_namespace_keeps_original_fresh_gate(tmp_path):
 m=original(tmp_path);config={'run_root':str(tmp_path)};p=tmp_path/'dispatcher-v2/selected';p.mkdir(parents=True)
 with pytest.raises(ValueError,match='no replay'):m.load(config,str(p))
 chosen=R.location_loader(m.load,R.source_hash(m.load),A.selected_operation_namespace)
 assert chosen(config,str(p))==p
 with pytest.raises(ValueError,match='no replay'):m.load(config,str(p))
 assert not (p/'intent.json').exists()

@pytest.mark.parametrize('body',['return 0','return dispatcher.operation_namespace(config,value),dispatcher.operation_namespace(config,value)'])
def test_only_exact_one_namespace_call(tmp_path,body):
 m=original(tmp_path,body)
 with pytest.raises(ValueError,match='one original'):R.location_loader(m.load,R.source_hash(m.load),A.selected_operation_namespace)

def test_changed_loader_witness_rejected(tmp_path):
 m=original(tmp_path)
 with pytest.raises(ValueError,match='changed'):R.location_loader(m.load,'0'*64,A.selected_operation_namespace)

@pytest.mark.parametrize('which',['escape','file','symlink'])
def test_projected_location_still_rejects_unsafe_namespace(tmp_path,which):
 m=original(tmp_path);p=tmp_path/'dispatcher-v2/selected';p.parent.mkdir()
 if which=='escape':p=tmp_path/'elsewhere'
 elif which=='file':p.write_text('retained')
 else:p.symlink_to(tmp_path,target_is_directory=True)
 chosen=R.location_loader(m.load,R.source_hash(m.load),A.selected_operation_namespace)
 with pytest.raises(ValueError):chosen({'run_root':str(tmp_path)},str(p))

def test_actual_cold_process_after_parent_namespace_creation(tmp_path):
 original(tmp_path);p=tmp_path/'dispatcher-v2/selected';p.mkdir(parents=True)
 root=Path(R.__file__).resolve().parents[2]
 code='''import sys,importlib.util
sys.path.insert(0,sys.argv[1])
from scripts.fleet import retained_initial_continue as R,undispatched_campaign as A
s=importlib.util.spec_from_file_location('old',sys.argv[2]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
from types import SimpleNamespace
m.dispatcher=SimpleNamespace(operation_namespace=lambda *a:(_ for _ in ()).throw(AssertionError('fresh gate must not execute during child load')))
f=R.location_loader(m.load,R.source_hash(m.load),A.selected_operation_namespace)
assert str(f({'run_root':sys.argv[3]},sys.argv[4]))==sys.argv[4]
'''
 q=subprocess.run([sys.executable,'-I','-B','-c',code,str(root),str(tmp_path/'original.py'),str(tmp_path),str(p)],capture_output=True)
 assert q.returncode==0,q.stderr.decode()
 assert list(p.iterdir())==[]
