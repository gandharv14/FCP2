from dataclasses import dataclass
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import normalization_continue_0006 as D

@dataclass
class Lease:
 owner:str='original';token:str='fixture';revision:int=4;sha256:str='a'*64

def test_normalization_commit_preserves_actual_result_and_blocks_all_producers(tmp_path):
 calls=[]
 E=SimpleNamespace(execute=lambda:None,_open=lambda:None)
 bridge=SimpleNamespace(run=lambda:None)
 lease=Lease()
 def advance(worker,chosen,*,dispatch):
  assert chosen is lease and dispatch is False
  for f in (E.execute,E._open,bridge.run,C.worker.normalization.prepare,C.worker.normalization.emit_phase1,C.worker.normalization.dispatch):
   with pytest.raises(ValueError,match='cannot dispatch'):f()
  calls.append('actual gate')
  return dict(status='STEP_COMPLETE',step='normalization_phase1',lease=Lease(revision=5),selection={'real':'checkpoint'},evidence=[{'actual':'gate'}],publication_ready=False)
 C=SimpleNamespace(executor=E,worker=SimpleNamespace(audit_bridge=bridge,normalization=SimpleNamespace(),advance=advance),content_codec=SimpleNamespace(encode=lambda x:x))
 (tmp_path/'normalization_phase1.json').write_text('{}')
 result=D.normalization_commit(C,SimpleNamespace(run=tmp_path),lease)
 assert calls==['actual gate'] and result['lease']['revision']==5 and result['selection']=={'real':'checkpoint'}
 assert result['evidence']==[{'actual':'gate'}]

def test_normalization_rejection_is_retained_without_fabricating_complete(tmp_path):
 C=SimpleNamespace(executor=SimpleNamespace(),worker=SimpleNamespace(audit_bridge=SimpleNamespace(),normalization=SimpleNamespace(),advance=lambda *a,**k:dict(status='UNRESOLVED',step='normalization_phase1',lease=Lease(),selection=None,evidence=[{'finding':'exact'}])),content_codec=SimpleNamespace(encode=lambda x:x))
 (tmp_path/'normalization_phase1.json').write_text('{}')
 result=D.normalization_commit(C,SimpleNamespace(run=tmp_path),Lease())
 assert result['status']=='UNRESOLVED'and result['evidence']==[{'finding':'exact'}]

@pytest.mark.parametrize('key,value',[('workbook','0190'),('source_sha256','0'*64),('version','0'*64),('expected_previous_fence',1),('owner','hhu-server:slot-2:0006-research-002'),('previous_owner','changed')])
def test_closed_identity_rejects_before_import(tmp_path,key,value):
 p={x:None for x in D.FIELDS};p.update(schema_version=D.SCHEMA,workbook='0006',source_sha256=D.SOURCE,version=D.VERSION,expected_previous_fence=3,previous_owner='hhu-server-8:slot-2:0006-research-002',owner='hhu-server-8:slot-2:0006-normalization-003');p[key]=value
 f=tmp_path/'plan.json';f.write_text(json.dumps(p))
 with pytest.raises(ValueError,match='closed exact'):D.load(D.binding(f))


def test_normalization_checkpoint_absent_never_enters_producer_or_gate(tmp_path):
 calls=[];C=SimpleNamespace(executor=SimpleNamespace(),worker=SimpleNamespace(audit_bridge=SimpleNamespace(),normalization=SimpleNamespace(),advance=lambda *a,**k:calls.append('advance')))
 with pytest.raises(ValueError,match='checkpoint absent'):D.normalization_commit(C,SimpleNamespace(run=tmp_path),Lease())
 assert calls==[]


def test_original_import_uses_actual_post_gate_lease_not_legacy_lease(tmp_path,monkeypatch):
 from scripts import harbor_campaign as C
 import dataclasses
 w=SimpleNamespace(bootstrap=SimpleNamespace(workbook='0006',run=tmp_path,model_policy=None),run=tmp_path,campaign=None)
 old=C.bootstrap.Lease('owner','fixture-token',6,'a'*64);actual=C.bootstrap.Lease('owner','fixture-token',7,'b'*64)
 legacy={'path':'legacy','sha256':'c'*64};checkpoint={'path':'checkpoint','sha256':'d'*64}
 values={'legacy':dict(worker={},lease=dataclasses.asdict(old)),'checkpoint':dict(status='STEP_COMPLETE',step='normalization_phase1',lease=dataclasses.asdict(actual),selection=None,evidence=[])}
 monkeypatch.setattr(C,'read',lambda row:values[row['path']]);monkeypatch.setattr(C,'_legacy_worker',lambda _:w)
 seen=[];monkeypatch.setattr(C.worker,'_setup',lambda selected,lease:seen.append(lease))
 monkeypatch.setattr(C.worker,'_read_checkpoint',lambda selected,kind:None)
 monkeypatch.setattr(C.content_codec,'decode',lambda x:x);monkeypatch.setattr(C.content_codec,'encode',lambda x:x)
 monkeypatch.setattr(C.executor,'checked_bytes',lambda x:None)
 monkeypatch.setattr(C,'_descriptor',lambda selected,lease,controls,origin:dict(lease=dataclasses.asdict(lease)))
 saved=[];monkeypatch.setattr(C,'keep',lambda path,value:saved.append(value)or{'path':str(path),'sha256':'e'*64})
 result=C._import(legacy,checkpoint,None,tmp_path/'import')
 assert seen==[actual] and saved[0]['lease']==dataclasses.asdict(actual)
 assert result['lease']==C.encode(actual)


def test_actual_run_checks_original_namespace_before_final_takeover():
 import ast
 tree=ast.parse(__import__('pathlib').Path(D.__file__).read_text());run=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='run')
 calls=[(n.lineno,n.func.attr)for n in ast.walk(run)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)]
 assert next(line for line,name in calls if name=='operation_namespace')<next(line for line,name in calls if name=='replace_expired')
