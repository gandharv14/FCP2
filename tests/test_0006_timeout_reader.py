from pathlib import Path
from dataclasses import asdict,replace
import ast
import json
import sys
from types import SimpleNamespace

import pytest
from scripts.fleet import build_0006_timeout_reader as B, research_timeout_0006 as H
from scripts.batch import harbor_lease_takeover as T,harbor_tracker_lifecycle as L
from scripts.batch.harbor_admission import read,record
from test_root_lease_takeover import setup,save


def adapter(tmp_path,s,guard,monkeypatch):
 original=tmp_path/'original.py';original.write_bytes(Path(T.__file__).read_bytes())
 profile=save(tmp_path/'profile.json',dict(guard=guard,checkpoint=s.checkpoint,workbook=s.workbook,owner=s.owner,tracker=s.tracker))
 helper=tmp_path/'fixture-timeout-helper.py'
 helper.write_text('''from pathlib import Path\nimport json,hashlib\ndef allowed_timeout(s,guard,p):\n raw=Path(p['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==p['sha256'];v=json.loads(raw)\n if not (s.guard==v['guard'] and s.checkpoint==v['checkpoint'] and s.workbook==v['workbook'] and s.owner==v['owner'] and s.tracker==v['tracker'] and guard['returncode'] is None):raise ValueError('wrong fixture timeout')\n return True\n''')
 manifest=B.build(B.binding(original),B.binding(T.__file__),B.binding(helper),profile,tmp_path/'reader')
 import scripts.batch
 monkeypatch.setitem(sys.modules,'scripts.batch.harbor_lease_takeover',T)
 monkeypatch.setattr(scripts.batch,'harbor_lease_takeover',T)
 selected=B.install(manifest)
 return selected,manifest


def next_selected(setup):
 s,d,b,lease=setup;first,completion=T.replace_expired(s,d,clock=lambda:120)
 checkpoint=save(d/'completed-root.json',dict(lease=asdict(first)))
 old=read(s.guard);newdir=d.parent/'stopped-second'
 intent=save(newdir/'intent.json',read(old['intent']));log=save(newdir/'step.log',{'fixture':'stopped'})
 guard=save(newdir/'resource.json',{**old,'intent':intent,'log':log,'returncode':None})
 value=replace(s,tracker=record(s.tracker['path']),guard=guard,checkpoint=checkpoint,owner='third-root',prior_completion=completion)
 return value,d.parent/'two',read(record(s.tracker['path']))


def test_actual_second_cas_keeps_original_cap_and_all_nonlease_state(setup,tmp_path,monkeypatch):
 s,d,before=next_selected(setup)
 with pytest.raises(ValueError,match='guard'):T._recipe(s)
 selected,manifest=adapter(tmp_path,s,s.guard,monkeypatch)
 p=selected.Selection(**asdict(s));lease,completion=selected.replace_expired(p,d,clock=lambda:220)
 old,after,observed=selected.validate_completion(completion)
 assert old==before and observed==lease and L.validate_lifecycle(after)==2
 assert after['lane_state']==before['lane_state'] and after['bindings']==before['bindings']
 assert after['repairs']==before['repairs'] and after['scheduler']==before['scheduler']
 assert read(read(completion)['stopped_owner_proof'])['implementation']==read(manifest)['entry']
 assert read(s.guard)['returncode'] is None
 with pytest.raises(ValueError,match='exists'):selected.replace_expired(p,d,clock=lambda:221)
 with pytest.raises(ValueError):T.validate_completion(completion)


@pytest.mark.parametrize('point',['prepared','after_publish'])
def test_lost_takeover_reply_never_reissues_cas(setup,tmp_path,monkeypatch,point):
 s,d,before=next_selected(setup);selected,_=adapter(tmp_path,s,s.guard,monkeypatch);p=selected.Selection(**asdict(s))
 def fault(stage):
  if stage==point:raise TimeoutError('fixture lost reply')
 with pytest.raises(TimeoutError):selected.replace_expired(p,d,clock=lambda:220,_fault=fault)
 stop=record(d/'stopped-owner.json')
 if point=='after_publish':
  lease,completion=selected.recover(stop)
  assert selected.recover(stop)==(lease,completion)
  assert L.validate_lifecycle(read(record(s.tracker['path'])))==2
 else:
  with pytest.raises(ValueError):selected.recover(stop)
  assert read(record(s.tracker['path']))==before
 with pytest.raises(ValueError,match='exists'):selected.replace_expired(p,d,clock=lambda:221)


def test_only_exact_recipe_predicate_and_historical_identity_change(setup,tmp_path,monkeypatch):
 s,d,b=next_selected(setup);selected,manifest=adapter(tmp_path,s,s.guard,monkeypatch)
 before={n.name:ast.dump(n)for n in ast.parse(Path(T.__file__).read_text()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
 after={n.name:ast.dump(n)for n in ast.parse(Path(selected.__file__).read_text()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
 assert set(after)-set(before)=={'_fleet_retained_walltime'}
 assert [k for k in before if before[k]!=after[k]]==['_recipe']
 assert selected.MAX_REPLACEMENTS==T.MAX_REPLACEMENTS==3
 assert selected.MAX_DURATION_NS==T.MAX_DURATION_NS==7200*10**9
 assert B.binding(T.__file__)['sha256'] in selected.HISTORICAL_IMPLEMENTATIONS


@pytest.mark.parametrize('change',['workbook','worker_kind','guard','checkpoint','config','tracker','prior_completion','duration_ns'])
def test_actual_0006_timeout_predicate_rejects_every_different_selection(tmp_path,monkeypatch,change):
 row=save(tmp_path/'row.json',dict(status='completed'));guard={}
 p=dict(guard=row,checkpoint=row,config=row,tracker=row,prior_takeover=row,model_outcomes=[row,row],research=row,audit_complete=row)
 monkeypatch.setattr(H,'profile',lambda _:p)
 monkeypatch.setattr(H,'read',lambda b:guard if b is row else {'status':'completed'})
 s=SimpleNamespace(workbook='0006',worker_kind='environment',guard=row,checkpoint=row,config=row,tracker=row,prior_completion=row,duration_ns=7200*10**9)
 setattr(s,change,'wrong')
 with pytest.raises(ValueError):H.allowed_timeout(s,guard,{})


def test_rehashed_arbitrary_takeover_code_change_rejected(setup,tmp_path,monkeypatch):
 s,d,b=next_selected(setup);selected,manifest=adapter(tmp_path,s,s.guard,monkeypatch)
 m=read(manifest);path=Path(m['entry']['path']);path.write_text(path.read_text().replace('MAX_REPLACEMENTS=3','MAX_REPLACEMENTS=30',1));m['entry']=B.binding(path)
 altered=save(tmp_path/'bad-meta.json',m)
 with pytest.raises(ValueError,match='unreviewed'):B.install(altered)
