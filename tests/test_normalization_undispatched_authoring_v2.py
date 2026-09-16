import ast
import copy
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts.fleet import normalization_undispatched_authoring as Old,normalization_undispatched_authoring_v2 as D,capacity_policy as P,coordinator as C


def test_only_policy_loading_active_validation_and_evidence_change():
 def funcs(m):return {x.name:ast.dump(x,include_attributes=False)for x in ast.parse(Path(m.__file__).read_text()).body if isinstance(x,ast.FunctionDef)}
 old,new=funcs(Old),funcs(D)
 assert set(old)==set(new)
 assert {k for k in old if old[k]!=new[k]}=={'load','active','evidence'}
 assert D.binding(P.__file__)['sha256']==D.CAPACITY_POLICY_SHA


def status():
 return dict(model_limit=None,publication_limit=1,model_policy=dict(schema_version=P.SCHEMA,revision=1,model_limit=None,authorization=dict(path='/authorized/authorization.json',sha256=P.AUTHORIZATION_SHA256),change_id=P.CHANGE_ID,per_workbook_model_limit=1,unknown_model_hold_barrier='global',publication_limit=1),operations=[dict(kind='model',state='active',workbook=w)for w in ('0013','0200')],workbooks=[dict(workbook='0077',state='active',owner='hhu-server-6:slot-2:0077-normalization-003',fence=3,source_sha256='source',version='version')])


def selected():
 return dict(workbook='0077',owner='hhu-server-6:slot-2:0077-normalization-003',source_sha256='source',version='version',capacity_policy=D.binding(P.__file__))


def check(monkeypatch,value,p=None):
 p=p or selected();calls=[]
 class Client:
  def __init__(self,*a):pass
  def call(self,action,**kw):
   calls.append(action)
   return value if action=='status' else {}
 monkeypatch.setattr(C,'Client',Client)
 with patch.dict(os.environ,dict(HARBOR_FLEET_OWNER=p['owner'],HARBOR_FLEET_WORKBOOK=p['workbook'],HARBOR_FLEET_FENCE='3',HARBOR_FLEET_CLAIM_TOKEN='test-only',HARBOR_FLEET_URL='http://127.0.0.1',HARBOR_FLEET_TOKEN_FILE='/test-only')):D.active(p)
 return calls


def test_actual_two_model_occupancy_authorized_without_rewriting_status(monkeypatch):
 s=status();original=copy.deepcopy(s)
 assert check(monkeypatch,s)==['status','heartbeat']
 assert s==original


@pytest.mark.parametrize('change',[lambda s:s['model_policy'].update(per_workbook_model_limit=2),lambda s:s.update(publication_limit=2),lambda s:s['model_policy']['authorization'].update(sha256='0'*64),lambda s:s['workbooks'][0].update(fence=2),lambda s:s['workbooks'][0].update(source_sha256='other'),lambda s:s['operations'].append(dict(kind='model',state='active',workbook='0013'))])
def test_unknown_policy_duplicate_book_and_foreign_custody_block(monkeypatch,change):
 s=status();change(s)
 with pytest.raises(ValueError):check(monkeypatch,s)


def test_changed_policy_file_binding_blocks(monkeypatch):
 p=selected();p['capacity_policy']['sha256']='0'*64
 with pytest.raises(ValueError):check(monkeypatch,status(),p)
