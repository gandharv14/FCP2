from dataclasses import dataclass
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import research_continue_0006 as D

@dataclass
class Lease:
 owner:str='original';token:str='fixture';revision:int=4;sha256:str='a'*64

def test_research_commit_preserves_actual_result_and_blocks_audit():
 calls=[]
 E=SimpleNamespace(execute=lambda:None,_open=lambda:None)
 bridge=SimpleNamespace(run=lambda:None)
 lease=Lease()
 def advance(worker,chosen,*,dispatch):
  assert chosen is lease and dispatch is False
  for f in (E.execute,E._open,bridge.run):
   with pytest.raises(ValueError,match='cannot dispatch'):f()
  calls.append('actual gate')
  return dict(status='STEP_COMPLETE',step='research',lease=Lease(revision=5),selection={'real':'checkpoint'},evidence=[{'actual':'gate'}],publication_ready=False)
 C=SimpleNamespace(executor=E,worker=SimpleNamespace(audit_bridge=bridge,advance=advance),content_codec=SimpleNamespace(encode=lambda x:x))
 result=D.research_commit(C,object(),lease)
 assert calls==['actual gate'] and result['lease']['revision']==5 and result['selection']=={'real':'checkpoint'}
 assert result['evidence']==[{'actual':'gate'}]

def test_research_rejection_is_retained_without_fabricating_complete():
 C=SimpleNamespace(executor=SimpleNamespace(),worker=SimpleNamespace(audit_bridge=SimpleNamespace(),advance=lambda *a,**k:dict(status='UNRESOLVED',step='research',lease=Lease(),selection=None,evidence=[{'finding':'exact'}])),content_codec=SimpleNamespace(encode=lambda x:x))
 result=D.research_commit(C,None,Lease())
 assert result['status']=='UNRESOLVED'and result['evidence']==[{'finding':'exact'}]

@pytest.mark.parametrize('key,value',[('workbook','0190'),('source_sha256','0'*64),('version','0'*64),('expected_previous_fence',1),('owner','hhu-server:slot-2:0006-research-002'),('previous_owner','changed')])
def test_closed_identity_rejects_before_import(tmp_path,key,value):
 p={x:None for x in D.FIELDS};p.update(schema_version=D.SCHEMA,workbook='0006',source_sha256=D.SOURCE,version=D.VERSION,expected_previous_fence=2,previous_owner='hhu-server-8:slot-2:0006-remaining-environment-001',owner='hhu-server-8:slot-2:0006-research-002');p[key]=value
 f=tmp_path/'plan.json';f.write_text(json.dumps(p))
 with pytest.raises(ValueError,match='closed exact'):D.load(D.binding(f))
