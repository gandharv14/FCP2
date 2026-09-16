from dataclasses import dataclass,replace
import json
from pathlib import Path
import pytest
from scripts.batch import harbor_environment_refresh as R,harbor_normalization_validator_bridge as V

@dataclass(frozen=True)
class Content:
    bindings:dict
    normalization_run:Path
    normalization_prepared:object=None
    normalization_call:object=None
    task:str='unchanged task'
@dataclass(frozen=True)
class Selection:
    content:Content
    model:str='same model'

def fixture(tmp_path,monkeypatch):
    original=tmp_path/'production-selection.json';original.write_text('{}')
    manifest=dict(previous=None,selection=R.record(original),prior_attempt_submitted=False,semantic_budget_reset=False)
    prepared=tmp_path/'prepared.json';prepared.write_text(json.dumps(manifest))
    old=Selection(Content({},tmp_path));new=replace(old,content=replace(old.content,bindings={'normalization_partitioned_review':{'actual':'review'}},normalization_prepared=R.record(prepared)))
    calls=[];monkeypatch.setattr(V,'validate_content',lambda content:calls.append(content))
    return old,new,manifest,prepared,calls

def test_initial_partition_retains_original_plan_and_full_validation(tmp_path,monkeypatch):
    old,new,m,p,calls=fixture(tmp_path,monkeypatch)
    assert R.unchanged(old,new) is True and calls==[new.content]

@pytest.mark.parametrize('kind',['plan','submitted','reset','run','task','single-call','validator'])
def test_initial_partition_cannot_hide_changed_task_or_review(tmp_path,monkeypatch,kind):
    old,new,m,p,calls=fixture(tmp_path,monkeypatch)
    if kind=='plan':m['selection']={'path':'/foreign','sha256':'a'*64}
    elif kind=='submitted':m['prior_attempt_submitted']=True
    elif kind=='reset':m['semantic_budget_reset']=True
    elif kind=='run':new=replace(new,content=replace(new.content,normalization_run=tmp_path/'foreign'))
    elif kind=='task':new=replace(new,content=replace(new.content,task='changed'))
    elif kind=='single-call':new=replace(new,content=replace(new.content,normalization_call={'fake':True}))
    else:
        def reject(_):raise ValueError('actual validator rejected')
        monkeypatch.setattr(V,'validate_content',reject)
    p.write_text(json.dumps(m));new=replace(new,content=replace(new.content,normalization_prepared=R.record(p)))
    with pytest.raises(ValueError):R.unchanged(old,new)
