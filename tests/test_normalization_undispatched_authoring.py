import importlib.util
import json
from pathlib import Path
from types import FunctionType, SimpleNamespace
import sys

import pytest
from scripts.fleet import normalization_undispatched_authoring as D, undispatched_campaign as A
from scripts import harbor_campaign as C, harbor_instruction_campaign as I


def saved(path,value):
 path.write_text(json.dumps(value));return D.binding(path)


@pytest.mark.parametrize('change',[{'workbook':'0073'},{'expected_previous_fence':1},
 {'owner':'hhu-server-8:slot-2:0173-normalization-003'},
 {'normalization_driver':{'sha256':'0'*64}},{'schema_version':'unknown'}])
def test_closed_remaining_profile_rejects_wrong_identity_before_module_import(tmp_path,change):
 fields={'schema_version','workbook','source_sha256','version','owner','expected_previous_fence','home',
  'normalization_plan','normalization_driver','phase1_result','campaign_adapter','tracker','stopped_proof'}
 p={k:None for k in fields};p.update(schema_version=D.SCHEMA,workbook='0173',expected_previous_fence=2,
  owner='hhu-server-4:slot-2:0173-normalization-003',normalization_driver={'sha256':'bad'})
 p.update(change)
 with pytest.raises(ValueError):D.load(saved(tmp_path/'plan.json',p))


def dispatcher_module():
 path=Path(__file__).resolve().parents[1]/'diagnostics/authoring-resume-20260910/runner/advance_authoring_v7.py'
 sys.path.insert(0,str(path.parent))
 spec=importlib.util.spec_from_file_location('fixture_original_dispatcher',path)
 dispatcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(dispatcher)
 return dispatcher


def test_full_original_dispatcher_bytecode_preserved_with_declared_dependencies(tmp_path,monkeypatch):
 dispatcher=dispatcher_module();env=(C,I,None,None,None,{},Path(C.ROOT),None)
 monkeypatch.setattr(dispatcher,'setup',lambda job:env)
 row=saved(tmp_path/'evidence.json',{})
 p={k:row for k in ('normalization_plan','normalization_driver','phase1_result','campaign_adapter','stopped_proof')}
 selected,newenv=D.selected_dispatcher(dispatcher,env,A,row,p)
 for name,value in dispatcher.__dict__.items():
  if isinstance(value,FunctionType)and value.__globals__ is dispatcher.__dict__ and name!='setup':
   assert getattr(selected,name).__code__ is value.__code__
 assert newenv[0].run.__code__ is C.run.__code__ and newenv[1].run.__code__ is I.run.__code__
 actual=selected.setup({'fixture':True})
 assert actual[0]is newenv[0] and actual[1]is newenv[1]
 assert selected.renew.__globals__ is selected.__dict__
 # The base in the returned environment is the one the unchanged renewal
 # function unpacks locally, so its actual process launch uses the reader too.
 assert actual[0]._run_in_process_group is not C._run_in_process_group
 assert C.run is not newenv[0].run and I.run is not newenv[1].run


def test_owning_child_validates_runtime_lineage_before_original_child(tmp_path,monkeypatch):
 called=[];plan={'campaign_adapter':{},'normalization_plan':{},'normalization_driver':{},'phase1_result':{},'stopped_proof':{}}
 fake=SimpleNamespace(child=lambda i:called.append(('child',i)))
 class Adapter:
  def validate_child(self,*a,**k):called.append(('validate',k['kind']));raise ValueError('changed adapter')
 monkeypatch.setattr(D,'load',lambda b:(plan,None,None,None,(fake,None),None,None,None,Adapter(),None,None,None))
 monkeypatch.setattr(D,'active',lambda p:None)
 with pytest.raises(ValueError,match='changed adapter'):D.owning_child({},'environment',{})
 assert called==[('validate','environment')]
