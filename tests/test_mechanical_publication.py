from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet import fresh_publication as F

@pytest.fixture
def fixture(monkeypatch):
 monkeypatch.setattr(F,'mechanical_publication_guard',lambda *a:None)
 def b(p):return dict(path=str(p),sha256=str(p))
 def require(ok,msg):
  if not ok:raise ValueError(msg)
 base=dict(driver=b('/D'),config=b('/config'),outcome=b('/prior'),operations='/run/first')
 old=dict(fresh_plan=b('/fresh'),original_job=b('/original-job'))
 selected=dict(schema_version='fleet-completed-mechanical-dialogue-tail/v1',helper=b('/reader'),original=b('/old'),workbook='0162',source=dict(sha256='source'),output='/tail',review_outcome=b('/review'))
 fresh=dict(schema_version='harbor-fleet-fresh-plan/v1',workbook='0162',code_root='/code',dispatcher=base['driver'])
 plan=dict(mechanical_tail=dict(reader=b('/reader'),plan=b('/plan')),fresh_authoring=b('/fresh'),workbook='0162',source_sha256='source',code_root='/code',author_helper=dict(path='/author',sha256='414a278fe2bc52858788e1253af18d4989f0275869e86fbab6b071a13b241666'))
 job=dict(base,outcome=selected['review_outcome'],operations='/run/dispatcher-v2/tail')
 ready=dict(status='PUBLICATION_READY',workbook='0162',continuation=b('/plan'),models_dispatched=0,publication_performed=False,automatic_retry=False,job=b('/tail/authoring-job.json'),outcome=b('/ready'),checkpoint=b('/checkpoint'))
 closed=[];env=(None,None,None,None,None,dict(run_root='/run'),Path('/code'))
 D=SimpleNamespace(successful=lambda r,e:(b('/checkpoint'),dict(status='PUBLICATION_READY')))
 @contextmanager
 def context(row,*,child):
  assert row==b('/plan') and child is True
  try:yield selected,None,None,D,env,None,None,None,None,None,None
  finally:closed.append(True)
 values={'/plan':selected,'/old':old,'/fresh':fresh,'/original-job':base}
 pub=SimpleNamespace(checked=lambda r:values[r['path']],binding=b,require=require,load=lambda *a:SimpleNamespace(context=context))
 return pub,SimpleNamespace(load=lambda *a,**k:None),plan,ready,job,selected,fresh,D,closed

def test_completed_tail_preserves_original_review_and_context(fixture):
 pub,pinned,p,r,j,s,f,D,closed=fixture
 assert F.dispatcher(pub,pinned,p,r,j)is D
 assert not closed
 D._mechanical_publication_manager.__exit__(None,None,None)
 assert closed==[True]

@pytest.mark.parametrize('defect',['source','fresh','reader','ready','models','replay-review','job','checkpoint'])
def test_no_foreign_source_replay_or_unfinished_enqueue(fixture,defect):
 pub,pinned,p,r,j,s,f,D,closed=fixture
 if defect=='source':p['source_sha256']='foreign'
 elif defect=='fresh':f['workbook']='foreign'
 elif defect=='reader':s['helper']={}
 elif defect=='ready':r['status']='STEP_COMPLETE'
 elif defect=='models':r['models_dispatched']=1
 elif defect=='replay-review':j['outcome']=pub.binding('/prior')
 elif defect=='job':j['config']={}
 else:D.successful=lambda *a:(None,dict(status='STEP_COMPLETE'))
 with pytest.raises(ValueError):F.dispatcher(pub,pinned,p,r,j)
 if defect in ('replay-review','job','checkpoint'):assert closed==[True]

def test_historical_instruction_alias_is_metadata_not_a_live_input():
 import json
 H=Path(__file__).resolve().parents[1]/'diagnostics/bulk-production/0162-mechanical-label-001'
 def checked(row):return json.loads(Path(row['path']).read_bytes())
 tail=H/'tail-003/plan.json';selected=json.loads(tail.read_bytes());old=checked(selected['original'])
 pub=SimpleNamespace(FRESH_DEPENDENCIES={},checked=checked,require=lambda ok,msg:None if ok else (_ for _ in ()).throw(ValueError(msg)))
 rows=F.reader_evidence(pub,dict(mechanical_tail=dict(reader=selected['helper'],plan=dict(path=str(tail))),fresh_authoring=old['fresh_plan'],author_helper=old['helper']))
 archive=selected['instruction_archive']
 assert archive['original']['path']==archive['current']['path']
 assert archive['original']['sha256']==archive['archive']['sha256']!=archive['current']['sha256']
 assert archive['original']not in rows
 assert all(archive[k]in rows for k in ('archive','current','context','submitted'))
