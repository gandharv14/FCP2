from pathlib import Path
import copy
import pytest
from scripts.batch import harbor_queued_publication_history as H,harbor_queued_publication_lease as Q,harbor_expired_queued_publication as X
from scripts import harbor_publish as P
from test_queued_publication_lease import selected

def chain(tmp_path,monkeypatch):
 r,path,before,profile,calls=selected(tmp_path,monkeypatch)
 raw=Path(r.checkpoint['path']).read_bytes();renewed=Q.renew(P,r,profile);prior=Q.projected(P,r,renewed)
 now=P.read(P.record(path))['leases']['task_agent']['expires_at']+1
 original=P.lifecycle.lease_operation.__closure__[0].cell_contents
 monkeypatch.setattr(P.lifecycle,'lease_operation',lambda *a,**kw:original(*a,**kw,clock=lambda:now))
 monkeypatch.setattr(X.time,'time_ns',lambda:now)
 qualification=P._keep(tmp_path/'qualification.json',dict(schema_version='harbor-complete-readiness/v1',workbook=r.selection.content.workbook,stage_sha256=r.selection.content.stage_sha256,publication_ready=True,blockers=[]))
 proof=P._keep(tmp_path/'expired-proof.json',dict(status='ACTUAL_QUEUED_QUALIFICATION_PROOF_PASSED',tracker=P.record(path),request=P.encode(prior),qualification=qualification,models_dispatched=0,publication_mutations=0,lease_mutations=0))
 profile={**profile,'schema_version':X.SCHEMA,'tracker':P.record(path),'ready_proof':proof,'home':str(tmp_path/'expired'),'owner':'expired-successor','stopped_owner_proof':P._keep(tmp_path/'stopped.json',dict(stopped=True))}
 expired=X.recover(P,prior,profile,stopped_validator=lambda *a:True)
 value=dict(schema_version=H.SCHEMA,workbook=profile['workbook'],source_sha256=profile['source_sha256'],steps=[dict(reader=P.record(Q.__file__),completion=renewed),dict(reader=P.record(X.__file__),completion=expired)])
 return r,value,raw,calls

def test_original_renewal_then_expired_CAS_remain_independently_proven(tmp_path,monkeypatch):
 r,v,raw,calls=chain(tmp_path,monkeypatch);c=P._keep(tmp_path/'chain.json',v);result=H.projected(P,r,c)
 assert result.lease.revision==r.lease.revision+2 and result.lease.token!=r.lease.token
 assert Path(r.checkpoint['path']).read_bytes()==raw and not calls
 assert P._checkpoint(result)['lease']['owner']=='expired-successor'

@pytest.mark.parametrize('fault',['reordered','duplicate','foreign_source','foreign_workbook','unknown_kind','nested','extra','reader_tamper'])
def test_history_rejects_forged_composition(tmp_path,monkeypatch,fault):
 r,v,raw,calls=chain(tmp_path,monkeypatch)
 if fault=='reordered':v['steps'].reverse()
 elif fault=='duplicate':v['steps'][1]=v['steps'][0]
 elif fault=='foreign_source':v['source_sha256']='0'*64
 elif fault=='foreign_workbook':v['workbook']='foreign'
 elif fault=='extra':v['automatic_retry']=True
 elif fault=='reader_tamper':v['steps'][0]['reader']={**v['steps'][0]['reader'],'sha256':'0'*64}
 else:
  a=P.read(v['steps'][0]['completion']);a['schema_version']=H.SCHEMA if fault=='nested'else'unknown';v['steps'][0]['completion']=P._keep(tmp_path/'bad-completion.json',a)
 c=P._keep(tmp_path/'chain.json',v)
 with pytest.raises((ValueError,P.lifecycle.Conflict)):H.projected(P,r,c)
 assert Path(r.checkpoint['path']).read_bytes()==raw and not calls
