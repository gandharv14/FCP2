import ast,importlib.util,json,time
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];H=R/'diagnostics/vm-fleet-20260912/control/0029-publication-003'
s=importlib.util.spec_from_file_location('collectonce',H/'collect_once.py');C=importlib.util.module_from_spec(s);s.loader.exec_module(C)
def state():
 p=C.E.checked(C.PLAN);r=C.E.checked(C.STAGE)['remote_result'];owner='hhu-server-3:publication:0029:003'
 return dict(at=time.time(),model_limit=1,publication_limit=1,workbooks=[dict(workbook='0029',state='hold',owner=owner,fence=5,version=Path(p['code_root']).name,source_sha256=p['source_sha256'],reason=json.dumps(r))],operations=[dict(kind='publication',workbook='0029',fence=5,state='confirmed',owner=owner,evidence=json.dumps(r))])
def test_exact_actual_staged_package_and_publication():assert C.central(state())['fence']==5
@pytest.mark.parametrize('field,value',[('fence',4),('state','active'),('owner','other'),('source_sha256','0'*64)])
def test_wrong_collection_owner_or_source_rejected(field,value):
 v=state();v['workbooks'][0][field]=value
 with pytest.raises(ValueError):C.central(v)
def test_unresolved_or_unknown_operation_rejected():
 for name in ['hold','active','unknown']:
  v=state();v['operations'][0]['state']=name
  with pytest.raises(ValueError):C.central(v)
def test_remote_probe_uses_true_nul_argv_delimiter(monkeypatch):
 code=[];monkeypatch.setattr(C.E,'ssh_script',lambda source,log:code.append(source) or '{"status":"PASS"}')
 C.stopped_probe(None);tree=ast.parse(code[0]);values=[n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='split' and n.args and isinstance(n.args[0],ast.Constant)]
 assert b'\0' in values
