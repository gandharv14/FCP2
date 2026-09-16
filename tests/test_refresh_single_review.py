from dataclasses import dataclass,replace
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.batch import harbor_environment_refresh as R

@dataclass(frozen=True)
class Content:
    bindings:dict
    normalization_run:Path
    normalization_prepared:object=None
    normalization_call:object=None
    task:str='same task'
@dataclass(frozen=True)
class Selection:
    content:Content
    model:str='same model'

def fixture(tmp_path,monkeypatch):
    production=tmp_path/'production-selection.json';production.write_text('{}')
    request=tmp_path/'request.json';request.write_text('{}')
    manifest=dict(schema_version='prepared-normalization-review/v1',selection=R.record(production),semantic_budget_reset=False,request=R.record(request))
    prepared=tmp_path/'prepared.json';prepared.write_text(json.dumps(manifest))
    old=Selection(Content({'source':'same'},tmp_path))
    content=replace(old.content,normalization_prepared=R.record(prepared),normalization_call=SimpleNamespace(request=R.record(request)),bindings={**old.content.bindings,'normalization_prepared_manifest':R.record(prepared),'normalization_prepared_request':R.record(request),'normalized_phase1':{'real':'result'}})
    new=replace(old,content=content);calls=[]
    monkeypatch.setattr(R.W.lanes,'normalization_attempt',lambda s:calls.append(('attempt',s)))
    monkeypatch.setattr(R.W.lanes,'normalization_projection',lambda s,**kw:calls.append(('projection',s,kw)))
    return old,new,manifest,prepared,calls

def test_original_call_and_full_projection_required(tmp_path,monkeypatch):
    old,new,m,p,calls=fixture(tmp_path,monkeypatch)
    assert R.unchanged(old,new) is True
    assert calls==[('attempt',new.content),('projection',new.content,{'phase1':True})]

@pytest.mark.parametrize('fault',['source','task','model','run','request','manifest','plan','schema','reset','review','attempt'])
def test_single_delegation_cannot_change_task_or_discard_original_review(tmp_path,monkeypatch,fault):
    old,new,m,p,calls=fixture(tmp_path,monkeypatch)
    if fault in {'source','request','manifest'}:
        key={'source':'source','request':'normalization_prepared_request','manifest':'normalization_prepared_manifest'}[fault]
        new=replace(new,content=replace(new.content,bindings={**new.content.bindings,key:'different'}))
    elif fault=='task':new=replace(new,content=replace(new.content,task='different'))
    elif fault=='model':new=replace(new,model='different')
    elif fault=='run':new=replace(new,content=replace(new.content,normalization_run=tmp_path/'foreign'))
    elif fault in {'plan','schema','reset'}:
        m[{'plan':'selection','schema':'schema_version','reset':'semantic_budget_reset'}[fault]]='different'
        p.write_text(json.dumps(m));row=R.record(p)
        new=replace(new,content=replace(new.content,normalization_prepared=row,bindings={**new.content.bindings,'normalization_prepared_manifest':row}))
    else:
        def reject(*args,**kwargs):raise ValueError('original proof rejects')
        monkeypatch.setattr(R.W.lanes,'normalization_projection'if fault=='review'else'normalization_attempt',reject)
    with pytest.raises(ValueError):R.unchanged(old,new)


def test_executor_materialized_request_retains_distinct_same_bytes(tmp_path,monkeypatch):
    old,new,m,p,calls=fixture(tmp_path,monkeypatch)
    materialized=tmp_path/'attempt-request.json';materialized.write_bytes(Path(m['request']['path']).read_bytes())
    new=replace(new,content=replace(new.content,normalization_call=SimpleNamespace(request=R.record(materialized))))
    assert R.unchanged(old,new) is True
    materialized.write_text('{"changed":true}')
    new=replace(new,content=replace(new.content,normalization_call=SimpleNamespace(request=R.record(materialized))))
    with pytest.raises(ValueError):R.unchanged(old,new)
