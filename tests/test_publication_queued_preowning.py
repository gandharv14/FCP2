import copy
import pytest
from scripts.fleet import publication_launch as L
from scripts.batch import harbor_expired_queued_publication as X
from scripts import harbor_publish as P
from test_expired_queued_publication import fixture

@pytest.mark.parametrize('fault',[None,'changed_work','wrong_source','old_tracker','changed_owner','lease_token','verdict','extra_event','wait_limit','wait_tracker'])
@pytest.mark.parametrize('local',[False,True])
def test_settled_admission_composes_actual_expired_CAS_only(tmp_path,monkeypatch,fault,local):
 r,path,before,profile,calls=fixture(tmp_path,monkeypatch)
 # READY names the dispatcher result; its publication request carries the
 # distinct original enqueue checkpoint. Preserve both identities.
 ready=P._keep(tmp_path/'ready.json',dict(checkpoint={'path':'/dispatcher-result','sha256':'dispatch'}))
 baseplan=dict(journal='/unused-base',workbook=profile['workbook'],source_sha256=profile['source_sha256'],ready=ready)
 wait=dict(maximum_wait_seconds=120,lease_tracker=profile['tracker'],authorization='unchanged')
 baseplan['publication_wait']=P._keep(tmp_path/'old-wait.json',wait)
 base=dict(plan=P._keep(tmp_path/'base-plan.json',baseplan),proof=P._keep(tmp_path/'base-proof.json',dict(tracker=profile['tracker'])),preowning_recovery='existing-exact-settlement',expected_owner='settled-P-owner',expected_fence=4,owner='unused-base-owner',publisher='unchanged')
 qualified=P.read(profile['ready_proof']);qualified['plan']=base['plan'];profile['ready_proof']=P._keep(tmp_path/'qualified-base.json',qualified)
 completion=X.recover(P,r,profile,stopped_validator=lambda *a:True);original=P.read(completion)
 edge=dict(schema_version='fleet-publication-preowning-queued-recovery/v1',prior_profile=P._keep(tmp_path/'base.json',base),reader=P.record(X.__file__),completion=completion)
 plan={**baseplan,'journal':'/new-unused','queued_lease':dict(reader=edge['reader'],completion=completion)}
 newwait={**wait,'lease_tracker':P.record(path)}
 if fault=='wait_limit':newwait['maximum_wait_seconds']=121
 elif fault=='wait_tracker':newwait['lease_tracker']=profile['tracker']
 plan['publication_wait']=P._keep(tmp_path/'new-wait.json',newwait)
 p={**base,'plan':'new-plan','proof':P._keep(tmp_path/'new-proof.json',dict(tracker=P.record(path))),'preowning_recovery':'new-edge','owner':'new-owner'}
 seen=[]
 def prior(actual,actualplan,read,bind,**kw):
  assert actual==base and actualplan==baseplan;seen.append('original-preowning');return ('exact-old','stopped','abandoned','unchanged-central')
 monkeypatch.setattr(L,'preowning',prior)
 if fault=='changed_work':plan['ready']='different'
 elif fault=='wrong_source':plan['source_sha256']='foreign'
 elif fault=='old_tracker':p['proof']=base['proof']
 elif fault=='changed_owner':p['expected_owner']='foreign'
 elif fault in ('lease_token','verdict','extra_event'):
  after=copy.deepcopy(P.read(original['after']))
  if fault=='lease_token':after['leases']['task_agent']['token']=r.lease.token
  elif fault=='verdict':after['terminal_verdict']='REJECTED'
  else:after['handoffs'].append(copy.deepcopy(after['handoffs'][-1]))
  original['after']=P._keep(tmp_path/'forged-after.json',after)
  edge['completion']=P._keep(tmp_path/'forged-completion.json',original);plan['queued_lease']['completion']=edge['completion']
  p['proof']=P._keep(tmp_path/'forged-proof.json',dict(tracker=dict(path=str(path),sha256=original['after']['sha256'])))
 if local:
  selected=P.read(edge['completion']);rows=[edge['completion'],selected['intent'],selected['before'],selected['after'],selected['projected_checkpoint'],ready]
  from pathlib import Path
  pairs=[]
  for i,row in enumerate(rows):
   q=tmp_path/('copy-'+str(i));q.write_bytes(Path(row['path']).read_bytes());pairs.append(dict(native=row,copy=P.record(q)))
  edge['custody']=P._keep(tmp_path/'custody.json',dict(schema_version='fleet-queued-publication-custody/v1',files=pairs))
 if fault:
  with pytest.raises(ValueError):L.queued_preowning(p,plan,edge,P.read,P.record,('wrapper',),local=local)
 else:
  assert L.queued_preowning(p,plan,edge,P.read,P.record,('wrapper',),local=local)[2]=='abandoned'
  assert X.projected(P,r,completion).lease.token!=r.lease.token
 assert seen==['original-preowning'] and not calls
