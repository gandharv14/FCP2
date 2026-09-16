import copy
import hashlib
import json
import pytest
from scripts.fleet import coordinator_upgrade as U


def status():
    owner='hhu-server/slot-0/2cf9c9a1-20df-49ec-a48b-0db75fb28be5'
    op=dict(kind='model',state='hold',operation_id='uncertain',workbook='0413',owner=owner,fence=1)
    claim=dict(state='hold',workbook='0413',owner=owner,fence=1,source_sha256='a'*64,version='b'*64)
    args={k:claim[k]for k in ('workbook','owner','fence','source_sha256','version')};args.update(operation_id='uncertain',operation_sha256=hashlib.sha256(json.dumps(op,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    v=dict(schema_version='fleet-coordinator/v1',workbooks=[claim],operations=[op],model_limit=None,publication_limit=1,model_policy=dict(unknown_model_hold_barrier='global'))
    return v,args


def test_quiescent_keeps_exact_unknown_effect_instead_of_requiring_resolution():
    s,a=status();before=copy.deepcopy(s);assert U.quiescent(s,a)==s['model_policy'];assert s==before


@pytest.mark.parametrize('fault',['active-claim','active-model','active-pub','other-hold','changed-effect','changed-source','prior-quarantine','model-policy'])
def test_every_live_or_unbound_boundary_refuses_before_service_stop(fault):
    s,a=status()
    if fault=='active-claim':s['workbooks'].append(dict(state='active',workbook='other'))
    elif fault in ('active-model','active-pub'):s['operations'].append(dict(state='active',kind='model'if fault=='active-model'else'publication'))
    elif fault=='other-hold':s['operations'].append(dict(state='hold',kind='model',operation_id='other'))
    elif fault=='changed-effect':s['operations'][0]['request']='new'
    elif fault=='changed-source':s['workbooks'][0]['source_sha256']='f'*64
    elif fault=='prior-quarantine':s['quarantines']=[{}]
    elif fault=='model-policy':s['model_policy']['unknown_model_hold_barrier']='none'
    with pytest.raises(ValueError):U.quiescent(s,a)


def test_failed_preflight_cannot_signal_or_start_service(monkeypatch,tmp_path):
    monkeypatch.setattr(U,'quarantine_selection',lambda row:(_ for _ in ()).throw(ValueError('active operation')))
    def forbidden(*a,**k):pytest.fail('mutating boundary reached')
    monkeypatch.setattr(U.os,'kill',forbidden);monkeypatch.setattr(U.subprocess,'Popen',forbidden)
    with pytest.raises(ValueError):U.quarantine_execute({},str(tmp_path/'unused'))
    assert not(tmp_path/'unused').exists()


def test_host_quiescent_requires_exact_existing_unknown_and_no_productive_claims(tmp_path):
 from tests.test_fleet_quarantine import setup
 from tests.test_fleet_quarantine_host_capacity import permit
 c,now,claims,args=setup(tmp_path);a=permit(c,args)
 for wb in ('0140','0141'):c.finish(evidence='stopped fixture',disposition='hold',**claims[wb])
 s=c.status();assert U.host_quiescent(s,a)==s['model_policy']
 for fault in ('active','quarantine-digest','existing-host-policy','extended','other-hold'):
  v=copy.deepcopy(s);selected=copy.deepcopy(a)
  if fault=='active':v['workbooks'][1]['state']='active'
  elif fault=='quarantine-digest':selected['quarantine_sha256']='f'*64
  elif fault=='existing-host-policy':v['quarantine_host_capacity']=[{}]
  elif fault=='extended':selected['expires_at']=3000
  else:v['operations'].append(dict(state='hold',kind='publication'))
  with pytest.raises(ValueError):U.host_quiescent(v,selected)


def test_complete_policy_ledger_preserves_old_rows_and_exact_append(tmp_path):
 from tests.test_fleet_quarantine import setup
 from tests.test_fleet_quarantine_host_capacity import permit
 c,now,claims,args=setup(tmp_path);a=permit(c,args);path=tmp_path/'ledger';before=U.policy_ledger(path)
 c.permit_quarantine_host_capacity(**a);after=U.host_policy_transition(path,before)
 assert after['operations']==before['operations'] and after['model_quarantines']==before['model_quarantines']
 assert after['events']['count']==before['events']['count']+1
 with c.connect()as db:db.execute("UPDATE model_quarantines SET identity='changed'");db.commit()
 with pytest.raises(ValueError):U.host_policy_transition(path,before)
