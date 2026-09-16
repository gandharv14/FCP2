import copy,json
from pathlib import Path
import pytest
from scripts.batch import harbor_expired_queued_publication as X
from scripts import harbor_publish as P
from test_queued_publication_lease import selected

def fixture(tmp_path,monkeypatch):
 r,path,before,base,calls=selected(tmp_path,monkeypatch)
 proof=P._keep(tmp_path/'qualification.json',dict(schema_version='harbor-complete-readiness/v1',workbook=r.selection.content.workbook,stage_sha256=r.selection.content.stage_sha256,publication_ready=True,blockers=[]))
 receipt=P._keep(tmp_path/'expired-proof.json',dict(status='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED',tracker=base['tracker'],request=P.encode(r),qualification=proof,models_dispatched=0,publication_mutations=0,lease_mutations=0))
 stop=P._keep(tmp_path/'stopped.json',dict(stopped=True))
 p={**base,'schema_version':X.SCHEMA,'ready_proof':receipt,'stopped_owner_proof':stop,'owner':'queued-root-successor'}
 # Original fixture lease ends at1000s; its explicit allowance remains valid.
 monkeypatch.setattr(X.time,'time_ns',lambda:1001*10**9)
 # selected() intentionally fixes lifecycle at40ns; select the real function
 # from its closure and give the expired recovery its explicit test clock.
 fn=P.lifecycle.lease_operation.__closure__[0].cell_contents
 monkeypatch.setattr(P.lifecycle,'lease_operation',lambda *a,**kw:fn(*a,**kw,clock=lambda:1001*10**9))
 return r,path,before,p,calls

def test_original_expired_CAS_new_token_exact_projection(tmp_path,monkeypatch):
 r,path,before,p,calls=fixture(tmp_path,monkeypatch);raw=Path(r.checkpoint['path']).read_bytes();seen=[]
 def stopped(*args):seen.append(args);return True
 c=X.recover(P,r,p,stopped_validator=stopped);new=X.projected(P,r,c);after=P.read(P.record(path))
 assert new.lease.token!=r.lease.token and new.lease.owner==p['owner']
 assert after['lane_state']==before['lane_state'] and after['repairs']==before['repairs']
 assert Path(r.checkpoint['path']).read_bytes()==raw and len(seen)==2 and not calls
 assert P.lifecycle.validate_lifecycle(after)==P.lifecycle.validate_lifecycle(before)+1
 with pytest.raises(ValueError):X.recover(P,r,p,stopped_validator=stopped)

@pytest.mark.parametrize('fault',['live','stopped','proof','source'])
def test_recovery_fails_before_CAS(tmp_path,monkeypatch,fault):
 r,path,before,p,calls=fixture(tmp_path,monkeypatch);raw=path.read_bytes()
 if fault=='live':monkeypatch.setattr(X.time,'time_ns',lambda:50)
 if fault=='source':p['source_sha256']='0'*64
 if fault=='proof':
  v=P.read(p['ready_proof']);v['models_dispatched']=1;p['ready_proof']=P._keep(tmp_path/'bad-proof.json',v)
 with pytest.raises(ValueError):X.recover(P,r,p,stopped_validator=lambda *a:fault!='stopped')
 assert path.read_bytes()==raw and not Path(p['home']).exists() and not calls

@pytest.mark.parametrize('fault',['token','selection','consumed','verdict'])
def test_projection_rejects_rehashed_forgery(tmp_path,monkeypatch,fault):
 r,path,before,p,calls=fixture(tmp_path,monkeypatch);c=X.recover(P,r,p,stopped_validator=lambda *a:True);v=P.read(c)
 if fault=='selection':
  a=P.read(v['projected_checkpoint']);a['selection']={};k='projected_checkpoint'
 else:
  a=P.read(v['after']);k='after'
  if fault=='token':a['leases']['task_agent']['token']=r.lease.token
  elif fault=='consumed':a['handoffs'][-1]['operation']='renew'
  else:a['terminal_verdict']='FAIL'
 target=Path(v[k]['path']);target.write_text(json.dumps(a));v[k]=P.record(target);Path(c['path']).write_text(json.dumps(v))
 with pytest.raises((ValueError,P.lifecycle.Conflict)):X.projected(P,r,P.record(c['path']))
 assert not calls
