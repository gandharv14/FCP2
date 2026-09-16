from dataclasses import dataclass,asdict
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import dialogue_remaining_derivative_review as M

@dataclass
class Policy:
 model:str='fixture-model'
 reasoning_effort:str='high'

@dataclass
class Scope:
 workbook_id:str='fixture'
 original_sha256:str='a'*64
 lane:str='harbor-dialogue-author'
 budget_id:str='dialogue-review'

@dataclass
class Prepared:
 request:dict
 policy:Policy
 scope:Scope
 ordinal:int
 limit:int
 attempt_id:str
 budget_evidence:tuple

def put(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return M.binding(path)

@pytest.fixture
def ordinary(tmp_path):
 policy=Policy();scope=asdict(Scope());home=tmp_path/'attempt-01'
 call=SimpleNamespace(submitted=put(home/'submitted.json',{'actual':True}))
 request=put(tmp_path/'request.json',{'fixture':True})
 plan=dict(scope=scope,ordinal=1,limit=2,policy=asdict(policy),response_mode='json_object',request=request,attempt_id='original',budget_evidence=[])
 put(home/'plan.json',plan)
 E=SimpleNamespace(PreparedCall=Prepared,SemanticScope=Scope,location=lambda p,c:home,inspect=lambda p,c:SimpleNamespace(status='completed',call=call))
 return home,plan,call,E,policy,scope

def test_original_review_is_inspected_without_new_call(ordinary):
 home,p,call,E,policy,scope=ordinary
 prepared,observed=M.ordinary_review(E,call,None,scope,1,policy)
 assert prepared.ordinal==1 and prepared.limit==2 and observed.call==call
 assert sorted(x.name for x in home.iterdir())==['plan.json','submitted.json']

@pytest.mark.parametrize('change',[
 {'ordinal':2},{'limit':3},{'response_mode':'text'}, {'physical_repair':{}},
 {'audit_context':{}},{'review_reservation':{}},{'policy':{'model':'other','reasoning_effort':'high'}},
 {'scope':dict(asdict(Scope()),original_sha256='b'*64)}, {'scope':dict(asdict(Scope()),budget_id='fresh-budget')},
])
def test_ordinary_budget_cannot_be_reset_or_replaced(ordinary,change):
 home,p,call,E,policy,scope=ordinary;put(home/'plan.json',dict(p,**change))
 with pytest.raises(ValueError):M.ordinary_review(E,call,None,scope,1,policy)

@pytest.mark.parametrize('status',['submitted','held','unknown','http_rejected','timeout'])
def test_uncertain_first_review_cannot_authorize_remaining_review(ordinary,status):
 home,p,call,E,policy,scope=ordinary;E.inspect=lambda p,c:SimpleNamespace(status=status,call=call)
 with pytest.raises(ValueError):M.ordinary_review(E,call,None,scope,1,policy)

def test_other_completed_call_is_not_original_review(ordinary):
 home,p,call,E,policy,scope=ordinary;E.inspect=lambda p,c:SimpleNamespace(status='completed',call='different')
 with pytest.raises(ValueError):M.ordinary_review(E,call,None,scope,1,policy)

def test_other_executor_namespace_is_rejected(ordinary,tmp_path):
 home,p,call,E,policy,scope=ordinary;other=tmp_path/'other';put(other/'plan.json',p);E.location=lambda p,c:other
 with pytest.raises(ValueError):M.ordinary_review(E,call,None,scope,1,policy)

def test_changed_file_and_symlink_are_rejected(tmp_path):
 path=tmp_path/'input.json';b=put(path,{'value':1});path.write_text('{}')
 with pytest.raises(ValueError):M.read(b)
 alias=tmp_path/'alias';alias.symlink_to(path)
 with pytest.raises(ValueError):M.binding(alias)

@pytest.mark.parametrize('field',['allowance','review_reservation','limit','prior_consumed','reset_budget'])
def test_remaining_plan_rejects_extra_authority(tmp_path,field):
 p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,helper=M.binding(M.__file__),semantic_context={'helper':{},'plan':{}},checker={'original':{},'selected':{},'proof':{}});p[field]={}
 with pytest.raises(ValueError):M.plan(put(tmp_path/'plan.json',p))

def test_remaining_plan_requires_all_three_checker_bindings(tmp_path):
 p={k:None for k in M.FIELDS};p.update(schema_version=M.SCHEMA,helper=M.binding(M.__file__),semantic_context={'helper':{},'plan':{}},checker={'selected':{}})
 with pytest.raises(ValueError):M.plan(put(tmp_path/'plan.json',p))
