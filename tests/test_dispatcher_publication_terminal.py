import ast
from pathlib import Path
from types import SimpleNamespace as N
import pytest

DRIVER=Path(__file__).parents[1]/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v5.py'

def context(tmp_path):
 tree=ast.parse(DRIVER.read_text());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='publication_request')
 bind=lambda p:{'path':str(p),'sha256':'exact'}
 worker=N(run=tmp_path,context=N(tracker_root=tmp_path/'trackers'));complete=bind(tmp_path/'enqueue/complete.json');initial=bind(tmp_path/'initial.json')
 calls=[]
 def checkpoint(r):
  calls.append(r);assert r[0]=='selected' and r[1]=='lease' and r[2]==complete
  return {'intent':{'initial':initial}}
 ns={'Path':Path,'require':lambda p,m:None if p else (_ for _ in ()).throw(ValueError(m)),'selected':lambda e,v:(worker,None),'checked':lambda x:x,'binding':bind,'QUEUE_LOCK':tmp_path/'queue'}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(DRIVER),'exec'),ns)
 env=(N(decode=lambda x:x),N(SCHEMA='coordinator',codec=N(decode=lambda x:x)),N(Request=lambda *a:a,_checkpoint=checkpoint),N(step_directory=lambda w,s:tmp_path/s),None,{'publication_root':str(tmp_path/'pub'),'output':str(tmp_path/'out')},tmp_path,N(slot_lock=tmp_path/'book'))
 value={'schema_version':'coordinator','status':'PUBLICATION_READY','publication_performed':False,'next_step':None,'required_action':None,'evidence':[{'verified':'evidence'}],'checkpoint':complete,'selection':'selected','lease':'lease'}
 return ns['publication_request'],env,value,calls

def test_terminal_uses_actual_publisher_checkpoint(tmp_path):
 f,e,v,c=context(tmp_path);assert f(e,v)==c[0];assert len(c)==1

@pytest.mark.parametrize('key,value',[('status','STEP_COMPLETE'),('publication_performed',True),('next_step','enqueue'),('required_action','repair'),('checkpoint',{'path':'foreign','sha256':'exact'}),('selection','foreign'),('lease','foreign')])
def test_terminal_rejects_detached_state(tmp_path,key,value):
 f,e,v,c=context(tmp_path);v[key]=value
 with pytest.raises((ValueError,AssertionError)):f(e,v)

def test_run_routes_terminal_before_resumable_check():
 source=DRIVER.read_text();start=source.index("                if status=='PUBLICATION_READY':");end=source.index("                C._prior(checkpoint,value['descriptor'])",start)
 assert 'publication_request(env,value)' in source[start:end]
