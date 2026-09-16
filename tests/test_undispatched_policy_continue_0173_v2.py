import copy,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import undispatched_policy_continue_0173_v2 as M
from scripts.fleet import normalization_undispatched_authoring as D,undispatched_campaign as A
from scripts import harbor_campaign as C,harbor_instruction_campaign as I
from test_normalization_undispatched_authoring import dispatcher_module


def saved(path,v):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(v));return M.binding(path)


def profile():
 p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,workbook='0173',source_sha256=M.SOURCE,version=M.VERSION,owner=M.OWNER,expected_previous_fence=4,prior_tail_driver={'sha256':M.TAIL_SHA},capacity_policy={'sha256':M.POLICY_SHA},completed={'sha256':M.COMPLETED},failed={'sha256':M.FAILED});return p


@pytest.mark.parametrize('field,value',[('workbook','0077'),('source_sha256','other'),('version','other'),('owner','other'),('expected_previous_fence',2),('completed',{'sha256':'other'}),('capacity_policy',{'sha256':'other'})])
def test_closed_policy_boundary_identity(field,value):
 p=profile();M.identity(p);p[field]=value
 with pytest.raises(ValueError):M.identity(p)


def test_original_dispatcher_runs_with_new_truthful_child_wrapper(tmp_path,monkeypatch):
 d=dispatcher_module();env=(C,I,None,None,None,{},Path(C.ROOT),None)
 monkeypatch.setattr(d,'setup',lambda job:env)
 row=saved(tmp_path/'p.json',{});p={k:row for k in ('prior_tail_plan','prior_tail_driver','completed','failed','stopped_proof','capacity_policy','namespace_failure')}
 selected,newenv=M.selected_dispatcher(D,d,env,A,row,p)
 assert selected.run.__code__ is d.run.__code__
 assert newenv[0].run.__code__ is C.run.__code__ and newenv[1].run.__code__ is I.run.__code__
 assert __import__('inspect').getclosurevars(newenv[0].run.declared_runner).nonlocals['child_wrapper']==M.binding(M.__file__)


def test_preowning_failure_requires_absent_next_lane_and_original_guard(tmp_path,monkeypatch):
 directory=tmp_path/'015';directory.mkdir();trace=directory/'step.log';trace.write_text('fixture exact pre-owning assertion')
 monkeypatch.setattr(M,'TRACE',M.binding(trace)['sha256'])
 prior={'descriptor':{'path':'descriptor','sha256':'a'}};checkpoint={'path':'actual-completion','sha256':'b'}
 c=saved(tmp_path/'014/outcome.json',{'result':checkpoint})
 intent=saved(directory/'intent.json',dict(action='step',arguments=dict(descriptor=prior['descriptor'],checkpoint=checkpoint),timeout_seconds=3300,memory_limit_bytes=8*1024**3,models=True))
 resource=saved(directory/'resource.json',dict(returncode=1,failure=None,intent=intent,log=M.binding(trace)))
 failed=saved(directory/'outcome.json',dict(status='RECONCILIATION_REQUIRED',resource=resource));p=dict(failed=failed,completed=c);w=SimpleNamespace(run=tmp_path/'instruction')
 M.failed_boundary(p,prior,w)
 (w.run/'steps/nat-review').mkdir(parents=True)
 with pytest.raises(ValueError,match='already entered'):M.failed_boundary(p,prior,w)


def test_child_validation_precedes_original_instruction(tmp_path,monkeypatch):
 seen=[];p={k:{}for k in ('prior_tail_plan','prior_tail_driver','completed','failed','stopped_proof','capacity_policy','namespace_failure')}
 base=SimpleNamespace();producer=SimpleNamespace(child=lambda _:seen.append('producer'))
 class Adapter:
  def validate_child(self,*args,**kw):seen.append('validate');raise ValueError('unknown lineage')
 monkeypatch.setattr(M,'load',lambda b:(p,None,(base,producer),Adapter(),None,None,None,None,None));monkeypatch.setattr(M,'active',lambda p:None)
 with pytest.raises(ValueError):M.owning_child({},'instruction',{})
 assert seen==['validate']


def test_original_dispatcher_namespace_rejects_policy_home(tmp_path):
 d=dispatcher_module();config={'run_root':str(tmp_path/'authoring')}
 correct=tmp_path/'authoring/dispatcher-v2/authoring-policy-005'
 assert d.operation_namespace(config,str(correct))==correct
 with pytest.raises(ValueError,match='dedicated workbook namespace'):
  d.operation_namespace(config,str(tmp_path/'authoring-policy-005/operations'))
 assert not correct.exists()


def test_real_dispatcher_directory_created_before_declared_child(tmp_path):
 d=dispatcher_module();config={'run_root':str(tmp_path/'authoring')}
 correct=tmp_path/'authoring/dispatcher-v2/authoring-policy-005'
 assert M.validate_operations(d,config,correct,initial=True)==correct
 correct.mkdir(parents=True)
 with pytest.raises(ValueError):M.validate_operations(d,config,correct,initial=True)
 assert M.validate_operations(d,config,correct,initial=False)==correct
 with pytest.raises(ValueError):M.validate_operations(d,config,correct.parent/'other',initial=False)


def test_owning_child_existing_namespace_still_validates_intent_before_producer(tmp_path,monkeypatch):
 d=dispatcher_module();config={'run_root':str(tmp_path/'authoring')};correct=tmp_path/'authoring/dispatcher-v2/authoring-policy-005';correct.mkdir(parents=True)
 seen=[];p={k:{}for k in ('prior_tail_plan','prior_tail_driver','completed','failed','stopped_proof','capacity_policy','namespace_failure')}
 def load(bound):
  M.validate_operations(d,config,correct,initial=False)
  return p,None,(SimpleNamespace(),SimpleNamespace(child=lambda _:seen.append('producer'))),SimpleNamespace(validate_child=lambda *a,**k:seen.append('validated')),None,None,None,None,None
 monkeypatch.setattr(M,'load',load);monkeypatch.setattr(M,'active',lambda p:None)
 M.owning_child({},'instruction',{})
 assert seen==['validated','producer']
