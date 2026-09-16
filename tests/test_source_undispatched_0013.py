import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import source_undispatched_0013 as M
from scripts.fleet import normalization_undispatched_authoring as D,undispatched_campaign as A
from scripts import harbor_campaign as C,harbor_instruction_campaign as I
from test_normalization_undispatched_authoring import dispatcher_module

def profile():
 p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,workbook='0013',source_sha256=M.SOURCE,version=M.VERSION,owner=M.OWNER,expected_previous_fence=3,native_proof={'sha256':M.PROOF},capacity_policy={'sha256':M.POLICY},model_gate={'sha256':M.GATE},executor_adapter={'sha256':M.EXECUTOR});return p
@pytest.mark.parametrize('field,value',[('workbook','0064'),('source_sha256','bad'),('version','bad'),('owner','bad'),('expected_previous_fence',2),('native_proof',{'sha256':'bad'}),('capacity_policy',{'sha256':'bad'}),('model_gate',{'sha256':'bad'})])
def test_exact_preserved_selection(field,value):
 p=profile();M.identity(p);p[field]=value
 with pytest.raises(ValueError):M.identity(p)
def test_original_namespace_before_and_after_directory_creation(tmp_path):
 d=dispatcher_module();cfg=dict(run_root=str(tmp_path/'authoring'));p=dict(operations=str(tmp_path/'authoring/dispatcher-v2/undispatched-003'))
 correct=M.operations(d,cfg,p,True);correct.mkdir(parents=True)
 with pytest.raises(ValueError):M.operations(d,cfg,p,True)
 assert M.operations(d,cfg,p,False)==correct
 with pytest.raises(ValueError):M.operations(d,cfg,dict(operations=str(tmp_path/'outside')),False)
def test_actual_original_run_bytecode_retained(tmp_path,monkeypatch):
 d=dispatcher_module();env=(C,I,None,None,None,{},Path(C.ROOT),None);monkeypatch.setattr(d,'setup',lambda _:env);f=tmp_path/'p.json';f.write_text('{}');row=M.binding(f);p={k:row for k in M.FIELDS}
 selected,new=M.selected_dispatcher(D,d,env,A,row,p)
 assert selected.run.__code__ is d.run.__code__ and new[0].run.__code__ is C.run.__code__ and new[1].run.__code__ is I.run.__code__
def test_true_child_contract_checked_before_producer(monkeypatch):
 seen=[];p={k:{}for k in M.FIELDS};base=SimpleNamespace();producer=SimpleNamespace(child=lambda _:seen.append('producer'))
 def reject(*a,**k):seen.append('validate');raise ValueError('unbound child')
 monkeypatch.setattr(M,'load',lambda *a,**k:(p,None,(base,producer),None,None,None,SimpleNamespace(validate_child=reject),None,None,None,None));monkeypatch.setattr(M,'active',lambda p:None)
 with pytest.raises(ValueError):M.owning_child({},'instruction',{})
 assert seen==['validate']
def test_probe_flag_cannot_enter_owning_path(monkeypatch):
 monkeypatch.setenv('HARBOR_FLEET_CONTINUATION_READ_ONLY','1');monkeypatch.setattr(M,'load',lambda *a,**k:pytest.fail('must reject before task read'))
 with pytest.raises(ValueError):M.run({})

def test_actual_coordinator_prior_keeps_checkpoint_and_uses_retained_renewal(tmp_path,monkeypatch):
 from scripts.batch.harbor_workbook_bootstrap import Lease
 old=Lease('hhu-server-5:slot-1:0013-source-first-authoring-002','fixture-task-lease',21,'a'*64);new=Lease(old.owner,old.token,22,'b'*64)
 def save(path,v):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return M.binding(path)
 descriptor=save(tmp_path/'descriptor.json',{});value=dict(status='PENDING_MODEL',descriptor=descriptor,lease=C.encode(old),evidence=[]);cp=save(tmp_path/'023/result.json',value)
 tracker=dict(path=str(tmp_path/'tracker.json'),sha256=new.sha256);renewal=save(tmp_path/'024/lease-renewal.json',dict(previous=C.encode(old),lease=C.encode(new),tracker=tracker));seen=[]
 b=SimpleNamespace(decode=C.decode,_prior=C._prior,worker=SimpleNamespace(_active=lambda w,l:(None,{'checked':l==new})),lifecycle=SimpleNamespace(validate_lifecycle=lambda t:seen.append(t)))
 p=dict(lease_renewal=renewal,failed=dict(path=str(tmp_path/'024/outcome.json'),sha256='c'*64),tracker=tracker)
 assert M.retained_renewal(b,None,p,cp,value,dict(lease=C.encode(new)))==new
 returned,current=b._prior(cp,descriptor)
 assert returned==value and current==new and C._prior(cp,descriptor)[1]==old and seen==[{'checked':True}]
 assert json.loads(Path(cp['path']).read_bytes())==value
 bad=Lease(new.owner,'other-token',22,new.sha256);p['lease_renewal']=save(tmp_path/'024/changed.json',dict(previous=C.encode(old),lease=C.encode(bad),tracker=tracker))
 with pytest.raises(ValueError):M.retained_renewal(b,None,p,cp,value,dict(lease=C.encode(bad)))
