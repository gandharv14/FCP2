from types import SimpleNamespace
from pathlib import Path
from dataclasses import asdict
import copy
import pytest
from test_root_lease_takeover import setup,save
from scripts.batch import harbor_completed_lane_history as H,harbor_lane_notes as N,harbor_lease_takeover as T
from scripts.batch.harbor_admission import read,record,require

@pytest.fixture
def case(setup,monkeypatch):
 s,_,before,lease=setup;run=s.campaign_root/'commit-worker';run.mkdir()
 before_row=save(run/'before.json',before);initial=save(run/'initial.json',{})
 lane='harbor-dialogue-author'
 assert lane in before['lane_state']
 gate=dict(passed=True,lane=lane,bindings={},gate='fixture-dialogue-gate',evidence=[],confidence='high')
 monkeypatch.setattr(N,'check_gate',lambda *a:copy.deepcopy(gate))
 from scripts.batch import harbor_completed_lanes as L
 monkeypatch.setattr(L,'downstream',lambda kind:set())
 facade=N.LaneNotes(N.RootContext(Path(s.tracker['path']).parent))
 original=N.schema.cas_mutate
 monkeypatch.setattr(N.schema,'cas_mutate',lambda *a,**kw:original(*a,**kw,now_ns=30))
 result=facade.complete(before['workbook_id'],SimpleNamespace(kind='dialogue'),expected_revision=lease.revision,expected_sha256=lease.sha256,owner=lease.owner,token=lease.token)
 after_row=save(run/'after.json',result['tracker'])
 phase=run/'dialogue-commit';phase.mkdir()
 intent=save(phase/'intent.json',dict(step='dialogue-commit',selection={},lease=asdict(lease),initial=initial,code={'fixture':'exact'}))
 done=save(phase/'complete.json',dict(step='dialogue-commit',status='completed',selection={},intent=intent,lease=dict(owner=lease.owner,token=lease.token,revision=result['revision'],sha256=result['sha256'])))
 worker=SimpleNamespace(run=run,context=facade.context)
 W=SimpleNamespace(read=read,record=record,require=require,step_directory=lambda w,p:w.run/p,code=lambda w:{'fixture':'exact'},codec=SimpleNamespace(decode=lambda s:s),HarborLaneLifecycle=N.LaneNotes,CompletionEvidence=lambda kind,selection:SimpleNamespace(kind=kind))
 return dict(W=W,T=T,worker=worker,before_binding=before_row,after_binding=after_row,completion=done,kind='dialogue'),Path(s.tracker['path'])

def test_original_facade_mutation_recomputed_without_second_cas(case,monkeypatch):
 kw,tracker=case;raw=tracker.read_bytes()
 monkeypatch.setattr(N.schema,'cas_mutate',lambda *a,**k:pytest.fail('real CAS called'))
 assert H.validate(**kw)==read(kw['after_binding'])
 assert tracker.read_bytes()==raw

@pytest.mark.parametrize('mutation',['source','budget','lease','lane','revision','completion','code'])
def test_recomputed_bindings_do_not_authorize_foreign_delta(case,mutation):
 kw,tracker=case;raw=tracker.read_bytes()
 if mutation in ('completion','code'):
  row=kw['completion'];done=read(row)
  if mutation=='completion':done['status']='failed';kw['completion']=save(Path(row['path']),done)
  else:
   intent=read(done['intent']);intent['code']={};done['intent']=save(Path(done['intent']['path']),intent);kw['completion']=save(Path(row['path']),done)
 else:
  row=kw['after_binding'];v=read(row)
  if mutation=='source':v['bindings']['original_raw_source']['sha256']='f'*64
  elif mutation=='budget':v['scheduler']['attempts']+=1
  elif mutation=='lease':v['leases']['task_agent']['token']='f'*64
  elif mutation=='lane':v['lane_state']['harbor-dialogue-author']['evidence_paths']=[{'path':'foreign','sha256':'a'*64}]
  else:v['revision']+=1
  kw['after_binding']=save(Path(row['path']),v)
  done=read(kw['completion']);done['lease']['sha256']=kw['after_binding']['sha256'];done['lease']['revision']=v['revision'];kw['completion']=save(Path(kw['completion']['path']),done)
 with pytest.raises((ValueError,AssertionError)):H.validate(**kw)
 assert tracker.read_bytes()==raw

def test_historical_gate_ignores_current_pending_transaction_without_weakening_live_sync(case,monkeypatch):
 kw,tracker=case;raw=tracker.read_bytes();wb=tracker.stem
 pending=tracker.parent/(wb+'-notes-transactions')/'current-prepared';pending.mkdir(parents=True)
 save(pending/'transaction.json',{'prepared':'current transition, not this historical commit'})
 facade=N.LaneNotes(kw['worker'].context)
 with pytest.raises(ValueError,match='pending notes'):facade.require_synchronized(wb)
 monkeypatch.setattr(N.schema,'cas_mutate',lambda *a,**k:pytest.fail('real CAS called'))
 assert H.validate(**kw)==read(kw['after_binding'])
 with pytest.raises(ValueError,match='pending notes'):facade.require_synchronized(wb)
 assert tracker.read_bytes()==raw and not (pending/'complete.json').exists()


def test_historical_snapshot_does_not_ignore_its_own_invalid_notes_journal(case):
 kw,tracker=case;before=Path(kw['before_binding']['path']);wb=tracker.stem
 pending=before.parent/(wb+'-notes-transactions')/'bad';pending.mkdir(parents=True)
 save(pending/'transaction.json',{'unclosed':True})
 with pytest.raises(ValueError,match='pending notes'):H.validate(**kw)
