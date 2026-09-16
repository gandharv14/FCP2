import json
from pathlib import Path
import pytest
from scripts.fleet import remaining_authoring_0017 as X

def test_bridge_bytes_remain_exact():
 assert X.binding(X.BRIDGE['path'])==X.BRIDGE

def test_read_only_cannot_run(monkeypatch):
 monkeypatch.setattr(X,'load',lambda row:({'prior_fence':1},None));monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1')
 with pytest.raises(ValueError,match='read-only'):X.run({})

@pytest.mark.parametrize('field,value',[('HARBOR_FLEET_OWNER','other'),('HARBOR_FLEET_WORKBOOK','0270'),('HARBOR_FLEET_FENCE','1'),('HARBOR_FLEET_FENCE','3')])
def test_only_successor_fence_can_delegate(monkeypatch,field,value):
 monkeypatch.setattr(X,'load',lambda row:({'prior_fence':1},None));monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
 for k,v in dict(HARBOR_FLEET_OWNER=X.OWNER,HARBOR_FLEET_WORKBOOK='0017',HARBOR_FLEET_FENCE='2').items():monkeypatch.setenv(k,v)
 monkeypatch.setenv(field,value)
 with pytest.raises(ValueError):X.run({})

def test_native_delegate_uses_actual_prepared_binding(monkeypatch):
 calls=[]
 class Bridge:
  def run(self,*a):calls.append(a);return {'real':'result'}
 p=dict(prior_fence=1,prepared={'actual':'binding'},output='/unused')
 monkeypatch.setattr(X,'load',lambda row:(p,Bridge()));monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
 for k,v in dict(HARBOR_FLEET_OWNER=X.OWNER,HARBOR_FLEET_WORKBOOK='0017',HARBOR_FLEET_FENCE='2').items():monkeypatch.setenv(k,v)
 assert X.run({})=={'real':'result'} and calls==[(p['prepared'],'/unused')]

def test_probe_needs_explicit_nonexecution(monkeypatch):
 monkeypatch.setattr(X,'load',lambda row:({},None));monkeypatch.delenv('HARBOR_FLEET_CONTINUATION_READ_ONLY',raising=False)
 with pytest.raises(ValueError,match='nonexecuting'):X.probe({})

def test_binding_tamper_rejects(tmp_path):
 p=tmp_path/'evidence.json';p.write_text('{}');b=X.binding(p);p.write_text('{"changed":true}')
 with pytest.raises(ValueError):X.checked(b)
