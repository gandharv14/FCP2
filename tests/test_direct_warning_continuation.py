"""Ordinary warning preparation preserves consumed history and exact custody."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts.fleet import retained_warning_continue as R


def write(path,value):
 path.write_text(json.dumps(value));return R.binding(path)


@pytest.mark.parametrize('fault',[None,'selection','lease','model','reset'])
def test_preparation_only_accepts_unchanged_content_custody_and_budget(tmp_path,fault):
 worker=SimpleNamespace(root=tmp_path)
 before=object();selected=object();lease=object();calls=[]
 permit=write(tmp_path/'permit.json',{'model_calls':1 if fault=='model' else 0,'semantic_budget_reset':fault=='reset'})
 scope=write(tmp_path/'scope.json',{'content_unchanged':True})
 M=SimpleNamespace(scope_context=lambda s,d,root:(selected,scope))
 def authorize(w,s,l,**kw):
  calls.append((w,s,l,kw))
  return w,object() if fault=='selection' else s,object() if fault=='lease' else l,permit
 prep={'Q':SimpleNamespace(authorize=authorize),'old':{'checkpoint':{'retained':'blocked'}},'old_snapshot':{'retained':'code'}}
 p={'completion':{'retained':'actual-completion'}};row={'retained':'exact-plan'}
 if fault:
  with pytest.raises(ValueError):R.prepare_direct(row,p,M,prep,(None,None),worker,before,lease,tmp_path)
  assert not(tmp_path/'preparation.json').exists()
 else:
  w,s,v=R.prepare_direct(row,p,M,prep,(None,None),worker,before,lease,tmp_path)
  assert w is worker and s is selected and v['context']==permit
  saved=json.loads((tmp_path/'preparation.json').read_bytes())
  assert saved==dict(plan=row,takeover=p['completion'],permit=permit,scope=scope,models_dispatched=0)
 assert len(calls)==1
 assert calls[0][3]['blocked']==prep['old']['checkpoint']
 assert calls[0][3]['old_snapshot']==prep['old_snapshot']
 assert calls[0][3]['takeover']==p['completion']


def test_direct_plan_cannot_inject_unselected_option(tmp_path):
 p={key:None for key in R.DIRECT_FIELDS};p.update(schema_version=R.DIRECT_SCHEMA,helper=R.binding(R.__file__),maximum_steps=4,models=True)
 with pytest.raises(ValueError,match='closed direct warning'):R.direct_context({'unused':True},p)
 assert list(tmp_path.iterdir())==[]


def test_preparation_receipt_is_once_only(tmp_path):
 target=tmp_path/'preparation.json';R.keep(target,{'original':'retained'})
 with pytest.raises(FileExistsError):R.keep(target,{'changed':'replacement'})
 assert json.loads(target.read_bytes())=={'original':'retained'}
