import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from scripts.fleet import dialogue_revision_adoption as M

@pytest.mark.parametrize('change',[None,'archive','context','original-validator','renewal-validator'])
def test_only_completed_review_tracker_read_is_historical_and_scopes_restore(tmp_path,monkeypatch,change):
 def put(name,value):
  p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return M.binding(p)
 archive=put('lease-before.json',{'original':'T-after'});live=put('live.json',{'current':'L-after'})
 put('lease/complete.json',{});put('lease-after.json',{})
 target=dict(path=live['path'],sha256=archive['sha256'])
 cp=dict(schema_version='fleet-retained-completed-review-tail/v1',helper='original',completed_takeover={'helper':'T-reader'})
 q=dict(tracker=target,semantic_context={'helper':'original','plan':put('context.json',cp)})
 p=dict(output=str(tmp_path),renewal_module='L');calls=[]
 reader=NS(binding=M.binding,read=M.read)
 original=NS(module=lambda row:reader)
 old_loader=original.module;old_binding=reader.binding;old_read=reader.read
 def completed(q,I,controls):
  selected=original.module('T-reader');calls.append('original')
  assert selected.binding(live['path'])==target
  assert selected.read(target)=={'original':'T-after'}
  assert selected.binding(archive['path'])==archive
  if change=='original-validator':raise ValueError('original T rejected')
  return 'value','selection','T-lease'
 original.completed_failure=completed
 def validate(**args):
  calls.append('L');assert args['previous']=='T-lease'and args['before_binding']==archive and args['authority_record']=='authority'
  assert reader.binding==old_binding and reader.read==old_read and original.module==old_loader
  if change=='renewal-validator':raise ValueError('original L rejected')
 monkeypatch.setattr(M,'module',lambda name,row:NS(validate_completed=validate))
 if change=='archive':Path(archive['path']).write_text('{}')
 elif change=='context':q['semantic_context']['helper']='foreign'
 if change:
  with pytest.raises(ValueError):M.completed_review_failure(original,cp,q,None,None,p,'authority')
 else:
  assert M.completed_review_failure(original,cp,q,None,None,p,'authority')==('value','selection','T-lease')
  assert calls==['original','L']
 assert original.module==old_loader and reader.binding==old_binding and reader.read==old_read
 assert M.binding(live['path'])==live


def test_no_takeover_or_no_completed_renewal_uses_original_live_gate(tmp_path):
 calls=[];original=NS(completed_failure=lambda *args:calls.append(args)or 'original')
 p=dict(output=str(tmp_path));q={}
 assert M.completed_review_failure(original,{},q,None,None,p,None)=='original'
 assert M.completed_review_failure(original,{'completed_takeover':{}},q,None,None,p,None)=='original'
 assert len(calls)==2

@pytest.mark.parametrize('change',[None,'changed-after','expired','owner','authority','takeovers','mutation','both'])
def test_retained_live_adoption_lease_is_exact_and_does_not_renew(tmp_path,monkeypatch,change):
 from dataclasses import dataclass,asdict
 from scripts.batch import harbor_tracker_lifecycle as life
 @dataclass
 class Lease:
  owner:str;token:str;revision:int;sha256:str
 def put(name,value):
  path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)
 before=dict(revision=7,leases={'task_agent':dict(owner='owner',token='token',heartbeat_at=100,expires_at=7200*10**9+200)})
 archive=put('lease-before.json',before);put('lease-after.json',before);lease=Lease('owner','token',7,archive['sha256'])
 value=dict(schema_version='fleet-retained-live-continuation-lease/v1',authority='authority',lease=asdict(lease),
  tracker=dict(path='/actual/tracker',sha256=lease.sha256),validated_evidence=[],minimum_remaining_ns=7200*10**9,
  observed_at_ns=200,original_expired_replacements=3,lease_operations=0,task_guards_changed=False,semantic_budget_reset=False,automatic_retry=False)
 monkeypatch.setattr(life,'validate_lifecycle',lambda value:3)
 if change=='changed-after':put('lease-after.json',{})
 elif change=='expired':value['observed_at_ns']+=1
 elif change=='owner':value['lease']['owner']='other'
 elif change=='authority':value['authority']='other'
 elif change=='takeovers':value['original_expired_replacements']=0
 elif change=='mutation':value['lease_operations']=1
 elif change=='both':put('lease/complete.json',{})
 put('lease/retained-live.json',value);p=dict(output=str(tmp_path))
 if change:
  with pytest.raises(ValueError):M.validate_adoption_lease(p,'authority','/actual/tracker',lease)
 else:
  assert M.validate_adoption_lease(p,'authority','/actual/tracker',lease)==lease
  assert len(M.lease_records(p))==3
