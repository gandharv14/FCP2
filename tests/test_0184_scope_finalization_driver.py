"""Root finalization preserves the exact selection except authenticated scope proof."""
from dataclasses import dataclass,replace
from pathlib import Path
from types import SimpleNamespace,ModuleType
import importlib.util,sys
import pytest
P=Path(__file__).resolve().parents[1]/'diagnostics/requested-fixes-20260910/finalize_0184_scope_equivalence.py'
spec=importlib.util.spec_from_file_location('scope_finalization_driver',P);d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
@dataclass(frozen=True)
class Content:
 dialogue_scope_receipt:object
 source:object='source'
 claims:object='claims'
@dataclass(frozen=True)
class Selection:
 content:Content
 runtime:object='actual-runtime'
 semantic:object='actual-66-calls'
@dataclass(frozen=True)
class Lease:
 owner:str='owner'
 token:str='token'
 revision:int=117
 sha256:str='tracker'
@pytest.fixture
def context(monkeypatch):
 original=Selection(Content('old-scope'));chosen=replace(original,content=replace(original.content,dialogue_scope_receipt='new-scope'));lease=Lease();calls=[]
 records={'prepared':dict(action='prepare',status='FULL_READINESS_QUALIFIED',selection=chosen,scope_receipt='new-scope',lease=d.asdict(lease),readiness='readiness'),'new-scope':dict(previous='old-scope',semantic_attempts_added=0,semantic_budget_reset=False),'readiness':dict(publication_ready=True)}
 monkeypatch.setattr(d,'checked',lambda b:records[b])
 module=ModuleType('scripts.batch.harbor_dialogue_scope_context');module.validate_selected=lambda s,root:calls.append((s,root))
 import scripts.batch
 monkeypatch.setitem(sys.modules,'scripts.batch.harbor_dialogue_scope_context',module);monkeypatch.setattr(scripts.batch,'harbor_dialogue_scope_context',module,raising=False)
 codec=SimpleNamespace(decode=lambda x:x)
 env=(None,Path('/current'),None,SimpleNamespace(codec=codec),None,None,None,None,None,original,lease)
 return records,env,calls

def test_only_scope_receipt_changes_and_current_validator_runs(context):
 records,env,calls=context
 assert d.qualification(env,'prepared')==records['prepared']['selection']
 assert calls==[(records['prepared']['selection'].content,Path('/current'))]
@pytest.mark.parametrize('field,value',[('runtime','forged-runtime'),('semantic','different-calls')])
def test_non_scope_selection_change_rejected(context,field,value):
 records,env,_=context;records['prepared']['selection']=replace(records['prepared']['selection'],**{field:value})
 with pytest.raises(AssertionError):d.qualification(env,'prepared')
@pytest.mark.parametrize('field,value',[('source','foreign-source'),('claims','other-claims')])
def test_source_or_claim_change_rejected(context,field,value):
 records,env,_=context;s=records['prepared']['selection'];records['prepared']['selection']=replace(s,content=replace(s.content,**{field:value}))
 with pytest.raises(AssertionError):d.qualification(env,'prepared')
@pytest.mark.parametrize('key,value',[('previous','unrelated'),('semantic_attempts_added',1),('semantic_budget_reset',True)])
def test_scope_history_or_budget_change_rejected(context,key,value):
 records,env,_=context;records['new-scope'][key]=value
 with pytest.raises(AssertionError):d.qualification(env,'prepared')
def test_unqualified_readiness_rejected(context):
 records,env,_=context;records['readiness']['publication_ready']=False
 with pytest.raises(AssertionError):d.qualification(env,'prepared')
def test_wrong_custody_lease_rejected(context):
 records,env,_=context;records['prepared']['lease']['revision']=118
 with pytest.raises(AssertionError):d.qualification(env,'prepared')
def test_guard_policy_and_no_worker_step_dispatch():
 assert d.SECONDS==dict(prepare=900,commit=900,renew=300,enqueue=3300)
 import ast
 tree=ast.parse(P.read_text());calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
 assert not any(isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='W' and n.func.attr=='step' for n in calls)
 assert not any(isinstance(n.func,ast.Attribute) and n.func.attr in ('finalize','execute_next') for n in calls)

@pytest.fixture
def custody_route(monkeypatch,context,tmp_path):
 records,env,_=context;env=list(env);s=records['prepared']['selection'];lease=env[-1]
 env[0]=SimpleNamespace(INITIAL='initial',checked=lambda b:records[b]);env[2]=SimpleNamespace(bootstrap=SimpleNamespace(Lease=Lease));env[3]=SimpleNamespace(codec=SimpleNamespace(encode=lambda x:x,decode=lambda x:x));env[4]=SimpleNamespace(code=lambda w:{'worker':'current'});env[6]=SimpleNamespace(run=tmp_path)
 records.update(initial={'checkpoint':'runtime-complete'},commit={'status':'DIALOGUE_COMMITTED','selection':s,'lease':d.asdict(replace(lease,revision=118,sha256='commit-tracker')),'checkpoint':'commit-complete','owning_intent':'commit-intent'},renew={'status':'CUSTODY_RENEWED','selection':s,'old_lease':d.asdict(replace(lease,revision=118,sha256='commit-tracker')),'lease':d.asdict(replace(lease,revision=119,sha256='renewed-tracker')),'previous':'commit','before':'before','after':'after','cas_result':'cas'},after={'revision':119},cas={'revision':119,'sha256':'renewed-tracker'})
 records['commit-complete']=dict(step='dialogue-commit',status='completed',selection=s,lease=records['commit']['lease'],intent='commit-intent')
 records['commit-intent']=dict(operation='W._commit(dialogue)',previous='runtime-complete',selection=s,code={'worker':'current'},initial='initial-binding')
 monkeypatch.setattr(d,'binding',lambda p:'initial-binding');monkeypatch.setattr(d,'sealed',lambda e,a:a);monkeypatch.setattr(d,'qualification',lambda e,p:s)
 module=ModuleType('scripts.batch.harbor_nat_retry');calls=[];module._renewal_chain=lambda *args:calls.append(args)
 import scripts.batch
 monkeypatch.setitem(sys.modules,'scripts.batch.harbor_nat_retry',module);monkeypatch.setattr(scripts.batch,'harbor_nat_retry',module,raising=False)
 return records,tuple(env),calls

def test_enqueue_uses_actual_postcommit_custody_and_rechecks_chain(custody_route):
 records,env,calls=custody_route
 selected,current,previous=d.selected(env,'enqueue')
 assert current.revision==119 and previous=='renew' and selected==records['commit']['selection']
 assert calls==[('before',records['renew']['old_lease'],records['after'],current)]
@pytest.mark.parametrize('field,value',[('previous','other-commit'),('selection','other-selection'),('old_lease',{'revision':117})])
def test_detached_postcommit_custody_rejected(custody_route,field,value):
 records,env,_=custody_route;records['renew'][field]=value
 with pytest.raises(AssertionError):d.selected(env,'enqueue')
@pytest.mark.parametrize('field,value',[('revision',120),('owner','other-owner'),('token','other-token')])
def test_renewed_lease_identity_and_exact_revision_preserved(custody_route,field,value):
 records,env,_=custody_route;records['renew']['lease'][field]=value
 if field=='revision':records['cas']['revision']=value
 with pytest.raises(AssertionError):d.selected(env,'enqueue')
def test_custody_chain_rejection_propagates(custody_route,monkeypatch):
 records,env,_=custody_route
 def reject(*args):raise ValueError('history mismatch')
 monkeypatch.setattr(sys.modules['scripts.batch.harbor_nat_retry'],'_renewal_chain',reject)
 with pytest.raises(ValueError,match='history mismatch'):d.selected(env,'enqueue')
